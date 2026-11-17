"""Garmin Connect -> Supabase, through Garmin's official personal API. Run hourly by launchd
(tools/install_garmin_sync.sh).

    .venv/bin/python db/garmin_sync.py              # hourly: activities from the last 7 days, daily metrics for the last 3
    .venv/bin/python db/garmin_sync.py --days 90    # backfill
    .venv/bin/python db/garmin_sync.py --dry-run    # fetch and print, write nothing

- Activities -> `activities` (source 'garmin', external_id = Garmin activity id, so re-runs update, never duplicate).
- A watch-recorded strength session on a day I also logged a gym session in the app is merged into that app session
  (its heart rate, calories and Garmin details go into the app row) instead of becoming a second activity.
- Sleep, HRV, resting HR, Body Battery, stress, steps, VO2max -> `daily_metrics`, one row per day, plus training load
  worked out from the activities (load_7d ~ Garmin's acute load, load_28d ~ chronic). The personal API has no training
  readiness or training status, so those columns only hold history from before the switch (2026-11-15).
- The key: ~/.garminconnect/personal-api.json, written by db/garmin_key.py from 1Password. Read-only, and it expires.
- Telegram (through the PA bot): a message when the sync starts failing and when it recovers, and a week before the key
  expires.
"""
import collections, datetime as dt, json, os, pathlib, re, sys, traceback, urllib.parse, urllib.request
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import sb

DIR = pathlib.Path.home() / ".garminconnect"
KEY_FILE = DIR / "personal-api.json"
BEARER = DIR / "bearer.json"
STATE = DIR / "sync-state.json"
PA_ENV = pathlib.Path.home() / "n8n" / "pa.env"
METRIC_COLS = ["sleep_score", "sleep_min", "deep_min", "rem_min", "hrv_ms", "hrv_status", "resting_hr",
               "body_battery_high", "body_battery_low", "steps", "stress_avg", "vo2max"]   # what this API fills
CHUNK = 28                       # days per range call (sleep refuses more than about a month)
NEW_KEY = ("Make a new key: Garmin Connect web > Personal API Key (pick 365 days), save it in the 1Password item, "
           "then run\ncd ~/gym-planner && .venv/bin/python db/garmin_key.py")

TYPE_MAP = {   # Garmin activityType.typeKey (version suffix removed) -> activities.type
    "run": ["running", "trail_running", "treadmill_running", "track_running", "indoor_running", "ultra_run",
            "virtual_run", "obstacle_run"],
    "cycle": ["cycling", "road_biking", "mountain_biking", "indoor_cycling", "virtual_ride", "gravel_cycling",
              "cyclocross", "e_bike_fitness", "e_bike_mountain", "bmx", "recumbent_cycling"],
    "swim": ["lap_swimming", "open_water_swimming", "swimming"],
    "football": ["soccer", "football", "indoor_soccer", "american_football"],
    "climb": ["rock_climbing", "indoor_climbing", "bouldering", "climbing"],
    "tennis": ["tennis", "padel", "pickleball", "squash", "badminton", "racket_sports", "table_tennis"],
    "snowboard": ["snowboarding", "resort_snowboarding", "backcountry_snowboarding"],
    "surf": ["surfing"],
    "walk": ["walking", "hiking", "casual_walking", "speed_walking"],
    "mobility": ["yoga", "pilates", "breathwork", "stretching", "mobility"],
    "gym": ["strength_training", "hiit", "indoor_cardio", "fitness_equipment", "elliptical", "stair_climbing",
            "indoor_rowing"],
}
TYPE_OF = {k: t for t, ks in TYPE_MAP.items() for k in ks}
STRENGTH = {"strength_training"}  # merged into the app's gym session for the same day

ACTIVITY_KEEP = ["activityName", "activityType", "startTimeLocal", "startTimeGMT", "duration", "movingDuration",
                 "elapsedDuration", "distance", "averageSpeed", "maxSpeed", "avgGradeAdjustedSpeed", "averageHR", "maxHR",
                 "calories", "elevationGain", "elevationLoss", "aerobicTrainingEffect", "anaerobicTrainingEffect",
                 "trainingEffectLabel", "activityTrainingLoad", "differenceBodyBattery",
                 "averageRunningCadenceInStepsPerMinute", "avgStrideLength", "avgGroundContactTime",
                 "avgVerticalOscillation", "avgPower", "normPower", "moderateIntensityMinutes",
                 "vigorousIntensityMinutes", "hrTimeInZone_1", "hrTimeInZone_2", "hrTimeInZone_3", "hrTimeInZone_4",
                 "hrTimeInZone_5", "lapCount", "locationName"]


