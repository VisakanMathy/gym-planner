# Gym planner

Mobile web app that guides gym sessions one exercise at a time. Data (plan, sessions, sets, other sports) lives in Supabase behind a login; nothing personal is stored in this repo.

- `index.html` + `app.js`: the app (static, served by GitHub Pages)
- `db/schema.sql`: database tables and row-level security
- `db/import_plan.py`: load a new plan version
- `tools/extract_plan.mjs`: pull the plan out of the original single-file app
