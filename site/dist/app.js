"use strict";

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let catalog = [];
let findings = [];

function fmtBytes(n) {
  if (n == null || n <= 0) return "—";
  const u = ["B", "KiB", "MiB", "GiB", "TiB"];
  let v = n, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return v.toFixed(v >= 100 ? 0 : 1) + " " + u[i];
}
function fmtDate(s) {
  if (!s) return "—";
  return String(s).replace("+", " ").replace("T", " ").slice(0, 16);
}
function fmtYear(s) {
  if (!s) return "—";
  return String(s).slice(0, 4);
}

const FLAG_LABELS = {
  unlabeled: "unlabeled",
  valve_test_app: "test app",
  pre_release: "pre-release",
  cut_content: "cut content",
  content_mismatch: "mismatch",
};

// ---- views ----
function show(id) {
  ["browse", "games", "game", "uncharted", "discoveries", "bundles", "tf2", "community", "depot", "about"].forEach((v) =>
    $("#view-" + v).classList.toggle("hidden", v !== id));
  ["nav-browse", "nav-games", "nav-uncharted", "nav-discoveries", "nav-bundles", "nav-tf2", "nav-community", "nav-about"].forEach((n) =>
    $("#" + n).classList.toggle("active", n === "nav-" + id));
  $("#searchbar").style.display = (["browse", "uncharted", "discoveries", "games"].includes(id)) ? "flex" : "none";
}

$("#nav-browse").onclick = () => { show("browse"); renderCatalog(); };
$("#nav-games").onclick = () => { show("games"); renderGames(); };
$("#nav-uncharted").onclick = () => { show("uncharted"); renderUncharted(); };
$("#nav-discoveries").onclick = () => { show("discoveries"); renderDiscoveries(); };
$("#nav-bundles").onclick = () => { show("bundles"); renderBundles(); };
$("#nav-tf2").onclick = () => { show("tf2"); renderTF2(); };
$("#nav-community").onclick = () => show("community");
$("#nav-about").onclick = () => show("about");

// ---- games ----
let gamesData = [];

// known icon slugs (Steam library art fetched at build time into img/games/)
const GAME_ICONS = new Set(["tf","css","cs16","cz","czds","hl2","p2","p1","l4d2","l4d1","hl1","dods","hl2dm","hl2lc","as","csgo","dota","p3","dnf","dod","opfor","bshift","tfc","dmc","ricochet","p2at","sfm"]);

function gameTile(g) {
  const el = document.createElement("div");
  el.className = "tile";
  const art = GAME_ICONS.has(g.icon)
    ? `<img class="art" src="img/games/${g.icon}.jpg" alt="" loading="lazy">`
    : `<div class="art placeholder" style="background:linear-gradient(135deg,#2c3644,#1a1f27)"><span>${esc(g.game.slice(0,2).toUpperCase())}</span></div>`;
  el.innerHTML =
    art +
    `<div class="tname">${esc(g.game)}</div>` +
    `<div class="tmeta">${g.map_count || 0} maps · ${g.depots.length} depots</div>` +
    `<div class="tmeta2">${g.versions.toLocaleString()} versions · ${fmtBytes(g.dat_bytes)}</div>`;
  el.onclick = () => openGame(g.slug);
  return el;
}

async function renderGames() {
  if (!gamesData.length) {
    try { gamesData = await (await fetch("data/games.json")).json(); }
    catch (e) { $("#result-count-games").textContent = "games data not built yet"; return; }
  }
  const q = ($("#search").value || "").trim().toLowerCase();
  const grid = $("#games-grid");
  grid.innerHTML = "";
  let shown = 0;
  for (const g of gamesData) {
    if (q && !g.game.toLowerCase().includes(q)) continue;
    if (shown >= 300) break;
    shown++;
    grid.appendChild(gameTile(g));
  }
  $("#result-count-games").textContent =
    `${shown.toLocaleString()} shown / ${gamesData.length.toLocaleString()} games`;
}

