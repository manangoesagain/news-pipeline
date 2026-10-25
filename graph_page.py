"""Visual renderers for the Intelligence Brief: connection map, scenario tree, causal flows.

Everything is inline SVG / CSS (no external libraries). Layout is computed here in Python
(deterministic layered layout), the small SCRIPT adds hover/tap/keyboard highlighting.
"""

import html
import itertools
import math
import re

DOMAINS = [
    "AI & ML",
    "Technology & Semiconductors",
    "Finance & Banking",
    "Stock Markets & Trading",
    "Geopolitics & Diplomacy",
    "Wars & Conflicts",
    "Commodities & Prices",
]
# keyword -> domain index, used to match loose domain strings from the LLM
_DOMAIN_KEYS = [
    ("ai", 0), ("machine", 0), ("tech", 1), ("semi", 1), ("financ", 2), ("bank", 2),
    ("stock", 3), ("market", 3), ("trad", 3), ("geopol", 4), ("diplom", 4),
    ("war", 5), ("conflict", 5), ("commod", 6), ("price", 6), ("oil", 6),
]

TYPES = ["event", "company", "country", "sector", "market"]
TYPE_LABEL = {"event": "Event", "company": "Company", "country": "Country", "sector": "Sector", "market": "Market"}

_counter = itertools.count(1)


def _e(text):
    text = re.sub(r"\s*\((?:ids?|per [^()]*?id)\s*[\d,\s]+(?:and\s*\d+)?\)", "", str(text or ""))
    return html.escape(text, quote=True)


def domain_index(name):
    """Index 0-6 into DOMAINS, or -1 for unknown."""
    n = str(name or "").strip().lower()
    if not n:
        return -1
    for i, d in enumerate(DOMAINS):
        if n == d.lower():
            return i
    for key, i in _DOMAIN_KEYS:
        if re.search(r"\b" + key, n):
            return i
    return -1


def _dcls(name):
    i = domain_index(name)
    return f"d{i}" if i >= 0 else "dx"


# ---------------------------------------------------------------- CSS / JS

