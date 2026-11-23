#!/usr/bin/env bash
#
# Renders each diagram source in docs/diagrams to a PNG card beside it, which the Markdown and the documentation site
# embed as an image. An icon diagram, <name>.py, is drawn by the diagrams package with Graphviz in a pinned container,
# so it needs only Docker; a sequence diagram, <name>.mmd, is drawn by headless Chrome with the Mermaid library. Both
# come out as the same dark card.
# Arguments:
#   $@: the .py and .mmd sources to render; every source in docs/diagrams when none is given
# Globals:
#   CHROME: Chrome or Chromium binary for .mmd sources, default the first of google-chrome, chromium and
#     chromium-browser on PATH
#   MERMAID_JS: address of the Mermaid library, default the pinned release on jsDelivr
#   DIAGRAMS_IMAGE: tag of the image that draws .py sources, built from the Dockerfile in render_icons, default
#     smart-surveillance-system-diagrams:local

set -euo pipefail

cd "$(dirname "$0")/.."

#######################################
# Renders Mermaid sources to PNG cards with headless Chrome.
# Globals:
#   CHROME
#   MERMAID_JS
# Arguments:
#   $@: the .mmd files, relative to the repository root
#######################################
render_sequences() {
  CHROME="${CHROME:-$(command -v google-chrome || command -v chromium || command -v chromium-browser || true)}"
  MERMAID_JS="${MERMAID_JS:-https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js}"
  if [ -z "$CHROME" ]; then
    echo "render-diagrams: .mmd sources need Chrome or Chromium; set CHROME" >&2
    exit 1
  fi
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
for name in sys.argv[1:]:
    render(Path(name), inner)
PY
}