async function openGame(slug) {
  show("game");
  $("#game-detail").innerHTML = '<div class="dim">Loading…</div>';
  const g = gamesData.find((x) => x.slug === slug);
  let maps = [];
  try {
    const md = await (await fetch(`data/maps/${slug}.json`)).json();
    maps = md.maps || [];
  } catch (e) { /* no maps */ }

  const bsp = maps.filter((m) => m.type === "bsp");
  const side = maps.filter((m) => m.type !== "bsp");

  // timeline: build the union of version dates across the game's depots
  const events = [];
  for (const [depot, vd] of Object.entries(g.vdates || {})) {
    for (const [v, date] of vd) events.push({ depot: Number(depot), v, date });
  }
  events.sort((a, b) => a.date < b.date ? -1 : 1);
  const hasTimeline = events.length > 1;

  const depotRows = (g.depots || []).map((d) => {
    const cd = catalog.find((x) => x.depot === d);
    return `<tr data-depot="${d}">
      <td class="num depot-id">${d}</td>
      <td class="name">${esc(cd ? (cd.label || cd.manifest_roots.join(", ") || "(unnamed)") : "")}</td>
      <td class="num">${cd ? cd.distinct_versions : "—"}</td>
      <td class="num">${cd ? fmtBytes(cd.dat_bytes) : "—"}</td>
    </tr>`;
  }).join("");

  const mapRows = bsp.map((m) =>
    `<div class="row maprow" data-f="${m.first_ver}" data-l="${m.last_ver}" data-depots="${m.depots.join(",")}">
      <span>${esc(m.path)}</span><span class="sz">${fmtBytes(m.size)}</span></div>`).join("");

  $("#game-detail").innerHTML = `
    <h2>${esc(g.game)}</h2>
    <div class="sub">${g.depots.length} depot(s) · ${g.versions.toLocaleString()} versions ·
      ${bsp.length} maps · ${fmtBytes(g.dat_bytes)} payload ·
      ${fmtDate(g.first_date)} → ${fmtDate(g.last_date)}</div>
    ${hasTimeline ? `
    <div class="panel" style="margin-bottom:14px">
      <h3>Time travel <span class="dim small">— view the game's files as they existed on a date</span></h3>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <input id="gdate" type="date" min="${fmtDate(events[0].date)}" max="${fmtDate(events[events.length-1].date)}" value="${fmtDate(events[events.length-1].date)}" style="background:var(--panel2);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:6px">
        <button id="gsnap" class="btn">Snapshot</button>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px">
          <input type="checkbox" id="gcut" checked> show only files missing from the final version (cut)
        </label>
        <span id="gsnap-info" class="dim small"></span>
      </div>
      <div id="gsnap-result" class="pathlist" style="max-height:340px;margin-top:8px"></div>
    </div>` : ""}
    <div class="cols">
      <div class="panel"><h3>Maps (${bsp.length})</h3>
        <div class="pathlist" id="gamemaps">${mapRows || '<div class="dim small">no map files indexed</div>'}</div></div>
      <div>
        <div class="panel"><h3>Depots</h3><table><tbody>${depotRows}</tbody></table>
          <div class="dim small">click a depot row for its full manifest + per-version browser</div>
          ${side.length ? `<h3 style="margin-top:12px">Sidecar files (nav/res/lst)</h3><div class="pathlist">${side.slice(0, 200).map((m) =>
            `<div class="row"><span>${esc(m.path)}</span><span class="sz">${fmtBytes(m.size)}</span></div>`).join("")}</div>` : ""}
        </div>
      </div>
    </div>`;

  $("#game-detail").querySelectorAll("tr[data-depot]").forEach((tr) =>
    tr.addEventListener("click", () => openDepot(Number(tr.dataset.depot))));

  // time-travel: given a date, per depot find the version current at that date,
  // then check which maps existed (map first_ver/last_ver vs depot version at date)
  const snapBtn = $("#gsnap");
  if (snapBtn) {
    snapBtn.onclick = async () => {
      const date = $("#gdate").value;
      const onlyCut = $("#gcut").checked;
      const info = $("#gsnap-info");
      const result = $("#gsnap-result");
      info.textContent = "loading version manifests…";
      result.innerHTML = "";

      // per depot: version current at date (last version whose date <= chosen)
      const depotAt = {};
      for (const [depot, vd] of Object.entries(g.vdates || {})) {
        let best = null;
        for (const [v, d] of vd) if (d <= date) best = Number(v);
        depotAt[Number(depot)] = best;
      }
      const active = Object.entries(depotAt).filter(([, v]) => v !== null);
      info.textContent = `${active.length} depot(s) active on ${date}: ` +
        active.map(([d, v]) => `${d}@v${v}`).join(", ");

      // fetch vfiles for the active depots (the main ones) and intersect with maps
      const rows = [];
      for (const m of bsp) {
        const existedOnDate = m.depots.some((dep) => {
          const v = depotAt[dep];
          return v !== null && v !== undefined && m.first_ver <= v;
        });
        const cutLater = m.last_ver < (function(){
          // final version of the last depot that had this map
          let mx = 0;
          for (const dep of m.depots) {
            const vd = (g.vdates || {})[String(dep)];
            if (vd && vd.length) mx = Math.max(mx, vd[vd.length - 1][0]);
          }
          return mx;
        })();
        if (onlyCut && !existedOnDate) continue;
        if (onlyCut && existedOnDate && !cutLater) continue;
        rows.push(`<div class="row"><span>${esc(m.path)}</span><span class="sz">${fmtBytes(m.size)} · depots ${m.depots.join(",")} · v${m.first_ver}–v${m.last_ver}</span></div>`);
      }
      result.innerHTML = rows.slice(0, 500).join("") ||
        '<div class="dim small">nothing matched — try unchecking "cut only"</div>';
      if (rows.length > 500) result.innerHTML += `<div class="dim small">+ ${rows.length - 500} more</div>`;
    };
  }
}
$("#back-games").onclick = () => { show("games"); renderGames(); };

