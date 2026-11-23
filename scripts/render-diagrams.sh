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

Each source is a Python file that draws one diagram with the diagrams package on a grid. Every node names its place
with grid="column,row", in columns that follow the flow from left to right and rows that may be fractional, and every
visible edge describes its step with step="...". The renderer puts the card's look into the package's defaults, keeps
the Graphviz graph instead of writing a file, places the nodes on the grid with neato and routes the edges at right
angles. Each edge carries a numbered marker, and a legend strip at the bottom of the card lists the steps; an edge that
repeats a step's text repeats its number. main="true" marks the main path in the accent colour.

A node shows its icon above its role, the first line of its label, and the tool, any further line, in the muted
size; a node without an icon is a box with the same two lines, dashed with dashed="true". Logos keep their colours,
while cloud and generic icons, which stand in for tools without a logo, and dark monochrome icons are redrawn in one
neutral grey; SVG icons, such as the mark in packages/brand, are converted to PNG. Clusters are drawn as hairline
boxes around their nodes with the title at the top left and, with graph_attr={"icon": <node class>}, a logo beside
it. A diagram may set the grid's step in points with graph_attr={"column": ..., "row": ...}.

The renderer reports a diagram wider than the card, which is drawn smaller, an edge that crosses another edge and an
edge through a cluster title, so that the grid can be laid out again.
"""

import html
import itertools
import json
import math
import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path

from diagrams import Cluster, Diagram, Edge, Node, getcluster, setdiagram
from diagrams.custom import Custom
from PIL import Image, ImageDraw, ImageFont, ImageStat

CARD_WIDTH = 920
SCALE = 2
PAD_X = 24
PAD_Y = 24
BORDER = 2

BACKGROUND = "#0a0a0a"
CARD_BORDER = "#3a3a3a"
TEXT = "#ededed"
MUTED = "#9a9a9a"
HAIRLINE = "#333333"
CLUSTER_FILL = ("#101010", "#161616")
BOX_FILL = "#161616"
BOX_BORDER = "#4a4a4a"
EDGE = "#6b6b6b"
ACCENT = "#e8a33d"
BADGE_FILL = "#3a3a3a"
NEUTRAL = (196, 196, 196)

SANS = "Ubuntu Sans"
SANS_STRONG = "Ubuntu Sans Medium"
MONO = "Ubuntu Sans Mono"
SANS_FILE = "/usr/share/fonts/truetype/ubuntu/UbuntuSans[wdth,wght].ttf"
LARGE = 16
SMALL = 13

ICON = 48
NODE_WIDTH = 88
BOX_WIDTH = 124
GAP = 6
BADGE = 18
MARKER_CLEAR = 10

COLUMN = 200
ROW = 100
MARGIN = 12
TITLE = 22
LEGEND_LINE = 22

DARK = 80
STAND_INS = ("/resources/aws/", "/resources/generic/")

WORK = Path(tempfile.mkdtemp())
ICONS: dict[str, str] = {}
GRAPHS: list = []
IDS = itertools.count()
GRID: dict[str, tuple[float, float]] = {}
CLUSTERS: list[dict] = []
STEPS: list[tuple[str, bool]] = []


def icon(path: str) -> str:
    """Return a PNG of an icon in the card's icon language.

    Args:
        path: The icon's PNG or SVG file.

    Returns:
        The file Graphviz draws: a logo as it is, or converted from SVG, and a stand-in or dark monochrome icon
        redrawn in the neutral grey.
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
    dark = ImageStat.Stat(shade, alpha.point(lambda a: 255 if a > 128 else 0)).mean[0] < DARK
    if dark or any(part in path for part in STAND_INS):
        grey = [Image.new("L", picture.size, value) for value in NEUTRAL]
        if dark:
            ink = shade.point(lambda value: 255 if value < 160 else 0)
            alpha = Image.composite(alpha, Image.new("L", picture.size, 0), ink)
        Image.merge("RGBA", (*grey, alpha)).save(target)
    elif path.endswith(".png"):
        target = Path(path)
    ICONS[path] = str(target)
    return ICONS[path]


