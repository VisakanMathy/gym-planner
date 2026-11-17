-- Gym planner database (Supabase / Postgres). Single user; every row belongs to auth.uid().
-- The app (publishable key + login) can only see its own rows; the agent uses the secret key.
-- Safe to re-run.

create extension if not exists pgcrypto;

-- Who I am, what I'm training for, my sports, injuries, equipment, preferences and week rules.
create table if not exists profile (
  user_id     uuid primary key default auth.uid() references auth.users on delete cascade,
  data        jsonb not null default '{}'::jsonb,
  updated_at  timestamptz not null default now()
);

-- The training plan (the old `const PLAN`), versioned. The agent proposes a new version; one is active.
create table if not exists plans (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid() references auth.users on delete cascade,
  version     text not null,
  plan        jsonb not null,
  note        text,
  created_by  text not null default 'me' check (created_by in ('me', 'agent', 'import')),
  active      boolean not null default false,
  created_at  timestamptz not null default now()
);
create unique index if not exists plans_one_active on plans (user_id) where active;

-- Everything I do: gym sessions from the app, runs from Garmin, football/swim/climb typed in.
create table if not exists activities (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null default auth.uid() references auth.users on delete cascade,
  day          date not null,
  started_at   timestamptz,
  type         text not null check (type in ('gym', 'run', 'cycle', 'swim', 'football', 'climb', 'tennis',
                                             'snowboard', 'surf', 'walk', 'mobility', 'other')),
  session_key  text,                     -- gym day key: morning, lower, upperH, upperV, full
  title        text,
  status       text not null default 'done' check (status in ('in_progress', 'done')),
  duration_min numeric,
  distance_km  numeric,
  avg_hr       integer,
  effort       integer check (effort between 1 and 10),   -- how hard it felt (RPE)
  notes        text,
  source       text not null default 'app' check (source in ('app', 'garmin', 'manual', 'agent')),
  external_id  text,                     -- e.g. Garmin activity id, so syncs never duplicate
  data         jsonb not null default '{}'::jsonb,         -- extras: splits, training load, HR zones…
  plan_id      uuid references plans on delete set null,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create unique index if not exists activities_external on activities (user_id, source, external_id) where external_id is not null;
create index if not exists activities_day on activities (user_id, day desc);

-- The exercise catalogue: one row per exercise, with a stable id (a slug of the name, e.g. 'goblet-squat').
-- Holds what an exercise IS (name, photos, video, default cues, muscle groups). How it's done on a given
-- day (sets x reps, stage, progression, start weight, and any day-specific cues/target) stays in the plan slot,
-- which points here by id. Renaming: change `name` and add the old one to `aliases`, so history still matches.
create table if not exists exercises (
  user_id     uuid not null default auth.uid() references auth.users on delete cascade,
  id          text not null,
  name        text not null,
  target      text,
  groups      text[] not null default '{}',   -- chest, back, shoulders, arms, core, glutes, legs, hamstrings, calves, hips, mobility
  imgs        text[],
  caps        text[],
  cues        text[],
  vid         text,
  yt          text,
  svgs        text[],
  aliases     text[] not null default '{}',
  source      text not null default 'plan' check (source in ('plan', 'me', 'agent')),
  created_at  timestamptz not null default now(),
  primary key (user_id, id)
);

-- Every set of every gym exercise.
create table if not exists sets (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null default auth.uid() references auth.users on delete cascade,
  activity_id   uuid not null references activities on delete cascade,
  exercise      text not null,           -- exercise name as shown at the time (swaps log their own name)
  exercise_id   text,                    -- the catalogue row; filled in by sets_fill_exercise if left out
  slot          text,                    -- the plan slot it filled, e.g. "lower:goblet-squat"
  set_no        integer not null,
  weight_kg     numeric,
  reps          integer,
  reps_in_tank  integer,                 -- RIR
  seconds       integer,                 -- holds
  done          boolean not null default true,
  note          text,
  created_at    timestamptz not null default now()
);
create index if not exists sets_activity on sets (activity_id);
create index if not exists sets_exercise on sets (user_id, exercise, created_at desc);
alter table sets add column if not exists exercise_id text;     -- for databases made before the catalogue
create index if not exists sets_exercise_id on sets (user_id, exercise_id);
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'sets_exercise_fk') then
    alter table sets add constraint sets_exercise_fk foreign key (user_id, exercise_id)
      references exercises (user_id, id) on update cascade;
  end if;
end $$;

-- What's planned on which day (gym days and other sports).
create table if not exists schedule (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null default auth.uid() references auth.users on delete cascade,
  day          date not null,
  type         text not null,
  session_key  text,
  title        text not null,
  details      text,
  status       text not null default 'planned' check (status in ('planned', 'done', 'skipped', 'moved')),
  activity_id  uuid references activities on delete set null,
  source       text not null default 'me' check (source in ('me', 'agent')),
  proposal_id  uuid,
  created_at   timestamptz not null default now()
);
create index if not exists schedule_day on schedule (user_id, day);

-- The agent's weekly reviews and suggested changes; nothing applies until I accept.
create table if not exists proposals (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null default auth.uid() references auth.users on delete cascade,
  week_start   date not null,
  summary      text not null,            -- the review, in plain words
  schedule     jsonb not null default '[]'::jsonb,   -- proposed schedule rows
  plan_changes jsonb not null default '[]'::jsonb,   -- proposed plan edits, explained
  new_plan_id  uuid references plans on delete set null,
  status       text not null default 'pending' check (status in ('pending', 'accepted', 'rejected', 'superseded')),
  feedback     text,
  created_at   timestamptz not null default now(),
  decided_at   timestamptz
);