// ---- tf2 hub ----
async function renderTF2() {
  const box = $("#tf2-content");
  let data;
  try { data = await (await fetch("data/tf2.json")).json(); }
  catch (e) { box.innerHTML = '<div class="dim">TF2 data not built yet.</div>'; return; }

  const fmtD = (s) => String(s || "").slice(0, 10);
  const depotRows = data.depots.map((d) =>
    `<tr data-depot="${d.depot}">
      <td class="num depot-id">${d.depot}</td>
      <td class="name">${esc(d.role)}</td>
      <td class="num">${d.versions}</td>
      <td class="num">${d.path_count.toLocaleString()}</td>
      <td class="num">${d.map_count ? d.map_count + " maps" : "—"}</td>
      <td class="num">${fmtBytes(d.dat_bytes)}</td>
      <td class="num">${fmtD(d.first_date)} → ${fmtD(d.last_date)}</td>
    </tr>`).join("");

  const cutRows = (data.cut || []).slice(0, 100).map((c) =>
    `<div class="row"><span>${esc(c.path)}</span><span class="sz">depot ${c.depot} · v${c.f}–v${c.l}</span></div>`).join("");

  const tl = data.timeline || [];
  const maxFiles = Math.max(1, ...tl.map((t) => t.files || 0));
  const spark = tl.map((t) =>
    `<div class="sparkbar" style="height:${Math.max(2, Math.round(60 * (t.files || 0) / maxFiles))}px" title="v${t.v}: ${t.files ?? "?"} files"></div>`).join("");

  box.innerHTML = `
    <div class="panel"><h3>Depot family</h3>
      <table id="tf2-depots"><thead><tr>
        <th class="num">Depot</th><th>Role</th><th class="num">Versions</th>
        <th class="num">Paths</th><th class="num">Maps</th><th class="num">Payload</th><th class="num">Span</th>
      </tr></thead><tbody>${depotRows}</tbody></table>
    </div>
    <div class="panel" style="margin-top:14px"><h3>Content version timeline (depot 441, ${tl.length} versions)</h3>
      <div class="spark">${spark}</div>
      <div class="dim small">Bar height = files in that version. First: v0 (${fmtD(tl[0] ? data.depots.find(d=>d.depot===441).first_date : "")}), last: v${tl.length ? tl[tl.length-1].v : "?"}</div>
    </div>
    <div class="panel cut-panel" style="margin-top:14px"><h3>Cut / changed content (${(data.cut || []).length}+ files removed by the final version)</h3>
      <div class="pathlist">${cutRows || '<div class="dim small">none found yet</div>'}</div>
    </div>`;

  box.querySelectorAll("#tf2-depots tr[data-depot]").forEach((tr) =>
    tr.addEventListener("click", () => openDepot(Number(tr.dataset.depot))));
}

