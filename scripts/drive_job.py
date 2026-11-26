#!/usr/bin/env python3
"""
GitHub Actions から呼ばれる動画制作ジョブ

1. Google Drive の投稿フォルダから素材（01〜05）と telop.txt を取得
2. renderer/render.py で動画を作る
3. completed.mp4 を同じフォルダに保存（あれば上書き）
4. GAS に結果を知らせる

環境変数:
  JOB_JSON  … GASから届いた依頼内容（postId, folderId, design, callbackUrl）
  DRIVE_TOKEN … Google Drive の一時的なアクセストークン（約1時間有効）
  CALLBACK_SECRET … GASへの結果通知に付ける合言葉
"""
import json, os, re, subprocess, sys, tempfile, traceback, urllib.parse, urllib.request, uuid

BASE = os.environ.get("DRIVE_API_BASE", "https://www.googleapis.com")  # テスト時だけ差し替える
API = BASE + "/drive/v3"
UPLOAD = BASE + "/upload/drive/v3"
HERE = os.path.dirname(os.path.abspath(__file__))
RENDERER = os.path.join(HERE, "..", "renderer", "render.py")


def http(method, url, token, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def list_folder(folder_id, token):
    q = urllib.parse.quote(f"'{folder_id}' in parents and trashed = false")
    url = f"{API}/files?q={q}&fields=files(id,name,mimeType,size)&pageSize=100&supportsAllDrives=true&includeItemsFromAllDrives=true"
    return json.loads(http("GET", url, token))["files"]


def download(file_id, dest, token):
    data = http("GET", f"{API}/files/{file_id}?alt=media&supportsAllDrives=true", token)
    with open(dest, "wb") as fh:
        fh.write(data)


def upload_mp4(folder_id, path, token, existing_id=None, name="completed.mp4", mime="video/mp4"):
    with open(path, "rb") as fh:
        body = fh.read()
    if existing_id:
        # すでに同じ名前のファイルがあれば中身を差し替える
        res = http("PATCH", f"{UPLOAD}/files/{existing_id}?uploadType=media&supportsAllDrives=true&fields=id",
                   token, body, {"Content-Type": mime})
        return json.loads(res)["id"]
    boundary = uuid.uuid4().hex
    meta = json.dumps({"name": name, "parents": [folder_id], "mimeType": mime}).encode()
    multipart = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode() + meta +
        f"\r\n--{boundary}\r\nContent-Type: {mime}\r\n\r\n".encode() + body +
        f"\r\n--{boundary}--".encode()
    )
    res = http("POST", f"{UPLOAD}/files?uploadType=multipart&supportsAllDrives=true&fields=id", token, multipart,
               {"Content-Type": f"multipart/related; boundary={boundary}"})
    return json.loads(res)["id"]


def callback(url, payload):
    if not url:
        return
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        # GASは302を返してから結果ページに転送する。処理自体はPOSTの時点で行われる
        urllib.request.urlopen(req, timeout=300).read()  # GAS側でAIキャプションを作る時間も待つ
    except Exception as e:
        print("結果の通知でエラー（処理には影響なし）:", e)


def main():
    job = json.loads(os.environ["JOB_JSON"])
    token = os.environ["DRIVE_TOKEN"]
    secret = os.environ.get("CALLBACK_SECRET", "")
    post_id, folder_id = job["postId"], job["folderId"]
    result = {"type": "render_done", "secret": secret, "postId": post_id,
              "runUrl": os.environ.get("RUN_URL", "")}
    try:
        files = list_folder(folder_id, token)
        work = tempfile.mkdtemp(prefix="job_")
        inp = os.path.join(work, "in")
        os.makedirs(inp)
        existing = None
        existing_cover = None
        existing_preview = None
        for f in files:
            name = f["name"]
            if name == "completed.mp4":
                existing = f["id"]
            elif name == "cover.jpg":
                existing_cover = f["id"]
            elif name == "preview.jpg":
                existing_preview = f["id"]
            elif name == "telop.txt" or re.match(r"^\d{2}_", name):
                download(f["id"], os.path.join(inp, name), token)
                print("取得:", name)

        design_path = os.path.join(work, "design.json")
        with open(design_path, "w", encoding="utf-8") as fh:
            json.dump(job.get("design") or {}, fh, ensure_ascii=False)

        cmd = [sys.executable, RENDERER, "--input", inp, "--design", design_path]
        if job.get("bgmFileId"):
            bgm_path = os.path.join(work, "bgm")
            download(job["bgmFileId"], bgm_path, token)
            cmd += ["--bgm", bgm_path]
            print("BGMを取得しました")

        out = os.path.join(work, "completed.mp4")
        r = subprocess.run(cmd + ["--out", out],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        print(r.stdout)
        if r.returncode != 0:
            raise RuntimeError(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "動画の生成に失敗しました")
        warnings = [l[3:] for l in r.stdout.splitlines() if l.startswith("注意：")]

        file_id = upload_mp4(folder_id, out, token, existing)
        print("保存しました:", file_id)
        result.update(ok=True, fileId=file_id, warnings=warnings)
        cover = os.path.join(work, "cover.jpg")
        if os.path.exists(cover):  # カバー画像（Instagramの「カバーを編集」で使う）
            result["coverFileId"] = upload_mp4(folder_id, cover, token, existing_cover, "cover.jpg", "image/jpeg")
            print("カバーを保存しました:", result["coverFileId"])
        preview = os.path.join(work, "preview.jpg")
        if os.path.exists(preview):  # LINEで動画を送るときの表紙
            result["previewFileId"] = upload_mp4(folder_id, preview, token, existing_preview, "preview.jpg", "image/jpeg")
    except Exception as e:
        traceback.print_exc()
        result.update(ok=False, error=str(e)[:500])
    callback(job.get("callbackUrl"), result)
    if not result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
