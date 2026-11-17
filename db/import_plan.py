"""Loads exercises.json into the exercise catalogue, plan.json as a new plan version, and profile.json as the profile.

    .venv/bin/python db/import_plan.py VERSION "note"              # becomes the active plan
    .venv/bin/python db/import_plan.py VERSION "note" --inactive   # stored, but the app keeps the current one

plan.json slots point at catalogue ids (see db/split_exercises.py). Catalogue rows are upserted: a row already
there is updated from the file, rows not in the file (exercises I typed in) are left alone.
"""
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import sb
HERE = sb.HERE
args = [a for a in sys.argv[1:] if not a.startswith("--")]
inactive = "--inactive" in sys.argv
version, note = args[0], args[1] if len(args) > 1 else ""
uid = sb.user_id()

def ok(label, s, r):
    print(label, s, r if s >= 300 else "")
    if s >= 300:
        sys.exit(1)

exercises = json.loads((HERE / "exercises.json").read_text())
plan = json.loads((HERE / "plan.json").read_text())
missing = {r["exercise"] for slots in plan["days"].values() for s in slots for r in [s, *s.get("alts", [])]} \
          - {e["id"] for e in exercises}
if missing:
    sys.exit(f"plan.json points at exercises that aren't in exercises.json: {sorted(missing)}")

ok("exercises:", *sb.call("POST", "/rest/v1/exercises?on_conflict=user_id,id", [dict(e, user_id=uid) for e in exercises],
                          prefer="resolution=merge-duplicates,return=minimal"))
if not inactive:
    ok("deactivate old plan:", *sb.call("PATCH", f"/rest/v1/plans?user_id=eq.{uid}&active=is.true", {"active": False}))
s, r = sb.call("POST", "/rest/v1/plans", {"user_id": uid, "version": version, "plan": plan, "note": note,
                                          "created_by": "import", "active": not inactive}, prefer="return=representation")
ok("plan:", s, r)
print("  ", r[0]["version"], "active" if r[0]["active"] else "stored, not active", r[0]["id"])
prof = json.loads((HERE / "db/profile.json").read_text())
ok("profile:", *sb.call("POST", "/rest/v1/profile", {"user_id": uid, "data": prof}, prefer="resolution=merge-duplicates,return=minimal"))