// ---- uncharted ----
function renderUncharted() {
  const box = $("#uncharted");
  const rows = catalog.filter((d) =>
    (d.flags || []).includes("unlabeled") ||
    (!d.label && (d.manifest_roots || []).length));
  if (!rows.length) {
    box.innerHTML = '<div class="dim">Nothing uncharted right now — every depot is labeled.</div>';
    return;
  }
  box.innerHTML = rows.slice(0, 300).map((d) => {
    const roots = (d.manifest_roots || []).filter((r) => r && !r.includes(".")).slice(0, 6);
    return `<div class="card" data-depot="${d.depot}">
      <div class="top"><span class="depot-id">${d.depot}</span>
        <span class="label dim">unnamed</span>
        <span class="badge">v${d.max_version}</span>
        <span class="badge">${fmtYear(d.first_date)}</span></div>
      <div class="meta mono">${roots.map(esc).join(" · ") || "(no manifest folders)"}</div>
      <div class="meta">${(d.flag_keywords || []).length ? "markers: " + d.flag_keywords.map(esc).join(" · ") : ""}</div>
    </div>`;
  }).join("") + (rows.length > 300
    ? `<div class="dim small">+ ${rows.length - 300} more — search them in Browse with an empty name filter</div>` : "");
  box.querySelectorAll(".card").forEach((c) =>
    c.addEventListener("click", () => openDepot(Number(c.dataset.depot))));
}

// ---- browse ----
let sortKey = "depot";
let sortDir = 1;

function badges(d) {
  return (d.flags || []).map((f) =>
    `<span class="badge ${f === "cut_content" || f === "valve_test_app" ? "hot" : ""}">${FLAG_LABELS[f] || esc(f)}</span>`
  ).join("");
}

