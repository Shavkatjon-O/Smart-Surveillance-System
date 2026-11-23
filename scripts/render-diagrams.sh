#!/usr/bin/env bash
#
# Renders each Mermaid source in docs/diagrams to a PNG beside it: a dark card drawn by headless Chrome with the
# Mermaid library, which the Markdown and the documentation site embed as an image.
# Arguments:
#   $@: the .mmd files to render; every docs/diagrams/*.mmd when none is given
# Globals:
#   CHROME: Chrome or Chromium binary, default the first of google-chrome, chromium and chromium-browser on PATH
#   MERMAID_JS: address of the Mermaid library, default the pinned release on jsDelivr

set -euo pipefail

cd "$(dirname "$0")/.."
CHROME="${CHROME:-$(command -v google-chrome || command -v chromium || command -v chromium-browser)}"
MERMAID_JS="${MERMAID_JS:-https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js}"
export CHROME MERMAID_JS

python3 - "$@" <<'PY'
"""Render Mermaid sources to PNG cards with headless Chrome.

Each card is CARD_WIDTH pixels wide at SCALE times the device pixels, on the dark palette below; a diagram wider than
the card shrinks to fit, and the script says so, so that a diagram too wide to read can be laid out again.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME = os.environ["CHROME"]
MERMAID_JS = os.environ["MERMAID_JS"]

CARD_WIDTH = 920
SCALE = 2
PAD_X = 24
PAD_Y = 24

BACKGROUND = "#0a0a0a"
CARD_BORDER = "#3a3a3a"
TEXT = "#ffffff"
LABEL = "#d4d4d4"
NODE = "#111111"
NODE_BORDER = "#6a6a6a"
EDGE = "#a3a3a3"
RESULT = "#ededed"

SANS = "Ubuntu Sans, DejaVu Sans, Arial, sans-serif"
WEIGHT = 500
MONO = "Ubuntu Sans Mono, DejaVu Sans Mono, monospace"

ROLES = f"\n    classDef result fill:{RESULT},stroke:{RESULT},color:{BACKGROUND}\n"

THEME_CSS = f"""
.edgeLabel, .edgeLabel p, .edgeLabel span, .edgeLabel div {{
  color: {LABEL} !important; font-family: {MONO} !important; font-size: 16px !important;
  font-weight: {WEIGHT} !important; background-color: {BACKGROUND} !important;
}}
.edgeLabel rect, .labelBkg {{ fill: {BACKGROUND} !important; background-color: {BACKGROUND} !important; }}
.cluster-label, .cluster-label p, .cluster-label span, .cluster-label div {{
  color: {LABEL} !important; font-family: {MONO} !important; font-size: 16px !important;
  font-weight: {WEIGHT} !important;
}}
.nodeLabel, .nodeLabel p, .node .label {{ font-weight: {WEIGHT} !important; color: {TEXT}; }}
.node.result .nodeLabel, .node.result .nodeLabel p, .node.result .label {{ color: {BACKGROUND} !important; }}
.cluster rect {{ fill: {BACKGROUND} !important; stroke: {CARD_BORDER} !important; stroke-width: 1px !important; }}
.node rect, .node polygon {{ stroke-width: 1.4px !important; }}
.flowchart-link {{ stroke-width: 1.4px !important; }}
marker path {{ fill: {EDGE} !important; stroke: {EDGE} !important; }}
.messageText {{ fill: {LABEL} !important; font-family: {MONO} !important; }}
.messageLine0, .messageLine1 {{ stroke: {EDGE} !important; stroke-width: 1.4px !important; }}
.actor {{ stroke-width: 1.4px !important; }}
text.actor, text.actor tspan {{ fill: {TEXT} !important; }}
.actor-line {{ stroke: {CARD_BORDER} !important; }}
.note {{ stroke-width: 1px !important; }}
.noteText, .noteText tspan {{ fill: {LABEL} !important; }}
#arrowhead path, #crosshead path {{ fill: {EDGE} !important; stroke: {EDGE} !important; }}
"""

CONFIG = {
    "startOnLoad": False,
    "theme": "base",
    "fontFamily": SANS,
    "themeCSS": THEME_CSS,
    "themeVariables": {
        "darkMode": True,
        "fontFamily": SANS,
        "fontSize": "18px",
        "background": BACKGROUND,
        "mainBkg": NODE,
        "primaryColor": NODE,
        "primaryBorderColor": NODE_BORDER,
        "nodeBorder": NODE_BORDER,
        "primaryTextColor": TEXT,
        "textColor": TEXT,
        "lineColor": EDGE,
        "edgeLabelBackground": BACKGROUND,
        "clusterBkg": BACKGROUND,
        "clusterBorder": CARD_BORDER,
        "titleColor": LABEL,
        "actorBkg": NODE,
        "actorBorder": NODE_BORDER,
        "actorTextColor": TEXT,
        "actorLineColor": CARD_BORDER,
        "signalColor": EDGE,
        "signalTextColor": LABEL,
        "noteBkgColor": BACKGROUND,
        "noteBorderColor": CARD_BORDER,
        "noteTextColor": LABEL,
    },
    "flowchart": {
        "htmlLabels": True,
        "curve": "basis",
        "nodeSpacing": 18,
        "rankSpacing": 28,
        "padding": 12,
        "diagramPadding": 4,
        "wrappingWidth": 100,
        "subGraphTitleMargin": {"top": 4, "bottom": 12},
        "useMaxWidth": False,
    },
    "sequence": {
        "useMaxWidth": False,
        "diagramMarginX": 4,
        "diagramMarginY": 4,
        "actorMargin": 40,
        "messageMargin": 32,
        "noteMargin": 12,
        "mirrorActors": False,
        "actorFontSize": 18,
        "actorFontWeight": WEIGHT,
        "messageFontSize": 16,
        "messageFontWeight": WEIGHT,
        "noteFontSize": 16,
        "noteFontWeight": WEIGHT,
    },
}

PAGE = """<!doctype html><html><head><meta charset="utf-8"><script src="{js}"></script>
<style>
html, body {{ margin: 0; background: {background}; }}
#card {{ box-sizing: border-box; width: {width}px; padding: {pad_y}px {pad_x}px; background: {background};
  border: 2px solid {border}; display: flex; justify-content: center; align-items: center; }}