CSS = """
    .viz { --d0:#7c3aed; --d1:#0e7490; --d2:#15803d; --d3:#b45309; --d4:#2563eb; --d5:#b91c1c; --d6:#be185d; --dx:#64748b;
           --ln:#64748b; --halo:var(--card); }
    @media (prefers-color-scheme: dark) {
      .viz { --d0:#a78bfa; --d1:#22d3ee; --d2:#4ade80; --d3:#fbbf24; --d4:#60a5fa; --d5:#f87171; --d6:#f472b6; --dx:#94a3b8; --ln:#94a3b8; }
    }
    .viz h4 { font-size: 14px; margin: 18px 0 6px; }
    .viz .vnote { font-size: 12px; color: var(--muted); margin: 0 0 8px; }
    .cmap { position: relative; border: 1px solid var(--border); border-radius: 10px; padding: 8px 4px 4px; background: var(--card); }
    .cmap svg { display: block; width: 100%; height: auto; margin: 0 auto; font-family: inherit; }
    .cmap .bgrect { fill: transparent; }
    .cmap .nd { cursor: pointer; outline: none; }
    .cmap .nd .shape { stroke: var(--card); stroke-width: 2; }
    .cmap .nd:focus-visible .shape, .cmap .nd.sel .shape { stroke: var(--text); stroke-width: 3; }
    .cmap .nlabel { font-size: 12px; fill: var(--text); text-anchor: middle; paint-order: stroke; stroke: var(--halo); stroke-width: 4px; stroke-linejoin: round; pointer-events: none; }
    .cmap .ed path.line { fill: none; stroke: var(--ln); opacity: .55; }
    .cmap .ed path.head { fill: var(--ln); stroke: none; opacity: .75; }
    .cmap .ed path.hit { fill: none; stroke: transparent; stroke-width: 14; cursor: help; }
    .cmap .elabel { font-size: 11px; fill: var(--text); text-anchor: middle; paint-order: stroke; stroke: var(--halo); stroke-width: 4px; stroke-linejoin: round; opacity: 0; pointer-events: none; }
    .cmap.focus .ed { opacity: .12; }
    .cmap.focus .nd { opacity: .35; }
    .cmap.focus .ed.hl { opacity: 1; }
    .cmap.focus .ed.hl path.line { opacity: 1; stroke: var(--text); }
    .cmap.focus .ed.hl path.head { opacity: 1; fill: var(--text); }
    .cmap.focus .ed.hl .elabel, .cmap .ed.hv .elabel { opacity: 1; }
    .cmap .ed.hv path.line { opacity: 1; stroke: var(--text); }
    .cmap.focus .nd.hl { opacity: 1; }
    .d0 { fill: var(--d0); } .d1 { fill: var(--d1); } .d2 { fill: var(--d2); } .d3 { fill: var(--d3); }
    .d4 { fill: var(--d4); } .d5 { fill: var(--d5); } .d6 { fill: var(--d6); } .dx { fill: var(--dx); }
    .cinfo { font-size: 13px; padding: 6px 10px 8px; min-height: 20px; color: var(--muted); }
    .cinfo b { color: var(--text); }
    .cinfo ul { list-style: none; margin-top: 4px; }
    .cinfo li { margin-bottom: 2px; }
    .ctip { position: absolute; z-index: 5; pointer-events: none; background: var(--text); color: var(--card); font-size: 12px;
            padding: 4px 8px; border-radius: 6px; max-width: 220px; line-height: 1.35; display: none; }
    .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; font-size: 12px; color: var(--muted); padding: 2px 10px 6px; }
    .legend span { display: inline-flex; align-items: center; gap: 5px; }
    .legend svg { width: 14px; height: 14px; flex: none; }
    .legend .sw { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
    .legend .sw.d0 { background: var(--d0); } .legend .sw.d1 { background: var(--d1); } .legend .sw.d2 { background: var(--d2); }
    .legend .sw.d3 { background: var(--d3); } .legend .sw.d4 { background: var(--d4); } .legend .sw.d5 { background: var(--d5); }
    .legend .sw.d6 { background: var(--d6); } .legend .sw.dx { background: var(--dx); }
    .legend .shp { fill: var(--muted); }

    .flows { display: flex; flex-direction: column; gap: 12px; }
    .flow-title { font-size: 13px; font-weight: 600; margin-bottom: 4px; }
    .flow { display: flex; flex-wrap: wrap; align-items: stretch; gap: 6px 0; }
    .fbox { flex: 0 1 auto; max-width: 150px; min-width: 84px; font-size: 13px; line-height: 1.3; padding: 6px 9px; background: var(--code-bg);
            border: 1px solid var(--border); border-left: 4px solid var(--dx); border-radius: 6px; }
    .fbox.d0 { border-left-color: var(--d0); } .fbox.d1 { border-left-color: var(--d1); } .fbox.d2 { border-left-color: var(--d2); }
    .fbox.d3 { border-left-color: var(--d3); } .fbox.d4 { border-left-color: var(--d4); } .fbox.d5 { border-left-color: var(--d5); }
    .fbox.d6 { border-left-color: var(--d6); }
    .farrow { flex: none; width: 24px; display: flex; align-items: center; justify-content: center; color: var(--muted); }
    .farrow svg { width: 20px; height: 14px; }
    .fdoms { display: flex; flex-wrap: wrap; gap: 2px 10px; font-size: 11px; color: var(--muted); margin-top: 4px; }
    .fdoms span::before { content: ""; display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 4px; background: var(--dx); }
    .fdoms .d0::before { background: var(--d0); } .fdoms .d1::before { background: var(--d1); } .fdoms .d2::before { background: var(--d2); }
    .fdoms .d3::before { background: var(--d3); } .fdoms .d4::before { background: var(--d4); } .fdoms .d5::before { background: var(--d5); }
    .fdoms .d6::before { background: var(--d6); }

    .stree { position: relative; margin-left: 10px; padding-left: 18px; border-left: 2px solid var(--border); }
    .sroot { font-size: 12px; color: var(--muted); margin: 0 0 8px -28px; }
    .sbranch { position: relative; margin-bottom: 12px; }
    .sbranch::before { content: ""; position: absolute; left: -18px; top: 22px; width: 18px; border-top: 2px solid var(--border); }
    .sbox { border: 1px solid var(--border); border-radius: 8px; background: var(--code-bg); padding: 8px 10px; font-size: 13px; line-height: 1.35; }
    .sbox .k { font-size: 11px; font-weight: 700; letter-spacing: .04em; color: var(--muted); display: block; }
    .sthen { margin: 0 0 0 22px; position: relative; border-left: 3px solid var(--lk, var(--dx)); }
    .sthen::before { content: ""; position: absolute; left: -23px; top: -6px; height: 20px; width: 20px; border-left: 2px solid var(--border); border-bottom: 2px solid var(--border); border-bottom-left-radius: 8px; }
    .sthen { margin-top: 6px; }
    .lk-low { --lk: #64748b; } .lk-medium { --lk: #b45309; } .lk-high { --lk: #b91c1c; }
    @media (prefers-color-scheme: dark) { .lk-low { --lk: #94a3b8; } .lk-medium { --lk: #fbbf24; } .lk-high { --lk: #f87171; } }
    .lkb { display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 600; border: 1px solid var(--lk, var(--dx)); color: var(--text);
           border-radius: 999px; padding: 0 8px 0 6px; margin-left: 6px; vertical-align: 1px; white-space: nowrap; }
    .lkb i { display: inline-flex; gap: 2px; }
    .lkb i b { width: 5px; height: 9px; border: 1px solid var(--lk); border-radius: 1px; display: inline-block; }
    .lkb i b.on { background: var(--lk); }
    .sign { font-size: 12px; color: var(--muted); margin-top: 4px; }

    details.idetails { margin-top: 16px; border: 1px solid var(--border); border-radius: 8px; }
    details.idetails > summary { cursor: pointer; padding: 8px 12px; font-size: 14px; font-weight: 600; }
    details.idetails > div { padding: 0 12px 10px; }
"""

