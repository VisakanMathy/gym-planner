"""Rebuild the inline plan (exercise fields written out in each slot) from plan.json + exercises.json.

    .venv/bin/python db/inline_plan.py            # writes plan.inline.json

Same merge as hydrate() in app.js: catalogue fields first, then the slot's own overrides. The live plan
(v3.4.x) is inline, so edit exercises.json / plan.json and regenerate this rather than editing it by hand.
"""
import json, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parents[1]
EX_FIELDS = ["name", "target", "imgs", "caps", "cues", "vid", "yt", "svgs"]

def inline(plan, exercises):
    cat = {e["id"]: e for e in exercises}
    def ref(r):
        base = {f: cat[r["exercise"]][f] for f in EX_FIELDS if cat[r["exercise"]].get(f) is not None}
        out = {**base, **{k: v for k, v in r.items() if k != "exercise"}}
        return out
    days = {d: [{**ref(s), "alts": [ref(a) for a in s.get("alts", [])]} for s in slots] for d, slots in plan["days"].items()}
    return {**plan, "days": days}

if __name__ == "__main__":
    src = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else HERE
    out = inline(json.loads((src / "plan.json").read_text()), json.loads((src / "exercises.json").read_text()))
    if "--check" in sys.argv:
        print(json.dumps(out, sort_keys=True) == json.dumps(json.loads((src / "plan.inline.json").read_text()), sort_keys=True))
    else:
        (HERE / "plan.inline.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
