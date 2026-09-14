"""
db.py
Database layer using Python's built-in sqlite3 - no extra dependency to
install, works identically in local dev and on most hosting platforms.

For real production scale (many concurrent users), SQLite's single-writer
model becomes a bottleneck and migrating to Postgres is recommended - the
SQL here is intentionally plain/portable to make that migration easier
later. For a friends-and-testing scale, SQLite is genuinely fine.

All resume/tailored-resume file content is stored as BLOBs directly in the
database (not on disk), so this works on hosting platforms with ephemeral
filesystems (most free tiers) without needing a persistent disk - as long
as the *database itself* is persisted (see README for why DATABASE_PATH
should point at a mounted volume, or why Postgres is recommended for real
deployment).
"""

import sqlite3
import os
import json
from contextlib import contextmanager

DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobbot.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT DEFAULT '',
    is_admin INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id),
    resume_filename TEXT,
    resume_blob BLOB,
    phone TEXT DEFAULT '',
    linkedin TEXT DEFAULT '',
    location TEXT DEFAULT '',
    years_experience INTEGER DEFAULT 0,
    target_titles TEXT DEFAULT '[]',        -- JSON list
    must_have_skills TEXT DEFAULT '[]',      -- JSON list
    nice_to_have_skills TEXT DEFAULT '[]',   -- JSON list
    acceptable_locations TEXT DEFAULT '[]',  -- JSON list
    remote_first INTEGER DEFAULT 0,
    ats_min_score INTEGER DEFAULT 90,
    gmail_email TEXT DEFAULT '',
    gmail_app_password_encrypted TEXT DEFAULT '',
    send_emails_enabled INTEGER DEFAULT 0,
    setup_complete INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS job_postings (
    id TEXT PRIMARY KEY,          -- e.g. "adzuna_12345"
    source TEXT,
    title TEXT,
    company TEXT,
    location TEXT,
    remote INTEGER,
    url TEXT,
    description TEXT,
    posted_date TEXT,
    apply_email TEXT,
    fetched_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_job_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    job_posting_id TEXT REFERENCES job_postings(id),
    decision TEXT DEFAULT 'skip',   -- skip / approve / reject
    match_score REAL,
    match_reasons TEXT DEFAULT '[]',
    applied INTEGER DEFAULT 0,
    ats_score REAL,
    base_ats_score REAL,
    added_keywords TEXT DEFAULT '[]',   -- JSON list of keywords actually added
    was_tailored INTEGER DEFAULT 0,
    tailored_resume_blob BLOB,
    tailored_resume_filename TEXT,
    cover_letter TEXT,
    apply_method TEXT,
    status TEXT,
    applied_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, job_posting_id)
);

CREATE TABLE IF NOT EXISTS pending_resumes (
    token TEXT PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    filename TEXT,
    blob BLOB,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_pool_refresh_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    raw_count INTEGER,
    new_count INTEGER,
    error TEXT
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    conn.close()


def _migrate(conn):
    """Adds columns introduced after initial release, for DBs that already
    existed before this code shipped. Safe to run every startup."""
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(user_job_decisions)")}
    if "base_ats_score" not in existing_cols:
        conn.execute("ALTER TABLE user_job_decisions ADD COLUMN base_ats_score REAL")
    if "added_keywords" not in existing_cols:
        conn.execute("ALTER TABLE user_job_decisions ADD COLUMN added_keywords TEXT DEFAULT '[]'")
    conn.commit()


@contextmanager
def db_session():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row):
    return dict(row) if row else None


def json_loads_safe(s, default=None):
    if not s:
        return default if default is not None else []
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []
