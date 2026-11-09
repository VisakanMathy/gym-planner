"""Tiny Supabase REST helper using ~/gym-planner/.env (secret key: bypasses row security; Mac-side only)."""
import json, pathlib, re, urllib.request, urllib.error
HERE = pathlib.Path(__file__).resolve().parents[1]
ENV = {m[1]: m[2].strip().strip('"').strip("'") for l in (HERE / ".env").read_text().splitlines()
       if (m := re.match(r"^\s*([A-Z_]+)\s*=\s*(.*)$", l.strip()))}
URL = ENV["SUPABASE_URL"].rstrip("/")

def call(method, path, body=None, key=None, token=None, prefer=None):
    key = key or ENV["SUPABASE_SERVICE_ROLE_KEY"]
    h = {"apikey": key, "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if prefer:
        h["Prefer"] = prefer
    req = urllib.request.Request(URL + path, method=method, headers=h,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            t = r.read()
            return r.status, (json.loads(t) if t else None)
    except urllib.error.HTTPError as e:
        t = e.read()
        try:
            return e.code, json.loads(t)
        except Exception:
            return e.code, t.decode(errors="replace")

def user_id(email="visakanmathivannan@gmail.com"):
    s, users = call("GET", "/auth/v1/admin/users?per_page=50")
    for u in users.get("users", []):
        if u.get("email") == email:
            return u["id"]
    return None
