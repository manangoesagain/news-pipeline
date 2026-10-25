"""Static PNG rendering of the connection-map graph (for Telegram / email).

The web page holds the full interactive map.  A PNG cannot show 40+ nodes and 80
labelled links legibly on a phone, so this renders the strongest multi-step causal
chains as a layered top-down diagram (causes at the top), domain-tagged nodes with a
shape per type, labelled arrows (solid = reported, dashed = predicted, red dotted =
contradicts, curved = feedback loop), a legend and a "Biggest movers" caption.

All geometry is in pixels on a 2400px-wide canvas (dpi 100, so 1px = 0.72pt).
Telegram shrinks photos to ~1080px, so main text is ~50px (about 22px on a phone).
Output is deterministic (no randomness).
"""
import base64
import io
import math
import re

W = 2400
MARGIN = 150
GAPX = 56
MAX_H = 3650
MAX_BYTES = 2_500_000
DEBUG = False  # tests: re-raise instead of returning None
INK, MUTED, SURFACE, LINE = "#1b1f27", "#4b5260", "#fbfaf7", "#394150"
CONTRA = "#d03b3b"
FONT_NODE, FONT_TAG, FONT_EDGE, FONT_LEG = 50, 30, 40, 36

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


def _tint(hexc, k=0.84):
    h = hexc.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % tuple(int(c + (255 - c) * k) for c in (r, g, b))


def _int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except Exception:
        return default


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
        words = text.split()
        lines, cur = [], ""
        for wd in words:
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


# ------------------------------------------------------------------ model
def _parse(graph, meas):
    raw_nodes = [x for x in (graph or {}).get("nodes", []) if isinstance(x, dict) and x.get("id") is not None][:200]
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
        pct = None
        if mk:
            try:
                pct = float(mk.get("change_pct"))
            except Exception:
                m = re.search(r"-?\d+(?:\.\d+)?", str(mk.get("change") or ""))
                pct = float(m.group()) * (-1 if str(mk.get("change")).strip().startswith("-") and m and float(m.group()) > 0 else 1) if m else None
        direction = str(nd.get("direction") or "").lower()
        glyph = ""
        if pct is not None:
            glyph = "▲" if pct > 0 else "▼" if pct < 0 else "–"
        elif direction in ("up", "down"):
            glyph = "▲" if direction == "up" else "▼"
        shape = TYPES[typ][0]
        lines = meas.wrap((glyph + " " if glyph and pct is None else "") + label, FONT_NODE, 380, 3)
        mline = ""
        if pct is not None:
            mline = f"{glyph} {mk.get('change') or ('%+.2f%%' % pct)}"
        tag = f"{dom[1]} · {typ}"
        tw = max([meas.w(l, FONT_NODE) for l in lines] + [meas.w(tag, FONT_TAG), meas.w(mline, 44) if mline else 0])
        h = 22 + FONT_TAG * 1.25 + len(lines) * FONT_NODE * 1.2 + (50 if mline else 0) + 22
        extra = {"hex": 0.32 * h, "para": 0.5 * h, "pill": 0.42 * h, "snip": 0.1 * h, "double": 26, "round": 10}.get(shape, 0)
        nodes.append(dict(id=nd["id"], label=label, typ=typ, shape=shape, dom=dom, lines=lines, tag=tag,
                          mline=mline, pct=pct, mk=mk, w=tw + 2 * 26 + extra, h=h, glyph=glyph))
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
        st = 1 if str(e.get("status") or "reported").lower() == "reported" else 0
        s = _int(e.get("strength"), 1, 3, 1)
        eff = str(e.get("effect") or "").lower()
        rec = dict(a=a, b=b, label=_clean(e.get("label")), effect=eff if eff in ("up", "down", "mixed") else "",
                   strength=s, conf=conf, reported=bool(st), kind=kind, score=3 * s + conf + st,
                   order=_int(e.get("order"), 1, 3, 1))
        if (a, b) not in edges or edges[(a, b)]["score"] < rec["score"]:
            edges[(a, b)] = rec
    return nodes, edges


