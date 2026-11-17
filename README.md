# Gym planner

Mobile web app that guides gym sessions one exercise at a time. Data (plan, sessions, sets, other sports) lives in Supabase behind a login; nothing personal is stored in this repo.

- `requirements.txt`: exact Python package versions for the `db/` scripts (`.venv/bin/pip install -r requirements.txt`)
- `index.html` + `app.js`: the app (static, served by GitHub Pages)
- `db/schema.sql`: database tables and row-level security
- `db/import_plan.py`: load `exercises.json` into the exercise catalogue and `plan.json` as a new plan version (`--inactive` to store without switching)
- `db/split_exercises.py`: turn a plan with exercises written out inline into catalogue + a plan that points at it by id
- `tools/extract_plan.mjs`: pull the plan out of the original single-file app
- `db/garmin_key.py`: copy the Garmin personal API key (Garmin's official, read-only API) from 1Password to `~/.garminconnect/personal-api.json`; re-run after making a new key
- `db/garmin_sync.py`: Garmin activities -> `activities`, sleep/HRV/resting HR/Body Battery/VO2max and estimated training load -> `daily_metrics`; `--days N` to backfill, `--dry-run` to preview
- `tools/install_garmin_sync.sh`: run the sync hourly with launchd (`remove` to uninstall); log in `~/Library/Logs/gym-garmin-sync.log`