def dig(o, *path, default=None):
    for p in path:
        if isinstance(o, dict):
            o = o.get(p)
        elif isinstance(o, list) and isinstance(p, int) and -len(o) <= p < len(o):
            o = o[p]
        else:
            return default
        if o is None:
            return default
    return o


def mins(sec):
    return round(sec / 60) if isinstance(sec, (int, float)) else None


def scalars(d):
    """The flat, non-empty part of a Garmin dict (drops the big time series)."""
    return {k: v for k, v in (d or {}).items() if v is not None and not isinstance(v, (list, dict))}


def days_between(a, b):
    while a <= b:
        yield a
        a += dt.timedelta(days=1)


# ---------------------------------------------------------------- Telegram (through the PA bot)
def telegram(text):
    token = next((l.split("=", 1)[1].strip().strip('"\'') for l in PA_ENV.read_text().splitlines()
                  if l.strip().removeprefix("export ").startswith("PA_TELEGRAM_TOKEN=")), None) if PA_ENV.exists() else None
    chat = sb.ENV.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("no Telegram token/chat id; alert not sent:", text)
        return
    body = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    try:
        urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", body, timeout=20)
    except Exception as e:
        print("Telegram send failed:", e)


def read_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def write_state(st):
    DIR.mkdir(mode=0o700, exist_ok=True)
    STATE.write_text(json.dumps(st))
    os.chmod(STATE, 0o600)


# ---------------------------------------------------------------- Garmin
class KeyProblem(Exception):
    pass


def garmin_client():
    from garmin_personal_api.auth import PersonalApiAuthenticator
    from garmin_personal_api.client import GarminPersonalApiClient
    from garmin_personal_api.errors import HttpError
    if not KEY_FILE.exists():
        raise KeyProblem("no Garmin key saved yet. " + NEW_KEY)
    info = json.loads(KEY_FILE.read_text())
    try:
        with PersonalApiAuthenticator(bearer_token_cache_file=BEARER) as auth:
            tok = auth.obtain_token(info["key"])
    except HttpError as e:
        if e.status_code in (401, 403):
            raise KeyProblem(f"Garmin refused the key (HTTP {e.status_code}): it has expired or been deleted. " + NEW_KEY)
        raise
    return GarminPersonalApiClient(tok.access_token), info.get("expires")


def all_activities(c, start, end):
    out, at = [], 0
    while True:
        page = c.search_activities(start_date=start, end_date=end, start=at, limit=100) or []
        out += page
        if len(page) < 100:
            return out
        at += 100


# ---------------------------------------------------------------- Supabase
def must(label, res):
    s, r = res
    if s >= 300:
        raise RuntimeError(f"Supabase {label}: {s} {r}")
    return r


def type_key(a):
    """Garmin's sport key without its version suffix ("surfing_v2" -> "surfing")."""
    return re.sub(r"_v\d+$", "", dig(a, "activityType", "typeKey", default="other"))


def activity_row(a, uid):
    key = type_key(a)
    return {
        "user_id": uid, "source": "garmin", "external_id": str(a["activityId"]),
        "day": a["startTimeLocal"][:10],
        "started_at": a["startTimeGMT"].replace(" ", "T") + "Z" if a.get("startTimeGMT") else None,
        "type": TYPE_OF.get(key, "other"),
        "title": a.get("activityName") or key.replace("_", " ").title(),
        "status": "done",
        "duration_min": round(a["duration"] / 60, 1) if a.get("duration") else None,
        "distance_km": round(a["distance"] / 1000, 2) if a.get("distance") else None,
        "avg_hr": round(a["averageHR"]) if a.get("averageHR") else None,
        "data": {"garmin": {k: a[k] for k in ACTIVITY_KEEP if a.get(k) is not None}},
    }