def _struct(nodes, N, E, maxL, rowlimit):
    """Layer the chosen causal edges E over node set N. None if it does not fit."""
    N = sorted(N)
    succ = {i: [] for i in N}
    for a, b in sorted(E):
        succ[a].append(b)
    state, back = {}, set()

    def dfs(u):
        state[u] = 1
        for v in succ[u]:
            if state.get(v) == 1:
                back.add((u, v))
            elif v not in state:
                dfs(v)
        state[u] = 2

    for u in N:
        if u not in state:
            dfs(u)
    fwd = [e for e in sorted(E) if e not in back]
    pred = {i: [] for i in N}
    sc = {i: [] for i in N}
    for a, b in fwd:
        pred[b].append(a)
        sc[a].append(b)
    layer = {}

    def lay(u):
        if u not in layer:
            layer[u] = 0 if not pred[u] else 1 + max(lay(p) for p in pred[u])
        return layer[u]

    for u in N:
        lay(u)
    for u in N:  # pull pure sources down next to their effects
        if not pred[u] and sc[u]:
            layer[u] = min(layer[v] for v in sc[u]) - 1
    lo = min(layer.values()) if layer else 0
    for u in layer:
        layer[u] -= lo
    nl = max(layer.values()) + 1 if layer else 1
    if nl > maxL:
        return None
    rows = {l: [("n", i) for i in N if layer[i] == l] for l in range(nl)}
    chains = {}
    for a, b in fwd:
        ch = [("n", a)] + [("d", (a, b), l) for l in range(layer[a] + 1, layer[b])] + [("n", b)]
        chains[(a, b)] = ch
        for it in ch[1:-1]:
            rows[it[2]].append(it)
    for l, items in rows.items():
        wsum = sum(nodes[it[1]]["w"] if it[0] == "n" else 70 for it in items) + GAPX * (len(items) - 1)
        if wsum > rowlimit:
            return None
    return dict(layer=layer, nl=nl, rows=rows, chains=chains, fwd=fwd, back=back)


def _select(nodes, edges, rowlimit):
    n = len(nodes)
    deg = [0] * n
    for (a, b) in edges:
        deg[a] += 1
        deg[b] += 1
    causal = {k: v for k, v in edges.items() if v["kind"] == "causal"}
    out = {}
    for (a, b), v in causal.items():
        out.setdefault(a, []).append((v["score"], b))
    for a in out:
        out[a].sort(key=lambda t: (-t[0], t[1]))
        out[a] = [b for _, b in out[a][:4]]

    def nscore(i):
        return 0.6 * deg[i] + (2.0 if nodes[i]["mk"] else 0)

    cands, budget = [], [30000]

    def walk(path):
        if len(path) >= 2:
            es = list(zip(path, path[1:]))
            sc = sum(causal[e]["score"] for e in es) + sum(nscore(i) for i in path)
            cands.append((sc, tuple(path), tuple(es)))
        if len(path) >= 5:
            return
        for v in out.get(path[-1], []):
            if v not in path and budget[0] > 0:
                budget[0] -= 1
                walk(path + [v])

    for s in range(n):
        walk([s])
    cands.sort(key=lambda c: (-c[0], c[1]))
    cands = cands[:500]

    for maxL in (5, 4, 3, 2):
        N, E, chosen = set(), set(), 0
        while chosen < 8 and len(N) < 18:
            best, tests = None, 0
            ranked = []
            for sc, path, es in cands:
                newe = [e for e in es if e not in E]
                if not newe:
                    continue
                newn = [i for i in path if i not in N]
                gain = (sum(causal[e]["score"] for e in newe) + sum(nscore(i) for i in newn)) / (1 + 0.12 * len(newn))
                ranked.append((-gain, path, es))
            ranked.sort(key=lambda r: (r[0], r[1]))
            for _, path, es in ranked:
                tests += 1
                if tests > 60:
                    break
                st = _struct(nodes, N | set(path), E | set(es), maxL, rowlimit)
                if st:
                    best = (path, es)
                    break
            if not best:
                break
            N |= set(best[0])
            E |= set(best[1])
            chosen += 1
        if N:
            break
    if not N:  # no usable chains: isolated nodes (tiny / edge-less graphs)
        order = sorted(range(n), key=lambda i: (-deg[i], i))
        for i in order:
            if len(N) < 8 and _struct(nodes, N | {i}, set(), 5, rowlimit):
                N.add(i)
    else:
        # extra links between chosen nodes, strongest first
        extra = sorted((v["score"], k) for k, v in causal.items() if k[0] in N and k[1] in N and k not in E)
        for _, k in sorted(extra, key=lambda t: (-t[0], t[1])):
            if len(E) >= 26:
                break
            if _struct(nodes, N, E | {k}, 5, rowlimit):
                E.add(k)
        if len(N) <= 8:  # tiny graph: also show unconnected nodes
            for i in range(n):
                if i not in N and deg[i] == 0 and len(N) < 8 and _struct(nodes, N | {i}, E, 5, rowlimit):
                    N.add(i)
    st = _struct(nodes, N, E, 5, rowlimit) if N else None
    if st is None and N:
        st = _struct(nodes, N, E, 9, rowlimit * 3)
    # annotation edges (feedback / contradicts) + causal back-edges
    ann = []
    pool = [v for k, v in edges.items() if k[0] in N and k[1] in N and (v["kind"] != "causal" or k in (st or {}).get("back", ()))]
    pool.sort(key=lambda v: (-v["score"], v["a"], v["b"]))
    cnt = {"fb": 0, "ct": 0}
    for v in pool:
        key = "ct" if v["kind"] == "contradicts" else "fb"
        if cnt[key] < (3 if key == "ct" else 2):
            cnt[key] += 1
            ann.append(dict(v, kind="contradicts" if key == "ct" else "feedback"))
    return sorted(N), E, st, ann


