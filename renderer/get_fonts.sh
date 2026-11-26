#!/bin/bash
# Google Fonts の公式リポジトリから、選択肢のフォントだけを fonts/ に取得する
set -e
cd "$(dirname "$0")"
TMP=$(mktemp -d)
git clone --quiet --depth 1 --filter=blob:none --sparse https://github.com/google/fonts "$TMP/gf"
cd "$TMP/gf"
git sparse-checkout set --no-cone \
  /ofl/notoserifjp/ /ofl/notosansjp/ \
  /ofl/shipporimincho/ShipporiMincho-SemiBold.ttf /ofl/shipporimincho/ShipporiMincho-Bold.ttf /ofl/shipporimincho/OFL.txt \
  /ofl/zenoldmincho/ZenOldMincho-SemiBold.ttf /ofl/zenoldmincho/ZenOldMincho-Bold.ttf /ofl/zenoldmincho/OFL.txt \
  /ofl/zenkakugothicnew/ZenKakuGothicNew-Medium.ttf /ofl/zenkakugothicnew/ZenKakuGothicNew-Bold.ttf /ofl/zenkakugothicnew/OFL.txt \
  /ofl/zenmarugothic/ZenMaruGothic-Medium.ttf /ofl/zenmarugothic/ZenMaruGothic-Bold.ttf /ofl/zenmarugothic/OFL.txt \
  /ofl/mplusrounded1c/MPLUSRounded1c-Bold.ttf /ofl/mplusrounded1c/MPLUSRounded1c-ExtraBold.ttf /ofl/mplusrounded1c/METADATA.pb \
  /ofl/kleeone/KleeOne-SemiBold.ttf /ofl/kleeone/OFL.txt
cd - >/dev/null
mkdir -p fonts/licenses
for d in "$TMP"/gf/ofl/*/; do
  name=$(basename "$d")
  cp "$d"*.ttf fonts/
  if [ -f "$d"OFL.txt ]; then cp "$d"OFL.txt "fonts/licenses/$name-OFL.txt"
  else cp "$d"METADATA.pb "fonts/licenses/$name-METADATA.txt"; fi
done
rm -rf "$TMP"
echo "fonts/ にフォントを取得しました"
ls fonts
