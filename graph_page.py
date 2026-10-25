"""Visual renderers for the Intelligence Brief: connection map, scenario tree, causal flows.

Everything is inline SVG / CSS / vanilla JS (no external libraries, no CDN). The map layout is
computed here in Python (deterministic: domain swim-lanes x causal depth columns), the SCRIPT adds
zoom/pan, filters, search, node panel and chain highlighting. The graph payload is embedded as JSON
(with "<" escaped), and every string is escaped or set through textContent.
"""

import html
import itertools
import json
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

# node types: one shape each. Legacy "market" is kept as an alias of "index".
TYPES = ["event", "country", "company", "sector", "commodity", "index", "institution"]
TYPE_LABEL = {"event": "Event", "country": "Country", "company": "Company", "sector": "Sector",
              "commodity": "Commodity", "index": "Index / market", "institution": "Institution"}
_TYPE_ALIAS = {"market": "index", "markets": "index", "person": "institution", "org": "institution",
               "organization": "institution", "organisation": "institution", "bank": "institution"}

_counter = itertools.count(1)
_CITE = re.compile(r"\s*\((?:ids?|per [^()]*?id)\s*[\d,\s]+(?:and\s*\d+)?\)")


def _e(text):
    return html.escape(_CITE.sub("", str(text if text is not None else "")), quote=True)


def _txt(v, n=400):
    """Clean, bounded plain string (unescaped)."""
    if v is None or isinstance(v, (dict, list, tuple, set)):
        return ""
    s = _CITE.sub("", str(v)).strip()
    s = re.sub(r"\s+", " ", s)
    return s[:n]


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
# Domain colours: reference categorical palette slots (blue, orange, aqua, yellow, magenta, green,
# violet), validated with the dataviz validator (adjacent CVD dE >= 8.4, normal-vision dE >= 19).
# Colour never works alone: lanes are labelled, every node is labelled and has a type shape.

CSS = """
    .viz { --d0:#6250d6; --d1:#008300; --d2:#1baf7a; --d3:#e87ba4; --d4:#2a78d6; --d5:#eb6834; --d6:#eda100; --dx:#64748b;
           --ln:#64748b; --halo:var(--card);
           --up:#1a7f37; --down:#cf222e; --mix:#9a6700; --con:#b4236f; --ink2:#475569; --focus:#2563eb; --lane:.075; }
    @media (prefers-color-scheme: dark) {
      .viz { --d0:#9085e9; --d1:#008300; --d2:#199e70; --d3:#d55181; --d4:#3987e5; --d5:#d95926; --d6:#c98500; --dx:#94a3b8; --ln:#94a3b8;
             --up:#3fb950; --down:#f85149; --mix:#d29922; --con:#f778ba; --ink2:#cbd5e1; --focus:#79c0ff; --lane:.11; }
    }
    .viz h4 { font-size: 14px; margin: 18px 0 6px; }
    .viz .vnote { font-size: 12px; color: var(--muted); margin: 0 0 8px; }

    .cmapwrap { margin: 0; }
    @media (min-width: 900px) {
      .cmapwrap { width: min(1080px, calc(100vw - 40px)); position: relative; left: 50%; transform: translateX(-50%); }
    }
    .cmap { position: relative; border: 1px solid var(--border); border-radius: 10px; background: var(--card); overflow: hidden; }
    .cmap button, .cmap select, .cmap input { font: inherit; color: var(--text); }
    .cfilters { display: flex; flex-wrap: wrap; gap: 6px 8px; align-items: center; padding: 6px 10px 8px; }
    .cfdet { border-bottom: 1px solid var(--border); }
    .cfilters .grp { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
    .cmap .chip { font-size: 12px; display: inline-flex; align-items: center; gap: 5px; font-size: 12px; line-height: 1.2; padding: 3px 9px 3px 7px; border: 1px solid var(--border);
            border-radius: 999px; background: transparent; cursor: pointer; min-height: 26px; }
    .chip i { width: 9px; height: 9px; border-radius: 50%; background: var(--dx); flex: none; }
    .chip[aria-pressed="false"] { color: var(--muted); text-decoration: line-through; }
    .chip[aria-pressed="false"] i { background: transparent; box-shadow: inset 0 0 0 1.5px var(--dx); }
    .chip.d0 i { background: var(--d0); } .chip.d1 i { background: var(--d1); } .chip.d2 i { background: var(--d2); } .chip.d3 i { background: var(--d3); }
    .chip.d4 i { background: var(--d4); } .chip.d5 i { background: var(--d5); } .chip.d6 i { background: var(--d6); }
    .chip[aria-pressed="false"].d0 i { box-shadow: inset 0 0 0 1.5px var(--d0); background: transparent; } .chip[aria-pressed="false"].d1 i { box-shadow: inset 0 0 0 1.5px var(--d1); background: transparent; }
    .chip[aria-pressed="false"].d2 i { box-shadow: inset 0 0 0 1.5px var(--d2); background: transparent; } .chip[aria-pressed="false"].d3 i { box-shadow: inset 0 0 0 1.5px var(--d3); background: transparent; }
    .chip[aria-pressed="false"].d4 i { box-shadow: inset 0 0 0 1.5px var(--d4); background: transparent; } .chip[aria-pressed="false"].d5 i { box-shadow: inset 0 0 0 1.5px var(--d5); background: transparent; }
    .chip[aria-pressed="false"].d6 i { box-shadow: inset 0 0 0 1.5px var(--d6); background: transparent; }
    .cmap select, .cmap .csearch { font-size: 12px; padding: 3px 6px; border: 1px solid var(--border); border-radius: 6px; background: var(--card); min-height: 26px; }
    .cmap .csearch { min-width: 120px; flex: 1 1 120px; max-width: 220px; }
    .cmap .clink { font-size: 12px; background: none; border: 0; color: var(--accent); cursor: pointer; padding: 3px 4px; text-decoration: underline; }
    .cfdet > summary { cursor: pointer; font-size: 12px; font-weight: 600; padding: 7px 10px; list-style: none; display: flex; gap: 10px; align-items: baseline; }
    .cfdet > summary::-webkit-details-marker { display: none; }
    .cfdet > summary::before { content: "\\25B8"; color: var(--muted); }
    .cfdet[open] > summary::before { content: "\\25BE"; }
    .cfdet .cfilters { padding-top: 2px; }
    .cmap .ccount { font-size: 11px; font-weight: 400; color: var(--muted); margin-left: auto; text-align: right; }
    @media (max-width: 600px) { .cfilters .grp { flex-wrap: nowrap; overflow-x: auto; width: 100%; padding-bottom: 3px; } .chip { white-space: nowrap; flex: none; } }
    .cmap :focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }

    .cbody { display: grid; grid-template-columns: minmax(0, 1fr); }
    @media (min-width: 900px) { .cbody { grid-template-columns: minmax(0, 1fr) 290px; } .cpanel { border-left: 1px solid var(--border); border-top: 0 !important; max-height: 680px; } }
    .cview { position: relative; height: min(68vh, 470px); background: var(--card); outline-offset: -2px; }
    @media (min-width: 900px) { .cview { height: 680px; } }
    .cview svg { display: block; width: 100%; height: 100%; touch-action: none; cursor: grab; font-family: inherit; user-select: none; -webkit-user-select: none; }
    .cview.drag svg { cursor: grabbing; }
    .cmap .lane { fill: var(--dx); fill-opacity: var(--lane); stroke: var(--dx); stroke-opacity: .3; stroke-width: 1; }
    .cmap .lane.d0 { fill: var(--d0); stroke: var(--d0); } .cmap .lane.d1 { fill: var(--d1); stroke: var(--d1); } .cmap .lane.d2 { fill: var(--d2); stroke: var(--d2); }
    .cmap .lane.d3 { fill: var(--d3); stroke: var(--d3); } .cmap .lane.d4 { fill: var(--d4); stroke: var(--d4); } .cmap .lane.d5 { fill: var(--d5); stroke: var(--d5); }
    .cmap .lane.d6 { fill: var(--d6); stroke: var(--d6); }
    .cmap .lanecount { font-size: 10px; fill: var(--muted); pointer-events: none; }
    .cmap .lanelabel { font-size: 11px; font-weight: 600; fill: var(--ink2); pointer-events: none; }
    .cmap .lanedot.d0 { fill: var(--d0); } .cmap .lanedot.d1 { fill: var(--d1); } .cmap .lanedot.d2 { fill: var(--d2); } .cmap .lanedot.d3 { fill: var(--d3); }
    .cmap .lanedot.d4 { fill: var(--d4); } .cmap .lanedot.d5 { fill: var(--d5); } .cmap .lanedot.d6 { fill: var(--d6); } .cmap .lanedot.dx { fill: var(--dx); }
    .cmap .lg.hid { opacity: .22; }

    .cmap .nd { cursor: pointer; outline: none; }
    .cmap .nd .tap { fill: transparent; }
    .cmap .nd .shape { stroke: var(--card); stroke-width: 1.5; }
    .cmap .nd .shape.d0 { fill: var(--d0); } .cmap .nd .shape.d1 { fill: var(--d1); } .cmap .nd .shape.d2 { fill: var(--d2); } .cmap .nd .shape.d3 { fill: var(--d3); }
    .cmap .nd .shape.d4 { fill: var(--d4); } .cmap .nd .shape.d5 { fill: var(--d5); } .cmap .nd .shape.d6 { fill: var(--d6); } .cmap .nd .shape.dx { fill: var(--dx); }
    .cmap .nd .ring { fill: none; stroke: transparent; stroke-width: 3; pointer-events: none; }
    .cmap .nd:focus-visible .ring { stroke: var(--focus); }
    .cmap .nd.sel .ring { stroke: var(--text); }
    .cmap .nd.m .ring { stroke: var(--focus); stroke-dasharray: 3 2; }
    .cmap .nlabel { font-size: 12px; fill: var(--text); text-anchor: middle; paint-order: stroke; stroke: var(--halo); stroke-width: 4px; stroke-linejoin: round; pointer-events: none; }
    .cmap .dirb { stroke: var(--card); stroke-width: 1.5; pointer-events: none; }
    .cmap .dirb.up { fill: var(--up); } .cmap .dirb.down { fill: var(--down); } .cmap .dirb.flat { fill: var(--dx); }

    .cmap .ed { opacity: var(--eo, .8); }
    .cmap .ed path.line { fill: none; stroke: var(--ec); stroke-linecap: butt; }
    .cmap .ed.e-up { --ec: var(--up); } .cmap .ed.e-down { --ec: var(--down); } .cmap .ed.e-mixed { --ec: var(--mix); } .cmap .ed.e-none { --ec: var(--ln); }
    .cmap .ed.k-contradicts { --ec: var(--con); }
    .cmap .ed.k-contradicts path.line { stroke-dasharray: 1.5 4; stroke-linecap: round; }
    .cmap .ed.st-predicted:not(.k-contradicts) path.line { stroke-dasharray: 7 4.5; }
    .cmap .ed path.hit { fill: none; stroke: transparent; stroke-width: 13; cursor: help; }
    .cmap .mk { stroke: none; }
    .cmap .mk.e-up { fill: var(--up); } .cmap .mk.e-down { fill: var(--down); } .cmap .mk.e-mixed { fill: var(--mix); } .cmap .mk.e-none { fill: var(--ln); }
    .cmap .mk.bar { fill: none; stroke: var(--con); stroke-width: 2.4; stroke-linecap: round; }
    .cmap .chip2 { fill: var(--card); stroke: var(--ec); stroke-width: 1.3; }
    .cmap .glyph { fill: var(--ec); stroke: none; }
    .cmap .glyphtx { font-size: 10px; font-weight: 700; fill: var(--ec); text-anchor: middle; pointer-events: none; }
    .cmap .elabel { font-size: 10.5px; fill: var(--text); text-anchor: middle; paint-order: stroke; stroke: var(--halo); stroke-width: 3.5px; stroke-linejoin: round; opacity: 0; pointer-events: none; }
    .cmap .ed.perm .elabel, .cmap .ed.hv .elabel, .cmap .ed.hl .elabel, .cmap .zl .ed .elabel { opacity: 1; }
    .cmap .ed.hv, .cmap .ed.hl { opacity: 1; }
    .cmap .ed.hv path.line, .cmap .ed.hl path.line { stroke-width: var(--sw2); }
    .cmap.focus .ed:not(.hl) { opacity: .07; }
    .cmap.focus .nd:not(.hl) { opacity: .25; }
    .cmap.focus .lg { opacity: .55; }
    .cmap.focus .ed:not(.hl) .elabel { opacity: 0; }
    .cmap.searching .nd:not(.m) { opacity: .25; }
    .cmap.searching .ed { opacity: .15; }
    .cmap .hid { display: none; }

    .czoom { position: absolute; top: 8px; right: 8px; display: flex; flex-direction: column; gap: 4px; }
    .czoom button { width: 32px; height: 32px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); cursor: pointer; font-size: 17px; line-height: 1; padding: 0; opacity: .94; }
    .czoom button.fit { font-size: 11px; }
    .czoom button:hover { background: var(--code-bg); }
    .cbar { position: absolute; left: 8px; right: 52px; bottom: 8px; padding: 7px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); text-align: left; font-size: 13px; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,.18); }
    .cbar[hidden] { display: none; }
    @media (min-width: 900px) { .cbar { display: none !important; } }
    .chint { position: absolute; left: 10px; bottom: 6px; font-size: 11px; color: var(--muted); pointer-events: none; background: var(--card); padding: 1px 6px; border-radius: 6px; opacity: .9; }
    .ctip { position: absolute; z-index: 5; pointer-events: none; background: var(--text); color: var(--card); font-size: 12px;
            padding: 6px 9px; border-radius: 6px; max-width: 250px; line-height: 1.35; display: none; }
    .ctip b { font-weight: 600; } .ctip .tm { opacity: .8; font-size: 11px; display: block; margin-top: 2px; }

    .cpanel { padding: 12px 14px 14px; font-size: 13px; line-height: 1.45; overflow: auto; border-top: 1px solid var(--border); min-height: 120px; }
    .cpanel h5 { font-size: 15px; line-height: 1.25; margin: 0 0 3px; }
    .cpanel .pmeta { font-size: 12px; color: var(--muted); margin-bottom: 8px; display: flex; flex-wrap: wrap; gap: 2px 10px; align-items: center; }
    .cpanel .pmeta svg { width: 14px; height: 14px; vertical-align: -2px; fill: var(--dx); }
    .cpanel p { margin: 0 0 8px; }
    .cpanel .pl { font-weight: 600; color: var(--text); }
    .cpanel h6 { font-size: 12px; font-weight: 600; margin: 12px 0 4px; color: var(--ink2); }
    .cpanel ul { list-style: none; margin: 0; padding: 0; }
    .cpanel li { margin: 0 0 2px; }
    .cpanel .pitem { display: block; width: 100%; text-align: left; background: none; border: 0; border-radius: 6px; padding: 4px 6px; cursor: pointer; line-height: 1.35; }
    .cpanel .pitem:hover { background: var(--code-bg); }
    .cpanel .pitem .mech { color: var(--muted); display: block; font-size: 12px; }
    .cpanel .pitem .fx { display: inline-block; width: 1.1em; text-align: center; font-weight: 700; }
    .cpanel .fx.up { color: var(--up); } .cpanel .fx.down { color: var(--down); } .cpanel .fx.mixed { color: var(--mix); } .cpanel .fx.con { color: var(--con); }
    .cpanel .mkt { display: inline-block; background: var(--code-bg); border-radius: 6px; padding: 2px 8px; font-variant-numeric: tabular-nums; }
    .cpanel .mkt.up { color: var(--up); } .cpanel .mkt.down { color: var(--down); }
    .cpanel .thr { color: var(--ink2); }
    .cpanel .src a { color: var(--accent); word-break: break-word; }
    .cpanel .empty { color: var(--muted); }
    .cpanel .clear { float: right; font-size: 12px; background: none; border: 0; color: var(--accent); cursor: pointer; text-decoration: underline; padding: 0; }

    .legend { display: flex; flex-wrap: wrap; gap: 5px 14px; font-size: 12px; color: var(--muted); padding: 8px 12px 10px; border-top: 1px solid var(--border); align-items: center; }
    .legend > span { display: inline-flex; align-items: center; gap: 5px; }
    .legend svg { flex: none; }
    .legend .shp { width: 16px; height: 16px; fill: var(--muted); }
    .legend .lk { width: 34px; height: 12px; overflow: visible; }
    .legend .lk path { fill: none; stroke: var(--ink2); }
    .legend .lk .up { stroke: var(--up); } .legend .lk .down { stroke: var(--down); } .legend .lk .mixed { stroke: var(--mix); } .legend .lk .con { stroke: var(--con); }
    .legend .lk .hd { fill: var(--ink2); stroke: none; } .legend .lk .hd.up { fill: var(--up); } .legend .lk .hd.down { fill: var(--down); } .legend .lk .hd.mixed { fill: var(--mix); }
    .legend .sep { flex-basis: 100%; height: 0; }
    .legend b { color: var(--text); font-weight: 600; }

"""