# ------------------------------------------------------------------ geometry
def _order_and_place(nodes, st, rowlimit):
    rows, chains, nl = st["rows"], st["chains"], st["nl"]
    nb_up, nb_dn = {}, {}
    for ch in chains.values():
        for p, q in zip(ch, ch[1:]):
            nb_dn.setdefault(p, []).append(q)
            nb_up.setdefault(q, []).append(p)

    def key0(it):
        if it[0] == "n":
            nd = nodes[it[1]]
            return (0, DOMAINS.index(nd["dom"]) if nd["dom"] in DOMAINS else 9, nd["label"])
        return (1, it[1], it[2])

    for l in rows:
        rows[l].sort(key=key0)
    pos = {l: {it: i for i, it in enumerate(rows[l])} for l in rows}

    def sweep(ls, nbr, off):
        for l in ls:
            def bary(it):
                ns = [pos[l + off][q] for q in nbr.get(it, []) if q in pos.get(l + off, {})]
                return sum(ns) / len(ns) if ns else pos[l][it]
            rows[l].sort(key=lambda it: (bary(it), key0(it)))
            pos[l] = {it: i for i, it in enumerate(rows[l])}

    for _ in range(8):
        sweep(range(1, nl), nb_up, -1)
        sweep(range(nl - 2, -1, -1), nb_dn, 1)

    def wd(it):
        return nodes[it[1]]["w"] if it[0] == "n" else 70

    x = {}
    for l in rows:
        tot = sum(wd(i) for i in rows[l]) + GAPX * (len(rows[l]) - 1)
        cur = W / 2 - tot / 2
        for it in rows[l]:
            x[it] = cur + wd(it) / 2
            cur += wd(it) + GAPX
    lo, hi = MARGIN, W - MARGIN
    for _ in range(14):
        for l in list(range(nl)) + list(range(nl - 2, -1, -1)):
            des = {}
            for it in rows[l]:
                ns = [x[q] for q in nb_up.get(it, []) + nb_dn.get(it, []) if q in x]
                des[it] = sum(ns) / len(ns) if ns else x[it]
            items = rows[l]
            for i, it in enumerate(items):
                x[it] = max(des[it], lo + wd(it) / 2) if i == 0 else max(des[it], x[items[i - 1]] + (wd(items[i - 1]) + wd(it)) / 2 + GAPX)
            for i in range(len(items) - 1, -1, -1):
                it = items[i]
                lim = hi - wd(it) / 2 if i == len(items) - 1 else x[items[i + 1]] - (wd(items[i + 1]) + wd(it)) / 2 - GAPX
                x[it] = min(x[it], lim)
    return x


