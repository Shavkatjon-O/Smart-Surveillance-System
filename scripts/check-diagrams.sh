#!/bin/sh
#
# Checks that every diagram of the repository is a PNG card: fails when a Markdown or MDX file holds a mermaid code
# block or the Mermaid component, when an icon diagram docs/diagrams/<name>.py or a Mermaid source <name>.mmd has no
# PNG beside it, or when a PNG in docs/diagrams has no source or two. scripts/render-diagrams.sh draws the cards.
# Outputs:
#   Writes each finding to stderr.
# Returns:
#   Exits with status 1 when it finds any, otherwise 0.

set -eu

cd "$(dirname "$0")/.."
status=0

blocks=$(git ls-files -z --cached --others --exclude-standard -- '*.md' '*.mdx' \
  | xargs -0 -r grep -s -n -i -E '^[[:space:]]*(`{3,}|~{3,})[[:space:]]*mermaid|<Mermaid([[:space:]/>]|$)' || true)
if [ -n "$blocks" ]; then
  echo "$blocks" | sed 's/^/check-diagrams: mermaid in /' >&2
  echo "check-diagrams: draw the diagram as docs/diagrams/<name>.py, or <name>.mmd for a sequence diagram, render it with scripts/render-diagrams.sh and embed the PNG" >&2
  status=1
fi

sources=$(git ls-files --cached --others --exclude-standard -- '*.mmd' 'docs/diagrams/*.py')
for source in $sources; do
  [ -f "$source" ] || continue
  card="${source%.*}.png"
  if [ ! -f "$card" ]; then
    echo "check-diagrams: $source has no $card; run scripts/render-diagrams.sh $source" >&2
    status=1
  fi
done

for card in docs/diagrams/*.png; do
  [ -f "$card" ] || continue
  base="${card%.png}"
  if [ -f "$base.py" ] && [ -f "$base.mmd" ]; then
    echo "check-diagrams: $card has two sources, $base.py and $base.mmd; keep one" >&2
    status=1
  elif [ ! -f "$base.py" ] && [ ! -f "$base.mmd" ]; then
    echo "check-diagrams: $card has no source $base.py or $base.mmd" >&2
    status=1
  fi
done

if [ "$status" -eq 0 ]; then
  echo "check-diagrams: every diagram is a PNG card with its source"
fi
exit "$status"
