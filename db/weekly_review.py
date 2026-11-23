"""Weekly progress review: Supabase + profile -> Claude -> Telegram (through the PA bot).
Run by launchd on Sunday evening (tools/install_weekly_review.sh).

    .venv/bin/python db/weekly_review.py              # review the week ending today, send to Telegram
    .venv/bin/python db/weekly_review.py --dry-run    # print the review, send nothing
    .venv/bin/python db/weekly_review.py --data       # print only the data bundle Claude would get

Review only: it reads, never writes to the database, and proposes nothing that changes the plan.
The week is Monday to today. The previous 3 weeks and 6 weeks of lift history are included for trends.
Claude runs through the local `claude` CLI (your Claude Code login), with no tools.
"""
import collections, datetime as dt, json, pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import sb
from garmin_sync import telegram

REPO = pathlib.Path(__file__).resolve().parents[1]
CLAUDE = pathlib.Path.home() / ".local" / "bin" / "claude"
LIMIT = 4000                      # Telegram caps a message at 4096 characters


def get(path):
    s, r = sb.call("GET", "/rest/v1/" + path)
    if s != 200:
        raise RuntimeError(f"Supabase {s} on {path}: {r}")
    return r


def bundle(today):
    week_start = today - dt.timedelta(days=today.weekday())
    since = week_start - dt.timedelta(weeks=3)
    lift_since = today - dt.timedelta(weeks=6)

    acts = get(f"activities?select=day,type,session_key,title,status,duration_min,distance_km,avg_hr,data"
               f"&day=gte.{since}&day=lte.{today}&status=eq.done&order=day")
    activities = []
    for a in acts:
        g = (a.get("data") or {}).get("garmin") or {}
        row = {k: a[k] for k in ("day", "type", "session_key", "title", "duration_min", "distance_km", "avg_hr") if a.get(k) is not None}
        if a.get("distance_km") and a.get("duration_min") and a["type"] == "run":
            pace = a["duration_min"] / a["distance_km"]
            row["pace_per_km"] = f"{int(pace)}:{round(pace % 1 * 60):02d}"
        for k in ("maxHR", "trainingLoad", "aerobicTrainingEffect", "anaerobicTrainingEffect"):
            if g.get(k) is not None:
                row[k] = g[k]
        notes = {k: v for k, v in ((a.get("data") or {}).get("notes") or {}).items() if v}
        if notes:
            row["notes"] = notes
        activities.append(row)

    sets = get(f"sets?select=exercise,set_no,weight_kg,reps,seconds,reps_in_tank,note,"
               f"activities!inner(day,session_key,status)&activities.day=gte.{lift_since}"
               f"&activities.status=eq.done&order=created_at")
    lifts = collections.defaultdict(lambda: collections.defaultdict(list))
    for s in sets:
        if s["weight_kg"] is None and s["reps"] is None and s["seconds"] is None:
            continue                                  # ticked warm-ups with no numbers
        w = "" if s["weight_kg"] is None else f"{float(s['weight_kg']):g}kg"
        x = f"{w}x{s['reps']}" if s["reps"] is not None else f"{w} {s['seconds']}s".strip()
        if s.get("reps_in_tank") is not None:
            x += f" (RIR {s['reps_in_tank']})"
        lifts[s["exercise"]][s["activities"]["day"]].append(x)

    metrics = get(f"daily_metrics?select=day,sleep_score,sleep_min,hrv_ms,hrv_status,resting_hr,body_battery_high,"
                  f"body_battery_low,stress_avg,vo2max,load_7d,load_28d&day=gte.{today - dt.timedelta(days=20)}&order=day")

    profile = json.loads((REPO / "db" / "profile.json").read_text())
    return {
        "today": str(today), "this_week": f"{week_start} (Mon) to {today}",
        "profile_and_targets": profile,
        "activities_last_4_weeks": activities,
        "lift_history_6_weeks": {k: dict(v) for k, v in lifts.items()},
        "daily_metrics_last_3_weeks": [{k: v for k, v in m.items() if v is not None} for m in metrics],
        "data_notes": ["Runs, football and other sports come from Garmin; gym sessions are logged in the app.",
                       "load_7d / load_28d are estimates from activity training load (gym barely counts).",
                       "Lift entries are per set, as logged; per-hand weights are logged per hand."],
    }


PROMPT = """You are my coach doing my weekly progress review. Below is my profile, goals, targets, rules and \
the last few weeks of training data as JSON.

Write it for a Telegram message I read on my phone in under a minute. Plain text, no markdown \
(no asterisks, #, or tables). HARD LIMIT: 700 characters. Fewer is better. Short lines.

The main thing I want is what to try next week. Use exactly this shape:

➡️ Next week
🏃 <the runs: how many, and for each the type, distance and target pace or HR cap>
🏋️ <the weights to try: only lifts that change, e.g. "Row 18kg, calf raise 14kg, OHP test 14kg". \
Say "rest the same" for everything else in a few words.>
🎯 <one focus or fix for the week, e.g. a form cue for a lift that hurt, or "keep easy runs easy">

📊 <ONE line on why: the week's headline, e.g. "Gym moving fast, running light (1 run, 7 km).">

Rules: only what matters for the goal (running first, strength in support). Weight increases follow my \
double-progression rule; never add weight to a lift my notes say is causing pain. Respect my week rules \
(Lower at least 48h before intervals, the long run or football). No per-lift lists, no stats, no caveats \
about missing data. This is advice only.

DATA:
"""


def review(data):
    r = subprocess.run([str(CLAUDE), "-p", "--model", "opus", "--tools", "", "--no-session-persistence",
                        "--output-format", "text"],
                       input=PROMPT + json.dumps(data, default=str, ensure_ascii=False),
                       capture_output=True, text=True, timeout=600, cwd=REPO)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"claude exited {r.returncode}: {(r.stderr or r.stdout)[-500:]}")
    return r.stdout.strip()


def chunks(text):
    out, cur = [], ""
    for para in text.split("\n\n"):
        if cur and len(cur) + len(para) + 2 > LIMIT:
            out.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
        while len(cur) > LIMIT:
            out.append(cur[:LIMIT])
            cur = cur[LIMIT:]
    return out + ([cur] if cur else [])


def main():
    today = dt.date.today()
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        # Freshen Garmin first so Sunday's activities are in.
        subprocess.run([sys.executable, str(REPO / "db" / "garmin_sync.py")], cwd=REPO, timeout=300,
                       capture_output="--dry-run" in sys.argv or "--data" in sys.argv)
        data = bundle(today)
        if "--data" in sys.argv:
            print(json.dumps(data, indent=1, default=str, ensure_ascii=False))
            return
        text = f"🏋️ Weekly review, week of {today - dt.timedelta(days=today.weekday()):%d %b}\n\n" + review(data)
        print(f"{stamp} review:\n{text}\n")
        if "--dry-run" in sys.argv:
            return
        for part in chunks(text):
            telegram(part)
        print(f"{stamp} sent ({len(text)} chars)")
    except Exception as e:
        print(f"{stamp} FAILED: {e}")
        if "--dry-run" not in sys.argv and "--data" not in sys.argv:
            telegram(f"⚠️ The weekly gym review failed: {e}"[:LIMIT] + "\nLog: ~/Library/Logs/gym-weekly-review.log")
        sys.exit(1)


if __name__ == "__main__":
    main()
