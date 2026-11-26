#!/usr/bin/env python3
"""
リール動画レンダラー（デザイン選択対応）

使い方:
  python3 render.py --input 投稿フォルダ --out completed.mp4 [--design design.json]

  layout.json   … 位置・秒数（固定）
  fonts.json    … 選べるフォント
  palettes.json … 選べる配色
  design.json   … 購入者のデザイン設定（店名・文言・フォント・配色・色）
                  CSV（1列目=項目、2列目=値）でも可

投稿フォルダ:
  01_*.jpg 〜 05_*.mp4 … 番号順に使う（写真・動画どちらでも可）
  telop.txt            … 1行に1テロップ（素材と同じ数、3〜10）。改行位置は「/」、大きくしたい言葉は【】で指定できる
"""
import argparse, csv, json, os, re, subprocess, sys, tempfile, shutil
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".avi", ".webm"}


def load_json(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}


def load_design(path):
    if not os.path.exists(path):
        return {}
    if path.lower().endswith(".csv"):
        with open(path, encoding="utf-8-sig") as fh:
            return {r[0].strip(): (r[1].strip() if len(r) > 1 else "") for r in csv.reader(fh) if r and r[0].strip()}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------- 色 ----------
def hex_rgb(h):
    h = h.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        raise SystemExit(f"色の指定が正しくありません：#{h}（例：#242E33）")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def contrast(c1, c2):
    def lum(c):
        def ch(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
        r, g, b = (ch(v) for v in c)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    a, b = sorted((lum(c1), lum(c2)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def resolve_design(d):
    """デザイン設定を検証して、使う値を確定する"""
    fonts, palettes = load_json("fonts.json"), load_json("palettes.json")
    warnings = []

    font_name = d.get("フォント") or next(iter(fonts))
    if font_name not in fonts:
        raise SystemExit(f"フォント「{font_name}」はありません。選べるもの：" + "／".join(fonts))
    pal_name = d.get("配色") or next(iter(palettes))
    if pal_name not in palettes:
        raise SystemExit(f"配色「{pal_name}」はありません。選べるもの：" + "／".join(palettes))
    pal = dict(palettes[pal_name])
    for k in ("背景色", "文字色", "テロップ文字色"):
        if str(d.get(k, "")).strip():
            pal[k] = d[k]

    bg, fg, tc = hex_rgb(pal["背景色"]), hex_rgb(pal["文字色"]), hex_rgb(pal["テロップ文字色"])
    cr = contrast(bg, fg)
    if cr < 4.5:
        warnings.append(f"背景色と文字色の差が小さく、読みにくい可能性があります（コントラスト比 {cr:.1f}、目安4.5以上）")
    shadow = str(d.get("テロップの影", "あり")).strip() not in ("なし", "無し", "OFF", "off", "0", "false")
    if not shadow and contrast(tc, (128, 128, 128)) < 2.5:
        warnings.append("テロップの影を「なし」にすると、写真によってはテロップが読みにくくなります")

    def seconds(key, lo, hi, default):
        v = str(d.get(key, "")).replace("秒", "").strip()  # 「1.3秒」「1.3」どちらでも可
        if not v:
            return default
        try:
            x = float(v)
        except ValueError:
            raise SystemExit(f"「{key}」は数字で入れてください（{lo}〜{hi}秒）")
        if not lo <= x <= hi:
            raise SystemExit(f"「{key}」は{lo}〜{hi}秒の範囲で入れてください（今は{v}）")
        return x

    lay = str(d.get("レイアウト", "")).strip()
    layout = "classic" if lay.startswith("クラシック") else "modern"
    decor = str(d.get("テロップの飾り", "")).strip()
    if decor not in ("線", "丸", "かっこ", "なし"):
        decor = "線"
    try:
        cover = int(str(d.get("カバー", "")).strip() or 1)
    except ValueError:
        cover = 1

    return {
        "layout": layout,
        "decor": decor,
        "cover": cover,
        "photo_seconds": seconds("写真の表示時間", 1.0, 2.0, None),
        "video_seconds": seconds("動画の表示時間", 1, 30, None),
        "font": fonts[font_name], "font_name": font_name, "palette_name": pal_name,
        "bg": bg, "fg": fg, "telop_color": tc, "shadow": shadow,
        "shop_name": d.get("店名", "").strip(),
        "catch1": d.get("キャッチコピー1", "").strip(),
        "catch2": d.get("キャッチコピー2", "").strip(),
    }, warnings


# ---------- フォント ----------
_font_cache = {}


def load_font(spec, size):
    key = (spec["file"], spec.get("wght"), size)
    if key not in _font_cache:
        path = os.path.join(HERE, "fonts", spec["file"])
        if not os.path.exists(path):
            raise SystemExit(f"フォントファイルがありません：{path}（get_fonts.sh を実行してください）")
        f = ImageFont.truetype(path, size)
        if spec.get("wght"):
            f.set_variation_by_axes([spec["wght"]])
        _font_cache[key] = f
    return _font_cache[key]


def fit_font(spec, size, text, max_width):
    """文字が枠からはみ出す場合は、収まるまで小さくする"""
    while size > 12:
        f = load_font(spec, size)
        l, t, r, b = f.getbbox(text)
        if r - l <= max_width:
            return f
        size -= 2
    return load_font(spec, size)


def draw_centered(draw, font, text, center_x, top, fill):
    l, t, r, b = font.getbbox(text)
    draw.text((center_x - (r - l) / 2 - l, top - t), text, font=font, fill=fill)


def make_base(L, D, out_png):
    """背景色＋店名・キャッチコピーの土台画像"""
    im = Image.new("RGB", (L["width"], L["height"]), D["bg"])
    d = ImageDraw.Draw(im)
    for key in ("shop_name", "catch1", "catch2"):
        text = D[key]
        if not text:
            continue
        c = L[key]
        f = fit_font(D["font"]["text"], c["size"], text, c["max_width"])
        draw_centered(d, f, text, c["center_x"], c["top"], D["fg"])
    im.save(out_png)


def _kind(c):
    o = ord(c)
    if 0x3041 <= o <= 0x309F: return "hira"
    if 0x30A0 <= o <= 0x30FF: return "kata"
    if 0x4E00 <= o <= 0x9FFF or c in "々〆": return "kanji"
    return "other"


def wrap_telop(text, max_chars):
    """「/」か改行があればそこで改行。なければ自然な位置で2行に分ける"""
    if "/" in text or "\n" in text:
        return [s.strip() for s in re.split(r"[/\n]", text) if s.strip()]
    if len(text) <= max_chars:
        return [text]
    PART = "でもにをはがとのへや"
    PUNCT = "、。!！?？&＆…‥ "
    cands = {}
    for p in range(2, len(text) - 1):
        a, b = text[p - 1], text[p]
        if a in PUNCT:
            cands[p] = 2.0                      # 記号の後ろ（最優先）
        elif a in PART and b not in PART and _kind(text[p - 2]) != "hira" or \
             a in PART and _kind(b) in ("kanji", "kata"):
            cands[p] = 1.5                      # 助詞の後ろ
        elif _kind(a) == "hira" and _kind(b) in ("kanji", "kata"):
            cands[p] = 0.5
        elif _kind(a) == "kata" and _kind(b) in ("hira", "kanji") and b not in PART:
            cands[p] = 0.5
    mid = len(text) / 2
    if cands:
        # 真ん中に近く、区切りの良い位置。同点なら1行目が長い方
        p = min(cands, key=lambda p: (abs(p - mid) - cands[p], -p))
    else:
        p = (len(text) + 1) // 2
    return [text[:p], text[p:]]


def make_telop(L, D, text, out_png):
    """透明背景にテロップ（文字＋影）を描いた画像"""
    T = L["telop"]
    W, H = L["width"], L["height"]
    lines = wrap_telop(text.replace("【", "").replace("】", ""), T["max_chars_per_line"])
    longest = max(lines, key=lambda s: load_font(D["font"]["telop"], T["size"]).getlength(s))
    f = fit_font(D["font"]["telop"], T["size"], longest, T["max_width"])
    scale = f.size / T["size"]
    lh = T["line_height"] * scale
    _, gt, _, gb = f.getbbox("広あ")
    total = lh * (len(lines) - 1) + (gb - gt)
    top = T["center_y"] - total / 2

    fg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    df = ImageDraw.Draw(fg)
    if D["shadow"]:
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ds = ImageDraw.Draw(shadow)
        sc = (0, 0, 0, int(255 * T["shadow_opacity"]))
        ox, oy = T["shadow_offset"]
    for i, line in enumerate(lines):
        y = top + i * lh
        if D["shadow"]:
            draw_centered(ds, f, line, T["center_x"] + ox, y + oy, sc)
        draw_centered(df, f, line, T["center_x"], y, D["telop_color"] + (255,))
    if D["shadow"]:
        shadow = shadow.filter(ImageFilter.GaussianBlur(T["shadow_blur"]))
        fg = Image.alpha_composite(shadow, fg)
    fg.save(out_png)


# ---------- モダン（全面写真）レイアウト ----------
EMPH = re.compile(r"【([^】]*)】")


def draw_tracked(draw, font, text, center_x, top, fill, tracking):
    """字間を広げて中央揃えで描く（店名用）"""
    sp = font.size * tracking
    widths = [font.getlength(c) for c in text]
    total = sum(widths) + sp * (len(text) - 1)
    _, t, _, _ = font.getbbox("広A")
    x = center_x - total / 2
    for c, w in zip(text, widths):
        draw.text((x, top - t), c, font=font, fill=fill)
        x += w + sp


def make_shade(L, D, out_png, plain=False):
    """写真の上に重ねる、文字を読みやすくする影＋店名・キャッチコピー。
    plain=True は2枚目以降用：テロップのまわりの影だけ（店名・キャッチコピーなし）"""
    M = L["modern"]; S = M["shade"]
    W, H = L["width"], L["height"]
    import math
    alpha = []
    for y in range(H):
        a = S["base"]
        dy = abs(y - M["telop"]["center_y"])
        if dy < S["center_spread"]:
            a += S["center"] * (0.5 + 0.5 * math.cos(math.pi * dy / S["center_spread"]))
        if not plain and y < S["top_h"]:
            a += S["top"] * (1 - y / S["top_h"]) ** 1.5
        if not plain and y > S["bottom_from"]:
            a += S["bottom"] * ((y - S["bottom_from"]) / (H - S["bottom_from"])) ** 1.3
        alpha.append(min(int(a * 255), 230))
    col = Image.new("L", (1, H)); col.putdata(alpha)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    im.putalpha(col.resize((W, H)))
    im = Image.composite(Image.new("RGBA", (W, H), (0, 0, 0, 255)), Image.new("RGBA", (W, H), (0, 0, 0, 0)), im.getchannel("A"))

    if plain:
        im.save(out_png)
        return
    txt = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(txt)
    color = D["telop_color"] + (255,)
    cx = W / 2
    if D["shop_name"]:
        c = M["shop_name"]
        f = fit_font(D["font"]["text"], c["size"], D["shop_name"] + " " * int(len(D["shop_name"]) * c["tracking"] * 2), c["max_width"])
        draw_tracked(d, f, D["shop_name"], cx, c["top"], color, c["tracking"])
        r = M["shop_rule"]  # 店名の下の線（飾りが「線」のときだけ）
        y0 = c["top"] + f.size + r["gap"]
        if D["decor"] == "線":
            d.line([(cx - r["width"] / 2, y0), (cx + r["width"] / 2, y0)], fill=D["telop_color"] + (200,), width=2)
    bottom = None
    for key in ("catch1", "catch2"):
        if D[key]:
            c = M[key]
            f = fit_font(D["font"]["text"], c["size"], D[key], c["max_width"])
            draw_centered(d, f, D[key], cx, c["top"], color)
            bottom = c["top"] + f.size
    if bottom is not None and D["decor"] == "線":  # キャッチコピーの下の線（飾りが「線」のときだけ）
        r = M["catch_rule"]
        y0 = bottom + r["gap"]
        d.line([(cx - r["width"] / 2, y0), (cx + r["width"] / 2, y0)], fill=D["telop_color"] + (200,), width=2)
    im = Image.alpha_composite(im, soft_shadow(txt, 8, 0.45))
    im.save(out_png)


def soft_shadow(layer, blur, opacity):
    """文字レイヤーに、ふんわりした影を付けて返す"""
    a = layer.getchannel("A").filter(ImageFilter.GaussianBlur(blur))
    a = a.point(lambda v: int(v * opacity))
    sh = Image.new("RGBA", layer.size, (0, 0, 0, 0)); sh.putalpha(a)
    return Image.alpha_composite(sh, layer)


def split_emph(text):
    """【】で囲んだ部分を強調。「/」がなければ強調部分を1行にする"""
    if "【" in text and "/" not in text and "\n" not in text:
        text = EMPH.sub(lambda m: "/【" + m.group(1) + "】/", text)
    return text


def line_runs(line):
    runs, pos = [], 0
    for m in EMPH.finditer(line):
        if m.start() > pos: runs.append((line[pos:m.start()], False))
        runs.append((m.group(1), True)); pos = m.end()
    if pos < len(line): runs.append((line[pos:], False))
    return [r for r in runs if r[0]]


def make_telop_modern(L, D, text, out_png):
    M = L["modern"]; T = M["telop"]
    W, H = L["width"], L["height"]
    lines = wrap_telop(split_emph(text), T["max_chars_per_line"])
    lines = [line_runs(l) for l in lines]
    lines = [l for l in lines if l]
    spec = D["font"]["telop"]

    def width(runs, size):
        return sum(load_font(spec, int(size * (T["emph_scale"] if e else 1))).getlength(t) for t, e in runs)

    size = T["size"]
    max_w = T["max_width"]
    while size > 30 and max(width(r, size) for r in lines) > max_w:
        size -= 2
    # 行ごとの高さ（強調行は大きい）
    heights = [size * (T["emph_scale"] if any(e for _, e in r) else 1) for r in lines]
    gaps = [h * T["line_height"] for h in heights]
    total = sum(gaps) - gaps[-1] + heights[-1]
    y = T["center_y"] - total / 2
    y_first = y

    fg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(fg)
    color = D["telop_color"] + (255,)
    for runs, h, g in zip(lines, heights, gaps):
        x = W / 2 - width(runs, size) / 2
        base = y + h * 0.88  # ベースラインをそろえる
        for t, e in runs:
            f = load_font(spec, int(size * (T["emph_scale"] if e else 1)))
            d.text((x, base), t, font=f, fill=color, anchor="ls")
            x += f.getlength(t)
        y += g
    # 飾り（線／丸／かっこ／なし）
    R = M["rule"]
    deco = D["telop_color"] + (int(255 * R["opacity"]),)
    y_last = y - gaps[-1] + heights[-1]           # テロップの下の端
    half_w = max(width(r, size) for r in lines) / 2  # いちばん長い行の半分
    if D["decor"] == "線":
        d.line([(W / 2 - R["width"] / 2, y_last + R["gap"]), (W / 2 + R["width"] / 2, y_last + R["gap"])],
               fill=deco, width=R["thickness"])
    elif D["decor"] == "丸":
        C = M["circle"]
        cy = (y_first + y_last) / 2
        # 上と下の半円でテロップをはさむ（左右はあけて、文字が通れるようにする）
        r = min(max(half_w * C["width_ratio"], (y_last - y_first) / 2 + C["pad"]), C["max_r"])
        # 上と下に、まん中をそろえた円弧（テロップの上下をはさむ）
        box = [W / 2 - r, cy - r, W / 2 + r, cy + r]
        d.arc(box, C["top_arc"][0], C["top_arc"][1], fill=deco, width=C["thickness"])        # 上
        d.arc(box, C["bottom_arc"][0], C["bottom_arc"][1], fill=deco, width=C["thickness"])  # 下
    elif D["decor"] == "かっこ":
        B = M["bracket"]
        x0, y0 = W / 2 - half_w - B["pad"], y_first - B["pad"]
        x1, y1 = W / 2 + half_w + B["pad"], y_last + B["pad"]
        t, n = B["thickness"], B["size"]
        d.line([(x0, y0 + n), (x0, y0), (x0 + n, y0)], fill=deco, width=t, joint="curve")   # 「
        d.line([(x1, y1 - n), (x1, y1), (x1 - n, y1)], fill=deco, width=t, joint="curve")   # 」
    if D["shadow"]:
        glow = fg.getchannel("A").filter(ImageFilter.GaussianBlur(T["glow_blur"])).point(lambda v: int(v * T["glow_opacity"]))
        tight = fg.getchannel("A").filter(ImageFilter.GaussianBlur(T["shadow_blur"])).point(lambda v: int(v * T["shadow_opacity"]))
        ox, oy = T["shadow_offset"]
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sh.putalpha(glow)
        t2 = Image.new("RGBA", (W, H), (0, 0, 0, 0)); t2.putalpha(tight)
        sh2 = Image.new("RGBA", (W, H), (0, 0, 0, 0)); sh2.paste(t2, (ox, oy))
        fg = Image.alpha_composite(Image.alpha_composite(sh, sh2), fg)
    fg.save(out_png)


def render_scene_modern(L, shade_png, telop_png, media, seconds, out_mp4):
    M = L["modern"]; fps = L["fps"]; W, H = L["width"], L["height"]
    frames = max(int(round(seconds * fps)), 1)
    if os.path.splitext(media)[1].lower() in VIDEO_EXT:
        src = ["-i", media]
        mf = (f"[0:v]tpad=stop_mode=clone:stop_duration={seconds},fps={fps},"
              f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[m]")
    else:
        # 写真はゆっくりズーム（2倍の解像度で動かして、揺れを防ぐ）
        src = ["-i", media]
        z = M["zoom"]
        mf = (f"[0:v]scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
              f"zoompan=z='1+{z}*on/{frames}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d={frames}:s={W}x{H}:fps={fps},setsar=1[m]")
    fc = (f"{mf};[2:v]format=rgba,fade=t=in:st=0:d={M['telop_fade']}:alpha=1[t];"
          f"[m][1:v]overlay=0:0[a];[a][t]overlay=0:0,format=yuv420p[v]")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *src,
         "-loop", "1", "-framerate", str(fps), "-i", shade_png,
         "-loop", "1", "-framerate", str(fps), "-i", telop_png,
         "-filter_complex", fc, "-map", "[v]", "-t", str(seconds), "-r", str(fps),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", out_mp4])


# ---------- 素材 ----------
def collect_input(folder):
    media = []
    for name in sorted(os.listdir(folder)):
        ext = os.path.splitext(name)[1].lower()
        if re.match(r"^\d+", name) and (ext in IMAGE_EXT or ext in VIDEO_EXT):
            media.append(os.path.join(folder, name))
    with open(os.path.join(folder, "telop.txt"), encoding="utf-8") as fh:
        telops = [l.strip() for l in fh.read().splitlines() if l.strip()]
    telops = [re.sub(r"^\s*([①-⑳]|\d+[.、．)）])\s*", "", t) for t in telops]
    return media, telops


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stderr[-3000:])
        raise SystemExit("ffmpegでエラーが出ました")


def render_scene(L, base_png, telop_png, media, seconds, out_mp4):
    fps, b = L["fps"], L["photo_box"]
    if os.path.splitext(media)[1].lower() in VIDEO_EXT:
        src = ["-i", media]
        pre = f"tpad=stop_mode=clone:stop_duration={seconds},"
    else:
        src = ["-loop", "1", "-framerate", str(fps), "-i", media]
        pre = ""
    fc = (f"[1:v]{pre}fps={fps},scale={b['w']}:{b['h']}:force_original_aspect_ratio=increase,"
          f"crop={b['w']}:{b['h']},setsar=1[m];"
          f"[0:v][m]overlay={b['x']}:{b['y']}[bm];[bm][2:v]overlay=0:0,format=yuv420p[v]")
    run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-loop", "1", "-framerate", str(fps), "-i", base_png, *src,
         "-loop", "1", "-framerate", str(fps), "-i", telop_png,
         "-filter_complex", fc, "-map", "[v]", "-t", str(seconds), "-r", str(fps),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", "-an", out_mp4])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--design", default=os.path.join(HERE, "design.json"))
    ap.add_argument("--bgm", default=None)
    a = ap.parse_args()

    L = load_json("layout.json")
    D, warnings = resolve_design(load_design(a.design))
    for w in warnings:
        print("注意：" + w)

    media, telops = collect_input(a.input)
    n = len(media)
    if not (3 <= n <= 10):
        raise SystemExit(f"素材が{n}点です。3〜10点にしてください。")
    if len(telops) != n:
        raise SystemExit(f"素材{n}点・テロップ{len(telops)}件です。同じ数にしてください。")
    # 1シーンの秒数：写真・動画ごとにデザイン設定の値、なければレイアウトの標準値
    std = L["scene_seconds"][0]
    durations = []
    for m in media:
        is_video = os.path.splitext(m)[1].lower() in VIDEO_EXT
        v = D["video_seconds"] if is_video else D["photo_seconds"]
        durations.append(round(v if v is not None else std, 2))

    work = tempfile.mkdtemp(prefix="reel_")
    try:
        modern = D["layout"] == "modern"
        base_png = os.path.join(work, "base.png")
        (make_shade if modern else make_base)(L, D, base_png)
        plain_png = os.path.join(work, "plain.png")
        D_plain = dict(D, decor="なし")
        if modern:  # 2枚目以降は、テロップの文字だけ
            make_shade(L, D, plain_png, plain=True)
        segs = []
        for i in range(n):
            tp = os.path.join(work, f"telop{i}.png")
            seg = os.path.join(work, f"seg{i}.mp4")
            if modern:
                first = i == 0  # 1枚目だけ作り込み（店名・キャッチコピー・飾り）
                make_telop_modern(L, D if first else D_plain, telops[i], tp)
                render_scene_modern(L, base_png if first else plain_png, tp, media[i], durations[i], seg)
            else:
                make_telop(L, D, telops[i], tp)
                render_scene(L, base_png, tp, media[i], durations[i], seg)
            segs.append(seg)
        lst = os.path.join(work, "list.txt")
        with open(lst, "w") as fh:
            fh.writelines(f"file '{s}'\n" for s in segs)
        total = round(sum(durations), 2)
        if a.bgm:
            audio = ["-stream_loop", "-1", "-i", a.bgm]
            af = ["-filter:a", f"afade=t=out:st={max(total - 0.5, 0)}:d=0.5"]
        else:
            audio = ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
            af = []
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", lst, *audio,
             "-map", "0:v", "-map", "1:a", *af, "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
             "-t", str(total), "-movflags", "+faststart", a.out])
        # カバー画像：2枚目以降を選んだときだけ、その写真で作り込みの画像を別に作る
        #（選ばないときは、作り込みの1枚目がそのままカバーになる）
        if D["cover"] >= 2:
            ci = min(D["cover"], n) - 1
            cover = os.path.join(os.path.dirname(os.path.abspath(a.out)), "cover.jpg")
            if modern:
                tp = os.path.join(work, "cover_telop.png")
                make_telop_modern(L, D, telops[ci], tp)
                cseg = os.path.join(work, "cover.mp4")
                render_scene_modern(L, base_png, tp, media[ci], 1.0, cseg)
            else:
                cseg = segs[ci]
            run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", "0.6",
                 "-i", cseg, "-frames:v", "1", "-q:v", "2", cover])
            print(f"カバー画像：{ci + 1}枚目")
        # LINEで動画を送るときの表紙（小さめのJPEG。LINEは1MBまで）
        preview = os.path.join(os.path.dirname(os.path.abspath(a.out)), "preview.jpg")
        run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", str(min(0.6, durations[0] * 0.6)),
             "-i", segs[0], "-frames:v", "1", "-vf", "scale=540:-2", "-q:v", "4", preview])
        print(f"OK: {a.out}（{total:g}秒／{'モダン' if D['layout'] == 'modern' else 'クラシック'}／{D['font_name']}／{D['palette_name']}）")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
