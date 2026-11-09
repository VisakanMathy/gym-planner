// Pulls `const PLAN` (with its SVG constants) out of the old single-file app and writes plan.json.
// Each exercise gets a stable `id` (slot) so logs never depend on card position again.
import fs from 'node:fs';
import vm from 'node:vm';
const src = fs.readFileSync(process.argv[2], 'utf8');
const start = src.indexOf('<script>') + 8;
const end = src.indexOf('const STAGE_LABEL');
const ctx = {};
vm.runInNewContext(src.slice(start, end).replace('const PLAN', 'globalThis.PLAN'), ctx);
const slug = s => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const days = {};
for (const [day, list] of Object.entries(ctx.PLAN)) {
  const seen = {};
  days[day] = list.map(ex => {
    let id = `${day}:${slug(ex.name)}`;
    seen[id] = (seen[id] || 0) + 1;
    if (seen[id] > 1) id += `-${seen[id]}`;
    const out = { id, ...ex };
    if (out.svgs === null) delete out.svgs;
    return out;
  });
}
const plan = {
  days,
  day_order: ['morning', 'lower', 'upperH', 'upperV', 'full'],
  day_labels: { morning: 'Morning', lower: 'Lower', upperH: 'Upper H', upperV: 'Upper V', full: 'Home / Stability' },
  stage_labels: { warmup: 'Warm-up', main: 'Main session', cooldown: 'Cool-down', mobility: 'Mobility' },
};
fs.writeFileSync(process.argv[3], JSON.stringify(plan, null, 1));
for (const [d, l] of Object.entries(days)) console.log(d, l.length, 'cards,', l.reduce((n, e) => n + (e.alts || []).length, 0), 'swaps,', l.filter(e => e.log).length, 'logged');
