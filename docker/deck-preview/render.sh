#!/bin/sh
# render-deck IN_DIR OUT_DIR DECK...   -> OUT_DIR/<deck stem>-NN.png, one per slide (1600 px wide)
set -eu
in_dir=$1; out_dir=$2; shift 2
tmp=$(mktemp -d)
for deck in "$@"; do
  stem=${deck%.pptx}
  soffice --headless --norestore -env:UserInstallation=file://$tmp/profile \
    --convert-to pdf --outdir "$tmp" "$in_dir/$deck" >/dev/null 2>&1
  pdftoppm -png -scale-to-x 1600 -scale-to-y -1 "$tmp/$stem.pdf" "$tmp/$stem"
  for f in "$tmp/$stem"-*.png; do
    n=${f##*-}; n=${n%.png}
    cp "$f" "$out_dir/$stem-$(printf %02d "$(expr "$n" + 0)").png"
  done
  echo "$deck -> $(ls "$tmp/$stem"-*.png | wc -l) slides"
done