def _bez(p0, p1, p2, p3, n=40):
    import numpy as np
    t = np.linspace(0, 1, n)[:, None]
    p0, p1, p2, p3 = (np.array(p, float) for p in (p0, p1, p2, p3))
    return (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3


def _clip(c, w, h, toward):
    dx, dy = toward[0] - c[0], toward[1] - c[1]
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return c
    t = min((w / 2) / abs(dx) if dx else 1e9, (h / 2) / abs(dy) if dy else 1e9)
    return (c[0] + dx * t, c[1] + dy * t)


def _inside(pts, rect, pad=6):
    return (pts[:, 0] > rect[0] - pad) & (pts[:, 0] < rect[0] + rect[2] + pad) & \
           (pts[:, 1] > rect[1] - pad) & (pts[:, 1] < rect[1] + rect[3] + pad)


def _free_route(sr, dr, obstacles, feedback, xmin, xmax):
    """Pick the least-colliding curve between two node rects (x, y, w, h)."""
    import numpy as np
    sc = (sr[0] + sr[2] / 2, sr[1] + sr[3] / 2)
    dc = (dr[0] + dr[2] / 2, dr[1] + dr[3] / 2)
    dist = math.hypot(dc[0] - sc[0], dc[1] - sc[1]) + 1e-6
    nx, ny = -(dc[1] - sc[1]) / dist, (dc[0] - sc[0]) / dist
    cands = []
    rads = [0.35, -0.35, 0.7, -0.7, 1.0, -1.0] if feedback else [0.0, 0.3, -0.3, 0.6, -0.6, 1.0, -1.0]
    for rad in rads:
        bulge = rad * min(dist, 520)
        c = ((sc[0] + dc[0]) / 2 + nx * bulge, (sc[1] + dc[1]) / 2 + ny * bulge)
        p0, p2 = _clip(sc, sr[2], sr[3], c), _clip(dc, dr[2], dr[3], c)
        p1 = (c[0] * 2 - (p0[0] + p2[0]) / 2, c[1] * 2 - (p0[1] + p2[1]) / 2)  # quad control -> cubic
        pts = _bez(p0, ((p0[0] + 2 * p1[0]) / 3, (p0[1] + 2 * p1[1]) / 3), ((p2[0] + 2 * p1[0]) / 3, (p2[1] + 2 * p1[1]) / 3), p2)
        cands.append((abs(rad) * 0.1 + (0 if rad else -0.01), pts))
    for sd in (-1, 1):
        p0 = (sr[0] + (0 if sd < 0 else sr[2]), sc[1])
        p2 = (dr[0] + (0 if sd < 0 else dr[2]), dc[1])
        xo = max(50, xmin - 60) if sd < 0 else min(W - 50, xmax + 60)
        cands.append((0.3, _bez(p0, (xo, p0[1]), (xo, p2[1]), p2)))
    best = None
    for pen, pts in cands:
        bad = 0
        for ob in obstacles:
            if ob is sr or ob is dr:
                continue
            bad += int(_inside(pts, ob, 8).sum())
        cost = bad * 10 + pen + len(pts) * 0
        if best is None or cost < best[0]:
            best = (cost, pts)
    return best[1]


# ------------------------------------------------------------------ drawing
def _shape(ax, kind, cx, cy, w, h, fc, ec, lw, z=2):
    from matplotlib.patches import FancyBboxPatch, Polygon, Rectangle
    x0, y0 = cx - w / 2, cy - h / 2
    kw = dict(fc=fc, ec=ec, lw=lw * 0.72, zorder=z, joinstyle="round")
    if kind in ("round", "pill"):
        r = min(h / 2, 18 if kind == "round" else h / 2)
        ax.add_patch(FancyBboxPatch((x0 + r, y0 + r), w - 2 * r, h - 2 * r, boxstyle=f"round,pad={r}", **kw))
        return
    if kind in ("rect", "double"):
        ax.add_patch(Rectangle((x0, y0), w, h, **kw))
        if kind == "double":
            ax.add_patch(Rectangle((x0 + 12, y0 + 12), w - 24, h - 24, fc="none", ec=ec, lw=lw * 0.72 * 0.45, zorder=z))
        return
    if kind == "hex":
        c = 0.3 * h
        pts = [(x0, cy), (x0 + c, y0), (x0 + w - c, y0), (x0 + w, cy), (x0 + w - c, y0 + h), (x0 + c, y0 + h)]
    elif kind == "para":
        s = 0.25 * h
        pts = [(x0 + s, y0), (x0 + w, y0), (x0 + w - s, y0 + h), (x0, y0 + h)]
    else:  # snip: top corners cut
        s = 0.2 * h
        pts = [(x0 + s, y0), (x0 + w - s, y0), (x0 + w, y0 + s), (x0 + w, y0 + h), (x0, y0 + h), (x0, y0 + s)]
    ax.add_patch(Polygon(pts, closed=True, **kw))


def _arrow_head(ax, pts, size, color):
    from matplotlib.patches import Polygon
    import numpy as np
    tip = pts[-1]
    k = len(pts) - 1
    while k > 0 and np.hypot(*(pts[k] - tip)) < 8:
        k -= 1
    d = tip - pts[k]
    n = np.hypot(*d) or 1.0
    d = d / n
    nrm = np.array([-d[1], d[0]])
    base = tip - d * size
    ax.add_patch(Polygon([tip, base + nrm * size * 0.46, base - nrm * size * 0.46], closed=True,
                         fc=color, ec=color, lw=1, zorder=1.5, joinstyle="round"))
    return base


def _line_style(e):
    if e["kind"] == "contradicts":
        return (0, (0.1, 2.3)), "round"
    if not e["reported"]:
        return (0, (2.6, 1.6)), "butt"
    return "-", "butt"


def _draw(graph, title, G, meas, nodes, edges, sel, plot=True):
    """Lay out and (optionally) draw. Returns (fig/None, height, hard_overlaps)."""
    import numpy as np
    N, E, st, ann = sel
    rowlimit = W - 2 * MARGIN
    x = _order_and_place(nodes, st, rowlimit)
    layer, rows, chains = st["layer"], st["rows"], st["chains"]
    nl = st["nl"]
    top = 250
    rowh = [max([nodes[it[1]]["h"] for it in rows[l] if it[0] == "n"] + [120]) for l in range(nl)]
    ycen, cur = {}, top
    for l in range(nl):
        ycen[l] = cur + rowh[l] / 2
        cur += rowh[l] + G
    diag_bottom = cur - G
    pos = {}
    rects = {}
    for l in range(nl):
        for it in rows[l]:
            if it[0] == "n":
                nd = nodes[it[1]]
                pos[it] = (x[it], ycen[l])
                rects[it[1]] = (x[it] - nd["w"] / 2, ycen[l] - nd["h"] / 2, nd["w"], nd["h"])
            else:
                pos[it] = (x[it], ycen[l])
    obstacles = list(rects.values())
    xs = [r[0] for r in obstacles] or [MARGIN]
    xe = [r[0] + r[2] for r in obstacles] or [W - MARGIN]
    xmin, xmax = max(min(xs), 90), min(max(xe), W - 90)

    # ports
    outs, ins = {}, {}
    for ed, ch in chains.items():
        outs.setdefault(ch[0], []).append((x[ch[1]], ed))
        ins.setdefault(ch[-1], []).append((x[ch[-2]], ed))
    port_out, port_in = {}, {}
    for table, store in ((outs, port_out), (ins, port_in)):
        for it, lst in table.items():
            lst.sort()
            nd = nodes[it[1]]
            step = min(80, 0.7 * nd["w"] / max(1, len(lst)))
            for i, (_, ed) in enumerate(lst):
                store[(it, ed)] = x[it] + (i - (len(lst) - 1) / 2) * step

    paths = []  # (rec, pts)
    for ed, ch in chains.items():
        rec = edges[ed]
        a, b = ch[0], ch[-1]
        wp = [(port_out[(a, ed)], rects[a[1]][1] + rects[a[1]][3])]
        for it in ch[1:-1]:
            wp.append((x[it], ycen[it[2]]))
        wp.append((port_in[(b, ed)], rects[b[1]][1]))
        segs = []
        for p, q in zip(wp, wp[1:]):
            k = 0.5 * (q[1] - p[1])
            seg = _bez(p, (p[0], p[1] + k), (q[0], q[1] - k), q)
            segs.append(seg if not segs else seg[1:])
        paths.append((rec, np.vstack(segs)))
    for rec in ann:
        sr, dr = rects[rec["a"]], rects[rec["b"]]
        paths.append((rec, _free_route(sr, dr, obstacles, rec["kind"] == "feedback", xmin, xmax)))

    # edge labels with collision avoidance
    elab = []
    lh = FONT_EDGE * 1.18
    allpts = [p for _, p in paths]
    order = sorted(range(len(paths)), key=lambda i: (-paths[i][0]["strength"], -paths[i][0]["score"], i))
    placed, hard = {}, 0
    for i in order:
        rec, pts = paths[i]
        pre = {"contradicts": "✕ ", "feedback": "↻ "}.get(rec["kind"], "")
        eff = {"up": "↑ ", "down": "↓ ", "mixed": "↕ "}.get(rec["effect"], "")
        txt = (pre + eff + rec["label"]).strip()
        if not rec["label"]:
            continue
        lines = meas.wrap(txt, FONT_EDGE, 440, 3)
        bw = max(meas.w(l, FONT_EDGE) for l in lines) + 28
        bh = len(lines) * lh + 14
        n = len(pts)
        fr = sorted([0.5 + 0.04 * k for k in range(-10, 11)], key=lambda f: (abs(f - 0.5), f))
        others = np.vstack([p for j, p in enumerate(allpts) if j != i]) if len(allpts) > 1 else np.zeros((0, 2))
        best = None
        cl = [(f, d, v) for v in (0, 0.9, -0.9) for d in (0, 0.55, -0.55, 1.1, -1.1) for f in fr]
        for f, d, v in cl:
            c = pts[min(n - 1, int(f * n))]
            r = (c[0] - bw / 2 + d * bw, c[1] - bh / 2 + v * bh, bw, bh)
            cost = abs(f - 0.5) * 30 + abs(d) * 60 + abs(v) * 50
            if r[0] < 20 or r[0] + bw > W - 20:
                cost += 5000
            for ob in obstacles:
                if r[0] < ob[0] + ob[2] + 10 and ob[0] < r[0] + bw + 10 and r[1] < ob[1] + ob[3] + 10 and ob[1] < r[1] + bh + 10:
                    cost += 5000
            for pr in placed.values():
                if r[0] < pr[0] + pr[2] + 10 and pr[0] < r[0] + bw + 10 and r[1] < pr[1] + pr[3] + 8 and pr[1] < r[1] + bh + 8:
                    cost += 5000
            if len(others):
                cost += 10 * min(40, int(_inside(others, r, 0).sum()))
            if best is None or cost < best[0]:
                best = (cost, r)
        if best[0] >= 5000:  # no clean spot: omit this label rather than overlap
            hard += 1
            continue
        placed[i] = best[1]
        elab.append((i, lines, best[1]))

    tail_y = diag_bottom + 90
    if not plot:
        return None, tail_y, hard

    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(W / 100, 34), dpi=100, facecolor=SURFACE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.axis("off")

    def T(xp, yp, s, px, color=INK, ha="left", va="center", weight="normal", z=6):
        return ax.text(xp, yp, s, fontsize=px * 0.72, color=color, ha=ha, va=va, fontweight=weight,
                       zorder=z, parse_math=False, linespacing=1.15)

    title = _clean(title) or "Connection map"
    while len(title) > 8 and meas.w(title, 66, "bold") > W - 2 * MARGIN + 60:
        title = title[:-2].rstrip() + "…"
    T(MARGIN - 60, 85, title, 66, weight="bold")
    T(MARGIN - 60, 160, f"Strongest cause-and-effect chains · causes at top, effects below · {len(N)} of {len(nodes)} items", 36, MUTED)

    # edges
    for rec, pts in paths:
        ls, cap = _line_style(rec)
        col = CONTRA if rec["kind"] == "contradicts" else LINE
        lw = (4, 7, 11)[rec["strength"] - 1]
        alpha = (0.7, 0.88, 1.0)[rec["conf"]]
        head = 30 + 7 * rec["strength"]
        end = _arrow_head(ax, pts, head, col)
        body = pts.copy()
        # trim the line so it does not poke through the arrow head
        k = len(body) - 1
        while k > 0 and math.hypot(*(body[k] - end)) > 2 and math.hypot(*(body[k] - pts[-1])) < head * 0.9:
            k -= 1
        body = body[:k + 1]
        ax.plot(body[:, 0], body[:, 1], color=col, lw=lw * 0.72, ls=ls, dash_capstyle=cap, solid_capstyle="butt",
                alpha=alpha, zorder=1)
    # nodes
    for i in N:
        nd = nodes[i]
        r = rects[i]
        cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
        col = nd["dom"][2]
        _shape(ax, nd["shape"], cx, cy, r[2], r[3], _tint(col), col, 8)
        yy = r[1] + 22 + FONT_TAG * 0.62
        T(cx, yy, nd["tag"], FONT_TAG, MUTED, "center")
        yy += FONT_TAG * 0.62 + FONT_NODE * 0.62
        for ln in nd["lines"]:
            T(cx, yy, ln, FONT_NODE, INK, "center")
            yy += FONT_NODE * 1.2
        if nd["mline"]:
            T(cx, yy - FONT_NODE * 0.2 + 12, nd["mline"], 44, INK, "center", weight="bold")
    # edge labels
    from matplotlib.patches import FancyBboxPatch
    for i, lines, r in elab:
        rec = paths[i][0]
        ec = CONTRA if rec["kind"] == "contradicts" else "#c9c6bd"
        ax.add_patch(FancyBboxPatch((r[0] + 8, r[1] + 8), r[2] - 16, r[3] - 16, boxstyle="round,pad=8",
                                    fc=SURFACE, ec=ec, lw=1.2, alpha=0.97, zorder=5))
        T(r[0] + r[2] / 2, r[1] + r[3] / 2, "\n".join(lines), FONT_EDGE, INK, "center", z=6)

    # ---- legend
    y = tail_y + 40
    used_dom = []
    for i in N:
        if nodes[i]["dom"] not in used_dom:
            used_dom.append(nodes[i]["dom"])
    used_typ = []
    for i in N:
        if nodes[i]["typ"] not in used_typ:
            used_typ.append(nodes[i]["typ"])
    used_dom.sort(key=lambda d: DOMAINS.index(d) if d in DOMAINS else 99)

    def flow(items, y0, label):
        T(MARGIN - 60, y0, label, FONT_LEG, MUTED, weight="bold")
        xx, yy = MARGIN - 60 + meas.w(label, FONT_LEG, "bold") + 40, y0
        for wdt, fn in items:
            if xx + wdt > W - MARGIN + 60:
                xx, yy = MARGIN - 60, yy + 76
            fn(xx, yy)
            xx += wdt + 44
        return yy + 76

    def dom_item(d):
        s = f"{d[1]} {d[0]}"
        def fn(xp, yp):
            ax.add_patch(__import__("matplotlib.patches", fromlist=["Rectangle"]).Rectangle((xp, yp - 19), 38, 38, fc=d[2], ec=INK, lw=0.8, zorder=3))
            T(xp + 52, yp, s, FONT_LEG, INK)
        return 52 + meas.w(s, FONT_LEG), fn

    def typ_item(t):
        shp, nm = TYPES[t]
        def fn(xp, yp):
            _shape(ax, shp, xp + 45, yp, 90, 64, "#e8e6df", INK, 3)
            T(xp + 110, yp, nm, FONT_LEG, INK)
        return 110 + meas.w(nm, FONT_LEG), fn

    def link_item(txt, kind):
        def fn(xp, yp):
            col = CONTRA if kind == "contra" else LINE
            ls = {"rep": "-", "pred": (0, (2.6, 1.6)), "contra": (0, (0.1, 2.3)), "fb": "-"}[kind]
            if kind == "fb":
                pts = _bez((xp, yp + 14), (xp + 30, yp - 34), (xp + 80, yp - 34), (xp + 110, yp + 14), 20)
            else:
                pts = np.array([[xp, yp], [xp + 110, yp]], float)
            ax.plot(pts[:, 0], pts[:, 1], color=col, lw=6 * 0.72, ls=ls, dash_capstyle="round", zorder=3)
            _arrow_head(ax, pts, 24, col)
            T(xp + 128, yp, txt, FONT_LEG, INK)
        return 128 + meas.w(txt, FONT_LEG), fn

    def text_item(txt):
        return meas.w(txt, FONT_LEG), (lambda xp, yp: T(xp, yp, txt, FONT_LEG, INK))

    y = flow([dom_item(d) for d in used_dom], y + 20, "Domain")
    y = flow([typ_item(t) for t in LEGEND_TYPES if t in used_typ or (t == "index" and "market" in used_typ)], y + 6, "Shape")
    y = flow([link_item("reported", "rep"), link_item("predicted", "pred"), link_item("contradicts", "contra"),
              link_item("feedback loop", "fb"), text_item("↑ ↓ ↕ effect on target · thicker = stronger")], y + 6, "Links")

    # ---- biggest movers
    mv = [nd for nd in nodes if nd["pct"] is not None]
    mv.sort(key=lambda nd: (-abs(nd["pct"]), nd["label"]))
    y += 30
    if mv:
        T(MARGIN - 60, y, "Biggest movers", 46, INK, weight="bold")
        y += 66
        colw = (W - 2 * MARGIN + 120) / 2
        shown = mv[:6]
        for k, nd in enumerate(shown):
            mk = nd["mk"] or {}
            price = str(mk.get("price") or "").strip()
            s = f"{nd['glyph']} {nd['label']}  {price}  ({mk.get('change') or ('%+.2f%%' % nd['pct'])})"
            s = " ".join(s.split())
            while len(s) > 8 and meas.w(s, 40) > colw - 30:
                s = s[:-2] + "…"
            T(MARGIN - 60 + (k % 2) * colw, y + (k // 2) * 62, s, 40, INK)
        y += ((len(shown) + 1) // 2) * 62 + 10
    hidden_e = len(edges) - len(E) - len(ann)
    note = f"Showing {len(E) + len(ann)} of {len(edges)} links; {hidden_e} more on the web page." if hidden_e > 0 else f"Showing all {len(edges)} links."
    T(MARGIN - 60, y + 20, note, 34, MUTED)
    y += 90
    T(W / 2, y, "Full interactive map on the web page", 44, INK, "center", weight="bold")
    H = y + 80
    fig.set_size_inches(W / 100, H / 100)
    ax.set_ylim(H, 0)
    return fig, H, hard


def render_graph_png(graph, title=""):
    """Return PNG bytes of the graph, or None if invalid / rendering fails."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        meas = _Meas()
        nodes, edges = _parse(graph, meas)
        if not nodes:
            return None
        sel = _select(nodes, edges, W - 2 * MARGIN)
        if not sel[0] or sel[2] is None:
            return None
        best = None
        for G in (260, 290, 320, 360, 400, 480):
            _, tail, hard = _draw(graph, title, G, meas, nodes, edges, sel, plot=False)
            if tail + 930 > MAX_H and best is not None:
                break
            if best is None or hard < best[1]:
                best = (G, hard)
            if hard == 0:
                break
        fig, H, _ = _draw(graph, title, best[0], meas, nodes, edges, sel)
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