CSS += """
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
  "use strict";
  var CR = { low: 1, medium: 2, high: 3 };
  function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }
  function safeUrl(u) { u = String(u == null ? "" : u).trim(); return /^https?:\/\/[^\s]+$/i.test(u) ? u : null; }
  function conf(e) { return CR[e.conf] || 2; }
  var FX = { up: ["▲", "up", "raises"], down: ["▼", "down", "lowers"], mixed: ["±", "mixed", "mixed effect"], none: ["→", "", "leads to"] };

  function init(box) {
    var data;
    try { data = JSON.parse(box.querySelector("script.cdata").textContent); } catch (x) { return; }
    var view = box.querySelector(".cview"), svg = view.querySelector("svg"), tip = box.querySelector(".ctip"), panel = box.querySelector(".cpanel");
    var N = data.nodes || {}, E = data.edges || [], W = data.W, H = data.H, hub = data.hub;
    var nodeEl = {}, edgeEl = [], laneEl = {}, outE = {}, inE = {};
    [].forEach.call(svg.querySelectorAll(".nd"), function (n) { nodeEl[n.getAttribute("data-id")] = n; });
    [].forEach.call(svg.querySelectorAll(".ed"), function (g) { edgeEl[+g.getAttribute("data-i")] = g; });
    [].forEach.call(svg.querySelectorAll(".lg"), function (g) { laneEl[g.getAttribute("data-d")] = g; });
    Object.keys(N).forEach(function (k) { outE[k] = []; inE[k] = []; });
    E.forEach(function (e, i) { if (outE[e.f]) outE[e.f].push(i); if (inE[e.t]) inE[e.t].push(i); });

    var F = { hideDom: {}, region: "", conf: 1, order: 3, status: "", q: "" };
    var sel = null, chain = null, tipTimer = null;

    /* ---------- viewport (viewBox zoom + pan) ---------- */
    var vb = { x: 0, y: 0, w: W, h: H }, MARGIN = 22;
    function box2() { var r = view.getBoundingClientRect(); return { w: Math.max(r.width, 50), h: Math.max(r.height, 50), l: r.left, t: r.top }; }
    function fitW() { var r = box2(); return Math.max(W + 2 * MARGIN, (H + 2 * MARGIN) * r.w / r.h); }
    function setVB() {
      var r = box2(); vb.h = vb.w * r.h / r.w;
      svg.setAttribute("viewBox", vb.x.toFixed(1) + " " + vb.y.toFixed(1) + " " + vb.w.toFixed(1) + " " + vb.h.toFixed(1));
      svg.classList.toggle("zl", r.w / vb.w >= 1.05);
    }
    function clampVB() {
      var m = 60, lo = -MARGIN - m, hi = W + MARGIN + m;
      vb.x = Math.min(Math.max(vb.x, lo - vb.w * 0.5), hi - vb.w * 0.5);
      vb.y = Math.min(Math.max(vb.y, -MARGIN - m - vb.h * 0.5), H + MARGIN + m - vb.h * 0.5);
    }
    function fit() {
      var r = box2(), w = fitW();
      if (r.w < 560) { w = r.w / 0.8; var c = N[hub] || { x: W / 2, y: H / 2 }; vb.w = w; vb.h = w * r.h / r.w; vb.x = c.x - w / 2; vb.y = c.y - vb.h / 2.4; clampVB(); }
      else { vb.w = w; vb.h = w * r.h / r.w; vb.x = W / 2 - w / 2; vb.y = H / 2 - vb.h / 2; }
      setVB();
    }
    function fitAll() { var r = box2(), w = fitW(); vb.w = w; vb.h = w * r.h / r.w; vb.x = W / 2 - w / 2; vb.y = H / 2 - vb.h / 2; setVB(); }
    function zoomAt(k, cx, cy) { /* cx, cy in client px, k > 1 zooms in */
      var r = box2(), minW = fitW() / 9, maxW = fitW() * 1.25;
      var nw = Math.min(maxW, Math.max(minW, vb.w / k)); k = vb.w / nw;
      var px = (cx - r.l) / r.w, py = (cy - r.t) / r.h, wx = vb.x + px * vb.w, wy = vb.y + py * vb.h;
      vb.w = nw; vb.h = nw * r.h / r.w; vb.x = wx - px * vb.w; vb.y = wy - py * vb.h; clampVB(); setVB();
    }
    function zoomCenter(k) { var r = box2(); zoomAt(k, r.l + r.w / 2, r.t + r.h / 2); }
    function panBy(fx, fy) { vb.x += fx * vb.w; vb.y += fy * vb.h; clampVB(); setVB(); }
    function centerOn(id) { var n = N[id]; if (!n) return; vb.x = n.x - vb.w / 2; vb.y = n.y - vb.h / 2; clampVB(); setVB(); }
    function ensureVisible(id) {
      var n = N[id]; if (!n) return; var m = 50;
      if (n.x < vb.x + m || n.x > vb.x + vb.w - m || n.y < vb.y + m || n.y > vb.y + vb.h - m - 20) centerOn(id);
    }

    var ptrs = {}, drag = null, pinch = null, moved = false, suppress = false;
    view.addEventListener("pointerdown", function (ev) {
      if (ev.button > 0) return;
      box.classList.add("act");
      ptrs[ev.pointerId] = { x: ev.clientX, y: ev.clientY };
      var ids = Object.keys(ptrs);
      moved = false;
      if (ids.length === 1) drag = { x: ev.clientX, y: ev.clientY, vx: vb.x, vy: vb.y, id: ev.pointerId };
      else if (ids.length === 2) {
        var a = ptrs[ids[0]], b = ptrs[ids[1]];
        pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, w: vb.w, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2 };
        drag = null; moved = true;
      }
    });
    view.addEventListener("pointermove", function (ev) {
      var p = ptrs[ev.pointerId]; if (!p) return;
      p.x = ev.clientX; p.y = ev.clientY;
      var r = box2();
      if (pinch) {
        var ids = Object.keys(ptrs); if (ids.length < 2) return;
        var a = ptrs[ids[0]], b = ptrs[ids[1]], d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
        var want = pinch.w * pinch.d / d; zoomAt(vb.w / want, (a.x + b.x) / 2, (a.y + b.y) / 2);
      } else if (drag && drag.id === ev.pointerId) {
        var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
        if (!moved && Math.hypot(dx, dy) > 4) {
          moved = true; view.classList.add("drag"); hideTip();
          try { view.setPointerCapture(ev.pointerId); } catch (x) {}
        }
        if (moved) { vb.x = drag.vx - dx * vb.w / r.w; vb.y = drag.vy - dy * vb.h / r.h; clampVB(); setVB(); }
      }
    });
    function endPtr(ev) {
      if (!ptrs[ev.pointerId]) return;
      delete ptrs[ev.pointerId];
      if (Object.keys(ptrs).length < 2) pinch = null;
      if (!Object.keys(ptrs).length) { drag = null; view.classList.remove("drag"); if (moved) { suppress = true; setTimeout(function () { suppress = false; }, 0); } }
    }
    view.addEventListener("pointerup", endPtr); view.addEventListener("pointercancel", endPtr);
    view.addEventListener("click", function (ev) { if (suppress) { ev.stopPropagation(); ev.preventDefault(); } }, true);
    view.addEventListener("mouseleave", function () { box.classList.remove("act"); });
    view.addEventListener("wheel", function (ev) {
      if (!(ev.ctrlKey || ev.metaKey || box.classList.contains("act"))) return;
      ev.preventDefault();
      var dy = ev.deltaMode === 1 ? ev.deltaY * 16 : ev.deltaY;
      dy = Math.max(-120, Math.min(120, dy));
      zoomAt(Math.exp(-dy * (ev.ctrlKey ? 0.008 : 0.0016)), ev.clientX, ev.clientY);
    }, { passive: false });
    [].forEach.call(box.querySelectorAll(".czoom button"), function (b) {
      b.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
      b.addEventListener("click", function () {
        var a = b.getAttribute("data-act");
        if (a === "in") zoomCenter(1.4); else if (a === "out") zoomCenter(1 / 1.4); else fitAll();
      });
    });
    if (window.ResizeObserver) { var lastW = 0; new ResizeObserver(function () { var w = view.clientWidth; if (w && w !== lastW) { lastW = w; if (!lastW) return; setVB(); } }).observe(view); }
    else window.addEventListener("resize", setVB);

    /* ---------- tooltip ---------- */
    function showTip(build, ev) {
      tip.textContent = ""; build(tip); tip.style.display = "block";
      var r = view.getBoundingClientRect(), x = ev.clientX - r.left + 12, y = ev.clientY - r.top + 14, w = tip.offsetWidth, h = tip.offsetHeight;
      if (x + w > r.width - 4) x = Math.max(4, r.width - w - 4);
      if (y + h > r.height - 4) y = Math.max(4, ev.clientY - r.top - h - 10);
      tip.style.left = x + "px"; tip.style.top = y + "px";
    }
    function hideTip() { tip.style.display = "none"; clearTimeout(tipTimer); }
    function edgeTip(i) {
      var e = E[i];
      return function (t) {
        var b = el("b", "", (N[e.f] ? N[e.f].l : "?") + " → " + (N[e.t] ? N[e.t].l : "?")); t.appendChild(b);
        t.appendChild(document.createElement("br")); t.appendChild(document.createTextNode(e.label || "(no mechanism given)"));
        var bits = [FX[e.effect] ? FX[e.effect][2] : "", e.kind === "causal" ? "" : e.kind, "strength " + e.s + "/3", (e.conf || "?") + " confidence", e.status || "reported", e.lag ? "lag: " + e.lag : "", e.order > 1 ? "step " + e.order : ""];
        t.appendChild(el("span", "tm", bits.filter(Boolean).join(" · ")));
        if (e.why && e.why !== e.label) t.appendChild(el("span", "tm", e.why));
      };
    }
    edgeEl.forEach(function (g, i) {
      if (!g) return;
      var hit = g.querySelector(".hit");
      hit.addEventListener("mousemove", function (ev) { g.classList.add("hv"); showTip(edgeTip(i), ev); });
      hit.addEventListener("mouseleave", function () { g.classList.remove("hv"); hideTip(); });
      hit.addEventListener("click", function (ev) { ev.stopPropagation(); g.classList.add("hv"); showTip(edgeTip(i), ev); clearTimeout(tipTimer); tipTimer = setTimeout(function () { hideTip(); g.classList.remove("hv"); }, 3500); });
    });

    /* ---------- filtering ---------- */
    function nodeOk(id) { var n = N[id]; return n && !F.hideDom[n.d] && (!F.region || n.r === F.region); }
    function edgeOk(i) {
      var e = E[i];
      return nodeOk(e.f) && nodeOk(e.t) && conf(e) >= F.conf && (e.order || 1) <= F.order && (!F.status || (e.status || "reported") === F.status);
    }
    function chains(id) {
      var res = { nodes: {}, edges: {}, down: [], up: [] }; res.nodes[id] = 0;
      [["down", outE, "t"], ["up", inE, "f"]].forEach(function (dir) {
        var seen = {}; seen[id] = true; var front = [{ id: id, path: [id] }];
        for (var d = 1; d <= 3; d++) {
          var next = [];
          front.forEach(function (cur) {
            dir[1][cur.id].forEach(function (i) {
              var e = E[i]; if (e.kind === "contradicts" || !edgeOk(i)) return;
              var o = e[dir[2]]; res.edges[i] = true;
              if (seen[o]) return; seen[o] = true; res.nodes[o] = d;
              var p = cur.path.concat([o]); next.push({ id: o, path: p }); res[dir[0]].push({ id: o, d: d, path: p, edge: i });
            });
          });
          front = next;
        }
      });
      outE[id].concat(inE[id]).forEach(function (i) { if (E[i].kind === "contradicts" && edgeOk(i)) { res.edges[i] = true; res.nodes[E[i].f] = res.nodes[E[i].f] || 1; res.nodes[E[i].t] = res.nodes[E[i].t] || 1; } });
      return res;
    }
    function refresh() {
      var nv = 0, ev2 = 0, total = Object.keys(N).length, domUsed = {};
      Object.keys(N).forEach(function (id) {
        var ok = nodeOk(id); nodeEl[id].classList.toggle("hid", !ok);
        if (ok) { nv++; }
      });
      edgeEl.forEach(function (g, i) { if (!g) return; var ok = edgeOk(i); g.classList.toggle("hid", !ok); if (ok) ev2++; });
      Object.keys(laneEl).forEach(function (d) { laneEl[d].classList.toggle("hid", !!F.hideDom[d]); });
      if (sel && !nodeOk(sel)) sel = null;
      chain = sel ? chains(sel) : null;
      box.classList.toggle("focus", !!chain);
      Object.keys(nodeEl).forEach(function (id) { nodeEl[id].classList.toggle("hl", !!(chain && chain.nodes[id] != null)); nodeEl[id].classList.toggle("sel", id === sel); });
      edgeEl.forEach(function (g, i) { if (g) g.classList.toggle("hl", !!(chain && chain.edges[i])); });
      var q = F.q.trim().toLowerCase(), any = false;
      Object.keys(N).forEach(function (id) {
        var n = N[id], m = !!q && (n.l + " " + n.det + " " + n.dn + " " + n.r + " " + n.t).toLowerCase().indexOf(q) >= 0 && nodeOk(id);
        nodeEl[id].classList.toggle("m", m); if (m) any = true;
      });
      box.classList.toggle("searching", !!q && any && !chain);
      var cnt = box.querySelector(".ccount");
      if (cnt) cnt.textContent = nv + " of " + total + " nodes · " + ev2 + " of " + E.length + " links" + (q ? (any ? "" : " · no match") : "");
      var act = F.region || F.conf > 1 || F.order < 3 || F.status || q || Object.keys(F.hideDom).some(function (k) { return F.hideDom[k]; });
      var cl = box.querySelector(".cclear"); if (cl) cl.hidden = !act;
      renderPanel();
      var bar = box.querySelector(".cbar");
      if (bar) { bar.hidden = !sel; if (sel) bar.textContent = N[sel].l + " — details below ▾"; }
    }

    /* ---------- side panel ---------- */
    function fxGlyph(e) { if (e.kind === "contradicts") return ["≠", "con"]; var f = FX[e.effect] || FX.none; return [f[0], f[1]]; }
    function item(otherId, i, arrow) {
      var e = E[i], o = N[otherId], b = el("button", "pitem"); b.type = "button";
      var g = fxGlyph(e), fx = el("span", "fx " + g[1], g[0]); fx.setAttribute("aria-hidden", "true");
      b.appendChild(fx); b.appendChild(document.createTextNode(" " + (o ? o.l : "?")));
      var meta = [e.kind !== "causal" ? e.kind : "", (e.conf || "?") + " conf.", e.status || "reported", e.lag || ""].filter(Boolean).join(" · ");
      b.appendChild(el("span", "mech", (e.label || "no mechanism given") + "  (" + meta + ")"));
      b.setAttribute("aria-label", (arrow || "") + " " + (o ? o.l : "") + ": " + (e.label || "") + ", " + (FX[e.effect] ? FX[e.effect][2] : "") + ", " + meta);
      b.addEventListener("click", function () { choose(otherId, true); });
      var li = el("li"); li.appendChild(b); return li;
    }
    function section(title, arr) {
      if (!arr.length) return null;
      var d = document.createDocumentFragment(); d.appendChild(el("h6", "", title));
      var ul = el("ul"); arr.forEach(function (x) { ul.appendChild(x); }); d.appendChild(ul); return d;
    }
    function renderPanel() {
      panel.textContent = "";
      if (!sel) {
        panel.appendChild(el("p", "empty", "Select a node to see what drives it, what it drives, and its multi-step chains. Drag to pan; wheel, pinch or +/− to zoom; arrow keys move between nodes."));
        var vis = Object.keys(N).filter(nodeOk).sort(function (a, b) { return (outE[b].length + inE[b].length) - (outE[a].length + inE[a].length); }).slice(0, 6);
        if (vis.length) {
          panel.appendChild(el("h6", "", "Most connected"));
          var ul = el("ul"); vis.forEach(function (id) {
            var b = el("button", "pitem", N[id].l); b.type = "button"; b.appendChild(el("span", "mech", N[id].dn + (N[id].r ? " · " + N[id].r : "")));
            b.addEventListener("click", function () { choose(id, true); }); var li = el("li"); li.appendChild(b); ul.appendChild(li);
          }); panel.appendChild(ul);
        }
        return;
      }
      var n = N[sel], c = el("button", "clear", "Clear"); c.type = "button"; c.addEventListener("click", function () { choose(null); }); panel.appendChild(c);
      panel.appendChild(el("h5", "", n.l));
      var meta = el("div", "pmeta"); meta.appendChild(el("span", "", data.typeLabel[n.t] || n.t));
      if (n.dn) meta.appendChild(el("span", "", n.dn)); if (n.r) meta.appendChild(el("span", "", n.r));
      if (n.dir && n.dir !== "none") meta.appendChild(el("span", "", { up: "▲ rising", down: "▼ falling", flat: "▬ steady" }[n.dir] || ""));
      panel.appendChild(meta);
      if (n.mkt) {
        var m = n.mkt, p = el("p"), s = el("span", "mkt " + (m.cp > 0 ? "up" : m.cp < 0 ? "down" : ""), (m.label || n.l) + (m.price ? " " + m.price : "") + (m.change ? " " + m.change : (m.cp != null ? " " + (m.cp > 0 ? "+" : "") + m.cp + "%" : "")));
        p.appendChild(s); panel.appendChild(p);
      }
      if (n.thr) panel.appendChild(el("p", "thr", "Running " + n.thr.days + " day" + (n.thr.days === 1 ? "" : "s") + ", " + n.thr.stories + " " + (n.thr.stories === 1 ? "story" : "stories") + (n.thr.first ? " (since " + n.thr.first + ")" : "")));
      if (n.det) panel.appendChild(el("p", "", n.det));
      if (n.why) { var w = el("p"); w.appendChild(el("span", "pl", "Why it matters: ")); w.appendChild(document.createTextNode(n.why)); panel.appendChild(w); }
      var drivers = inE[sel].filter(edgeOk).map(function (i) { return item(E[i].f, i, "driven by"); });
      var driven = outE[sel].filter(edgeOk).map(function (i) { return item(E[i].t, i, "drives"); });
      var s1 = section("Driven by (" + drivers.length + ")", drivers), s2 = section("Drives (" + driven.length + ")", driven);
      if (s1) panel.appendChild(s1); if (s2) panel.appendChild(s2);
      if (!s1 && !s2) panel.appendChild(el("p", "empty", "No visible links with the current filters."));
      [["Further downstream (to 3 steps)", chain.down], ["Further upstream (to 3 steps)", chain.up]].forEach(function (g) {
        var far = g[1].filter(function (x) { return x.d > 1; }).slice(0, 8);
        var items = far.map(function (x) {
          var path = g[0].indexOf("down") > 0 ? x.path.slice(1) : x.path.slice(1).reverse();
          var b = el("button", "pitem", path.map(function (id) { return N[id].l; }).join(" → ")); b.type = "button";
          b.addEventListener("click", function () { choose(x.id, true); }); var li = el("li"); li.appendChild(b); return li;
        });
        var s = section(g[0], items); if (s) panel.appendChild(s);
      });
      var good = (n.src || []).map(function (s) { return [s[0], safeUrl(s[1])]; }).filter(function (s) { return s[1]; });
      if (good.length) {
        panel.appendChild(el("h6", "", "Sources")); var ul = el("ul", "src");
        good.slice(0, 6).forEach(function (s) { var li = el("li"), a = el("a", "", s[0] || s[1]); a.href = s[1]; a.target = "_blank"; a.rel = "noopener noreferrer"; li.appendChild(a); ul.appendChild(li); });
        panel.appendChild(ul);
      }
    }

    function choose(id, pan) {
      sel = (id && id !== sel) || (id && pan) ? id : null;
      if (id == null) sel = null;
      hideTip(); refresh();
      if (sel && pan) { ensureVisible(sel); try { nodeEl[sel].focus({ preventScroll: true }); } catch (x) {} }
      if (sel && window.matchMedia && window.matchMedia("(max-width: 899px)").matches && pan === "scroll") panel.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }

    Object.keys(nodeEl).forEach(function (id) {
      var n = nodeEl[id];
      n.addEventListener("click", function (ev) { ev.stopPropagation(); choose(sel === id ? null : id, false); });
      n.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ev.stopPropagation(); choose(sel === id ? null : id, false); }
        else if (ev.key.indexOf("Arrow") === 0) { ev.preventDefault(); ev.stopPropagation(); spatial(id, ev.key); }
      });
      n.addEventListener("focus", function () { ensureVisible(id); });
      n.addEventListener("mousemove", function (ev) { if (drag && moved) return; showTip(function (t) { t.appendChild(el("b", "", N[id].l)); t.appendChild(el("span", "tm", (data.typeLabel[N[id].t] || "") + (N[id].dn ? " · " + N[id].dn : "") + (N[id].r ? " · " + N[id].r : ""))); }, ev); });
      n.addEventListener("mouseleave", hideTip);
    });
    function spatial(from, key) {
      var a = N[from], best = null, bs = 1e9;
      Object.keys(N).forEach(function (id) {
        if (id === from || !nodeOk(id)) return; var b = N[id], dx = b.x - a.x, dy = b.y - a.y, p, q;
        if (key === "ArrowRight") { p = dx; q = dy; } else if (key === "ArrowLeft") { p = -dx; q = dy; } else if (key === "ArrowDown") { p = dy; q = dx; } else { p = -dy; q = dx; }
        if (p <= 4) return; var s = p + 2.2 * Math.abs(q); if (Math.abs(q) > p * 2.5) s += 400; if (s < bs) { bs = s; best = id; }
      });
      if (best) { ensureVisible(best); nodeEl[best].focus({ preventScroll: true }); }
    }
    svg.addEventListener("click", function (ev) { if (!ev.target.closest || !ev.target.closest(".nd")) { if (sel) choose(null); } });
    view.addEventListener("keydown", function (ev) {
      if (ev.target.closest && ev.target.closest("input,select,textarea")) return;
      var k = ev.key, onNode = ev.target.closest && ev.target.closest(".nd");
      if (k === "+" || k === "=") { ev.preventDefault(); zoomCenter(1.4); }
      else if (k === "-" || k === "_") { ev.preventDefault(); zoomCenter(1 / 1.4); }
      else if (k === "0") { ev.preventDefault(); fitAll(); }
      else if (k === "Escape") { choose(null); hideTip(); }
      else if (!onNode && k.indexOf("Arrow") === 0) {
        ev.preventDefault(); var f = 0.15;
        panBy(k === "ArrowLeft" ? -f : k === "ArrowRight" ? f : 0, k === "ArrowUp" ? -f : k === "ArrowDown" ? f : 0);
      }
    });

    /* ---------- controls ---------- */
    [].forEach.call(box.querySelectorAll(".chip[data-d]"), function (c) {
      c.addEventListener("click", function () {
        var d = c.getAttribute("data-d"), on = c.getAttribute("aria-pressed") === "true";
        c.setAttribute("aria-pressed", on ? "false" : "true"); F.hideDom[d] = on; refresh();
      });
    });
    function sel2(name, key, num) { var s = box.querySelector('select[name="' + name + '"]'); if (s) s.addEventListener("change", function () { F[key] = num ? +s.value : s.value; refresh(); }); }
    sel2("region", "region"); sel2("conf", "conf", true); sel2("order", "order", true); sel2("status", "status");
    var qi = box.querySelector(".csearch");
    if (qi) {
      qi.addEventListener("input", function () { F.q = qi.value; refresh(); });
      qi.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter") { var m = Object.keys(nodeEl).filter(function (id) { return nodeEl[id].classList.contains("m"); })[0]; if (m) { ev.preventDefault(); choose(m, true); } }
        if (ev.key === "Escape") { qi.value = ""; F.q = ""; refresh(); }
      });
    }
    var bar0 = box.querySelector(".cbar");
    if (bar0) { bar0.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); }); bar0.addEventListener("click", function () { panel.scrollIntoView({ block: "start", behavior: "smooth" }); }); }
    var cl = box.querySelector(".cclear");
    if (cl) cl.addEventListener("click", function () {
      F = { hideDom: {}, region: "", conf: 1, order: 3, status: "", q: "" };
      [].forEach.call(box.querySelectorAll(".chip[data-d]"), function (c) { c.setAttribute("aria-pressed", "true"); });
      [].forEach.call(box.querySelectorAll("select"), function (s) { s.selectedIndex = 0; }); if (qi) qi.value = ""; refresh();
    });

    if (window.matchMedia && window.matchMedia("(max-width: 700px)").matches) { var fd = box.querySelector(".cfdet"); if (fd) fd.open = false; }
    fit(); refresh();
    var fdd = box.querySelector(".cfdet"); if (fdd) fdd.addEventListener("toggle", function () { setVB(); });
    if (view.clientWidth === 0) window.addEventListener("load", function () { fit(); });
  }
  [].forEach.call(document.querySelectorAll(".cmap"), function (b) { try { init(b); } catch (e) { if (window.console) console.error(e); } });
})();
"""


