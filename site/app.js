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
  ["browse", "discoveries", "bundles", "depot", "about"].forEach((v) =>
    $("#view-" + v).classList.toggle("hidden", v !== id));
  ["nav-browse", "nav-discoveries", "nav-bundles", "nav-about"].forEach((n) =>
    $("#" + n).classList.toggle("active", n === "nav-" + id));
  $("#searchbar").style.display = (id === "depot" || id === "about") ? "none" : "flex";
}

$("#nav-browse").onclick = () => show("browse");
$("#nav-discoveries").onclick = () => { show("discoveries"); renderDiscoveries(); };
$("#nav-bundles").onclick = () => { show("bundles"); renderBundles(); };
$("#nav-about").onclick = () => show("about");

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

  $("#depot").innerHTML = `
    <h2><span class="depot-id">${id}</span> ${esc(label)}</h2>
    <div class="sub">${versions.length} version(s) · ${paths.length.toLocaleString()} distinct file paths ·
      ${fmtBytes(d.dat_bytes)} payload · ${fmtBytes(d.blob_bytes)} metadata</div>
    ${(d.flags || []).length ? `<div class="flagrow">${badges(d)}
      ${d.flag_score ? `<span class="badge">interest score ${d.flag_score}</span>` : ""}</div>` : ""}
    ${(d.flag_keywords || []).length ? `<div class="sub">Markers found: ${d.flag_keywords.map(esc).join(" · ")}</div>` : ""}
    <div class="linkrow">
      <a href="https://steamdb.info/depot/${id}/" target="_blank" rel="noopener">SteamDB ↗</a>
      <a href="https://steamdb.info/app/${versions[0] && versions[0].app ? versions[0].app : id}/depots/" target="_blank" rel="noopener">SteamDB app ↗</a>
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