SCRIPT = r"""
(function () {
  function init(box) {
    var svg = box.querySelector("svg"), tip = box.querySelector(".ctip"), info = box.querySelector(".cinfo");
    var nodes = [].slice.call(svg.querySelectorAll(".nd")), edges = [].slice.call(svg.querySelectorAll(".ed"));
    var byId = {}; nodes.forEach(function (n) { byId[n.getAttribute("data-id")] = n; });
    var sel = null;
    function hint() { info.textContent = "Tap or focus a node to see its connections."; }
    function clear() {
      sel = null; box.classList.remove("focus");
      nodes.forEach(function (n) { n.classList.remove("hl", "sel"); });
      edges.forEach(function (e) { e.classList.remove("hl"); });
      hint();
    }
    function select(n) {
      if (sel === n) { clear(); return; }
      clear(); sel = n; var id = n.getAttribute("data-id");
      box.classList.add("focus"); n.classList.add("hl", "sel");
      var out = [], inn = [];
      edges.forEach(function (e) {
        var f = e.getAttribute("data-from"), t = e.getAttribute("data-to");
        if (f !== id && t !== id) return;
        e.classList.add("hl"); var o = byId[f === id ? t : f]; if (o) o.classList.add("hl");
        var line = (o ? o.getAttribute("data-label") : "?") + (e.getAttribute("data-label") ? " — " + e.getAttribute("data-label") : "");
        (f === id ? out : inn).push(line);
      });
      function li(a) { return a.map(function (s) { var l = document.createElement("li"); l.textContent = s; return l; }); }
      info.textContent = "";
      var b = document.createElement("b"); b.textContent = n.getAttribute("data-label");
      info.appendChild(b); info.appendChild(document.createTextNode(" · " + n.getAttribute("data-meta")));
      [["Leads to", out], ["Driven by", inn]].forEach(function (g) {
        if (!g[1].length) return;
        var p = document.createElement("div"); p.textContent = g[0] + ":"; info.appendChild(p);
        var ul = document.createElement("ul"); li(g[1]).forEach(function (x) { ul.appendChild(x); }); info.appendChild(ul);
      });
    }
    function showTip(text, ev) {
      tip.textContent = text; tip.style.display = "block";
      var r = box.getBoundingClientRect(), x = ev.clientX - r.left + 12, y = ev.clientY - r.top + 14;
      var w = tip.offsetWidth; if (x + w > r.width - 4) x = Math.max(4, r.width - w - 4);
      tip.style.left = x + "px"; tip.style.top = y + "px";
    }
    function hideTip() { tip.style.display = "none"; }
    nodes.forEach(function (n) {
      n.addEventListener("click", function (ev) { ev.stopPropagation(); select(n); });
      n.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); select(n); } if (ev.key === "Escape") clear(); });
      n.addEventListener("mousemove", function (ev) { showTip(n.getAttribute("data-label") + " · " + n.getAttribute("data-meta"), ev); });
      n.addEventListener("mouseleave", hideTip);
    });
    edges.forEach(function (e) {
      var hit = e.querySelector(".hit");
      hit.addEventListener("mousemove", function (ev) {
        e.classList.add("hv");
        showTip(e.getAttribute("data-flabel") + " → " + e.getAttribute("data-tlabel") + (e.getAttribute("data-label") ? ": " + e.getAttribute("data-label") : "") + " (strength " + e.getAttribute("data-s") + "/3)", ev);
      });
      hit.addEventListener("mouseleave", function () { e.classList.remove("hv"); hideTip(); });
      hit.addEventListener("click", function (ev) { ev.stopPropagation(); showTip(e.getAttribute("data-flabel") + " → " + e.getAttribute("data-tlabel") + (e.getAttribute("data-label") ? ": " + e.getAttribute("data-label") : ""), ev); setTimeout(hideTip, 2500); });
    });
    svg.addEventListener("click", clear);
    box.addEventListener("keydown", function (ev) { if (ev.key === "Escape") clear(); });
    hint();
  }
  [].forEach.call(document.querySelectorAll(".cmap"), init);
})();
"""


