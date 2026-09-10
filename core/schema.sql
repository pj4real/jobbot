PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
  id           INTEGER PRIMARY KEY,
  kind         TEXT NOT NULL,              -- gmail | whatsapp | rss
  name         TEXT NOT NULL UNIQUE,
  config_json  TEXT NOT NULL DEFAULT '{}',
  last_cursor  TEXT,
  enabled      INTEGER NOT NULL DEFAULT 1,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS raw_items (
  id           INTEGER PRIMARY KEY,
  source_id    INTEGER NOT NULL REFERENCES sources(id),
  external_id  TEXT NOT NULL,              -- gmail msg id, whatsapp msg id
  subject      TEXT,
  sender       TEXT,
  body         TEXT NOT NULL,
  url          TEXT,
  received_at  TEXT,
  processed    INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS ix_raw_unprocessed ON raw_items(processed, id);

CREATE TABLE IF NOT EXISTS companies (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  norm_name    TEXT NOT NULL UNIQUE,       -- lowercased, punctuation stripped
  domain       TEXT,
  careers_url  TEXT,
  notes        TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jobs (
  id           INTEGER PRIMARY KEY,
  company_id   INTEGER REFERENCES companies(id),
  title        TEXT NOT NULL,
  role_family  TEXT,                       -- sde | ml | data | product | other
  location     TEXT,
  apply_url    TEXT,
  apply_kind   TEXT,                       -- greenhouse|lever|ashby|gform|workday|email|unknown
  apply_email  TEXT,
  deadline     TEXT,
  description  TEXT,
  skills_json  TEXT NOT NULL DEFAULT '[]', -- extracted requirement keywords
  fit_score    REAL,
  fit_reason   TEXT,
  raw_item_id  INTEGER REFERENCES raw_items(id),
  dedupe_hash  TEXT NOT NULL UNIQUE,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_jobs_score ON jobs(fit_score DESC);

CREATE TABLE IF NOT EXISTS contacts (
  id           INTEGER PRIMARY KEY,
  company_id   INTEGER REFERENCES companies(id),
  name         TEXT,
  email        TEXT NOT NULL,
  title        TEXT,
  source       TEXT NOT NULL,              -- posting | careers_page | manual
  verified     INTEGER NOT NULL DEFAULT 0, -- never email an unverified address
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (company_id, email)
);

-- one row per tailored resume actually rendered, so you can always
-- pull up exactly what you sent to a given company
CREATE TABLE IF NOT EXISTS resumes (
  id             INTEGER PRIMARY KEY,
  job_id         INTEGER NOT NULL REFERENCES jobs(id),
  path           TEXT NOT NULL,
  bullet_ids     TEXT NOT NULL,            -- json list, audit trail
  headline       TEXT,
  skills_shown   TEXT,
  coverage       REAL,                     -- fraction of jd keywords hit
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS drafts (
  id           INTEGER PRIMARY KEY,
  job_id       INTEGER NOT NULL REFERENCES jobs(id),
  contact_id   INTEGER REFERENCES contacts(id),
  subject      TEXT NOT NULL,
  body         TEXT NOT NULL,
  evidence_ids TEXT NOT NULL DEFAULT '[]',
  edited       INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS applications (
  id            INTEGER PRIMARY KEY,
  job_id        INTEGER NOT NULL REFERENCES jobs(id),
  draft_id      INTEGER REFERENCES drafts(id),
  resume_id     INTEGER REFERENCES resumes(id),
  channel       TEXT NOT NULL,             -- form | email
  status        TEXT NOT NULL DEFAULT 'needs_review',
  screenshot    TEXT,
  thread_id     TEXT,                      -- gmail thread, for reply detection
  needs_human   TEXT,                      -- json list of unfilled fields
  submitted_at  TEXT,
  response_at   TEXT,
  outcome       TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (job_id, channel)
);
CREATE INDEX IF NOT EXISTS ix_app_status ON applications(status, created_at);

CREATE TABLE IF NOT EXISTS form_maps (
  id           INTEGER PRIMARY KEY,
  domain       TEXT NOT NULL,
  fingerprint  TEXT NOT NULL,
  mapping_json TEXT NOT NULL,
  hits         INTEGER NOT NULL DEFAULT 0,
  last_used    TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (domain, fingerprint)
);

CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY,
  entity       TEXT NOT NULL,
  entity_id    INTEGER,
  kind         TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_events_at ON events(at DESC);

-- llm response cache, keyed by hash of (task, input). keeps plan usage down.
CREATE TABLE IF NOT EXISTS llm_cache (
  key          TEXT PRIMARY KEY,
  task         TEXT NOT NULL,
  response     TEXT NOT NULL,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- daily send counter, enforced by the mailer before every send
CREATE TABLE IF NOT EXISTS send_log (
  id           INTEGER PRIMARY KEY,
  day          TEXT NOT NULL,
  to_email     TEXT NOT NULL,
  application_id INTEGER REFERENCES applications(id),
  at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_send_day ON send_log(day);

-- ---------------------------------------------------------------- v2 --------

-- Sender memory. Scanning a whole inbox only stays cheap because a sender you
-- have already judged never costs anything again. One "not a job" on a
-- newsletter suppresses every future mail from it, for free, forever.
CREATE TABLE IF NOT EXISTS senders (
  id           INTEGER PRIMARY KEY,
  address      TEXT NOT NULL UNIQUE,      -- lowercased, envelope address only
  domain       TEXT,
  verdict      TEXT NOT NULL,             -- job | not_job | unsure
  reason       TEXT,                      -- who decided and why
  seen         INTEGER NOT NULL DEFAULT 1,
  jobs_found   INTEGER NOT NULL DEFAULT 0,
  decided_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_senders_verdict ON senders(verdict);

-- What happened to an application, in order. The applications table holds the
-- current state; this holds how it got there, which is what you actually want
-- to look at three weeks later.
CREATE TABLE IF NOT EXISTS timeline (
  id             INTEGER PRIMARY KEY,
  application_id INTEGER REFERENCES applications(id),
  job_id         INTEGER REFERENCES jobs(id),
  kind           TEXT NOT NULL,           -- found | applied | mailed | replied | ...
  detail         TEXT,
  at             TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_timeline_job ON timeline(job_id, at);

-- Your own triage on a found job, before any application exists.
ALTER TABLE jobs ADD COLUMN triage TEXT;          -- NULL | shortlisted | skipped
ALTER TABLE jobs ADD COLUMN triage_at TEXT;
ALTER TABLE jobs ADD COLUMN sender_id INTEGER REFERENCES senders(id);
ALTER TABLE senders ADD COLUMN decided_by TEXT NOT NULL DEFAULT 'system';