function renderCatalog() {
  const q = $("#search").value.trim().toLowerCase();
  const tbody = $("#catalog tbody");
  tbody.innerHTML = "";
  let rows = catalog;

  if (q) {
    rows = catalog.filter((d) =>
      String(d.depot).includes(q) ||
      (d.label || "").toLowerCase().includes(q) ||
      (d.manifest_roots || []).some((r) => r.toLowerCase().includes(q)));
  }

  rows = rows.slice().sort((a, b) => {
    let va = a[sortKey], vb = b[sortKey];
    if (sortKey === "label") { va = (va || "").toLowerCase(); vb = (vb || "").toLowerCase(); }
    if (va < vb) return -sortDir;
    if (va > vb) return sortDir;
    return 0;
  });

  let shown = 0;
  for (const d of rows) {
    if (shown >= 500) break;
    shown++;
    const label = d.label || d.manifest_roots.join(", ") || "(unnamed)";
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="num depot-id">${d.depot}</td>` +
      `<td class="name">${esc(label)}${badges(d)}</td>` +
      `<td class="num">${d.distinct_versions}</td>` +
      `<td class="num">${d.path_count.toLocaleString()}</td>` +
      `<td class="num">${fmtBytes(d.dat_bytes)}</td>` +
      `<td class="num">${fmtYear(d.first_date)}–${fmtYear(d.last_date)}</td>`;
    tr.onclick = () => openDepot(d.depot);
    tbody.appendChild(tr);
  }
  $("#result-count").textContent =
    `${rows.length.toLocaleString()} matches · showing ${shown.toLocaleString()}`;
}

document.querySelectorAll("#catalog th[data-key]").forEach((th) => {
  th.onclick = () => {
    const k = th.dataset.key;
    if (sortKey === k) sortDir = -sortDir;
    else { sortKey = k; sortDir = 1; }
    document.querySelectorAll("#catalog th").forEach((t) => t.classList.remove("sorted"));
    th.classList.add("sorted");
    renderCatalog();
  };
});

let searchTimer;
$("#search").addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(renderCatalog, 80);
});

document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== $("#search")) {
    e.preventDefault();
    show("browse");
    $("#search").focus();
  }
});

// ---- depot detail ----
const DL_NOTE = "You need the complete delta chain (all versions up to the one you want) to extract. Easiest: use <a href=\"https://github.com/extremebleem/steam2_downloader\" target=\"_blank\" rel=\"noopener\">steam2_downloader</a>, which resolves chains automatically.";

async function openDepot(id) {
  show("depot");
  $("#depot").innerHTML = '<div class="dim">Loading…</div>';
  history.replaceState(null, "", "#depot-" + id);

  let detail;
  try {
    detail = await (await fetch(`data/depots/${id}.json`)).json();
  } catch (e) {
    $("#depot").innerHTML = `<div class="dim">Depot ${id} has no indexed manifest yet.</div>`;
    return;
  }
  const d = catalog.find((x) => x.depot === id) || {};
  const label = d.label || (d.manifest_roots || []).join(", ") || "(unnamed)";
  const paths = detail.paths || [];
  const versions = detail.versions || [];
  const files = detail.files || [];
  const cut = detail.cut || [];

  const datFiles = files.filter((f) => f.kind === "dat");
  const dlHtml = datFiles.length ? `
    <div class="panel dl"><h3>Payload downloads <span class="dim small">(external mirrors)</span></h3>
      <p class="dim small">${DL_NOTE}</p>
      ${datFiles.slice(0, 12).map((f) =>
        `<div><a href="https://de.steam2.download/dats/${f.name}" target="_blank" rel="noopener">v${f.v} · ${fmtBytes(f.size)}</a></div>`).join("")}
      ${datFiles.length > 12 ? `<div class="dim small">+ ${datFiles.length - 12} more versions</div>` : ""}
    </div>` : "";

  const cutHtml = cut.length ? `
    <div class="panel cut-panel"><h3>Cut content — files removed before the final version (${cut.length.toLocaleString()})</h3>
      <input id="cf" type="text" placeholder="filter cut files...">
      <div class="pathlist" id="cl">` +
      cut.slice(0, 400).map((p) =>
        `<div class="row"><span>${esc(p.p)}</span><span class="sz">v${p.f}–v${p.l}</span></div>`).join("") +
      `</div></div>` : "";

  const REPO = "femtendo/steam2-catalog";
  const suggestUrl = `https://github.com/${REPO}/issues/new?template=id-suggestion.yml` +
    `&depot=${id}` +
    `&confidence=Guess` +
    `&evidence=` + encodeURIComponent(
      `Root folders: ${(d.manifest_roots || []).join(", ")}\n` +
      `Versions: ${versions.length} (latest v${d.max_version}), ${fmtDate(d.first_date)} → ${fmtDate(d.last_date)}\n` +
      `Sample paths:\n` + paths.slice(0, 15).map((p) => p.p).join("\n"));
  const findUrl = `https://github.com/${REPO}/issues/new?template=find-report.yml` +
    `&depot=${id} v${d.max_version || "?"}` +
    `&paths=` + encodeURIComponent(paths.slice(0, 10).map((p) => p.p).join("\n"));

  const cur = versions.length ? versions[versions.length - 1] : null;
  $("#depot").innerHTML = `
    <h2><span class="depot-id">${id}</span> ${esc(label)}</h2>
    <div class="sub">${versions.length} version(s) · ${paths.length.toLocaleString()} distinct file paths ·
      ${fmtBytes(d.dat_bytes)} payload · ${fmtBytes(d.blob_bytes)} metadata</div>
    ${(d.flags || []).length ? `<div class="flagrow">${badges(d)}
      ${d.flag_score ? `<span class="badge">interest score ${d.flag_score}</span>` : ""}</div>` : ""}
    ${(d.flag_keywords || []).length ? `<div class="sub">Markers found: ${d.flag_keywords.map(esc).join(" · ")}</div>` : ""}
    <div class="linkrow">
      <a class="btn accent" href="${suggestUrl}" target="_blank" rel="noopener">Suggest an ID</a>
      <a class="btn" href="${findUrl}" target="_blank" rel="noopener">Report a find</a>
      <a href="https://steamdb.info/depot/${id}/" target="_blank" rel="noopener">SteamDB ↗</a>
      <a href="https://steamdb.info/app/${versions[0] && versions[0].app ? versions[0].app : id}/depots/" target="_blank" rel="noopener">SteamDB app ↗</a>
    </div>
    <div class="panel version-browser" style="margin-bottom:14px" id="vb-panel">
      <h3>File browser by version</h3>
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <select id="vsel" style="flex:0 0 auto">
          ${versions.map((v) => `<option value="${v.v}">v${v.v} — ${v.files ?? "?"} files</option>`).join("")}
        </select>
        <input id="vf-filter" type="text" placeholder="filter paths in this version..." style="flex:1;min-width:200px">
        <span id="vstat" class="dim small"></span>
      </div>
      <div class="pathlist" id="vflist" style="max-height:360px;margin-top:8px">
        <div class="dim small">${cur ? "loading version manifest…" : "no manifest data"}</div>
      </div>
    </div>
    <div class="cols">
      <div class="panel"><h3>File manifest (${paths.length.toLocaleString()})</h3>
        <input id="pf" type="text" placeholder="filter ${paths.length.toLocaleString()} paths...">
        <div class="pathlist" id="pl">${renderPaths(paths)}</div>
      </div>
      <div>
        <div class="panel"><h3>Version history</h3><div class="pathlist">
          ${versions.map((v) =>
            `<div class="row"><span>v${v.v}</span><span class="sz">${v.files ?? "—"} files · ${(v.roots || []).slice(0, 2).join(", ")}</span></div>`).join("") ||
            '<div class="dim small">no manifest data</div>'}
        </div></div>
        ${dlHtml}
        ${cutHtml}
      </div>
    </div>`;

  $("#pf").addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase();
    $("#pl").innerHTML = renderPaths(paths.filter((p) => p.p.toLowerCase().includes(q)));
  });
  if (cut.length) {
    $("#cf").addEventListener("input", (e) => {
      const q = e.target.value.toLowerCase();
      $("#cl").innerHTML = cut.slice(0, 400).filter((p) => p.p.toLowerCase().includes(q))
        .map((p) => `<div class="row"><span>${esc(p.p)}</span><span class="sz">v${p.f}–v${p.l}</span></div>`).join("");
    });
  }
  loadViewer(id);
  wireVersionBrowser(id, versions);
}