# ---------------------------------------------------------------- connection map

COL_GAP = 150
ROW_GAP = 128
PER_ROW = 3
MAX_NODES = 18
PAD_X = 20
PAD_TOP = 34


def _wrap(label, width=17, lines=2):
    words = str(label or "").split()
    out, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1][: width - 1].rstrip() + "…"
    return [o[: width + 2] for o in out] or [""]


def _layout(nodes, edges):
    """Layered layout. Returns {id: (x, y)}, width, height. Edges point downward where possible."""
    ids = [n["id"] for n in nodes]
    succ = {i: [] for i in ids}
    for e in edges:
        succ[e["from"]].append(e["to"])
    # break cycles: DFS, drop back edges for layering purposes
    state, fwd = {}, []

    def dfs(u):
        state[u] = 1
        for v in succ[u]:
            if state.get(v) == 1:
                continue
            fwd.append((u, v))
            if v not in state:
                dfs(v)
        state[u] = 2

    deg = {i: 0 for i in ids}
    for e in edges:
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    indeg = {i: 0 for i in ids}
    for e in edges:
        indeg[e["to"]] += 1
    for i in sorted(ids, key=lambda k: (indeg[k], -deg[k])):
        if i not in state:
            dfs(i)
    fwd = list(dict.fromkeys(fwd))
    preds = {i: [] for i in ids}
    for u, v in fwd:
        preds[v].append(u)
    layer = {}

    def lv(i, seen=()):
        if i in layer:
            return layer[i]
        layer[i] = 0 if not preds[i] else 1 + max(lv(p) for p in preds[i])
        return layer[i]

    for i in ids:
        lv(i)
    nl = max(layer.values()) + 1
    layers = [[i for i in ids if layer[i] == k] for k in range(nl)]
    pos = {i: k for L in layers for k, i in enumerate(L)}
    nbr = {i: [] for i in ids}
    for u, v in fwd:
        nbr[u].append(v)
        nbr[v].append(u)

    def bary(i, ref):
        xs = [pos[j] for j in nbr[i] if layer[j] in ref]
        return sum(xs) / len(xs) if xs else pos[i]

    for _ in range(4):  # barycenter sweeps down then up
        for k in range(1, nl):
            layers[k].sort(key=lambda i: bary(i, {layer[i] - 1}))
            for x, i in enumerate(layers[k]):
                pos[i] = x
        for k in range(nl - 2, -1, -1):
            layers[k].sort(key=lambda i: bary(i, {layer[i] + 1}))
            for x, i in enumerate(layers[k]):
                pos[i] = x
    # split wide layers into rows of PER_ROW
    rows = []
    for L in layers:
        for s in range(0, len(L), PER_ROW):
            rows.append(L[s:s + PER_ROW])
    width = PER_ROW * COL_GAP + 2 * PAD_X - COL_GAP + 80
    coords = {}
    for r, row in enumerate(rows):
        for c, i in enumerate(row):
            x = width / 2 + (c - (len(row) - 1) / 2) * COL_GAP
            coords[i] = (x, PAD_TOP + r * ROW_GAP)
    height = PAD_TOP + (len(rows) - 1) * ROW_GAP + 74
    return coords, width, height