def sync_activities(c, uid, start, end, dry):
    acts = all_activities(c, start, end)
    if not acts:
        return 0, 0
    ids = [str(a["activityId"]) for a in acts]
    known = must("read activities", sb.call("GET", "/rest/v1/activities?select=id,source,external_id,type,day&external_id=in.("
                                            + ",".join(ids) + f")&user_id=eq.{uid}"))
    by_ext = {}
    for r in known:
        by_ext.setdefault(r["external_id"], []).append(r)
    days = sorted({a["startTimeLocal"][:10] for a in acts})
    app_gym = must("read gym sessions", sb.call("GET", "/rest/v1/activities?select=id,day,started_at,status,external_id,avg_hr,data"
                                                f"&user_id=eq.{uid}&source=eq.app&type=eq.gym&day=in.({','.join(days)})"))
    new = merged = 0
    for a in acts:
        row, ext = activity_row(a, uid), str(a["activityId"])
        if type_key(a) in STRENGTH:
            # the app session that day (closest start time) that isn't already merged with another watch activity
            cands = [r for r in app_gym if r["day"] == row["day"] and r["status"] == "done" and r["external_id"] in (None, ext)]
            if any(r["day"] == row["day"] and r["status"] == "in_progress" for r in app_gym) and not cands:
                continue                     # still training: merge once the app session is finished
            if cands:
                t0 = dt.datetime.fromisoformat(row["started_at"].replace("Z", "+00:00")) if row["started_at"] else None
                def gap(r):
                    if not (t0 and r["started_at"]):
                        return 0
                    return abs((dt.datetime.fromisoformat(r["started_at"]) - t0).total_seconds())
                app = min(cands, key=gap)
                patch = {"external_id": ext, "avg_hr": app["avg_hr"] or row["avg_hr"],
                         "data": dict(app["data"] or {}, garmin=row["data"]["garmin"])}
                if dry:
                    print("  merge into app session", app["day"], row["title"], row["avg_hr"])
                else:
                    must("merge", sb.call("PATCH", f"/rest/v1/activities?id=eq.{app['id']}", patch, prefer="return=minimal"))
                    for r in by_ext.get(ext, []):        # an earlier sync stored it on its own: drop that copy
                        if r["source"] == "garmin":
                            must("drop duplicate", sb.call("DELETE", f"/rest/v1/activities?id=eq.{r['id']}"))
                app["external_id"] = ext
                merged += 1
                continue
        mine = [r for r in by_ext.get(ext, []) if r["source"] == "garmin"]
        if any(r["source"] == "app" for r in by_ext.get(ext, [])):
            continue                                  # already merged into an app session
        if dry:
            print(f"  {'update' if mine else 'new   '} {row['day']} {row['type']:9} {row['title']}  "
                  f"{row['distance_km'] or ''}km {row['duration_min']}min HR {row['avg_hr']}")
        elif mine:
            must("update activity", sb.call("PATCH", f"/rest/v1/activities?id=eq.{mine[0]['id']}", row, prefer="return=minimal"))
        else:
            must("insert activity", sb.call("POST", "/rest/v1/activities", row, prefer="return=minimal"))
            new += 1
    return new, merged


def daily_metrics(c, uid, start, end):
    """One dict per day from the range endpoints, fetched a month at a time."""
    out = {d.isoformat(): {"day": d.isoformat(), "data": {}} for d in days_between(start, end)}
    vo2 = {}
    a = start
    while a <= end:
        b = min(end, a + dt.timedelta(days=CHUNK - 1))
        A, B = a.isoformat(), b.isoformat()
        for s in dig(c.get_daily_summaries(A, B), "data", default=[]):
            m = out.get(s.get("calendarDate"))
            if m is None:
                continue
            stress = dig(s, "stress", "avgLevel")
            m.update(resting_hr=dig(s, "heartRate", "restingValue"), body_battery_high=dig(s, "bodyBattery", "maxValue"),
                     body_battery_low=dig(s, "bodyBattery", "minValue"), steps=dig(s, "movement", "steps", "value"),
                     stress_avg=stress if isinstance(stress, (int, float)) and stress >= 0 else None)
            m["data"]["summary"] = {"calories": s.get("calories"), "intensityMinutes": s.get("intensityMinutes"),
                                    "bodyBattery": scalars(s.get("bodyBattery")), "heartRate": s.get("heartRate"),
                                    "stress": scalars(s.get("stress")), "respiration": scalars(s.get("respiration")),
                                    "pulseOx": scalars(s.get("pulseOx"))}
        for sl in c.get_daily_sleep_summaries(A, B) or []:
            m = out.get(sl.get("calendarDate"))
            if m is None:
                continue
            m.update(sleep_score=dig(sl, "sleepScores", "overall", "value"), sleep_min=mins(sl.get("sleepTimeSeconds")),
                     deep_min=mins(sl.get("deepSleepSeconds")), rem_min=mins(sl.get("remSleepSeconds")))
            m["data"]["sleep"] = scalars(sl)
        for h in dig(c.get_daily_hrv_summaries(A, B), "hrvSummaries", default=[]):
            m = out.get(h.get("calendarDate"))
            if m is None:
                continue
            m.update(hrv_ms=h.get("lastNightAvg"), hrv_status=h.get("status"))
            m["data"]["hrv"] = {**scalars(h), **({"baseline": h["baseline"]} if h.get("baseline") else {})}
        for v in c.get(f"/metrics-service/metrics/maxmet/daily/{A}/{B}") or []:
            g = v.get("generic") or {}
            if g.get("calendarDate") and (g.get("vo2MaxPreciseValue") or g.get("vo2MaxValue")):
                vo2[g["calendarDate"]] = g.get("vo2MaxPreciseValue") or g.get("vo2MaxValue")
        a = b + dt.timedelta(days=1)

    # VO2max only comes on days Garmin recalculates it: carry the latest value forward, as Garmin shows it
    last = must("last vo2max", sb.call("GET", f"/rest/v1/daily_metrics?select=vo2max&user_id=eq.{uid}&vo2max=not.is.null"
                                       f"&day=lt.{start.isoformat()}&order=day.desc&limit=1"))
    cur = last[0]["vo2max"] if last else None
    for d in sorted(out):
        cur = vo2.get(d, cur)
        out[d]["vo2max"] = cur
    return out