// wire the per-version file browser on a depot page
async function wireVersionBrowser(depot, versions) {
  const sel = $("#vsel");
  if (!sel || !versions.length) return;
  const data = await loadVFiles(depot);
  const list = $("#vflist");
  const stat = $("#vstat");
  if (!data) {
    if (list) list.innerHTML = '<div class="dim small">version-level manifest not available for this depot — use the union manifest below</div>';
    return;
  }
  const apply = () => {
    const v = sel.value;
    const files = (data.files || {})[v] || [];
    const q = ($("#vf-filter")?.value || "").toLowerCase();
    const shown = q ? files.filter((f) => f[0].toLowerCase().includes(q)) : files;
    if (list) list.innerHTML = renderVersionFiles(v, { [v]: shown });
    if (stat) stat.textContent = `${shown.length.toLocaleString()} files in v${v}`;
  };
  sel.addEventListener("change", apply);
  $("#vf-filter").addEventListener("input", apply);
  apply();
}

// ---- per-version file browser ----
const vfileCache = {};  // depot -> parsed {versions, files}

async function fetchJSONgz(url) {
  // .json.gz served as application/octet-stream; decompress in the browser.
  const resp = await fetch(url);
  if (!resp.ok) throw new Error("not found");
  const buf = await resp.arrayBuffer();
  if ("DecompressionStream" in window) {
    const ds = new DecompressionStream("gzip");
    const stream = new Blob([buf]).stream().pipeThrough(ds);
    const text = await new Response(stream).text();
    return JSON.parse(text);
  }
  // some hosts serve pre-decompressed; try plain JSON
  return JSON.parse(new TextDecoder().decode(buf));
}