def _shape(kind, x, y, r, cls):
    if kind == "company":
        return f'<rect class="shape {cls}" x="{x - r:.1f}" y="{y - r:.1f}" width="{2 * r}" height="{2 * r}" rx="5"/>', r
    if kind == "country":
        d = r * 1.3
        return f'<path class="shape {cls}" d="M{x:.1f} {y - d:.1f} L{x + d:.1f} {y:.1f} L{x:.1f} {y + d:.1f} L{x - d:.1f} {y:.1f}Z"/>', d
    if kind == "sector":
        pts = " ".join(f"{x + r * 1.1 * math.cos(a):.1f},{y + r * 1.1 * math.sin(a):.1f}"
                       for a in [k * 3.14159265 / 3 for k in range(6)])
        return f'<polygon class="shape {cls}" points="{pts}"/>', r * 1.1
    if kind == "market":
        h = r * 1.25
        return (f'<path class="shape {cls}" d="M{x:.1f} {y - h:.1f} L{x + h:.1f} {y + h * 0.8:.1f} '
                f'L{x - h:.1f} {y + h * 0.8:.1f}Z"/>'), h
    return f'<circle class="shape {cls}" cx="{x:.1f}" cy="{y:.1f}" r="{r + 3}"/>', r + 3


def _legend(domains_used, types_used):
    items = []
    for i in sorted(domains_used):
        name = DOMAINS[i] if i >= 0 else "Other"
        items.append(f'<span><i class="sw {"d%d" % i if i >= 0 else "dx"}"></i>{_e(name)}</span>')
    shp = {
        "event": '<circle cx="7" cy="7" r="5.5"/>',
        "company": '<rect x="2" y="2" width="10" height="10" rx="2"/>',
        "country": '<path d="M7 1 L13 7 L7 13 L1 7Z"/>',
        "sector": '<polygon points="12.5,7 9.8,11.7 4.2,11.7 1.5,7 4.2,2.3 9.8,2.3"/>',
        "market": '<path d="M7 1.5 L13 12 L1 12Z"/>',
    }
    for t in TYPES:
        if t in types_used:
            items.append(f'<span><svg viewBox="0 0 14 14" aria-hidden="true" class="shp">{shp[t]}</svg>{TYPE_LABEL[t]}</span>')
    return '<div class="legend">' + "".join(items) + "</div>"


