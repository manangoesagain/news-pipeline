"""Static PNG rendering of the connection-map graph (for Telegram / email).

The web page holds the full interactive map.  A PNG cannot show 40+ nodes and 80
labelled links legibly on a phone, so this renders the 3-4 strongest causal chains as
numbered "story" cards stacked top to bottom.  Each story reads left to right (a chain
of 4-5 items wraps onto a second line): big nodes (shape = type, colour = domain,
market change on market nodes) joined by arrows that each carry the mechanism
(solid = reported, dashed = predicted, red dotted = contradicts, thicker = stronger,
triangle = effect on the next item).  Below the stories: a compact legend, the
"Biggest movers" and a pointer to the web page.

Geometry is in pixels on a 2400px-wide canvas (dpi 100, so 1px = 0.72pt).  Telegram
shrinks photos to ~1080px (x0.45), so 50px text lands at ~22px on a phone.
Output is deterministic (no randomness).
"""
import base64
import io
import math
import re

W = 2400
MARGIN = 80
CARD_PAD = 40
MAX_H = 3400
MAX_BYTES = 2_500_000
MAX_STORIES = 4
DEBUG = False  # tests: re-raise instead of returning None
INK, MUTED, SURFACE, LINE = "#1c1b19", "#55534d", "#fbfaf7", "#3a3935"
CARD, CARD_EDGE = "#ffffff", "#d6d2c8"
# Polarity accents (glyphs + arrowheads only; text stays in ink): raises blue, lowers red.
# "cuts against" is amber + dotted + a = glyph, so it never reads as "lowers".
RAISE, LOWER, CONTRA = "#2a78d6", "#e34948", "#b97a00"
POL = {"up": ("▲", RAISE), "down": ("▼", LOWER), "mixed": ("↕", INK)}
FONT_NODE, FONT_TAG, FONT_EDGE, FONT_LEG = 50, 36, 48, 48
NODE_W, GAP3, GAP2 = 490, 350, 500

# (name, tag, colour) - colour-blind-checked categorical slots (validate_palette.js,
# light surface #fbfaf7: all checks pass, contrast WARN relieved by tag + legend labels).
DOMAINS = [
    ("AI & ML", "AI", "#2a78d6"),
    ("Technology & Semiconductors", "TECH", "#eb6834"),
    ("Finance & Banking", "FIN", "#1baf7a"),
    ("Stock Markets & Trading", "MKT", "#eda100"),
    ("Geopolitics & Diplomacy", "GEO", "#e87ba4"),
    ("Wars & Conflicts", "WAR", "#008300"),
    ("Commodities & Prices", "CMD", "#6250d6"),
]
OTHER = ("Other", "OTH", "#6b7280")
SHORT = {"Technology & Semiconductors": "Tech & Chips", "Finance & Banking": "Finance",
         "Stock Markets & Trading": "Markets", "Geopolitics & Diplomacy": "Geopolitics",
         "Wars & Conflicts": "Wars", "Commodities & Prices": "Commodities"}
_DOMAIN_KEYS = [
    ("ai", 0), ("machine", 0), ("tech", 1), ("semi", 1), ("financ", 2), ("bank", 2),
    ("stock", 3), ("market", 3), ("trad", 3), ("geopol", 4), ("diplom", 4),
    ("war", 5), ("conflict", 5), ("commod", 6), ("price", 6), ("oil", 6),
]
# type -> (shape, legend name)
TYPES = {
    "event": ("round", "event"), "company": ("rect", "company"),
    "country": ("hex", "country"), "sector": ("para", "sector"),
    "commodity": ("snip", "commodity"), "index": ("pill", "index / market"),
    "market": ("pill", "index / market"), "institution": ("double", "institution"),
}
LEGEND_TYPES = ["event", "company", "country", "sector", "commodity", "index", "institution"]


# ------------------------------------------------------------------ helpers
def _domain_index(name):
    n = str(name or "").strip().lower()
    if not n:
        return -1
    for i, d in enumerate(DOMAINS):
        if n == d[0].lower():
            return i
    for key, i in _DOMAIN_KEYS:
        if re.search(r"\b" + key, n):
            return i
    return -1