-- Conversation with the agent (step 4), kept so it remembers what we discussed.
create table if not exists messages (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid() references auth.users on delete cascade,
  role        text not null check (role in ('me', 'agent')),
  text        text not null,
  channel     text not null default 'telegram',
  created_at  timestamptz not null default now()
);

-- One row per day from Garmin: sleep, HRV, resting HR, Body Battery, readiness, training load, VO2max.
-- The columns are what the weekly review reads; `data` keeps the rest of Garmin's daily summary.
create table if not exists daily_metrics (
  user_id            uuid not null default auth.uid() references auth.users on delete cascade,
  day                date not null,
  sleep_score        integer,
  sleep_min          integer,       -- total asleep
  deep_min           integer,
  rem_min            integer,
  hrv_ms             integer,       -- last night's average
  hrv_status         text,          -- BALANCED, UNBALANCED, LOW, POOR
  resting_hr         integer,
  body_battery_high  integer,
  body_battery_low   integer,
  readiness          integer,       -- training readiness 0-100
  readiness_level    text,
  training_status    text,          -- PRODUCTIVE, MAINTAINING, RECOVERY, UNPRODUCTIVE, …
  acute_load         integer,       -- Garmin's own (readiness, status, acute/chronic: history up to 2026-11-15,
  chronic_load       integer,       --   from the unofficial API; Garmin's official personal API doesn't offer them)
  load_7d            integer,       -- training load of the last 7 days (sum of activity loads): ~ Garmin's acute load
  load_28d           integer,       -- last 28 days' load / 4, a weekly average: ~ Garmin's chronic load
  vo2max             numeric,
  steps              integer,
  stress_avg         integer,
  data               jsonb not null default '{}'::jsonb,
  source             text not null default 'garmin',
  updated_at         timestamptz not null default now(),
  primary key (user_id, day)
);

alter table daily_metrics add column if not exists load_7d integer;     -- for databases made before these
alter table daily_metrics add column if not exists load_28d integer;

-- Row level security: each table only shows the signed-in user's own rows.
do $$
declare t text;
begin
  foreach t in array array['profile', 'plans', 'exercises', 'activities', 'daily_metrics', 'sets', 'schedule', 'proposals', 'messages'] loop
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists own_rows on %I', t);
    execute format('create policy own_rows on %I for all to authenticated using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()))', t);
  end loop;
end $$;

-- keep updated_at current
create or replace function touch_updated_at() returns trigger language plpgsql as $$
begin new.updated_at := now(); return new; end $$;
drop trigger if exists activities_touch on activities;
create trigger activities_touch before update on activities for each row execute function touch_updated_at();
drop trigger if exists profile_touch on profile;
create trigger profile_touch before update on profile for each row execute function touch_updated_at();
drop trigger if exists daily_metrics_touch on daily_metrics;
create trigger daily_metrics_touch before update on daily_metrics for each row execute function touch_updated_at();

-- Every set points at a catalogue exercise. If the writer didn't say which (an old app, the agent, a typed-in
-- name), match the name on id-slug, name or alias; failing that, add it to the catalogue as my own exercise.
create or replace function slugify(t text) returns text language sql immutable as $$
  select trim(both '-' from regexp_replace(lower(t), '[^a-z0-9]+', '-', 'g')) $$;
create or replace function sets_fill_exercise() returns trigger language plpgsql as $$
declare s text := slugify(new.exercise);
begin
  if new.exercise_id is null then
    select e.id into new.exercise_id from exercises e
     where e.user_id = new.user_id
       and (e.id = s or lower(e.name) = lower(new.exercise)
            or lower(new.exercise) in (select lower(a) from unnest(e.aliases) a))
     order by (e.id = s) desc, (lower(e.name) = lower(new.exercise)) desc limit 1;
    new.exercise_id := coalesce(new.exercise_id, nullif(s, ''), 'exercise-' || left(md5(new.exercise), 8));
  end if;
  insert into exercises (user_id, id, name, source) values (new.user_id, new.exercise_id, new.exercise, 'me')
  on conflict do nothing;
  return new;
end $$;
drop trigger if exists sets_fill_exercise on sets;
create trigger sets_fill_exercise before insert or update of exercise, exercise_id on sets
  for each row execute function sets_fill_exercise();

-- The most recent finished session of every exercise, all its sets (the app's "last time" hints).
-- security_invoker: the view runs under the signed-in user's row security, not the owner's.
drop view if exists last_sets;
create view last_sets with (security_invoker = true) as
select s.exercise_id, s.exercise, s.set_no, s.weight_kg, s.reps, s.seconds, a.day
from sets s
join activities a on a.id = s.activity_id and a.status = 'done'
join (select distinct on (s2.user_id, s2.exercise_id) s2.user_id, s2.exercise_id, a2.day
      from sets s2 join activities a2 on a2.id = s2.activity_id and a2.status = 'done'
      order by s2.user_id, s2.exercise_id, a2.day desc) latest
  on latest.user_id = s.user_id and latest.exercise_id = s.exercise_id and latest.day = a.day;

-- tell the API about new tables/views straight away
notify pgrst, 'reload schema';