def _clean_graph(graph):
    nodes, seen = [], set()
    for n in (graph or {}).get("nodes") or []:
        if not isinstance(n, dict) or not n.get("id") or str(n["id"]) in seen:
            continue
        seen.add(str(n["id"]))
        t = str(n.get("type") or "event").lower()
        nodes.append({"id": str(n["id"]), "label": str(n.get("label") or n["id"]),
                      "type": t if t in TYPES else "event", "domain": n.get("domain") or ""})
    edges, es = [], set()
    ids = {n["id"] for n in nodes}
    for e in (graph or {}).get("edges") or []:
        if not isinstance(e, dict):
            continue
        f, t = str(e.get("from", "")), str(e.get("to", ""))
        if f not in ids or t not in ids or f == t or (f, t) in es:
            continue
        es.add((f, t))
        try:
            s = max(1, min(3, int(e.get("strength", 1))))
        except (TypeError, ValueError):
            s = 1
        edges.append({"from": f, "to": t, "label": str(e.get("label") or ""), "strength": s})
    if len(nodes) > MAX_NODES:
        deg = {}
        for e in edges:
            deg[e["from"]] = deg.get(e["from"], 0) + 1
            deg[e["to"]] = deg.get(e["to"], 0) + 1
        keep = {n["id"] for n in sorted(nodes, key=lambda n: -deg.get(n["id"], 0))[:MAX_NODES]}
        nodes = [n for n in nodes if n["id"] in keep]
        edges = [e for e in edges if e["from"] in keep and e["to"] in keep]
    return nodes, edges


def connection_map(graph, note=""):
    """Inline-SVG connection map. Returns '' if the graph is empty."""
    nodes, edges = _clean_graph(graph)
    if len(nodes) < 2:
        return ""
    uid = next(_counter)
    coords, W, H = _layout(nodes, edges)
    byid = {n["id"]: n for n in nodes}
    R = {"event": 17}
    edge_svg, node_svg = [], []
    used_d, used_t = set(), set()
    for e in edges:
        (x1, y1), (x2, y2) = coords[e["from"]], coords[e["to"]]
        r1 = 20 if byid[e["from"]]["type"] != "event" else 22
        r2 = 20 if byid[e["to"]]["type"] != "event" else 22
        back = y2 <= y1
        if y2 > y1:
            sx, sy, ex, ey = x1, y1 + r1 + 4 + 14 * len(_wrap(byid[e["from"]]["label"])) + 2, x2, y2 - r2 - 5
            c1 = (sx, sy + (ey - sy) * 0.55)
            c2 = (ex, ey - (ey - sy) * 0.45)
        else:  # same row or upward: arc around the side
            side = 1 if (x1 + x2) / 2 >= W / 2 else -1
            sx, sy = x1 + side * r1, y1
            ex, ey = x2 + side * (r2 + 5), y2
            bulge = side * (abs(x2 - x1) * 0.3 + 40 + abs(y2 - y1) * 0.15)
            c1 = (sx + bulge, sy + (ey - sy) * 0.15)
            c2 = (ex + bulge, ey - (ey - sy) * 0.15)
        sw = 0.8 + e["strength"] * 0.9
        d = f"M{sx:.1f} {sy:.1f} C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {ex:.1f} {ey:.1f}"
        # arrowhead along the end tangent
        tx, ty = ex - c2[0], ey - c2[1]
        L = math.hypot(tx, ty) or 1
        tx, ty = tx / L, ty / L
        ah = 5 + e["strength"] * 1.5
        bx, by = ex - tx * ah, ey - ty * ah
        px, py = -ty * ah * 0.55, tx * ah * 0.55
        head = f"M{ex + tx * 3:.1f} {ey + ty * 3:.1f} L{bx + px:.1f} {by + py:.1f} L{bx - px:.1f} {by - py:.1f}Z"
        # label at curve midpoint (t=.5)
        mx = (sx + 3 * c1[0] + 3 * c2[0] + ex) / 8
        my = (sy + 3 * c1[1] + 3 * c2[1] + ey) / 8
        lab = _e(e["label"])
        edge_svg.append(
            f'<g class="ed" data-from="{_e(e["from"])}" data-to="{_e(e["to"])}" data-label="{lab}" data-s="{e["strength"]}" '
            f'data-flabel="{_e(byid[e["from"]]["label"])}" data-tlabel="{_e(byid[e["to"]]["label"])}">'
            f'<path class="line" d="{d}" stroke-width="{sw:.1f}"/><path class="head" d="{head}"/>'
            f'<path class="hit" d="{d}"/><text class="elabel" x="{mx:.1f}" y="{my:.1f}">{_e(_wrap(e["label"], 22, 1)[0])}</text></g>')
    for n in nodes:
        x, y = coords[n["id"]]
        di = domain_index(n["domain"])
        used_d.add(di)
        used_t.add(n["type"])
        cls = f"d{di}" if di >= 0 else "dx"
        shp, ext = _shape(n["type"], x, y, 17, cls)
        lines = _wrap(n["label"])
        ty0 = y + ext + 15
        tsp = "".join(f'<tspan x="{x:.1f}" y="{ty0 + k * 14:.1f}">{_e(l)}</tspan>' for k, l in enumerate(lines))
        meta = f'{TYPE_LABEL[n["type"]]}' + (f', {DOMAINS[di]}' if di >= 0 else (f', {n["domain"]}' if n["domain"] else ""))
        node_svg.append(
            f'<g class="nd" tabindex="0" role="button" data-id="{_e(n["id"])}" data-label="{_e(n["label"])}" data-meta="{_e(meta)}" '
            f'aria-label="{_e(n["label"])}, {_e(meta)}. Show connections.">{shp}'
            f'<text class="nlabel">{tsp}</text></g>')
    svg = (f'<svg viewBox="0 0 {W:.0f} {H:.0f}" role="group" aria-label="Connection map" style="max-width:{W * 1.45:.0f}px">'
           f'<rect class="bgrect" width="{W:.0f}" height="{H:.0f}"/>{"".join(edge_svg)}{"".join(node_svg)}</svg>')
    note_html = f'<p class="vnote">{_e(note)}</p>' if note else ""
    return (f'<h4>Connection map</h4>{note_html}'
            f'<div class="cmap" id="cmap{uid}">{svg}<div class="ctip" aria-hidden="true"></div>'
            f'{_legend(used_d, used_t)}<div class="cinfo" aria-live="polite"></div></div>')