#card svg {{ max-width: 100%; height: auto; }}
</style></head><body>
<div id="card"></div>
<script type="application/json" id="source">{source}</script>
<script>
(async () => {{
  mermaid.initialize({config});
  const source = JSON.parse(document.getElementById("source").textContent);
  const out = document.createElement("script");
  out.type = "application/json";
  out.id = "size";
  try {{
    const r = await mermaid.render("diagram", source);
    const card = document.getElementById("card");
    card.innerHTML = r.svg;
    const svg = card.querySelector("svg");
    const natural = svg.viewBox.baseVal;
    svg.setAttribute("width", natural.width);
    svg.setAttribute("height", natural.height);
    svg.style.maxWidth = "100%";
    const box = card.getBoundingClientRect();
    out.textContent = JSON.stringify({{width: Math.ceil(box.width), height: Math.ceil(box.height),
      diagram: Math.ceil(natural.width)}});
  }} catch (e) {{
    out.textContent = JSON.stringify({{error: String((e && e.message) || e)}});
  }}
  document.body.appendChild(out);
}})();
</script></body></html>
"""


def chrome(page: Path, profile: Path, *extra: str) -> str:
    """Run headless Chrome on a page.

    Args:
        page: The HTML page to open.
        profile: A fresh profile folder for this run.
        *extra: Further Chrome switches, such as the window size and the output.

    Returns:
        What Chrome writes to its standard output.

    Raises:
        subprocess.CalledProcessError: Chrome exits with an error.
        subprocess.TimeoutExpired: Chrome runs longer than three minutes.
    """
    return subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
         "--run-all-compositor-stages-before-draw", "--allow-file-access-from-files",
         f"--user-data-dir={profile}", "--virtual-time-budget=30000", *extra, page.as_uri()],
        capture_output=True, text=True, timeout=180, check=True,
    ).stdout


def render(source: Path, inner: int) -> None:
    """Render one Mermaid source to a PNG of the same name beside it and print its size.

    Args:
        source: The .mmd file.
        inner: Width in pixels a diagram may take inside the card before it shrinks.

    Raises:
        SystemExit: Mermaid cannot draw the source.
    """
    text = source.read_text()
    if re.match(r"\s*(flowchart|graph)\b", text):
        text += ROLES
    with tempfile.TemporaryDirectory() as work:
        page = Path(work) / "page.html"
        page.write_text(PAGE.format(
            js=MERMAID_JS,
            config=json.dumps(CONFIG),
            source=json.dumps(text).replace("<", "\\u003c"),
            background=BACKGROUND, border=CARD_BORDER, width=CARD_WIDTH, pad_x=PAD_X, pad_y=PAD_Y,
        ))
        dom = chrome(page, Path(work) / "profile", f"--window-size={CARD_WIDTH},4000", "--dump-dom")
        found = re.search(r'<script type="application/json" id="size">(.*?)</script>', dom, re.S)
        size = json.loads(found.group(1)) if found else {"error": "no result"}
        if "error" in size:
            sys.exit(f"{source}: {size['error']}")
        target = source.with_suffix(".png")
        chrome(page, Path(work) / "profile", f"--window-size={size['width']},{size['height']}",
               f"--force-device-scale-factor={SCALE}", f"--screenshot={target.resolve()}")
        shrunk = f", diagram {size['diagram']} px shrunk to fit {inner} px" if size["diagram"] > inner else ""
        print(f"{target} {size['width'] * SCALE}x{size['height'] * SCALE}{shrunk}")


inner = CARD_WIDTH - 2 * PAD_X - 4
sources = [Path(name) for name in sys.argv[1:]] or sorted(Path("docs/diagrams").glob("*.mmd"))
for source in sources:
    render(source, inner)
PY
