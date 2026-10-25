"""archive.html: browse every past day, search all stories, and follow story threads."""

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>News Archive</title>
<style>
  :root { --bg:#f5f5f7; --card:#fff; --text:#1a1a1a; --muted:#666; --border:#e0e0e0; --accent:#2563eb; --chip:#eef2ff; --heat:37,99,235; --heat-ink:#fff; --lo:217,119,6; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f172a; --card:#1e293b; --text:#f1f5f9; --muted:#94a3b8; --border:#334155; --accent:#60a5fa; --chip:#27354f; --heat:96,165,250; --heat-ink:#0f172a; --lo:251,191,36; }
  }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; background:var(--bg); color:var(--text); line-height:1.5; padding:20px; }
  .wrap { max-width:760px; margin:0 auto; }
  header { padding:24px 0 16px; border-bottom:2px solid var(--border); margin-bottom:20px; }
  header a.back { color:var(--accent); text-decoration:none; font-size:14px; }
  header h1 { font-size:26px; margin:6px 0 2px; }
  header p { color:var(--muted); font-size:14px; }
  .tabs { display:flex; gap:8px; margin-bottom:16px; }
  .tabs button { flex:1; padding:10px; border:1px solid var(--border); background:var(--card); color:var(--text); border-radius:8px; font-size:15px; cursor:pointer; }
  .tabs button[aria-selected="true"] { background:var(--accent); color:#fff; border-color:var(--accent); }
  input[type=search], select { width:100%; padding:10px 12px; border:1px solid var(--border); border-radius:8px; background:var(--card); color:var(--text); font-size:15px; }
  .row { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }
  .row > * { flex:1 1 160px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:12px 14px; margin-bottom:10px; }
  .card a.t { color:var(--text); text-decoration:none; font-weight:600; }
  .card a.t:hover { color:var(--accent); }
  .meta { color:var(--muted); font-size:13px; margin-top:2px; }
  .chip { display:inline-block; background:var(--chip); border-radius:999px; padding:1px 9px; font-size:12px; margin:4px 4px 0 0; }
  .brk { color:#dc2626; font-weight:700; font-size:12px; }
  details.thread > summary { cursor:pointer; }
  .tl { border-left:2px solid var(--border); margin:10px 0 2px 6px; padding-left:14px; }
  .tl div { margin-bottom:8px; font-size:14px; }
  .tl .d { color:var(--muted); font-size:12px; }
  .empty { color:var(--muted); text-align:center; padding:30px 0; }
  .tabs button:focus-visible, .hm button:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  .hmwrap { overflow-x:auto; border:1px solid var(--border); border-radius:10px; background:var(--card); }
  table.hm { border-collapse:collapse; width:100%; font-size:13px; min-width:340px; }
  table.hm th { font-weight:600; color:var(--muted); padding:6px 4px; text-align:center; font-size:12px; }
  table.hm th.rh { text-align:left; padding-left:10px; color:var(--text); min-width:96px; max-width:150px; position:sticky; left:0; background:var(--card); }
  table.hm td { padding:3px; text-align:center; }
  table.hm td button { width:100%; min-width:44px; height:40px; border:1px solid var(--border); border-radius:6px; font:inherit; font-variant-numeric:tabular-nums; cursor:pointer; background:transparent; color:var(--text); }
  table.hm td button[aria-pressed="true"] { outline:2px solid var(--text); }
  table.hm td button.zero { color:var(--muted); }
  .hmkey { display:flex; align-items:center; gap:8px; font-size:12px; color:var(--muted); margin:8px 0; flex-wrap:wrap; }
  .hmkey .bar { width:90px; height:10px; border-radius:5px; border:1px solid var(--border); }
  .olderbtn { padding:10px; border:1px solid var(--border); background:var(--card); color:var(--text); border-radius:8px; font-size:15px; cursor:pointer; }
  .olderbtn:disabled { opacity:.6; cursor:default; }
  h2.day { font-size:15px; margin:18px 0 8px; color:var(--muted); }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <a class="back" href="./">&larr; Latest digests</a>
    <h1>News Archive</h1>
    <p id="stats">Loading&hellip;</p>
  </header>
  <div class="tabs" role="tablist">
    <button role="tab" id="tab-browse" aria-selected="true">Browse &amp; search</button>
    <button role="tab" id="tab-threads" aria-selected="false">Story threads</button>
    <button role="tab" id="tab-heat" aria-selected="false">Heat map</button>
  </div>

  <section id="browse">
    <div class="row">
      <input type="search" id="q" placeholder="Search every story ever shown&hellip;" aria-label="Search stories">
      <select id="domain" aria-label="Topic"><option value="">All topics</option></select>
      <select id="day" aria-label="Day"><option value="">All days</option></select>
    </div>
    <div id="results"></div>
    <div class="row" id="olderrow" hidden><button type="button" id="older" class="olderbtn">Load older days</button></div>
  </section>

  <section id="heat" hidden>
    <p class="meta" style="margin-bottom:12px" id="hmnote"></p>
    <div class="row">
      <select id="hmmode" aria-label="Heat map view">
        <option value="cur">Last 7 days</option><option value="prev">Previous 7 days</option><option value="delta">Change vs previous</option>
      </select>
    </div>
    <div class="hmwrap"><table class="hm" id="hmtable"></table></div>
    <div class="hmkey" id="hmkey"></div>
    <div id="hmlist"></div>
  </section>

  <section id="threads" hidden>
    <p class="meta" style="margin-bottom:12px">Related stories followed across days and topics. Threads that touch several topics are marked.</p>
    <div class="row">
      <input type="search" id="tq" placeholder="Filter threads&hellip;" aria-label="Filter threads">
      <select id="tmode" aria-label="Thread filter">
        <option value="2">2+ stories</option><option value="cross">Cross-topic only</option><option value="1">All threads</option>
      </select>
    </div>
    <div id="threadlist"></div>
  </section>
</div>
<script>
const $ = id => document.getElementById(id);
const safe = u => /^https?:\/\//i.test(String(u || "")) ? u : "#";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const INITIAL_DAYS = 30;
let days = [], stories = [], threads = [];
const loadedDays = new Set();

async function get(url) { try { const r = await fetch(url); return r.ok ? await r.json() : null; } catch { return null; } }

/* fetch day files, ignoring any that are missing or malformed */
async function loadDays(list) {
  const todo = list.filter(d => d && typeof d.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(d.date) && !loadedDays.has(d.date));
  const got = await Promise.all(todo.map(d => get(`_data/archive/${d.date}.json`)));
  got.forEach((d, i) => {
    loadedDays.add(todo[i].date);
    if (!d || !Array.isArray(d.stories)) return;
    d.stories.forEach(s => { if (s && typeof s === "object" && s.title) stories.push({ ...s, day: todo[i].date }); });
  });
  stories.sort((a, b) => b.day.localeCompare(a.day));
}

function refreshFilters() {
  const keep = $("domain").value;
  while ($("domain").options.length > 1) $("domain").remove(1);
  [...new Set(stories.map(s => s.domain).filter(Boolean))].sort().forEach(d => $("domain").add(new Option(d, d)));
  $("domain").value = keep;
}

function updateStats() {
  const nth = threads.filter(t => t && Array.isArray(t.stories) && t.stories.length > 1).length;
  const more = days.length - loadedDays.size;
  $("stats").textContent = `${stories.length} stories over ${loadedDays.size} day${loadedDays.size === 1 ? "" : "s"}${more > 0 ? ` (of ${days.length})` : ""} · ${nth} story threads`;
  $("olderrow").hidden = more <= 0;
}

async function init() {
  const idx = await get("_data/archive/index.json");
  days = idx && Array.isArray(idx.days) ? idx.days.filter(d => d && typeof d.date === "string") : [];
  days.sort((a, b) => b.date.localeCompare(a.date));
  await loadDays(days.slice(0, INITIAL_DAYS));
  const th = await get("_data/threads.json");
  threads = th && Array.isArray(th.threads) ? th.threads.filter(t => t && Array.isArray(t.stories) && Array.isArray(t.domains) && Array.isArray(t.keywords) && typeof t.first_seen === "string" && typeof t.last_seen === "string") : [];
  days.forEach(d => $("day").add(new Option(`${d.date} (${d.count})`, d.date)));
  refreshFilters(); updateStats();
  renderResults(); renderThreads();
}

async function loadOlder() {
  const b = $("older"); b.disabled = true; b.textContent = "Loading…";
  await loadDays(days);
  b.disabled = false; b.textContent = "Load older days";
  refreshFilters(); updateStats(); renderResults();
  if (!$("heat").hidden) renderHeat();
}

function storyHtml(s) {
  const src = (s.sources || [s.source]).slice(0, 3).join(", ");
  return `<div class="card"><a class="t" href="${esc(safe(s.link))}" target="_blank" rel="noopener">${esc(s.title)}</a>
    <div class="meta">${s.breaking ? '<span class="brk">BREAKING</span> · ' : ""}${esc(src)} · ${esc(s.region)}</div>
    <span class="chip">${esc(s.domain)}</span>${s.why ? `<div class="meta">${esc(s.why)}</div>` : ""}</div>`;
}

function renderResults() {
  const q = $("q").value.trim().toLowerCase(), dom = $("domain").value, day = $("day").value;
  const words = q.split(/\s+/).filter(Boolean);
  let list = stories.filter(s => (!dom || s.domain === dom) && (!day || s.day === day) &&
    words.every(w => (s.title + " " + (s.summary || "") + " " + (s.sources || []).join(" ")).toLowerCase().includes(w)));
  const total = list.length;
  list = list.slice(0, 200);
  if (!list.length) { $("results").innerHTML = '<p class="empty">No stories match.</p>'; return; }
  let out = "", cur = "";
  list.forEach(s => { if (s.day !== cur) { cur = s.day; out += `<h2 class="day">${esc(cur)}</h2>`; } out += storyHtml(s); });
  if (total > list.length) out += `<p class="empty">Showing ${list.length} of ${total} — narrow the search to see more.</p>`;
  $("results").innerHTML = out;
}

function renderThreads() {
  const q = $("tq").value.trim().toLowerCase(), mode = $("tmode").value;
  let list = threads.filter(t => (mode === "1" || t.stories.length >= 2) && (mode !== "cross" || t.domains.length > 1) &&
    (!q || (t.title + " " + t.keywords.join(" ")).toLowerCase().includes(q)));
  if (!list.length) { $("threadlist").innerHTML = '<p class="empty">No threads yet. They build up as digests accumulate.</p>'; return; }
  $("threadlist").innerHTML = list.slice(0, 150).map(t => `
    <div class="card"><details class="thread"><summary>
      <b>${esc(t.title)}</b>
      <div class="meta">${t.stories.length} stories · ${esc(t.first_seen.slice(0,10))} → ${esc(t.last_seen.slice(0,10))}${t.domains.length > 1 ? " · cross-topic" : ""}</div>
      ${t.domains.map(d => `<span class="chip">${esc(d)}</span>`).join("")}
    </summary><div class="tl">${t.stories.slice().sort((a,b) => a.date.localeCompare(b.date)).map(s =>
      `<div><span class="d">${esc(s.date)} · ${esc(s.source)}</span><br><a class="t" href="${esc(safe(s.link))}" target="_blank" rel="noopener">${esc(s.title)}</a></div>`).join("")}
    </div></details></div>`).join("");
}

/* ---- heat map: domain x region, last 7 days vs the 7 before (relative to newest day loaded) ---- */
let hmSel = null;
function dayNum(s) { return Math.floor(Date.parse(s + "T00:00:00Z") / 864e5); }
function renderHeat() {
  if (!stories.length) { $("hmtable").innerHTML = ""; $("hmnote").textContent = "No stories loaded yet."; return; }
  const maxD = stories.reduce((m, s) => Math.max(m, dayNum(s.day)), 0);
  const per = s => { const a = maxD - dayNum(s.day); return a < 7 ? "cur" : a < 14 ? "prev" : null; };
  const doms = [...new Set(stories.map(s => s.domain || "Other"))].sort();
  const regs = [...new Set(stories.map(s => s.region || "Other"))].sort();
  const cnt = {}; // key -> {cur, prev, list}
  stories.forEach(s => {
    const p = per(s); if (!p) return;
    const k = (s.domain || "Other") + "|" + (s.region || "Other");
    const c = cnt[k] || (cnt[k] = { cur: 0, prev: 0, cl: [] });
    c[p]++; if (p === "cur") c.cl.push(s);
  });
  const mode = $("hmmode").value;
  const val = c => !c ? 0 : mode === "cur" ? c.cur : mode === "prev" ? c.prev : c.cur - c.prev;
  let mx = 1;
  doms.forEach(d => regs.forEach(r => { mx = Math.max(mx, Math.abs(val(cnt[d + "|" + r]))); }));
  const fmt = v => mode === "delta" ? (v > 0 ? "+" + v : v < 0 ? "−" + Math.abs(v) : "0") : String(v);
  let h = '<thead><tr><th class="rh" scope="col">Topic</th>' + regs.map(r => `<th scope="col">${esc(r)}</th>`).join("") + "</tr></thead><tbody>";
  doms.forEach(d => {
    h += `<tr><th class="rh" scope="row">${esc(d)}</th>`;
    regs.forEach(r => {
      const c = cnt[d + "|" + r], v = val(c), a = Math.abs(v) / mx;
      const rgb = mode === "delta" && v < 0 ? "var(--lo)" : "var(--heat)";
      const bg = v === 0 ? "transparent" : `rgba(${rgb}, ${(0.12 + 0.88 * a).toFixed(2)})`;
      const ink = v !== 0 && a > 0.5 ? "var(--heat-ink)" : "var(--text)";
      const label = `${d}, ${r}: ${mode === "delta" ? `${c ? c.cur : 0} now vs ${c ? c.prev : 0} before` : fmt(v) + " stories"}`;
      h += `<td><button type="button" class="${v === 0 ? "zero" : ""}" style="background:${bg};${v ? "color:" + ink : ""}" data-k="${esc(d + "|" + r)}" aria-pressed="${hmSel === d + "|" + r}" aria-label="${esc(label)}" title="${esc(label)}">${fmt(v)}</button></td>`;
    });
    h += "</tr>";
  });
  $("hmtable").innerHTML = h + "</tbody>";
  const lo = mode === "delta" ? `<span class="bar" style="background:linear-gradient(90deg, rgba(var(--lo),.9), transparent 50%, rgba(var(--heat),.9))"></span> fewer ← same → more` :
    `<span class="bar" style="background:linear-gradient(90deg, rgba(var(--heat),.12), rgba(var(--heat),1))"></span> 0 → ${mx} stories`;
  $("hmkey").innerHTML = lo;
  const cur = stories.filter(s => per(s) === "cur").length, prev = stories.filter(s => per(s) === "prev").length;
  $("hmnote").textContent = `Stories per topic and region. Last 7 days (to ${new Date(maxD * 864e5).toISOString().slice(0, 10)}): ${cur}; the 7 days before: ${prev}. Tap a cell to list its stories.`;
  const sel = hmSel && cnt[hmSel];
  $("hmlist").innerHTML = sel ? `<h2 class="day">${esc(hmSel.replace("|", " · "))} — last 7 days</h2>` + (sel.cl.map(storyHtml).join("") || '<p class="empty">None in the last 7 days.</p>') : "";
}
$("hmtable").addEventListener("click", ev => {
  const b = ev.target.closest("button[data-k]"); if (!b) return;
  hmSel = hmSel === b.dataset.k ? null : b.dataset.k; renderHeat();
  const again = document.querySelector(`button[data-k="${CSS.escape(b.dataset.k)}"]`); if (again) again.focus();
});
$("hmmode").addEventListener("input", renderHeat);

function tab(which) {
  ["browse", "threads", "heat"].forEach(n => { $(n).hidden = which !== n; $("tab-" + n).setAttribute("aria-selected", which === n); });
  if (which === "heat") renderHeat();
}
$("tab-browse").onclick = () => tab("browse"); $("tab-threads").onclick = () => tab("threads"); $("tab-heat").onclick = () => tab("heat");
["q", "domain"].forEach(i => $(i).addEventListener("input", renderResults));
$("day").addEventListener("input", async () => {
  const d = days.find(x => x.date === $("day").value);
  if (d && !loadedDays.has(d.date)) { await loadDays([d]); refreshFilters(); updateStats(); }
  renderResults();
});
$("older").onclick = loadOlder;
["tq", "tmode"].forEach(i => $(i).addEventListener("input", renderThreads));
init();
</script>
</body>
</html>
"""
