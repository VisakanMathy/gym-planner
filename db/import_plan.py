"""Loads plan.json as the active plan (keeps old versions) and profile.json as the profile.
Usage: .venv/bin/python db/import_plan.py VERSION "note" """
import json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import sb
HERE = sb.HERE
uid = sb.user_id()
plan = json.loads((HERE / "plan.json").read_text())
version, note = sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""
s, r = sb.call("PATCH", f"/rest/v1/plans?user_id=eq.{uid}&active=is.true", {"active": False})
s, r = sb.call("POST", "/rest/v1/plans", {"user_id": uid, "version": version, "plan": plan, "note": note,
                                          "created_by": "import", "active": True}, prefer="return=representation")
print("plan:", s, r[0]["version"] if isinstance(r, list) else r)
prof = json.loads((HERE / "db/profile.json").read_text())
s, r = sb.call("POST", "/rest/v1/profile", {"user_id": uid, "data": prof}, prefer="resolution=merge-duplicates,return=minimal")
print("profile:", s, r or "")