def font(text: str, face: str, size: int, colour: str) -> str:
    """Return HTML-like Graphviz markup of escaped text in one face, size and colour.

    Args:
        text: The text, plain.
        face: Font name.
        size: Size in points.
        colour: Colour.

    Returns:
        The markup.
    """
    return f'<FONT FACE="{face}" POINT-SIZE="{size}" COLOR="{colour}">{html.escape(text)}</FONT>'


def lines(label: str, width: int, mono: bool, keep: bool) -> str:
    """Return the table rows of a node's role and tool lines.

    Args:
        label: The node's label: the role on the first line and the tool on the next, if any.
        width: Least width of the rows in points.
        mono: Whether the role is a NATS subject, set in the monospaced face.
        keep: Whether to keep an empty tool row when the label has one line, so icon nodes share their height.

    Returns:
        The rows.
    """
    role, _, tool = label.partition("\n")
    face = MONO if mono else SANS_STRONG
    rows = f'<TR><TD WIDTH="{width}">{font(role, face, LARGE, TEXT)}</TD></TR>'
    if tool:
        rows += f"<TR><TD>{font(tool.replace(chr(10), ', '), SANS, SMALL, MUTED)}</TD></TR>"
    elif keep:
        rows += f'<TR><TD HEIGHT="{SMALL + 3}"></TD></TR>'
    return rows


def theme() -> None:
    """Put the card's look and the grid into the defaults of the diagrams package.

    Nodes get sequential ids instead of random ones, so the same source always gives the same graph.
    """
    Diagram._default_graph_attrs = {
        "bgcolor": "transparent",
        "pad": "0",
        "splines": "ortho",
        "outputorder": "edgesfirst",
        "fontname": SANS,
        "fontsize": str(SMALL),
        "fontcolor": MUTED,
    }
    Diagram._default_node_attrs = {"shape": "plain", "fontname": SANS_STRONG, "fontsize": str(LARGE), "fontcolor": TEXT}
    Diagram._default_edge_attrs = {"color": EDGE, "penwidth": "1.2", "arrowsize": "0.7"}
    Edge._default_edge_attrs = {"fontcolor": MUTED, "fontname": SANS, "fontsize": str(SMALL)}
    Cluster._default_graph_attrs = {"label": ""}

    load = Node._load_icon
    Node._load_icon = lambda self: icon(load(self))
    custom = Custom._load_icon
    Custom._load_icon = lambda self: icon(custom(self))
    Node._rand_id = staticmethod(lambda: f"node{next(IDS)}")

    node_init = Node.__init__

    def card_node(self, label="", *, nodeid=None, **attrs):
        """Create a node in the card's style at its place on the grid.

        An icon node becomes a table of its icon above its role and tool lines, and a node without an icon a rounded
        box with the same two lines.
        """
        mono = attrs.pop("mono", "") == "true"
        dashed = attrs.pop("dashed", "") == "true"
        column, _, row = attrs.pop("grid", "").partition(",")
        if self._icon:
            label = (
                '<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="0" CELLPADDING="0">'
                f'<TR><TD FIXEDSIZE="TRUE" WIDTH="{ICON}" HEIGHT="{ICON}"><IMG SRC="{self._load_icon()}" SCALE="TRUE"/>'
                f'</TD></TR><TR><TD HEIGHT="{GAP}"></TD></TR>{lines(label, NODE_WIDTH, mono, True)}</TABLE>>'
            )
            attrs = {"shape": "none", "margin": "0.08,0.02", **attrs}
        else:
            style = "rounded,dashed" if dashed else "rounded"
            label = (
                f'<<TABLE BORDER="1" STYLE="{style}" COLOR="{BOX_BORDER}" BGCOLOR="{BOX_FILL}" CELLBORDER="0" '
                f'CELLSPACING="0" CELLPADDING="4">{lines(label, BOX_WIDTH, mono, False)}</TABLE>>'
            )
        attrs = {"shape": "plain", "image": "", "height": "0", **attrs}
        node_init(self, label, nodeid=nodeid, **attrs)
        if not column or not row:
            sys.exit(f"node {self.label!r}: give its place with grid='column,row'")
        GRID[self.nodeid] = (float(column), float(row))
        cluster = getcluster()
        while cluster is not None:
            cluster.members.append(self.nodeid)
            cluster = cluster._parent

    Node.__init__ = card_node

    edge_init = Edge.__init__

    def card_edge(self, *args, **attrs):
        """Create an edge with the number of its step, in the accent colour when marked main."""
        main = attrs.pop("main", "") == "true"
        step = attrs.pop("step", "")
        if main:
            attrs = {"color": ACCENT, "penwidth": "2", **attrs}
        if step:
            texts = [text for text, _ in STEPS]
            if step not in texts:
                STEPS.append((step, main))
            number = texts.index(step) + 1 if step in texts else len(STEPS)
            attrs = {"marker": str(number), "markermain": "1" if main else "0", **attrs}
        edge_init(self, *args, **attrs)

    Edge.__init__ = card_edge

    cluster_init = Cluster.__init__

    def card_cluster(self, label="cluster", direction="LR", graph_attr=None):
        """Create a cluster and remember its title, logo and nodes, which the renderer draws itself."""
        graph_attr = dict(graph_attr or {})
        logo = graph_attr.pop("icon", None)
        cluster_init(self, label, direction, graph_attr)
        self.members = []
        self.logo = None
        if logo is not None:
            root = Path(sys.modules["diagrams"].__file__).parent.parent
            self.logo = icon(os.path.join(root, logo._icon_dir, logo._icon))
        CLUSTERS.append(self)

    Cluster.__init__ = card_cluster

    def keep_graph(self, *exc):
        """Keep the finished diagram instead of rendering it to a file."""
        GRAPHS.append(self)
        setdiagram(None)

    Diagram.__exit__ = keep_graph