async function loadVFiles(depot) {
  if (vfileCache[depot]) return vfileCache[depot];
  let data;
  try { data = await fetchJSONgz(`data/vfiles/${depot}.json.gz`); }
  catch (e) { return null; }
  vfileCache[depot] = data;
  return data;
}

function renderVersionFiles(version, files) {
  const list = files[String(version)] || [];
  const max = 1500;
  return list.slice(0, max).map((f) =>
    `<div class="row"><span>${esc(f[0])}</span><span class="sz">${fmtBytes(f[1])}</span></div>`).join("") +
    (list.length > max ? `<div class="dim small">+ ${(list.length - max).toLocaleString()} more</div>` : "") ||
    '<div class="dim small">no manifest for this version</div>';
}


// Models are pre-extracted by build_bundles.py into data/models/<depot>/ as .glb,
// each with a .meta.json listing its source path. Nothing loads unless it exists.
async function loadViewer(depot) {
  const slot = $("#viewer-slot");
  if (!slot) return;
  let files = [];
  try {
    files = await (await fetch(`data/models/${depot}/index.json`)).json();
  } catch (e) { return; } // no models for this depot — slot stays empty
  if (!files.length) return;

  slot.innerHTML = `
    <div class="panel viewer-panel">
      <h3>Models from this depot <span class="dim small">(drag to rotate — rendered from the original files)</span></h3>
      <div class="viewer-row">
        <select id="model-pick">
          ${files.map((f) => `<option value="${esc(f.file)}">${esc(f.label)}</option>`).join("")}
        </select>
      </div>
      <model-viewer id="mv" camera-controls auto-rotate shadow-intensity="1"
        exposure="1" style="width:100%;height:420px;background:#0d1015;border-radius:6px;"></model-viewer>
      <div class="meta small dim" id="mv-src"></div>
    </div>`;

  const mv = $("#mv");
  const pick = $("#model-pick");
  const setModel = () => {
    const f = files.find((x) => x.file === pick.value) || files[0];
    mv.src = `data/models/${depot}/${f.file}`;
    $("#mv-src").textContent = "source: " + f.src;
  };
  pick.addEventListener("change", setModel);
  setModel();
}

function renderPaths(paths) {
  const max = 2000;
  const out = paths.slice(0, max).map((p) =>
    `<div class="row"><span>${esc(p.p)}</span><span class="sz">${fmtBytes(p.s)}</span></div>`).join("");
  return out || '<div class="dim small">no files</div>';
}

