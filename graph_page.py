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
    .viz { --d0:#6250d6; --d1:#eb6834; --d2:#1baf7a; --d3:#eda100; --d4:#e87ba4; --d5:#008300; --d6:#2a78d6; --dx:#64748b;
           --ln:#64748b; --halo:var(--card); --soft:rgba(100,116,139,.12); --soft2:rgba(100,116,139,.2);
           --up:#2a78d6; --down:#e34948; --mix:#9a6700; --con:#fab219; --ink2:#475569; --focus:#2563eb; --lane:.075; }
    @media (prefers-color-scheme: dark) {
      .viz { --d0:#9085e9; --d1:#d95926; --d2:#199e70; --d3:#c98500; --d4:#d55181; --d5:#008300; --d6:#3987e5; --dx:#94a3b8; --ln:#94a3b8;
             --up:#3987e5; --down:#e66767; --mix:#d29922; --con:#fab219; --ink2:#c3c2b7; --focus:#79c0ff; --lane:.12;
             --soft:rgba(148,163,184,.14); --soft2:rgba(148,163,184,.24); }
    }
    .viz h4 { font-size: 14px; margin: 18px 0 6px; }
    .viz .vnote { font-size: 12px; color: var(--muted); margin: 0 0 8px; }

    .cmapwrap { margin: 0; }
    @media (min-width: 900px) {
      .cmapwrap { width: min(1480px, calc(100vw - 40px)); position: relative; left: 50%; transform: translateX(-50%); }
    }
    .cmap { position: relative; border: 1px solid var(--border); border-radius: 10px; background: var(--card); }
    .cmap button, .cmap select, .cmap input { font: inherit; color: var(--text); }
    .cmap :focus-visible { outline: 2px solid var(--focus); outline-offset: 1px; }
    .cmap .muted { color: var(--muted); }

    /* top bar: view switch + search */
    .ctop { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; padding: 10px 12px; border-bottom: 1px solid var(--border); }
    .seg { display: inline-flex; padding: 3px; border-radius: 999px; background: var(--soft); gap: 2px; max-width: 100%; }
    .seg button { font-size: 14px; font-weight: 600; line-height: 1; padding: 8px 15px; border: 0; border-radius: 999px; background: transparent; color: var(--ink2); cursor: pointer; white-space: nowrap; min-height: 34px; }
    .seg button[aria-checked="true"] { background: var(--text); color: var(--card); }
    .seg button:hover:not([aria-checked="true"]) { background: var(--soft2); }
    .csw { position: relative; flex: 1 1 180px; max-width: 340px; margin-left: auto; }
    .cmap .csearch { width: 100%; font-size: 14px; padding: 7px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); min-height: 36px; }
    .sres { position: absolute; z-index: 30; left: 0; right: 0; top: calc(100% + 4px); margin: 0; padding: 4px; list-style: none; background: var(--card);
            border: 1px solid var(--border); border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,.22); max-height: 320px; overflow: auto; min-width: 250px; }
    .sres[hidden] { display: none; }
    .sres li { margin: 0; }
    .sres button { display: flex; width: 100%; text-align: left; gap: 8px; align-items: center; justify-content: space-between; padding: 7px 9px; border: 0; background: none; border-radius: 7px; cursor: pointer; font-size: 14px; line-height: 1.25; }
    .sres button:hover, .sres button.on { background: var(--soft2); }
    .sres .sno { padding: 8px 10px; font-size: 13px; color: var(--muted); }
    .dchip { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: var(--ink2); white-space: nowrap; }
    .dchip::before { content: ""; width: 9px; height: 9px; border-radius: 50%; background: var(--dx); flex: none; }
    .dchip.d0::before { background: var(--d0); } .dchip.d1::before { background: var(--d1); } .dchip.d2::before { background: var(--d2); } .dchip.d3::before { background: var(--d3); }
    .dchip.d4::before { background: var(--d4); } .dchip.d5::before { background: var(--d5); } .dchip.d6::before { background: var(--d6); }

    /* everything-view filters */
    .cflt { border-bottom: 1px solid var(--border); }
    .cflt[hidden] { display: none; }
    .cflt > summary { cursor: pointer; font-size: 13px; font-weight: 600; padding: 8px 12px; list-style: none; display: flex; gap: 8px; align-items: baseline; }
    .cflt > summary::-webkit-details-marker { display: none; }
    .cflt > summary::before { content: "\\25B8"; color: var(--muted); }
    .cflt[open] > summary::before { content: "\\25BE"; }
    .cfilters { display: flex; flex-wrap: wrap; gap: 6px 8px; align-items: center; padding: 2px 12px 10px; }
    .cfilters .grp { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
    .cmap .chip { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; line-height: 1.2; padding: 4px 10px 4px 8px; border: 1px solid var(--border);
            border-radius: 999px; background: transparent; cursor: pointer; min-height: 28px; }
    .chip i { width: 9px; height: 9px; border-radius: 50%; background: var(--dx); flex: none; }
    .chip[aria-pressed="false"] { color: var(--muted); text-decoration: line-through; }
    .chip[aria-pressed="false"] i { background: transparent !important; box-shadow: inset 0 0 0 1.5px var(--dx); }
    .chip.d0 i { background: var(--d0); } .chip.d1 i { background: var(--d1); } .chip.d2 i { background: var(--d2); } .chip.d3 i { background: var(--d3); }
    .chip.d4 i { background: var(--d4); } .chip.d5 i { background: var(--d5); } .chip.d6 i { background: var(--d6); }
    .cmap select { font-size: 12px; padding: 4px 6px; border: 1px solid var(--border); border-radius: 6px; background: var(--card); min-height: 28px; }
    .cmap .clink { font-size: 12px; background: none; border: 0; color: var(--accent); cursor: pointer; padding: 3px 4px; text-decoration: underline; }
    .cmap .ccount { font-size: 12px; font-weight: 400; color: var(--muted); margin-left: auto; }

    /* body: chooser rail, then the map (the focus view replaces both when a node is opened) */
    .cbody { display: grid; grid-template-columns: minmax(0, 1fr); }
    .crail { min-width: 0; border-bottom: 1px solid var(--border); }
    .cmain { min-width: 0; }
    .cmap[data-view="all"] .crail, .cmap[data-view="full"] .crail { display: none; }
    .cmap.is-focus .cbody, .cmap.is-focus .legend, .cmap.is-focus .cflt, .cmap.is-focus .nos, .cmap.is-focus .top5 { display: none; }

    /* chooser: key chains */
    .rail-in { display: flex; gap: 8px; overflow-x: auto; padding: 10px 12px; scroll-snap-type: x proximity; }
    .rail-in .rh { display: none; }
    .rail-in ul.cl { display: contents; list-style: none; margin: 0; padding: 0; }
    .rail-in ul.cl li { display: flex; flex: none; margin: 0; }
    .rh { font-size: 13px; font-weight: 600; color: var(--ink2); margin: 0 2px 2px; }
    .ccard { flex: 0 0 min(260px, 78vw); scroll-snap-align: start; display: flex; flex-direction: column; gap: 5px; text-align: left; padding: 10px 12px; border: 1px solid var(--border);
             border-radius: 10px; background: transparent; cursor: pointer; line-height: 1.3; }
    .ccard:hover { background: var(--soft); }
    .ccard[aria-pressed="true"] { border-color: var(--text); background: var(--soft); box-shadow: inset 4px 0 0 var(--focus); padding-left: 14px; }
    .ccard .ct { font-size: 14px; font-weight: 600; color: var(--text); }
    .ccard .cs { font-size: 12.5px; color: var(--muted); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .ccard .cm { display: flex; flex-wrap: wrap; gap: 3px 10px; align-items: center; }
    .bdg { font-size: 12px; font-weight: 600; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--ink2); color: var(--text); white-space: nowrap; }
    .bdg.predicted { border-style: dashed; }
    .bdg.mixed { border-style: dashed; color: var(--ink2); }
    @media (max-width: 479px) {
      .ctop { min-width: 0; }
      .seg { display: flex; width: 100%; }
      .cmap .seg button { flex: 1 1 auto; padding: 8px 4px; font-size: 13px; min-width: 0; }
    }
    @media (max-width: 899px) {
      .rail-in { -webkit-mask-image: linear-gradient(to right, #000 calc(100% - 32px), transparent); mask-image: linear-gradient(to right, #000 calc(100% - 32px), transparent); padding-right: 32px; }
      .seg button { min-height: 44px; padding: 8px 14px; }
      .cmap .csearch { font-size: 16px; min-height: 44px; }
      .czoom button { width: 44px; height: 44px; }
      .cmap[data-view="chains"] .mkey { display: inline; }
      .cmap { display: flex; flex-direction: column; }
      .cbody { display: contents; }
      .ctop { order: 0; } .cflt, .nos { order: 1; } .crail { order: 2; } .cmain { order: 3; } .fview { order: 4; } .legend { order: 5; }
      .ccard { flex-basis: auto; width: max-content; max-width: 78vw; padding: 8px 12px; }
      .ccard .cs, .ccard .cm .dchip { display: none; }
      .ccard .ct { font-size: 13.5px; }
    }
    /* chooser: topics */
    .tbtn { display: flex; align-items: center; gap: 8px; text-align: left; padding: 7px 10px; border: 1px solid var(--border); border-radius: 999px; background: transparent; cursor: pointer; font-size: 13.5px; white-space: nowrap; flex: none; }
    .tbtn:hover { background: var(--soft); }
    .tbtn[aria-pressed="true"] { background: var(--text); color: var(--card); border-color: var(--text); }
    .tbtn[aria-pressed="true"] .tn { color: inherit; opacity: .8; }
    .tbtn i { width: 10px; height: 10px; border-radius: 50%; background: var(--dx); flex: none; }
    .tbtn[aria-pressed="true"] i { box-shadow: 0 0 0 2px var(--card); }
    .tbtn.d0 i { background: var(--d0); } .tbtn.d1 i { background: var(--d1); } .tbtn.d2 i { background: var(--d2); } .tbtn.d3 i { background: var(--d3); }
    .tbtn.d4 i { background: var(--d4); } .tbtn.d5 i { background: var(--d5); } .tbtn.d6 i { background: var(--d6); }
    .tbtn .tl { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
    .tbtn .tn { margin-left: auto; font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
    .tgrp { display: contents; }

    /* map header + view */
    .mhead { display: flex; flex-wrap: wrap; gap: 2px 14px; align-items: baseline; padding: 9px 14px 8px; border-bottom: 1px solid var(--border); min-height: 42px; }
    .mhead .mt { font-size: 15px; font-weight: 600; }
    .mhead .mh { font-size: 13px; color: var(--muted); }
    .cview { position: relative; height: min(66vh, 480px); background: var(--card); outline-offset: -2px; overflow: hidden; }
    @media (max-width: 899px) { .cmap[data-view="all"] .cview, .cmap[data-view="topic"] .cview, .cmap[data-view="full"] .cview { height: min(88vh, 720px); } }
    @media (min-width: 900px) { .cview { height: 640px; } .cmap[data-view="all"] .cview, .cmap[data-view="full"] .cview { height: 860px; } }
    .cview svg { display: block; width: 100%; height: 100%; touch-action: none; cursor: grab; font-family: inherit; user-select: none; -webkit-user-select: none; }
    .cview.drag svg { cursor: grabbing; }

    .cmap .lane { fill: var(--dx); fill-opacity: var(--lane); stroke: none; }
    .cmap .lane.d0 { fill: var(--d0); } .cmap .lane.d1 { fill: var(--d1); } .cmap .lane.d2 { fill: var(--d2); }
    .cmap .lane.d3 { fill: var(--d3); } .cmap .lane.d4 { fill: var(--d4); } .cmap .lane.d5 { fill: var(--d5); } .cmap .lane.d6 { fill: var(--d6); }
    .cmap .ltag { fill: var(--dx); }
    .cmap .ltag.d0 { fill: var(--d0); } .cmap .ltag.d1 { fill: var(--d1); } .cmap .ltag.d2 { fill: var(--d2); } .cmap .ltag.d3 { fill: var(--d3); }
    .cmap .ltag.d4 { fill: var(--d4); } .cmap .ltag.d5 { fill: var(--d5); } .cmap .ltag.d6 { fill: var(--d6); }
    .cmap .lanelabel { font-size: 16px; font-weight: 700; fill: var(--text); pointer-events: none; }
    .cmap .lanecount { font-size: 14px; fill: var(--muted); pointer-events: none; }
    .cmap .axis { font-size: 14px; fill: var(--muted); pointer-events: none; }
    .cmap .axisln { stroke: var(--muted); stroke-width: 1; opacity: .5; }
    .cmap .lg.hid { opacity: .22; }

    .cmap .nd { cursor: pointer; outline: none; }
    .cmap .nd .tap { fill: transparent; }
    .cmap .nd .shape { stroke: var(--card); stroke-width: 1.5; fill: var(--dx); }
    .cmap .nd .shape.d0 { fill: var(--d0); } .cmap .nd .shape.d1 { fill: var(--d1); } .cmap .nd .shape.d2 { fill: var(--d2); } .cmap .nd .shape.d3 { fill: var(--d3); }
    .cmap .nd .shape.d4 { fill: var(--d4); } .cmap .nd .shape.d5 { fill: var(--d5); } .cmap .nd .shape.d6 { fill: var(--d6); } .cmap .nd .shape.dx { fill: var(--dx); }
    .cmap .nd .ring { fill: none; stroke: transparent; stroke-width: 3; pointer-events: none; }
    .cmap .nd:focus-visible .ring { stroke: var(--focus); }
    .cmap .nd.sel .ring { stroke: var(--text); }
    .cmap .nd.m .ring { stroke: var(--focus); stroke-dasharray: 3 2; }
    .cmap .nlabel { font-size: var(--nfs, 13px); fill: var(--text); paint-order: stroke; stroke: var(--halo); stroke-width: 4px; stroke-linejoin: round; pointer-events: none; }
    .cmap .lh { opacity: 0; visibility: hidden; transition: opacity .15s, visibility 0s .15s; }
    .cmap .nlabel, .cmap .lanelabel, .cmap .lanecount, .cmap .axis { transition: opacity .15s, visibility 0s; }
    .cmap .nlabel.mid { text-anchor: middle; }
    .cmap .nlabel.hub { font-weight: 600; }
    .cmap svg.scn-all:not(.zl) .nlabel.ln { display: none; }
    .cmap svg.scn-all.zl .nlabel.sh { display: none; }
    .cmap .nlabel.start { text-anchor: start; }
    .cmap .scn-chain .nlabel { font-weight: 600; }
    .cmap .dirb { stroke: var(--card); stroke-width: 1.5; pointer-events: none; }
    .cmap .dirb.up { fill: var(--up); } .cmap .dirb.down { fill: var(--down); } .cmap .dirb.flat { fill: var(--dx); }
    .cmap .nd.ghost .shape, .cmap .nd.ghost .dirb { opacity: .5; }
    .cmap .nd.ghost .nlabel { fill: var(--ink2); }
    .cmap .nd.ghost:hover .shape, .cmap .nd.ghost:focus-visible .shape, .cmap .nd.ghost.sel .shape, .cmap .nd.ghost.open .shape { opacity: 1; }
    .cmap .mkey { display: none; font-size: 12.5px; color: var(--ink2); white-space: nowrap; }
    .cmap .nd.lo .nlabel { opacity: 0; }
    .cmap .zl .nd.lo .nlabel, .cmap .nd.lo:hover .nlabel, .cmap .nd.lo:focus .nlabel, .cmap .nd.lo.m .nlabel, .cmap .nd.lo.sel .nlabel, .cmap .nd.lo.hl .nlabel, .cmap .nd.lo.hh .nlabel { opacity: 1; }

    .cmap .ed { opacity: var(--eo, .5); }
    .cmap .ed path.line { fill: none; stroke: var(--ec); stroke-width: var(--sw, 1.4); stroke-linecap: butt; }
    .cmap .ed.e-up { --ec: var(--up); } .cmap .ed.e-down { --ec: var(--down); } .cmap .ed.e-mixed { --ec: var(--mix); } .cmap .ed.e-none { --ec: var(--ln); }
    .cmap .ed.k-contradicts { --ec: var(--con); }
    .cmap .ed.k-contradicts path.line { stroke-dasharray: 1.5 4; stroke-linecap: round; }
    .cmap .ed.st-predicted:not(.k-contradicts) path.line { stroke-dasharray: 7 4.5; }
    .cmap .ed path.hit { fill: none; stroke: transparent; stroke-width: 12; cursor: help; }
    .cmap .mk { stroke: none; }
    .cmap .mk.e-up { fill: var(--up); } .cmap .mk.e-down { fill: var(--down); } .cmap .mk.e-mixed { fill: var(--mix); } .cmap .mk.e-none { fill: var(--ln); }
    .cmap .mk.bar { fill: none; stroke: var(--con); stroke-width: 2.4; stroke-linecap: round; }
    .cmap .elabel { font-size: var(--efs, 12.5px); fill: var(--text); paint-order: stroke; stroke: var(--halo); stroke-width: 4px; stroke-linejoin: round; pointer-events: none; display: none; }
    .cmap .elabel.sub { font-size: 12.5px; fill: var(--muted); }
    .cmap .ed.perm .elabel, .cmap .ed.hv .elabel { display: inline; }
    .cmap .ed.hh, .cmap .ed.hl, .cmap .ed.hv, .cmap .ed.perm { opacity: 1; }
    .cmap .ed.hh path.line, .cmap .ed.hl path.line, .cmap .ed.hv path.line { stroke-width: var(--sw2, 2.6); }
    .cmap .ed.ghosted { opacity: .55; }
    .cmap .ed.ghosted.hl, .cmap .ed.ghosted.hh { opacity: 1; }
    .cmap svg.focus .ed:not(.hl):not(.perm) { opacity: .25; }
    .cmap svg.focus .nd:not(.hl):not(.ghost) { opacity: .22; }
    .cmap svg.scn-chain.focus .nd:not(.hl):not(.ghost) { opacity: .5; }
    .cmap svg.scn-chain.focus .nd.ghost { opacity: 1; }
    .cmap svg.scn-chain.focus .ed:not(.hl):not(.perm) { opacity: .1; }
    .cmap svg.scn-chain .ed.ghosted { opacity: .7; }
    .cmap svg.scn-chain .ed.ghosted.hl { opacity: 1; }
    .cmap svg.scn-chain .elabel { font-size: 12px; font-weight: 600; fill: var(--text); }
    .cmap svg.scn-chain.focus .ed.perm { opacity: 1; }
    .cmap svg.hov .ed:not(.hh):not(.perm):not(.hv) { opacity: .25; }
    .cmap svg.lsel .ed:not(.hl):not(.hh):not(.hv):not(.perm) { opacity: 0; }
    .cmap svg.hov .nd:not(.hh):not(.hov1) { opacity: .28; }
    .cmap svg.scn-chain.hov .nd { opacity: 1; }
    .cmap svg.scn-chain.hov .nd.ghost { opacity: 1; }
    .cmap svg.searching .nd:not(.m) { opacity: .25; }
    .cmap .hid { display: none; }

    .czoom { position: absolute; bottom: 10px; right: 10px; display: flex; flex-direction: column; gap: 4px; }
    .czoom button { width: 34px; height: 34px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); cursor: pointer; font-size: 18px; line-height: 1; padding: 0; opacity: .95; }
    .czoom button.fit { font-size: 12px; }
    .czoom button:hover { background: var(--soft2); }
    .chint { font-size: 12px; color: var(--muted); padding: 4px 2px 0; }
    .ctip { position: absolute; z-index: 5; pointer-events: none; background: var(--text); color: var(--card); font-size: 13px;
            padding: 7px 10px; border-radius: 6px; max-width: 270px; line-height: 1.35; display: none; }
    .ctip b { font-weight: 600; } .ctip .tm { opacity: .85; font-size: 12px; display: block; margin-top: 2px; }

    /* map tools: currency lens + predicted ghosts */
    .mhead { align-items: center; }
    .mtools { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: center; margin-left: auto; }
    .lens, .lns { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; color: var(--ink2); }
    .lens .lbl, .lns .lbl { margin-right: 2px; } .lns { align-items: center; gap: 4px; font-size: 12px; color: var(--ink2); }
    .cmap .lns button, .cmap .lens button { font-size: 12px; font-weight: 600; padding: 3px 11px; min-height: 28px; border: 1px solid var(--border); border-radius: 999px; background: transparent; cursor: pointer; }
    .cmap .lns button[aria-checked="true"], .cmap .lens button[aria-checked="true"] { background: var(--text); color: var(--card); border-color: var(--text); }
    .ptog { display: inline-flex; gap: 6px; align-items: center; font-size: 12px; color: var(--ink2); cursor: pointer; min-height: 28px; }
    @media (max-width: 899px) { .cmap .lns button, .cmap .lens button, .ptog { min-height: 44px; } .cmap .lens button { padding: 3px 14px; } .ptog input { width: 20px; height: 20px; } .mtools { margin-left: 0; } }
    .sronly { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }

    /* map: lens, trace, predicted ghosts, full map */
    .cmap svg.lens .nd:not(.lit) { filter: opacity(.16); }
    .cmap svg.lens .ed:not(.lit) { filter: opacity(.08); }
    .cmap svg.lens .nd.lit .ring { stroke: var(--focus); stroke-dasharray: 2 2; }
    .cmap svg.tracing .ed:not(.tr) { opacity: .04; }
    .cmap svg.tracing .nd:not(.tr) { opacity: .2; }
    .cmap svg.tracing .ed.tr { opacity: 1; }
    .cmap svg.tracing .ed.tr path.line { stroke-width: var(--sw2, 3px); }
    .cmap svg.tracing .ed.tr .elabel { display: inline; }
    .cmap .nd.tr .ring { stroke: var(--focus); }
    .cmap svg.scn-full.zl .ed .elabel { display: inline; font-size: var(--efs, 12.5px); }
    .cmap svg.scn-full.focus .ed:not(.hl) .elabel { display: none; }
    .cmap .lanestory { fill: var(--ink2); pointer-events: none; }
    .cmap .ft { cursor: pointer; outline: none; }
    .cmap .ft-ln { fill: none; stroke: var(--ink2); stroke-width: 1.5; stroke-dasharray: 1.5 4.5; stroke-linecap: round; opacity: .75; }
    .cmap .ft-dot { fill: var(--card); stroke: var(--ink2); stroke-width: 1.5; stroke-dasharray: 2.2 2.2; }
    .cmap .ftl { font-size: 11.5px; fill: var(--ink2); paint-order: stroke; stroke: var(--halo); stroke-width: 3.5px; stroke-linejoin: round; }
    .cmap .ftl .fto { font-size: 11px; fill: var(--muted); font-weight: 600; }
    .cmap .ft:hover .ft-dot, .cmap .ft:focus-visible .ft-dot { stroke-dasharray: none; fill: var(--soft2); }

    /* popover */
    .pop { position: absolute; z-index: 40; width: min(350px, calc(100% - 12px)); max-height: min(72vh, 440px); overflow: auto; background: var(--card); color: var(--text); border: 1px solid var(--text); border-radius: 10px;
           box-shadow: 0 10px 30px rgba(0,0,0,.3); padding: 10px 14px 12px; font-size: 13px; line-height: 1.4; }
    .pop[hidden] { display: none; }
    .pop .px { position: absolute; top: 2px; right: 2px; width: 36px; height: 36px; border: 0; background: none; cursor: pointer; font-size: 16px; color: var(--ink2); }
    .pop .ph { padding-right: 30px; } .pop .pt { font-size: 14px; }
    .pop .pm { margin: 4px 0 6px; font-weight: 600; }
    .pop .pc { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 6px; }
    .pop .chp { font-size: 11.5px; border: 1px solid var(--ink2); border-radius: 999px; padding: 0 8px; white-space: nowrap; }
    .pop .chp.dashed { border-style: dashed; }
    .pop .psteps { margin: 0 0 6px; padding-left: 20px; } .pop .psteps li { margin: 0 0 2px; }
    .pop .ps { margin: 0 0 5px; } .pop .ps b { display: block; font-size: 12px; color: var(--ink2); }
    .pop .pn { margin: 6px 0 0; font-size: 12px; color: var(--muted); }
    .odds { display: inline-flex; align-items: center; gap: 5px; font-size: 11.5px; font-weight: 600; }
    .odds i { display: inline-flex; gap: 2px; } .odds i b { width: 5px; height: 10px; border: 1px solid var(--ink2); border-radius: 1px; display: inline-block; } .odds i b.on { background: var(--ink2); }

    /* focus view */
    .sic path { fill: var(--dx); } .sic path.d0 { fill: var(--d0); } .sic path.d1 { fill: var(--d1); } .sic path.d2 { fill: var(--d2); } .sic path.d3 { fill: var(--d3); } .sic path.d4 { fill: var(--d4); } .sic path.d5 { fill: var(--d5); } .sic path.d6 { fill: var(--d6); }
    .fview { padding: 14px 20px 16px; font-size: 13px; line-height: 1.4; display: flex; flex-direction: column; gap: 10px; position: relative; overflow: hidden; }
    .fview[hidden] { display: none; }
    @media (min-width: 900px) { .fview { height: auto; min-height: 560px; } }
    .fv-top { display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center; justify-content: space-between; }
    .fv-crumbs { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 4px; font-size: 13px; min-width: 0; }
    .fv-crumbs .sep { color: var(--muted); padding: 0 2px; }
    .fv-back, .fv-crumbs .crumb { font: inherit; font-size: 13px; font-weight: 600; padding: 4px 10px; min-height: 32px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); color: var(--text); cursor: pointer; }
    .fv-back { border-color: var(--text); } .fv-back:hover, .fv-crumbs button.crumb:hover { background: var(--soft2); }
    .fv-crumbs .crumb.cur { border-color: transparent; background: none; cursor: default; padding-left: 4px; }
    .fv-title { min-width: 0; }
    .fv-title h2 { margin: 0; font-size: 18px; line-height: 1.2; outline: none; }
    .cmap .fv-title h2:focus, .cmap .fv-title h2:focus-visible { outline: none; }
    .fv-chips { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center; font-size: 12.5px; color: var(--ink2); margin-top: 3px; }
    .gly { font-style: normal; } .gly.up, .gly.stronger { color: var(--up); } .gly.down, .gly.weaker { color: var(--down); } .gly.flat { color: var(--ink2); }
    .dirb2 { font-weight: 600; color: var(--ink2); }
    .imb { font-weight: 700; color: var(--text); border: 1px solid var(--ink2); border-radius: 999px; padding: 0 9px; font-size: 12px; }
    .imb.im-small { font-weight: 500; border-style: dashed; color: var(--ink2); }
    .fv-tools .trace { display: flex; align-items: center; gap: 6px; font-size: 12.5px; color: var(--ink2); }
    .fview select { font: inherit; font-size: 13px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); color: var(--text); min-height: 36px; max-width: 210px; }
    .fv-so { margin: 2px 0 0; font-size: 28px; font-weight: 700; line-height: 1.18; letter-spacing: -.01em; max-width: 34em; text-wrap: balance; color: var(--text); }
    .fview.small .fv-so { font-size: 24px; font-weight: 650; }
    .fv-fy { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 20px; }
    .fyc-w { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
    .fy-h { font-size: 12px; font-weight: 700; color: var(--ink2); margin-right: 2px; }
    .fyc, .gg { font: inherit; font-size: 12.5px; color: var(--text); background: var(--soft); border: 1px solid var(--border); border-radius: 999px; padding: 4px 11px; min-height: 30px; cursor: pointer; display: inline-flex; gap: 6px; align-items: center; font-variant-numeric: proportional-nums; }
    .fyc:hover, .gg:hover, .stile[role=button]:hover, .t5c:hover { border-color: var(--text); }
    .fyc .fk { font-weight: 700; } .fyc .fs { font-size: 12px; } .fyc.d-up .fs { color: var(--up); } .fyc.d-down .fs { color: var(--down); } .fyc .fw { color: var(--ink2); }
    .gauges { display: flex; flex-wrap: wrap; gap: 6px; }
    .gg { border-radius: 8px; gap: 7px; } .gg .gl { color: var(--ink2); font-weight: 600; } .gg .gv { font-weight: 700; }
    .gg i { display: inline-flex; gap: 2px; align-items: flex-end; } .gg i b { width: 5px; height: 11px; border: 1px solid var(--ink2); border-radius: 1px; display: inline-block; } .gg i b.on { background: var(--ink2); }
    .fv-note { margin: 0; font-size: 12px; color: var(--ink2); padding: 3px 10px; border: 1px dashed var(--ink2); border-radius: 8px; width: fit-content; max-width: 100%; }
    .fv-trace { font-size: 12.5px; padding: 6px 10px; border: 1px solid var(--focus); border-radius: 8px; background: var(--soft); display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: center; }
    .fv-trace[hidden] { display: none; }
    .fv-trace .tsteps { display: inline-flex; flex-wrap: wrap; gap: 3px 4px; align-items: center; }
    .fv-trace button { font: inherit; font-size: 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--card); padding: 2px 8px; min-height: 28px; cursor: pointer; color: var(--text); }
    .fv-trace .tnode { font-weight: 600; } .fv-trace .tl { color: var(--ink2); border-style: dashed; } .fv-trace .tx { margin-left: auto; }
    .fv-flow { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 4px; }
    .fv-dg { flex: 1; min-height: 230px; position: relative; overflow: hidden auto; }
    @media (min-width: 900px) { .fv-flow { flex: none; } .fv-dg { flex: none; height: clamp(300px, calc(100vh - 500px), 470px); } }
    .fv-leg { display: flex; flex-wrap: wrap; gap: 4px 16px; align-items: center; font-size: 12px; color: var(--ink2); }
    .fv-leg span { display: inline-flex; align-items: center; gap: 5px; }
    .fv-leg .lg { fill: none; stroke: var(--ink2); stroke-width: 2; } .fv-leg .lg.pred { stroke-dasharray: 6 4; } .fv-leg .lg.con { stroke: var(--con); stroke-dasharray: 1.5 4; stroke-linecap: round; }
    .fv-strip { display: flex; gap: 8px; align-items: stretch; }
    .stile { flex: 1 1 0; min-width: 0; border: 1px solid var(--border); border-radius: 10px; padding: 8px 12px 9px; background: var(--card); display: flex; flex-direction: column; gap: 3px; font-variant-numeric: proportional-nums; }
    .stile[role=button] { cursor: pointer; }
    .stile.cur { flex: 1.5 1 0; } .stile.next { flex: 1.3 1 0; }
    .stile h3 { margin: 0; font-size: 12px; font-weight: 700; color: var(--ink2); }
    .stile .sn { font-size: 13px; font-weight: 600; color: var(--text); overflow-wrap: anywhere; } .stile .sn.mute { color: var(--muted); font-weight: 500; }
    .stile .nm { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; } .stile .nm .chp { font-size: 11.5px; border: 1px solid var(--ink2); border-radius: 999px; padding: 0 8px; white-space: nowrap; }
    .stile .snx { font-size: 13px; color: var(--text); display: -webkit-box; -webkit-line-clamp: 5; -webkit-box-orient: vertical; overflow: hidden; }
    .stile .cr { display: flex; flex-wrap: wrap; gap: 0 12px; align-items: baseline; font-size: 12.5px; }
    .stile .cr .ck { min-width: 2.6em; } .stile .cr .cs, .stile .cr .ct { color: var(--ink2); white-space: nowrap; }
    .stile .cr .gly { font-weight: 700; }
    .keypts { list-style: none; margin: 0 0 6px; padding: 0; display: grid; gap: 6px; }
    .keypts li { padding-left: 10px; border-left: 3px solid var(--border); }
    .keypts .pk { display: block; font-size: 12px; font-weight: 700; color: var(--ink2); }
    .keypts .pv { display: block; font-size: 13px; }
    .seen h6 { margin: 0 0 3px; font-size: 13px; color: var(--ink2); } .seen h6.sub { margin-top: 5px; }
    .seen p { margin: 0 0 3px; font-size: 12.5px; color: var(--ink2); } .seen p.hs { color: var(--text); font-weight: 600; }
    .seen ul.ag { margin: 0; padding-left: 18px; font-size: 12.5px; color: var(--ink2); }
    @media (max-width: 899px) {
      .fview { height: auto; padding: 12px 14px 14px; } .fv-dg { overflow: visible; flex: none; min-height: 0; } .fv-flow { flex: none; }
      .fview select { min-height: 44px; } .fv-back, .fv-crumbs .crumb { min-height: 44px; } .fv-trace button { min-height: 44px; }
      .fv-so { font-size: 24px; } .fview.small .fv-so { font-size: 21px; }
      .fv-strip { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .fyc { min-height: 44px; } .gauges { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); width: 100%; gap: 6px; }
      .gg { min-height: 44px; flex-direction: column; gap: 2px; border-radius: 10px; padding: 4px 6px; justify-content: center; text-align: center; }
      .stile.cur { grid-column: 1 / -1; } .stile .snx { display: block; -webkit-line-clamp: unset; } .stile .cr .w { display: none; } .stile .cr { display: grid; grid-template-columns: 2.6em 1fr 1fr; gap: 0 6px; }
    }
    .pop.wide { width: min(560px, calc(100% - 12px)); max-height: min(78vh, 560px); }
    .pop a { color: var(--accent, var(--focus)); }
    .pop .pbar { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
    .pbtn { font: inherit; font-size: 13px; font-weight: 600; border: 1px solid var(--text); border-radius: 8px; background: var(--card); color: var(--text); padding: 6px 12px; min-height: 36px; cursor: pointer; } .pbtn:hover { background: var(--soft2); }
    .pop .prow { display: block; width: 100%; text-align: left; font: inherit; font-size: 13px; color: var(--text); background: none; border: 0; border-top: 1px solid var(--border); padding: 8px 2px; min-height: 40px; cursor: pointer; } .pop .prow:hover { background: var(--soft); }
    .pop table.lt { border-collapse: collapse; width: 100%; font-size: 12px; } .pop table.lt th, .pop table.lt td { text-align: left; padding: 3px 6px 3px 0; border-top: 1px solid var(--border); vertical-align: top; } .pop table.lt th { color: var(--ink2); }
    @media (max-width: 699px) {
      .pop { position: fixed !important; left: 0 !important; right: 0; bottom: 0; top: auto !important; width: auto !important; max-height: 72vh !important; border-radius: 16px 16px 0 0; padding-bottom: 20px; z-index: 60; }
      .pop .px { width: 44px; height: 44px; }
    }
    @media (max-width: 699px) and (prefers-reduced-motion: no-preference) { .pop:not([hidden]) { animation: popup .22s ease-out; } }
    @keyframes popup { from { transform: translateY(100%); } to { transform: none; } }
    .top5 { padding: 10px 14px 12px; border-bottom: 1px solid var(--border); }
    .top5[hidden] { display: none; }
    .top5 h5 { margin: 0 0 6px; font-size: 13px; color: var(--ink2); }
    .t5row { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }
    .t5c { font: inherit; text-align: left; color: var(--text); background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 8px 10px; display: flex; flex-direction: column; gap: 3px; cursor: pointer; min-width: 0; }
    .t5h { display: flex; gap: 6px; align-items: baseline; } .t5h b { font-size: 13px; overflow-wrap: anywhere; } .t5h .rk { font-size: 12px; color: var(--ink2); font-weight: 700; }
    .t5s { margin: 0; font-size: 12.5px; color: var(--ink2); line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .t5m { display: flex; flex-wrap: wrap; gap: 3px 8px; font-size: 11.5px; color: var(--ink2); margin-top: auto; }
    .t5m .t5k { white-space: nowrap; } .t5m .gly { font-weight: 700; }
    @media (max-width: 899px) { .t5row { display: flex; overflow-x: auto; scroll-snap-type: x proximity; padding-bottom: 4px; } .t5c { flex: 0 0 78%; max-width: 280px; scroll-snap-align: start; min-height: 44px; } }
    .cmap .nd.o5 { filter: opacity(.72); }
    /* diagram */
    .cmap svg.dg { display: block; font-family: inherit; max-width: 100%; }
    .dg .dg-title { font-size: 12px; font-weight: 700; fill: var(--ink2); }
    .dg .dg-box { fill: var(--card); stroke: var(--border); stroke-width: 1.2; }
    .dg .dg-n { cursor: pointer; outline: none; } .dg .dg-n:hover .dg-box, .dg .dg-n:focus-visible .dg-box { stroke: var(--text); stroke-width: 1.8; }
    .dg .dg-bar, .dg .dg-ic { fill: var(--dx); }
    .dg .dg-bar.d0, .dg .dg-ic.d0 { fill: var(--d0); } .dg .dg-bar.d1, .dg .dg-ic.d1 { fill: var(--d1); } .dg .dg-bar.d2, .dg .dg-ic.d2 { fill: var(--d2); } .dg .dg-bar.d3, .dg .dg-ic.d3 { fill: var(--d3); }
    .dg .dg-bar.d4, .dg .dg-ic.d4 { fill: var(--d4); } .dg .dg-bar.d5, .dg .dg-ic.d5 { fill: var(--d5); } .dg .dg-bar.d6, .dg .dg-ic.d6 { fill: var(--d6); }
    .dg .dg-nm { font-size: 12px; font-weight: 600; fill: var(--text); }
    .dg .dg-dir { font-size: 11px; } .dg .dg-dir.up { fill: var(--up); } .dg .dg-dir.down { fill: var(--down); }
    .dg .dg-pil { fill: var(--soft); stroke: var(--dx); stroke-width: 2.2; }
    .dg .dg-pillar.d0 .dg-pil { stroke: var(--d0); } .dg .dg-pillar.d1 .dg-pil { stroke: var(--d1); } .dg .dg-pillar.d2 .dg-pil { stroke: var(--d2); } .dg .dg-pillar.d3 .dg-pil { stroke: var(--d3); }
    .dg .dg-pillar.d4 .dg-pil { stroke: var(--d4); } .dg .dg-pillar.d5 .dg-pil { stroke: var(--d5); } .dg .dg-pillar.d6 .dg-pil { stroke: var(--d6); }
    .dg .dg-pn { font-size: 13px; font-weight: 700; fill: var(--text); }
    .dg .dg-pd, .dg .dg-pm { font-size: 12px; fill: var(--ink2); font-weight: 600; } .dg .dg-pd.up, .dg .dg-pm.up { fill: var(--up); } .dg .dg-pd.down, .dg .dg-pm.down { fill: var(--down); }
    .dg .dg-l { font-size: 11.5px; fill: var(--ink2); paint-order: stroke; stroke: var(--card); stroke-width: 4px; stroke-linejoin: round; }
    .dg .dg-l.lnk:hover { fill: var(--text); text-decoration: underline; }
    .cmap .dg .ed path.hit { stroke: transparent; stroke-width: 26; cursor: pointer; fill: none; outline: none; }
    .cmap .dg .ed:hover path.line { stroke-width: 3.2px; }
    .dg .dg-empty { font-size: 12px; fill: var(--muted); }
    .dg .dg-fut-ln { fill: none; stroke: var(--ink2); stroke-width: 1.7; stroke-dasharray: 1.5 4.5; stroke-linecap: round; }
    .dg .dg-fb { fill: var(--card); stroke: var(--ink2); stroke-width: 1.4; stroke-dasharray: 5 4; }
    .dg .dg-f { cursor: pointer; outline: none; } .dg .dg-f:hover .dg-fb, .dg .dg-f:focus-visible .dg-fb { stroke: var(--text); stroke-dasharray: none; }
    .dg .dg-ob { fill: none; stroke: var(--ink2); } .dg .dg-ob.on { fill: var(--ink2); }
    .dg .dg-o { font-size: 11.5px; font-weight: 700; fill: var(--text); }
    .dg .dg-ft { font-size: 12px; fill: var(--text); } .dg .dg-if { fill: var(--ink2); }
    .dg .dg-more { cursor: pointer; outline: none; } .dg .dg-more rect, .dg .dg-more-r { fill: var(--soft); stroke: var(--border); } .dg .dg-more text, .dg .dg-nvt { font-size: 12px; font-weight: 600; fill: var(--text); }
    .dg .vnav { cursor: pointer; outline: none; } .dg .vhit { cursor: pointer; }
    .cmap .dg .ed.dgl { --ec: var(--ink2); opacity: .9; } .cmap .dg .ed.dgl.k-contradicts { --ec: var(--con); }
    .cmap .dg .ed.dgl path.line { stroke-linecap: round; }
    .cmap .dg .ed.dgl.st-predicted:not(.k-contradicts) path.line { stroke-dasharray: 7 5; }
    .dg .gl { font-weight: 700; } .dg .gl.up { fill: var(--up); } .dg .gl.down { fill: var(--down); } .dg .gl.con { fill: var(--text); }
    .dg .dg-l { font-size: 12px; stroke-width: 5px; }
    .dg .dg-tagr { fill: var(--card); stroke: var(--ink2); stroke-width: 1.2; } .dg .dg-tagt { font-size: 12px; font-weight: 600; fill: var(--ink2); } .dg .gl.mixed, .dg .gl.none, .dg .gl.flat { fill: var(--ink2); }
    .dg .dg-dir { font-size: 14px; font-weight: 700; }
    .dg .dg-hub { cursor: pointer; outline: none; } .dg .dg-hub:hover .dg-pil, .dg .dg-hub:focus-visible .dg-pil { fill: var(--soft2); }
    .dg .dg-more-t { font-size: 12px; font-weight: 600; fill: var(--ink2); }
    .dg .dg-more rect { fill: var(--soft); stroke: var(--border); } .dg .dg-more:hover rect, .dg .dg-more:focus-visible rect { stroke: var(--text); }
    .dg .dg-n:focus-visible .dg-box { stroke: var(--focus); stroke-width: 2.4; }
    .cmap .dg .ed path.hit:focus-visible { stroke: var(--focus); stroke-opacity: .35; }
    @media (prefers-reduced-motion: no-preference) { .fv-dg svg { animation: dgin .18s ease-out; } @keyframes dgin { from { opacity: .55; } to { opacity: 1; } } }

    .legend { display: flex; flex-wrap: wrap; gap: 5px 14px; font-size: 13px; color: var(--muted); padding: 8px 14px 10px; border-top: 1px solid var(--border); align-items: center; }
    .legend > span { display: inline-flex; align-items: center; gap: 5px; }
    .legend svg { flex: none; }
    .legend .shp { width: 16px; height: 16px; fill: var(--muted); }
    .legend .lk { width: 34px; height: 12px; overflow: visible; }
    .legend .lk path { fill: none; stroke: var(--ink2); }
    .legend .lk .up { stroke: var(--up); } .legend .lk .down { stroke: var(--down); } .legend .lk .mixed { stroke: var(--mix); } .legend .lk .con { stroke: var(--con); }
    .legend .lk .hd { fill: var(--ink2); stroke: none; } .legend .lk .hd.up { fill: var(--up); } .legend .lk .hd.down { fill: var(--down); } .legend .lk .hd.mixed { fill: var(--mix); }
    .legend .sep { flex-basis: 100%; height: 0; }
    .legend b { color: var(--text); font-weight: 600; }
    .nos { font-size: 13px; padding: 8px 12px 8px 30px; margin: 0; }

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
  var NS = "http://www.w3.org/2000/svg";
  var CR = { low: 1, medium: 2, high: 3 };
  var FX = { up: ["▲", "up", "raises"], down: ["▼", "down", "lowers"], mixed: ["±", "mixed", "mixed effect"], none: ["→", "", "leads to"] };
  function el(tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }
  function sv(tag, attrs, cls) {
    var e = document.createElementNS(NS, tag); if (cls) e.setAttribute("class", cls);
    if (attrs) Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    return e;
  }
  function safeUrl(u) { u = String(u == null ? "" : u).trim(); return /^https?:\/\/[^\s]+$/i.test(u) ? u : null; }
  function conf(e) { return CR[e.conf] || 2; }
  function r1(v) { return Math.round(v * 10) / 10; }
  function wrap(s, width, lines) {
    var words = String(s || "").split(/\s+/).filter(Boolean).map(function (w) { return w.length > width ? w.slice(0, Math.max(1, width - 1)) + "…" : w; }), out = [], cur = "";
    words.forEach(function (w) { if (cur && cur.length + 1 + w.length > width) { out.push(cur); cur = w; } else cur = (cur ? cur + " " : "") + w; });
    if (cur) out.push(cur);
    if (out.length > lines) { out = out.slice(0, lines); var l = out[lines - 1]; out[lines - 1] = l.slice(0, Math.max(1, width - 1)).replace(/\s+$/, "") + "…"; }
    return out.length ? out : [""];
  }
  function bez(p, t) {
    var u = 1 - t;
    return [u * u * u * p[0] + 3 * u * u * t * p[2] + 3 * u * t * t * p[4] + t * t * t * p[6],
            u * u * u * p[1] + 3 * u * u * t * p[3] + 3 * u * t * t * p[5] + t * t * t * p[7]];
  }
  function pathD(p) { return "M" + p[0] + " " + p[1] + "C" + p[2] + " " + p[3] + " " + p[4] + " " + p[5] + " " + p[6] + " " + p[7]; }
  function dcls(d) { return d >= 0 && d < 7 ? "d" + d : "dx"; }

  function init(box) {
    var data;
    try { data = JSON.parse(box.querySelector("script.cdata").textContent); } catch (x) { return; }
    var view = box.querySelector(".cview"), svg = view.querySelector("svg"), gScene = svg.querySelector(".scene"), tip = box.querySelector(".ctip");
    var fv = box.querySelector(".fview"), rail = box.querySelector(".crail"), mt = box.querySelector(".mt"), mh = box.querySelector(".mh");
    var N = data.nodes || {}, E = data.edges || [], CH = data.chains || [], TOP = data.topics || [], LAY = data.lay || {}, SH = data.shapes, EXT = data.ext, UID = data.uid;
    /* defensive: strip leaked internal ids ("n12", "Node n33", "(IDs 5,18)") from every displayed string */
    function scrub(str) {
      if (/^n\d+$/.test(str) || /^https?:/i.test(str) || str.length < 2) return str;
      var o = str.replace(/\s*\((?:ids?|nodes?)\s*[:#]?\s*n?\d[^)]*\)/gi, "")
        .replace(/\s*\(\s*(?:(?:per|see|from|via|cf\.?)\s+)?n\d+(?:\s*(?:,|;|and|&|\/)\s*n?\d+)*\s*\)/gi, "")
        .replace(/\b[Nn]odes?\s+n(\d+)\b/g, function (m, d) { return N["n" + d] ? N["n" + d].l : ""; })
        .replace(/\bn(\d+)\b/g, function (m, d) { return N["n" + d] ? N["n" + d].l : m; });
      if (/\blabeled\b|^detail shows/i.test(str)) o = o.replace(/;?\s*\blabeled\s+[^;.]*?(?=\s*(?:;|\.|$))/gi, "").replace(/^\s*(?:detail|up|down|mixed|flat)\s*;\s*/i, "").replace(/^detail shows\s*/i, "Shows ").replace(/^(?:detail|up|down|mixed|flat)$/i, "").replace(/;\s*$/, "");
      return o === str ? str : o.replace(/\s+([,.;:])/g, "$1").replace(/\s{2,}/g, " ").trim();
    }
    (function walk(o) {
      if (!o || typeof o !== "object") return;
      Object.keys(o).forEach(function (k) { var v = o[k]; if (typeof v === "string") o[k] = scrub(v); else if (v && typeof v === "object") walk(v); });
    })(data);
    var ids = Object.keys(N), outE = {}, inE = {}, HUB = {};
    ids.slice().sort(function (a, b) { return (N[b].k || 0) - (N[a].k || 0) || (a < b ? -1 : 1); }).forEach(function (id, i) { HUB[id] = i; });
    ids.forEach(function (k) { outE[k] = []; inE[k] = []; });
    E.forEach(function (e, i) { if (outE[e.f]) outE[e.f].push(i); if (inE[e.t]) inE[e.t].push(i); });

    function defaultTopic() {
      if (CH.length) { var d = N[CH[0].n[0]].d; var k = d >= 0 ? "d" + d : "dx"; if (LAY["t:" + k]) return k; }
      return TOP.length ? TOP[0].k : "";
    }
    var S = { view: CH.length ? "chains" : "all", chain: 0, topic: defaultTopic(), sel: null, focus: false, trace: null, lens: "", showPred: true };
    var F = { hideDom: {}, region: "", conf: 1, order: 3, status: "", q: "" };
    var SC = null, nodeEl = {}, edgeEl = {}, laneEl = {}, tipTimer = null, hoverId = null;

    /* ---------- scene: chain (laid out in the browser so it can use the width it has) ---------- */
    function ghostPick(chainIds) {
      var inChain = {}; chainIds.forEach(function (i) { inChain[i] = true; });
      var rec = {};
      chainIds.forEach(function (id) {
        outE[id].concat(inE[id]).forEach(function (k) {
          var e = E[k], other = e.f === id ? e.t : e.f; if (inChain[other]) return;
          var r = rec[other] || (rec[other] = { s: 0, cause: false, anchors: {}, edges: [] });
          r.s += e.s; if (e.t === id) r.cause = true; r.anchors[id] = true; r.edges.push(k);
        });
      });
      var all = Object.keys(rec).sort(function (a, b) { return rec[b].s - rec[a].s || (a < b ? -1 : 1); });
      var up = all.filter(function (g) { return rec[g].cause; }), dn = all.filter(function (g) { return !rec[g].cause; });
      return { rec: rec, up: up.slice(0, 4), dn: dn.slice(0, 4), more: Math.max(0, up.length - 4) + Math.max(0, dn.length - 4), total: all.length };
    }
    function buildChain() {
      var c = CH[S.chain], r = box2(), n = c.n.length, K = 1.15, sc = { kind: "chain", nodes: {}, edges: [], lanes: null, ghostList: [] };
      var gp = ghostPick(c.n); sc.gp = gp;
      var m = 64, step = (r.w - 2 * m) / Math.max(1, n - 1), vertical = r.w < 520 || (n > 1 && step < 138);
      sc.vertical = vertical;
      function gedge(g, ids2, from) { return gp.rec[g].edges.filter(function (k) { return ids2.indexOf(E[k].f === g ? E[k].t : E[k].f) >= 0; }); }
      if (!vertical) {
        step = Math.max(150, Math.min(290, step));
        var x0 = m, W = 2 * m + (n - 1) * step, hasUp = gp.up.length > 0, hasDn = gp.dn.length > 0;
        var Y0 = hasUp ? 205 : 90, GAP = 128;
        function slots(list) {
          var want = list.map(function (g) { var a = Object.keys(gp.rec[g].anchors).map(function (id) { return x0 + c.n.indexOf(id) * step; }); return { g: g, x: a.reduce(function (s, v) { return s + v; }, 0) / a.length }; });
          want.sort(function (a, b) { return a.x - b.x; });
          for (var i = 0; i < want.length; i++) if (i && want[i].x < want[i - 1].x + GAP) want[i].x = want[i - 1].x + GAP;
          if (want.length) { var over = want[want.length - 1].x - (W - 60); if (over > 0) { want.forEach(function (w) { w.x -= over; }); for (var j = want.length - 1; j >= 0; j--) if (j < want.length - 1 && want[j].x > want[j + 1].x - GAP) want[j].x = want[j + 1].x - GAP; var lo = 60 - want[0].x; if (lo > 0) { want.forEach(function (w) { w.x += lo; }); W = Math.max(W, want[want.length - 1].x + 60); } } }
          return want;
        }
        var upS = slots(gp.up), dnS = slots(gp.dn);
        c.n.forEach(function (id, i) { sc.nodes[id] = { x: x0 + i * step, y: Y0, k: K, chain: true, lab: "below", w: Math.floor((step - 26) / 8.2), ln: 3, fs: 15 }; });
        upS.forEach(function (w) { sc.nodes[w.g] = { x: w.x, y: Y0 - 168, k: 0.8, ghost: true, lab: "above", w: 17, ln: 2, fs: 12.5 }; });
        dnS.forEach(function (w) { sc.nodes[w.g] = { x: w.x, y: Y0 + 172, k: 0.8, ghost: true, lab: "below", w: 17, ln: 2, fs: 12.5 }; });
        sc.W = W; sc.H = hasDn ? Y0 + 172 + 84 : Y0 + 120;
        if (!hasUp) sc.H = Math.max(sc.H, Y0 + 120);
        c.e.forEach(function (k, i) {
          var a = sc.nodes[c.n[i]], b = sc.nodes[c.n[i + 1]], ea = EXT[N[c.n[i]].t], eb = EXT[N[c.n[i + 1]].t];
          var p0 = [a.x + ea[0] * K + 6, Y0], p3 = [b.x - eb[0] * K - 9, Y0], d = p3[0] - p0[0];
          var s = E[k].s;
          sc.edges.push({ i: k, pts: [p0[0], Y0, p0[0] + d / 3, Y0, p0[0] + 2 * d / 3, Y0, p3[0], Y0], perm: true, sw: 2.4 + s * 0.9, size: s >= 2 ? 4 : 3,
                          lab: { x: (p0[0] + p3[0]) / 2, y: Y0, w: Math.max(14, Math.floor(d / 6.4)), ln: 3, anchor: "middle" } });
        });
        function link(g, up) {
          var gn = sc.nodes[g], eg = EXT[N[g].t];
          var bestK = null, bestV = -1;
          gp.rec[g].edges.forEach(function (k) { var o2 = E[k].f === g ? E[k].t : E[k].f; if (sc.nodes[o2] && sc.nodes[o2].chain && E[k].s > bestV) { bestV = E[k].s; bestK = k; } });
          (bestK == null ? [] : [bestK]).forEach(function (k) {
            var e = E[k], other = e.f === g ? e.t : e.f, an = sc.nodes[other]; if (!an || !an.chain) return;
            var ea2 = EXT[N[other].t], p;
            if (up) p = [gn.x, gn.y + eg[1] * gn.k + 3, gn.x, (gn.y + Y0) / 2 + 4, an.x, (gn.y + Y0) / 2 - 8, an.x, Y0 - ea2[1] * K - 8];
            else p = [an.x + ea2[0] * K * 0.55, Y0 + ea2[1] * K * 0.85, an.x + ea2[0] * K * 0.55 + (gn.x - an.x) * 0.1, Y0 + 70, gn.x, Y0 + 100, gn.x, gn.y - eg[1] * gn.k - 9];
            sc.edges.push({ i: k, pts: p, ghosted: true, sw: 1.4 + e.s * 0.3, size: 2, lab: { x: 0, y: 0, w: 22, ln: 2, anchor: "middle", mid: true } });
          });
        }
        gp.up.forEach(function (g) { link(g, true); }); gp.dn.forEach(function (g) { link(g, false); });
      } else {
        var STEP = 132, xc = 56, H = 56 * 2 + (n - 1) * STEP, W2 = Math.max(r.w, 300), lw = Math.floor((W2 - xc - 56) / 8.4), ew = Math.floor((W2 - xc - 40) / 6.9);
        c.n.forEach(function (id, i) { sc.nodes[id] = { x: xc, y: 56 + i * STEP, k: K, chain: true, lab: "right", w: Math.max(14, lw), ln: 3, fs: 15 }; });
        sc.W = W2; sc.H = H;
        c.e.forEach(function (k, i) {
          var a = sc.nodes[c.n[i]], b = sc.nodes[c.n[i + 1]], ea = EXT[N[c.n[i]].t], eb = EXT[N[c.n[i + 1]].t];
          var y0 = a.y + ea[1] * K + 6, y1 = b.y - eb[1] * K - 10, d = y1 - y0, s = E[k].s;
          sc.edges.push({ i: k, pts: [xc, y0, xc, y0 + d / 3, xc, y0 + 2 * d / 3, xc, y1], perm: true, sw: 2.4 + s * 0.9, size: s >= 2 ? 4 : 3,
                          lab: { x: xc + 16, y: (y0 + y1) / 2, w: Math.max(14, ew), ln: 3, anchor: "start", vert: true } });
        });
      }
      return sc;
    }
    /* ---------- scene: topic / everything (positions precomputed in Python) ---------- */
    function topRank(L, gh, id) {
      if (!L.rk) L.rk = Object.keys(L.p).filter(function (x) { return N[x] && !gh[x]; }).sort(function (a, b) { return HUB[a] - HUB[b]; });
      return L.rk.indexOf(id);
    }
    function buildLayout(key, kind) {
      var narrow = view.clientWidth > 0 && view.clientWidth < 600;
      if (narrow && LAY[key + ":m"]) key = key + ":m"; else narrow = narrow && /:m$/.test(key);
      var L = LAY[key]; if (!L) return null;
      /* fill the pane: wide panes get wider columns, tall panes taller rows (nodes keep their size) */
      var r0 = box2(), gut0 = L.gut || 120, kx = 1, ky = 1;
      if (!narrow && kind !== "full" && view.clientWidth > 300) {
        var asp = Math.max(200, r0.w - 32) / Math.max(200, r0.h - 46), la = L.W / L.H;
        if (asp > la) {
          kx = Math.min(3.2, asp / la);
          if (L.lanes) for (var it = 0; it < 4; it++) { var gk0 = Math.min(1.7, Math.max(1, kx)); kx = Math.max(1, Math.min(3.2, (asp * L.H - gut0 * gk0) / (L.W - gut0))); }
        } else ky = Math.min(1.5, la / asp);
      }
      var gk = (!narrow && L.lanes) ? Math.min(1.7, Math.max(1, kx)) : 1;   /* wide panes also get a wider topic-label gutter */
      function fx(x) { return x <= gut0 ? x : gut0 * gk + (x - gut0) * kx; }
      var W2 = fx(L.W), H2 = L.H * ky;
      var sc = { kind: kind, W: W2, H: H2, nodes: {}, edges: [], lanes: L.lanes ? L.lanes.map(function (l) { return [l[0], l[1] * ky, l[2] * ky]; }) : null, narrow: narrow, gut: gut0 * gk, kx: kx, ky: ky }, gh = {};
      var s0 = Math.min(r0.w / (W2 + 24), r0.h / (H2 + 24)), fsU = Math.min(28, Math.max(13, (narrow ? 10.5 : 12.5) / s0));
      var nc = Math.max(7, Math.floor((((L.cw || 130) * kx) - 10) / (fsU * 0.6))), nHub = narrow ? 8 : 12;
      sc.fsU = fsU; sc.nc = nc;
      (L.g || []).forEach(function (g) { gh[g] = true; });
      var wide = !narrow && (kind === "all" || kind === "full");
      Object.keys(L.p).forEach(function (id) {
        if (!N[id]) return;
        var q = L.p[id], hub = kind === "all" ? HUB[id] < nHub : (kind === "topic" && !gh[id] && topRank(L, gh, id) < (narrow ? 6 : 8));
        sc.nodes[id] = { x: fx(q[0]), y: q[1] * ky, k: gh[id] ? 0.8 : (kind === "all" && !narrow ? 1.2 : 1), ghost: !!gh[id], lab: "below",
          w: kind === "full" ? 14 : kind === "all" ? (narrow ? (hub ? Math.min(nc + 2, 18) : 13) : 18) : (narrow ? nc + 1 : Math.min(17, nc + 2)),
          ln: hub && narrow ? 3 : 2, fs: 0, lo: false, hub: hub, sn: Math.min(13, nc + 2), wide: wide };
      });
      Object.keys(L.e || {}).forEach(function (k) {
        var e = E[+k]; if (!e || !sc.nodes[e.f] || !sc.nodes[e.t]) return;
        var gs = sc.nodes[e.f].ghost || sc.nodes[e.t].ghost, pts = L.e[k].map(function (v, i) { return i % 2 ? v * ky : fx(v); });
        sc.edges.push({ i: +k, pts: pts, ghosted: gs, sw: (kind === "all" || kind === "full") ? (e.weak ? 1 : { 1: 1.5, 2: 2, 3: 3 }[e.s] || 1.5) : 1.3 + e.s * 0.6, size: e.s, lab: { x: 0, y: 0, w: kind === "full" ? 26 : 24, ln: kind === "full" ? 1 : 3, anchor: "middle", mid: true } });
      });
      return sc;
    }
    var NARROW_LANE = ["AI", "Tech", "Finance", "Markets", "Politics", "Conflict", "Prices"];
    function buildScene() {
      if (S.view === "chains" && CH.length) return buildChain();
      if (S.view === "full" && LAY["full"]) return buildLayout("full", "full");
      if (S.view === "topic" && LAY["t:" + S.topic]) return buildLayout("t:" + S.topic, "topic");
      S.view = "all"; return buildLayout("all", "all");
    }

    /* ---------- rendering ---------- */
    function mkNode(id, o) {
      var n = N[id], ext = EXT[n.t], k = o.k, hh = ext[1] * k, hw = ext[0] * k, dc = dcls(n.d);
      var g = sv("g", { "class": "nd" + (o.ghost ? " ghost" : "") + (o.lo ? " lo" : ""), tabindex: o.ghost && SC.kind === "chain" ? "-1" : "0", role: "button", "data-id": id, transform: "translate(" + r1(o.x) + " " + r1(o.y) + ")" });
      var meta = (data.typeLabel[n.t] || n.t) + (n.dn ? ", " + n.dn : "") + (n.r ? ", " + n.r : "");
      g.setAttribute("aria-label", n.l + ", " + meta + (o.ghost ? ". Linked node outside this view." : "") + ". Show connections.");
      var lines = wrap(n.l, o.w, o.ln), fsz = o.fs ? o.fs : 13; g._lines = lines;
      g.appendChild(sv("rect", { "class": "tap", x: -Math.max(hw + 8, 58), y: -hh - 8, width: Math.max(hw + 8, 58) * 2, height: hh * 2 + 16 + (o.lab === "right" ? 0 : lines.length * fsz * 1.2 + 6) }));
      g.appendChild(sv("path", { "class": "shape " + dc, d: SH[n.t], transform: "scale(" + k + ")" }));
      g.appendChild(sv("path", { "class": "ring", d: SH[n.t], transform: "scale(" + r1(k * 1.3) + ")" }));
      if (n.dir === "up" || n.dir === "down" || n.dir === "flat") {
        var bx = hw * 0.8, by = -hh * 0.8, d = n.dir === "up" ? "M" + bx + " " + (by - 7.5) + "L" + (bx + 6.5) + " " + (by + 5) + "L" + (bx - 6.5) + " " + (by + 5) + "Z"
          : n.dir === "down" ? "M" + bx + " " + (by + 7.5) + "L" + (bx + 6.5) + " " + (by - 5) + "L" + (bx - 6.5) + " " + (by - 5) + "Z" : "M" + (bx - 6.5) + " " + (by - 2.8) + "H" + (bx + 6.5) + "V" + (by + 2.8) + "H" + (bx - 6.5) + "Z";
        g.appendChild(sv("path", { "class": "dirb " + n.dir, d: d }));
      }
      var t = sv("text", null, "nlabel " + (o.lab === "right" ? "start" : "mid") + (o.hub ? " hub" : ""));
      if (o.fs) t.setAttribute("style", "font-size:" + o.fs + "px");
      var emH = o.lab === "above" ? -(hh + 6 + (lines.length - 1) * 1.12 * fsz) : o.lab === "right" ? -((lines.length - 1) * 0.56 * fsz) - fsz * 0.35 : hh + 3;
      var x = o.lab === "right" ? hw + 12 : 0;
      lines.forEach(function (s, i) {
        var ts = sv("tspan", { x: x, dy: i ? "1.12em" : (o.lab === "above" ? "0" : "1em") }); ts.textContent = s; t.appendChild(ts);
      });
      t.setAttribute("y", r1(o.lab === "above" ? emH : o.lab === "right" ? emH + fsz * 0.9 : emH));
      g.appendChild(t);
      if (o.short) {
        t.setAttribute("class", t.getAttribute("class") + " ln");
        var t0 = sv("text", { "class": "nlabel mid sh", y: r1(hh + 3) });
        wrap(n.l, o.sn || 12, SC.narrow ? 1 : 2).forEach(function (s0, i0) { var ts0 = sv("tspan", { x: 0, dy: i0 ? "1.12em" : "1em" }); ts0.textContent = s0; t0.appendChild(ts0); }); g.appendChild(t0);
      }
      return g;
    }
    function mkEdge(o) {
      var e = E[o.i], eff = e.effect, con = e.kind === "contradicts", p = o.pts;
      var eo = { 3: 0.5, 2: 0.34, 1: 0.2 }[conf(e)];
      if (SC.kind === "chain") eo = { 3: 1, 2: 0.85, 1: 0.7 }[conf(e)]; else if (SC.kind === "full" || SC.kind === "all") eo = e.weak ? 0.4 : 0.78; else eo = { 3: 0.9, 2: 0.7, 1: 0.5 }[conf(e)];
      var g = sv("g", { "class": "ed e-" + eff + " k-" + e.kind + " st-" + e.status + (o.perm ? " perm" : "") + (o.ghosted ? " ghosted" : ""), "data-i": o.i });
      var swc = SC.kind === "chain" && conf(e) === 1 ? o.sw * 0.8 : o.sw;
      g.setAttribute("style", "--eo:" + (o.perm ? 1 : eo) + ";--sw:" + r1(swc) + "px;--sw2:" + r1(swc + 1.4) + "px");
      var d = pathD(p), line = sv("path", { "class": "line", d: d, "marker-end": "url(#mk" + UID + "-" + (con ? "bar" : eff) + (o.size || e.s) + ")" });
      var tt = sv("title"); tt.textContent = N[e.f].l + " to " + N[e.t].l + ": " + (e.label || ""); line.appendChild(tt);
      g.appendChild(line); g.appendChild(sv("path", { "class": "hit", d: d }));
      var lb = o.lab, ptxt = SC.kind === "chain" && !o.ghosted ? (con ? "≠ contradicts · " : eff === "up" ? "▲ raises · " : eff === "down" ? "▼ lowers · " : eff === "mixed" ? "◆ mixed · " : "") : "";
      var lines = wrap(ptxt + (e.label || ""), lb.w + ptxt.length, lb.ln), t = sv("text", null, "elabel");
      var pos = lb.mid ? bez(p, 0.5) : [lb.x, lb.y], lh = 14.5, y0;
      if (lb.vert) { y0 = pos[1] - (lines.length * lh) / 2 + 11; }
      else if (lb.mid) { y0 = pos[1] - 8 - (lines.length - 1) * lh; }
      else { y0 = pos[1] - 13 - (lines.length - 1) * lh; }
      t.setAttribute("text-anchor", lb.anchor); t.setAttribute("y", r1(y0)); t.setAttribute("x", r1(pos[0]));
      lines.forEach(function (s, i) { var ts = sv("tspan", { x: r1(pos[0]), dy: i ? lh : 0 }); ts.textContent = s; t.appendChild(ts); });
      g.appendChild(t);
      if (o.perm && (lb.vert || SC.kind !== 'chain')) {
        var sub = [FX[eff] && eff !== "none" ? FX[eff][2] : "", e.status, e.conf ? e.conf + " confidence" : "", e.lag].filter(Boolean).join(" · ");
        var t2 = sv("text", null, "elabel sub"); t2.setAttribute("text-anchor", lb.anchor);
        if (lb.vert) { t2.setAttribute("x", r1(pos[0])); t2.setAttribute("y", r1(y0 + lines.length * lh + 1)); t2.textContent = wrap(sub, lb.w, 2).join(" "); }
        else { t2.setAttribute("x", r1(pos[0])); t2.setAttribute("y", r1(pos[1] + 20)); t2.textContent = sub; }
        g.appendChild(t2);
      }
      return g;
    }
    function render() {
      gScene.textContent = ""; nodeEl = {}; edgeEl = {}; laneEl = {}; LR = {}; AXR = null; lastLK = "";
      svg.setAttribute("class", "scn-" + SC.kind + (SC.vertical ? " vert" : ""));
      if (!svg.getAttribute("data-al")) svg.setAttribute("data-al", svg.getAttribute("aria-label") || "");
      if (SC.kind === "chain") { var cc = CH[S.chain]; svg.setAttribute("aria-label", cc.title + ": " + cc.n.map(function (id) { return N[id].l; }).join(", then ") + ". Select a node for details."); } else svg.setAttribute("aria-label", svg.getAttribute("data-al"));
      var gl = sv("g", null, "lanes"), ge = sv("g", null, "edges"), gn = sv("g", null, "nodes");
      if (SC.lanes) {
        var cnt = {}; ids.forEach(function (id) { var d = N[id].d; cnt[d] = (cnt[d] || 0) + 1; });
        SC.lanes.forEach(function (ln) {
          var d = ln[0], top = ln[1], h = ln[2], c = dcls(d), g = sv("g", { "class": "lg", "data-d": d });
          g.appendChild(sv("rect", { "class": "lane " + c, x: 6, y: top, width: SC.W - 12, height: h, rx: 8 }));
          g.appendChild(sv("rect", { "class": "ltag " + c, x: 6, y: top, width: 5, height: h, rx: 2 }));
          var name = d >= 0 ? data.doms[d] : "Other", t = sv("text", null, "lanelabel"), tc = sv("text", { "class": "lanecount" }), lf = Math.round(SC.fsU * 1.02), ts0 = SC.narrow ? null : sv("text", { "class": "lanestory" });
          if (SC.narrow) name = d >= 0 ? NARROW_LANE[d] : "Other";   /* phone: short topic names */
          g.appendChild(t); g.appendChild(tc); if (ts0) g.appendChild(ts0);
          LR[d] = { t: t, tc: tc, ts: ts0, story: (data.ltop && data.ltop[String(d)]) || "", name: name, top: top, h: h, x: SC.narrow ? 16 : 22, base: SC.narrow ? lf : 16, cbase: SC.narrow ? Math.round(lf * 0.85) : 14, ctxt: SC.narrow ? String(cnt[d]) : cnt[d] + (cnt[d] === 1 ? " node" : " nodes") };
          gl.appendChild(g); laneEl[d] = g;
        });
        var ax = sv("g", null, "axisg"), x1 = SC.gut + 48, x2 = SC.W - 20;
        var t1 = sv("text", { "class": "axis", x: x1 - 20, y: 18 }); t1.textContent = "Causes"; ax.appendChild(t1);
        var t2 = sv("text", { "class": "axis", x: x2, y: 18, "text-anchor": "end" }); t2.textContent = "Outcomes"; ax.appendChild(t2);
        var al = sv("path", { "class": "axisln", fill: "none" }); ax.appendChild(al);
        AXR = { t1: t1, t2: t2, ln: al, x1: x1 - 20, x2: x2, base: SC.narrow ? Math.round(SC.fsU * 0.8) : 14, top: Math.min.apply(null, SC.lanes.map(function (l) { return l[1]; })) };
        gl.appendChild(ax);
      }
      SC.edges.slice().sort(function (a, b) { return E[a.i].s - E[b.i].s; }).forEach(function (o) { var g = mkEdge(o); ge.appendChild(g); edgeEl[o.i] = g; });
      Object.keys(SC.nodes).forEach(function (id) {
        var o = SC.nodes[id]; o.id = id;
        o.lo = false; o.short = false;
        var g = mkNode(id, o); gn.appendChild(g); nodeEl[id] = g; wireNode(g, id);
      });
      gScene.appendChild(gl); gScene.appendChild(ge); gScene.appendChild(gn);
      Object.keys(edgeEl).forEach(function (i) { wireEdge(edgeEl[i], +i); });
    }

    /* ---------- viewport ---------- */
    var vb = { x: 0, y: 0, w: 800, h: 400 }, fitS = 1, LR = {}, AXR = null, lastLK = "", futG = null, futS = 1;
    /* Label floor: SVG text scales with the zoom, so every map label is sized in screen pixels. Hubs, lane and axis
       labels are counter-scaled to stay >= MIN px on screen; other node labels keep their natural size and fade out
       while that would be < 11px. Overlaps between visible node labels drop the lower-priority one. */
    function setLabel(t, lines, above, hh, fs) {
      t.textContent = "";
      lines.forEach(function (ln, i) { var ts = sv("tspan", { x: 0, dy: i ? "1.12em" : (above ? "0" : "1em") }); ts.textContent = ln; t.appendChild(ts); });
      t.setAttribute("y", r1(above ? -(hh + 6 + (lines.length - 1) * 1.12 * fs) : hh + 3));
    }
    function layoutLabels(s, force) {
      if (!SC || SC.kind === "chain") return;
      var key = s.toFixed(3) + "|" + SC.kind + "|" + (S.sel || "") + "|" + (S.lens || ""); if (!force && key === lastLK) return; lastLK = key;
      var MIN = 11.5, FLOOR = 11, wideAll = !SC.narrow && (SC.kind === "all" || SC.kind === "full");
      Object.keys(LR).forEach(function (d) {
        var q = LR[d], fs = Math.max(q.base, MIN / s), cf = Math.max(q.cbase, MIN / s), avail = SC.gut - q.x - 8;
        var lines, used, longest = q.name.split(/\s+/).reduce(function (m, w) { return Math.max(m, w.length); }, 3);
        for (var pass = 0; pass < 3; pass++) {
          lines = wrap(q.name, Math.max(longest, Math.floor(avail / (fs * 0.56))), 4); used = 6 + fs + (lines.length - 1) * fs * 1.12;
          var wid = lines.reduce(function (m, l) { return Math.max(m, l.length); }, 0) * fs * 0.58;
          if (wid <= avail || fs <= 11.2 / s) break;
          fs = Math.max(11.2 / s, fs * avail / wid); cf = Math.min(cf, fs);
        }
        var showC = used + cf * 1.4 + 4 <= q.h, showL = used + 4 <= q.h;
        q.t.textContent = ""; q.t.setAttribute("style", "font-size:" + r1(fs) + "px");
        lines.forEach(function (ln, i) { var ts = sv("tspan", { x: q.x, y: r1(q.top + 6 + fs + i * fs * 1.12) }); ts.textContent = ln; q.t.appendChild(ts); });
        q.tc.textContent = q.ctxt; q.tc.setAttribute("x", q.x); q.tc.setAttribute("y", r1(q.top + used + cf * 1.3)); q.tc.setAttribute("style", "font-size:" + r1(cf) + "px");
        q.t.classList.toggle("lh", !showL); q.tc.classList.toggle("lh", !(showL && showC));
        /* one-line top story under the count (phone: hidden) */
        if (q.ts) {
          var sfs = Math.max(11.2 / s, cf), top2 = q.top + used + cf * 1.3 + 5, room = Math.floor((q.top + q.h - 5 - top2) / (sfs * 1.14)), sl = (q.story && room >= 1) ? wrap(q.story, Math.max(8, Math.floor(avail / (sfs * 0.52))), Math.min(4, room)) : [];
          q.ts.textContent = ""; q.ts.setAttribute("style", "font-size:" + r1(sfs) + "px");
          sl.forEach(function (ln, i) { var ts = sv("tspan", { x: q.x, y: r1(top2 + sfs + i * sfs * 1.14) }); ts.textContent = ln; q.ts.appendChild(ts); });
          q.ts.classList.toggle("lh", !(showL && showC && room >= 1 && q.story));
        }
      });
      if (AXR) {
        var fa = Math.max(AXR.base, MIN / s), ok = fa + 4 <= AXR.top, yb = r1(fa * 0.95 + 1), w1 = 6 * fa * 0.58, w2 = 8 * fa * 0.58, xa = AXR.x1 + w1 + 10, xb = AXR.x2 - w2 - 10, h = fa * 0.3;
        [AXR.t1, AXR.t2].forEach(function (t) { t.setAttribute("y", yb); t.setAttribute("style", "font-size:" + r1(fa) + "px"); t.classList.toggle("lh", !ok); });
        AXR.ln.setAttribute("d", xb - xa > 30 ? "M" + r1(xa) + " " + r1(yb - h) + "H" + r1(xb) + "M" + r1(xb - h * 1.4) + " " + r1(yb - h * 2) + "L" + r1(xb) + " " + r1(yb - h) + "L" + r1(xb - h * 1.4) + " " + r1(yb) : "");
        AXR.ln.classList.toggle("lh", !ok);
      }
      var items = [], bodies = [];
      Object.keys(SC.nodes).forEach(function (id) {
        var g = nodeEl[id], o = SC.nodes[id], t = g && g.querySelector("text.nlabel:not(.sh)"); if (!t || g.classList.contains("hid")) return;
        var ex = EXT[N[id].t], hw = ex[0] * o.k, hh = ex[1] * o.k;
        bodies.push({ id: id, x0: o.x - hw - 2, x1: o.x + hw + 2, y0: o.y - hh - 2, y1: o.y + hh + 2 });
        var m = g.classList.contains("m"), pin = o.hub || id === S.sel || m, fs = (pin || wideAll) ? Math.max(13, MIN / s) : 13;
        t.style.fontSize = r1(fs) + "px";
        var pr = id === S.sel ? -2 : m ? -1 : o.hub ? (HUB[id] || 0) / 100 : 1 + (HUB[id] || 0) / 1000;
        items.push({ id: id, t: t, o: o, g: g, pin: pin, fs: fs, hh: hh, pr: pr, ok: pin || wideAll || fs * s >= FLOOR });
      });
      items.sort(function (a, b) { return a.pr - b.pr; });
      var taken = [], pad = 3 / s;
      function hits(r, own) {
        for (var i = 0; i < taken.length; i++) { var c = taken[i]; if (r.x0 < c.x1 && r.x1 > c.x0 && r.y0 < c.y1 && r.y1 > c.y0) return true; }
        for (var j = 0; j < bodies.length; j++) { var c2 = bodies[j]; if (c2.id !== own && r.x0 < c2.x1 && r.x1 > c2.x0 && r.y0 < c2.y1 && r.y1 > c2.y0) return true; }
        return false;
      }
      function rectOf(it) { var b = it.t.getBBox(); return { x0: it.o.x + b.x - pad, x1: it.o.x + b.x + b.width + pad, y0: it.o.y + b.y - pad, y1: it.o.y + b.y + b.height + pad }; }
      items.forEach(function (it) {
        var vis = it.ok;
        if (vis && wideAll && it.g._lines) {
          /* every name stays visible: full 2-line label, else a shortened one, else above the node */
          var nm = N[it.id].l, v0 = it.g._lines, v1 = wrap(nm, 14, 1), v2 = wrap(nm, 9, 1), v3 = wrap(nm, 12, 3), tries = [[v0, false], [v0, true], [v3, false], [v3, true], [v1, false], [v1, true], [v2, false], [v2, true]], done = false, last = null;
          for (var k = 0; k < tries.length && !done; k++) {
            setLabel(it.t, tries[k][0], tries[k][1], it.hh, it.fs); var r = rectOf(it); last = r;
            if (!hits(r, it.id)) { taken.push(r); done = true; }
          }
          if (!done) { setLabel(it.t, v2, false, it.hh, it.fs); taken.push(rectOf(it)); }
          it.t.classList.remove("lh"); return;
        }
        if (vis) {
          var b = it.t.getBBox(), r2 = { x0: it.o.x + b.x - pad, x1: it.o.x + b.x + b.width + pad, y0: it.o.y + b.y - pad, y1: it.o.y + b.y + b.height + pad };
          for (var i = 0; i < taken.length; i++) { var c = taken[i]; if (r2.x0 < c.x1 && r2.x1 > c.x0 && r2.y0 < c.y1 && r2.y1 > c.y0) { vis = false; break; } }
          if (vis) taken.push(r2);
        }
        it.t.classList.toggle("lh", !vis);
      });
    }
    function box2() { var r = view.getBoundingClientRect(); return { w: Math.max(r.width, 50), h: Math.max(r.height, 50), l: r.left, t: r.top }; }
    function setVB() {
      var r = box2(); vb.h = vb.w * r.h / r.w;
      svg.setAttribute("viewBox", vb.x.toFixed(1) + " " + vb.y.toFixed(1) + " " + vb.w.toFixed(1) + " " + vb.h.toFixed(1));
      var s = r.w / vb.w;
      svg.classList.toggle("zl", s >= 0.9);
      if (SC && SC.kind !== "chain") { var f = Math.max(13, 11.5 / s); svg.style.setProperty("--efs", f.toFixed(1) + "px"); layoutLabels(s, false); } else { svg.style.removeProperty("--nfs"); svg.style.removeProperty("--efs"); }
      if (SC && futG) { if (Math.abs(s / futS - 1) > 0.12) placeFutures(s); else futG._items.forEach(function (it) { it.g.setAttribute("transform", "translate(" + it.x + " " + it.y + ") scale(" + (1 / s).toFixed(4) + ")"); }); }
    }
    function clampVB() {
      var m = 90;
      vb.x = Math.min(Math.max(vb.x, -m - vb.w * 0.6), SC.W + m - vb.w * 0.4);
      vb.y = Math.min(Math.max(vb.y, -m - vb.h * 0.6), SC.H + m - vb.h * 0.4);
    }
    function fit(top) {
      var r = box2(), s;
      if (SC.kind === "chain") {
        var pad = 8; s = Math.min(r.w / (SC.W + 2 * pad), r.h / (SC.H + 2 * pad), 1.15);
        vb.w = r.w / s; vb.h = r.h / s; vb.x = SC.W / 2 - vb.w / 2; vb.y = SC.H / 2 - vb.h / 2;
        if (SC.narrow && vb.h > SC.H + 2 * pad) vb.y = -pad;
      } else {
        /* padding in screen px so counter-scaled hub labels (up to 3 lines below a node, ~half a label either side) are never clipped */
        var pt = SC.narrow ? 14 : 6, pb = SC.narrow ? 62 : 40, ps = SC.narrow ? 34 : 16;
        s = Math.min((r.w - 2 * ps) / SC.W, (r.h - pt - pb) / SC.H);
        if (SC.kind === "full" && !top) { s = Math.min(1.1, (r.w - 2 * ps) / SC.W); vb.w = r.w / s; vb.h = r.h / s; vb.x = SC.W / 2 - vb.w / 2; vb.y = -(SC.narrow ? 30 : 26) / s; fitS = Math.min(s, (r.h - pt - pb) / SC.H); setVB(); return; }
        vb.w = r.w / s; vb.h = r.h / s; vb.x = SC.W / 2 - vb.w / 2;
        var extra = Math.max(0, r.h - pt - pb - SC.H * s);
        vb.y = -(pt + (SC.narrow ? 0 : extra / 3)) / s;   /* phone: map hugs the top, free space below keeps the zoom buttons clear */
      }
      fitS = s; setVB();
    }
    function zoomAt(k, cx, cy) {
      var r = box2(), maxW = r.w / (fitS * 0.7), minW = r.w / 4;
      var nw = Math.min(maxW, Math.max(minW, vb.w / k));
      var px = (cx - r.l) / r.w, py = (cy - r.t) / r.h, wx = vb.x + px * vb.w, wy = vb.y + py * vb.h;
      vb.w = nw; vb.h = nw * r.h / r.w; vb.x = wx - px * vb.w; vb.y = wy - py * vb.h; clampVB(); setVB();
    }
    function zoomCenter(k) { var r = box2(); zoomAt(k, r.l + r.w / 2, r.t + r.h / 2); }
    function panBy(fx, fy) { vb.x += fx * vb.w; vb.y += fy * vb.h; clampVB(); setVB(); }
    function centerOn(id) { var n = SC.nodes[id]; if (!n) return; vb.x = n.x - vb.w / 2; vb.y = n.y - vb.h / 2; clampVB(); setVB(); }
    function ensureVisible(id) {
      var n = SC.nodes[id]; if (!n) return; var m = 60;
      if (n.x < vb.x + m || n.x > vb.x + vb.w - m || n.y < vb.y + m || n.y > vb.y + vb.h - m - 20) centerOn(id);
    }
    var ptrs = {}, drag = null, pinch = null, moved = false, suppress = false;
    view.addEventListener("pointerdown", function (ev) {
      if (ev.button > 0) return;
      box.classList.add("act");
      ptrs[ev.pointerId] = { x: ev.clientX, y: ev.clientY };
      var pids = Object.keys(ptrs); moved = false;
      if (pids.length === 1) drag = { x: ev.clientX, y: ev.clientY, vx: vb.x, vy: vb.y, id: ev.pointerId };
      else if (pids.length === 2) {
        var a = ptrs[pids[0]], b = ptrs[pids[1]];
        pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, w: vb.w }; drag = null; moved = true;
      }
    });
    view.addEventListener("pointermove", function (ev) {
      var p = ptrs[ev.pointerId]; if (!p) return;
      p.x = ev.clientX; p.y = ev.clientY;
      var r = box2();
      if (pinch) {
        var pids = Object.keys(ptrs); if (pids.length < 2) return;
        var a = ptrs[pids[0]], b = ptrs[pids[1]], d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
        zoomAt(vb.w / (pinch.w * pinch.d / d), (a.x + b.x) / 2, (a.y + b.y) / 2);
      } else if (drag && drag.id === ev.pointerId) {
        var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
        if (!moved && Math.hypot(dx, dy) > 4) { moved = true; view.classList.add("drag"); hideTip(); try { view.setPointerCapture(ev.pointerId); } catch (x) {} }
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
      var dy = ev.deltaMode === 1 ? ev.deltaY * 16 : ev.deltaY; dy = Math.max(-120, Math.min(120, dy));
      zoomAt(Math.exp(-dy * (ev.ctrlKey ? 0.008 : 0.0016)), ev.clientX, ev.clientY);
    }, { passive: false });
    [].forEach.call(box.querySelectorAll(".czoom button"), function (b) {
      b.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); });
      b.addEventListener("click", function () { var a = b.getAttribute("data-act"); if (a === "in") zoomCenter(1.4); else if (a === "out") zoomCenter(1 / 1.4); else fit(true); });
    });
    var lastW = 0, lastV = false;
    function onResize() {
      var w = view.clientWidth; if (!w) return;
      if (S.view === "chains" && CH.length && SC && SC.kind === "chain") {
        if (Math.abs(w - lastW) > 8) { lastW = w; var keep = S.sel; SC = buildScene(); sizeView(); render(); applyAll(); fit(); if (keep && nodeEl[keep]) { } }
      } else {
        lastW = w;
        if (SC && SC.kind !== "chain" && (w < 600) !== !!SC.narrow && LAY[(SC.kind === "all" ? "all" : SC.kind === "full" ? "full" : "t:" + S.topic) + ":m"]) { rebuild(); return; }
        if (SC) setVB();
      }
    }
    if (window.ResizeObserver) new ResizeObserver(onResize).observe(view); else window.addEventListener("resize", onResize);

    /* ---------- tooltip + hover ---------- */
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
        t.appendChild(el("b", "", (N[e.f] ? N[e.f].l : "?") + " → " + (N[e.t] ? N[e.t].l : "?")));
        t.appendChild(document.createElement("br")); t.appendChild(document.createTextNode(e.label || "(no mechanism given)"));
        var bits = [FX[e.effect] ? FX[e.effect][2] : "", e.kind === "causal" ? "" : e.kind, "strength " + e.s + "/3", (e.conf || "?") + " confidence", e.status || "reported", e.lag ? "lag: " + e.lag : "", e.order > 1 ? "step " + e.order : ""];
        t.appendChild(el("span", "tm", bits.filter(Boolean).join(" · ")));
        if (e.why && e.why !== e.label) t.appendChild(el("span", "tm", e.why));
      };
    }
    function wireEdge(g, i) {
      var hit = g.querySelector(".hit");
      hit.addEventListener("mousemove", function (ev) { g.classList.add("hv"); showTip(edgeTip(i), ev); });
      hit.addEventListener("mouseleave", function () { g.classList.remove("hv"); hideTip(); });
      hit.addEventListener("click", function (ev) { ev.stopPropagation(); hideTip(); g.classList.remove("hv"); showPop(edgeContent(i), ev); });
    }
    function hover(id) {
      if (hoverId === id) return;
      clearHover(); if (!id || !nodeEl[id]) return;
      hoverId = id; svg.classList.add("hov"); nodeEl[id].classList.add("hh");
      outE[id].concat(inE[id]).forEach(function (i) {
        if (!edgeEl[i] || !edgeOk(i)) return; edgeEl[i].classList.add("hh");
        var o = E[i].f === id ? E[i].t : E[i].f; if (nodeEl[o]) nodeEl[o].classList.add("hov1");
      });
    }
    function clearHover() {
      if (!hoverId) return; hoverId = null; svg.classList.remove("hov");
      [].forEach.call(svg.querySelectorAll(".hh,.hov1"), function (n) { n.classList.remove("hh"); n.classList.remove("hov1"); });
    }

    /* ---------- filters (Everything view) ---------- */
    function filtersOn() { return SC && (SC.kind === "all" || SC.kind === "full"); }
    function nodeOk(id) { var n = N[id]; return n && (!filtersOn() || (!F.hideDom[n.d] && (!F.region || n.r === F.region))); }
    function edgeOk(i) {
      var e = E[i]; if (!nodeOk(e.f) || !nodeOk(e.t)) return false;
      return !filtersOn() || (conf(e) >= F.conf && (e.order || 1) <= F.order && (!F.status || (e.status || "reported") === F.status));
    }
    function chainsOf(id) {
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
    var chain = null;
    function litSet(K) { var set = {}; ids.forEach(function (id) { var c = N[id].c; if ((c && c.cur && c.cur[K]) || (MKIDS[K] && MKIDS[K].indexOf(id) >= 0)) set[id] = true; }); return set; }
    function applyAll() {
      var nv = 0, ev2 = 0, total = ids.length;
      Object.keys(nodeEl).forEach(function (id) { var ok = nodeOk(id); nodeEl[id].classList.toggle("hid", !ok); if (ok) nv++; });
      Object.keys(edgeEl).forEach(function (i) { var ok = edgeOk(+i); edgeEl[i].classList.toggle("hid", !ok); if (ok) ev2++; });
      Object.keys(laneEl).forEach(function (d) { laneEl[d].classList.toggle("hid", !!F.hideDom[d]); });
      if (S.sel && !nodeOk(S.sel)) S.sel = null;
      chain = S.sel ? chainsOf(S.sel) : null;
      var multi = SC.kind === "chain" ? (S.sel ? (function () { var h = { nodes: {}, edges: {} }; h.nodes[S.sel] = 0; outE[S.sel].concat(inE[S.sel]).forEach(function (i) { h.edges[i] = true; var o = E[i].f === S.sel ? E[i].t : E[i].f; h.nodes[o] = 1; }); return h; })() : null) : chain;
      var tr = S.trace && S.trace.nodes.length ? S.trace : null, trN = {}, trE = {};
      if (tr) { tr.nodes.forEach(function (x) { trN[x] = true; }); tr.edges.forEach(function (x) { trE[x] = true; }); }
      svg.classList.toggle("focus", !!multi && !tr);
      svg.classList.toggle("tracing", !!tr);
      var lit = S.lens ? litSet(S.lens) : null; svg.classList.toggle("lens", !!lit);
      var nHl = 0;
      Object.keys(nodeEl).forEach(function (id) { var n2 = nodeEl[id]; n2.classList.toggle("hl", !!(multi && multi.nodes[id] != null)); n2.classList.toggle("sel", id === S.sel); n2.classList.toggle("tr", !!trN[id]); n2.classList.toggle("lit", !!(lit && lit[id])); });
      Object.keys(edgeEl).forEach(function (i) { var on = !!(multi && multi.edges[i]), e = E[i]; edgeEl[i].classList.toggle("hl", on); edgeEl[i].classList.toggle("tr", !!trE[i]); edgeEl[i].classList.toggle("lit", !!(lit && lit[e.f] && lit[e.t])); if (on) nHl++; });
      Object.keys(edgeEl).forEach(function (i) { edgeEl[i].classList.toggle("lab", nHl > 0 && nHl <= 5 && edgeEl[i].classList.contains("hl")); });
      var q = F.q.trim().toLowerCase(), any = false;
      Object.keys(nodeEl).forEach(function (id) {
        var m = !!q && matches(id, q); nodeEl[id].classList.toggle("m", m); if (m) any = true;
      });
      svg.classList.toggle("searching", !!q && any && !multi);
      var cnt = box.querySelector(".ccount");
      if (cnt) cnt.textContent = filtersOn() ? nv + " of " + total + " nodes · " + ev2 + " of " + E.length + " links" : "";
      var act = F.region || F.conf > 1 || F.order < 3 || F.status || Object.keys(F.hideDom).some(function (k) { return F.hideDom[k]; });
      var cl = box.querySelector(".cclear"); if (cl) cl.hidden = !act;
      if (!S.focus) { layoutLabels(box2().w / vb.w, true); placeFutures(box2().w / vb.w); }
    }
    /* predicted ripple on the main map: faint dotted arrows to small "future" ghost nodes beside the selected node */
    function placeFutures(s) {
      if (futG && futG.parentNode) futG.parentNode.removeChild(futG); futG = null; futS = s;
      if (!S.showPred || !S.sel || S.focus || !SC || !s || !isFinite(s)) return;
      var o = SC.nodes[S.sel], g0 = nodeEl[S.sel]; if (!o || !g0 || g0.classList.contains("hid")) return;
      var preds = predsOf(S.sel, cardOf(S.sel)).filter(function (p) { return p.src === "card"; }).slice(0, 3); if (!preds.length) return;
      var others = [];
      Object.keys(SC.nodes).forEach(function (id) {
        if (id === S.sel || !nodeEl[id] || nodeEl[id].classList.contains("hid")) return; var n2 = SC.nodes[id], ex = EXT[N[id].t], t = nodeEl[id].querySelector("text.nlabel:not(.sh)");
        var dx = (n2.x - o.x) * s, dy = (n2.y - o.y) * s; others.push([dx - ex[0] * s * n2.k - 4, dy - ex[1] * s * n2.k - 4, dx + ex[0] * s * n2.k + 4, dy + ex[1] * s * n2.k + 4]);
        if (t && !t.classList.contains("lh")) others.push([dx - 46, dy + ex[1] * s * n2.k, dx + 46, dy + ex[1] * s * n2.k + 30]);
      });
      var r = box2(), cx = (o.x - vb.x) * s, cy = (o.y - vb.y) * s, placed = [], angs = [-18, 18, 52, -52, 86, -86, 122, -122, 160, 180], rads = [86, 118];
      futG = sv("g", { "class": "futs" }); futG._items = [];
      preds.forEach(function (p, j) {
        var best = null, bp = 1e9, lw = 124;
        rads.forEach(function (R) { angs.forEach(function (A, ai) {
          var a = A * Math.PI / 180, gx = Math.cos(a) * R, gy = Math.sin(a) * R, right = Math.cos(a) >= -0.3, rc = [right ? gx + 6 : gx - 6 - lw, gy - 15, right ? gx + 6 + lw : gx - 6, gy + 17], pen = ai * 0.6 + R * 0.02;
          others.concat(placed).forEach(function (b) { if (rc[0] < b[2] && rc[2] > b[0] && rc[1] < b[3] && rc[3] > b[1]) pen += 10; });
          if (cx + rc[0] < 4 || cx + rc[2] > r.w - 4 || cy + rc[1] < 4 || cy + rc[3] > r.h - 4) pen += 25;
          if (pen < bp) { bp = pen; best = { gx: gx, gy: gy, right: right, rc: rc, a: a }; }
        }); });
        placed.push(best.rc);
        var G = sv("g", { "class": "ft", tabindex: "0", role: "button", "aria-label": "Predicted: " + (p.then || "") + ", " + (p.odds || "unknown") + " odds" });
        var r0 = Math.max(EXT[N[S.sel].t][0], EXT[N[S.sel].t][1]) * s * o.k + 5, ca = Math.cos(best.a), sa = Math.sin(best.a), mx = (r0 * ca + best.gx - ca * 9) / 2 - sa * 8, my = (r0 * sa + best.gy - sa * 9) / 2 + ca * 8;
        G.appendChild(sv("path", { "class": "ft-ln", d: "M" + r1(r0 * ca) + " " + r1(r0 * sa) + "Q" + r1(mx) + " " + r1(my) + " " + r1(best.gx - ca * 9) + " " + r1(best.gy - sa * 9), "marker-end": "url(#mk" + UID + "-none2)" }));
        G.appendChild(sv("circle", { "class": "ft-dot", cx: r1(best.gx), cy: r1(best.gy), r: 6.5 }));
        var t = sv("text", { "class": "ftl", "text-anchor": best.right ? "start" : "end" }), lines = wrap(p.then || "", 20, 2), tx = best.gx + (best.right ? 12 : -12);
        lines.forEach(function (ln, i) { var ts = sv("tspan", { x: r1(tx), y: r1(best.gy - 2 + i * 13.5) }); ts.textContent = ln; t.appendChild(ts); });
        var t2 = sv("tspan", { x: r1(tx), y: r1(best.gy - 2 + lines.length * 13.5), "class": "fto" }); t2.textContent = (p.odds || "?") + " odds"; t.appendChild(t2);
        G.appendChild(t);
        var tt = sv("title"); tt.textContent = "Could happen: " + (p.then || "") + " (" + (p.odds || "?") + " odds)"; G.appendChild(tt);
        G.addEventListener("click", function (ev) { ev.stopPropagation(); showPop(predContent(p), ev); });
        G.addEventListener("keydown", function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ev.stopPropagation(); var b = G.getBoundingClientRect(); showPop(predContent(p), { clientX: b.right, clientY: b.top }); } });
        G.setAttribute("transform", "translate(" + r1(o.x) + " " + r1(o.y) + ") scale(" + (1 / s).toFixed(4) + ")");
        futG._items.push({ g: G, x: r1(o.x), y: r1(o.y) }); futG.appendChild(G);
      });
      gScene.insertBefore(futG, gScene.lastChild);   /* under the nodes so a ghost dot can never swallow a node's tap */
    }
    function matches(id, q) { var n = N[id]; return (n.l + " " + n.dn + " " + n.r + " " + (data.typeLabel[n.t] || "")).toLowerCase().indexOf(q) >= 0; }

    function badge(st) { var b = el("span", "bdg " + st, st === "mixed" ? "Reported + predicted" : st === "predicted" ? "Predicted" : "Reported"); if (st !== "reported") b.title = "Dashed = forecast, not yet reported"; return b; }
    /* ---------- focus view: points | currency | seen before  +  causes, hub, effects, then, could happen ---------- */
    var CURS = ["INR", "CAD", "USD"];
    var CURNAME = { INR: "Indian rupee", CAD: "Canadian dollar", USD: "US dollar" };
    var CURMK = { INR: ["usd/inr"], CAD: ["usd/cad"], USD: ["us dollar index", "dollar index", "dxy", "usd index"] };
    var MKTRX = { INR: /usd\s*\/\s*inr|rupee/i, CAD: /usd\s*\/\s*cad|canadian dollar|loonie/i, USD: /usd\s*index|dollar index|\bdxy\b|usd\s*\/\s*(inr|cad)/i };
    var CDG = { stronger: "▲", weaker: "▼", flat: "▬" };
    var PTS = ["What's happening", "Why it happens", "What's likely next", "Watch for"];
    var HZ = { days: "next few days", weeks: "next few weeks" };
    var ESIGN = { up: 1, down: -1 };
    var MKT = {};
    (data.mk || []).forEach(function (m) { MKT[String(m.l).toLowerCase()] = m; });
    function mkFor(K) { var ks = CURMK[K]; for (var i = 0; i < ks.length; i++) if (MKT[ks[i]]) return MKT[ks[i]]; return null; }
    function clip(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1).replace(/\s+\S*$/, "") + "…" : s; }
    function sentences(s) {
      s = String(s || "").trim(); var out = [], re = /[.!?]["”')]*\s+(?=[A-Z0-9“"(])/g, m, last = 0;
      while ((m = re.exec(s))) { var end = m.index + m[0].length; out.push(s.slice(last, end).trim()); last = end; }
      if (last < s.length) out.push(s.slice(last).trim());
      return out.filter(Boolean);
    }
    function hostOf(u) { try { return new URL(u).hostname.replace(/^www\./, ""); } catch (x) { return ""; } }
    var MKIDS = {};
    CURS.forEach(function (K) { MKIDS[K] = ids.filter(function (id) { return MKTRX[K].test(N[id].l); }); });
    function mkFactor(id, K) { return /usd\s*\/\s*(inr|cad)/i.test(N[id].l) ? (K === "USD" ? 1 : -1) : 1; }
    function deriveCur(id, K) {
      var list = MKIDS[K]; if (!list.length) return null;
      if (list.indexOf(id) >= 0) {
        var d = N[id].dir; if (d !== "up" && d !== "down") return { dir: "flat", why: N[id].l + " is steady.", derived: true };
        var up = (d === "up") === (mkFactor(id, K) > 0);
        return { dir: up ? "stronger" : "weaker", why: "Read from " + N[id].l + " moving " + d + ".", derived: true };
      }
      var seen = {}; seen[id] = true; var front = [{ id: id, sign: 1, path: [id] }];
      for (var d2 = 1; d2 <= 3; d2++) {
        var next = [];
        for (var a = 0; a < front.length; a++) {
          var cur = front[a], outs = outE[cur.id].slice().sort(function (x, y) { return E[y].s - E[x].s; });
          for (var b = 0; b < outs.length; b++) {
            var e = E[outs[b]]; if (e.kind === "contradicts" || seen[e.t]) continue; seen[e.t] = true;
            var sg = cur.sign * (ESIGN[e.effect] || 0), p = cur.path.concat([e.t]);
            if (list.indexOf(e.t) >= 0) {
              var via = p.map(function (x) { return N[x].l; }).join(" ▸ ");
              if (!sg) return { dir: "flat", why: "Mixed effect via " + via + ".", derived: true };
              var st = (sg * mkFactor(e.t, K)) > 0;
              return { dir: st ? "stronger" : "weaker", why: "Via links: " + via + ".", derived: true };
            }
            next.push({ id: e.t, sign: sg, path: p });
          }
        }
        front = next;
      }
      return null;
    }
    function cardOf(id) {
      var n = N[id], c = n.c, o = { full: !!c, light: !!(c && c.light), hl: "", pts: [], fy: {}, cur: {}, pred: [], ag: [], pat: "", hist: {} };
      if (c) { o.hl = c.hl || ""; o.pts = c.pts || []; o.fy = c.fy || {}; o.cur = c.cur || {}; o.pred = c.pred || []; o.ag = c.ag || []; o.pat = c.pat || ""; o.hist = c.hist || {}; }
      var det = sentences(n.det);
      if (!o.hl) o.hl = clip(det[0] || n.why || "", 170);
      o.labelled = o.full && !o.light && o.pts.filter(Boolean).length >= 2;
      if (!o.labelled) {
        var pts = o.pts.filter(Boolean);
        if (!pts.length) {
          det.slice(0, 2).forEach(function (x) { pts.push(x); });
          if (n.why) pts.push("Why it matters: " + n.why);
          if (n.mkt) pts.push("Market: " + (n.mkt.label || n.l) + (n.mkt.price ? " " + n.mkt.price : "") + (n.mkt.change ? " " + n.mkt.change : ""));
          if (n.thr) pts.push("Running " + n.thr.days + " day" + (n.thr.days === 1 ? "" : "s") + " across " + n.thr.stories + " " + (n.thr.stories === 1 ? "story" : "stories") + ".");
        }
        o.pts = pts.slice(0, 4);
      }
      return o;
    }
    function ptsOf(info) {
      var out = [];
      info.pts.forEach(function (t, i) {
        if (!t) return;
        if (info.labelled && i < 4) out.push({ k: PTS[i], t: String(t).replace(/^(next|watch|why|what'?s happening)\s*:\s*/i, "") });
        else out.push({ k: "", t: t });
      });
      return out;
    }
    function predsOf(id, info) {
      var arr = info.pred.map(function (p, i) { return { src: "card", idx: i, if: p.if, then: p.then, odds: p.odds, hz: p.hz, why: p.why, watch: p.watch, wrong: p.wrong }; });
      if (arr.length < 3) outE[id].forEach(function (i) {
        var e = E[i]; if (arr.length >= 3 || e.status !== "predicted" || e.kind === "contradicts" || !N[e.t]) return;
        arr.push({ src: "edge", edge: i, if: "", then: N[e.t].l + " " + (e.effect === "up" ? "rises" : e.effect === "down" ? "falls" : "is affected"), odds: e.conf === "high" ? "high" : e.conf === "low" ? "low" : "medium", hz: e.lag, why: (e.label || "") + (e.why ? ". " + e.why : ""), watch: "", wrong: "" });
      });
      return arr;
    }
    function oddsBars(odds) {
      var n = CR[odds] || 0, w = el("span", "odds o-" + (odds || "none")), b = el("i"); b.setAttribute("aria-hidden", "true");
      for (var k = 0; k < 3; k++) b.appendChild(el("b", k < n ? "on" : ""));
      w.appendChild(b); w.appendChild(document.createTextNode(odds ? odds + " odds" : "odds unknown")); return w;
    }
    function shapeIcon(type, d, px) {
      var s = sv("svg", { viewBox: "-24 -24 48 48", width: px, height: px, "class": "sic", "aria-hidden": "true" });
      s.appendChild(sv("path", { "class": dcls(d), d: SH[type] })); return s;
    }
    var CUNIT = { INR: "Rupee", CAD: "Canadian dollar", USD: "US dollar" };
    function words(s, n) { var w = String(s || "").split(/\s+/).filter(Boolean); return w.length > n ? w.slice(0, n).join(" ").replace(/[,;:.]+$/, "") + "…" : w.join(" "); }
    /* Today = the market pair's own move, read as strength of the currency (USD/INR up = rupee weaker) */
    function todayOf(K) {
      var m = mkFor(K); if (!m || typeof m.cp !== "number") return null;
      var flat = Math.abs(m.cp) < 0.05, str = K === "USD" ? m.cp : -m.cp, dir = flat ? "flat" : (str > 0 ? "stronger" : "weaker");
      var pair = m.l + " " + (flat ? "flat" : (m.cp > 0 ? "up " : "down ") + Math.abs(m.cp).toFixed(2) + "%") + (m.p ? " (" + m.p + ")" : "");
      return { dir: dir, word: CUNIT[K] + " " + (flat ? "flat" : dir) + " today", pair: pair, why: K === "USD" || flat ? pair : pair + ", so the " + CUNIT[K].toLowerCase() + " is " + dir };
    }
    function curOf(id, K, info) {
      var c = info.cur[K], dv = false;
      if (!c || !c.dir) { c = deriveCur(id, K); dv = !!c; }
      return c ? { dir: c.dir, why: c.why || "", derived: dv } : null;
    }
    /* impact gauges (size / sure / when / top-5); derived from the graph when the producer has not supplied them */
    var IMP = null;
    function impactAll() {
      if (IMP) return IMP; IMP = {};
      var sc = {}, cf = {}, anyReal = false;
      ids.forEach(function (id) { var im = N[id].im; if (im && (im.size || im.top5)) anyReal = true; });
      ids.forEach(function (id) {
        var n = N[id], s3 = 0, cs = 0, cn = 0;
        outE[id].concat(inE[id]).forEach(function (i) { var e = E[i]; if (e.s >= 3) s3++; cs += conf(e); cn++; });
        sc[id] = (n.k || 0) + 2 * s3 + (n.mkt ? 3 : 0) + (/india|^us$|u\.s\.|united states|canada/i.test(n.r || "") ? 2 : 0) + (n.c && !n.c.light ? 2 : 0);
        cf[id] = cn ? cs / cn : 2;
      });
      var order = ids.slice().sort(function (a, b) { return sc[b] - sc[a] || (a < b ? -1 : 1); });
      order.forEach(function (id, r) {
        var n = N[id], im = n.im || {}, f = r / Math.max(1, ids.length);
        var lag = outE[id].map(function (i) { return String(E[i].lag || ""); }).join(" ").toLowerCase();
        IMP[id] = { size: im.size || (f < 0.2 ? "big" : f < 0.5 ? "medium" : "small"), sure: im.sure || (cf[id] >= 2.5 ? "high" : cf[id] < 1.7 ? "low" : "medium"),
          when: im.when || (/month/.test(lag) ? "months" : /day|hour|immediate/.test(lag) ? "days" : "weeks"), score: im.score != null ? im.score : sc[id],
          top5: anyReal ? (im.top5 || 0) : (r < 5 ? r + 1 : 0), fb: !im.size };
      });
      return IMP;
    }
    function whyMatters(id, info, im) {
      var n = N[id], bits = [], cs = CURS.filter(function (K) { return curOf(id, K, info); }), rg = String(n.r || "").trim();
      if (cs.length) bits.push("Moves " + cs.join(cs.length === 2 ? " and " : ", "));
      if (/india|canada/i.test(rg) || /^us$|united states/i.test(rg)) bits.push("Touches " + (/^us$|united states/i.test(rg) ? "the US" : rg));
      if (n.mkt && (n.mkt.change || n.mkt.price)) bits.push("Live market: " + (n.mkt.label || n.l) + (n.mkt.change ? " " + n.mkt.change.replace(/^[▲▼]\s*/, "") : ""));
      if (!bits.length) bits.push(im.size === "big" ? "Big market mover" : "Linked to " + (outE[id].length + inE[id].length) + " other stories");
      else if (im.size === "big" && bits.length < 2) bits.push("Big market mover");
      return bits.slice(0, 2).join(" · ");
    }
    function soWhat(id, info) {
      if (info.so) return words(info.so, 15);
      if (N[id].c && N[id].c.hl) { var cl = String(N[id].c.hl).split(/[;:]|,\s|\s[–—-]\s|\s(?:on|as|amid|after|while|with|but)\s/i)[0].trim(); if (cl.split(/\s+/).length >= 3) return words(cl, 15); }
      var n = N[id], ups = [], downs = [], oth = [], t = "";
      sortLinks(outE[id], function (e) { return e.t; }).forEach(function (x) {
        if (x.e.kind === "contradicts") return; var l = N[x.o].l;
        if (x.e.effect === "up") { if (ups.length < 2) ups.push(l); } else if (x.e.effect === "down") { if (downs.length < 2) downs.push(l); } else if (oth.length < 2) oth.push(l);
      });
      if (ups.length && downs.length) t = n.l + " pushes " + ups.join(" and ") + " up and " + downs.join(" and ") + " down";
      else if (ups.length) t = n.l + " pushes " + ups.join(" and ") + " up";
      else if (downs.length) t = n.l + " pushes " + downs.join(" and ") + " down";
      else if (oth.length) t = n.l + " affects " + oth.join(" and ");
      else {
        var cs = sortLinks(inE[id], function (e) { return e.f; });
        t = cs.length ? n.l + " is driven by " + cs.slice(0, 2).map(function (x) { return N[x.o].l; }).join(" and ") : (info.hl || n.why || n.det || n.l);
      }
      return words(t, 15);
    }
    var FS = { id: null };
    function sel0() { return FS.id; }

    function seenBlock(id, info) {
      var n = N[id], b = el("div", "seen"), hh = n.h || null, any = false;
      b.appendChild(el("h6", "", "Seen before"));
      function line(txt, cls) { if (!txt) return; any = true; b.appendChild(el("p", cls || "", txt)); }
      if (hh) {
        var bits = [];
        if (hh.trend && hh.trend !== "unknown") bits.push({ heating: "▲ heating up", cooling: "▼ cooling", steady: "▬ steady", "new": "● new" }[hh.trend] || "");
        if (hh.days_running != null) bits.push(hh.days_running + " day" + (hh.days_running === 1 ? "" : "s") + " running");
        if (hh.stories != null) bits.push(hh.stories + " stories");
        if (hh.last7 != null && hh.prev7 != null) bits.push("last 7 days " + hh.last7 + " vs " + hh.prev7 + " before");
        line(bits.filter(Boolean).join(" · "), "hs");
        line(hh.note, "hn");
        if (hh.hit != null) line("Past calls held up " + Math.round(hh.hit * 100) + "% of the time.");
        (hh.past || []).slice(0, 2).forEach(function (p) { line("Earlier call: " + clip(p.if + (p.then ? " → " + p.then : ""), 110) + (p.st ? " (" + p.st + ")" : ""), "hp"); });
      }
      var ch = info.hist || {};
      if (!hh || hh.days_running == null) {
        var bits2 = [];
        if (ch.days != null) bits2.push(ch.days + " day" + (ch.days === 1 ? "" : "s") + " running");
        if (ch.stories != null) bits2.push(ch.stories + " " + (ch.stories === 1 ? "story" : "stories"));
        if (!bits2.length && n.thr) bits2.push(n.thr.days + " day" + (n.thr.days === 1 ? "" : "s") + " running, " + n.thr.stories + " " + (n.thr.stories === 1 ? "story" : "stories"));
        line(bits2.join(" · "), "hs");
      }
      line(ch.seen, "hn");
      if (info.pat) line("Pattern: " + info.pat, "pt");
      var hot = [], cold = [], lab = n.l.toLowerCase(), words = lab.split(/\W+/).filter(function (w) { return w.length > 3; });
      function rel(t) { t = t.toLowerCase(); return t.indexOf(lab) >= 0 || words.some(function (w) { return t.indexOf(w) >= 0; }); }
      ((data.pat && data.pat.heating) || []).forEach(function (t) { if (rel(t)) hot.push(t); });
      ((data.pat && data.pat.cooling) || []).forEach(function (t) { if (rel(t)) cold.push(t); });
      if (hot.length) line("▲ Heating: " + hot.join(", "), "hs");
      if (cold.length) line("▼ Cooling: " + cold.join(", "), "hs");
      if (info.ag.length) { any = true; var u = el("ul", "ag"); info.ag.slice(0, 2).forEach(function (t) { u.appendChild(el("li", "", t)); }); b.appendChild(el("h6", "sub", "The case against")); b.appendChild(u); }
      if (!any) return null;
      return b;
    }

    /* ---- the 10-second view ---- */
    var dgHost = null;
    var GL = { size: ["small", "medium", "big"], sure: ["low", "medium", "high"], when: ["days", "weeks", "months"] };
    var GT = { size: ["Size", "How far this could move markets."], sure: ["Sure", "How well the evidence backs it up."], when: ["When", "How soon the effect should show."] };
    var CTRY = { India: /india|rbi|rupee|inr|nifty|sensex|mumbai|delhi/i, US: /\bus\b|u\.s\.|united states|american|federal reserve|\bfed\b|dollar|wall street|s&p|nasdaq|treasury/i, Canada: /canada|canadian|\bcad\b|tsx|ottawa|alberta/i };
    var SYM = { up: "▲", down: "▼", neutral: "●", "": "●" };
    function rectPos(t) { try { var r = t.getBoundingClientRect(); return { clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 }; } catch (x) { return null; } }
    /* one detail card at a time; same trigger again = "go further" (open the node) or close */
    function openCard(key, trig, ev, build, openId, wide) {
      if (ev && ev.stopPropagation) ev.stopPropagation();
      if (pop && !pop.hidden && FS.card === key) { if (openId) { FS.refocus = true; navTo(openId); } else closePop(); return; }
      closePop(); FS.card = key; FS.trig = trig; try { trig.setAttribute("aria-expanded", "true"); } catch (x) {}
      pop.classList.toggle("wide", !!wide);
      var real = ev && ev.type === "click" && (ev.clientX || ev.clientY);
      showPop(build, real ? ev : rectPos(trig));
    }
    function actKey(fn) { return function (ev) { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); fn(ev); } }; }
    function srcLinks(p, list, n) {
      var good = (list || []).map(function (s) { return [s[0], safeUrl(s[1])]; }).filter(function (s) { return s[1]; }).slice(0, n);
      if (!good.length) return;
      var d = el("div", "ps"); d.appendChild(el("b", "", "Sources"));
      good.forEach(function (s) { var a = el("a", "", clip(s[0], 56) + " (" + hostOf(s[1]) + ")"); a.href = s[1]; a.target = "_blank"; a.rel = "noopener noreferrer"; a.style.display = "block"; d.appendChild(a); });
      p.appendChild(d);
    }
    function pbtn(label, fn) { var b = el("button", "pbtn", label); b.type = "button"; b.addEventListener("click", function (ev) { ev.stopPropagation(); fn(ev); }); return b; }
    function chipRow(p, arr) { var c = el("div", "pc"); arr.filter(Boolean).forEach(function (t) { c.appendChild(el("span", "chp" + (t === "predicted" ? " dashed" : ""), t)); }); p.appendChild(c); }
    function mktLine(nd) {
      var mk = nd.mkt;
      if (mk && (mk.price || mk.change)) return (mk.label || nd.l) + " " + (mk.price || "") + (mk.change ? " " + mk.change : "");
      if (nd.dir && nd.dir !== "none") return nd.l + " is " + { up: "rising", down: "falling", flat: "steady" }[nd.dir] + " today";
      return "";
    }
    /* neighbour card: how it is affected and why, today's number, strength, timing, reported vs predicted, sources */
    function nodeCard(o, ei, role) {
      var nd = N[o], e = ei != null ? E[ei] : null;
      return function (p) {
        var h = el("div", "ph"); h.appendChild(el("b", "pt", nd.l)); p.appendChild(h);
        if (e) {
          p.appendChild(el("p", "pm", edgeGlyph(e) + " " + (e.label || "no mechanism given")));
          chipRow(p, [(FX[e.effect] ? FX[e.effect][2] : ""), "strength " + e.s + "/3", (e.conf || "?") + " confidence", e.status || "reported", e.lag ? "timing " + e.lag : ""]);
          popSec(p, role === "c" ? "Why it drives " + N[FS.id].l : "Why " + N[FS.id].l + " drives it", e.why || (e.c && e.c.ev) || "");
        }
        popSec(p, "Today", mktLine(nd)); popSec(p, "What it is", sentences(nd.det)[0] || nd.why || "");
        srcLinks(p, nd.src, 2);
        var bar = el("div", "pbar");
        bar.appendChild(pbtn("Open " + clip(nd.l, 24) + " ▸", function () { FS.refocus = true; navTo(o); }));
        if (e) bar.appendChild(pbtn("Link steps", function (ev) { openLinkAt(ei, ev); }));
        p.appendChild(bar);
      };
    }
    function listCard(title, list) {
      return function (p) {
        var h = el("div", "ph"); h.appendChild(el("b", "pt", title)); p.appendChild(h);
        list.forEach(function (x) {
          var b = el("button", "prow"); b.type = "button"; b.appendChild(el("b", "", N[x.o].l)); b.appendChild(el("span", "", " " + shortLbl(x.e)));
          b.addEventListener("click", function (ev) { ev.stopPropagation(); FS.refocus = true; navTo(x.o); }); p.appendChild(b);
        });
        p.appendChild(el("p", "pn", "Click a name to open it."));
      };
    }
    function curCard(id, info) {
      return function (p) {
        var h = el("div", "ph"); h.appendChild(el("b", "pt", "Currencies")); p.appendChild(h);
        CURS.forEach(function (K) {
          var c = curOf(id, K, info), t = todayOf(K); if (!c) return;
          var d = el("div", "ps"); d.appendChild(el("b", "", K + " · " + CURNAME[K]));
          if (t) d.appendChild(el("span", "", (t.dir === "flat" ? "▬ " : t.dir === "stronger" ? "▲ " : "▼ ") + t.word + ": " + t.why + ". "));
          d.appendChild(el("span", "", "This story's pressure: " + CDG[c.dir] + " " + c.dir + (c.why ? ". " + c.why : "") + (c.derived ? " (from graph links)" : "")));
          p.appendChild(d);
        });
        p.appendChild(el("p", "pn", "Today is the market's move. The story's pressure is what this news pushes toward. They can differ."));
      };
    }
    function forYouLinks(K, id) {
      var all = sortLinks(outE[id], function (e) { return e.t; }).map(function (x) { x.role = "e"; return x; }).concat(sortLinks(inE[id], function (e) { return e.f; }).map(function (x) { x.role = "c"; return x; }));
      var hit = all.filter(function (x) { var nd = N[x.o]; return CTRY[K].test(nd.l) || CTRY[K].test(nd.r || ""); });
      return (hit.length ? hit : all).sort(function (a, b) { return b.e.s - a.e.s; }).slice(0, 3);
    }
    function forYouCard(K, f, id) {
      return function (p) {
        var h = el("div", "ph"); h.appendChild(el("b", "pt", "For " + K + " " + SYM[f.dir || ""] + " " + ({ up: "up", down: "down", neutral: "neutral", "": "" }[f.dir || ""]))); p.appendChild(h);
        if (f.why) p.appendChild(el("p", "pm", f.why));
        var ls = forYouLinks(K, id);
        if (ls.length) {
          var d = el("div", "ps"); d.appendChild(el("b", "", "Links behind it"));
          ls.forEach(function (x) { var r = el("span", "", N[x.o].l + ": " + shortLbl(x.e)); r.style.display = "block"; d.appendChild(r); });
          p.appendChild(d);
        }
      };
    }
    function gaugeCard(k, val, fb) {
      return function (p) {
        var h = el("div", "ph"); h.appendChild(el("b", "pt", GT[k][0] + ": " + val)); p.appendChild(h);
        p.appendChild(el("p", "pm", GT[k][1] + " Here: " + val + "."));
        if (fb) p.appendChild(el("p", "pn", "Estimated from the links in the map."));
      };
    }
    /* centre box: everything else the AI wrote */
    function hubCard(id, info, preds) {
      return function (p) {
        var n = N[id], h = el("div", "ph"); h.appendChild(el("b", "pt", n.l)); p.appendChild(h);
        if (!info.full) p.appendChild(el("p", "pn", "Short card: the AI only writes full cards for the busiest nodes."));
        else if (info.light) p.appendChild(el("p", "pn", "Short version: the AI summary was not available this run."));
        if (info.hl) p.appendChild(el("p", "pm", info.hl));
        var pts = ptsOf(info);
        if (pts.length) { var ol = el("ol", "keypts"); pts.forEach(function (q) { var li = el("li"); if (q.k) li.appendChild(el("b", "pk", q.k)); li.appendChild(el("span", "pv", q.t)); ol.appendChild(li); }); p.appendChild(ol); }
        ["India", "US", "Canada"].forEach(function (K) { var f = info.fy[K]; if (f && f.why) popSec(p, "For " + K, f.why); });
        CURS.forEach(function (K) {
          var c = curOf(id, K, info), t = todayOf(K); if (!c) return;
          popSec(p, K + " · " + CURNAME[K], (t ? t.word.charAt(0).toUpperCase() + t.word.slice(1) + ": " + t.why + ". " : "") + "Story's pressure " + CDG[c.dir] + " " + c.dir + (c.why ? ": " + c.why : ""));
        });
        preds.forEach(function (q) { if (q.wrong) popSec(p, "Would prove it wrong", q.wrong); });
        if (info.ag.length) { var d = el("div", "ps"); d.appendChild(el("b", "", "The case against")); info.ag.slice(0, 2).forEach(function (t) { var r = el("span", "", t); r.style.display = "block"; d.appendChild(r); }); p.appendChild(d); }
        if (preds.length > 1) { var d2 = el("div", "ps"); d2.appendChild(el("b", "", "Also could happen")); preds.slice(1).forEach(function (q) { var r = el("span", "", (q.if ? "If " + q.if + " → " : "→ ") + (q.then || "") + (q.odds ? " (" + q.odds + " odds)" : "")); r.style.display = "block"; d2.appendChild(r); }); p.appendChild(d2); }
        var sb = seenBlock(id, info); if (sb) p.appendChild(sb);
        srcLinks(p, n.src, 3);
        /* table twin of the picture */
        var rows = sortLinks(inE[id], function (e) { return e.f; }).concat(sortLinks(outE[id], function (e) { return e.t; }));
        if (rows.length) {
          var tw = el("div", "ps"); tw.appendChild(el("b", "", "All links (table view)"));
          var tb = el("table", "lt"), th = el("tr"); ["From", "To", "Effect", "Why", "Status"].forEach(function (c) { var x = el("th", "", c); x.scope = "col"; th.appendChild(x); }); tb.appendChild(th);
          rows.forEach(function (x) { var e = x.e, r = el("tr"); [N[e.f].l, N[e.t].l, e.kind === "contradicts" ? "≠ cuts against" : (e.effect === "up" ? "▲ raises" : e.effect === "down" ? "▼ lowers" : "affects"), e.label || "", e.status || "reported"].forEach(function (c) { r.appendChild(el("td", "", c)); }); tb.appendChild(r); });
          tw.appendChild(tb); p.appendChild(tw);
        }
      };
    }
    function gaugeEl(k, im) {
      var val = im[k], lvl = GL[k].indexOf(val), g = el("button", "gg gg-" + k), bars = el("i");
      g.type = "button"; g.setAttribute("aria-expanded", "false"); g.appendChild(el("span", "gl", GT[k][0])); bars.setAttribute("aria-hidden", "true");
      for (var q = 0; q < 3; q++) bars.appendChild(el("b", q <= lvl ? "on" : "")); g.appendChild(bars); g.appendChild(el("span", "gv", val));
      g.title = GT[k][1]; g.addEventListener("click", function (ev) { openCard("g" + k, g, ev, gaugeCard(k, val, im.fb)); });
      return g;
    }
    function fyRow(id, info, im) {
      var row = el("div", "fv-fy"), chips = el("div", "fyc-w"), any = false;
      row.setAttribute("aria-label", "What it means for you");
      ["India", "US", "Canada"].forEach(function (K) {
        var f = info.fy[K]; if (typeof f === "string") f = { dir: "", why: f }; if (!f || (!f.why && !f.dir)) return; any = true;
        var b = el("button", "fyc d-" + (f.dir || "n")); b.type = "button"; b.setAttribute("aria-expanded", "false");
        b.appendChild(el("span", "fk", K)); b.appendChild(el("span", "fs", SYM[f.dir || ""])); if (f.why) b.appendChild(el("span", "fw", words(f.why, f.dir ? 4 : 7)));
        b.addEventListener("click", function (ev) { openCard("f" + K, b, ev, forYouCard(K, f, id)); }); chips.appendChild(b);
      });
      if (any) { chips.insertBefore(el("span", "fy-h", "For you"), chips.firstChild); row.appendChild(chips); }
      var gg = el("div", "gauges"); gg.setAttribute("role", "group"); gg.setAttribute("aria-label", "How big, how sure, how soon");
      ["size", "sure", "when"].forEach(function (k) { gg.appendChild(gaugeEl(k, im)); }); row.appendChild(gg);
      return row;
    }
    function stripEl(id, info, effects, preds, small) {
      var st = el("div", "fv-strip"), win = [], los = [], lim = small ? 2 : 3;
      effects.forEach(function (x) { if (x.e.kind === "contradicts") return; if (x.e.effect === "up") win.push(x); else if (x.e.effect === "down") los.push(x); });
      function tile(cls, head, key, build, wide, interactive) {
        var t = el("div", "stile " + cls); t.appendChild(el("h3", "", head));
        if (interactive !== false) {
          t.tabIndex = 0; t.setAttribute("role", "button"); t.setAttribute("aria-expanded", "false");
          var go = function (ev) { openCard(key, t, ev, build, null, wide); };
          t.addEventListener("click", go); t.addEventListener("keydown", actKey(go));
        }
        st.appendChild(t); return t;
      }
      function names(list) { var d = el("div", "sn", list.slice(0, lim).map(function (x) { return N[x.o].l; }).join(", ") + (list.length > lim ? " +" + (list.length - lim) + " more" : "")); return d; }
      if (win.length) { var tw = tile("win", "Winners ▲", "tw", listCard("Pushed up", win)); tw.appendChild(names(win)); }
      if (los.length) { var tl = tile("lose", "Losers ▼", "tl", listCard("Pushed down", los)); tl.appendChild(names(los)); }
      if (preds.length) {
        var p0 = preds[0], tn = tile("next", "Next", "tn", function (p) { predContent(p0)(p); preds.slice(1).forEach(function (q) { popSec(p, "Also", (q.if ? "If " + q.if + " → " : "→ ") + (q.then || "") + (q.odds ? " (" + q.odds + " odds)" : "")); }); }), meta = el("div", "nm");
        meta.appendChild(oddsBars(p0.odds)); if (p0.hz) meta.appendChild(el("span", "chp", HZ[String(p0.hz).toLowerCase()] || p0.hz));
        tn.appendChild(meta); tn.appendChild(el("div", "snx", (p0.if ? "If " + p0.if + " → " : "→ ") + (p0.then || "")));
      }
      var linked = CURS.filter(function (K) { return curOf(id, K, info); });
      var tc = tile("cur", "Currencies", "tc", curCard(id, info), true, linked.length > 0);
      if (!linked.length) tc.appendChild(el("div", "sn mute", "No direct currency effect"));
      linked.forEach(function (K) {
        var c = curOf(id, K, info), t = todayOf(K), r = el("div", "cr");
        r.appendChild(el("b", "ck", K));
        var s1 = el("span", "cs", "Story "); s1.appendChild(el("i", "gly " + c.dir, CDG[c.dir])); s1.appendChild(el("span", "w", " " + c.dir)); r.appendChild(s1);
        if (t) { var s2 = el("span", "ct", "Today "); s2.appendChild(el("i", "gly " + t.dir, t.dir === "stronger" ? "▲" : t.dir === "weaker" ? "▼" : "▬")); s2.appendChild(el("span", "w", " " + t.dir)); r.appendChild(s2); }
        r.title = K + ": the story pushes it " + c.dir + (t ? "; today it is " + t.dir + " (" + t.pair + ")" : "");
        tc.appendChild(r);
      });
      return st;
    }
    function renderFocus() {
      var id = FS.id = S.sel; fv.textContent = ""; if (!id || !N[id]) return;
      if (!FS.crumbs || FS.crumbs.indexOf(id) < 0) FS.crumbs = [id];
      if (FS.last !== id) { FS.exp = {}; FS.last = id; }
      var n = N[id], info = cardOf(id), dc = dcls(n.d), im = impactAll()[id], small = im.size === "small";
      var causes = sortLinks(inE[id], function (e) { return e.f; }), effects = sortLinks(outE[id], function (e) { return e.t; });
      var preds = predsOf(id, info), noflow = !causes.length && !effects.length;
      fv.classList.toggle("noflow", noflow); fv.classList.toggle("small", small);
      var top = el("div", "fv-top");
      var nav = el("nav", "fv-crumbs"); nav.setAttribute("aria-label", "Where you are");
      var back = el("button", "fv-back", "◂ Map"); back.type = "button"; back.setAttribute("data-f", "back"); back.setAttribute("aria-label", "Back to map"); back.addEventListener("click", closeFocus); nav.appendChild(back);
      FS.crumbs.forEach(function (cid, k) {
        nav.appendChild(el("span", "sep", "›"));
        if (k === FS.crumbs.length - 1) { var cur = el("span", "crumb cur", clip(N[cid].l, 28)); cur.setAttribute("aria-current", "page"); nav.appendChild(cur); }
        else { var cb = el("button", "crumb", clip(N[cid].l, 28)); cb.type = "button"; cb.addEventListener("click", function () { FS.refocus = true; navTo(cid); }); nav.appendChild(cb); }
      });
      top.appendChild(nav);
      var tools = el("div", "fv-tools"), lab = el("label", "trace"); lab.appendChild(el("span", "", "Trace to"));
      var sl = el("select"); sl.setAttribute("aria-label", "Trace the shortest causal path from " + n.l + " to another node");
      var o0 = el("option", "", "Pick a node…"); o0.value = ""; sl.appendChild(o0);
      ids.filter(function (x) { return x !== id; }).sort(function (a, b) { return N[a].l.toLowerCase() < N[b].l.toLowerCase() ? -1 : 1; }).forEach(function (x) { var o = el("option", "", N[x].l); o.value = x; if (S.trace && S.trace.to === x) o.selected = true; sl.appendChild(o); });
      sl.addEventListener("change", function () { setTrace(sl.value || null); });
      lab.appendChild(sl); tools.appendChild(lab); top.appendChild(tools);
      fv.appendChild(top);
      var ti = el("div", "fv-title"), h2 = el("h2", "", n.l); h2.tabIndex = -1; h2.setAttribute("data-f", "name");
      var row = el("div", "fv-chips"); row.appendChild(shapeIcon(n.t, n.d, 18)); row.appendChild(el("span", "", data.typeLabel[n.t] || n.t));
      if (n.dn) row.appendChild(el("span", "dchip " + dc, n.dn)); if (n.r) row.appendChild(el("span", "", n.r));
      if (n.dir && n.dir !== "none") { var db = el("span", "dirb2 " + n.dir); db.appendChild(el("i", "gly " + n.dir, { up: "▲", down: "▼", flat: "▬" }[n.dir])); db.appendChild(document.createTextNode(" " + { up: "rising", down: "falling", flat: "steady" }[n.dir])); row.appendChild(db); }
      row.appendChild(el("span", "imb im-" + im.size, "Market impact: " + im.size));
      ti.appendChild(h2); ti.appendChild(row); fv.appendChild(ti);
      fv.appendChild(el("p", "fv-so", soWhat(id, info)));
      fv.appendChild(fyRow(id, info, im));
      var trb = el("div", "fv-trace"); trb.hidden = !S.trace; fv.appendChild(trb); FS.trb = trb;
      dgHost = null;
      if (!noflow) {
        var flow = el("div", "fv-flow"), host = el("div", "fv-dg"); flow.appendChild(host); flow.appendChild(legendEl()); fv.appendChild(flow);
        if (dgRO) { try { dgRO.disconnect(); } catch (x) {} dgRO.observe(host); } dgHost = host;
      }
      fv.appendChild(stripEl(id, info, effects, preds, small));
      renderTrace(); draw();
      var nm = fv.querySelector('[data-f="name"]'); if (nm && FS.refocus) { try { nm.focus({ preventScroll: true }); } catch (x) {} }
      FS.refocus = false;
      FS.hubInfo = { info: info, preds: preds };
      var live = box.querySelector(".sronly"); if (live) live.textContent = "Showing " + n.l;
    }
    /* Top 5 for you today: cards above the map */
    function renderTop5() {
      var host = box.querySelector(".top5"); if (!host) return;
      var im = impactAll(), list = ids.filter(function (i) { return im[i].top5 > 0; }).sort(function (a, b) { return im[a].top5 - im[b].top5; });
      host.textContent = "";
      ids.forEach(function (i) { if (nodeEl[i]) nodeEl[i].classList.toggle("o5", list.length > 0 && im[i].top5 === 0); });
      if (!list.length) { host.hidden = true; return; }
      host.hidden = false;
      var hd = el("h5", "", "Top 5 for you today"); host.appendChild(hd);
      var rowEl = el("div", "t5row"); host.appendChild(rowEl);
      list.forEach(function (i) {
        var n = N[i], info = cardOf(i), b = el("button", "t5c"); b.type = "button";
        var why = whyMatters(i, info, im[i]); b.setAttribute("aria-label", n.l + ". " + why + ". Open the 10-second view.");
        var h = el("div", "t5h"); h.appendChild(el("span", "rk", String(im[i].top5))); h.appendChild(el("b", "", n.l)); b.appendChild(h);
        b.appendChild(el("p", "t5s", why));
        var ch = el("div", "t5m");
        CURS.forEach(function (K) { var c = curOf(i, K, info); if (!c) return; var s = el("span", "t5k", K + " "); s.appendChild(el("i", "gly " + c.dir, CDG[c.dir])); ch.appendChild(s); });
        ch.appendChild(el("span", "t5k", "Size " + im[i].size)); ch.appendChild(el("span", "t5k", "in " + im[i].when)); b.appendChild(ch);
        b.addEventListener("click", function () { openFocus(i); }); rowEl.appendChild(b);
      });
    }
    function legendEl() {
      var d = el("div", "fv-leg"); d.setAttribute("aria-label", "Legend");
      function it(cls, txt) { var s = el("span"); var g = sv("svg", { width: 34, height: 10, viewBox: "0 0 34 10", "aria-hidden": "true" }); g.appendChild(sv("path", { d: "M1 5H33", "class": "lg " + cls })); s.appendChild(g); s.appendChild(document.createTextNode(txt)); return s; }
      d.appendChild(it("rep", "reported")); d.appendChild(it("pred", "predicted")); d.appendChild(it("con", "contradicts"));
      d.appendChild(el("span", "", "Labels: ▲ raises ▼ lowers · thicker line = stronger · tag = the node's own move · click any box for details"));
      return d;
    }
    function draw() { if (dgHost) drawDiagram(dgHost); }

    /* ---- diagram ---- */
    function sortLinks(list, other) { return list.map(function (i) { var e = E[i]; return { i: i, e: e, o: other(e) }; }).filter(function (x) { return N[x.o]; }).sort(function (a, b) { return b.e.s - a.e.s || conf(b.e) - conf(a.e) || (N[a.o].l < N[b.o].l ? -1 : 1); }); }
    function edgeGlyph(e) { return e.kind === "contradicts" ? "≠" : (FX[e.effect] || FX.none)[0]; }
    function mkLine(g, d, e, extra) {
      var eg = sv("g", { "class": "ed e-" + e.effect + " k-" + e.kind + " st-" + e.status + " perm" + (extra ? " " + extra : ""), style: "--eo:1;--sw:" + r1(1.4 + e.s * 0.7) + "px" });
      var line = sv("path", { "class": "line", d: d, "marker-end": "url(#mk" + UID + "-" + (e.kind === "contradicts" ? "bar" : e.effect) + e.s + ")" });
      eg.appendChild(line); return eg;
    }
    function lblText(g, txt, x, y, w, maxL, anchor, cls) {
      var lines = wrap(txt, Math.max(6, Math.floor(w / 5.5)), maxL), t = sv("text", { "class": "dg-l " + (cls || ""), "text-anchor": anchor || "middle" });
      lines.forEach(function (s, i) { var ts = sv("tspan", { x: r1(x), y: r1(y + i * 13) }); ts.textContent = s; t.appendChild(ts); });
      g.appendChild(t); return t;
    }
    function drawDiagram(host) {
      var id = FS.id; if (!id) return;
      var W = Math.floor(host.clientWidth);
      if (W < 160) return;
      var n = N[id], info = cardOf(id);
      var causes = sortLinks(inE[id], function (e) { return e.f; }), effects = sortLinks(outE[id], function (e) { return e.t; });
      var old = host.firstChild, svgD, vertical = W < 560;
      if (vertical) { svgD = drawVertical(W, id, n, info, causes, effects); host.style.height = "auto"; }
      else {
        host.style.height = "";
        var H = Math.floor(host.clientHeight); if (H < 160) return;
        svgD = drawHoriz(W, H, id, n, info, causes, effects);
      }
      if (old) host.replaceChild(svgD, old); else host.appendChild(svgD);
    }
    function openLinkAt(i, ev) { openCard("l" + i, FS.trig || fv, ev, edgeContent(i)); }
    function shortLbl(e) { var t = String(e.label || "no mechanism given").replace(/^[▲▼≠\s]+/, ""); return edgeGlyph(e) + " " + words(t, 4); }
    /* line label: the glyph carries the colour, the words stay in secondary ink */
    function glyphLabel(g, e, x, y, w, anchor) {
      var txt = shortLbl(e), gl = txt.charAt(0), rest = txt.slice(1).trim(), lines = wrap(rest, Math.max(6, Math.floor(w / 5.6)), 2);
      var t = sv("text", { "class": "dg-l lnk", "text-anchor": anchor || "middle" });
      lines.forEach(function (s, k) {
        var ts = sv("tspan", { x: r1(x), y: r1(y - 6.5 * (lines.length - 1) + k * 13) });
        if (k === 0) { var gs = sv("tspan", { "class": "gl " + (e.kind === "contradicts" ? "con" : e.effect) }); gs.textContent = gl + " "; ts.appendChild(gs); }
        ts.appendChild(document.createTextNode(s)); t.appendChild(ts);
      });
      g.appendChild(t); return t;
    }
    function dgLine(g, d, e) {
      var cls = "ed dgl e-" + e.effect + " k-" + e.kind + " st-" + e.status + " perm", eg = sv("g", { "class": cls, style: "--eo:1;--sw:" + (e.s >= 3 ? 3 : e.s === 2 ? 2 : 1.5) + "px" });
      eg.appendChild(sv("path", { "class": "line", d: d, "marker-end": "url(#mk" + UID + "-" + (e.kind === "contradicts" ? "bar" : "none") + e.s + ")" }));
      return eg;
    }
    function lineAt(g, i, p, lw, tt0, lx, ly) {
      var e = E[i], d = pathD(p), eg = dgLine(g, d, e), hit = sv("path", { "class": "hit", d: d, tabindex: "0", role: "button", "aria-expanded": "false" });
      hit.setAttribute("aria-label", "Open the steps for: " + N[e.f].l + " to " + N[e.t].l + ", " + (e.label || ""));
      var tt = sv("title"); tt.textContent = (e.kind === "contradicts" ? "cuts against: " : "") + (e.label || "no mechanism given"); hit.appendChild(tt); eg.appendChild(hit);
      var q = bez(p, tt0 || 0.4), lt = glyphLabel(eg, e, lx != null ? lx : q[0], ly != null ? ly : q[1] - 10, lw, "middle");
      function open(ev) { openCard("l" + i, hit, ev, edgeContent(i)); }
      hit.addEventListener("click", open); lt.addEventListener("click", open); hit.addEventListener("keydown", actKey(open));
      lt.style.cursor = "pointer"; lt.style.pointerEvents = "all";
      g.appendChild(eg); return eg;
    }
    function neighbourBox(g, o, x, y, w, h, ei, role) {
      var nd = N[o], G = sv("g", { "class": "dg-n", tabindex: "0", role: "button", "aria-expanded": "false", transform: "translate(" + r1(x) + " " + r1(y) + ")", "aria-label": nd.l + ". Show details." });
      var tt = sv("title"); tt.textContent = nd.l + " (" + (data.typeLabel[nd.t] || nd.t) + (nd.dn ? ", " + nd.dn : "") + ")"; G.appendChild(tt);
      G.appendChild(sv("rect", { "class": "dg-box", width: w, height: h, rx: 7 }));
      G.appendChild(sv("rect", { "class": "dg-bar " + dcls(nd.d), width: 4, height: h - 10, x: 3, y: 5, rx: 2 }));
      var ic = sv("g", { transform: "translate(19 " + r1(h / 2) + ") scale(.34)" }); ic.appendChild(sv("path", { "class": "dg-ic " + dcls(nd.d), d: SH[nd.t] })); G.appendChild(ic);
      var lines = wrap(nd.l, Math.max(6, Math.floor((w - 62) / 6.6)), 2), t = sv("text", { "class": "dg-nm", x: 33 });
      lines.forEach(function (s, i) { var ts = sv("tspan", { x: 33, y: r1(h / 2 + (i - (lines.length - 1) / 2) * 14 + 4.4) }); ts.textContent = s; t.appendChild(ts); });
      G.appendChild(t);
      if (nd.dir === "up" || nd.dir === "down") dirTag(G, nd.dir, w - 40, h / 2, 32, "");
      function go(ev) { openCard("n" + o, G, ev, nodeCard(o, ei, role), o); }
      G.addEventListener("click", go); G.addEventListener("keydown", actKey(go));
      g.appendChild(G); return G;
    }
    function dirTag(parent, dir, x, cy, wd, word) {
      var G = sv("g", { "class": "dg-tag", transform: "translate(" + r1(x) + " " + r1(cy - 9) + ")" }), tt = sv("title"); tt.textContent = "The node's own move: " + ({ up: "rising", down: "falling", flat: "steady" }[dir] || "");
      G.appendChild(tt); G.appendChild(sv("rect", { "class": "dg-tagr", width: wd, height: 18, rx: 9 }));
      var t = sv("text", { "class": "dg-tagt", x: wd / 2, y: 13, "text-anchor": "middle" }), g1 = sv("tspan", { "class": "gl " + dir }); g1.textContent = { up: "▲", down: "▼", flat: "▬" }[dir] || "";
      t.appendChild(g1); if (word) t.appendChild(document.createTextNode(" " + word)); G.appendChild(t); parent.appendChild(G); return G;
    }
    function moreChip(g, x, y, w, label, key) {
      var G = sv("g", { "class": "dg-more", tabindex: "0", role: "button", transform: "translate(" + r1(x) + " " + r1(y) + ")", "aria-label": label });
      G.appendChild(sv("rect", { width: w, height: 26, rx: 13 }));
      var t = sv("text", { x: w / 2, y: 17.5, "text-anchor": "middle" }); t.textContent = label; G.appendChild(t);
      function go(ev) { ev.stopPropagation(); FS.exp[key] = !FS.exp[key]; draw(); }
      G.addEventListener("click", go); G.addEventListener("keydown", actKey(go));
      g.appendChild(G);
    }
    function hubOpen(G) {
      function go(ev) { var hi = FS.hubInfo || { info: cardOf(FS.id), preds: [] }; openCard("hub", G, ev, hubCard(FS.id, hi.info, hi.preds), null, true); }
      G.addEventListener("click", go); G.addEventListener("keydown", actKey(go));
    }
    function drawHoriz(W, H, id, n, info, causes, effects) {
      var im = impactAll()[id], small = im.size === "small";
      var hasC = causes.length > 0, hasE = effects.length > 0, titleH = 24, boxH = 52, hw = W < 760 ? 128 : 160, cap0 = 3;
      function take(list, key) { var cap = FS.exp[key] ? Math.min(list.length, 10) : cap0; return list.slice(0, cap); }
      var vc = take(causes, "cs"), ve = take(effects, "ef");
      var shown = {}; shown[id] = true; vc.concat(ve).forEach(function (x) { shown[x.o] = true; });
      var seenT = {}, thrAll = [];
      if (!small) ve.forEach(function (x, k) {
        sortLinks(outE[x.o], function (e) { return e.t; }).forEach(function (y) { if (shown[y.o] || seenT[y.o] || y.o === id) return; seenT[y.o] = true; thrAll.push({ par: x, k: k, y: y }); });
      });
      thrAll.sort(function (a, b) { return (b.y.e.s - a.y.e.s) || (a.k - b.k); });
      var thr = thrAll.slice(0, FS.exp.th ? 8 : 3); thr.sort(function (a, b) { return a.k - b.k || b.y.e.s - a.y.e.s; });
      var nb = (hasC ? 1 : 0) + (hasE ? 1 : 0) + (thr.length ? 1 : 0);
      var colw = nb ? Math.max(104, Math.min(220, Math.floor((W - 8 - hw - nb * 120) / nb))) : 0;
      var gw = nb ? Math.min(250, Math.floor((W - 8 - hw - nb * colw) / nb)) : 0;
      var used = nb * (colw + gw) + hw, off = Math.max(4, Math.floor((W - used) / 2)), x = off, xL = 0, xE = 0, xT = 0, xP;
      if (hasC) { xL = x; x += colw + gw; } xP = x; x += hw + gw; if (hasE) { xE = x; x += colw + gw; } if (thr.length) xT = x;
      var chipC = causes.length > cap0, chipE = effects.length > cap0, chipT = thrAll.length > 3;
      function units(v, chip) { return v + (chip ? 0.6 : 0); }
      var maxU = Math.max(units(vc.length, chipC), units(ve.length, chipE), units(thr.length, chipT), 1);
      var svgH = Math.max(H, Math.ceil(titleH + 16 + maxU * 56)), pit = Math.min(88, (svgH - titleH - 16) / maxU), blockH = titleH + 8 + maxU * pit, yo = Math.max(0, (svgH - blockH) / 2), avail = maxU * pit;
      function rows(k, chip) { var u = units(k, chip), y0 = yo + titleH + 8 + (avail - pit * u) / 2, a = []; for (var i = 0; i < k; i++) a.push(y0 + pit * (i + 0.5)); return a; }
      var yc = rows(vc.length, chipC), ye = rows(ve.length, chipE), yt = rows(thr.length, chipT);
      var s = sv("svg", { viewBox: "0 0 " + W + " " + svgH, width: W, height: svgH, "class": "dg", role: "group" });
      s.setAttribute("aria-label", "Connections of " + n.l + ": " + causes.length + " causes, " + effects.length + " effects");
      var gB = sv("g"), gN = sv("g"), gT = sv("g", { "class": "dg-t" });
      function title(tx, txt) { var t = sv("text", { x: tx, y: yo + 15, "class": "dg-title" }); t.textContent = txt; gT.appendChild(t); }
      if (hasC) title(xL, "What drives it · " + causes.length); if (hasE) title(xE, "What it drives · " + effects.length); if (thr.length) title(xT, "Then · " + thrAll.length);
      /* centre box: sized to its content */
      var pl = wrap(n.l, Math.floor((hw - 24) / 7.6), 3), hasDir = n.dir && n.dir !== "none", hasMk = n.mkt && (n.mkt.price || n.mkt.change);
      var hubH = 16 + 20 + 8 + pl.length * 16 + (hasDir ? 18 : 0) + (hasMk ? 16 + (n.mkt.change ? 15 : 0) : 0) + 26;
      var hubCy = yo + titleH + 8 + avail / 2, hubTop = hubCy - hubH / 2;
      var hub = sv("g", { "class": "dg-pillar dg-hub " + dcls(n.d), tabindex: "0", role: "button", "aria-expanded": "false", transform: "translate(" + xP + " " + r1(hubTop) + ")", "aria-label": n.l + ". Show full details." });
      hub.appendChild(sv("rect", { width: hw, height: hubH, rx: 12, "class": "dg-pil" }));
      var ic = sv("g", { transform: "translate(" + hw / 2 + " 26) scale(.7)" }); ic.appendChild(sv("path", { "class": "dg-ic " + dcls(n.d), d: SH[n.t] })); hub.appendChild(ic);
      var pt = sv("text", { "class": "dg-pn", "text-anchor": "middle" }), yy = 58;
      pl.forEach(function (t, i) { var ts = sv("tspan", { x: hw / 2, y: yy + i * 16 }); ts.textContent = t; pt.appendChild(ts); }); hub.appendChild(pt); yy += pl.length * 16 + 2;
      if (hasDir) { dirTag(hub, n.dir, hw / 2 - 42, yy - 4, 84, n.mkt ? "price" : { up: "rising", down: "falling", flat: "steady" }[n.dir]); yy += 24; }
      if (hasMk) { var mt2 = sv("text", { "class": "dg-pm", x: hw / 2, y: yy, "text-anchor": "middle" }); mt2.textContent = n.mkt.price || ""; hub.appendChild(mt2); if (n.mkt.change) { var mc = sv("text", { "class": "dg-pm", x: hw / 2, y: yy + 15, "text-anchor": "middle" }); mc.textContent = n.mkt.change; hub.appendChild(mc); } }
      var mo = sv("text", { "class": "dg-more-t", x: hw / 2, y: hubH - 9, "text-anchor": "middle" }); mo.textContent = "+ More"; hub.appendChild(mo);
      hubOpen(hub); gN.appendChild(hub);
      function port(k, i) { return k === 1 ? hubCy : hubCy + (i - (k - 1) / 2) * Math.min(14, (hubH - 44) / (k - 1)); }
      function curve(x0, y0, x1, y1) { var m = (x0 + x1) / 2; return [r1(x0), r1(y0), r1(m), r1(y0), r1(m), r1(y1), r1(x1), r1(y1)]; }
      var lw = Math.max(60, gw - 14);
      var LB = { c: [], e: [], t: [] };
      function addL(grp, i, pth, tt) { LB[grp].push({ i: i, p: pth, t: tt, nl: wrap(shortLbl(E[i]).slice(1).trim(), Math.max(6, Math.floor(lw / 5.6)), 2).length }); }
      vc.forEach(function (c, k) { addL("c", c.i, curve(xL + colw + 2, yc[k], xP - 3, port(vc.length, k)), 0.4); neighbourBox(gN, c.o, xL, yc[k] - boxH / 2, colw, boxH, c.i, "c"); });
      ve.forEach(function (c, k) { addL("e", c.i, curve(xP + hw + 2, port(ve.length, k), xE - 3, ye[k]), 0.62); neighbourBox(gN, c.o, xE, ye[k] - boxH / 2, colw, boxH, c.i, "e"); });
      thr.forEach(function (t, k) { var py = ye[t.k]; addL("t", t.y.i, curve(xE + colw + 2, py, xT - 3, yt[k]), 0.66); neighbourBox(gN, t.y.o, xT, yt[k] - boxH / 2, colw, boxH, t.y.i, "e"); });
      ["c", "e", "t"].forEach(function (grp) {
        var a = LB[grp];
        a.forEach(function (l) { var q = bez(l.p, l.t), x0 = l.p[0], x1 = l.p[6]; l.lx = (x1 - x0 < lw + 8) ? (x0 + x1) / 2 : Math.min(Math.max(q[0], x0 + lw / 2 + 4), x1 - lw / 2 - 4); l.ly = q[1] - 10 - 6.5 * (l.nl - 1); });
        a.sort(function (u, v) { return u.ly - v.ly; });
        a.forEach(function (l, k) { if (k) { var pv = a[k - 1], need = pv.ly + (pv.nl + l.nl) * 6.5 + 7; if (l.ly < need) l.ly = need; } });
        a.forEach(function (l) { lineAt(gB, l.i, l.p, lw, l.t, l.lx, l.ly); });
      });
      function chipAt(rowsY, k, list, key, colx) {
        var last = k ? rowsY[k - 1] + boxH / 2 + 6 : yo + titleH + 6;
        moreChip(gN, colx, last, colw, FS.exp[key] ? "show fewer" : "+" + (list.length - k) + " more", key);
      }
      if (chipC) chipAt(yc, vc.length, causes, "cs", xL);
      if (chipE) chipAt(ye, ve.length, effects, "ef", xE);
      if (chipT) chipAt(yt, thr.length, thrAll, "th", xT);
      s.appendChild(gB); s.appendChild(gT); s.appendChild(gN);
      return s;
    }
    function drawVertical(W, id, n, info, causes, effects) {
      var rowH = 58, y = 4, items = [], small = impactAll()[id].size === "small";
      function sec(title, list, kind, key) {
        if (!list.length) return;
        var vis = FS.exp[key] ? list.slice(0, 10) : list.slice(0, 3);
        items.push({ t: title, y: y }); y += 24;
        vis.forEach(function (x) { items.push({ x: x, kind: kind, y: y }); y += rowH; });
        if (list.length > 3) { items.push({ chip: key, y: y, label: FS.exp[key] ? "show fewer" : "+" + (list.length - vis.length) + " more" }); y += 44; }
        y += 6;
      }
      var shown = {}; shown[id] = true;
      var ce = causes.slice(0, FS.exp.cs ? 10 : 3), ef = effects.slice(0, FS.exp.ef ? 10 : 3); ce.concat(ef).forEach(function (x) { shown[x.o] = true; });
      var thr = [], seenT = {};
      if (!small) ef.forEach(function (x) { sortLinks(outE[x.o], function (e) { return e.t; }).forEach(function (t) { if (shown[t.o] || seenT[t.o]) return; seenT[t.o] = true; thr.push(t); }); });
      thr.sort(function (a, b) { return b.e.s - a.e.s; });
      sec("What drives it · " + causes.length, causes, "c", "cs");
      items.push({ hub: true, y: y }); y += 62;
      sec("What it drives · " + effects.length, effects, "e", "ef");
      sec("Then", thr.slice(0, 2), "t", "th");
      var H = y + 4, s = sv("svg", { viewBox: "0 0 " + W + " " + H, width: W, height: H, "class": "dg vert", role: "group" });
      s.setAttribute("aria-label", "Connections of " + n.l);
      var g = sv("g"), xr = 6, bw = W - 12;
      items.forEach(function (it) {
        if (it.t) { var t = sv("text", { x: 4, y: it.y + 14, "class": "dg-title" }); t.textContent = it.t; g.appendChild(t); return; }
        if (it.chip) { moreChip(g, xr + 26, it.y + 6, Math.min(160, bw - 26), it.label, it.chip); return; }
        if (it.hub) {
          var hb = sv("g", { "class": "dg-pillar dg-hub " + dcls(n.d), tabindex: "0", role: "button", "aria-expanded": "false", transform: "translate(4 " + it.y + ")", "aria-label": n.l + ". Show full details." });
          hb.appendChild(sv("rect", { width: W - 8, height: 54, rx: 10, "class": "dg-pil" }));
          var ic = sv("g", { transform: "translate(26 27) scale(.7)" }); ic.appendChild(sv("path", { "class": "dg-ic " + dcls(n.d), d: SH[n.t] })); hb.appendChild(ic);
          var pt = sv("text", { x: 50, y: 31, "class": "dg-pn" }); pt.textContent = clip(n.l, Math.floor((W - ((n.dir && n.dir !== "none") ? 200 : 120)) / 7)); hb.appendChild(pt); if (n.dir && n.dir !== "none") dirTag(hb, n.dir, W - 118, 27, 62, n.mkt ? "price" : "");
          var mo = sv("text", { x: W - 18, y: 31, "class": "dg-more-t", "text-anchor": "end" }); mo.textContent = "+ More"; hb.appendChild(mo);
          hubOpen(hb); g.appendChild(hb); return;
        }
        var x = it.x, e = x.e, nd = N[x.o], up = it.kind === "c";
        var G2 = sv("g", { transform: "translate(" + xr + " " + it.y + ")" });
        G2.appendChild(dgLine(G2, up ? "M14 40V56" : "M14 -8V8", e));
        var card = sv("g", { "class": "dg-n", tabindex: "0", role: "button", "aria-expanded": "false", "aria-label": nd.l + ". " + (e.label || "") + ". Show details." });
        card.appendChild(sv("rect", { "class": "dg-box", x: 28, width: bw - 28, height: 52, rx: 7 }));
        card.appendChild(sv("rect", { "class": "dg-bar " + dcls(nd.d), x: 31, y: 5, width: 4, height: 42, rx: 2 }));
        var nm = sv("text", { "class": "dg-nm", x: 44, y: 20 }); nm.textContent = clip(nd.l, Math.floor((bw - 100) / 7)); card.appendChild(nm);
        var ml = sv("text", { "class": "dg-l lnk", x: 44, y: 38, "text-anchor": "start" }), mtx = shortLbl(e), gs = sv("tspan", { "class": "gl " + (e.kind === "contradicts" ? "con" : e.effect) });
        gs.textContent = mtx.charAt(0) + " "; ml.appendChild(gs); ml.appendChild(document.createTextNode(clip(mtx.slice(1).trim(), Math.floor((bw - 100) / 5.8)))); card.appendChild(ml);
        if (nd.dir === "up" || nd.dir === "down") dirTag(card, nd.dir, bw - 44, 26, 32, "");
        var go = function (ev) { openCard("n" + x.o, card, ev, nodeCard(x.o, x.i, it.kind === "c" ? "c" : "e"), x.o); };
        card.addEventListener("click", go); card.addEventListener("keydown", actKey(go));
        G2.appendChild(card); g.appendChild(G2);
      });
      s.appendChild(g); return s;
    }

    /* ---- popover (link steps / prediction reasoning) ---- */
    var pop = box.querySelector(".pop");
    function closePop() {
      if (pop) { pop.hidden = true; pop.textContent = ""; pop.classList.remove("wide"); }
      if (FS.trig) { try { FS.trig.setAttribute("aria-expanded", "false"); } catch (x1) {} if (FS.rf && FS.trig.isConnected) { try { FS.trig.focus({ preventScroll: true }); } catch (x2) {} } }
      FS.card = null;
    }
    function popSec(parent, title, txt) { if (!txt) return; var d = el("div", "ps"); d.appendChild(el("b", "", title)); d.appendChild(el("span", "", txt)); parent.appendChild(d); }
    function edgeContent(i) {
      var e = E[i], c = e.c || {};
      return function (p) {
        var h = el("div", "ph"); h.appendChild(el("b", "pt", N[e.f].l + " → " + N[e.t].l)); p.appendChild(h);
        p.appendChild(el("p", "pm", edgeGlyph(e) + " " + (e.label || "no mechanism given")));
        var chips = el("div", "pc");
        [(FX[e.effect] ? FX[e.effect][2] : ""), e.kind !== "causal" ? e.kind : "", "strength " + e.s + "/3", (e.conf || "?") + " confidence", e.status || "reported", e.lag ? "lag " + e.lag : ""].filter(Boolean).forEach(function (t) { chips.appendChild(el("span", "chp" + (t === "predicted" ? " dashed" : ""), t)); });
        p.appendChild(chips);
        var steps = (c.steps && c.steps.length) ? c.steps : [N[e.f].l, e.label || "leads to", N[e.t].l];
        var ol = el("ol", "psteps"); steps.forEach(function (t) { ol.appendChild(el("li", "", t)); }); p.appendChild(ol);
        popSec(p, "Evidence", c.ev || e.why || "");
        popSec(p, "What could weaken it", c.wk || "");
        popSec(p, "Where it leads next", c.nx || (outE[e.t].slice(0, 2).map(function (k) { return N[E[k].t].l + " (" + (E[k].label || "") + ")"; }).join("; ")));
        popSec(p, "Seen before", c.seen || "");
        if (!c.steps && !c.ev && !c.wk) p.appendChild(el("p", "pn", "Short link: the AI writes step-by-step notes for the strongest links only."));
      };
    }
    function predContent(pr) {
      return function (p) {
        var h = el("div", "ph"); h.appendChild(el("b", "pt", "Could happen: " + (pr.then || "")));  p.appendChild(h);
        var chips = el("div", "pc"); chips.appendChild(oddsBars(pr.odds)); if (pr.hz) chips.appendChild(el("span", "chp", HZ[String(pr.hz).toLowerCase()] || pr.hz)); p.appendChild(chips);
        popSec(p, "If", pr.if); popSec(p, "Why", pr.why); popSec(p, "Watch for", pr.watch); popSec(p, "Wrong if", pr.wrong);
        if (pr.src === "edge") p.appendChild(el("p", "pn", "From a predicted link in the graph."));
      };
    }
    function showPop(build, ev) {
      if (!pop) return; pop.textContent = ""; var x = el("button", "px", "✕"); x.type = "button"; x.setAttribute("aria-label", "Close"); x.addEventListener("click", function (e2) { e2.stopPropagation(); FS.rf = true; closePop(); FS.rf = false; }); pop.appendChild(x);
      build(pop); pop.hidden = false;
      var br = box.getBoundingClientRect(), px = (ev && ev.clientX != null ? ev.clientX : br.left + br.width / 2) - br.left, py = (ev && ev.clientY != null ? ev.clientY : br.top + 200) - br.top, w = pop.offsetWidth, h = pop.offsetHeight;
      var left = Math.max(6, Math.min(br.width - w - 6, px + 12)), topP = Math.max(6, Math.min(br.height - h - 6, py + 12));
      if (window.innerWidth < 700) { left = Math.max(6, (br.width - w) / 2); topP = Math.max(6, Math.min(br.height - h - 6, py - h / 2)); }
      pop.style.left = left + "px"; pop.style.top = topP + "px"; try { x.focus({ preventScroll: true }); } catch (e3) {}
    }
    document.addEventListener("click", function (ev) { if (pop && !pop.hidden && !(ev.target.closest && ev.target.closest(".pop"))) closePop(); });

    /* ---- trace (shortest causal path) ---- */
    function bfsPath(from, to, nbr) {
      var prev = {}; prev[from] = null; var q = [from];
      while (q.length) { var u = q.shift(); if (u === to) break; nbr(u).forEach(function (x) { if (!(x.o in prev)) { prev[x.o] = { u: u, i: x.i }; q.push(x.o); } }); }
      if (!(to in prev)) return null;
      var nodes = [to], edges = [], c = to; while (prev[c]) { edges.unshift(prev[c].i); c = prev[c].u; nodes.unshift(c); }
      return { nodes: nodes, edges: edges };
    }
    function fwdN(u) { return outE[u].filter(function (i) { return E[i].kind !== "contradicts"; }).sort(function (a, b) { return E[b].s - E[a].s; }).map(function (i) { return { o: E[i].t, i: i }; }); }
    function bothN(u) { return fwdN(u).concat(inE[u].sort(function (a, b) { return E[b].s - E[a].s; }).map(function (i) { return { o: E[i].f, i: i }; })); }
    function setTrace(to) {
      if (!to || !S.sel || to === S.sel) { S.trace = null; renderTrace(); applyAll(); return; }
      var a = S.sel, r = bfsPath(a, to, fwdN), mode = "fwd";
      if (!r) { var r2 = bfsPath(to, a, fwdN); if (r2) { r = r2; mode = "back"; } }
      if (!r) { r = bfsPath(a, to, bothN); mode = "any"; }
      S.trace = r ? { to: to, nodes: r.nodes, edges: r.edges, mode: mode } : { to: to, nodes: [], edges: [], mode: "none" };
      renderTrace(); applyAll();
      if (S.trace.nodes.length && !S.focus && S.trace.nodes.some(function (x) { return !nodeEl[x]; })) setView("all", { sel: S.sel });
    }
    function renderTrace() {
      var b = FS.trb; if (!b) return; b.textContent = ""; b.hidden = !S.trace; if (!S.trace) return;
      var t = S.trace, head = el("b", "", t.nodes.length ? "Trace · " + (t.nodes.length - 1) + " step" + (t.nodes.length === 2 ? "" : "s") : "Trace · no path"); b.appendChild(head);
      var note = { fwd: "causal path", back: N[t.to].l + " drives " + N[S.sel].l + " (reverse direction)", any: "linked, but not as a one-way cause-and-effect chain", none: "these two nodes are not connected" }[t.mode];
      b.appendChild(el("span", "tn", " " + note + ". "));
      var ol = el("span", "tsteps");
      t.nodes.forEach(function (id, k) {
        if (k) { var e = E[t.edges[k - 1]]; var bt = el("button", "tl", edgeGlyph(e) + " " + clip(e.label || "leads to", 34)); bt.type = "button"; bt.addEventListener("click", function (ev) { openLinkAt(t.edges[k - 1], ev); }); ol.appendChild(bt); }
        var nb = el("button", "tnode", N[id].l); nb.type = "button"; nb.addEventListener("click", function () { FS.refocus = true; navTo(id); }); ol.appendChild(nb);
      });
      b.appendChild(ol);
      var cl = el("button", "tx", "Clear trace"); cl.type = "button"; cl.addEventListener("click", function () { setTrace(null); var sl = fv.querySelector(".fv-tools select"); if (sl) sl.selectedIndex = 0; }); b.appendChild(cl);
    }

    /* ---- open / close / navigate ---- */
    function openFocus(id) {
      if (!N[id]) return; closePop();
      if (S.sel !== id) { S.trace = null; }
      S.sel = id; S.focus = true; FS.refocus = true; FS.crumbs = [id]; applyAll();
      box.classList.add("is-focus"); fv.hidden = false; renderFocus();
      try { box.scrollIntoView({ block: "start" }); } catch (x) {}
    }
    function navTo(id) {
      if (!N[id]) return; closePop(); S.trace = null; S.sel = id;
      var ci = (FS.crumbs || []).indexOf(id); if (ci >= 0) FS.crumbs = FS.crumbs.slice(0, ci + 1); else FS.crumbs = (FS.crumbs || []).concat([id]);
      applyAll(); renderFocus();
    }
    function closeFocus() {
      closePop(); S.focus = false; box.classList.remove("is-focus"); fv.hidden = true;
      if (S.sel && !nodeEl[S.sel]) { var keep = S.sel; setView("all", { sel: keep }); return; }
      applyAll(); setVB();
      var b = S.sel && nodeEl[S.sel]; if (b) { try { b.focus({ preventScroll: true }); } catch (x) {} }
    }
    var dgRO = window.ResizeObserver ? new ResizeObserver(function () { if (S.focus) draw(); }) : null;

    /* ---------- selection ---------- */
    function choose(id, pan) {
      S.sel = id || null; if (!S.sel) { S.trace = null; }
      hideTip();
      if (S.sel && nodeEl[S.sel] && SC.nodes[S.sel].ghost) nodeEl[S.sel].classList.add("open");
      applyAll();
      if (S.sel && pan && nodeEl[S.sel]) { ensureVisible(S.sel); try { nodeEl[S.sel].focus({ preventScroll: true }); } catch (x) {} }
    }
    function goTo(id) {
      if (!N[id]) return;
      if (!nodeEl[id] || (SC.kind === "chain" && SC.nodes[id] == null)) {
        var ci = -1; CH.forEach(function (c, i) { if (ci < 0 && c.n.indexOf(id) >= 0) ci = i; });
        var tk = N[id].d >= 0 ? "d" + N[id].d : "dx";
        if (ci >= 0) setView("chains", { chain: ci, sel: id });
        else if (LAY["t:" + tk]) setView("topic", { topic: tk, sel: id });
        else setView("all", { sel: id });
        if (!nodeEl[id]) setView("all", { sel: id });
      }
      openFocus(id);
    }
    function wireNode(n, id) {
      n.addEventListener("click", function (ev) { ev.stopPropagation(); if (ev.shiftKey && S.sel && S.sel !== id) { setTrace(id); return; } openFocus(id); });
      n.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ev.stopPropagation(); if (ev.shiftKey && S.sel && S.sel !== id) setTrace(id); else openFocus(id); }
        else if (ev.key.indexOf("Arrow") === 0) { ev.preventDefault(); ev.stopPropagation(); spatial(id, ev.key); }
      });
      n.addEventListener("focus", function () { ensureVisible(id); hover(id); });
      n.addEventListener("blur", function () { if (hoverId === id) clearHover(); });
      n.addEventListener("mouseenter", function () { if (!(drag && moved)) hover(id); });
      n.addEventListener("mousemove", function (ev) {
        if (drag && moved) return;
        showTip(function (t) {
          var n3 = N[id], c3 = n3.c, kp = (c3 && c3.pts && c3.pts[0]) || n3.det || n3.why || "", mv = n3.mkt ? ((n3.mkt.label || n3.l) + (n3.mkt.price ? " " + n3.mkt.price : "") + (n3.mkt.change ? " " + n3.mkt.change : "")) : (n3.dir === "up" ? "▲ rising" : n3.dir === "down" ? "▼ falling" : n3.dir === "flat" ? "▬ steady" : "");
          t.appendChild(el("b", "", n3.l)); t.appendChild(el("span", "tm", (data.typeLabel[n3.t] || "") + (n3.dn ? " · " + n3.dn : "") + (n3.r ? " · " + n3.r : "")));
          if (mv) t.appendChild(el("span", "tm", mv)); if (kp) t.appendChild(el("span", "tm", clip(kp, 130))); t.appendChild(el("span", "tm", "Click to open the focus view"));
        }, ev);
      });
      n.addEventListener("mouseleave", function () { hideTip(); clearHover(); });
    }
    function spatial(from, key) {
      var a = SC.nodes[from], best = null, bs = 1e9;
      Object.keys(SC.nodes).forEach(function (id) {
        if (id === from || !nodeOk(id)) return; var b = SC.nodes[id], dx = b.x - a.x, dy = b.y - a.y, p, q;
        if (key === "ArrowRight") { p = dx; q = dy; } else if (key === "ArrowLeft") { p = -dx; q = dy; } else if (key === "ArrowDown") { p = dy; q = dx; } else { p = -dy; q = dx; }
        if (p <= 4) return; var s = p + 2.2 * Math.abs(q); if (Math.abs(q) > p * 2.5) s += 400; if (s < bs) { bs = s; best = id; }
      });
      if (best) { ensureVisible(best); nodeEl[best].focus({ preventScroll: true }); }
    }
    svg.addEventListener("click", function (ev) { closePop(); if (!ev.target.closest || !(ev.target.closest(".nd") || ev.target.closest(".ft"))) { if (S.sel) choose(null); } });
    box.addEventListener("keydown", function (ev) { if (ev.key === "Escape") { if (pop && !pop.hidden) { FS.rf = true; closePop(); FS.rf = false; ev.stopPropagation(); } else if (S.focus) { closeFocus(); ev.stopPropagation(); } } });
    view.addEventListener("keydown", function (ev) {
      if (ev.target.closest && ev.target.closest("input,select,textarea")) return;
      var k = ev.key, onNode = ev.target.closest && ev.target.closest(".nd");
      if (k === "+" || k === "=") { ev.preventDefault(); zoomCenter(1.4); }
      else if (k === "-" || k === "_") { ev.preventDefault(); zoomCenter(1 / 1.4); }
      else if (k === "0") { ev.preventDefault(); fit(true); }
      else if (k === "Escape") { if (S.focus) return; choose(null); hideTip(); }
      else if (!onNode && k.indexOf("Arrow") === 0) {
        ev.preventDefault(); var f = 0.15;
        panBy(k === "ArrowLeft" ? -f : k === "ArrowRight" ? f : 0, k === "ArrowUp" ? -f : k === "ArrowDown" ? f : 0);
      }
    });

    /* ---------- chooser rail + headers + views ---------- */
    function buildRail() {
      rail.textContent = "";
      if (S.view === "all" || S.view === "full") return;
      var wrapEl = el("div", "rail-in");
      if (S.view === "chains") {
        wrapEl.appendChild(el("div", "rh", "Key causal chains"));
        var cul = el("ul", "cl"); wrapEl.appendChild(cul);
        CH.forEach(function (c, i) {
          var b = el("button", "ccard"); b.type = "button"; b.setAttribute("aria-pressed", i === S.chain ? "true" : "false"); b.setAttribute("data-c", i);
          if (i === S.chain) b.setAttribute("aria-current", "true");
          b.appendChild(el("span", "ct", c.title)); b.appendChild(el("span", "cs", c.sum));
          var m = el("span", "cm"); m.appendChild(badge(c.st));
          c.d.slice(0, 3).forEach(function (d) { m.appendChild(el("span", "dchip " + dcls(d), d >= 0 ? data.short[d] : "Other")); });
          if (c.d.length > 3) m.appendChild(el("span", "dchip", "+" + (c.d.length - 3)));
          b.appendChild(m);
          b.addEventListener("click", function () { S.chain = i; S.sel = null; rebuild(); });
          var cli = el("li"); cli.appendChild(b); cul.appendChild(cli);
        });
      } else {
        wrapEl.appendChild(el("div", "rh", "Pick a topic"));
        var g1 = el("div", "tgrp"), g2 = el("div", "tgrp");
        TOP.forEach(function (t) {
          var b = el("button", "tbtn " + (t.t === "d" ? dcls(t.d) : "")); b.type = "button"; b.setAttribute("aria-pressed", t.k === S.topic ? "true" : "false"); b.setAttribute("data-t", t.k);
          if (t.t === "d") b.appendChild(el("i")); b.appendChild(el("span", "tl", t.l)); b.appendChild(el("span", "tn", String(t.n)));
          b.addEventListener("click", function () { S.topic = t.k; S.sel = null; rebuild(); });
          (t.t === "d" ? g1 : g2).appendChild(b);
        });
        wrapEl.appendChild(g1);
        if (g2.childNodes.length) { wrapEl.appendChild(el("div", "rh", "Regions")); wrapEl.appendChild(g2); }
      }
      rail.appendChild(wrapEl);
    }
    function headers() {
      mt.textContent = ""; mh.textContent = "";
      if (S.view === "chains") {
        var c = CH[S.chain]; mt.appendChild(document.createTextNode(c.title + " ")); mt.appendChild(badge(c.st));
        mh.textContent = SC.vertical ? "Top to bottom, one step per arrow. Tap any node for details." : "Left to right, one step per arrow. Faint nodes link to the chain: click one to follow it.";
        mh.appendChild(el("span", "mkey", " ▲ raises  ▼ lowers  dashed = predicted"));
      } else if (S.view === "topic") {
        var t = TOP.filter(function (x) { return x.k === S.topic; })[0];
        mt.textContent = (t ? t.l : "Topic") + (t ? " · " + t.n + " nodes" : "");
        mh.textContent = "Faint nodes belong to other topics. Click any node for its links.";
      } else if (S.view === "full") {
        mt.textContent = "Full map · " + ids.length + " nodes, " + E.length + " links";
        mh.textContent = "Every node is named and every arrow says why. Drag to pan, zoom in for detail. Hover or pick a node to light up its links; the rest dim.";
      } else {
        mt.textContent = "Everything · " + ids.length + " nodes, " + E.length + " links";
        mh.textContent = "Hover a node for a quick look, click it to open the focus view. Links light up as you hover.";
      }
    }
    function sizeView() {
      if (SC.kind === "chain" && SC.vertical) view.style.height = Math.min(SC.H + 16, 820) + "px";
      else if (SC.narrow && view.clientWidth > 0) view.style.height = Math.round(Math.min(760, Math.max(380, SC.H * ((view.clientWidth - 68) / SC.W) + 84))) + "px";
      else view.style.height = "";
    }
    function rebuild() {
      hideTip(); clearHover();
      box.setAttribute("data-view", S.view); SC = buildScene(); sizeView();
      var fl = box.querySelector(".cflt"); if (fl) fl.hidden = !(S.view === "all" || S.view === "full");
      [].forEach.call(box.querySelectorAll(".seg button"), function (b) { var on = b.getAttribute("data-view") === S.view; b.setAttribute("aria-checked", on ? "true" : "false"); b.tabIndex = on ? 0 : -1; });
      buildRail(); headers(); render();
      if (S.sel && !nodeEl[S.sel]) S.sel = null;
      lastW = view.clientWidth; fit(); applyAll();
    }
    function setView(v, o) {
      o = o || {}; S.view = v; if (S.focus) { S.focus = false; box.classList.remove("is-focus"); fv.hidden = true; closePop(); }
      if (o.chain != null) S.chain = o.chain; if (o.topic) S.topic = o.topic;
      S.sel = o.sel || null; rebuild();
      if (o.sel && nodeEl[o.sel]) choose(o.sel, true);
    }
    [].forEach.call(box.querySelectorAll(".seg button"), function (b) {
      if (b.getAttribute("data-view") === "chains" && !CH.length) b.hidden = true;
      if (b.getAttribute("data-view") === "topic" && !TOP.length) b.hidden = true;
      b.addEventListener("click", function () { setView(b.getAttribute("data-view")); });
      b.addEventListener("keydown", function (ev) {
        var k = ev.key; if (["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"].indexOf(k) < 0) return;
        var bs = [].filter.call(box.querySelectorAll(".seg button"), function (x) { return !x.hidden; }), i = bs.indexOf(b);
        var nb = k === "Home" ? bs[0] : k === "End" ? bs[bs.length - 1] : bs[(i + (k === "ArrowRight" || k === "ArrowDown" ? 1 : bs.length - 1)) % bs.length]; ev.preventDefault(); nb.focus(); setView(nb.getAttribute("data-view"));
      });
    });

    /* ---------- search with a result list ---------- */
    var qi = box.querySelector(".csearch"), res = box.querySelector(".sres"), resIdx = -1;
    function closeRes() { if (!res) return; res.hidden = true; res.textContent = ""; resIdx = -1; qi.setAttribute("aria-expanded", "false"); }
    function runSearch() {
      var q = qi.value.trim().toLowerCase(); F.q = qi.value;
      res.textContent = ""; resIdx = -1;
      if (!q) { closeRes(); applyAll(); return; }
      var hits = ids.filter(function (id) { return matches(id, q); }).sort(function (a, b) {
        var la = N[a].l.toLowerCase(), lb = N[b].l.toLowerCase(), pa = la.indexOf(q) === 0 ? 0 : la.indexOf(q) >= 0 ? 1 : 2, pb = lb.indexOf(q) === 0 ? 0 : lb.indexOf(q) >= 0 ? 1 : 2;
        return pa - pb || N[b].k - N[a].k || (a < b ? -1 : 1);
      });
      if (!hits.length) { var li0 = el("li", "sno", "No node matches “" + qi.value.trim() + "”"); res.appendChild(li0); }
      hits.slice(0, 8).forEach(function (id) {
        var li = el("li"), b = el("button"); b.type = "button"; b.setAttribute("role", "option"); b.setAttribute("data-id", id);
        b.appendChild(el("span", "", N[id].l)); b.appendChild(el("span", "dchip " + dcls(N[id].d), N[id].d >= 0 ? data.short[N[id].d] : (N[id].dn || "Other")));
        b.addEventListener("click", function () { qi.value = ""; F.q = ""; closeRes(); goTo(id); });
        li.appendChild(b); res.appendChild(li);
      });
      if (hits.length > 8) res.appendChild(el("li", "sno", (hits.length - 8) + " more — keep typing to narrow"));
      res.hidden = false; qi.setAttribute("aria-expanded", "true"); applyAll();
    }
    if (qi) {
      qi.addEventListener("input", runSearch);
      qi.addEventListener("keydown", function (ev) {
        var items = [].slice.call(res.querySelectorAll("button"));
        if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
          if (!items.length) return; ev.preventDefault();
          resIdx = (resIdx + (ev.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
          items.forEach(function (b, i) { b.classList.toggle("on", i === resIdx); }); items[resIdx].scrollIntoView({ block: "nearest" });
        } else if (ev.key === "Enter") { var pick = items[resIdx >= 0 ? resIdx : 0]; if (pick) { ev.preventDefault(); pick.click(); } }
        else if (ev.key === "Escape") { qi.value = ""; F.q = ""; closeRes(); applyAll(); }
      });
      qi.addEventListener("focus", function () { if (qi.value.trim()) runSearch(); });
      document.addEventListener("click", function (ev) { if (!ev.target.closest || !ev.target.closest(".csw")) closeRes(); });
    }

    /* ---------- Everything filters ---------- */
    [].forEach.call(box.querySelectorAll(".chip[data-d]"), function (c) {
      c.addEventListener("click", function () {
        var d = c.getAttribute("data-d"), on = c.getAttribute("aria-pressed") === "true";
        c.setAttribute("aria-pressed", on ? "false" : "true"); F.hideDom[d] = on; applyAll();
      });
    });
    function sel2(name, key, num) { var s = box.querySelector('select[name="' + name + '"]'); if (s) s.addEventListener("change", function () { F[key] = num ? +s.value : s.value; applyAll(); }); }
    sel2("region", "region"); sel2("conf", "conf", true); sel2("order", "order", true); sel2("status", "status");
    renderTop5();
    if (pop) {
      var ty0 = null;
      pop.addEventListener("touchstart", function (ev) { ty0 = ev.touches[0].clientY; }, { passive: true });
      pop.addEventListener("touchend", function (ev) { if (ty0 != null && pop.scrollTop <= 0 && ev.changedTouches[0].clientY - ty0 > 60) { FS.rf = true; closePop(); FS.rf = false; } ty0 = null; });
    }
    /* currency lens + predicted-ghosts toggle */
    [].forEach.call(box.querySelectorAll(".lens button"), function (b) {
      b.addEventListener("click", function () {
        S.lens = b.getAttribute("data-lens") || "";
        [].forEach.call(box.querySelectorAll(".lens button"), function (x) { x.setAttribute("aria-checked", x === b ? "true" : "false"); });
        lastLK = ""; applyAll();
      });
    });
    [].forEach.call(box.querySelectorAll(".lns button"), function (b) {
      b.addEventListener("click", function () {
        S.lines = b.getAttribute("data-lines") || "all"; svg.classList.toggle("lsel", S.lines === "sel");
        [].forEach.call(box.querySelectorAll(".lns button"), function (x) { x.setAttribute("aria-checked", x === b ? "true" : "false"); });
      });
    });
    var ptog = box.querySelector(".ptog input"); if (ptog) ptog.addEventListener("change", function () { S.showPred = ptog.checked; placeFutures(box2().w / vb.w); });
    var cl = box.querySelector(".cclear");
    if (cl) cl.addEventListener("click", function () {
      F.hideDom = {}; F.region = ""; F.conf = 1; F.order = 3; F.status = "";
      [].forEach.call(box.querySelectorAll(".chip[data-d]"), function (c) { c.setAttribute("aria-pressed", "true"); });
      [].forEach.call(box.querySelectorAll(".cflt select"), function (s) { s.selectedIndex = 0; }); applyAll();
    });
    var fdet = box.querySelector(".cflt");
    if (fdet) fdet.addEventListener("toggle", function () { setVB(); });

    rebuild();
    if (view.clientWidth === 0) window.addEventListener("load", function () { rebuild(); });
  }
  [].forEach.call(document.querySelectorAll(".cmap"), function (b) { try { init(b); } catch (e) { if (window.console) console.error(e); } });
})();
"""

# ---------------------------------------------------------------- connection map: data cleaning

MAX_NODES = 50
MAX_EDGES = 90
_CONF = {"low": 1, "medium": 2, "high": 3}
_LANE_OTHER = 7  # domain-less nodes
SHORT = ["AI", "Tech", "Finance", "Markets", "Geopolitics", "Conflict", "Commodities", "Other"]

COL_W = 140      # horizontal pitch between causal-depth columns
ROW_H = 72       # vertical pitch between node slots inside a topic row
GUTTER = 120     # left tag column that names each topic row
PAD_TOP = 34     # room for the "causes -> outcomes" axis
LANE_GAP = 6
SLOT_PAD = 24    # lane top padding before the first node centre
LANE_TAIL = 54   # lane bottom padding after the last node centre (node label room)


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


def _lst(v, n=5, ln=260):
    """List of clean strings (accepts a single string too)."""
    if isinstance(v, str):
        v = [v]
    out = []
    for x in v if isinstance(v, list) else []:
        if isinstance(x, dict):
            x = " ".join(str(x.get(k)) for k in ("label", "name", "title", "text", "why", "detail") if x.get(k))
        t = _txt(x, ln)
        if t:
            out.append(t)
    return out[:n]


def _str_any(v, ln=400):
    """A string from a string or list-of-strings field (e.g. seen_before)."""
    if isinstance(v, list):
        return _txt("; ".join(_txt(x, 200) for x in v if x and not isinstance(x, (dict, list))), ln)
    return _txt(v, ln)


_ODDS = ("low", "medium", "high")
_CURS = ("INR", "CAD", "USD")


def _clean_impact(v):
    """Node impact gauges: size / sure / when plus score and top-5 rank. None when absent."""
    if not isinstance(v, dict):
        return None
    size = _txt(v.get("size"), 8).lower()
    sure = _txt(v.get("sure"), 8).lower()
    when = _txt(v.get("when"), 8).lower()
    out = {"size": size if size in ("small", "medium", "big") else "",
           "sure": sure if sure in ("low", "medium", "high") else "",
           "when": when if when in ("days", "weeks", "months") else "",
           "score": _num(v.get("score")), "top5": _int(v.get("top5"), 0, 5, 0)}
    return out if (out["size"] or out["sure"] or out["when"] or out["top5"]) else None


def _clean_card(c):
    """Story card of a hub node (every field optional). Returns None when there is nothing usable."""
    if not isinstance(c, dict):
        return None
    fy = c.get("for_you") if isinstance(c.get("for_you"), dict) else {}
    fy2 = {}
    alias = {"india": "India", "us": "US", "usa": "US", "united states": "US", "canada": "Canada"}
    for kk, vv in fy.items():
        K = alias.get(str(kk).strip().lower())
        if not K:
            continue
        if isinstance(vv, dict):
            dd = _txt(vv.get("dir") or vv.get("direction"), 10).lower()
            dd = {"up": "up", "down": "down", "neutral": "neutral", "flat": "neutral", "mixed": "neutral"}.get(dd, "")
            if _txt(vv.get("why"), 240) or dd:
                fy2[K] = {"dir": dd, "why": _txt(vv.get("why"), 240)}
        elif _txt(vv, 240):
            fy2[K] = {"dir": "", "why": _txt(vv, 240)}
    cur = c.get("currency") if isinstance(c.get("currency"), dict) else {}
    cur2 = {}
    for k, v in cur.items():
        K = str(k).strip().upper()
        if K not in _CURS:
            continue
        if isinstance(v, str):
            v = {"why": v}
        if not isinstance(v, dict):
            continue
        d = _txt(v.get("dir") or v.get("direction"), 10).lower()
        d = {"up": "stronger", "down": "weaker", "strengthen": "stronger", "strengthens": "stronger", "weaken": "weaker", "weakens": "weaker", "stable": "flat", "steady": "flat"}.get(d, d)
        cur2[K] = {"dir": d if d in ("stronger", "weaker", "flat") else "flat", "why": _txt(v.get("why"), 220)}
    preds = []
    for p in c.get("predictions") if isinstance(c.get("predictions"), list) else []:
        if not isinstance(p, dict):
            continue
        odds = _txt(p.get("odds"), 10).lower()
        q = {"if": _txt(p.get("if"), 200), "then": _txt(p.get("then"), 200), "odds": odds if odds in _ODDS else "",
             "hz": _txt(p.get("horizon"), 40), "why": _txt(p.get("reasoning"), 360), "watch": _txt(p.get("watch"), 220),
             "wrong": _txt(p.get("wrong_if"), 220)}
        if q["then"] or q["if"]:
            preds.append(q)
    h = c.get("history") if isinstance(c.get("history"), dict) else {}
    hist = {}
    if h.get("days_running") is not None or h.get("days") is not None:
        hist["days"] = _int(h.get("days_running", h.get("days")), 0, 9999, 0)
    if h.get("stories") is not None:
        hist["stories"] = _int(h.get("stories"), 0, 9999, 0)
    if _str_any(h.get("seen_before")):
        hist["seen"] = _str_any(h.get("seen_before"), 320)
    pat = c.get("pattern")
    out = {"so": _txt(c.get("so_what"), 200), "hl": _txt(c.get("headline"), 240), "pts": [_txt(x, 320) for x in (c.get("points") if isinstance(c.get("points"), list) else [])[:5]], "light": bool(c.get("light")), "fy": fy2, "cur": cur2, "pred": preds[:4],
           "ag": _lst(c.get("against"), 4, 280), "pat": _str_any(pat, 600), "hist": hist}
    return out if (out["so"] or out["hl"] or any(out["pts"]) or out["pred"] or out["cur"] or out["fy"]) else None


def _clean_hist(h):
    """Archive stats of a node (every field optional)."""
    if not isinstance(h, dict):
        return None
    out = {}
    for k in ("days_running", "stories", "last7", "prev7"):
        if h.get(k) is not None:
            out[k] = _int(h.get(k), 0, 99999, 0)
    t = _txt(h.get("trend"), 12).lower()
    if t in ("heating", "cooling", "steady", "new", "unknown"):
        out["trend"] = t
    for k, kk, n in (("first_seen", "first", 20), ("note", "note", 320)):
        if _txt(h.get(k), n):
            out[kk] = _txt(h.get(k), n)
    hr = _num(h.get("hit_rate"))
    if hr is not None:
        out["hit"] = round(hr, 2)
    past = []
    for p in h.get("past_predictions") if isinstance(h.get("past_predictions"), list) else []:
        if isinstance(p, dict) and (p.get("if") or p.get("then")):
            past.append({"if": _txt(p.get("if"), 160), "then": _txt(p.get("then"), 160), "st": _txt(p.get("status"), 20).lower(),
                         "ev": _txt(p.get("evidence"), 200)})
    if past:
        out["past"] = past[:3]
    return out or None


def _clean_market(graph):
    out = []
    m = graph.get("market") if isinstance(graph, dict) else None
    for x in m if isinstance(m, list) else []:
        if isinstance(x, dict) and _txt(x.get("label"), 40):
            out.append({"l": _txt(x.get("label"), 40), "p": _txt(x.get("price"), 24), "c": _txt(x.get("change"), 24), "cp": _num(x.get("change_pct"))})
    return out[:30]


def _clean_ecard(c):
    if not isinstance(c, dict):
        return None
    out = {"steps": _lst(c.get("steps"), 6, 120), "ev": _txt(c.get("evidence"), 420), "wk": _str_any(c.get("weaken"), 360),
           "nx": _str_any(c.get("next"), 360), "seen": _str_any(c.get("seen_before"), 320)}
    return out if any(out.values()) else None


def _clean_patterns(graph):
    pt = graph.get("patterns") if isinstance(graph, dict) and isinstance(graph.get("patterns"), dict) else {}
    out = {}
    for k in ("heating", "cooling"):
        v = _lst(pt.get(k), 6, 200)
        if v:
            out[k] = v
    return out


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
            "src": src[:6], "mkt": mkt, "thr": thr, "card": _clean_card(n.get("card")), "impact": _clean_impact(n.get("impact")), "hist": _clean_hist(n.get("history"))})
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
            "order": _int(e.get("order"), 1, 3, 1), "kind": kind, "why": _txt(e.get("why"), 300), "lag": _txt(e.get("lag"), 40),
            "weak": bool(e.get("weak")), "card": _clean_ecard(e.get("card"))})
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
    return nodes, edges


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
        out[-1] = out[-1][: width - 1].rstrip() + "…"
    return [o[: width + 2] for o in out] or [""]


# half-width, half-height of each shape (used for ports and label placement)
_EXT = {"event": (16, 16), "country": (20, 20), "company": (15, 15), "sector": (19, 17), "commodity": (22, 13),
        "index": (20, 18), "institution": (18, 18)}


def _shape_path(kind):
    """Outline centred on (0, 0); the browser scales/translates it. Pure constants, no user data."""
    if kind == "event":
        return "M-16 0A16 16 0 1 0 16 0A16 16 0 1 0 -16 0Z"
    if kind == "country":
        return "M0 -20L20 0L0 20L-20 0Z"
    if kind == "company":
        return "M-11 -15H11A4 4 0 0 1 15 -11V11A4 4 0 0 1 11 15H-11A4 4 0 0 1 -15 11V-11A4 4 0 0 1 -11 -15Z"
    if kind == "sector":
        pts = [f"{19 * math.cos(math.radians(a)):.1f} {19 * math.sin(math.radians(a)):.1f}" for a in range(0, 360, 60)]
        return "M" + "L".join(pts) + "Z"
    if kind == "commodity":
        return "M-9 -13H9A13 13 0 0 1 9 13H-9A13 13 0 0 1 -9 -13Z"
    if kind == "index":
        return "M0 -18L20 15L-20 15Z"
    return "M0 -18L18 -6V15H-18V-6Z"


def _lane_of(n):
    return n["di"] if n["di"] >= 0 else _LANE_OTHER


# ---------------------------------------------------------------- key chains

def _find_chains(nodes, edges, limit=8):
    """Top multi-step causal chains (3-5 nodes, following causal edges), best first, with little overlap."""
    byid = {n["id"]: n for n in nodes}
    adj = {n["id"]: [] for n in nodes}
    cin = {n["id"]: 0 for n in nodes}
    cout = {n["id"]: 0 for n in nodes}
    deg = {n["id"]: 0 for n in nodes}
    for k, e in enumerate(edges):
        deg[e["from"]] += 1
        deg[e["to"]] += 1
        if e["kind"] == "causal":
            adj[e["from"]].append((e["to"], k))
            cin[e["to"]] += 1
            cout[e["from"]] += 1
    paths, budget = [], [40000]

    def dfs(path, eis):
        if budget[0] <= 0:
            return
        if len(path) >= 3:
            paths.append((tuple(path), tuple(eis)))
            budget[0] -= 1
        if len(path) == 5:
            return
        for t, k in adj[path[-1]]:
            if t in path:
                continue
            path.append(t)
            eis.append(k)
            dfs(path, eis)
            path.pop()
            eis.pop()

    for n in nodes:
        dfs([n["id"]], [])
    min_len = 3
    if not paths:  # tiny graphs: fall back to single strong links
        min_len = 2
        for k, e in enumerate(edges):
            if e["kind"] == "causal":
                paths.append(((e["from"], e["to"]), (k,)))

    def score(path, eis):
        q = 0.0
        for k in eis:
            e = edges[k]
            q += e["strength"] * 1.3 + _CONF.get(e["conf"], 2) * 0.8 + (1.2 if e["status"] == "reported" else 0.0)
        hub = sum(min(deg[i], 10) for i in path) * 0.25
        mk = sum(1 for i in path if byid[i]["mkt"]) * 1.2
        cross = (len({_lane_of(byid[i]) for i in path}) - 1) * 1.6
        ends = (1.5 if cin[path[0]] == 0 else 0.0) + (1.5 if (byid[path[-1]]["mkt"] or cout[path[-1]] == 0) else 0.0)
        weak = -3.0 if any(edges[k]["strength"] == 1 for k in eis) else 0.0
        return q + hub + mk + cross + ends + weak

    ranked = sorted(((score(p, es), p, es) for p, es in paths), key=lambda r: (-r[0], r[1]))
    chosen = []
    for sc, p, es in ranked:
        if len(p) < min_len:
            continue
        ok = True
        for c in chosen:
            shared = len(set(p) & set(c["n"]))
            if shared > min(len(p), len(c["n"])) // 2 or (p[0] == c["n"][0] and p[-1] == c["n"][-1]) or p[-1] == c["n"][-1] and p[0] == c["n"][0]:
                ok = False
                break
            if p[0] == c["n"][0] and shared >= 2:
                ok = False
                break
        if not ok:
            continue
        stat = {edges[k]["status"] for k in es}
        labels = [edges[k]["label"] for k in es if edges[k]["label"]]
        chosen.append({
            "id": f"c{len(chosen)}", "n": list(p), "e": list(es), "sc": round(sc, 1),
            "title": f'{byid[p[0]]["label"]} → {byid[p[-1]]["label"]}',
            "sum": " › ".join(labels),
            "d": list(dict.fromkeys(_lane_of(byid[i]) if _lane_of(byid[i]) < 7 else -1 for i in p)),
            "st": "reported" if stat == {"reported"} else ("predicted" if stat == {"predicted"} else "mixed")})
        if len(chosen) >= limit:
            break
    return chosen


# ---------------------------------------------------------------- layered layout (deterministic)

def _assign_layers(ids, edges):
    """Causal depth: longest path from causes to effects over causal links (cycles broken by DFS),
    sources pulled next to their first consumer. Returns {id: layer} with layers starting at 0."""
    order_ix = {i: k for k, i in enumerate(ids)}
    idset = set(ids)
    succ = {i: [] for i in ids}
    indeg = {i: 0 for i in ids}
    deg = {i: 0 for i in ids}
    for e in edges:
        if e["from"] in idset and e["to"] in idset:
            deg[e["from"]] += 1
            deg[e["to"]] += 1
            if e["kind"] == "causal":
                succ[e["from"]].append(e["to"])
                indeg[e["to"]] += 1
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
    L = {}

    def lay(i):
        if i not in L:
            L[i] = 0
            L[i] = 0 if not preds[i] else max(lay(p) for p in preds[i]) + 1
        return L[i]

    for i in ids:
        lay(i)
    for i in ids:
        if not preds[i] and fsucc[i]:
            L[i] = min(L[s] for s in fsucc[i]) - 1
    lo = min(L.values()) if L else 0
    return {i: L[i] - lo for i in ids}


def _seg_cross(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0


def _flow_layout(nodes, edges, lane_of, cap=3, layers=None, ghosts=(), ghost_cap=5, row_h=ROW_H, col_w=COL_W, gutter=GUTTER, tail=LANE_TAIL, min_h=0):
    """Columns = causal depth (causes left, outcomes right); rows = topic lanes; inside a lane the
    nodes of one column stack in slots ordered by barycentre sweeps to cut crossings. A column
    holding more than `cap` nodes of one lane splits into sub-columns to its left, so lanes stay short.
    Returns dict(pos, lanes, W, H, col)."""
    ids = [n["id"] for n in nodes]
    order_ix = {i: k for k, i in enumerate(ids)}
    idset = set(ids)
    ghosts = set(ghosts)
    L = dict(layers) if layers is not None else _assign_layers(ids, [e for e in edges if e["from"] in idset and e["to"] in idset])
    wdeg = {i: 0 for i in ids}
    nbr = {i: [] for i in ids}
    for e in edges:
        if e["from"] in idset and e["to"] in idset:
            wdeg[e["from"]] += e["strength"]
            wdeg[e["to"]] += e["strength"]
            nbr[e["from"]].append(e["to"])
            nbr[e["to"]].append(e["from"])
    lane = {i: lane_of[i] for i in ids}

    # lane order: short cross-lane links first
    used = sorted(set(lane.values()))
    wt = {}
    for e in edges:
        if e["from"] in idset and e["to"] in idset:
            a, b = lane[e["from"]], lane[e["to"]]
            if a != b:
                k = (min(a, b), max(a, b))
                wt[k] = wt.get(k, 0) + e["strength"] + (0.5 if e["kind"] == "causal" else 0)
    best = tuple(used)
    if len(used) > 1:
        bestc = None
        for perm in itertools.permutations(used):
            pos = {l: k for k, l in enumerate(perm)}
            c = sum(w * abs(pos[a] - pos[b]) for (a, b), w in wt.items())
            if bestc is None or c < bestc - 1e-9:
                best, bestc = perm, c

    # sub-columns for crowded cells
    cell = {}
    for i in ids:
        cell.setdefault((lane[i], L[i]), []).append(i)
    sub = {}
    for key, mem in cell.items():
        mem.sort(key=lambda i: (-wdeg[i], order_ix[i]))
        c = ghost_cap if all(m in ghosts for m in mem) else cap
        parts = max(1, math.ceil(len(mem) / c))
        chunk = math.ceil(len(mem) / parts)
        for j, i in enumerate(mem):
            sub[i] = j // chunk
    nsub = {}
    for i in ids:
        nsub[L[i]] = max(nsub.get(L[i], 1), sub[i] + 1)
    colidx, ncol = {}, 0
    for layer in sorted(nsub):
        for s in range(nsub[layer] - 1, -1, -1):
            colidx[(layer, s)] = ncol
            ncol += 1
    col = {i: colidx[(L[i], sub[i])] for i in ids}

    groups = {}
    for i in ids:
        groups.setdefault((lane[i], col[i]), []).append(i)
    R = {l: max([len(g) for (ln, c), g in groups.items() if ln == l] or [1]) for l in best}
    heights = {l: max(min_h, SLOT_PAD + (R[l] - 1) * row_h + tail) for l in best}
    tops, y = {}, PAD_TOP
    for l in best:
        tops[l] = y
        y += heights[l] + LANE_GAP
    H = y - LANE_GAP + 8
    slot = {}
    for g in groups.values():
        g.sort(key=lambda i: order_ix[i])
        for k, i in enumerate(g):
            slot[i] = k

    def ypos(i, sl=None):
        return tops[lane[i]] + SLOT_PAD + (slot[i] if sl is None else sl[i]) * row_h

    X0 = gutter + 56

    def xpos(i):
        return X0 + col[i] * col_w

    elist = [(e["from"], e["to"]) for e in edges if e["from"] in idset and e["to"] in idset]

    def crossings(sl):
        pts = {i: (xpos(i), ypos(i, sl)) for i in ids}
        segs = [(pts[a], pts[b], a, b) for a, b in elist]
        n = 0
        for x in range(len(segs)):
            for z in range(x + 1, len(segs)):
                p, q, a, b = segs[x]
                r, s, c, d = segs[z]
                if a in (c, d) or b in (c, d):
                    continue
                if _seg_cross(p, q, r, s):
                    n += 1
        return n

    bestslot, bestn = dict(slot), crossings(slot)
    for it in range(10):
        cols = list(range(ncol)) if it % 2 == 0 else list(range(ncol - 1, -1, -1))
        for c in cols:
            for l in best:
                g = groups.get((l, c))
                if not g:
                    continue
                tgt = {}
                for i in g:
                    others = [j for j in nbr[i] if col[j] != c] or nbr[i]
                    tgt[i] = (sum(ypos(j) for j in others) / len(others)) if others else ypos(i)
                g.sort(key=lambda i: (tgt[i], order_ix[i]))
                want = [min(R[l] - 1, max(0, int(round((tgt[i] - tops[l] - SLOT_PAD) / row_h)))) for i in g]
                for k in range(1, len(want)):
                    want[k] = max(want[k], want[k - 1] + 1)
                over = (want[-1] - (R[l] - 1)) if want else 0
                if over > 0:
                    want = [w - over for w in want]
                    for k in range(len(want) - 2, -1, -1):
                        want[k] = min(want[k], want[k + 1] - 1)
                    want = [max(0, w) for w in want]
                    for k in range(1, len(want)):
                        want[k] = max(want[k], want[k - 1] + 1)
                for i, w in zip(g, want):
                    slot[i] = w
        n = crossings(slot)
        if n < bestn:
            bestslot, bestn = dict(slot), n
    slot = bestslot
    pos = {i: (xpos(i), ypos(i)) for i in ids}
    W = X0 + (ncol - 1) * col_w + 66
    lanes = [(l, tops[l], heights[l]) for l in best]
    return {"pos": pos, "lanes": lanes, "W": W, "H": H, "col": col, "layer": L, "crossings": bestn}


# ---------------------------------------------------------------- "Full map": the first detailed layout (ported)

OROW_H = 70
OLANE_GAP = 8
OPAD_X = 120
OPAD_Y = 8

SAME_DX = 108   # min x distance between linked nodes in one lane (labels need room)
CROSS_DX = 48   # min x distance between linked nodes in different lanes (link runs diagonally)
MIN_ROW_DX = 106  # nodes of one lane row never closer than this


def _old_layout(nodes, edges):
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
    heights = {l: 26 + (nrows[l] - 1) * OROW_H + 62 for l in best}
    tops, y = {}, OPAD_Y
    for l in best:
        tops[l] = y
        y += heights[l] + OLANE_GAP
    H = y - OLANE_GAP + OPAD_Y
    X0 = OPAD_X + 40
    pos = {i: (X0 + xs[i], tops[lane[i]] + 26 + row[i] * OROW_H) for i in ids}
    W = X0 + max(xs.values()) + 56
    lanes = [(l, tops[l], heights[l]) for l in best]
    return pos, lanes, W, H, {i: pos[i][0] for i in ids}


def _full_layout(nodes, edges):
    """The first detailed layout with roomier columns so every node name and arrow label has space."""
    global SAME_DX, CROSS_DX, MIN_ROW_DX
    old = (SAME_DX, CROSS_DX, MIN_ROW_DX)
    SAME_DX, CROSS_DX, MIN_ROW_DX = 190, 110, 120
    try:
        return _old_layout(nodes, edges)
    finally:
        SAME_DX, CROSS_DX, MIN_ROW_DX = old


def _old_route(edges, nodes_by_id, pos, layer):
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


# ---------------------------------------------------------------- edge routing around nodes

def _bez(p, t):
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = p
    u = 1 - t
    return (u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
            u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3)


def _spread(k, n, step):
    return (k - (n - 1) / 2) * min(step, 20 / max(n, 1))


def _fan(k, n):
    """Angle in degrees for port k of n: evenly spread, wider for busier nodes (capped so ports stay clear of the label)."""
    half = min(64.0, 13.0 * (n - 1) / 2 + 8)
    return -half + 2 * half * k / (n - 1)


def _route_edges(edge_ids, edges, pos, byid):
    """One cubic per edge from the right side of the source to the left side of the target. Links that
    would run through other nodes (or their labels) bow above/below them; the cheapest clean candidate
    wins. Returns {edge_index: [x0,y0,x1,y1,x2,y2,x3,y3]}."""
    boxes = {}
    for nid, (x, y) in pos.items():
        hw, hh = _EXT[byid[nid]["type"]]
        lines = _wrap(byid[nid]["label"])
        lw = max(len(s) for s in lines) * 3.7 + 3
        half = max(hw, lw) + 3
        boxes[nid] = (x - half, y - hh - 4, x + half, y + hh + 6 + 14 * len(lines))
    outp, inp = {}, {}
    for k in edge_ids:
        e = edges[k]
        outp.setdefault(e["from"], []).append(k)
        inp.setdefault(e["to"], []).append(k)
    off_o, off_i = {}, {}
    ang_o, ang_i = {}, {}   # fan angles (radians) for busy nodes: ports spread around the shape, not stacked
    for nid, lst in outp.items():
        lst.sort(key=lambda k: (pos[edges[k]["to"]][1], k))
        for j, k in enumerate(lst):
            if len(lst) >= 3:
                ang_o[k] = math.radians(_fan(j, len(lst)))
            else:
                off_o[k] = _spread(j, len(lst), 6)
    for nid, lst in inp.items():
        lst.sort(key=lambda k: (pos[edges[k]["from"]][1], k))
        for j, k in enumerate(lst):
            if len(lst) >= 3:
                ang_i[k] = math.radians(_fan(j, len(lst)))
            else:
                off_i[k] = _spread(j, len(lst), 6)
    out = {}
    order = sorted(edge_ids, key=lambda k: (-edges[k]["strength"], k))
    for k in order:
        e = edges[k]
        a, b = e["from"], e["to"]
        (xa, ya), (xb, yb) = pos[a], pos[b]
        hwa = _EXT[byid[a]["type"]][0]
        hwb = _EXT[byid[b]["type"]][0]
        hha = _EXT[byid[a]["type"]][1]
        hhb = _EXT[byid[b]["type"]][1]
        ta, tb = ang_o.get(k), ang_i.get(k)
        if ta is not None:
            p0 = (xa + (hwa + 2) * math.cos(ta), ya + (hha + 3) * math.sin(ta))
        else:
            p0 = (xa + hwa + 2, ya + off_o.get(k, 0))
        if tb is not None:
            p3 = (xb - (hwb + 3) * math.cos(tb), yb + (hhb + 3) * math.sin(tb))
        else:
            p3 = (xb - hwb - 3, yb + off_i.get(k, 0))
        dx = p3[0] - p0[0]
        fwd = dx > 24
        ax = max(34.0, 0.42 * dx) if fwd else 64.0 + 0.1 * abs(ya - yb)
        bows = [0, 26, -26, 52, -52, 82, -82, 118, -118, 150, -150]
        if not fwd:
            bows = [b_ for b_ in bows if abs(b_) >= 52] or bows
        best_c, best_s = None, None
        for bw in bows:
            for b1, b2 in ((bw, bw), (bw, 0), (0, bw)):
                if bw == 0 and (b1, b2) != (0, 0):
                    continue
                c1 = (p0[0] + ax, p0[1] + b1 + (ax * 0.55 * math.sin(ta) if ta is not None else 0))
                c2 = (p3[0] - ax, p3[1] + b2 + (ax * 0.55 * math.sin(tb) if tb is not None else 0))
                pts = [p0, c1, c2, p3]
                xs = [q[0] for q in pts]
                ys = [q[1] for q in pts]
                bb = (min(xs), min(ys), max(xs), max(ys))
                near = [nid for nid, bx in boxes.items() if nid not in (a, b) and not (bx[2] < bb[0] or bx[0] > bb[2] or bx[3] < bb[1] or bx[1] > bb[3])]
                hits = 0
                for s in range(1, 26):
                    px, py = _bez(pts, s / 26.0)
                    for nid in near:
                        bx = boxes[nid]
                        if bx[0] <= px <= bx[2] and bx[1] <= py <= bx[3]:
                            hits += 1
                            break
                # own label box: the curve should not cross the source/target label region either
                cost = hits * 100 + abs(b1) + abs(b2) + (0 if fwd else 30)
                if best_s is None or cost < best_s:
                    best_c, best_s = pts, cost
                if hits == 0 and bw == 0:
                    break
            if best_s is not None and best_s < 100 and bw == 0:
                break
            if best_s is not None and best_s < 100 and bw != 0 and abs(bw) >= 26:
                # a clean bowed candidate exists; larger bows only cost more
                break
        out[k] = [round(v, 1) for q in best_c for v in q]
    return out
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
    items.append(f'<span>{lk("", "M1 6 H27", WD)}<span>predicted (dashed = forecast, not yet reported)</span></span>')
    if has.get("feedback"):
        items.append(f'<span><svg class="lk" viewBox="0 0 36 12" aria-hidden="true"><path d="M3 11 C3 -4 31 -4 31 8" stroke-width="2"/><path class="hd" d="M31 11 L27.5 6 L34.5 6Z"/></svg><span>feedback loop</span></span>')
    if has.get("contradicts"):
        items.append(f'<span><svg class="lk" viewBox="0 0 36 12" aria-hidden="true"><path class="con" d="M1 6 H28" stroke-width="2.4" stroke-dasharray="1.5 4" stroke-linecap="round"/><path class="con" d="M32 1 V11" stroke-width="2.6" stroke-linecap="round"/></svg><span>contradicts</span></span>')
    items.append('<span>thicker = stronger; fainter = less confident; arrow tip = direction</span>')
    items.append('<span class="sep"></span><span><b>Node</b></span><span>\u25B2 \u25BC rising or falling</span>')
    return '<div class="legend">' + "".join(items) + "</div>"


# ---------------------------------------------------------------- connection map

# ---------------------------------------------------------------- scenes (topic views)

def _pick_layout(nodes, edges, lane_of, caps, tw, th, **kw):
    """Try several column heights and keep the layout that shows biggest in a tw x th viewport."""
    best, bs = None, None
    for cap in caps:
        r = _flow_layout(nodes, edges, lane_of, cap=cap, **kw)
        s = min(tw / (r["W"] + 24.0), th / (r["H"] + 24.0), 1.2)
        key = (round(s, 2), -r["crossings"])
        if bs is None or key > bs:
            best, bs = r, key
    return best


def _scene_json(res, routes, ghosts=(), with_lanes=False, cw=None, gut=None):
    out = {"W": round(res["W"]), "H": round(res["H"]),
           "p": {i: [round(x, 1), round(y, 1)] for i, (x, y) in res["pos"].items()},
           "e": {str(k): v for k, v in routes.items()}}
    if cw:
        out["cw"] = cw
    if gut:
        out["gut"] = gut
    if ghosts:
        out["g"] = sorted(ghosts)
    if with_lanes:
        out["lanes"] = [[l if l < 7 else -1, round(t), round(h)] for l, t, h in res["lanes"]]
    return out


def _topic_scene(members, edges, byid, order_ix, ghost_cap=10, tw=740, th=560, col_w=COL_W, row_h=ROW_H, gutter=GUTTER):
    """Nodes of one topic laid out by causal depth, plus the strongest outside neighbours as ghosts
    (causes in a column on the left, effects in a column on the right)."""
    mids = [m["id"] for m in members]
    mset = set(mids)
    inner = [k for k, e in enumerate(edges) if e["from"] in mset and e["to"] in mset]
    L = _assign_layers(mids, [edges[k] for k in inner])
    gs = {}
    for k, e in enumerate(edges):
        a_in, b_in = e["from"] in mset, e["to"] in mset
        if a_in == b_in:
            continue
        other = e["to"] if a_in else e["from"]
        rec = gs.setdefault(other, [0, False, []])
        rec[0] += e["strength"]
        rec[1] = rec[1] or (not a_in)  # the outside node causes a member
        rec[2].append(k)
    ranked = sorted(gs, key=lambda i: (-gs[i][0], order_ix[i]))[:ghost_cap]
    maxl = max(L.values()) if L else 0
    layers = {i: v + 1 for i, v in L.items()}
    for g in ranked:
        layers[g] = 0 if gs[g][1] else maxl + 2
    gset = set(ranked)
    ekeys = inner + sorted(k for g in ranked for k in gs[g][2])
    ekeys = list(dict.fromkeys(ekeys))
    sub_nodes = [byid[i] for i in mids] + [byid[g] for g in ranked]
    res = _pick_layout(sub_nodes, [edges[k] for k in ekeys], {n["id"]: 0 for n in sub_nodes}, (3, 4, 5, 6, 8, 11), tw, th,
                       layers=layers, ghosts=gset, ghost_cap=6, col_w=col_w, row_h=row_h, gutter=gutter)
    routes = _route_edges(ekeys, edges, res["pos"], byid)
    return _scene_json(res, routes, gset, cw=col_w)


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
    """Interactive connection map (key chains / by topic / everything). Returns '' if the graph is unusable."""
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
    order_ix = {n["id"]: k for k, n in enumerate(nodes)}
    lane_of = {n["id"]: _lane_of(n) for n in nodes}

    # everything: layered layout with topic rows
    res = _pick_layout(nodes, edges, lane_of, (2, 3, 4, 5), 1000, 860, row_h=100, col_w=132, gutter=190, tail=76, min_h=150)
    lay = {"all": _scene_json(res, _route_edges(range(len(edges)), edges, res["pos"], byid), (), True, cw=132, gut=190)}
    # "Full map": the first detailed layout (wide topic lanes, a name on every node, labelled arrows)
    fpos, flanes, fW, fH, flayer = _full_layout(nodes, edges)
    froutes = _old_route(edges, byid, fpos, flayer)
    lay["full"] = _scene_json({"W": fW, "H": fH, "pos": fpos, "lanes": flanes},
                              {k: [round(c, 1) for pt in r["pts"] for c in pt] for k, r in enumerate(froutes)}, (), True, cw=150, gut=OPAD_X + 10)
    # phone variants (key ":m"): chosen for a tall, narrow viewport so the whole map fits at a readable scale
    resm = _pick_layout(nodes, edges, lane_of, (2, 3, 4, 5, 6, 8), 366, 720, row_h=90, col_w=150, gutter=215, tail=82)
    lay["all:m"] = _scene_json(resm, _route_edges(range(len(edges)), edges, resm["pos"], byid), (), True, cw=150, gut=215)

    # topics: one per domain (and per region with at least two nodes)
    members_by = {}
    for n in nodes:
        members_by.setdefault(_lane_of(n), []).append(n)
    topics = []
    for l in sorted(members_by, key=lambda l: (-len(members_by[l]), l)):
        key = f"d{l}" if l < 7 else "dx"
        topics.append({"k": key, "t": "d", "d": l if l < 7 else -1, "l": DOMAINS[l] if l < 7 else "Other", "n": len(members_by[l])})
        lay["t:" + key] = _topic_scene(members_by[l], edges, byid, order_ix)
        lay["t:" + key + ":m"] = _topic_scene(members_by[l], edges, byid, order_ix, tw=366, th=720, col_w=148, row_h=118, gutter=34)
    regions = {}
    for n in nodes:
        if n["region"]:
            regions.setdefault(n["region"], []).append(n)
    for r in sorted(regions, key=lambda r: (-len(regions[r]), r)):
        if len(regions[r]) < 2:
            continue
        topics.append({"k": "r:" + r, "t": "r", "d": -2, "l": r, "n": len(regions[r])})
        lay["t:r:" + r] = _topic_scene(regions[r], edges, byid, order_ix)
        lay["t:r:" + r + ":m"] = _topic_scene(regions[r], edges, byid, order_ix, tw=366, th=720, col_w=148, row_h=118, gutter=34)

    chains = _find_chains(nodes, edges)

    deg = {n["id"]: 0 for n in nodes}
    for e in edges:
        deg[e["from"]] += 1
        deg[e["to"]] += 1
    types_used, doms_used, jn = set(), {}, {}
    for n in nodes:
        l = _lane_of(n)
        types_used.add(n["type"])
        doms_used[l] = doms_used.get(l, 0) + 1
        dname = DOMAINS[l] if l < 7 else (n["dom"] or "")
        jn[n["id"]] = {"l": n["label"], "t": n["type"], "d": l if l < 7 else -1, "dn": dname, "r": n["region"],
                       "det": n["detail"], "why": n["why"], "dir": n["dir"], "src": n["src"], "k": deg[n["id"]]}
        if n["mkt"]:
            jn[n["id"]]["mkt"] = n["mkt"]
        if n["thr"]:
            jn[n["id"]]["thr"] = n["thr"]
        if n.get("card"):
            jn[n["id"]]["c"] = n["card"]
        if n.get("hist"):
            jn[n["id"]]["h"] = n["hist"]
        if n.get("impact"):
            jn[n["id"]]["im"] = n["impact"]
    hub = max(nodes, key=lambda n: (deg[n["id"]], -order_ix[n["id"]]))["id"]
    ltop = {}
    for l, mem in members_by.items():
        top = max(mem, key=lambda n: (deg[n["id"]], -order_ix[n["id"]]))
        t = (top.get("card") or {}).get("hl") or top["detail"] or top["why"] or ""
        first = re.split(r"(?<=[.!?])\s", t)[0].strip()
        if first:
            ltop[str(l if l < 7 else -1)] = (first if len(first) <= 62 else first[:60].rsplit(" ", 1)[0] + "\u2026")
    je = [{"f": e["from"], "t": e["to"], "label": e["label"], "effect": e["effect"], "s": e["strength"], "conf": e["conf"],
           "status": e["status"], "order": e["order"], "kind": e["kind"], "why": e["why"], "lag": e["lag"], **({"weak": True} if e.get("weak") else {}), **({"c": e["card"]} if e.get("card") else {})} for e in edges]
    payload = {"uid": uid, "nodes": jn, "edges": je, "chains": chains, "topics": topics, "lay": lay, "hub": hub,
               "typeLabel": TYPE_LABEL, "short": SHORT, "doms": DOMAINS, "pat": _clean_patterns(graph), "mk": _clean_market(graph), "ltop": ltop,
               "shapes": {t: _shape_path(t) for t in TYPES}, "ext": {t: list(_EXT[t]) for t in TYPES}}

    defs = ['<defs>']
    for s, size in ((1, 8), (2, 10), (3, 12), (4, 15)):
        for eff in ("up", "down", "mixed", "none"):
            defs.append(f'<marker id="mk{uid}-{eff}{s}" viewBox="0 0 10 10" refX="9.5" refY="5" markerWidth="{size}" markerHeight="{size}" '
                        f'markerUnits="userSpaceOnUse" orient="auto"><path class="mk e-{eff}" d="M0 0.8 L10 5 L0 9.2Z"/></marker>')
        defs.append(f'<marker id="mk{uid}-bar{s}" viewBox="0 0 10 14" refX="5" refY="7" markerWidth="{size * 0.8:.0f}" markerHeight="{size * 1.12:.0f}" '
                    f'markerUnits="userSpaceOnUse" orient="auto"><path class="mk bar" d="M5 1 V13"/></marker>')
    defs.append("</defs>")
    svg = (f'<svg viewBox="0 0 800 400" role="group" preserveAspectRatio="xMidYMid meet" '
           f'aria-label="Connection map: {len(nodes)} nodes, {len(edges)} links. Use the view switch above the map to change what is shown.">'
           f'{"".join(defs)}<g class="scene"></g></svg>')

    # everything-view filters
    chips = "".join(_chip_html(l if l < 7 else -1, DOMAINS[l] if l < 7 else "Other", c) for l, c in sorted(doms_used.items())) if len(doms_used) > 1 else ""
    reg_names = sorted({n["region"] for n in nodes if n["region"]})
    flt = []
    if chips:
        flt.append(f'<div class="grp" role="group" aria-label="Show or hide topics">{chips}</div>')
    if len(reg_names) > 1:
        flt.append(_select("region", "Region", [("", "All regions")] + [(r, r) for r in reg_names]))
    if len({e["conf"] or "medium" for e in edges}) > 1:
        flt.append(_select("conf", "Minimum confidence", [("1", "Any confidence"), ("2", "Medium + high"), ("3", "High only")]))
    if any(e["order"] > 1 for e in edges):
        flt.append(_select("order", "Chain depth", [("3", "All chain steps"), ("2", "Up to 2 steps"), ("1", "Direct links only")]))
    if len({e["status"] for e in edges}) > 1:
        flt.append(_select("status", "Status", [("", "Reported + predicted"), ("reported", "Reported only"), ("predicted", "Predicted only")]))
    flt.append('<button type="button" class="clink cclear" hidden>Clear filters</button>')
    filters = (f'<details class="cflt" hidden><summary>Filters<span class="ccount" aria-live="polite"></span></summary>'
               f'<div class="cfilters">{"".join(flt)}</div></details>')

    seg = ('<div class="seg" role="radiogroup" aria-label="Map view">'
           '<button type="button" role="radio" aria-checked="true" data-view="chains">Key chains</button>'
           '<button type="button" role="radio" aria-checked="false" data-view="topic">By topic</button>'
           '<button type="button" role="radio" aria-checked="false" data-view="all">Everything</button>'
           '<button type="button" role="radio" aria-checked="false" data-view="full">Full map</button></div>')
    search = ''
    if len(nodes) >= 4:
        search = (f'<div class="csw"><input class="csearch" type="search" role="combobox" aria-expanded="false" aria-controls="sres{uid}" '
                  f'aria-autocomplete="list" placeholder="Find a node" aria-label="Search nodes" autocomplete="off">'
                  f'<ul class="sres" id="sres{uid}" role="listbox" aria-label="Matching nodes" hidden></ul></div>')
    zoom = ('<div class="czoom"><button type="button" data-act="in" aria-label="Zoom in" title="Zoom in (+)">+</button>'
            '<button type="button" data-act="out" aria-label="Zoom out" title="Zoom out (\u2212)">\u2212</button>'
            '<button type="button" data-act="fit" class="fit" aria-label="Fit to view" title="Fit to view (0)">Fit</button></div>')
    nos = ""
    if chains:
        nos = ('<noscript><ol class="nos">' + "".join(f'<li>{_e(c["title"])}: {_e(c["sum"])}</li>' for c in chains) + "</ol></noscript>")
    tools = ('<div class="mtools"><div class="lens" role="radiogroup" aria-label="Currency lens: dim everything except what touches one currency">'
             '<span class="lbl">Currency lens</span><button type="button" role="radio" aria-checked="true" data-lens="">All</button>'
             '<button type="button" role="radio" aria-checked="false" data-lens="INR" title="Indian rupee">INR</button>'
             '<button type="button" role="radio" aria-checked="false" data-lens="CAD" title="Canadian dollar">CAD</button>'
             '<button type="button" role="radio" aria-checked="false" data-lens="USD" title="US dollar">USD</button></div>'
             '<div class="lns" role="radiogroup" aria-label="Which lines to draw"><span class="lbl">Lines</span>'
             '<button type="button" role="radio" aria-checked="true" data-lines="all">All</button>'
             '<button type="button" role="radio" aria-checked="false" data-lines="sel">Only selected</button></div>'
             '<label class="ptog"><input type="checkbox" checked> Show predictions</label></div>')
    note_html = f'<p class="vnote">{_e(note)}</p>' if note else ""
    return (f'<h4>How the news moves markets, step by step</h4><p class="vnote">Each arrow is one cause-and-effect step. Green raises, red lowers.</p>{note_html}<div class="cmapwrap">'
            f'<div class="cmap" id="cmap{uid}" data-view="chains"><div class="ctop">{seg}{search}</div>{filters}{nos}<div class="top5" hidden></div><div class="cbody">'
            f'<nav class="crail" aria-label="Chooser"></nav>'
            f'<div class="cmain"><div class="mhead"><div class="mt"></div><div class="mh"></div>{tools}</div>'
            f'<div class="cview" tabindex="0" aria-label="Map area. Plus and minus zoom, zero fits, arrow keys pan, Tab moves between nodes.">{svg}{zoom}'
            f'<div class="ctip" aria-hidden="true"></div></div><div class="chint">Drag to pan, scroll or pinch to zoom</div></div></div>'
            f'<section class="fview" aria-label="Focus view" hidden></section><div class="pop" role="dialog" aria-label="Details" hidden></div><div class="sronly" aria-live="polite"></div>{_legend(types_used, {e["kind"]: True for e in edges})}'
            f'<script type="application/json" class="cdata">{_json_embed(payload)}</script></div></div>')

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