# ---------------------------------------------------------------- scenario tree

_LK = {"low": 1, "medium": 2, "high": 3}


def scenario_tree(scenarios):
    if not scenarios:
        return ""
    out = ['<h4>Scenario tree (next 1-2 weeks)</h4><div class="stree"><div class="sroot">Today &rarr; possible branches</div>']
    for sc in scenarios:
        lk = str(sc.get("likelihood") or "").strip().lower()
        n = _LK.get(lk, 0)
        cls = f"lk-{lk}" if lk in _LK else ""
        bars = "".join(f'<b class="{"on" if k < n else ""}"></b>' for k in range(3))
        badge = f'<span class="lkb"><i aria-hidden="true">{bars}</i>{_e(lk or "?")}</span>'
        sign = f'<div class="sign">Watch for: {_e(sc["signpost"])}</div>' if sc.get("signpost") else ""
        out.append(
            f'<div class="sbranch {cls}"><div class="sbox"><span class="k">IF</span>{_e(sc.get("if"))}</div>'
            f'<div class="sbox sthen"><span class="k">THEN {badge}</span>{_e(sc.get("then"))}{sign}</div></div>')
    out.append("</div>")
    return "".join(out)


# ---------------------------------------------------------------- causal flows

_ARROW = '<svg viewBox="0 0 20 14" aria-hidden="true"><path d="M1 7 H15 M10 2 L16 7 L10 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'


def causal_flows(chains):
    if not chains:
        return ""
    out = ['<h4>Causal chains</h4><div class="flows">']
    for c in chains:
        steps = [s for s in (c.get("chain") or []) if s]
        doms = [d for d in (c.get("domains") or []) if d]
        boxes = []
        for k, s in enumerate(steps):
            d = doms[min(k, len(doms) - 1)] if doms else ""
            if k:
                boxes.append(f'<div class="farrow">{_ARROW}</div>')
            boxes.append(f'<div class="fbox {_dcls(d) if d else "dx"}">{_e(s)}</div>')
        dd = "".join(f'<span class="{_dcls(d)}">{_e(d)}</span>' for d in dict.fromkeys(doms))
        out.append(f'<div><div class="flow-title">{_e(c.get("title"))}</div><div class="flow" role="list">{"".join(boxes)}</div>'
                   f'{"<div class=fdoms>" + dd + "</div>" if dd else ""}</div>')
    out.append("</div>")
    return "".join(out)
