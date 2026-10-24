// Contract checks using the production functions. This is not a real DOM/browser.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { gzipSync } from "node:zlib";
import vm from "node:vm";

const source = await readFile(new URL("../site/app.js", import.meta.url), "utf8");
function section(begin, end) {
  const start = source.indexOf(begin);
  const stop = source.indexOf(end, start);
  assert(start >= 0 && stop > start, `production section ${begin} exists`);
  return source.slice(start, stop);
}
const helpers = section("function fmtBytes(", "function fmtDate(") +
  section("function renderVersionFiles(", "// Models are pre-extracted");
const browserFunction = section("async function wireVersionBrowser(", "// ---- per-version file browser ----");
const loader = section("const vfileCache =", "function renderVersionFiles(");
const SNAPSHOT = "a".repeat(64);
const tick = () => new Promise((resolve) => setImmediate(resolve));
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function rowPage(version, rows, page = 0, overrides = {}) {
  return { schema_version: 2, depot: 3591, data_revision: SNAPSHOT,
    version, page, rows, ...overrides };
}
function status(count, state = "valid") {
  return { state, header_file_count: count, parsed_file_count: count };
}
const validData = { schema_version: 2, depot: 3591, data_revision: SNAPSHOT,
  paged: true, page_rows: 1,
  version_status: { "6": status(1), "7": status(1) },
  version_pages: { "6": 1, "7": 1 } };

function makeContext({ versions = [{ v: 6 }], data = validData, fetchPage,
                       loadVFiles = async () => data } = {}) {
  const elements = {};
  const element = (extra = {}) => ({ listeners: {}, ...extra,
    addEventListener(name, callback) { this.listeners[name] = callback; } });
  const sel = element({ value: String(versions[0].v), isConnected: true });
  const list = { innerHTML: "" }, stat = { textContent: "" };
  const controls = { dataset: {}, setAttribute(name, value) {
    if (name === "data-page") this.dataset.page = value;
  } };
  let controlHTML = "";
  Object.defineProperty(controls, "innerHTML", {
    get: () => controlHTML,
    set: (value) => {
      controlHTML = value;
      for (const id of ["vpage-prev", "vpage-next"]) {
        elements[`#${id}`] = value.includes(`id="${id}"`) ? element() : null;
      }
    },
  });
  const filter = element({ value: "" });
  Object.assign(elements, { "#vsel": sel, "#vflist": list, "#vstat": stat,
    "#vpage-controls": controls, "#vf-filter": filter });
  const context = vm.createContext({ depotOpenGeneration: 1,
    $: (selector) => elements[selector] ?? null,
    loadVFiles, fetchJSONgz: fetchPage,
    esc: (value) => String(value ?? ""), Number, String, Array, Math });
  vm.runInContext(helpers + browserFunction, context);
  return { context, sel, list, stat, controls, filter, elements, versions };
}
const open = (t, expected = SNAPSHOT) => t.context.wireVersionBrowser(3591, t.versions, 1, expected);
let passed = 0;
async function check(name, run) {
  await run(); passed++; console.log(`ok ${passed}: ${name}`);
}

await check("late old revision response is ignored", async () => {
  const old = deferred(), current = deferred();
  const t = makeContext({ versions: [{ v: 6 }, { v: 7 }],
    fetchPage: (url) => url.includes(".6.") ? old.promise : current.promise });
  const opening = open(t); await tick();
  t.sel.value = "7";
  const changed = t.sel.listeners.change();
  current.resolve(rowPage(7, [["current.bin", 7]])); await changed;
  old.resolve(rowPage(6, [["stale.bin", 6]])); await opening;
  assert.match(t.list.innerHTML, /current\.bin/);
  assert.doesNotMatch(t.list.innerHTML, /stale\.bin/);
});

await check("malformed page rows are unavailable", async () => {
  const t = makeContext({ fetchPage: async () => rowPage(6, { bad: true }) });
  await open(t); assert.equal(t.stat.textContent, "page unavailable");
});

await check("late depot metadata after navigation is ignored", async () => {
  const metadata = deferred();
  const t = makeContext({ loadVFiles: () => metadata.promise,
    fetchPage: () => { throw new Error("must not fetch"); } });
  const opening = open(t); t.context.depotOpenGeneration = 2;
  metadata.resolve(validData); await opening;
  assert.equal(t.list.innerHTML, "");
});

await check("UNKNOWN does not become an empty manifest", async () => {
  const t = makeContext({ data: { ...validData, version_status: { "6": status(null, "unavailable") } },
    fetchPage: () => { throw new Error("must not fetch"); } });
  await open(t); assert.match(t.list.innerHTML, /unknown for v6: unavailable/);
});

await check("empty valid manifest has explicit zero counts", async () => {
  const t = makeContext({ data: { ...validData, version_status: { "6": status(0) },
    version_pages: { "6": 0 } }, fetchPage: () => { throw new Error("must not fetch"); } });
  await open(t); assert.match(t.list.innerHTML, /Validated manifest: no file records/);
});

await check("V1 and pre-snapshot V2 stay unvalidated; trust labels stay distinct", async () => {
  const old = makeContext({ data: { depot: 3591, files: { "6": [] } } });
  await open(old); assert.match(old.list.innerHTML, /older data format/);
  const oldV2 = makeContext({ data: { ...validData, data_revision: undefined } });
  await open(oldV2); assert.equal(oldV2.stat.textContent, "historical list not validated");
  assert.match(old.context.versionFileLabel({ files: 61 }), /legacy\/inferred/);
  assert.match(old.context.versionFileLabel({ manifest_state: "exact", files: 0 }), /0 files.*exact/);
  assert.match(old.context.versionFileLabel({ manifest_state: "unknown" }), /unknown/);
});

