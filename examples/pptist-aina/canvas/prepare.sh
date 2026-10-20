#!/bin/sh
# Turns a PPTist checkout into the Superbot canvas build, without changing any PPTist source file:
#   - copies bridge/ to src/aina/ (type-checked and bundled with the editor);
#   - loads it as a second module script after src/main.ts in index.html;
#   - replaces the demo deck PPTist opens with (public/mocks/slides.json) by one blank slide.
#
#   ./prepare.sh /path/to/PPTist && (cd /path/to/PPTist && npm run build)
set -eu

here=$(cd "$(dirname "$0")" && pwd)
pptist=${1:?usage: prepare.sh <PPTist checkout>}

grep -q '<script type="module" src="/src/main.ts"></script>' "$pptist/index.html" || {
  echo "prepare.sh: $pptist/index.html no longer loads /src/main.ts; check the pinned PPTist commit" >&2
  exit 1
}

rm -rf "$pptist/src/aina"
cp -R "$here/bridge" "$pptist/src/aina"

if ! grep -q 'src/aina/aina-bridge.ts' "$pptist/index.html"; then
  sed 's#<script type="module" src="/src/main.ts"></script>#&\n    <script type="module" src="/src/aina/aina-bridge.ts"></script>#' \
    "$pptist/index.html" > "$pptist/index.html.tmp"
  mv "$pptist/index.html.tmp" "$pptist/index.html"
fi

printf '%s\n' '[{ "id": "blank", "elements": [], "background": { "type": "solid", "color": "#ffffff" } }]' \
  > "$pptist/public/mocks/slides.json"
