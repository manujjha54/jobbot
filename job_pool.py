"""
job_pool.py
Maintains ONE shared pool of job postings that every user matches against.
This is the actual fix for the "many users, one Adzuna quota" problem: the
number of external API calls depends only on how often the pool is
refreshed (admin-triggered or on a timer), never on how many users are
active or how often they click "Search".
"""

import os
import json
from datetime import datetime

from db import db_session
from search_jobs import search_all

# A generic profile just wide enough to pull a broad, varied pool of
# postings. Individual relevance is decided later, per-user, in matching.py -
# this only needs to cast a reasonably wide net.
def _aggregate_target_titles(conn):
    rows = conn.execute("SELECT target_titles FROM profiles WHERE target_titles != '[]'").fetchall()
    titles = set()
    for row in rows:
        try:
            for t in json.loads(row["target_titles"]):
                titles.add(t)
        except (json.JSONDecodeError, TypeError):
            continue
    return list(titles) if titles else ["Customer Success Manager"]  # sane fallback if no users yet


def _server_config():
    return {
        "adzuna": {
            "app_id": os.environ.get("ADZUNA_APP_ID", ""),
            "app_key": os.environ.get("ADZUNA_APP_KEY", ""),
            "countries": os.environ.get("ADZUNA_COUNTRIES", "in,gb,us").split(","),
            "results_per_query": 20,
        },
        "remoteok": {"enabled": True},
    }


def refresh_job_pool():
    """
    Pulls fresh postings from every configured source and upserts them into
    the shared job_postings table. Safe to call repeatedly - postings are
    deduped by their source id, so re-running just refreshes fetched_at for
    ones that are still live and adds ones that are new.
    """
    started_at = datetime.utcnow().isoformat()
    with db_session() as conn:
        target_titles = _aggregate_target_titles(conn)

    profile_stub = {"target_titles": target_titles}
    config = _server_config()

    try:
        raw_jobs = search_all(profile_stub, config)
    except Exception as e:
        with db_session() as conn:
            conn.execute(
                "INSERT INTO job_pool_refresh_log (started_at, finished_at, raw_count, new_count, error) VALUES (?, ?, ?, ?, ?)",
                (started_at, datetime.utcnow().isoformat(), 0, 0, str(e)),
            )
        raise

    new_count = 0
    with db_session() as conn:
        for job in raw_jobs:
            existing = conn.execute("SELECT id FROM job_postings WHERE id = ?", (job["id"],)).fetchone()
            if existing:
                conn.execute("UPDATE job_postings SET fetched_at = ? WHERE id = ?",
                             (datetime.utcnow().isoformat(), job["id"]))
                continue
            conn.execute(
                """INSERT INTO job_postings
                   (id, source, title, company, location, remote, url, description, posted_date, apply_email)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (job["id"], job["source"], job["title"], job["company"], job["location"],
                 1 if job.get("remote") else 0, job.get("url"), job.get("description"),
                 job.get("posted_date"), job.get("apply_email")),
            )
            new_count += 1

        conn.execute(
            "INSERT INTO job_pool_refresh_log (started_at, finished_at, raw_count, new_count, error) VALUES (?, ?, ?, ?, ?)",
            (started_at, datetime.utcnow().isoformat(), len(raw_jobs), new_count, None),
        )

    return {"raw_count": len(raw_jobs), "new_count": new_count}


def pool_size():
    with db_session() as conn:
        row = conn.execute("SELECT COUNT(*) as n FROM job_postings").fetchone()
        return row["n"]


def last_refresh_info():
    with db_session() as conn:
        row = conn.execute(
            "SELECT * FROM job_pool_refresh_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
