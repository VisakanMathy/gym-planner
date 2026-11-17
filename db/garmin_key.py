"""Copy the Garmin personal API key from 1Password to ~/.garminconnect/personal-api.json for the hourly sync.

    .venv/bin/python db/garmin_key.py

Run it once now and again whenever you make a new key (Garmin Connect web > Personal API Key; pick 365 days and
save it in 1Password, replacing the old one). The 1Password item is GARMIN_KEY_OP_REF in .env; the key goes in its
`credential` field and the expiry in `expires`. The launchd job can't use Touch ID, so it reads this file, which only
you can read. The key is read-only and expires; the sync warns on Telegram a week before it does.
"""
import datetime as dt, json, os, pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import sb

DIR = pathlib.Path.home() / ".garminconnect"
OUT = DIR / "personal-api.json"
ref = sb.ENV.get("GARMIN_KEY_OP_REF") or sys.exit("set GARMIN_KEY_OP_REF in .env (op://<vault>/<item id>)")


def op(field):
    r = subprocess.run(["op", "read", f"{ref}/{field}"], capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"1Password: couldn't read {ref}/{field}: {r.stderr.strip()[:150]}")
    return r.stdout.strip()


key, expires = op("credential"), op("expires")
exp = dt.datetime.fromtimestamp(int(expires)).date().isoformat() if expires.isdigit() else expires[:10]
DIR.mkdir(mode=0o700, exist_ok=True)
fd = os.open(OUT, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump({"key": key, "expires": exp}, f)
os.chmod(OUT, 0o600)
(DIR / "bearer.json").unlink(missing_ok=True)      # a token from the old key would be refused
print(f"Saved the Garmin key (expires {exp}) to {OUT}")