def training_load(uid, start, end):
    """load_7d / load_28d for each day, from the activities' Garmin training load (any source: merged gym sessions too)."""
    since = (start - dt.timedelta(days=27)).isoformat()
    rows = must("read loads", sb.call("GET", f"/rest/v1/activities?select=day,data->garmin->activityTrainingLoad"
                                      f"&user_id=eq.{uid}&day=gte.{since}&day=lte.{end.isoformat()}"))
    per_day = collections.defaultdict(float)
    for r in rows:
        if isinstance(r.get("activityTrainingLoad"), (int, float)):
            per_day[r["day"]] += r["activityTrainingLoad"]
    def window(d, n):
        return sum(per_day.get((d - dt.timedelta(days=i)).isoformat(), 0.0) for i in range(n))
    return {d.isoformat(): {"load_7d": round(window(d, 7)), "load_28d": round(window(d, 28) / 4)} for d in days_between(start, end)}


def main(state):
    args = sys.argv[1:]
    dry = "--dry-run" in args
    days = int(args[args.index("--days") + 1]) if "--days" in args else None
    today = dt.date.today()
    act_from = today - dt.timedelta(days=(days or 7) - 1)
    met_from = today - dt.timedelta(days=(days or 3) - 1)

    c, expires = garmin_client()
    uid = sb.user_id()
    new, merged = sync_activities(c, uid, act_from, today, dry)
    metrics = daily_metrics(c, uid, met_from, today)
    loads = training_load(uid, met_from, today)
    keys = ("sleep_score", "hrv_ms", "resting_hr", "steps", "body_battery_high")
    days_with_data = [d for d, m in sorted(metrics.items()) if any(m.get(k) is not None for k in keys)]
    # merge into what's stored: `data` keeps older keys (e.g. readiness/training history from before the switch),
    # and the columns this API doesn't have (readiness, training status, acute/chronic) are never sent, so never wiped
    stored = {r["day"]: r["data"] or {} for r in must("read stored metrics", sb.call(
        "GET", f"/rest/v1/daily_metrics?select=day,data&user_id=eq.{uid}&day=gte.{met_from.isoformat()}"))}
    rows = [{"user_id": uid, "source": "garmin", "day": d, **{k: metrics[d].get(k) for k in METRIC_COLS},
             **loads[d], "data": {**stored.get(d, {}), **metrics[d]["data"]}} for d in days_with_data]
    if dry:
        for r in rows:
            print("  ", r["day"], {k: v for k, v in r.items() if k not in ("data", "day", "user_id", "source") and v is not None})
    elif rows:
        for i in range(0, len(rows), 50):
            must("upsert daily metrics", sb.call("POST", "/rest/v1/daily_metrics?on_conflict=user_id,day", rows[i:i + 50],
                                                 prefer="resolution=merge-duplicates,return=minimal"))

    # a week's warning before the key runs out (once per key)
    if expires and not dry:
        left = (dt.date.fromisoformat(expires) - today).days
        if left <= 7 and state.get("expiry_warned") != expires:
            telegram(f"🔑 The Garmin key for the gym app expires on {expires} ({left} days). " + NEW_KEY)
            state["expiry_warned"] = expires
    return f"{new} new activities, {merged} merged into app gym sessions, {len(rows)} days of metrics"


if __name__ == "__main__":
    st = read_state()
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    dry = "--dry-run" in sys.argv
    try:
        summary = main(st)
        print(stamp, "ok:", summary)
        if st.get("failing") and not dry:
            telegram("✅ Garmin sync is working again: " + summary)
        if not dry:
            write_state({**st, "failing": False, "last_ok": stamp})
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(stamp, "FAILED:", msg)
        traceback.print_exc()
        if not st.get("failing") and not dry:
            hint = "" if isinstance(e, KeyProblem) else "\nLog: ~/Library/Logs/gym-garmin-sync.log"
            telegram(f"⚠️ Garmin sync failed: {msg[:400]}{hint}")
        if not dry:
            write_state({**st, "failing": True, "last_error": msg[:500], "failed_at": stamp})
        sys.exit(1)
