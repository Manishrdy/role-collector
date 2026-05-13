-- Job Sourcing Agent — SQLite schema. See design_plan.md §17.
-- All timestamps are ISO-8601 strings in UTC.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS search_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_type TEXT NOT NULL,
  query TEXT,
  time_window TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  error_message TEXT,
  config_json TEXT
);

CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  website_url TEXT,
  careers_url TEXT,
  ats_type TEXT,
  ats_url TEXT,
  funding_source_url TEXT,
  first_seen_at TEXT NOT NULL,
  last_checked_at TEXT,
  last_polled_at TEXT,
  notes TEXT
);

CREATE TABLE IF NOT EXISTS funding_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER,
  company_name TEXT NOT NULL,
  normalized_company_name TEXT,
  round TEXT,
  amount TEXT,
  announced_date TEXT,
  investors TEXT,
  source_url TEXT,
  found_at TEXT NOT NULL,
  raw_snippet TEXT,
  FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS linkedin_posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER,
  post_url TEXT UNIQUE,
  canonical_url TEXT,
  author_name TEXT,
  author_url TEXT,
  company_name TEXT,
  normalized_company_name TEXT,
  detected_role TEXT,
  role_family TEXT,
  role_match_status TEXT,
  level TEXT,
  level_confidence REAL,
  extraction_source TEXT,
  post_text TEXT,
  source_query TEXT,
  confidence REAL,
  found_at TEXT NOT NULL,
  processed_status TEXT DEFAULT 'new',
  FOREIGN KEY(company_id) REFERENCES companies(id)
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER,
  company_name TEXT NOT NULL,
  normalized_company_name TEXT NOT NULL,
  title TEXT NOT NULL,
  normalized_title TEXT NOT NULL,
  location TEXT,
  location_normalized_json TEXT,
  remote_type TEXT,
  canonical_url TEXT,
  apply_url TEXT,
  ats_type TEXT,
  ats_job_id TEXT,
  posted_date TEXT,
  posted_at_source TEXT,
  observed_at TEXT,
  freshness_bucket TEXT,
  posted_date_confidence REAL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  description TEXT,
  description_hash TEXT,
  description_embedding_json TEXT,
  skills_json TEXT,
  role_family TEXT,
  role_match_status TEXT,
  seniority TEXT,
  level TEXT,
  level_confidence REAL,
  employment_type TEXT,
  salary_text TEXT,
  fit_score REAL,
  duplicate_status TEXT DEFAULT 'new',
  duplicate_of_job_id INTEGER,
  duplicate_score REAL,
  source_type TEXT,
  source_query TEXT,
  status TEXT DEFAULT 'discovered',
  extraction_confidence REAL,
  needs_review INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(company_id) REFERENCES companies(id),
  FOREIGN KEY(duplicate_of_job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS agent_cycles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  search_run_id INTEGER,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  sleep_until TEXT,
  summary_json TEXT,
  error_message TEXT,
  config_json TEXT,
  FOREIGN KEY(search_run_id) REFERENCES search_runs(id)
);

CREATE TABLE IF NOT EXISTS job_batches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  search_run_id INTEGER,
  agent_cycle_id INTEGER,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  flushed_at TEXT,
  job_count INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT,
  FOREIGN KEY(search_run_id) REFERENCES search_runs(id),
  FOREIGN KEY(agent_cycle_id) REFERENCES agent_cycles(id)
);

CREATE TABLE IF NOT EXISTS job_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER,
  batch_id INTEGER,
  source_type TEXT NOT NULL,
  source_url TEXT,
  canonical_source_url TEXT,
  source_query TEXT,
  search_run_id INTEGER,
  found_at TEXT NOT NULL,
  raw_snippet TEXT,
  FOREIGN KEY(job_id) REFERENCES jobs(id),
  FOREIGN KEY(batch_id) REFERENCES job_batches(id),
  FOREIGN KEY(search_run_id) REFERENCES search_runs(id)
);

CREATE TABLE IF NOT EXISTS duplicate_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL,
  candidate_job_id INTEGER NOT NULL,
  duplicate_score REAL NOT NULL,
  company_score REAL,
  title_score REAL,
  description_score REAL,
  location_score REAL,
  skills_score REAL,
  decision TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(job_id) REFERENCES jobs(id),
  FOREIGN KEY(candidate_job_id) REFERENCES jobs(id)
);

CREATE TABLE IF NOT EXISTS page_fetches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  canonical_url TEXT,
  domain TEXT,
  fetched_at TEXT NOT NULL,
  status TEXT NOT NULL,
  http_status INTEGER,
  content_hash TEXT,
  detected_page_type TEXT,
  blocked_reason TEXT,
  search_run_id INTEGER,
  FOREIGN KEY(search_run_id) REFERENCES search_runs(id)
);

CREATE TABLE IF NOT EXISTS agent_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  search_run_id INTEGER,
  event_type TEXT NOT NULL,
  event_message TEXT,
  safe_metadata_json TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(search_run_id) REFERENCES search_runs(id)
);

CREATE TABLE IF NOT EXISTS agent_tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_cycle_id INTEGER,
  search_run_id INTEGER,
  tool_name TEXT NOT NULL,
  source_name TEXT,
  status TEXT NOT NULL,
  input_json TEXT,
  output_json TEXT,
  error_message TEXT,
  latency_ms INTEGER,
  created_at TEXT NOT NULL,
  FOREIGN KEY(agent_cycle_id) REFERENCES agent_cycles(id),
  FOREIGN KEY(search_run_id) REFERENCES search_runs(id)
);

CREATE TABLE IF NOT EXISTS agent_source_stats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_name TEXT NOT NULL,
  last_started_at TEXT,
  last_finished_at TEXT,
  last_status TEXT,
  backoff_until TEXT,
  runs_total INTEGER NOT NULL DEFAULT 0,
  successes_total INTEGER NOT NULL DEFAULT 0,
  failures_total INTEGER NOT NULL DEFAULT 0,
  candidates_total INTEGER NOT NULL DEFAULT 0,
  jobs_saved_total INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT
);

CREATE TABLE IF NOT EXISTS agent_memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_key TEXT NOT NULL,
  memory_scope TEXT NOT NULL,
  value_json TEXT NOT NULL,
  confidence REAL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_canonical_url
  ON jobs(canonical_url)
  WHERE canonical_url IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_normalized_company
  ON jobs(normalized_company_name);

CREATE INDEX IF NOT EXISTS idx_jobs_normalized_title
  ON jobs(normalized_title);

CREATE INDEX IF NOT EXISTS idx_jobs_description_hash
  ON jobs(description_hash);

CREATE INDEX IF NOT EXISTS idx_jobs_freshness_bucket
  ON jobs(freshness_bucket);

CREATE INDEX IF NOT EXISTS idx_jobs_ats_job_id
  ON jobs(ats_type, ats_job_id)
  WHERE ats_job_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_job_sources_source_url
  ON job_sources(canonical_source_url);

CREATE INDEX IF NOT EXISTS idx_companies_normalized_name
  ON companies(normalized_name);

CREATE INDEX IF NOT EXISTS idx_funding_company
  ON funding_events(normalized_company_name);

CREATE INDEX IF NOT EXISTS idx_agent_cycles_status
  ON agent_cycles(status);

CREATE INDEX IF NOT EXISTS idx_job_sources_batch
  ON job_sources(batch_id);

CREATE INDEX IF NOT EXISTS idx_agent_source_stats_source
  ON agent_source_stats(source_name);

CREATE INDEX IF NOT EXISTS idx_agent_memory_key_scope
  ON agent_memory(memory_key, memory_scope);
