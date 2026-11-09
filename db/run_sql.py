"""Run a .sql file against the Supabase database using ~/gym-planner/.env. Usage: .venv/bin/python db/run_sql.py db/schema.sql"""
import pathlib, re, sys
import psycopg
HERE = pathlib.Path(__file__).resolve().parents[1]
env = {}
for l in (HERE / ".env").read_text().splitlines():
    m = re.match(r"^\s*([A-Z_]+)\s*=\s*(.*)$", l.strip())
    if m:
        env[m[1]] = m[2].strip().strip('"').strip("'")
ref = re.match(r"https://([a-z0-9]+)\.supabase\.co", env["SUPABASE_URL"])[1]
with psycopg.connect(host="aws-0-eu-west-2.pooler.supabase.com", port=5432, user=f"postgres.{ref}",
                     password=env["SUPABASE_DB_PASSWORD"], dbname="postgres", connect_timeout=10) as c:
    c.execute(pathlib.Path(sys.argv[1]).read_text())
    rows = c.execute("""select c.relname, c.relrowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace
                        where n.nspname='public' and c.relkind='r' order by 1""").fetchall()
    for name, rls in rows:
        print(f"{name:12} row security: {'on' if rls else 'OFF'}")