// ---- discoveries ----
async function renderDiscoveries() {
  const box = $("#discoveries");
  if (!findings.length) {
    box.innerHTML = '<div class="dim">No findings yet — run the discovery pass first.</div>';
    return;
  }
  box.innerHTML = findings.map((f) => {
    const kws = f.evidence && f.evidence.keywords ? Object.keys(f.evidence.keywords) : [];
    const evHtml = kws.length
      ? `<div class="ev"><span class="kw">${kws.map(esc).join(" · ")}</span><ul>` +
        kws.slice(0, 3).map((k) =>
          (f.evidence.keywords[k] || []).slice(0, 3).map((p) => `<li>${esc(p)}</li>`).join("")).join("") +
        `</ul></div>`
      : "";
    return `<div class="card" data-depot="${f.depot}">
      <div class="top"><span class="depot-id">${f.depot}</span>
        <span class="label">${esc(f.label || "(unnamed)")}</span>
        ${f.flags.map((x) => `<span class="badge ${["unlabeled","valve_test_app","cut_content"].includes(x) ? "hot" : ""}">${FLAG_LABELS[x] || esc(x)}</span>`).join("")}
        <span class="badge">score ${f.score}</span></div>
      <div class="meta">v${f.max_version} · ${fmtDate(f.first_date)} → ${fmtDate(f.last_date)} · ${fmtBytes(f.dat_bytes)} payload</div>
      ${evHtml}
    </div>`;
  }).join("");
  box.querySelectorAll(".card").forEach((c) =>
    c.addEventListener("click", () => openDepot(Number(c.dataset.depot))));
}

// ---- bundles ----
async function renderBundles() {
  const box = $("#bundles");
  let data = null;
  try { data = await (await fetch("data/bundles.json")).json(); }
  catch (e) {
    box.innerHTML = '<div class="dim">No bundles published yet — the first verified ' +
      'bundles are still in the pipeline.</div>';
    return;
  }
  if (!data.bundles || !data.bundles.length) {
    box.innerHTML = '<div class="dim">No verified bundles yet.</div>';
    return;
  }
  box.innerHTML = data.bundles.map((b) =>
    `<div class="card">
      <div class="top"><span class="label">${esc(b.game)}</span>
        <span class="badge">${b.map_count} maps</span>
        <span class="badge">${fmtBytes(b.bytes)}</span>
        <span class="badge">built ${esc((b.built || "").slice(0, 10))}</span></div>
      <div class="meta">depots: ${(b.depots || []).join(", ")}</div>
      <div class="meta"><a href="${esc(b.url)}">Download zip</a></div>
    </div>`).join("")
    + (data.queued || []).map((q) =>
    `<div class="card">
      <div class="top"><span class="label">${esc(q.game)}</span>
        <span class="badge">queued</span></div>
      <div class="meta">${q.map_count || "?"} maps identified — payload verification pending</div>
    </div>`).join("");
}

// ---- deep links ----
window.addEventListener("hashchange", () => {
  const m = location.hash.match(/^#depot-(\d+)$/);
  if (m) openDepot(Number(m[1]));
});

// ---- boot ----
(async () => {
  try {
    const v = await (await fetch("data/verify.json")).json();
    $("#verify-badge .pct").textContent = v.pct.toFixed(1) + "%";
    $("#verify-badge .lbl").textContent =
      `${v.indexed_versions.toLocaleString()}/${v.total_versions.toLocaleString()} versions verified`;
  } catch (e) { /* badge stays at —% */ }
  try {
    catalog = await (await fetch("data/catalog.json")).json();
    renderCatalog();
    $("#stat-depots").textContent = catalog.length.toLocaleString();
  } catch (e) { console.error(e); }
  try {
    findings = await (await fetch("data/findings.json")).json();
  } catch (e) { /* no findings yet */ }
  const m = location.hash.match(/^#depot-(\d+)$/);
  if (m) openDepot(Number(m[1]));
})();
