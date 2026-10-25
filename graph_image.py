"""Static PNG rendering of the connection-map graph (for Telegram / email)."""
import base64
import io
import textwrap

W, H = 12.0, 9.0  # figure inches; dpi 100 -> 1200x900
DOMAIN_COLORS = {
    "geopolitics": "#d1495b", "world": "#d1495b",
    "markets": "#2e86ab", "finance": "#2e86ab",
    "economy": "#edae49", "business": "#edae49",
    "tech": "#7b5ea7", "technology": "#7b5ea7", "ai": "#7b5ea7",
    "science": "#00a896", "health": "#00a896",
    "energy": "#e07a1f", "climate": "#3d9a47",
    "politics": "#a23b72", "sports": "#6c757d",
}
FALLBACK = ["#2e86ab", "#d1495b", "#edae49", "#7b5ea7", "#00a896",
            "#e07a1f", "#3d9a47", "#a23b72", "#6c757d"]
TYPE_STYLE = {  # radius (data units), marker
    "event": (0.50, "o"), "company": (0.40, "s"), "country": (0.40, "D"),
    "sector": (0.36, "h"), "market": (0.36, "^"),
}


def _color(domain, cache):
    key = str(domain or "other").strip().lower()
    if key in DOMAIN_COLORS:
        return DOMAIN_COLORS[key]
    if key not in cache:
        cache[key] = FALLBACK[len(cache) % len(FALLBACK)]
    return cache[key]


def _layout(n, edges, boxes):
    """Force layout in a W x H box with box-collision removal."""
    import numpy as np
    rng = np.random.default_rng(7)
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    pos = np.c_[W / 2 + 3.5 * np.cos(ang), H / 2 - 0.2 + 2.6 * np.sin(ang)]
    pos += rng.normal(0, 0.1, pos.shape)
    bw = np.array([b[0] for b in boxes])
    bh = np.array([b[1] for b in boxes])
    lo = np.array([0.2, 0.2]) + np.c_[bw, bh] / 2
    hi = np.array([W - 0.2, H - 1.5]) - np.c_[bw, bh] / 2
    hi[:, 1] = np.maximum(hi[:, 1], lo[:, 1] + 0.1)
    for it in range(400):
        t = 1 - it / 400
        d = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(d, axis=2) + 1e-3
        f = (d / dist[..., None] * (2.0 / dist ** 2)[..., None]).sum(1)
        for a, b in edges:
            v = pos[b] - pos[a]
            L = np.linalg.norm(v) + 1e-3
            pull = 0.12 * (L - 2.2) * v / L
            f[a] += pull
            f[b] -= pull
        f += 0.02 * (np.array([W / 2, H / 2 - 0.4]) - pos)
        pos += np.clip(f, -0.3, 0.3) * (0.3 + 0.7 * t)
        pos = np.clip(pos, lo, hi)
    for _ in range(300):  # hard overlap removal on label boxes
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                dx, dy = pos[j] - pos[i]
                ox = (bw[i] + bw[j]) / 2 + 0.15 - abs(dx)
                oy = (bh[i] + bh[j]) / 2 + 0.15 - abs(dy)
                if ox > 0 and oy > 0:
                    moved = True
                    if oy < ox:
                        s = 1 if dy >= 0 else -1
                        pos[i, 1] -= s * oy / 2
                        pos[j, 1] += s * oy / 2
                    else:
                        s = 1 if dx >= 0 else -1
                        pos[i, 0] -= s * ox / 2
                        pos[j, 0] += s * ox / 2
        pos = np.clip(pos, lo, hi)
        if not moved:
            break
    return pos


