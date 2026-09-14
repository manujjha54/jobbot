"""
matching.py
Scores the SHARED job pool against ONE user's profile. This is the part
that runs per-user (cheap - pure Python scoring, no external API calls),
as opposed to job_pool.py's refresh which is shared/expensive and runs
independently of how many users exist.
"""

import json
from db import db_session, json_loads_safe
from match_engine import filter_and_rank
import profile_builder


def _profile_row_to_dict(profile_row):
    """Converts a DB profile row into the dict shape match_engine/ats_score expect."""
    return profile_builder.build_profile(
        resume_filename=profile_row["resume_filename"] or "",
        name="", email="", phone=profile_row["phone"] or "",
        linkedin=profile_row["linkedin"] or "", location=profile_row["location"] or "",
        target_titles=json_loads_safe(profile_row["target_titles"]),
        must_have_skills=json_loads_safe(profile_row["must_have_skills"]),
        nice_to_have_skills=json_loads_safe(profile_row["nice_to_have_skills"]),
        years_experience=profile_row["years_experience"],
        remote_first=bool(profile_row["remote_first"]),
        acceptable_locations=json_loads_safe(profile_row["acceptable_locations"]),
    )


def get_user_profile_dict(user_id):
    with db_session() as conn:
        row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return None
    return _profile_row_to_dict(row)


def match_new_jobs_for_user(user_id, threshold=0.55):
    """
    Finds job_postings this user hasn't seen yet (no existing decision row),
    scores them against their profile, and creates decision rows (decision
    defaulting to 'skip') for anything that clears the threshold. Returns
    how many postings were considered and how many matched.
    """
    with db_session() as conn:
        profile_row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        if not profile_row or not profile_row["setup_complete"]:
            return {"considered": 0, "matched": 0, "error": "Profile setup not complete."}

        profile = _profile_row_to_dict(profile_row)

        unseen = conn.execute(
            """SELECT jp.* FROM job_postings jp
               WHERE jp.id NOT IN (
                   SELECT job_posting_id FROM user_job_decisions WHERE user_id = ?
               )""",
            (user_id,),
        ).fetchall()

        jobs = [{
            "id": r["id"], "source": r["source"], "title": r["title"], "company": r["company"],
            "location": r["location"], "remote": bool(r["remote"]), "url": r["url"],
            "description": r["description"], "posted_date": r["posted_date"],
            "apply_email": r["apply_email"],
        } for r in unseen]

        matched = filter_and_rank(jobs, profile, threshold=threshold)

        for job in matched:
            conn.execute(
                """INSERT OR IGNORE INTO user_job_decisions
                   (user_id, job_posting_id, decision, match_score, match_reasons)
                   VALUES (?, ?, 'skip', ?, ?)""",
                (user_id, job["id"], job["match_score"], json.dumps(job.get("match_reasons", []))),
            )
        # Also record "considered but not matched" as nothing - we don't
        # want to re-score them every time, so mark them seen with a
        # rejected-by-score sentinel decision that never shows in the UI.
        matched_ids = {j["id"] for j in matched}
        for job in jobs:
            if job["id"] not in matched_ids:
                conn.execute(
                    """INSERT OR IGNORE INTO user_job_decisions
                       (user_id, job_posting_id, decision, match_score)
                       VALUES (?, ?, 'below_threshold', 0)""",
                    (user_id, job["id"]),
                )

    return {"considered": len(jobs), "matched": len(matched)}


def get_active_jobs_for_user(user_id):
    """Jobs still needing a decision or awaiting apply (excludes below_threshold, applied)."""
    with db_session() as conn:
        rows = conn.execute(
            """SELECT ujd.*, jp.title, jp.company, jp.location, jp.remote, jp.url,
                      jp.description, jp.source, jp.apply_email
               FROM user_job_decisions ujd
               JOIN job_postings jp ON jp.id = ujd.job_posting_id
               WHERE ujd.user_id = ? AND ujd.applied = 0 AND ujd.decision != 'below_threshold'
               ORDER BY ujd.match_score DESC""",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]