def points(spline: str) -> list[tuple[float, float]]:
    """Return the points of an edge's route from its Graphviz pos attribute.

    Args:
        spline: The pos attribute, such as "e,10,20 0,0 10,0".

    Returns:
        The points from tail to head, the arrow's tip last.
    """
    tip = None
    route = []
    for part in spline.split():
        if part.startswith("e,"):
            tip = tuple(float(value) for value in part[2:].split(","))
        elif not part.startswith("s,"):
            route.append(tuple(float(value) for value in part.split(",")))
    return route + ([tip] if tip else [])


def crosses(a, b, c, d) -> bool:
    """Tell whether segment ab properly crosses segment cd.

    Args:
        a: First end of the first segment.
        b: Second end of the first segment.
        c: First end of the second segment.
        d: Second end of the second segment.

    Returns:
        True when they cross at a point inside both.
    """

    def side(p, q, r):
        """Return the sign of the turn from pq to pr."""
        value = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (value > 1e-6) - (value < -1e-6)

    return side(a, b, c) * side(a, b, d) < 0 and side(c, d, a) * side(c, d, b) < 0


def overlap(a, b) -> bool:
    """Tell whether two boxes, each left, bottom, right and top, overlap.

    Args:
        a: The first box.
        b: The second box.

    Returns:
        True when they share any area.
    """
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def hits(segment, box) -> bool:
    """Tell whether a segment touches a box, by clipping it to the box.

    Args:
        segment: The segment's two ends.
        box: The box as left, bottom, right and top.

    Returns:
        True when part of the segment lies in the box.
    """
    (x0, y0), (x1, y1) = segment
    low, high = 0.0, 1.0
    for start, delta, lower, upper in ((x0, x1 - x0, box[0], box[2]), (y0, y1 - y0, box[1], box[3])):
        if abs(delta) < 1e-9:
            if not lower <= start <= upper:
                return False
            continue
        first, second = (lower - start) / delta, (upper - start) / delta
        low, high = max(low, min(first, second)), min(high, max(first, second))
        if low > high:
            return False
    return True