def render_graph_png(graph, title=""):
    """Return PNG bytes of the graph, or None if invalid / rendering fails."""
    try:
        nodes = [x for x in (graph or {}).get("nodes", []) if isinstance(x, dict) and x.get("id") is not None]
        if not nodes:
            return None
        nodes = nodes[:24]
        idx = {}
        for i, nd in enumerate(nodes):
            idx.setdefault(nd["id"], i)
        edges, meta, seen = [], [], set()
        for e in graph.get("edges", []) or []:
            a, b = idx.get(e.get("from")), idx.get(e.get("to"))
            if a is None or b is None or a == b or (a, b) in seen:
                continue
            seen.add((a, b))
            edges.append((a, b))
            try:
                s = max(1, min(3, int(e.get("strength", 1))))
            except Exception:
                s = 1
            meta.append((s, str(e.get("label") or "")[:28]))

        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import FancyArrowPatch, Patch

        wrapped = [textwrap.fill(str(nd.get("label") or nd["id"]), 15)[:60] for nd in nodes]
        boxes = []
        for nd, w in zip(nodes, wrapped):
            r = TYPE_STYLE.get(nd.get("type"), (0.38, "o"))[0]
            lines = w.count("\n") + 1
            boxes.append((max(2 * r, 0.13 * max(len(s) for s in w.split("\n")) + 0.2),
                          2 * r + 0.28 * lines + 0.1))
        pos = _layout(len(nodes), edges, boxes)

        fig = plt.figure(figsize=(W, H), dpi=100, facecolor="#fbfaf7")
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(0, W)
        ax.set_ylim(0, H)
        ax.axis("off")
        cache = {}
        cols = [_color(nd.get("domain"), cache) for nd in nodes]
        rad = [TYPE_STYLE.get(nd.get("type"), (0.38, "o"))[0] for nd in nodes]
        # node centre sits above the box centre so the label fits below it
        cen = [(pos[i][0], pos[i][1] + 0.14 * (wrapped[i].count("\n") + 1)) for i in range(len(nodes))]

        for (a, b), (s, lab) in zip(edges, meta):
            p = FancyArrowPatch(cen[a], cen[b], arrowstyle="-|>", mutation_scale=10 + 4 * s,
                                lw=0.8 + 1.1 * s, color="#8a8f98", alpha=0.85,
                                shrinkA=rad[a] * 72 * 1.05, shrinkB=rad[b] * 72 * 1.05,
                                connectionstyle="arc3,rad=0", zorder=1)
            ax.add_patch(p)
        for (a, b), (s, lab) in zip(edges, meta):
            if lab:
                mx, my = (cen[a][0] + cen[b][0]) / 2, (cen[a][1] + cen[b][1]) / 2
                ax.text(mx, my, lab, fontsize=9, ha="center", va="center", color="#444",
                        zorder=2, bbox=dict(boxstyle="round,pad=0.15", fc="#fbfaf7", ec="none", alpha=0.9))
        for i, nd in enumerate(nodes):
            style = TYPE_STYLE.get(nd.get("type"), (0.38, "o"))[1]
            ax.scatter([cen[i][0]], [cen[i][1]], s=(rad[i] * 72 * 2) ** 2 * 0.62, c=cols[i],
                       marker=style, edgecolors="white", linewidths=2, zorder=3)
            ax.text(cen[i][0], cen[i][1] - rad[i] - 0.06, wrapped[i], fontsize=11,
                    ha="center", va="top", color="#1d2430", fontweight="bold",
                    zorder=4, linespacing=1.05)

        if title:
            ax.text(0.3, H - 0.35, textwrap.shorten(str(title), 60), fontsize=20,
                    fontweight="bold", va="top", color="#1d2430")
        doms = []
        for nd in nodes:
            d = str(nd.get("domain") or "other")
            if d not in doms:
                doms.append(d)
        handles = [Patch(fc=_color(d, cache), ec="none", label=d.title()) for d in doms]
        leg = ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.02, 0.93),
                        ncol=min(len(handles), 5), fontsize=11, frameon=False)
        leg.set_zorder(5)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, facecolor=fig.get_facecolor())
        plt.close(fig)
        return buf.getvalue()
    except Exception:
        return None


def to_data_uri(png_bytes):
    """Data URI for embedding the PNG in email HTML."""
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
