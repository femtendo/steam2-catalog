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

// ---- views ----
function show(id) {
  ["browse", "discoveries", "depot", "about"].forEach((v) =>
    $("#view-" + v).classList.toggle("hidden", v !== id));
  ["nav-browse", "nav-discoveries", "nav-about"].forEach((n) =>
    $("#" + n).classList.toggle("active", n === "nav-" + id));
  $("#searchbar").style.display = (id === "depot" || id === "about") ? "none" : "flex";
}

$("#nav-browse").onclick = () => show("browse");
$("#nav-discoveries").onclick = () => { show("discoveries"); renderDiscoveries(); };
$("#nav-about").onclick = () => show("about");

// ---- browse ----
function renderCatalog(filter) {
  const q = (filter || "").trim().toLowerCase();
  const tbody = $("#catalog tbody");
  tbody.innerHTML = "";
  let shown = 0;
  for (const d of catalog) {
    const label = d.label || d.manifest_roots.join(", ") || "(unnamed)";
    if (q && !(String(d.depot).includes(q) || label.toLowerCase().includes(q))) continue;
    shown++;
    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td class="num depot-id">${d.depot}</td>` +
      `<td class="name">${esc(label)}</td>` +
      `<td class="num">${d.distinct_versions}</td>` +
      `<td class="num">${d.path_count.toLocaleString()}</td>` +
      `<td class="num">${fmtBytes(d.dat_bytes)}</td>` +
      `<td class="num">${fmtBytes(d.blob_bytes)}</td>` +
      `<td class="num">${String(d.first_date || "").slice(0, 4)}–${String(d.last_date || "").slice(0, 4)}</td>`;
    tr.onclick = () => openDepot(d.depot);
    tbody.appendChild(tr);
    if (shown >= 500) break;
  }
  $("#result-count").textContent =
    `${shown.toLocaleString()} shown / ${catalog.length.toLocaleString()} depots`;
}

let searchTimer;
$("#search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => renderCatalog(e.target.value), 80);
});

// ---- depot detail ----
async function openDepot(id) {
  show("depot");
  const d = catalog.find((x) => x.depot === id);
  const label = (d && (d.label || d.manifest_roots.join(", "))) || "";
  const res = await fetch(`data/depots/${id}.json`);
  const detail = await res.json();
  const paths = detail.paths || [];
  const versions = detail.versions || [];
  const files = detail.files || [];

  const datFiles = files.filter((f) => f.kind === "dat");
  const dlHtml = datFiles.length
    ? `<div class="panel dl"><h3>Download (.dat payloads — external mirrors)</h3>` +
      datFiles.slice(0, 12).map((f) =>
        `<div><a href="https://de.steam2.download/dats/${f.name}">v${f.v} ${fmtBytes(f.size)}</a></div>`).join("") +
      (datFiles.length > 12 ? `<div class="dim small">+ ${datFiles.length - 12} more versions</div>` : "") +
      `</div>`
    : "";

  $("#depot").innerHTML =
    `<h2><span class="depot-id">${id}</span> ${esc(label)}</h2>` +
    `<div class="sub">${versions.length} version(s) · ${paths.length.toLocaleString()} distinct file paths · ` +
    `${fmtBytes((d && d.dat_bytes) || 0)} payload · ${fmtBytes((d && d.blob_bytes) || 0)} metadata</div>` +
    `<div class="cols">` +
      `<div class="panel"><h3>File manifest (${paths.length.toLocaleString()})</h3>` +
        `<input id="pf" type="text" placeholder="filter paths...">` +
        `<div class="pathlist" id="pl">` + renderPaths(paths) + `</div>` +
      `</div>` +
      `<div>` +
        `<div class="panel"><h3>Version history</h3><div class="pathlist">` +
          versions.map((v) => `<div class="row"><span>v${v.v} <span class="sz">${v.files ?? "—"} files</span></span></div>`).join("") +
        `</div></div>` +
        dlHtml +
      `</div>` +
    `</div>`;

  $("#pf").addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase();
    $("#pl").innerHTML = renderPaths(paths.filter((p) => p.p.toLowerCase().includes(q)));
  });
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
        ${f.flags.map((x) => `<span class="badge ${["unlabeled","valve_test_app","cut_content"].includes(x) ? "hot" : ""}">${esc(x)}</span>`).join("")}
        <span class="badge">score ${f.score}</span></div>
      <div class="meta">v${f.max_version} · ${fmtDate(f.first_date)} → ${fmtDate(f.last_date)} · ${fmtBytes(f.dat_bytes)} payload</div>
      ${evHtml}
    </div>`;
  }).join("");
  box.querySelectorAll(".card").forEach((c) =>
    c.addEventListener("click", () => openDepot(Number(c.dataset.depot))));
}

// ---- boot ----
(async () => {
  try {
    catalog = await (await fetch("data/catalog.json")).json();
    renderCatalog("");
    $("#stat-depots").textContent = catalog.length.toLocaleString();
  } catch (e) { console.error(e); }
  try {
    findings = await (await fetch("data/findings.json")).json();
  } catch (e) { /* no findings yet */ }
})();