def _clean(text):
    text = re.sub(r"\s*\((?:ids?|per [^()]*?id)\s*[\d,\s]+(?:and\s*\d+)?\)", "", str(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _tint(hexc, k=0.91):
    h = hexc.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % tuple(int(c + (255 - c) * k) for c in (r, g, b))


def _int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except Exception:
        return default


def _pct(mk):
    """Signed percentage change of a market dict, or None."""
    if not isinstance(mk, dict):
        return None
    for key in ("change_pct", "cp"):
        try:
            v = float(mk.get(key))
            if v == v:
                return v
        except Exception:
            pass
    txt = str(mk.get("change") or "")
    m = re.search(r"\d+(?:\.\d+)?", txt)
    if not m:
        return None
    v = float(m.group())
    return -v if re.search(r"[-−▼▽]", txt) else v


def _chg(mk, pct):
    c = str(mk.get("change") or "").strip().lstrip("▲▼△▽ ").strip() if mk else ""
    return c or ("%+.2f%%" % pct)


class _Meas:
    """Text measurement in pixels at dpi 100 (matches the final render)."""

    def __init__(self):
        from matplotlib.backends.backend_agg import RendererAgg
        self.r = RendererAgg(200, 200, 100)
        self.cache = {}

    def w(self, text, px, weight="normal"):
        key = (text, px, weight)
        if key not in self.cache:
            from matplotlib.font_manager import FontProperties
            fp = FontProperties(size=px * 0.72, weight=weight)
            self.cache[key] = self.r.get_text_width_height_descent(text, fp, False)[0]
        return self.cache[key]

    def wrap(self, text, px, maxw, maxlines, weight="normal"):
        words = str(text).split()
        lines, cur = [], ""
        for wd in words:
            if self.w(wd, px, weight) > maxw and "-" in wd.strip("-"):  # break at existing hyphens
                parts = re.findall(r"[^-]+-?|-", wd)
                for pi, part in enumerate(parts):
                    t = (cur + part) if (cur and pi) else ((cur + " " + part).strip() if cur else part)
                    if cur and self.w(t, px, weight) > maxw:
                        lines.append(cur)
                        cur = part
                    else:
                        cur = t
                continue
            while self.w(wd, px, weight) > maxw and len(wd) > 3:  # break overlong word
                k = len(wd)
                while k > 1 and self.w(wd[:k] + "-", px, weight) > maxw:
                    k -= 1
                if cur:
                    lines.append(cur)
                    cur = ""
                lines.append(wd[:k] + "-")
                wd = wd[k:]
            t = (cur + " " + wd).strip()
            if cur and self.w(t, px, weight) > maxw:
                lines.append(cur)
                cur = wd
            else:
                cur = t
        if cur:
            lines.append(cur)
        if len(lines) > maxlines:
            lines = lines[:maxlines]
            last = lines[-1]
            while last and self.w(last + "…", px, weight) > maxw:
                last = last[:-1]
            lines[-1] = last.rstrip() + "…"
        return lines or [""]


def _fit(meas, text, px, maxw, weight="normal"):
    while len(text) > 4 and meas.w(text, px, weight) > maxw:
        text = text[:-2].rstrip() + "…"
    return text


# ------------------------------------------------------------------ model
def _parse(graph):
    raw_nodes = [x for x in (graph or {}).get("nodes", []) or [] if isinstance(x, dict) and x.get("id") is not None][:300]
    idx, nodes = {}, []
    for nd in raw_nodes:
        if nd["id"] in idx:
            continue
        idx[nd["id"]] = len(nodes)
        typ = str(nd.get("type") or "event").strip().lower()
        if typ not in TYPES:
            typ = "event"
        di = _domain_index(nd.get("domain"))
        dom = DOMAINS[di] if di >= 0 else OTHER
        label = _clean(nd.get("label") or nd["id"]) or str(nd["id"])
        mk = nd.get("market") if isinstance(nd.get("market"), dict) else None
        pct = _pct(mk)
        direction = str(nd.get("direction") or "").lower()
        glyph = ""
        if pct is not None:
            glyph = "▲" if pct > 0 else "▼" if pct < 0 else "–"
        elif direction in ("up", "down"):
            glyph = "▲" if direction == "up" else "▼"
        nodes.append(dict(id=nd["id"], label=label, typ=typ, shape=TYPES[typ][0], dom=dom, pct=pct,
                          mk=mk if pct is not None else None, glyph=glyph))
    edges = {}
    for e in (graph or {}).get("edges", []) or []:
        if not isinstance(e, dict):
            continue
        a, b = idx.get(e.get("from")), idx.get(e.get("to"))
        if a is None or b is None or a == b:
            continue
        kind = str(e.get("kind") or "causal").lower()
        if kind not in ("causal", "feedback", "contradicts"):
            kind = "causal"
        conf = {"low": 0, "medium": 1, "high": 2}.get(str(e.get("confidence") or "medium").lower(), 1)
        rep = str(e.get("status") or "reported").lower() == "reported"
        s = _int(e.get("strength"), 1, 3, 1)
        eff = str(e.get("effect") or "").lower()
        rec = dict(a=a, b=b, label=_clean(e.get("label")), effect=eff if eff in ("up", "down", "mixed") else "",
                   strength=s, conf=conf, reported=rep, kind=kind, score=3 * s + conf + (1 if rep else 0))
        if (a, b) not in edges or edges[(a, b)]["score"] < rec["score"]:
            edges[(a, b)] = rec
    return nodes, edges


def _chains(nodes, edges, minlen):
    """Top causal chains (3-5 nodes), scored, overlapping by at most half."""
    n = len(nodes)
    deg = [0] * n
    has_in = set()
    for (a, b) in edges:
        deg[a] += 1
        deg[b] += 1
        has_in.add(b)
    use = {k: v for k, v in edges.items() if v["kind"] in ("causal", "contradicts")}
    out = {}
    for (a, b), v in use.items():
        out.setdefault(a, []).append((-v["score"], b))
    for a in out:
        out[a] = [b for _, b in sorted(out[a])[:5]]
    cands, budget = [], [40000]

    def walk(path):
        if len(path) >= minlen:
            es = list(zip(path, path[1:]))
            doms = {nodes[i]["dom"][0] for i in path}
            sc = (sum(use[e]["score"] for e in es)
                  + 0.8 * sum(deg[i] for i in path)
                  + 3.0 * sum(1 for i in path if nodes[i]["mk"])
                  + 2.5 * (len(doms) - 1)
                  + (1.0 if path[0] not in has_in else 0.0)
                  + 1.5 * len(path))
            cands.append((sc, tuple(path)))
        if len(path) >= 5:
            return
        for v in out.get(path[-1], []):
            if v not in path and budget[0] > 0:
                budget[0] -= 1
                walk(path + [v])

    for s in range(n):
        walk([s])
    cands.sort(key=lambda c: (-c[0], c[1]))
    chosen = []
    for sc, path in cands:
        ok = True
        for _, other in chosen:
            shared = len(set(path) & set(other))
            if shared > min(len(path), len(other)) // 2 or set(zip(path, path[1:])) & set(zip(other, other[1:])):
                ok = False
                break
        if ok:
            chosen.append((sc, path))
            if len(chosen) >= 8:
                break
    return chosen, use


# ------------------------------------------------------------------ drawing primitives
def _shape(ax, kind, cx, cy, w, h, fc, ec, lw, z=2):
    from matplotlib.patches import FancyBboxPatch, Polygon, Rectangle
    x0, y0 = cx - w / 2, cy - h / 2
    kw = dict(fc=fc, ec=ec, lw=lw * 0.72, zorder=z, joinstyle="round")
    if kind in ("round", "pill"):
        r = min(h / 2, 20 if kind == "round" else 64)
        ax.add_patch(FancyBboxPatch((x0 + r, y0 + r), w - 2 * r, h - 2 * r, boxstyle=f"round,pad={r}", **kw))
        return
    if kind in ("rect", "double"):
        ax.add_patch(Rectangle((x0, y0), w, h, **kw))
        if kind == "double":
            ax.add_patch(Rectangle((x0 + 12, y0 + 12), w - 24, h - 24, fc="none", ec=ec, lw=lw * 0.72 * 0.45, zorder=z))
        return
    if kind == "hex":
        c = min(0.3 * h, 60)
        pts = [(x0, cy), (x0 + c, y0), (x0 + w - c, y0), (x0 + w, cy), (x0 + w - c, y0 + h), (x0 + c, y0 + h)]
    elif kind == "para":
        s = min(0.25 * h, 50)
        pts = [(x0 + s, y0), (x0 + w, y0), (x0 + w - s, y0 + h), (x0, y0 + h)]
    else:  # snip: top corners cut
        s = min(0.2 * h, 44)
        pts = [(x0 + s, y0), (x0 + w - s, y0), (x0 + w, y0 + s), (x0 + w, y0 + h), (x0, y0 + h), (x0, y0 + s)]
    ax.add_patch(Polygon(pts, closed=True, **kw))


def _arrow_head(ax, pts, size, color):
    from matplotlib.patches import Polygon
    import numpy as np
    tip = pts[-1]
    k = len(pts) - 2
    while k > 0 and np.hypot(*(pts[k] - tip)) < 8:
        k -= 1
    d = tip - pts[k]
    n = np.hypot(*d) or 1.0
    d = d / n
    nrm = np.array([-d[1], d[0]])
    base = tip - d * size
    ax.add_patch(Polygon([tip, base + nrm * size * 0.46, base - nrm * size * 0.46], closed=True,
                         fc=color, ec=color, lw=1, zorder=3, joinstyle="round"))
    return base


def _line_style(e):
    if e["kind"] == "contradicts":
        return (0, (0.1, 2.3)), "round"
    if not e["reported"]:
        return (0, (2.6, 1.6)), "butt"
    return "-", "butt"


class _Ctx:
    """Replays queued drawing ops on a matplotlib axes."""

    def __init__(self, ax):
        self.ax = ax

    def text(self, x, y, s, px, color=INK, ha="left", weight="normal", z=6):
        self.ax.text(x, y, s, fontsize=px * 0.72, color=color, ha=ha, va="center", fontweight=weight,
                     zorder=z, parse_math=False)

    def rrect(self, x, y, w, h, r, fc, ec, lw=2, z=1):
        from matplotlib.patches import FancyBboxPatch
        self.ax.add_patch(FancyBboxPatch((x + r, y + r), w - 2 * r, h - 2 * r, boxstyle=f"round,pad={r}",
                                         fc=fc, ec=ec, lw=lw * 0.72, zorder=z))

    def gtext(self, x, y, s, px, color=INK, ha="center", weight="normal", gcol=None, wid=0, gw=0):
        """Centred label whose leading glyph (polarity cue) is drawn in gcol, rest in ink."""
        x0 = x - wid / 2
        self.text(x0, y, s[0], px, gcol or color, "left", weight)
        self.text(x0 + gw, y, s[1:].lstrip(), px, color, "left", weight)

    def circle(self, x, y, r, fc):
        from matplotlib.patches import Circle
        self.ax.add_patch(Circle((x, y), r, fc=fc, ec=fc, zorder=4))

    def rect(self, x, y, w, h, fc, ec=INK, lw=1.2):
        from matplotlib.patches import Rectangle
        self.ax.add_patch(Rectangle((x, y), w, h, fc=fc, ec=ec, lw=lw * 0.72, zorder=3))

    def shape(self, *a, **k):
        _shape(self.ax, *a, **k)

    def arrow(self, pts, rec, head=None):
        import numpy as np
        pts = np.array(pts, float)
        ls, cap = _line_style(rec)
        col = CONTRA if rec["kind"] == "contradicts" else LINE
        lw = (6, 9, 13)[rec["strength"] - 1]
        head = head or 34 + 6 * rec["strength"]
        base = _arrow_head(self.ax, pts, head, _edge_color(rec) if rec.get("effect") or rec["kind"] == "contradicts" else col)
        body = np.vstack([pts[:-1], base])
        self.ax.plot(body[:, 0], body[:, 1], color=col, lw=lw * 0.72, ls=ls, dash_capstyle=cap,
                     solid_capstyle="butt", alpha=(0.75, 0.9, 1.0)[rec["conf"]], zorder=2)


# ------------------------------------------------------------------ layout
def _node_box(meas, nd, w):
    """Text lines and height for node nd drawn w pixels wide."""
    inset = {"hex": 56, "para": 36, "pill": 30, "snip": 12, "double": 24}.get(nd["shape"], 8)
    maxw = w - 2 * 20 - inset
    g = nd["glyph"] if nd["glyph"] and nd["pct"] is None else ""
    ltxt = g + nd["label"]  # glyph glued to the first word so it never wraps alone
    fs, lines = FONT_NODE, None
    for fs in (FONT_NODE, 46, 42):
        lines = meas.wrap(ltxt, fs, maxw - 24, 99)
        if len(lines) <= 3 and all(meas.w(wd, fs) <= maxw - 24 for wd in ltxt.split()):
            break
    lines = meas.wrap(ltxt, fs, maxw - 24, 3)
    if g:
        lines[0] = g + " " + lines[0][len(g):]
    mline = ""
    if nd["pct"] is not None:
        mline = _fit(meas, f"{nd['glyph']} {_chg(nd['mk'], nd['pct'])}".strip(), 46, maxw, "bold")
    tag = nd["dom"][1] + " · " + nd["typ"] if nd["dom"] is not OTHER else nd["typ"]
    h = 18 + FONT_TAG * 1.3 + len(lines) * fs * 1.2 + (56 if mline else 0) + 20
    return dict(lines=lines, fs=fs, mline=mline, tag=tag, h=max(h, 190))


def _draw_node(ops, nd, box, cx, cy, w, h):
    col = nd["dom"][2]
    ops.append(("shape", (nd["shape"], cx, cy, w, h, _tint(col), col, 8), {}))
    y = cy - h / 2 + 16 + FONT_TAG * 0.62
    ops.append(("text", (cx, y, box["tag"], FONT_TAG, MUTED, "center"), {}))
    y += FONT_TAG * 0.62 + box["fs"] * 0.66
    for ln in box["lines"]:
        ops.append(("text", (cx, y, ln, box["fs"], INK, "center"), {}))
        y += box["fs"] * 1.2
    if box["mline"]:
        ops.append(("text", (cx, y - box["fs"] * 0.2 + 18, box["mline"], 46, INK, "center", "bold"), {}))


def _edge_text(rec):
    if rec["kind"] == "contradicts":
        pre = "≠ "
    else:
        pre = POL[rec["effect"]][0] + " " if rec["effect"] in POL else ""
    return (pre + (rec["label"] or "leads to")).strip()


def _edge_color(rec):
    if rec["kind"] == "contradicts":
        return CONTRA
    return POL[rec["effect"]][1] if rec["effect"] in POL else INK


def _emit_lab(ops, meas, cx, y, lines, rec):
    """Emit centred edge-label lines (first line carries the coloured polarity glyph)."""
    for n, ln in enumerate(lines):
        if n == 0 and ln[:1] in "▲▼↕≠":
            wid, gw = meas.w(ln, FONT_EDGE), meas.w(ln[0] + " ", FONT_EDGE)
            ops.append(("gtext", (cx, y, ln, FONT_EDGE, INK, "center", "normal"),
                        dict(gcol=_edge_color(rec), wid=wid, gw=gw)))
        else:
            ops.append(("text", (cx, y, ln, FONT_EDGE, INK, "center"), {}))
        y += FONT_EDGE * 1.18


def _rows_for(k):
    return [k] if k <= 3 else [2, 2] if k == 4 else [3, 2]


def _story(meas, nodes, path, use, num, y0, x0, cw):
    """Lay out one chain as a numbered card. Returns (height, ops)."""
    ops = []
    plan = _rows_for(len(path))
    first, last = nodes[path[0]], nodes[path[-1]]
    t = f"{first['label']} → {last['label']}"
    tlines = meas.wrap(t, 54, cw - 2 * CARD_PAD - 130, 2, "bold")
    nsteps = len(path) - 1
    rep = sum(1 for a, b in zip(path, path[1:]) if use[(a, b)]["reported"])
    domains = []
    for i in path:
        if nodes[i]["dom"][1] not in domains and nodes[i]["dom"] is not OTHER:
            domains.append(nodes[i]["dom"][1])
    meta = f"{nsteps} steps · {rep} reported" + (f" · {' > '.join(domains[:4])}" if len(domains) > 1 else "")
    hdr_h = 12 + len(tlines) * 64 + 46 + 14
    y = y0 + CARD_PAD * 0.6
    cxm = x0 + cw / 2
    ops.append(("circle", (x0 + CARD_PAD + 44, y + 20 + 32, 44, INK), {}))
    ops.append(("text", (x0 + CARD_PAD + 44, y + 20 + 32, str(num), 54, "#ffffff", "center", "bold", 7), {}))
    ty = y + 20 + 32
    for ln in tlines:
        ops.append(("text", (x0 + CARD_PAD + 112, ty, ln, 54, INK, "left", "bold"), {}))
        ty += 64
    ops.append(("text", (x0 + CARD_PAD + 112, ty - 6 + 22, meta, FONT_TAG, MUTED), {}))
    y += hdr_h
    # rows
    pos = []  # per path index: (cx, cy, w, h, box)
    ptr = 0
    row_y = y
    rowinfo = []
    for ri, cnt in enumerate(plan):
        idxs = list(range(ptr, ptr + cnt))
        ptr += cnt
        boxes = [_node_box(meas, nodes[path[i]], NODE_W) for i in idxs]
        nh = max(b["h"] for b in boxes)
        gap = GAP3 if cnt == 3 else GAP2
        # label heights for in-row arrows
        lab = []
        for i in idxs[:-1]:
            rec = use[(path[i], path[i + 1])]
            lines = meas.wrap(_edge_text(rec), FONT_EDGE, gap - 34, 4)
            lab.append(lines)
        lab_h = max([len(l) * FONT_EDGE * 1.18 + 16 for l in lab] + [0])
        arrow_y_off = nh * 0.62  # arrow below centre so label fits above it
        up = max(arrow_y_off - 16 - 0, 0)
        top_pad = max(0, lab_h + 34 - arrow_y_off)
        total = cnt * NODE_W + (cnt - 1) * gap
        sx = x0 + (cw - total) / 2
        top = row_y + top_pad
        for j, i in enumerate(idxs):
            cx = sx + j * (NODE_W + gap) + NODE_W / 2
            cy = top + nh / 2
            pos.append((cx, cy, NODE_W, nh, boxes[j]))
            _draw_node(ops, nodes[path[i]], boxes[j], cx, cy, NODE_W, nh)
        for j, i in enumerate(idxs[:-1]):
            rec = use[(path[i], path[i + 1])]
            ax_ = pos[i][0] + NODE_W / 2 + 10
            bx_ = pos[i + 1][0] - NODE_W / 2 - 10
            ay = top + arrow_y_off
            ops.append(("arrow", ([(ax_, ay), (bx_, ay)], rec), {}))
            gx = (ax_ + bx_) / 2
            ly = ay - 18 - len(lab[j]) * FONT_EDGE * 1.18 + FONT_EDGE * 0.59
            _emit_lab(ops, meas, gx, ly, lab[j], rec)
        rowinfo.append((top, nh))
        row_y = top + nh
        if ri < len(plan) - 1:
            # wrap gutter: elbow arrow from last node of this row to first node of next
            rec = use[(path[ptr - 1], path[ptr])]
            lines = meas.wrap(_edge_text(rec), FONT_EDGE, 1500, 2)
            lh = len(lines) * FONT_EDGE * 1.18 + 16
            gut = max(lh + 44, 112)
            row_y += gut
            rowinfo.append(("gut", row_y - gut, gut, rec, lines))
    # elbows (need next row geometry, so place after)
    gi = 0
    for item in rowinfo:
        pass
    # recompute elbows from stored info
    ptr = 0
    for ri, cnt in enumerate(plan[:-1]):
        ptr += cnt
        a, b = pos[ptr - 1], pos[ptr]
        rec = use[(path[ptr - 1], path[ptr])]
        g = [r for r in rowinfo if r[0] == "gut"][ri]
        gy0, gut, lines = g[1], g[2], g[4]
        ya = a[1] + a[3] / 2 + 8
        yb = b[1] - b[3] / 2 - 10
        ym = (ya + yb) / 2
        ops.append(("arrow", ([(a[0], ya), (a[0], ym), (b[0], ym), (b[0], yb)], rec), {}))
        lw_ = max(meas.w(l, FONT_EDGE) for l in lines) + 36
        lh = len(lines) * FONT_EDGE * 1.18 + 16
        mx = (a[0] + b[0]) / 2
        ops.append(("rrect", (mx - lw_ / 2, ym - lh / 2, lw_, lh, 14, CARD, CARD), {"z": 5}))
        ly = ym - lh / 2 + 8 + FONT_EDGE * 0.59
        _emit_lab(ops, meas, mx, ly, lines, rec)
    bottom = row_y + CARD_PAD * 0.7
    h = bottom - y0
    ops.insert(0, ("rrect", (x0, y0, cw, h, 30, CARD, CARD_EDGE, 3), {"z": 0.5}))
    return h, ops


def _items_card(meas, nodes, idxs, y0, x0, cw, heading):
    """A card of loose nodes (no links), up to 3 per row, centred."""
    ops = []
    y = y0 + CARD_PAD * 0.6
    ops.append(("text", (x0 + CARD_PAD + 10, y + 36, heading, 54, INK, "left", "bold"), {}))
    y += 90
    for r in range(0, len(idxs), 3):
        grp = idxs[r:r + 3]
        boxes = [_node_box(meas, nodes[i], NODE_W) for i in grp]
        nh = max(b["h"] for b in boxes)
        total = len(grp) * NODE_W + (len(grp) - 1) * 80
        sx = x0 + (cw - total) / 2
        for j, i in enumerate(grp):
            _draw_node(ops, nodes[i], boxes[j], sx + j * (NODE_W + 80) + NODE_W / 2, y + nh / 2, NODE_W, nh)
        y += nh + 40
    h = y - y0 + CARD_PAD * 0.2
    ops.insert(0, ("rrect", (x0, y0, cw, h, 30, CARD, CARD_EDGE, 3), {"z": 0.5}))
    return h, ops


def _layout(meas, nodes, edges, title):
    chosen, use = _chains(nodes, edges, 3)
    if not chosen:
        chosen, use = _chains(nodes, edges, 2)
    x0, cw = MARGIN, W - 2 * MARGIN
    top, reserve = 250, 900
    budget = MAX_H - reserve - top
    hs = [_story(meas, nodes, path, use, 1, 0, x0, cw)[0] + 28 for _, path in chosen]
    sel, used = [], 0
    for k in range(len(chosen)):
        if len(sel) < MAX_STORIES and used + hs[k] <= budget:
            sel.append(k)
            used += hs[k]
    for k in sorted(range(len(chosen)), key=lambda k: (hs[k], k)):  # keep >= 3 stories if available
        if len(sel) >= min(3, len(chosen)):
            break
        if k not in sel and used + hs[k] <= budget + 500:
            sel.append(k)
            used += hs[k]
    if not sel and chosen:
        sel = [0]
    sel.sort()
    ops, y, shown_edges, used_nodes = [], top, 0, []
    for num, k in enumerate(sel, 1):
        path = chosen[k][1]
        h, o = _story(meas, nodes, path, use, num, y, x0, cw)
        ops += o
        y += h + 28
        shown_edges += len(path) - 1
        used_nodes += [i for i in path if i not in used_nodes]
    if not sel and nodes:  # nothing chained: show loose items
        idxs = list(range(min(len(nodes), 6)))
        h, o = _items_card(meas, nodes, idxs, y, x0, cw, "In the news")
        ops += o
        y += h + 28
        used_nodes = idxs
    elif len(nodes) <= 8 and len(sel) < 3:  # tiny graph: unconnected items too
        linked = {i for k in edges for i in k}
        rest = [i for i in range(len(nodes)) if i not in used_nodes and i not in linked][:3]
        if rest:
            h, o = _items_card(meas, nodes, rest, y, x0, cw, "Also in the news")
            ops += o
            y += h + 28
            used_nodes += rest
    return y, ops, shown_edges, used_nodes, len(sel)


def _draw(graph, title, meas, nodes, edges):
    import matplotlib.pyplot as plt
    import numpy as np
    y, ops, shown_edges, used_nodes, nstories = _layout(meas, nodes, edges, title)
    x0 = MARGIN
    items = []  # footer ops, y-relative handled below

    # ---- legend
    used_dom, used_typ = [], []
    for i in used_nodes:
        if nodes[i]["dom"] not in used_dom:
            used_dom.append(nodes[i]["dom"])
        if nodes[i]["typ"] not in used_typ:
            used_typ.append(nodes[i]["typ"])
    used_dom.sort(key=lambda d: DOMAINS.index(d) if d in DOMAINS else 99)
    foot = []

    def T(xp, yp, s, px, color=INK, ha="left", weight="normal"):
        foot.append(("text", (xp, yp, s, px, color, ha, weight), {}))

    def flow(items_, y0, label):
        T(x0 + 30, y0, label, FONT_LEG, MUTED, weight="bold")
        xx, yy = x0 + 30 + meas.w(label, FONT_LEG, "bold") + 36, y0
        for wdt, fn in items_:
            if xx + wdt > W - MARGIN - 20:
                xx, yy = x0 + 30, yy + 70
            fn(xx, yy)
            xx += wdt + 30
        return yy + 70

    def dom_item(d):
        s = SHORT.get(d[0], d[0])

        def fn(xp, yp):
            foot.append(("rect", (xp, yp - 17, 34, 34, d[2]), {}))
            T(xp + 46, yp, s, FONT_LEG)
        return 46 + meas.w(s, FONT_LEG), fn

    def typ_item(t):
        shp, nm = TYPES[t]

        def fn(xp, yp):
            foot.append(("shape", (shp, xp + 40, yp, 80, 56, "#e8e6df", INK, 3), {}))
            T(xp + 98, yp, nm, FONT_LEG)
        return 98 + meas.w(nm, FONT_LEG), fn

    def link_item(txt, kind):
        def fn(xp, yp):
            rec = dict(kind="contradicts" if kind == "contra" else "causal", reported=kind != "pred",
                       strength=2, conf=2, effect="")
            foot.append(("arrow", ([(xp, yp), (xp + 100, yp)], rec, 26), {}))
            if kind == "contra":
                T(xp + 116, yp, "≠", FONT_LEG, CONTRA, weight="bold")
                T(xp + 116 + meas.w("≠ ", FONT_LEG, "bold"), yp, txt, FONT_LEG)
            else:
                T(xp + 116, yp, txt, FONT_LEG)
        return 116 + (meas.w("≠ ", FONT_LEG, "bold") if kind == "contra" else 0) + meas.w(txt, FONT_LEG), fn

    def pol_item(glyph, col, txt):
        def fn(xp, yp):
            T(xp, yp, glyph, FONT_LEG, col, weight="bold")
            T(xp + meas.w(glyph + " ", FONT_LEG, "bold"), yp, txt, FONT_LEG)
        return meas.w(glyph + " ", FONT_LEG, "bold") + meas.w(txt, FONT_LEG), fn

    def text_item(txt):
        return meas.w(txt, FONT_LEG), (lambda xp, yp: T(xp, yp, txt, FONT_LEG))

    ly = y + 20
    panel_top = ly
    ly += 44
    ly = flow([dom_item(d) for d in used_dom], ly, "Colour")
    tl = [t for t in LEGEND_TYPES if t in used_typ or (t == "index" and "market" in used_typ)]
    ly = flow([typ_item(t) for t in tl], ly + 4, "Shape")
    ly = flow([link_item("reported", "rep"), link_item("predicted", "pred"), link_item("cuts against", "contra"),
               pol_item("▲", RAISE, "raises"),
               pol_item("▼", LOWER, "lowers"), pol_item("↕", INK, "mixed"),
               text_item("thicker = stronger")], ly + 4, "Links")
    foot.insert(0, ("rrect", (x0, panel_top, W - 2 * MARGIN, ly - panel_top - 16, 26, "#f1efe8", CARD_EDGE, 2), {"z": 0.5}))
    y = ly + 24

    # ---- biggest movers
    mv = [nd for nd in nodes if nd["pct"] is not None]
    mv.sort(key=lambda nd: (-abs(nd["pct"]), nd["label"]))
    if mv:
        T(x0 + 10, y + 24, "Biggest movers", 54, INK, weight="bold")
        y += 84
        colw = (W - 2 * MARGIN) / 2
        for k, nd in enumerate(mv[:6]):
            mk = nd["mk"] or {}
            price = str(mk.get("price") or "").strip()
            tail = f"  {price}" if price else ""
            chg = f"  {_chg(mk, nd['pct'])}"
            name = _fit(meas, nd["label"], 46, colw - 40 - meas.w(f"{nd['glyph']} " + tail + chg, 46, "bold") - 10)
            T(x0 + 10 + (k % 2) * colw, y + (k // 2) * 62 + 24, f"{nd['glyph']} {name}{tail}{chg}", 46)
        y += ((min(6, len(mv)) + 1) // 2) * 62 + 14
    hidden = len(edges) - shown_edges
    msg = f"{hidden} more link{'s' if hidden != 1 else ''} on the web page" if hidden > 0 else "Full interactive map on the web page"
    T(W / 2, y + 40, msg, 50, INK, "center", "bold")
    H = y + 96

    fig = plt.figure(figsize=(W / 100, H / 100), dpi=100, facecolor=SURFACE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    ctx = _Ctx(ax)
    ttl = _fit(meas, _clean(title) or "Connection map", 72, W - 2 * MARGIN, "bold")
    ctx.text(MARGIN + 10, 90, ttl, 72, INK, weight="bold")
    n_s = max(nstories, 1)
    sub = f"{n_s} key cause-and-effect {'story' if n_s == 1 else 'stories'} · read top to bottom" if nstories else "Key items"
    ctx.text(MARGIN + 10, 170, sub, 42, MUTED)
    for name, args, kw in ops + foot:
        getattr(ctx, name)(*args, **kw)
    return fig, H


def render_graph_png(graph, title=""):
    """Return PNG bytes of the graph, or None if invalid / rendering fails."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        meas = _Meas()
        nodes, edges = _parse(graph)
        if not nodes:
            return None
        fig, H = _draw(graph, title, meas, nodes, edges)
        out = None
        for dpi in (100, 80, 64, 50):
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=dpi, facecolor=SURFACE)
            out = buf.getvalue()
            if len(out) <= MAX_BYTES:
                break
        plt.close(fig)
        return out
    except Exception:
        if DEBUG:
            raise
        try:
            import matplotlib.pyplot as plt
            plt.close("all")
        except Exception:
            pass
        return None


def to_data_uri(png_bytes):
    """Data URI for embedding the PNG in email HTML."""
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