# ---------------------------------------------------------------- connection map: clean

MAX_NODES = 50
MAX_EDGES = 90
_CONF = {"low": 1, "medium": 2, "high": 3}
_LANE_OTHER = 7  # domain-less nodes

COL_W = 172
ROW_H = 70
LANE_HEAD = 0
LANE_GAP = 8
PAD_X = 120
PAD_Y = 8


def _int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(float(v))))
    except (TypeError, ValueError):
        return default


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _http(u):
    u = str(u or "").strip()
    return u if re.match(r"^https?://[^\s]+$", u, re.I) and len(u) < 600 else ""


def _clean_graph(graph):
    """Defensive normalisation: any malformed input yields (nodes, edges) lists, never raises."""
    if not isinstance(graph, dict):
        return [], []
    raw_nodes = graph.get("nodes")
    raw_edges = graph.get("edges")
    nodes, seen = [], set()
    for n in raw_nodes if isinstance(raw_nodes, list) else []:
        if not isinstance(n, dict) or n.get("id") in (None, "") or isinstance(n.get("id"), (dict, list)):
            continue
        nid = _txt(n["id"], 60)
        if not nid or nid in seen:
            continue
        seen.add(nid)
        t = _txt(n.get("type"), 30).lower() or "event"
        t = _TYPE_ALIAS.get(t, t)
        dom = _txt(n.get("domain"), 60)
        di = domain_index(dom)
        src = []
        for s in n.get("sources") if isinstance(n.get("sources"), list) else []:
            if isinstance(s, dict):
                link, title = _http(s.get("link") or s.get("url")), _txt(s.get("title"), 160)
            elif isinstance(s, str):
                link, title = _http(s), ""
            else:
                continue
            if link:
                src.append([title or link, link])
        mkt = None
        m = n.get("market")
        if isinstance(m, dict) and (m.get("price") is not None or m.get("change") is not None or m.get("change_pct") is not None):
            price = m.get("price")
            if isinstance(price, (int, float)) and not isinstance(price, bool):
                price = f"{price:,.2f}"
            mkt = {"label": _txt(m.get("label"), 40), "price": _txt(price, 20), "change": _txt(m.get("change"), 20),
                   "cp": _num(m.get("change_pct"))}
        thr = None
        th = n.get("thread")
        if isinstance(th, dict) and (th.get("days") is not None or th.get("stories") is not None):
            thr = {"days": _int(th.get("days"), 0, 9999, 0), "stories": _int(th.get("stories"), 0, 9999, 0),
                   "first": _txt(th.get("first_seen"), 20)}
        d = _txt(n.get("direction"), 8).lower()
        nodes.append({
            "id": nid, "label": _txt(n.get("label"), 80) or nid, "type": t if t in TYPES else "event",
            "dom": dom, "di": di, "region": _txt(n.get("region"), 24), "detail": _txt(n.get("detail"), 500),
            "why": _txt(n.get("why_it_matters"), 400), "dir": d if d in ("up", "down", "flat") else "none",
            "src": src[:6], "mkt": mkt, "thr": thr})
    edges, es = [], set()
    ids = {n["id"] for n in nodes}
    for e in raw_edges if isinstance(raw_edges, list) else []:
        if not isinstance(e, dict):
            continue
        f, t = _txt(e.get("from"), 60), _txt(e.get("to"), 60)
        kind = _txt(e.get("kind"), 20).lower()
        kind = kind if kind in ("causal", "feedback", "contradicts") else "causal"
        if f not in ids or t not in ids or f == t or (f, t, kind) in es:
            continue
        es.add((f, t, kind))
        eff = _txt(e.get("effect"), 8).lower()
        conf = _txt(e.get("confidence"), 8).lower()
        stat = _txt(e.get("status"), 12).lower()
        edges.append({
            "from": f, "to": t, "label": _txt(e.get("label"), 140), "effect": eff if eff in ("up", "down", "mixed") else "none",
            "strength": _int(e.get("strength"), 1, 3, 1), "conf": conf if conf in _CONF else "", "status": "predicted" if stat == "predicted" else "reported",
            "order": _int(e.get("order"), 1, 3, 1), "kind": kind, "why": _txt(e.get("why"), 300), "lag": _txt(e.get("lag"), 40)})
    if len(nodes) > MAX_NODES:
        deg = {}
        for e in edges:
            deg[e["from"]] = deg.get(e["from"], 0) + e["strength"]
            deg[e["to"]] = deg.get(e["to"], 0) + e["strength"]
        keep = {n["id"] for n in sorted(nodes, key=lambda n: -deg.get(n["id"], 0))[:MAX_NODES]}
        nodes = [n for n in nodes if n["id"] in keep]
        edges = [e for e in edges if e["from"] in keep and e["to"] in keep]
    if len(edges) > MAX_EDGES:
        edges = sorted(edges, key=lambda e: -e["strength"])[:MAX_EDGES]
        edges.sort(key=lambda e: 0)  # stable: keep strongest, original relative order is irrelevant after sort
    return nodes, edges


