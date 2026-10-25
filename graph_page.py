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
    .viz { --d0:#6250d6; --d1:#0b8ea6; --d2:#1baf7a; --d3:#e87ba4; --d4:#2a78d6; --d5:#eb6834; --d6:#eda100; --dx:#64748b;
           --ln:#64748b; --halo:var(--card); --soft:rgba(100,116,139,.12); --soft2:rgba(100,116,139,.2);
           --up:#1a7f37; --down:#cf222e; --mix:#9a6700; --con:#b4236f; --ink2:#475569; --focus:#2563eb; --lane:.075; }
    @media (prefers-color-scheme: dark) {
      .viz { --d0:#9085e9; --d1:#2bb3cf; --d2:#199e70; --d3:#d55181; --d4:#3987e5; --d5:#d95926; --d6:#c98500; --dx:#94a3b8; --ln:#94a3b8;
             --up:#3fb950; --down:#f85149; --mix:#d29922; --con:#f778ba; --ink2:#cbd5e1; --focus:#79c0ff; --lane:.12;
             --soft:rgba(148,163,184,.14); --soft2:rgba(148,163,184,.24); }
    }
    .viz h4 { font-size: 14px; margin: 18px 0 6px; }
    .viz .vnote { font-size: 12px; color: var(--muted); margin: 0 0 8px; }

    .cmapwrap { margin: 0; }
    @media (min-width: 900px) {
      .cmapwrap { width: min(1320px, calc(100vw - 40px)); position: relative; left: 50%; transform: translateX(-50%); }
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

    /* body: chooser rail | map | panel */
    .cbody { display: grid; grid-template-columns: minmax(0, 1fr); }
    .crail { min-width: 0; border-bottom: 1px solid var(--border); }
    .cpanel { padding: 14px 16px 16px; font-size: 14px; line-height: 1.45; overflow: auto; border-top: 1px solid var(--border); min-height: 120px; }
    .cmain { min-width: 0; }
    @media (min-width: 900px) {
      .cbody { grid-template-columns: minmax(0, 1fr) 300px; grid-template-areas: "rail rail" "main panel"; }
      .crail { grid-area: rail; } .cmain { grid-area: main; }
      .cpanel { grid-area: panel; border-top: 0; border-left: 1px solid var(--border); max-height: 700px; }
    }
    @media (min-width: 1280px) {
      .cbody { grid-template-columns: 276px minmax(0, 1fr) 316px; grid-template-areas: "rail main panel"; }
      .crail { border-bottom: 0; border-right: 1px solid var(--border); overflow-y: auto; max-height: 700px; }
      .cmap[data-view="all"] .cbody { grid-template-columns: minmax(0, 1fr) 316px; grid-template-areas: "main panel"; }
    }
    .cmap[data-view="all"] .crail { display: none; }

    /* chooser: key chains */
    .rail-in { display: flex; gap: 8px; overflow-x: auto; padding: 10px 12px; scroll-snap-type: x proximity; }
    .rail-in .rh { display: none; }
    .rail-in ul.cl { display: contents; list-style: none; margin: 0; padding: 0; }
    .rail-in ul.cl li { display: flex; flex: none; margin: 0; }
    @media (min-width: 1280px) { .rail-in ul.cl { display: flex; flex-direction: column; gap: 8px; } .rail-in ul.cl li { display: block; } .rail-in ul.cl .ccard { width: 100%; } }
    @media (min-width: 1280px) { .rail-in { flex-direction: column; overflow: visible; padding: 12px; } .rail-in .rh { display: block; } }
    .rh { font-size: 13px; font-weight: 600; color: var(--ink2); margin: 0 2px 2px; }
    .ccard { flex: 0 0 min(260px, 78vw); scroll-snap-align: start; display: flex; flex-direction: column; gap: 5px; text-align: left; padding: 10px 12px; border: 1px solid var(--border);
             border-radius: 10px; background: transparent; cursor: pointer; line-height: 1.3; }
    .ccard:hover { background: var(--soft); }
    .ccard[aria-pressed="true"] { border-color: var(--text); background: var(--soft); box-shadow: inset 4px 0 0 var(--focus); padding-left: 14px; }
    @media (min-width: 1280px) { .ccard { flex: none; } }
    .ccard .ct { font-size: 14px; font-weight: 600; color: var(--text); }
    .ccard .cs { font-size: 12.5px; color: var(--muted); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
    .ccard .cm { display: flex; flex-wrap: wrap; gap: 3px 10px; align-items: center; }
    .bdg { font-size: 12px; font-weight: 600; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--ink2); color: var(--text); white-space: nowrap; }
    .bdg.predicted { border-style: dashed; }
    .bdg.mixed { border-style: dashed; color: var(--ink2); }
    @media (max-width: 899px) {
      .rail-in { -webkit-mask-image: linear-gradient(to right, #000 calc(100% - 32px), transparent); mask-image: linear-gradient(to right, #000 calc(100% - 32px), transparent); padding-right: 32px; }
      .seg button { min-height: 44px; padding: 8px 14px; }
      .cmap .csearch { font-size: 16px; min-height: 44px; }
      .czoom button { width: 44px; height: 44px; }
      .cpanel .step, .cpanel .pitem { min-height: 44px; display: flex; flex-direction: column; justify-content: center; }
      .cmap[data-view="chains"] .mkey { display: inline; }
      .cmap { display: flex; flex-direction: column; }
      .cbody { display: contents; }
      .ctop { order: 0; } .cflt, .nos { order: 1; } .crail { order: 2; } .cmain { order: 3; } .legend { order: 4; } .cpanel { order: 5; }
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
    @media (min-width: 1280px) { .tbtn { border-radius: 8px; width: 100%; } .tgrp { display: flex; flex-direction: column; gap: 4px; } }

    /* map header + view */
    .mhead { display: flex; flex-wrap: wrap; gap: 2px 14px; align-items: baseline; padding: 9px 14px 8px; border-bottom: 1px solid var(--border); min-height: 42px; }
    .mhead .mt { font-size: 15px; font-weight: 600; }
    .mhead .mh { font-size: 13px; color: var(--muted); }
    .cview { position: relative; height: min(66vh, 480px); background: var(--card); outline-offset: -2px; overflow: hidden; }
    @media (max-width: 899px) { .cmap[data-view="all"] .cview, .cmap[data-view="topic"] .cview { height: min(88vh, 720px); } }
    @media (min-width: 900px) { .cview { height: 600px; } .cmap[data-view="all"] .cview { height: 860px; } }
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
    .cmap svg.focus .ed:not(.hl):not(.perm) { opacity: .04; }
    .cmap svg.focus .nd:not(.hl):not(.ghost) { opacity: .22; }
    .cmap svg.scn-chain.focus .nd:not(.hl):not(.ghost) { opacity: .5; }
    .cmap svg.scn-chain.focus .nd.ghost { opacity: 1; }
    .cmap svg.scn-chain.focus .ed:not(.hl):not(.perm) { opacity: .1; }
    .cmap svg.scn-chain .ed.ghosted { opacity: .7; }
    .cmap svg.scn-chain .ed.ghosted.hl { opacity: 1; }
    .cmap svg.scn-chain .elabel { font-size: 12px; font-weight: 600; fill: var(--text); }
    .cmap svg.scn-chain.focus .ed.perm { opacity: 1; }
    .cmap svg.hov .ed:not(.hh):not(.perm):not(.hv) { opacity: .04; }
    .cmap svg.hov .nd:not(.hh):not(.hov1) { opacity: .28; }
    .cmap svg.scn-chain.hov .nd { opacity: 1; }
    .cmap svg.scn-chain.hov .nd.ghost { opacity: 1; }
    .cmap svg.searching .nd:not(.m) { opacity: .25; }
    .cmap .hid { display: none; }

    .czoom { position: absolute; bottom: 10px; right: 10px; display: flex; flex-direction: column; gap: 4px; }
    .czoom button { width: 34px; height: 34px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); cursor: pointer; font-size: 18px; line-height: 1; padding: 0; opacity: .95; }
    .czoom button.fit { font-size: 12px; }
    .czoom button:hover { background: var(--soft2); }
    .cbar { position: absolute; left: 8px; right: 8px; top: 8px; padding: 8px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); text-align: left; font-size: 14px; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,.18); }
    .cbar[hidden] { display: none; }
    @media (min-width: 900px) { .cbar { display: none !important; } }
    .chint { position: absolute; left: 10px; bottom: 6px; font-size: 12px; color: var(--muted); pointer-events: none; background: var(--card); padding: 1px 6px; border-radius: 6px; opacity: .92; }
    .ctip { position: absolute; z-index: 5; pointer-events: none; background: var(--text); color: var(--card); font-size: 13px;
            padding: 7px 10px; border-radius: 6px; max-width: 270px; line-height: 1.35; display: none; }
    .ctip b { font-weight: 600; } .ctip .tm { opacity: .85; font-size: 12px; display: block; margin-top: 2px; }

    /* panel */
    .cpanel h5 { font-size: 16px; line-height: 1.25; margin: 0 0 3px; }
    .cpanel .pmeta { font-size: 13px; color: var(--muted); margin-bottom: 8px; display: flex; flex-wrap: wrap; gap: 2px 10px; align-items: center; }
    .cpanel p { margin: 0 0 8px; }
    .cpanel .pl { font-weight: 600; color: var(--text); }
    .cpanel h6 { font-size: 13px; font-weight: 600; margin: 14px 0 4px; color: var(--ink2); }
    .cpanel ul { list-style: none; margin: 0; padding: 0; }
    .cpanel li { margin: 0 0 2px; }
    .cpanel .pitem { display: block; width: 100%; text-align: left; background: none; border: 0; border-radius: 6px; padding: 5px 6px; cursor: pointer; line-height: 1.35; font-size: 14px; }
    .cpanel .pitem:hover { background: var(--soft); }
    .cpanel .pitem .mech { color: var(--muted); display: block; font-size: 12.5px; }
    .cpanel .pitem .fx { display: inline-block; width: 1.1em; text-align: center; font-weight: 700; }
    .cpanel .fx.up { color: var(--up); } .cpanel .fx.down { color: var(--down); } .cpanel .fx.mixed { color: var(--mix); } .cpanel .fx.con { color: var(--con); }
    .cpanel .mkt { display: inline-block; background: var(--soft); border-radius: 6px; padding: 2px 8px; font-variant-numeric: tabular-nums; }
    .cpanel .mkt.up { color: var(--up); } .cpanel .mkt.down { color: var(--down); }
    .cpanel .thr { color: var(--ink2); }
    .cpanel .src a { color: var(--accent); word-break: break-word; }
    .cpanel .empty { color: var(--muted); }
    .cpanel .clear { float: right; font-size: 13px; background: none; border: 0; color: var(--accent); cursor: pointer; text-decoration: underline; padding: 0; }
    .cpanel .step { display: block; width: 100%; text-align: left; border: 0; background: none; padding: 7px 6px; border-radius: 6px; cursor: pointer; font-size: 14px; line-height: 1.35; }
    .cpanel .step:hover { background: var(--soft); }
    .cpanel .step .sn { font-weight: 600; display: block; }
    .cpanel .step .how { display: block; color: var(--ink2); font-size: 13px; }
    .cpanel .step .sm { display: block; color: var(--muted); font-size: 12.5px; }
    .cpanel .gobtn { margin: 6px 0 2px; font-size: 13px; padding: 5px 10px; border: 1px solid var(--border); border-radius: 8px; background: var(--card); cursor: pointer; }
    .cpanel .gobtn:hover { background: var(--soft2); }

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
    var panel = box.querySelector(".cpanel"), rail = box.querySelector(".crail"), mt = box.querySelector(".mt"), mh = box.querySelector(".mh");
    var N = data.nodes || {}, E = data.edges || [], CH = data.chains || [], TOP = data.topics || [], LAY = data.lay || {}, SH = data.shapes, EXT = data.ext, UID = data.uid;
    var ids = Object.keys(N), outE = {}, inE = {}, HUB = {};
    ids.slice().sort(function (a, b) { return (N[b].k || 0) - (N[a].k || 0) || (a < b ? -1 : 1); }).forEach(function (id, i) { HUB[id] = i; });
    ids.forEach(function (k) { outE[k] = []; inE[k] = []; });
    E.forEach(function (e, i) { if (outE[e.f]) outE[e.f].push(i); if (inE[e.t]) inE[e.t].push(i); });

    function defaultTopic() {
      if (CH.length) { var d = N[CH[0].n[0]].d; var k = d >= 0 ? "d" + d : "dx"; if (LAY["t:" + k]) return k; }
      return TOP.length ? TOP[0].k : "";
    }
    var S = { view: CH.length ? "chains" : "all", chain: 0, topic: defaultTopic(), sel: null };
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
      var sc = { kind: kind, W: L.W, H: L.H, nodes: {}, edges: [], lanes: L.lanes || null, narrow: narrow, gut: L.gut || 120 }, gh = {};
      /* label sizing at the "fit" scale: font in map units, characters per label line from the column pitch */
      var r0 = box2(), s0 = Math.min(r0.w / (L.W + 24), r0.h / (L.H + 24)), fsU = Math.min(28, Math.max(13, (narrow ? 10.5 : 12.5) / s0));
      var nc = Math.max(7, Math.floor(((L.cw || 130) - 10) / (fsU * 0.6))), nHub = narrow ? 8 : 12;
      sc.fsU = fsU; sc.nc = nc;
      (L.g || []).forEach(function (g) { gh[g] = true; });
      Object.keys(L.p).forEach(function (id) {
        if (!N[id]) return;
        var q = L.p[id], hub = kind === "all" ? HUB[id] < nHub : (kind === "topic" && !gh[id] && topRank(L, gh, id) < (narrow ? 6 : 8));
        sc.nodes[id] = { x: q[0], y: q[1], k: gh[id] ? 0.8 : 1, ghost: !!gh[id], lab: "below", w: kind === "all" ? (hub ? Math.min(nc + (narrow ? 2 : 5), 18) : 13) : (narrow ? nc + 1 : Math.min(17, nc + 2)), ln: hub && narrow ? 3 : 2, fs: 0, lo: false, hub: hub, sn: Math.min(13, nc + 2) };
      });
      Object.keys(L.e || {}).forEach(function (k) {
        var e = E[+k]; if (!e || !sc.nodes[e.f] || !sc.nodes[e.t]) return;
        var gs = sc.nodes[e.f].ghost || sc.nodes[e.t].ghost;
        sc.edges.push({ i: +k, pts: L.e[k], ghosted: gs, sw: kind === "all" ? 0.9 + e.s * 0.45 : 1.3 + e.s * 0.6, size: e.s, lab: { x: 0, y: 0, w: 24, ln: 3, anchor: "middle", mid: true } });
      });
      return sc;
    }
    var NARROW_LANE = ["AI", "Tech", "Finance", "Markets", "Politics", "Conflict", "Prices"];
    function buildScene() {
      if (S.view === "chains" && CH.length) return buildChain();
      if (S.view === "topic" && LAY["t:" + S.topic]) return buildLayout("t:" + S.topic, "topic");
      S.view = "all"; return buildLayout("all", "all");
    }

    /* ---------- rendering ---------- */
    function mkNode(id, o) {
      var n = N[id], ext = EXT[n.t], k = o.k, hh = ext[1] * k, hw = ext[0] * k, dc = dcls(n.d);
      var g = sv("g", { "class": "nd" + (o.ghost ? " ghost" : "") + (o.lo ? " lo" : ""), tabindex: o.ghost && SC.kind === "chain" ? "-1" : "0", role: "button", "data-id": id, transform: "translate(" + r1(o.x) + " " + r1(o.y) + ")" });
      var meta = (data.typeLabel[n.t] || n.t) + (n.dn ? ", " + n.dn : "") + (n.r ? ", " + n.r : "");
      g.setAttribute("aria-label", n.l + ", " + meta + (o.ghost ? ". Linked node outside this view." : "") + ". Show connections.");
      var lines = wrap(n.l, o.w, o.ln), fsz = o.fs ? o.fs : 13;
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
      if (SC.kind === "chain") eo = { 3: 1, 2: 0.85, 1: 0.7 }[conf(e)]; else if (SC.kind !== "all") eo = { 3: 0.9, 2: 0.7, 1: 0.5 }[conf(e)];
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
          var name = d >= 0 ? data.doms[d] : "Other", t = sv("text", null, "lanelabel"), tc = sv("text", { "class": "lanecount" }), lf = Math.round(SC.fsU * 1.02);
          if (SC.narrow) name = d >= 0 ? NARROW_LANE[d] : "Other";   /* phone: short topic names */
          g.appendChild(t); g.appendChild(tc);
          LR[d] = { t: t, tc: tc, name: name, top: top, h: h, x: SC.narrow ? 16 : 22, base: SC.narrow ? lf : 16, cbase: SC.narrow ? Math.round(lf * 0.85) : 14, ctxt: SC.narrow ? String(cnt[d]) : cnt[d] + (cnt[d] === 1 ? " node" : " nodes") };
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
    var vb = { x: 0, y: 0, w: 800, h: 400 }, fitS = 1, LR = {}, AXR = null, lastLK = "";
    /* Label floor: SVG text scales with the zoom, so every map label is sized in screen pixels. Hubs, lane and axis
       labels are counter-scaled to stay >= MIN px on screen; other node labels keep their natural size and fade out
       while that would be < 11px. Overlaps between visible node labels drop the lower-priority one. */
    function layoutLabels(s, force) {
      if (!SC || SC.kind === "chain") return;
      var key = s.toFixed(3) + "|" + SC.kind + "|" + (S.sel || ""); if (!force && key === lastLK) return; lastLK = key;
      var MIN = 11.5, FLOOR = 11;
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
      });
      if (AXR) {
        var fa = Math.max(AXR.base, MIN / s), ok = fa + 4 <= AXR.top, yb = r1(fa * 0.95 + 1), w1 = 6 * fa * 0.58, w2 = 8 * fa * 0.58, xa = AXR.x1 + w1 + 10, xb = AXR.x2 - w2 - 10, h = fa * 0.3;
        [AXR.t1, AXR.t2].forEach(function (t) { t.setAttribute("y", yb); t.setAttribute("style", "font-size:" + r1(fa) + "px"); t.classList.toggle("lh", !ok); });
        AXR.ln.setAttribute("d", xb - xa > 30 ? "M" + r1(xa) + " " + r1(yb - h) + "H" + r1(xb) + "M" + r1(xb - h * 1.4) + " " + r1(yb - h * 2) + "L" + r1(xb) + " " + r1(yb - h) + "L" + r1(xb - h * 1.4) + " " + r1(yb) : "");
        AXR.ln.classList.toggle("lh", !ok);
      }
      var items = [];
      Object.keys(SC.nodes).forEach(function (id) {
        var g = nodeEl[id], o = SC.nodes[id], t = g && g.querySelector("text.nlabel"); if (!t || g.classList.contains("hid")) return;
        var m = g.classList.contains("m"), pin = o.hub || id === S.sel || m, fs = pin ? Math.max(13, MIN / s) : 13;
        t.style.fontSize = r1(fs) + "px";
        var pr = id === S.sel ? -2 : m ? -1 : o.hub ? (HUB[id] || 0) / 100 : 1 + (HUB[id] || 0) / 1000;
        items.push({ t: t, o: o, pin: pin, fs: fs, pr: pr, ok: pin || fs * s >= FLOOR });
      });
      items.sort(function (a, b) { return a.pr - b.pr; });
      var taken = [], pad = 3 / s;
      items.forEach(function (it) {
        var vis = it.ok;
        if (vis) {
          var b = it.t.getBBox(), r = { x0: it.o.x + b.x - pad, x1: it.o.x + b.x + b.width + pad, y0: it.o.y + b.y - pad, y1: it.o.y + b.y + b.height + pad };
          for (var i = 0; i < taken.length; i++) { var c = taken[i]; if (r.x0 < c.x1 && r.x1 > c.x0 && r.y0 < c.y1 && r.y1 > c.y0) { vis = false; break; } }
          if (vis) taken.push(r);
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
        var pt = 14, pb = SC.narrow ? 62 : 54, ps = SC.narrow ? 34 : 48;
        s = Math.min((r.w - 2 * ps) / SC.W, (r.h - pt - pb) / SC.H);
        vb.w = r.w / s; vb.h = r.h / s; vb.x = SC.W / 2 - vb.w / 2;
        var extra = Math.max(0, r.h - pt - pb - SC.H * s);
        vb.y = -(pt + (SC.narrow ? 0 : extra / 2)) / s;   /* phone: map hugs the top, free space below keeps the zoom buttons clear */
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
        if (SC && SC.kind !== "chain" && (w < 600) !== !!SC.narrow && LAY[(SC.kind === "all" ? "all" : "t:" + S.topic) + ":m"]) { rebuild(); return; }
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
      hit.addEventListener("click", function (ev) { ev.stopPropagation(); g.classList.add("hv"); showTip(edgeTip(i), ev); clearTimeout(tipTimer); tipTimer = setTimeout(function () { hideTip(); g.classList.remove("hv"); }, 3500); });
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
    function filtersOn() { return SC && SC.kind === "all"; }
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
    function applyAll() {
      var nv = 0, ev2 = 0, total = ids.length;
      Object.keys(nodeEl).forEach(function (id) { var ok = nodeOk(id); nodeEl[id].classList.toggle("hid", !ok); if (ok) nv++; });
      Object.keys(edgeEl).forEach(function (i) { var ok = edgeOk(+i); edgeEl[i].classList.toggle("hid", !ok); if (ok) ev2++; });
      Object.keys(laneEl).forEach(function (d) { laneEl[d].classList.toggle("hid", !!F.hideDom[d]); });
      if (S.sel && !nodeOk(S.sel)) S.sel = null;
      chain = S.sel ? chainsOf(S.sel) : null;
      var multi = SC.kind === "chain" ? (S.sel ? (function () { var h = { nodes: {}, edges: {} }; h.nodes[S.sel] = 0; outE[S.sel].concat(inE[S.sel]).forEach(function (i) { h.edges[i] = true; var o = E[i].f === S.sel ? E[i].t : E[i].f; h.nodes[o] = 1; }); return h; })() : null) : chain;
      svg.classList.toggle("focus", !!multi);
      var nHl = 0;
      Object.keys(nodeEl).forEach(function (id) { nodeEl[id].classList.toggle("hl", !!(multi && multi.nodes[id] != null)); nodeEl[id].classList.toggle("sel", id === S.sel); });
      Object.keys(edgeEl).forEach(function (i) { var on = !!(multi && multi.edges[i]); edgeEl[i].classList.toggle("hl", on); if (on) nHl++; });
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
      renderPanel(); layoutLabels(box2().w / vb.w, true);
      var bar = box.querySelector(".cbar");
      if (bar) { bar.hidden = !S.sel; if (S.sel) bar.textContent = N[S.sel].l + " — details below ▾"; }
    }
    function matches(id, q) { var n = N[id]; return (n.l + " " + n.dn + " " + n.r + " " + (data.typeLabel[n.t] || "")).toLowerCase().indexOf(q) >= 0; }

    /* ---------- panel ---------- */
    function fxGlyph(e) { if (e.kind === "contradicts") return ["≠", "con"]; var f = FX[e.effect] || FX.none; return [f[0], f[1]]; }
    function badge(st) { var b = el("span", "bdg " + st, st === "mixed" ? "Reported + predicted" : st === "predicted" ? "Predicted" : "Reported"); if (st !== "reported") b.title = "Dashed = forecast, not yet reported"; return b; }
    function item(otherId, i, arrow) {
      var e = E[i], o = N[otherId], b = el("button", "pitem"); b.type = "button";
      var g = fxGlyph(e), fx = el("span", "fx " + g[1], g[0]); fx.setAttribute("aria-hidden", "true");
      b.appendChild(fx); b.appendChild(document.createTextNode(" " + (o ? o.l : "?")));
      var meta = [e.kind !== "causal" ? e.kind : "", (e.conf || "?") + " conf.", e.status || "reported", e.lag || ""].filter(Boolean).join(" · ");
      b.appendChild(el("span", "mech", (e.label || "no mechanism given") + "  (" + meta + ")"));
      b.setAttribute("aria-label", (arrow || "") + " " + (o ? o.l : "") + ": " + (e.label || "") + ", " + (FX[e.effect] ? FX[e.effect][2] : "") + ", " + meta);
      b.addEventListener("click", function () { goTo(otherId); });
      var li = el("li"); li.appendChild(b); return li;
    }
    function section(title, arr) {
      if (!arr.length) return null;
      var d = document.createDocumentFragment(); d.appendChild(el("h6", "", title));
      var ul = el("ul"); arr.forEach(function (x) { ul.appendChild(x); }); d.appendChild(ul); return d;
    }
    function domChips(list) {
      var w = el("div", "pmeta");
      list.forEach(function (d) { var s = el("span", "dchip " + dcls(d), d >= 0 ? data.short[d] : "Other"); w.appendChild(s); });
      return w;
    }
    function chainPanel() {
      var c = CH[S.chain];
      panel.appendChild(el("h5", "", c.title));
      var m = el("div", "pmeta"); m.appendChild(badge(c.st)); c.d.forEach(function (d) { m.appendChild(el("span", "dchip " + dcls(d), d >= 0 ? data.short[d] : "Other")); });
      panel.appendChild(m);
      panel.appendChild(el("p", "empty", "Read it step by step. Click a step for the full story, or a faint node on the map to see what else it touches."));
      var ol = el("ul");
      c.n.forEach(function (id, i) {
        var li = el("li"), b = el("button", "step"); b.type = "button";
        if (i) {
          var e = E[c.e[i - 1]], g = fxGlyph(e);
          b.appendChild(el("span", "how", g[0] + " " + (e.label || "leads to") + "  (" + [e.conf ? e.conf + " conf." : "", e.status, e.lag].filter(Boolean).join(" · ") + ")"));
        }
        b.appendChild(el("span", "sn", N[id].l));
        b.addEventListener("click", function () { choose(id, true); });
        li.appendChild(b); ol.appendChild(li);
      });
      panel.appendChild(ol);
      if (SC && SC.gp && SC.gp.total) {
        var shown = Object.keys(SC.nodes).filter(function (id) { return SC.nodes[id].ghost; });
        var rest = SC.vertical ? SC.gp.up.concat(SC.gp.dn) : [];
        if (SC.gp.more || rest.length) {
          var lis = rest.map(function (id) { var li = el("li"), b = el("button", "pitem", N[id].l); b.type = "button"; b.addEventListener("click", function () { goTo(id); }); li.appendChild(b); return li; });
          var s = section("Also linked to this chain", lis); if (s) panel.appendChild(s);
        }
      }
    }
    function renderPanel() {
      panel.textContent = "";
      if (!S.sel) {
        if (S.view === "chains" && CH.length) { chainPanel(); return; }
        panel.appendChild(el("p", "empty", "Select a node to see what drives it, what it drives, and its multi-step chains. Drag to pan; wheel, pinch or +/− to zoom; arrow keys move between nodes."));
        var vis = Object.keys(SC.nodes).filter(function (id) { return nodeOk(id) && !SC.nodes[id].ghost; }).sort(function (a, b) { return N[b].k - N[a].k || (a < b ? -1 : 1); }).slice(0, 6);
        if (vis.length) {
          panel.appendChild(el("h6", "", "Most connected"));
          var ul = el("ul"); vis.forEach(function (id) {
            var b = el("button", "pitem", N[id].l); b.type = "button"; b.appendChild(el("span", "mech", N[id].dn + (N[id].r ? " · " + N[id].r : "")));
            b.addEventListener("click", function () { choose(id, true); }); var li = el("li"); li.appendChild(b); ul.appendChild(li);
          }); panel.appendChild(ul);
        }
        return;
      }
      var sel = S.sel, n = N[sel], c = el("button", "clear", "Clear"); c.type = "button"; c.addEventListener("click", function () { choose(null); }); panel.appendChild(c);
      panel.appendChild(el("h5", "", n.l));
      var meta = el("div", "pmeta"); meta.appendChild(el("span", "", data.typeLabel[n.t] || n.t));
      if (n.dn) meta.appendChild(el("span", "dchip " + dcls(n.d), n.dn)); if (n.r) meta.appendChild(el("span", "", n.r));
      if (n.dir && n.dir !== "none") meta.appendChild(el("span", "", { up: "▲ rising", down: "▼ falling", flat: "▬ steady" }[n.dir] || ""));
      panel.appendChild(meta);
      if (SC.nodes[sel] && SC.nodes[sel].ghost && S.view === "topic") {
        var tk = n.d >= 0 ? "d" + n.d : "dx";
        if (LAY["t:" + tk]) { var gb = el("button", "gobtn", "Open topic: " + (n.d >= 0 ? data.doms[n.d] : "Other")); gb.type = "button"; gb.addEventListener("click", function () { setView("topic", { topic: tk, sel: sel }); }); panel.appendChild(gb); }
      }
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
      var ch = chain || chainsOf(sel);
      [["Further downstream (to 3 steps)", ch.down], ["Further upstream (to 3 steps)", ch.up]].forEach(function (g) {
        var far = g[1].filter(function (x) { return x.d > 1; }).slice(0, 8);
        var items = far.map(function (x) {
          var path = g[0].indexOf("down") > 0 ? x.path.slice(1) : x.path.slice(1).reverse();
          var b = el("button", "pitem", path.map(function (id) { return N[id].l; }).join(" → ")); b.type = "button";
          b.addEventListener("click", function () { goTo(x.id); }); var li = el("li"); li.appendChild(b); return li;
        });
        var s = section(g[0], items); if (s) panel.appendChild(s);
      });
      var good = (n.src || []).map(function (s) { return [s[0], safeUrl(s[1])]; }).filter(function (s) { return s[1]; });
      if (good.length) {
        panel.appendChild(el("h6", "", "Sources")); var ul2 = el("ul", "src");
        good.slice(0, 6).forEach(function (s) { var li = el("li"), a = el("a", "", s[0] || s[1]); a.href = s[1]; a.target = "_blank"; a.rel = "noopener noreferrer"; li.appendChild(a); ul2.appendChild(li); });
        panel.appendChild(ul2);
      }
    }

    /* ---------- selection ---------- */
    function choose(id, pan) {
      S.sel = id && (id !== S.sel || pan) ? id : null;
      if (id == null) S.sel = null;
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
        return;
      }
      choose(id, true);
    }
    function wireNode(n, id) {
      n.addEventListener("click", function (ev) { ev.stopPropagation(); choose(S.sel === id ? null : id, false); });
      n.addEventListener("keydown", function (ev) {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); ev.stopPropagation(); choose(S.sel === id ? null : id, false); }
        else if (ev.key.indexOf("Arrow") === 0) { ev.preventDefault(); ev.stopPropagation(); spatial(id, ev.key); }
      });
      n.addEventListener("focus", function () { ensureVisible(id); hover(id); });
      n.addEventListener("blur", function () { if (hoverId === id) clearHover(); });
      n.addEventListener("mouseenter", function () { if (!(drag && moved)) hover(id); });
      n.addEventListener("mousemove", function (ev) {
        if (drag && moved) return;
        showTip(function (t) { t.appendChild(el("b", "", N[id].l)); t.appendChild(el("span", "tm", (data.typeLabel[N[id].t] || "") + (N[id].dn ? " · " + N[id].dn : "") + (N[id].r ? " · " + N[id].r : ""))); }, ev);
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
    svg.addEventListener("click", function (ev) { if (!ev.target.closest || !ev.target.closest(".nd")) { if (S.sel) choose(null); } });
    view.addEventListener("keydown", function (ev) {
      if (ev.target.closest && ev.target.closest("input,select,textarea")) return;
      var k = ev.key, onNode = ev.target.closest && ev.target.closest(".nd");
      if (k === "+" || k === "=") { ev.preventDefault(); zoomCenter(1.4); }
      else if (k === "-" || k === "_") { ev.preventDefault(); zoomCenter(1 / 1.4); }
      else if (k === "0") { ev.preventDefault(); fit(true); }
      else if (k === "Escape") { choose(null); hideTip(); }
      else if (!onNode && k.indexOf("Arrow") === 0) {
        ev.preventDefault(); var f = 0.15;
        panBy(k === "ArrowLeft" ? -f : k === "ArrowRight" ? f : 0, k === "ArrowUp" ? -f : k === "ArrowDown" ? f : 0);
      }
    });

    /* ---------- chooser rail + headers + views ---------- */
    function buildRail() {
      rail.textContent = "";
      if (S.view === "all") return;
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
      } else {
        mt.textContent = "Everything · " + ids.length + " nodes, " + E.length + " links";
        mh.textContent = "Click any node to light up its links. Hover or zoom in to read every label.";
      }
    }
    function sizeView() {
      if (SC.kind === "chain" && SC.vertical) view.style.height = Math.min(SC.H + 16, 820) + "px";
      else if (SC.narrow && view.clientWidth > 0) view.style.height = Math.round(Math.min(720, Math.max(380, SC.H * view.clientWidth / (SC.W + 24) + 110))) + "px";
      else view.style.height = "";
    }
    function rebuild() {
      hideTip(); clearHover();
      SC = buildScene(); sizeView(); box.setAttribute("data-view", S.view);
      var fl = box.querySelector(".cflt"); if (fl) fl.hidden = S.view !== "all";
      [].forEach.call(box.querySelectorAll(".seg button"), function (b) { var on = b.getAttribute("data-view") === S.view; b.setAttribute("aria-checked", on ? "true" : "false"); b.tabIndex = on ? 0 : -1; });
      buildRail(); headers(); render();
      if (S.sel && !nodeEl[S.sel]) S.sel = null;
      lastW = view.clientWidth; fit(); applyAll();
    }
    function setView(v, o) {
      o = o || {}; S.view = v;
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
    var bar0 = box.querySelector(".cbar");
    if (bar0) { bar0.addEventListener("pointerdown", function (ev) { ev.stopPropagation(); }); bar0.addEventListener("click", function () { panel.scrollIntoView({ block: "start", behavior: "smooth" }); }); }
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


def _flow_layout(nodes, edges, lane_of, cap=3, layers=None, ghosts=(), ghost_cap=5, row_h=ROW_H, col_w=COL_W, gutter=GUTTER, tail=LANE_TAIL):
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
    heights = {l: SLOT_PAD + (R[l] - 1) * row_h + tail for l in best}
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
    res = _pick_layout(nodes, edges, lane_of, (2, 3, 4, 5), 1000, 860, row_h=96, col_w=132, gutter=140, tail=72)
    lay = {"all": _scene_json(res, _route_edges(range(len(edges)), edges, res["pos"], byid), (), True, cw=132, gut=140)}
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
    hub = max(nodes, key=lambda n: (deg[n["id"]], -order_ix[n["id"]]))["id"]
    je = [{"f": e["from"], "t": e["to"], "label": e["label"], "effect": e["effect"], "s": e["strength"], "conf": e["conf"],
           "status": e["status"], "order": e["order"], "kind": e["kind"], "why": e["why"], "lag": e["lag"]} for e in edges]
    payload = {"uid": uid, "nodes": jn, "edges": je, "chains": chains, "topics": topics, "lay": lay, "hub": hub,
               "typeLabel": TYPE_LABEL, "short": SHORT, "doms": DOMAINS,
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
           '<button type="button" role="radio" aria-checked="false" data-view="all">Everything</button></div>')
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
    note_html = f'<p class="vnote">{_e(note)}</p>' if note else ""
    return (f'<h4>How the news moves markets, step by step</h4><p class="vnote">Each arrow is one cause-and-effect step. Green raises, red lowers.</p>{note_html}<div class="cmapwrap">'
            f'<div class="cmap" id="cmap{uid}" data-view="chains"><div class="ctop">{seg}{search}</div>{filters}{nos}<div class="cbody">'
            f'<nav class="crail" aria-label="Chooser"></nav>'
            f'<div class="cmain"><div class="mhead"><div class="mt"></div><div class="mh"></div></div>'
            f'<div class="cview" tabindex="0" aria-label="Map area. Plus and minus zoom, zero fits, arrow keys pan, Tab moves between nodes.">{svg}{zoom}'
            f'<div class="chint">Drag to pan, scroll or pinch to zoom</div><button type="button" class="cbar" hidden></button><div class="ctip" aria-hidden="true"></div></div></div>'
            f'<div class="cpanel" aria-live="polite"></div></div>{_legend(types_used, {e["kind"]: True for e in edges})}'
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