#######################################
# Renders icon diagrams to PNG cards with the diagrams package and Graphviz, in a container built from the pinned
# Dockerfile below, so the host needs only Docker.
# Globals:
#   DIAGRAMS_IMAGE
# Arguments:
#   $@: the .py files, relative to the repository root
#######################################
render_icons() {
  local image="${DIAGRAMS_IMAGE:-smart-surveillance-system-diagrams:local}"
  docker build -q -t "$image" - >/dev/null <<'DOCKERFILE'
FROM ubuntu:noble-20261024.1@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
RUN apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    fonts-dejavu-core fonts-ubuntu graphviz librsvg2-bin python3 python3-venv \
  && rm -rf /var/lib/apt/lists/* \
  && fc-cache -f
RUN python3 -m venv /opt/diagrams \
  && /opt/diagrams/bin/pip install --no-cache-dir --no-deps \
    diagrams==0.25.1 graphviz==0.20.3 jinja2==3.1.6 markupsafe==3.0.4 pillow==13.0.0
ENV PATH=/opt/diagrams/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
DOCKERFILE
  docker run --rm -i --network none --memory 2g -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD:/repo" -w /repo \
    "$image" python3 - "$@" <<'PY'
"""Render icon diagrams to PNG cards with the diagrams package and Graphviz.

Each source is a Python file that draws one diagram with the diagrams package, as its documentation describes. Before
a source runs, the renderer puts the dark card theme into the package's defaults and makes it keep the Graphviz graph
instead of writing a file. The graph is drawn at SCALE times the card's pixels, or smaller when the diagram is wider
than the card, which the script then reports, so that a diagram too wide to read can be laid out again, and the
picture is centred on a card of the same size, colours and fonts as the Mermaid cards. Icons darker than the card are
redrawn in light grey, and SVG icons, such as the mark in packages/brand, are converted to PNG, because Graphviz here
reads only raster images.
"""

import html
import itertools
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path

from diagrams import Cluster, Diagram, Edge, Node, setdiagram
from diagrams.custom import Custom
from PIL import Image, ImageDraw, ImageStat

CARD_WIDTH = 920
SCALE = 2
PAD_X = 24
PAD_Y = 24
BORDER = 2

BACKGROUND = "#0a0a0a"
CARD_BORDER = "#3a3a3a"
TEXT = "#ffffff"
LABEL = "#d4d4d4"
EDGE = "#a3a3a3"
CLUSTERS = ("#121212", "#1a1a1a", "#222222")

SANS = "Ubuntu Sans Medium"
MONO = "Ubuntu Sans Mono Medium"
NODE_FONT = "15"
EDGE_FONT = "14"
CLUSTER_FONT = "15"

ICON = 64
GAP = 6

DARK = 80
LIGHT = (50, 212)

WORK = Path(tempfile.mkdtemp())
ICONS: dict[str, str] = {}
GRAPHS: list[str] = []
IDS = itertools.count()


def icon(path: str) -> str:
    """Return a PNG of an icon that reads on the card.

    Args:
        path: The icon's PNG or SVG file.

    Returns:
        The file Graphviz draws: the icon itself, its PNG conversion, or a light grey copy when it is darker than DARK
        on average.
    """
    if path in ICONS:
        return ICONS[path]
    target = WORK / f"icon{len(ICONS)}.png"
    if path.endswith(".svg"):
        subprocess.run(["rsvg-convert", "-w", "256", "-h", "256", "-o", str(target), path], check=True)
        picture = Image.open(target).convert("RGBA")
    else:
        picture = Image.open(path).convert("RGBA")
    alpha = picture.getchannel("A")
    shade = picture.convert("L")
    if ImageStat.Stat(shade, alpha.point(lambda a: 255 if a > 128 else 0)).mean[0] < DARK:
        low, high = LIGHT
        light = shade.point(lambda value: round(high - value / 255 * (high - low)))
        Image.merge("RGBA", (light, light, light, alpha)).save(target)
    elif path.endswith(".png"):
        target = Path(path)
    ICONS[path] = str(target)
    return ICONS[path]


def theme() -> None:
    """Put the card's colours, fonts and node sizes into the defaults of the diagrams package.

    Nodes get sequential ids instead of random ones, so the same source always gives the same graph, and the edges
    are splines, which keep their labels beside them, unless a diagram sets splines in its graph_attr.
    """
    Diagram._default_graph_attrs = {
        "bgcolor": BACKGROUND,
        "pad": "0.05",
        "nodesep": "0.45",
        "ranksep": "0.6",
        "fontname": SANS,
        "fontsize": "18",
        "fontcolor": TEXT,
    }
    Diagram._default_node_attrs = {
        "shape": "plain",
        "fontname": SANS,
        "fontsize": NODE_FONT,
        "fontcolor": TEXT,
    }
    Diagram._default_edge_attrs = {"color": EDGE, "penwidth": "1.4", "arrowsize": "0.8"}
    Edge._default_edge_attrs = {"fontcolor": LABEL, "fontname": MONO, "fontsize": EDGE_FONT}
    Cluster._default_graph_attrs = {
        "shape": "box",
        "style": "rounded",
        "labeljust": "l",
        "pencolor": CARD_BORDER,
        "penwidth": "1.4",
        "fontname": MONO,
        "fontsize": CLUSTER_FONT,
        "fontcolor": LABEL,
        "margin": "14",
    }
    Cluster._Cluster__bgcolors = CLUSTERS

    load = Node._load_icon
    Node._load_icon = lambda self: icon(load(self))
    custom = Custom._load_icon
    Custom._load_icon = lambda self: icon(custom(self))
    Node._rand_id = staticmethod(lambda: f"node{next(IDS)}")

    node_init = Node.__init__

    def icon_node(self, label="", *, nodeid=None, **attrs):
        """Create an icon node as a table of its icon above its label, so the label takes its place in the layout."""
        if self._icon and "image" not in attrs:
            lines = "<BR/>".join(html.escape(line) for line in label.split("\n")) if label else ""
            text = f'<TR><TD HEIGHT="{GAP}"></TD></TR><TR><TD>{lines}</TD></TR>' if lines else ""
            label = (
                '<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="0">'
                f'<TR><TD FIXEDSIZE="TRUE" WIDTH="{ICON}" HEIGHT="{ICON}"><IMG SRC="{self._load_icon()}" SCALE="TRUE"/>'
                f"</TD></TR>{text}</TABLE>>"
            )
            attrs = {"shape": "plain", "image": "", "height": "0", **attrs}
        node_init(self, label, nodeid=nodeid, **attrs)

    Node.__init__ = icon_node

    diagram_init = Diagram.__init__

    def spline_diagram(self, *args, **kwargs):
        """Create the diagram, with spline edges unless its graph_attr sets splines."""
        diagram_init(self, *args, **kwargs)
        if "splines" not in (kwargs.get("graph_attr") or {}):
            self.dot.graph_attr["splines"] = "spline"

    Diagram.__init__ = spline_diagram

    def keep_graph(self, *exc):
        """Keep the finished graph instead of rendering it to a file."""
        GRAPHS.append(self.dot.source)
        setdiagram(None)

    Diagram.__exit__ = keep_graph


def render(source: Path, inner: int) -> None:
    """Render one source to a PNG of the same name beside it and print its size.

    Args:
        source: The .py file.
        inner: Width in card pixels a diagram may take before it shrinks.

    Raises:
        SystemExit: The source draws no diagram, or more than one.
        subprocess.CalledProcessError: Graphviz fails on the graph.
    """
    GRAPHS.clear()
    runpy.run_path(str(source), run_name="__main__")
    if len(GRAPHS) != 1:
        sys.exit(f"{source}: draws {len(GRAPHS)} diagrams instead of one")
    graph = GRAPHS[0]
    plain = subprocess.run(["dot", "-Tplain"], input=graph, capture_output=True, text=True, check=True).stdout
    natural = float(plain.split("\n", 1)[0].split()[2]) * 72
    dpi = 72 * SCALE * min(1, inner / natural)
    raw = WORK / "graph.png"
    drawn = subprocess.run(
        ["dot", f"-Gdpi={dpi:.3f}", "-Tpng", "-o", str(raw)], input=graph, capture_output=True, text=True, check=True
    )
    if drawn.stderr:
        print(f"{source}: {drawn.stderr.strip()}", file=sys.stderr)
    picture = Image.open(raw).convert("RGB")
    width = CARD_WIDTH * SCALE
    height = picture.height + 2 * (PAD_Y + BORDER) * SCALE
    card = Image.new("RGB", (width, height), BACKGROUND)
    ImageDraw.Draw(card).rectangle([0, 0, width - 1, height - 1], outline=CARD_BORDER, width=BORDER * SCALE)
    card.paste(picture, ((width - picture.width) // 2, (PAD_Y + BORDER) * SCALE))
    target = source.with_suffix(".png")
    card.save(target, optimize=True)
    shrunk = f", diagram {round(natural)} px shrunk to fit {inner} px" if natural > inner else ""
    print(f"{target} {width}x{height}{shrunk}")


theme()
inner = CARD_WIDTH - 2 * PAD_X - 2 * BORDER
for name in sys.argv[1:]:
    render(Path(name), inner)
PY
}

shopt -s nullglob
if [ "$#" -eq 0 ]; then
  set -- docs/diagrams/*.py docs/diagrams/*.mmd
fi
icons=()
sequences=()
for source in "$@"; do
  if [ ! -f "$source" ]; then
    echo "render-diagrams: no such source $source" >&2
    exit 1
  fi
  relative="$(realpath --relative-to=. "$source")"
  case "$relative" in
    ../*) echo "render-diagrams: $source is outside the repository" >&2; exit 1 ;;
    *.py) icons+=("$relative") ;;
    *.mmd) sequences+=("$relative") ;;
    *) echo "render-diagrams: $source is neither a .py nor a .mmd source" >&2; exit 1 ;;
  esac
done
if [ "${#icons[@]}" -gt 0 ]; then
  render_icons "${icons[@]}"
fi
if [ "${#sequences[@]}" -gt 0 ]; then
  render_sequences "${sequences[@]}"
fi