# ---------------------------------------------------------------- layout (deterministic)

def _wrap(label, width=16, lines=2):
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
        out[-1] = out[-1][: width - 1].rstrip() + "\u2026"
    return [o[: width + 2] for o in out] or [""]


# half-width, half-height of each shape (used for ports and label placement)
_EXT = {"event": (16, 16), "country": (20, 20), "company": (15, 15), "sector": (19, 17), "commodity": (22, 13),
        "index": (20, 18), "institution": (18, 18)}


def _shape(kind, x, y, cls):
    hw, hh = _EXT[kind]
    c = f'class="shape {cls}"'
    if kind == "event":
        return f'<circle {c} cx="{x:.1f}" cy="{y:.1f}" r="16"/>'
    if kind == "country":
        return f'<path {c} d="M{x:.1f} {y - 20:.1f}L{x + 20:.1f} {y:.1f}L{x:.1f} {y + 20:.1f}L{x - 20:.1f} {y:.1f}Z"/>'
    if kind == "company":
        return f'<rect {c} x="{x - 15:.1f}" y="{y - 15:.1f}" width="30" height="30" rx="4"/>'
    if kind == "sector":
        pts = " ".join(f"{x + 19 * math.cos(math.radians(a)):.1f},{y + 19 * math.sin(math.radians(a)):.1f}" for a in range(0, 360, 60))
        return f'<polygon {c} points="{pts}"/>'
    if kind == "commodity":
        return f'<rect {c} x="{x - 22:.1f}" y="{y - 13:.1f}" width="44" height="26" rx="13"/>'
    if kind == "index":
        return f'<path {c} d="M{x:.1f} {y - 18:.1f}L{x + 20:.1f} {y + 15:.1f}L{x - 20:.1f} {y + 15:.1f}Z"/>'
    return (f'<path {c} d="M{x:.1f} {y - 18:.1f}L{x + 18:.1f} {y - 6:.1f}L{x + 18:.1f} {y + 15:.1f}'
            f'L{x - 18:.1f} {y + 15:.1f}L{x - 18:.1f} {y - 6:.1f}Z"/>')