def marker_place(route, half, fits):
    """Return where a step marker sits on its edge: the free point nearest the middle of the longest segment.

    Args:
        route: The edge's points.
        half: Half the side of the marker with its clearance, in points.
        fits: Tells whether a box, left, bottom, right and top, is free.

    Returns:
        The marker's centre, or None when no point of the edge is free.
    """
    segments = sorted(zip(route, route[1:]), key=lambda pair: -math.dist(*pair))
    for (x0, y0), (x1, y1) in segments:
        length = math.dist((x0, y0), (x1, y1))
        if length < 2 * half:
            continue
        offsets = sorted(range(int((length - 2 * half) // 2) + 1), key=lambda k: abs(half + 2 * k - length / 2))
        for k in offsets:
            share = (half + 2 * k) / length
            x, y = x0 + (x1 - x0) * share, y0 + (y1 - y0) * share
            if fits((x - half, y - half, x + half, y + half)):
                return x, y
    return None


def inside(segment, box) -> bool:
    """Tell whether a segment passes through a box.

    Args:
        segment: The segment's two ends.
        box: The box as left, bottom, right and top.

    Returns:
        True when part of the segment lies inside the box.
    """
    (x0, y0), (x1, y1) = segment
    left, bottom, right, top = box
    for step in range(21):
        x = x0 + (x1 - x0) * step / 20
        y = y0 + (y1 - y0) * step / 20
        if left < x < right and bottom < y < top:
            return True
    return False


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
    GRID.clear()
    CLUSTERS.clear()
    STEPS.clear()
    runpy.run_path(str(source), run_name="__main__")
    if len(GRAPHS) != 1:
        sys.exit(f"{source}: draws {len(GRAPHS)} diagrams instead of one")
    dot = GRAPHS[0].dot
    column = float(dot.graph_attr.pop("column", COLUMN))
    row = float(dot.graph_attr.pop("row", ROW))
    for nodeid, (x, y) in GRID.items():
        dot.body.append(f'\t{nodeid} [pos="{x * column:.1f},{-y * row:.1f}"]\n')
    graph = dot.source

    laid = json.loads(
        subprocess.run(
            ["neato", "-n2", "-Tjson0"], input=graph.encode(), capture_output=True, check=True
        ).stdout.decode(errors="replace")
    )
    boxes = {}
    for item in laid.get("objects", []):
        if "pos" in item and "width" in item:
            x, y = (float(value) for value in item["pos"].split(","))
            half_w, half_h = float(item["width"]) * 36, float(item["height"]) * 36
            boxes[item["name"]] = (x - half_w, y - half_h, x + half_w, y + half_h)
    sans = ImageFont.truetype(SANS_FILE, 100)
    sans.set_variation_by_name("Regular")
    titles = {}
    frames = {}
    for cluster in sorted(CLUSTERS, key=lambda item: -item.depth):
        parts = [boxes[name] for name in cluster.members] + [
            frames[id(child)] for child in CLUSTERS if child._parent is cluster
        ]
        width = sans.getlength(cluster.label) * SMALL / 100 + (SMALL + 11 if cluster.logo else 0)
        left = min(part[0] for part in parts) - MARGIN
        bottom = min(part[1] for part in parts) - MARGIN
        right = max([part[2] for part in parts] + [left + 2 * MARGIN + width]) + MARGIN
        top = max(part[3] for part in parts) + MARGIN + TITLE
        if right - left < 2 * MARGIN + width:
            right = left + 2 * MARGIN + width
        frames[id(cluster)] = (left, bottom, right, top)
        titles[id(cluster)] = (left + MARGIN, top - TITLE - 2, left + MARGIN + width, top - 4)

    def nested(outer, inner) -> bool:
        """Tell whether one cluster lies inside the other."""
        parent = inner._parent
        while parent is not None:
            if parent is outer:
                return True
            parent = parent._parent
        return False

    for index, cluster in enumerate(CLUSTERS):
        for other in CLUSTERS[index + 1 :]:
            a, b = frames[id(cluster)], frames[id(other)]
            apart = a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]
            if not apart and not nested(cluster, other) and not nested(other, cluster):
                print(f"{source}: clusters {cluster.label} and {other.label} overlap", file=sys.stderr)

    edges = [points(item["pos"]) for item in laid.get("edges", []) if "pos" in item]
    for index, route in enumerate(edges):
        for other in edges[index + 1 :]:
            if any(crosses(a, b, c, d) for a, b in zip(route, route[1:]) for c, d in zip(other, other[1:])):
                print(f"{source}: two edges cross near {route[0]}", file=sys.stderr)
        for cluster in CLUSTERS:
            if any(inside(segment, titles[id(cluster)]) for segment in zip(route, route[1:])):
                print(f"{source}: an edge crosses the title of {cluster.label}", file=sys.stderr)

    bb = [float(value) for value in laid["bb"].split(",")]
    extent = [
        min([bb[0]] + [frame[0] for frame in frames.values()]) - 2,
        min([bb[1]] + [frame[1] for frame in frames.values()]) - 2,
        max([bb[2]] + [frame[2] for frame in frames.values()]) + 2,
        max([bb[3]] + [frame[3] for frame in frames.values()]) + 2,
    ]
    natural = extent[2] - extent[0]
    scale = SCALE * min(1, inner / natural)
    raw = WORK / "graph.png"
    drawn = subprocess.run(
        ["neato", "-n2", f"-Gdpi={72 * scale:.3f}", "-Tpng", "-o", str(raw)],
        input=graph,
        capture_output=True,
        text=True,
        check=True,
    )
    if drawn.stderr:
        print(f"{source}: {drawn.stderr.strip()}", file=sys.stderr)

    def pixel(x: float, y: float) -> tuple[int, int]:
        """Return the picture pixel of a point of the graph."""
        return round((x - extent[0]) * scale), round((extent[3] - y) * scale)

    picture = Image.new("RGBA", pixel(extent[2], extent[1]), BACKGROUND)
    draw = ImageDraw.Draw(picture)
    title_font = ImageFont.truetype(SANS_FILE, round(SMALL * scale))
    title_font.set_variation_by_name("Regular")
    for cluster in sorted(CLUSTERS, key=lambda item: item.depth):
        left, bottom, right, top = frames[id(cluster)]
        draw.rounded_rectangle(
            [pixel(left, top), pixel(right, bottom)],
            radius=round(8 * scale),
            fill=CLUSTER_FILL[min(cluster.depth, len(CLUSTER_FILL) - 1)],
            outline=HAIRLINE,
            width=max(1, round(scale)),
        )
        x, y = pixel(left + MARGIN, top - 6)
        if cluster.logo:
            size = round((SMALL + 5) * scale)
            logo = Image.open(cluster.logo).convert("RGBA").resize((size, size), Image.LANCZOS)
            picture.alpha_composite(logo, (x, y))
            x += size + round(6 * scale)
        draw.text((x, y + round(2 * scale)), cluster.label, font=title_font, fill=MUTED)
    layer = Image.open(raw).convert("RGBA")
    picture.alpha_composite(layer, pixel(bb[0], bb[3]))

    number_font = ImageFont.truetype(SANS_FILE, round(SMALL * SCALE))
    number_font.set_variation_by_name("Medium")
    marker_font = ImageFont.truetype(SANS_FILE, round(SMALL * scale))
    marker_font.set_variation_by_name("Medium")
    clear = MARKER_CLEAR * SCALE / scale
    half = BADGE / 2 + clear
    taken: list[tuple[float, float, float, float]] = []
    marked = [item for item in laid.get("edges", []) if "pos" in item and item.get("marker")]
    for item in marked:
        route = points(item["pos"])
        others = [points(other["pos"]) for other in laid.get("edges", []) if "pos" in other and other is not item]
        arrows = [(path[-2], path[-1]) for path in others + [route] if len(path) > 1]

        def fits(box) -> bool:
            """Tell whether a marker box keeps clear of nodes, arrows, cluster borders and titles, markers and edges."""
            core = (box[0] + clear, box[1] + clear, box[2] - clear, box[3] - clear)
            if any(overlap(box, node) for node in boxes.values()):
                return False
            if any(overlap(box, title) for title in titles.values()) or any(overlap(box, mark) for mark in taken):
                return False
            for frame in frames.values():
                within = frame[0] <= box[0] and frame[1] <= box[1] and box[2] <= frame[2] and box[3] <= frame[3]
                if overlap(box, frame) and not within:
                    return False
            if any(hits(arrow, box) for arrow in arrows):
                return False
            return not any(hits(segment, core) for other in others for segment in zip(other, other[1:]))

        place = marker_place(route, half, fits)
        if place is None:
            print(f"{source}: marker {item['marker']} has no clear place on its edge", file=sys.stderr)
            continue
        taken.append((place[0] - half, place[1] - half, place[0] + half, place[1] + half))
        x, y = pixel(*place)
        size = BADGE * scale
        fill, ink = (ACCENT, BACKGROUND) if item.get("markermain") == "1" else (BADGE_FILL, TEXT)
        draw.rounded_rectangle(
            [x - size / 2, y - size / 2, x + size / 2, y + size / 2], radius=round(4 * scale), fill=fill
        )
        draw.text((x, y), item["marker"], font=marker_font, fill=ink, anchor="mm")

    legend = ImageFont.truetype(SANS_FILE, round(SMALL * SCALE))
    legend.set_variation_by_name("Regular")
    width = CARD_WIDTH * SCALE
    usable = width - 2 * (PAD_X + BORDER) * SCALE
    longest = max((legend.getlength(text) for text, _ in STEPS), default=0) + (BADGE + 10 + 24) * SCALE
    columns = next((count for count in (3, 2) if count <= len(STEPS) and longest * count <= usable), 1)
    per_column = -(-len(STEPS) // columns)
    line = LEGEND_LINE * SCALE
    legend_height = (per_column * line + 2 * PAD_Y * SCALE // 2) if STEPS else 0
    height = picture.height + legend_height + 2 * (PAD_Y + BORDER) * SCALE
    card = Image.new("RGB", (width, height), BACKGROUND)
    draw = ImageDraw.Draw(card)
    draw.rectangle([0, 0, width - 1, height - 1], outline=CARD_BORDER, width=BORDER * SCALE)
    top = (PAD_Y + BORDER) * SCALE
    card.paste(picture.convert("RGB"), ((width - picture.width) // 2, top))
    if STEPS:
        left = (PAD_X + BORDER) * SCALE
        rule = top + picture.height + PAD_Y * SCALE // 2
        draw.line([(left, rule), (width - left, rule)], fill=HAIRLINE, width=SCALE)
        column_width = (width - 2 * left) // columns
        for index, (text, main) in enumerate(STEPS):
            x = left + (index // per_column) * column_width
            y = rule + PAD_Y * SCALE // 2 + (index % per_column) * line
            size = BADGE * SCALE
            fill, ink = (ACCENT, BACKGROUND) if main else (BADGE_FILL, TEXT)
            draw.rounded_rectangle([x, y, x + size, y + size], radius=round(4 * SCALE), fill=fill)
            draw.text((x + size / 2, y + size / 2), str(index + 1), font=number_font, fill=ink, anchor="mm")
            draw.text((x + size + 10 * SCALE, y + size / 2), text, font=legend, fill=MUTED, anchor="lm")
    target = source.with_suffix(".png")
    card.save(target, optimize=True)
    shrunk = f", diagram {round(natural)} px shrunk to fit {inner} px" if natural > inner else ""
    print(f"{target} {width}x{height} natural {round(natural)} px{shrunk}")


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
