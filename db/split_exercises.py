"""Split a plan with inline exercises into an exercise catalogue + a plan whose slots point at it.

    .venv/bin/python db/split_exercises.py            # plan.json -> exercises.json + plan.json (old one kept as plan.inline.json)

Catalogue row = what the exercise IS: name, photos, captions, video, default target/cues, muscle groups.
Plan slot     = how it's done that day: id, stage, detail, log, prog, startW, plus `exercise` (catalogue id)
                and any target/caps/cues/vid that differ from the catalogue default (the day-specific wording).
Swaps (`alts`) are refs too: {exercise, ...overrides, prog?, startW?}.
The app rebuilds the old inline shape from the two (hydrate in app.js); this script checks that round trip
reproduces the original plan exactly before writing anything. Safe to re-run: a plan already split is left alone.
"""
import collections, json, pathlib, re, shutil, sys

HERE = pathlib.Path(__file__).resolve().parents[1]
EX_FIELDS = ["name", "target", "imgs", "caps", "cues", "vid", "yt", "svgs"]   # catalogue fields (same list as app.js)
SLOT_FIELDS = ["id", "stage", "detail", "log"]                                 # always the slot's own
# Where the name/target patterns guess wrong. After the split, edit `groups` in the table (or exercises.json) directly.
GROUP_FIXES = {
    "knees-to-chest": ["back", "mobility"],          # "chest" is in the name, not the muscle
    "side-plank-hip-dips": ["core", "hips"],         # "dips" here isn't the triceps exercise
}


def slugify(t):  # same rule as slugify() in app.js and schema.sql
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")


def muscle_groups():
    """The GROUPS table in app.js, so the muscle-group rules live in one place."""
    src = (HERE / "app.js").read_text()
    block = src[src.index("const GROUPS = ["):]
    block = block[:block.index("];")]
    return [(k, re.compile(p)) for k, p in re.findall(r"\['(\w+)',\s*'[^']*',\s*/(.+?)/\]", block)]


def hydrate(days, cat):
    def from_ref(ref):
        e = cat[ref["exercise"]]
        out = {f: e[f] for f in EX_FIELDS if e.get(f) is not None}
        out.update({k: v for k, v in ref.items() if k != "exercise"})
        return out
    return {d: [dict(from_ref({k: v for k, v in s.items() if k != "alts"}), alts=[from_ref(a) for a in s.get("alts", [])])
                for s in slots] for d, slots in days.items()}


def drop_nulls(x):
    """`"imgs": null` and no imgs mean the same to the app."""
    if isinstance(x, dict):
        return {k: drop_nulls(v) for k, v in x.items() if v is not None}
    return [drop_nulls(v) for v in x] if isinstance(x, list) else x


def split(plan):
    days = plan["days"]
    occ = collections.defaultdict(list)          # id -> [(is_base, exercise dict)]
    for slots in days.values():
        for s in slots:
            occ[slugify(s["name"])].append((True, s))
            for a in s.get("alts", []):
                occ[slugify(a["name"])].append((False, a))

    groups = muscle_groups()
    cat = {}
    for xid, uses in occ.items():
        names = {u["name"] for _, u in uses}
        if len(names) > 1:
            sys.exit(f"two names share the id {xid}: {names}")
        row = {"id": xid, "aliases": [], "source": "plan"}
        for f in EX_FIELDS:
            vals = [json.dumps(u.get(f), sort_keys=True) for _, u in uses]
            # default = most common value; ties go to the first use as a main exercise, then the first use at all
            first_base = next((json.dumps(u.get(f), sort_keys=True) for b, u in uses if b), vals[0])
            best = max(collections.Counter(vals).items(), key=lambda kv: (kv[1], kv[0] == first_base))[0]
            row[f] = json.loads(best)
        texts = {(u.get("target") or "").lower() for _, u in uses} | {row["name"].lower()}
        row["groups"] = GROUP_FIXES.get(xid) or [k for k, rx in groups if any(rx.search(t) for t in texts)]
        cat[xid] = row

    def ref(src, keep):
        e = cat[slugify(src["name"])]
        out = {k: src[k] for k in keep if k in src}
        out["exercise"] = e["id"]
        for f in EX_FIELDS:
            if f in src and src[f] != e[f]:
                out[f] = src[f]
            elif f not in src and e.get(f) is not None:
                sys.exit(f"{src['name']} has no {f} but the catalogue does; can't express that as an override")
        for k in ("prog", "startW"):
            if k in src:
                out[k] = src[k]
        return out

    new_days = {d: [dict(ref(s, SLOT_FIELDS), alts=[ref(a, []) for a in s.get("alts", [])]) for s in slots]
                for d, slots in days.items()}
    if drop_nulls(hydrate(new_days, cat)) != drop_nulls(days):
        sys.exit("round trip failed: the split plan doesn't rebuild the original")
    return dict(plan, days=new_days), cat


if __name__ == "__main__":
    plan = json.loads((HERE / "plan.json").read_text())
    if all("exercise" in s for slots in plan["days"].values() for s in slots):
        sys.exit("plan.json already points at the catalogue; nothing to do")
    new_plan, cat = split(plan)
    shutil.copy(HERE / "plan.json", HERE / "plan.inline.json")
    (HERE / "plan.json").write_text(json.dumps(new_plan, indent=1, ensure_ascii=False))
    (HERE / "exercises.json").write_text(json.dumps(list(cat.values()), indent=1, ensure_ascii=False))
    n_over = sum(len([f for f in EX_FIELDS if f in r]) for slots in new_plan["days"].values()
                 for s in slots for r in [s, *s["alts"]])
    print(f"{len(cat)} exercises; {n_over} day-specific overrides kept in the plan; round trip OK")
    print(f"plan.json {len(json.dumps(plan))//1024}KB -> {len(json.dumps(new_plan))//1024}KB (+ exercises.json)")