def _lane_of(n):
    return n["di"] if n["di"] >= 0 else _LANE_OTHER


SAME_DX = 108   # min x distance between linked nodes in one lane (labels need room)
CROSS_DX = 48   # min x distance between linked nodes in different lanes (link runs diagonally)
MIN_ROW_DX = 106  # nodes of one lane row never closer than this


def _layout(nodes, edges):
    """Swim-lane layout. x = causal depth (weighted longest path, left to right), lane = domain
    (ordered to shorten cross-lane links), rows inside a lane packed so labels never overlap.
    Returns pos {id: (x, y)}, lanes [(lane, top, height)], width, height, xs."""
    ids = [n["id"] for n in nodes]
    order_ix = {i: k for k, i in enumerate(ids)}
    lane = {n["id"]: _lane_of(n) for n in nodes}
    succ = {i: [] for i in ids}
    for e in edges:
        if e["kind"] == "causal":
            succ[e["from"]].append(e["to"])
    indeg = {i: 0 for i in ids}
    deg = {i: 0 for i in ids}
    for e in edges:
        indeg[e["to"]] += 1
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    nbr0 = {i: [] for i in ids}
    for e in edges:
        nbr0[e["from"]].append(e["to"])
        nbr0[e["to"]].append(e["from"])
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

    for i in sorted(ids, key=lambda k: (indeg[k], -deg[k], order_ix[k])):
        if i not in state:
            dfs(i)
    fwd = list(dict.fromkeys(fwd))
    preds = {i: [] for i in ids}
    fsucc = {i: [] for i in ids}
    for u, v in fwd:
        preds[v].append(u)
        fsucc[u].append(v)

    def wdx(a, b):
        return SAME_DX if lane[a] == lane[b] else CROSS_DX

    xs = {}

    def px(i):
        if i not in xs:
            xs[i] = 0 if not preds[i] else max(px(p) + wdx(p, i) for p in preds[i])
        return xs[i]

    for i in ids:
        px(i)
    for _ in range(2):  # pull sources next to their first consumer so links stay short
        for i in ids:
            if not preds[i] and fsucc[i]:
                xs[i] = max(xs[i], min(xs[s] - wdx(i, s) for s in fsucc[i]))

    for _ in range(10):  # slide nodes toward the median of their neighbours inside their legal interval
        for i in sorted(ids, key=lambda k: (xs[k], order_ix[k])):
            lo = max([xs[p] + wdx(p, i) for p in preds[i]] or [-10 ** 6])
            hi = min([xs[q] - wdx(i, q) for q in fsucc[i]] or [10 ** 6])
            nb = sorted(xs[j] for j in nbr0[i])
            if not nb or lo > hi:
                continue
            med = nb[len(nb) // 2] if len(nb) % 2 else (nb[len(nb) // 2 - 1] + nb[len(nb) // 2]) / 2
            xs[i] = min(max(med, lo), hi)
    mn = min(xs.values())
    for i in ids:
        xs[i] -= mn

    used = sorted(set(lane.values()))
    wt = {}
    for e in edges:
        a, b = lane[e["from"]], lane[e["to"]]
        if a != b:
            k = (min(a, b), max(a, b))
            wt[k] = wt.get(k, 0) + e["strength"] + (0.5 if e["kind"] == "causal" else 0)
    best, bestc = tuple(used), None
    if len(used) > 1:
        for perm in itertools.permutations(used):
            pos = {l: k for k, l in enumerate(perm)}
            c = sum(w * abs(pos[a] - pos[b]) for (a, b), w in wt.items())
            if bestc is None or c < bestc - 1e-9:
                best, bestc = perm, c
    lane_pos = {l: k for k, l in enumerate(best)}
    nbr = {i: [] for i in ids}
    for e in edges:
        nbr[e["from"]].append(e["to"])
        nbr[e["to"]].append(e["from"])

    # row packing inside each lane
    row = {}
    nrows = {}
    for l in best:
        members = sorted([i for i in ids if lane[i] == l], key=lambda i: (xs[i], order_ix[i]))
        for pass_no in (1, 2):
            placed = {}
            R = nrows.get(l, 1)
            for i in members:
                if pass_no == 1:
                    scan = range(0, 50)
                else:
                    pref = sum(lane_pos[lane[j]] - lane_pos[l] for j in nbr[i] if lane[j] != l)
                    scan = range(R - 1, -1, -1) if pref > 0 else range(0, R)
                for r in scan:
                    if all(abs(xs[j] - xs[i]) >= MIN_ROW_DX for j in placed.get(r, [])):
                        placed.setdefault(r, []).append(i)
                        row[i] = r
                        break
            if pass_no == 1:
                nrows[l] = (max(row[i] for i in members) + 1) if members else 1
        used_rows = sorted({row[i] for i in members})
        remap = {r: k for k, r in enumerate(used_rows)}
        for i in members:
            row[i] = remap[row[i]]
        nrows[l] = max(1, len(used_rows))
    heights = {l: 26 + (nrows[l] - 1) * ROW_H + 62 for l in best}
    tops, y = {}, PAD_Y
    for l in best:
        tops[l] = y
        y += heights[l] + LANE_GAP
    H = y - LANE_GAP + PAD_Y
    X0 = PAD_X + 40
    pos = {i: (X0 + xs[i], tops[lane[i]] + 26 + row[i] * ROW_H) for i in ids}
    W = X0 + max(xs.values()) + 56
    lanes = [(l, tops[l], heights[l]) for l in best]
    return pos, lanes, W, H, {i: pos[i][0] for i in ids}


# ---------------------------------------------------------------- edge geometry

def _bez(p, t):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p
    u = 1 - t
    return (u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
            u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3)


def _spread(k, n, step):
    return (k - (n - 1) / 2) * min(step, 20 / max(n, 1))


def _route(edges, nodes_by_id, pos, layer):
    """Return per-edge dict(pts=[P0,C1,C2,P3], mode). Modes: fwd (side to side), down (side to top),
    up (top to side), side (loop around the right edge for same-column links), top (arched loop)."""
    ports = {"fo": {}, "fi": {}, "to": {}, "ti": {}}
    modes = []
    for i, e in enumerate(edges):
        a, b = e["from"], e["to"]
        dx = layer[b] - layer[a]
        dy = pos[b][1] - pos[a][1]
        if e["kind"] == "feedback" or dx < -4:
            mode, o, d = "top", "to", "ti"
        elif dx < 30:
            mode, o, d = "side", "fo", "fi"
        elif dx < 118 and dy > 46:
            mode, o, d = "down", "fo", "ti"
        elif dx < 118 and dy < -46:
            mode, o, d = "up", "to", "fi"
        else:
            mode, o, d = "fwd", "fo", "fi"
        modes.append(mode)
        ports[o].setdefault(a, []).append(i)
        ports[d].setdefault(b, []).append(i)
    off = {}
    for tag, dd in ports.items():
        for nid, lst in dd.items():
            far = "to" if tag in ("fo", "to") else "from"
            if tag in ("ti", "to"):
                lst.sort(key=lambda i: (pos[edges[i][far]][0], i))
            else:
                lst.sort(key=lambda i: (pos[edges[i][far]][1], i))
            for k, i in enumerate(lst):
                off[(i, tag)] = _spread(k, len(lst), 7)
    routes = []
    for i, e in enumerate(edges):
        a, b = e["from"], e["to"]
        (x1, y1), (x2, y2) = pos[a], pos[b]
        hw1, hh1 = _EXT[nodes_by_id[a]["type"]]
        hw2, hh2 = _EXT[nodes_by_id[b]["type"]]
        mode = modes[i]
        right = (x1 + hw1 + 1, y1 + off.get((i, "fo"), 0))
        left = (x2 - hw2 - 2, y2 + off.get((i, "fi"), 0))
        if mode == "fwd":
            dx = max(36, (left[0] - right[0]) * 0.45)
            pts = [right, (right[0] + dx, right[1]), (left[0] - dx, left[1]), left]
        elif mode == "down":
            p3 = (x2 + off.get((i, "ti"), 0), y2 - hh2 - 3)
            k = max(30, (p3[1] - right[1]) * 0.45)
            pts = [right, (right[0] + max(24, (p3[0] - right[0]) * 0.7), right[1]), (p3[0], p3[1] - k), p3]
        elif mode == "up":
            p0 = (x1 + off.get((i, "to"), 0), y1 - hh1 - 1)
            k = max(30, (p0[1] - left[1]) * 0.45)
            pts = [p0, (p0[0], p0[1] - k), (left[0] - max(24, (left[0] - p0[0]) * 0.7), left[1]), left]
        elif mode == "side":
            p3 = (x2 + hw2 + 2, y2 + off.get((i, "fi"), 0))
            bulge = 54 + 0.12 * abs(y2 - y1)
            pts = [right, (right[0] + bulge, right[1]), (p3[0] + bulge, p3[1]), p3]
        else:
            p0 = (x1 + off.get((i, "to"), 0), y1 - hh1 - 1)
            p3 = (x2 + off.get((i, "ti"), 0), y2 - hh2 - 3)
            up = min(p0[1], p3[1]) - 44 - min(0.1 * abs(x2 - x1), 30)
            sg = 6 if x2 >= x1 else -6
            pts = [p0, (p0[0] - sg, up), (p3[0] + sg, up), p3]
        routes.append({"pts": pts, "mode": mode})
    return routes


# ---------------------------------------------------------------- legend

_SHP = {
    "event": '<circle cx="8" cy="8" r="6"/>',
    "country": '<path d="M8 1.5 L14.5 8 L8 14.5 L1.5 8Z"/>',
    "company": '<rect x="2.5" y="2.5" width="11" height="11" rx="2"/>',
    "sector": '<polygon points="14.5,8 11.2,13.7 4.8,13.7 1.5,8 4.8,2.3 11.2,2.3"/>',
    "commodity": '<rect x="0.8" y="3.5" width="14.4" height="9" rx="4.5"/>',
    "index": '<path d="M8 1.8 L14.6 13.5 L1.4 13.5Z"/>',
    "institution": '<path d="M8 1.5 L14.2 6 L14.2 13.5 L1.8 13.5 L1.8 6Z"/>',
}


def _legend(types_used, has):
    items = ['<span><b>Shape</b></span>']
    for t in TYPES:
        if t in types_used:
            items.append(f'<span><svg viewBox="0 0 16 16" aria-hidden="true" class="shp">{_SHP[t]}</svg>{TYPE_LABEL[t]}</span>')
    items.append('<span class="sep"></span><span><b>Link</b></span>')

    W24 = 'stroke-width="2.4"'
    WD = 'stroke-width="2.4" stroke-dasharray="6 3.5"'

    def lk(cls, d, extra="", head=True):
        h = '<path class="hd %s" d="M34 6 L27 2.5 L27 9.5Z"/>' % cls if head else ""
        return f'<svg class="lk" viewBox="0 0 36 12" aria-hidden="true"><path class="{cls}" d="{d}" {extra}/>{h}</svg>'

    items.append(f'<span>{lk("up", "M1 6 H27", W24)}<span>raises</span></span>')
    items.append(f'<span>{lk("down", "M1 6 H27", W24)}<span>lowers</span></span>')
    items.append(f'<span>{lk("mixed", "M1 6 H27", W24)}<span>mixed</span></span>')
    items.append(f'<span>{lk("", "M1 6 H27", W24)}<span>reported</span></span>')
    items.append(f'<span>{lk("", "M1 6 H27", WD)}<span>predicted</span></span>')
    if has.get("feedback"):
        items.append(f'<span><svg class="lk" viewBox="0 0 36 12" aria-hidden="true"><path d="M3 11 C3 -4 31 -4 31 8" stroke-width="2"/><path class="hd" d="M31 11 L27.5 6 L34.5 6Z"/></svg><span>feedback loop</span></span>')
    if has.get("contradicts"):
        items.append(f'<span><svg class="lk" viewBox="0 0 36 12" aria-hidden="true"><path class="con" d="M1 6 H28" stroke-width="2.4" stroke-dasharray="1.5 4" stroke-linecap="round"/><path class="con" d="M32 1 V11" stroke-width="2.6" stroke-linecap="round"/></svg><span>contradicts</span></span>')
    items.append('<span>thicker = stronger; fainter = less confident; arrow tip = direction</span>')
    items.append('<span class="sep"></span><span><b>Node</b></span><span>\u25B2 \u25BC rising or falling</span>')
    return '<div class="legend">' + "".join(items) + "</div>"


# ---------------------------------------------------------------- connection map

def _json_embed(obj):
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return (s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def _chip_html(di, name, count):
    cls = f"d{di}" if di in range(7) else "dx"
    return (f'<button type="button" class="chip {cls}" data-d="{di}" aria-pressed="true" title="Show or hide {_e(name)}">'
            f'<i aria-hidden="true"></i>{_e(name)} <span class="muted">{count}</span></button>')


def _select(name, label, options):
    opts = "".join(f'<option value="{_e(v)}">{_e(t)}</option>' for v, t in options)
    return f'<select name="{name}" aria-label="{_e(label)}">{opts}</select>'


def connection_map(graph, note=""):
    """Interactive inline-SVG connection map. Returns '' if the graph is empty or unusable."""
    try:
        return _connection_map(graph, note)
    except Exception:  # never let a malformed LLM graph break the page build
        return ""


def _connection_map(graph, note=""):
    nodes, edges = _clean_graph(graph)
    if len(nodes) < 2:
        return ""
    uid = next(_counter)
    byid = {n["id"]: n for n in nodes}
    pos, lanes, W, H, layer = _layout(nodes, edges)
    routes = _route(edges, byid, pos, layer)

    # label boxes of nodes (for edge-label collision avoidance)
    nbox = []
    for n in nodes:
        x, y = pos[n["id"]]
        hw, hh = _EXT[n["type"]]
        lines = _wrap(n["label"])
        lw = max(len(l) for l in lines) * 6.3 + 6
        nbox.append((x - hw - 3, y - hh - 3, x + hw + 3, y + hh + 3))
        nbox.append((x - lw / 2, y + hh + 2, x + lw / 2, y + hh + 4 + 14 * len(lines)))

    def hit(b, boxes):
        return sum(1 for o in boxes if not (b[2] <= o[0] or b[0] >= o[2] or b[3] <= o[1] or b[1] >= o[3]))

    placed = []
    order = sorted(range(len(edges)), key=lambda i: (-edges[i]["strength"], i))
    lab_pos = {}
    for i in order:
        e = edges[i]
        txt = _wrap(e["label"], 24, 1)[0] if e["label"] else ""
        w = len(txt) * 5.6 + 6
        best_c, best_s = None, None
        for t in (0.5, 0.42, 0.58, 0.34, 0.66, 0.26, 0.74, 0.18, 0.82):
            px, py = _bez(routes[i]["pts"], t)
            chip = (px - 8, py - 8, px + 8, py + 8)
            for side in (-1, 1):
                bb = (px - w / 2, py - 25, px + w / 2, py - 8) if side < 0 else (px - w / 2, py + 9, px + w / 2, py + 26)
                s = hit(bb, nbox) * 3 + hit(chip, nbox) * 3 + hit(bb, placed) * 2 + hit(chip, placed)
                if best_s is None or s < best_s:
                    best_c, best_s = (px, py, side, bb), s
                if s == 0:
                    break
            if best_s == 0:
                break
        px, py, side, bb = best_c
        lab_pos[i] = (px, py, txt, side)
        placed.append(bb)

    defs = ['<defs>']
    for s, size in ((1, 8), (2, 10), (3, 12)):
        for eff in ("up", "down", "mixed", "none"):
            defs.append(f'<marker id="mk{uid}-{eff}{s}" viewBox="0 0 10 10" refX="9.5" refY="5" markerWidth="{size}" markerHeight="{size}" '
                        f'markerUnits="userSpaceOnUse" orient="auto"><path class="mk e-{eff}" d="M0 0.8 L10 5 L0 9.2Z"/></marker>')
        defs.append(f'<marker id="mk{uid}-bar{s}" viewBox="0 0 10 14" refX="5" refY="7" markerWidth="{size * 0.8:.0f}" markerHeight="{size * 1.12:.0f}" '
                    f'markerUnits="userSpaceOnUse" orient="auto"><path class="mk bar" d="M5 1 V13"/></marker>')
    defs.append("</defs>")

    lane_svg = []
    for l, top, h in lanes:
        cls = f"d{l}" if l < 7 else "dx"
        name = DOMAINS[l] if l < 7 else "Other"
        cnt = sum(1 for n in nodes if _lane_of(n) == l)
        ll = _wrap(f"{name}", 15, 3)
        tsp = "".join(f'<tspan x="{PAD_X - 100 + 14}" y="{top + 20 + k * 13:.0f}">{_e(t)}</tspan>' for k, t in enumerate(ll))
        lane_svg.append(
            f'<g class="lg" data-d="{l if l < 7 else -1}"><rect class="lane {cls}" x="{PAD_X - 108}" y="{top}" width="{W - PAD_X + 100}" height="{h}" rx="9"/>'
            f'<circle class="lanedot {cls}" cx="{PAD_X - 100 + 6}" cy="{top + 16}" r="4.5"/>'
            f'<text class="lanelabel">{tsp}</text><text class="lanecount" x="{PAD_X - 100 + 14}" y="{top + 20 + len(ll) * 13:.0f}">{cnt} nodes</text></g>')
    edge_svg = [None] * len(edges)
    has = {}
    for i, e in enumerate(edges):
        r = routes[i]
        p0, c1, c2, p3 = r["pts"]
        d = f"M{p0[0]:.1f} {p0[1]:.1f}C{c1[0]:.1f} {c1[1]:.1f} {c2[0]:.1f} {c2[1]:.1f} {p3[0]:.1f} {p3[1]:.1f}"
        sw = 1.1 + e["strength"] * 0.85
        s = e["strength"]
        eff = e["effect"]
        con = e["kind"] == "contradicts"
        has[e["kind"]] = True
        mk = f"url(#mk{uid}-bar{s})" if con else f"url(#mk{uid}-{eff}{s})"
        px, py, txt, side = lab_pos[i]
        if con:
            glyph = '<text class="glyphtx" y="3.6">\u2260</text>'
        elif eff == "up":
            glyph = '<path class="glyph" d="M0 -3.6 L3.8 2.6 L-3.8 2.6Z"/>'
        elif eff == "down":
            glyph = '<path class="glyph" d="M0 3.6 L3.8 -2.6 L-3.8 -2.6Z"/>'
        elif eff == "mixed":
            glyph = '<text class="glyphtx" y="3.6">\u00B1</text>'
        else:
            glyph = '<path class="glyph" d="M-3.4 -1 H3.4 V1 H-3.4Z"/>'
        eo = {"high": 0.95, "medium": 0.72, "low": 0.5}.get(e["conf"], 0.72)
        cls = (f'ed e-{eff} k-{e["kind"]} st-{e["status"]} c-{e["conf"] or "medium"}' + (" perm" if s >= 3 and txt else ""))
        aria = f'{byid[e["from"]]["label"]} to {byid[e["to"]]["label"]}: {e["label"]}'
        edge_svg[i] = (
            f'<g class="{cls}" data-i="{i}" style="--eo:{eo};--sw2:{sw + 1:.1f}px">'
            f'<path class="line" d="{d}" stroke-width="{sw:.2f}" marker-end="{mk}"><title>{_e(aria)}</title></path>'
            f'<path class="hit" d="{d}"/>'
            f'<g transform="translate({px:.1f} {py:.1f})"><circle class="chip2" r="7"/>{glyph}'
            f'<text class="elabel" y="{-11 if side < 0 else 19}">{_e(txt)}</text></g></g>')
    # stronger edges drawn last (on top) but keep their data-i
    edge_order = sorted(range(len(edges)), key=lambda i: (edges[i]["strength"], i))

    node_svg, types_used, doms_used, jn = [], set(), {}, {}
    deg = {n["id"]: 0 for n in nodes}
    for e in edges:
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    for n in nodes:
        x, y = pos[n["id"]]
        hw, hh = _EXT[n["type"]]
        l = _lane_of(n)
        cls = f"d{l}" if l < 7 else "dx"
        types_used.add(n["type"])
        doms_used[l] = doms_used.get(l, 0) + 1
        lines = _wrap(n["label"])
        ty0 = y + hh + 14
        tsp = "".join(f'<tspan x="{x:.1f}" y="{ty0 + k * 13.5:.1f}">{_e(t)}</tspan>' for k, t in enumerate(lines))
        dname = DOMAINS[l] if l < 7 else (n["dom"] or "")
        meta = TYPE_LABEL[n["type"]] + (f", {dname}" if dname else "") + (f", {n['region']}" if n["region"] else "")
        db = ""
        if n["dir"] in ("up", "down", "flat"):
            bx, by = x + hw * 0.78, y - hh * 0.8
            tri = {"up": f"M{bx:.1f} {by - 5.5:.1f}L{bx + 5:.1f} {by + 4:.1f}L{bx - 5:.1f} {by + 4:.1f}Z",
                   "down": f"M{bx:.1f} {by + 5.5:.1f}L{bx + 5:.1f} {by - 4:.1f}L{bx - 5:.1f} {by - 4:.1f}Z",
                   "flat": f"M{bx - 5:.1f} {by - 2.2:.1f}H{bx + 5:.1f}V{by + 2.2:.1f}H{bx - 5:.1f}Z"}[n["dir"]]
            db = f'<path class="dirb {n["dir"]}" d="{tri}"/>'
        ring = _ring(n["type"], x, y)
        node_svg.append(
            f'<g class="nd" tabindex="0" role="button" data-id="{_e(n["id"])}" aria-label="{_e(n["label"])}, {_e(meta)}. Show connections.">'
            f'<rect class="tap" x="{x - 62:.1f}" y="{y - hh - 6:.1f}" width="124" height="{2 * hh + 12 + 14 * len(lines) + 6}"/>'
            f'{_shape(n["type"], x, y, cls)}{ring}{db}<text class="nlabel">{tsp}</text></g>')
        jn[n["id"]] = {"l": n["label"], "t": n["type"], "d": l if l < 7 else -1, "dn": dname, "r": n["region"], "x": round(x, 1), "y": round(y, 1),
                       "det": n["detail"], "why": n["why"], "dir": n["dir"], "src": n["src"]}
        if n["mkt"]:
            jn[n["id"]]["mkt"] = n["mkt"]
        if n["thr"]:
            jn[n["id"]]["thr"] = n["thr"]
    hub = max(nodes, key=lambda n: (deg[n["id"]], -nodes.index(n)))["id"]
    je = [{"f": e["from"], "t": e["to"], "label": e["label"], "effect": e["effect"], "s": e["strength"], "conf": e["conf"],
           "status": e["status"], "order": e["order"], "kind": e["kind"], "why": e["why"], "lag": e["lag"]} for e in edges]
    payload = {"nodes": jn, "edges": je, "W": round(W), "H": round(H), "hub": hub, "typeLabel": TYPE_LABEL}

    svg = (f'<svg viewBox="0 0 {W:.0f} {H:.0f}" role="group" preserveAspectRatio="xMidYMid meet" '
           f'aria-label="Connection map: {len(nodes)} nodes, {len(edges)} links, grouped by domain, flowing left to right">'
           f'{"".join(defs)}{"".join(lane_svg)}<g class="edges">{"".join(edge_svg[i] for i in edge_order)}</g>{"".join(node_svg)}</svg>')

    # controls
    chips = "".join(_chip_html(l if l < 7 else -1, DOMAINS[l] if l < 7 else "Other", c) for l, c in sorted(doms_used.items())) if len(doms_used) > 1 else ""
    regions = sorted({n["region"] for n in nodes if n["region"]})
    ctrl = []
    if chips:
        ctrl.append(f'<div class="grp" role="group" aria-label="Filter by domain">{chips}</div>')
    if len(regions) > 1:
        ctrl.append(_select("region", "Region", [("", "All regions")] + [(r, r) for r in regions]))
    if len({e["conf"] or "medium" for e in edges}) > 1:
        ctrl.append(_select("conf", "Minimum confidence", [("1", "Any confidence"), ("2", "Medium + high"), ("3", "High only")]))
    if any(e["order"] > 1 for e in edges):
        ctrl.append(_select("order", "Chain depth", [("3", "All chain steps"), ("2", "Up to 2 steps"), ("1", "Direct links only")]))
    if len({e["status"] for e in edges}) > 1:
        ctrl.append(_select("status", "Status", [("", "Reported + predicted"), ("reported", "Reported only"), ("predicted", "Predicted only")]))
    if len(nodes) >= 6:
        ctrl.append('<input class="csearch" type="search" placeholder="Find a node" aria-label="Search nodes" autocomplete="off">')
    ctrl.append('<button type="button" class="clink cclear" hidden>Clear filters</button>')
    filters = (f'<details class="cfdet" open><summary>Filters and search<span class="ccount" aria-live="polite"></span></summary>'
               f'<div class="cfilters">{"".join(ctrl)}</div></details>')
    zoom = ('<div class="czoom"><button type="button" data-act="in" aria-label="Zoom in" title="Zoom in (+)">+</button>'
            '<button type="button" data-act="out" aria-label="Zoom out" title="Zoom out (\u2212)">\u2212</button>'
            '<button type="button" data-act="fit" class="fit" aria-label="Fit whole map" title="Fit whole map (0)">Fit</button></div>')
    note_html = f'<p class="vnote">{_e(note)}</p>' if note else ""
    return (f'<h4>Connection map</h4>{note_html}<div class="cmapwrap">'
            f'<div class="cmap" id="cmap{uid}">{filters}<div class="cbody">'
            f'<div class="cview" tabindex="0" aria-label="Map area. Plus and minus zoom, zero fits, arrow keys pan, Tab moves between nodes.">{svg}{zoom}'
            f'<div class="chint">Drag to pan, scroll or pinch to zoom</div><button type="button" class="cbar" hidden></button><div class="ctip" aria-hidden="true"></div></div>'
            f'<div class="cpanel" aria-live="polite"></div></div>{_legend(types_used, has)}'
            f'<script type="application/json" class="cdata">{_json_embed(payload)}</script></div></div>')


def _ring(kind, x, y):
    """Outline following the node shape, used for focus / selection / search match."""
    hw, hh = _EXT[kind]
    if kind == "event":
        return f'<circle class="ring" cx="{x:.1f}" cy="{y:.1f}" r="20"/>'
    if kind == "country":
        return f'<path class="ring" d="M{x:.1f} {y - 25:.1f}L{x + 25:.1f} {y:.1f}L{x:.1f} {y + 25:.1f}L{x - 25:.1f} {y:.1f}Z"/>'
    if kind == "commodity":
        return f'<rect class="ring" x="{x - 27:.1f}" y="{y - 18:.1f}" width="54" height="36" rx="18"/>'
    return f'<rect class="ring" x="{x - hw - 5:.1f}" y="{y - hh - 5:.1f}" width="{2 * hw + 10}" height="{2 * hh + 10}" rx="8"/>'



# ---------------------------------------------------------------- scenario tree

_LK = {"low": 1, "medium": 2, "high": 3}


def scenario_tree(scenarios):
    try:
        return _scenario_tree(scenarios)
    except Exception:
        return ""


def _scenario_tree(scenarios):
    if not isinstance(scenarios, list):
        return ""
    scs = [s for s in scenarios if isinstance(s, dict)]
    if not scs:
        return ""
    out = ['<h4>Scenario tree (next 1-2 weeks)</h4><div class="stree"><div class="sroot">Today &rarr; possible branches</div>']
    for sc in scs:
        lk = _txt(sc.get("likelihood"), 12).lower()
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
    try:
        return _causal_flows(chains)
    except Exception:
        return ""


def _causal_flows(chains):
    if not isinstance(chains, list):
        return ""
    cs = [c for c in chains if isinstance(c, dict)]
    if not cs:
        return ""
    out = ['<h4>Causal chains</h4><div class="flows">']
    for c in cs:
        raw = c.get("chain")
        steps = [s for s in (raw if isinstance(raw, list) else []) if s and not isinstance(s, (dict, list))]
        rawd = c.get("domains")
        doms = [d for d in (rawd if isinstance(rawd, list) else []) if d and isinstance(d, str)]
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
