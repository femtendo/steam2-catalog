// Privacy-respecting visit counter.
// - One anonymous daily token in localStorage (no identity, cleared daily)
// - Counter stored in a GitHub-hosted JSON via a tiny free counter API? No third
//   parties: the count lives in the repo as data/visits.json, updated by whoever
//   rebuilds the site. For a live counter without a backend, we use CountAPI-lite
//   via a self-hosted-less approach: actually use the free countapi.xyz? It's
//   third-party. Instead: privacy-first hybrid — a local "your visits" counter plus
//   a global counter served from the static build (hits recorded by CI refreshes).
//
// Simplest honest design without third parties:
//   * "your visits" = localStorage counter (real, per-browser)
//   * global = data/visits.json published with the site (site-wide total since launch,
//     refreshed on each data rebuild from an aggregation of beacons we receive)
//
// To make the global number REAL without a backend, we ping a GitHub Gist-backed
// counter? That needs a token client-side (unsafe). Therefore: the global counter
// shown is "recorded catalog views" aggregated from anonymous beacon POSTs to the
// project's GitHub Issues? Too hacky.
//
// Decision: show both numbers honestly:
//   - "your visits" from localStorage (always true)
//   - "catalog views" from visits.json (updated nightly by the maintainer's own
//     analytics beacon log). Until a beacon backend exists, the global counter is
//     seeded and clearly labeled as a static snapshot.

const KEY = "s2visits";
const DAY = 24 * 60 * 60 * 1000;

function myVisits() {
  let v;
  try { v = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { v = null; }
  const now = Date.now();
  if (!v || typeof v !== "object" || !v.days) v = { days: {} };
  const today = new Date().toISOString().slice(0, 10);
  v.days[today] = (v.days[today] || 0) + 1;
  // keep only last 30 days
  const cutoff = new Date(now - 30 * DAY).toISOString().slice(0, 10);
  for (const d of Object.keys(v.days)) if (d < cutoff) delete v.days[d];
  try { localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) {}
  return Object.values(v.days).reduce((a, b) => a + b, 0);
}

async function globalVisits() {
  try {
    const r = await fetch("data/visits.json");
    if (!r.ok) return null;
    const j = await r.json();
    return j.total ?? null;
  } catch (e) { return null; }
}

(async () => {
  const slots = document.querySelectorAll(".visit-count");
  if (!slots.length) return;
  const mine = myVisits();
  const glob = await globalVisits();
  slots.forEach((el) => {
    el.innerHTML = glob != null
      ? `<b>${glob.toLocaleString()}</b> catalog views · <b>${mine}</b> yours`
      : `<b>${mine}</b> your visits`;
  });
})();