await check("sizes above 4 GiB and safe-integer range; zero stays known", async () => {
  const t = makeContext();
  assert.match(t.context.fmtBytes(5 * 1024 ** 3), /5\.0 GiB/);
  assert.equal(t.context.fmtBytes(0), "0 B");
  assert.equal(t.context.isValidFileRow(["max", "9223372036854775807"]), true);
  assert.equal(t.context.isValidFileRow(["bad", "9223372036854775808"]), false);
  assert.equal(t.context.isValidFileRow(["rounded", Number("9223372036854775807")]), false);
  assert.match(t.context.fmtBytes("9223372036854775807"), /EiB/);
});

await check("wrong depot, revision, page and snapshot are rejected", async () => {
  for (const overrides of [{ depot: 4 }, { version: 7 }, { page: 1 }, { data_revision: "b".repeat(64) }]) {
    const t = makeContext({ fetchPage: async () => rowPage(6, [["wrong.bin", 7]], 0, overrides) });
    await open(t); assert.equal(t.stat.textContent, "page unavailable");
    assert.doesNotMatch(t.list.innerHTML, /wrong\.bin/);
  }
});

await check("nonempty header plus zero pages cannot look empty-valid", async () => {
  const t = makeContext({ data: { ...validData, version_pages: { "6": 0 } } });
  await open(t); assert.equal(t.stat.textContent, "pages unavailable");
});

await check("whole-depot record counts must match", async () => {
  const t = makeContext({ data: { ...validData, paged: false, files: { "6": [] } } });
  await open(t); assert.equal(t.stat.textContent, "records missing");
});

await check("filtering and removal/restoration use selected exact revision", async () => {
  const t = makeContext({ versions: [{ v: 6 }, { v: 7 }, { v: 8 }],
    data: { ...validData, paged: false,
      version_status: { "6": status(2), "7": status(1), "8": status(2) },
      files: { "6": [["return.bin", 10], ["stable.bin", 5]], "7": [["stable.bin", 5]],
        "8": [["return.bin", 12], ["stable.bin", 5]] } } });
  await open(t); assert.match(t.list.innerHTML, /return\.bin/);
  t.filter.value = "RETURN"; await t.filter.listeners.input();
  assert.doesNotMatch(t.list.innerHTML, /stable\.bin/);
  t.sel.value = "7"; await t.sel.listeners.change();
  assert.doesNotMatch(t.list.innerHTML, /return\.bin/);
  t.sel.value = "8"; await t.sel.listeners.change();
  assert.match(t.list.innerHTML, /return\.bin/);
});

await check("pagination callbacks and revision change reset to page zero", async () => {
  const t = makeContext({ versions: [{ v: 6 }, { v: 7 }], data: { ...validData,
    version_status: { "6": status(2), "7": status(1) }, version_pages: { "6": 2, "7": 1 } },
    fetchPage: async (url) => url.includes(".6.1.") ? rowPage(6, [["second.bin", 2]], 1) :
      url.includes(".7.") ? rowPage(7, [["new.bin", 1]]) : rowPage(6, [["first.bin", 1]]) });
  await open(t); t.elements["#vpage-next"].listeners.click(); await tick();
  assert.match(t.list.innerHTML, /second\.bin/);
  t.sel.value = "7"; await t.sel.listeners.change();
  assert.equal(t.controls.dataset.page, "0"); assert.match(t.list.innerHTML, /new\.bin/);
  assert.equal(t.filter.placeholder, "Filter this page…");
});

await check("missing/corrupt page has no stale navigation controls", async () => {
  const t = makeContext({ fetchPage: async () => { throw new Error("invalid gzip"); } });
  await open(t); assert.equal(t.stat.textContent, "page unavailable");
  assert.equal(t.controls.innerHTML, "");
});

function loaderContext(fetch) {
  const context = vm.createContext({ fetch, window: { DecompressionStream },
    DecompressionStream, Blob, Response, Uint8Array, TextDecoder, encodeURIComponent });
  vm.runInContext(loader, context); return context;
}
const response = (data) => new Response(JSON.stringify(data));

await check("snapshot cache cannot reuse an old source or let V1 mask new paged data", async () => {
  let calls = 0;
  const context = loaderContext(async (url) => {
    calls++;
    if (url.includes(".meta.")) return response(validData);
    return response({ depot: 3591, files: { "6": [] } });
  });
  const first = await context.loadVFiles(3591, SNAPSHOT);
  assert.equal(first.data_revision, SNAPSHOT); assert.equal(calls, 2);
  await context.loadVFiles(3591, SNAPSHOT); assert.equal(calls, 2);
  const newer = "b".repeat(64);
  context.fetch = async () => { calls++; return response({ ...validData, data_revision: newer }); };
  const next = await context.loadVFiles(3591, newer);
  assert.equal(next.data_revision, newer); assert.equal(calls, 3);
});

await check("real gzip decoder handles gzip, decoded JSON, bad gzip and bad JSON", async () => {
  const context = loaderContext(async () => new Response(gzipSync(JSON.stringify(validData))));
  assert.equal((await context.fetchJSONgz("local")).data_revision, SNAPSHOT);
  context.fetch = async () => response(validData);
  assert.equal((await context.fetchJSONgz("local")).schema_version, 2);
  context.fetch = async () => new Response(new Uint8Array([0x1f, 0x8b, 0, 0]));
  await assert.rejects(() => context.fetchJSONgz("local"));
  context.fetch = async () => new Response("{not JSON");
  await assert.rejects(() => context.fetchJSONgz("local"));
});

console.log(`frontend_harness: ${passed} scenarios passed`);
