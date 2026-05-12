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
  post_url TEXT UNIQUE,
  canonical_url TEXT,
  author_name TEXT,
  author_url TEXT,
  company_name TEXT,
  normalized_company_name TEXT,
  detected_role TEXT,
  post_text TEXT,
  source_query TEXT,
  confidence REAL,
  found_at TEXT NOT NULL,
  processed_status TEXT DEFAULT 'new'
);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER,
  company_name TEXT NOT NULL,
  normalized_company_name TEXT NOT NULL,
  title TEXT NOT NULL,
  normalized_title TEXT NOT NULL,
  location TEXT,
  remote_type TEXT,
  canonical_url TEXT,
  apply_url TEXT,
  ats_type TEXT,
  ats_job_id TEXT,
  posted_date TEXT,
  posted_date_confidence REAL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  description TEXT,
  description_hash TEXT,
  description_embedding_json TEXT,
  skills_json TEXT,
  seniority TEXT,
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

CREATE TABLE IF NOT EXISTS job_sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER,
  source_type TEXT NOT NULL,
  source_url TEXT,
  canonical_source_url TEXT,
  source_query TEXT,
  search_run_id INTEGER,
  found_at TEXT NOT NULL,
  raw_snippet TEXT,
  FOREIGN KEY(job_id) REFERENCES jobs(id),
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_canonical_url
  ON jobs(canonical_url)
  WHERE canonical_url IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_normalized_company
  ON jobs(normalized_company_name);

CREATE INDEX IF NOT EXISTS idx_jobs_normalized_title
  ON jobs(normalized_title);

CREATE INDEX IF NOT EXISTS idx_jobs_description_hash
  ON jobs(description_hash);

CREATE INDEX IF NOT EXISTS idx_jobs_ats_job_id
  ON jobs(ats_type, ats_job_id)
  WHERE ats_job_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_job_sources_source_url
  ON job_sources(canonical_source_url);

CREATE INDEX IF NOT EXISTS idx_companies_normalized_name
  ON companies(normalized_name);

CREATE INDEX IF NOT EXISTS idx_funding_company
  ON funding_events(normalized_company_name);
