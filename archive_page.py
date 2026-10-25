"""archive.html: browse every past day, search all stories, and follow story threads."""

PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>News Archive</title>
<style>
  :root { --bg:#f5f5f7; --card:#fff; --text:#1a1a1a; --muted:#666; --border:#e0e0e0; --accent:#2563eb; --chip:#eef2ff; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0f172a; --card:#1e293b; --text:#f1f5f9; --muted:#94a3b8; --border:#334155; --accent:#60a5fa; --chip:#27354f; }
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
  </div>

  <section id="browse">
    <div class="row">
      <input type="search" id="q" placeholder="Search every story ever shown&hellip;" aria-label="Search stories">
      <select id="domain" aria-label="Topic"><option value="">All topics</option></select>
      <select id="day" aria-label="Day"><option value="">All days</option></select>
    </div>
    <div id="results"></div>
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
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let days = [], stories = [], threads = [];

async function get(url) { try { const r = await fetch(url); return r.ok ? r.json() : null; } catch { return null; } }

async function init() {
  const idx = await get("_data/archive/index.json");
  days = idx ? idx.days : [];
  const loaded = await Promise.all(days.map(d => get(`_data/archive/${d.date}.json`)));
  loaded.forEach(d => d && d.stories.forEach(s => stories.push({...s, day: d.date})));
  const th = await get("_data/threads.json");
  threads = th ? th.threads : [];
  $("stats").textContent = `${stories.length} stories over ${days.length} day${days.length === 1 ? "" : "s"} · ${threads.filter(t => t.stories.length > 1).length} story threads`;
  [...new Set(stories.map(s => s.domain))].sort().forEach(d => $("domain").add(new Option(d, d)));
  days.forEach(d => $("day").add(new Option(`${d.date} (${d.count})`, d.date)));
  renderResults(); renderThreads();
}

function storyHtml(s) {
  const src = (s.sources || [s.source]).slice(0, 3).join(", ");
  return `<div class="card"><a class="t" href="${esc(s.link)}" target="_blank" rel="noopener">${esc(s.title)}</a>
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
      `<div><span class="d">${esc(s.date)} · ${esc(s.source)}</span><br><a class="t" href="${esc(s.link)}" target="_blank" rel="noopener">${esc(s.title)}</a></div>`).join("")}
    </div></details></div>`).join("");
}

function tab(which) {
  $("browse").hidden = which !== "browse"; $("threads").hidden = which !== "threads";
  $("tab-browse").setAttribute("aria-selected", which === "browse"); $("tab-threads").setAttribute("aria-selected", which === "threads");
}
$("tab-browse").onclick = () => tab("browse"); $("tab-threads").onclick = () => tab("threads");
["q", "domain", "day"].forEach(i => $(i).addEventListener("input", renderResults));
["tq", "tmode"].forEach(i => $(i).addEventListener("input", renderThreads));
init();
</script>
</body>
</html>
"""
