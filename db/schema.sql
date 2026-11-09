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

-- Every set of every gym exercise.
create table if not exists sets (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null default auth.uid() references auth.users on delete cascade,
  activity_id   uuid not null references activities on delete cascade,
  exercise      text not null,           -- exercise name as shown (swaps log their own name)
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

-- Row level security: each table only shows the signed-in user's own rows.
do $$
declare t text;
begin
  foreach t in array array['profile', 'plans', 'activities', 'sets', 'schedule', 'proposals', 'messages'] loop
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
