"""
matching.py
Evaluates all job postings in the database pool against the user's
parsed resume, selected locations, and target titles.
"""

import re
import json
from collections import Counter
from db import db_session, json_loads_safe
import resume_tailor


def _profile_row_to_dict(row):
    """Converts a profiles database row into a structured dictionary."""
    if not row:
        return {}
    d = dict(row)
    d["target_titles"] = json_loads_safe(d.get("target_titles")) or []
    d["must_have_skills"] = json_loads_safe(d.get("must_have_skills")) or []
    d["nice_to_have_skills"] = json_loads_safe(d.get("nice_to_have_skills")) or []
    d["acceptable_locations"] = json_loads_safe(d.get("acceptable_locations")) or []
    return d


def location_matches(job_location: str, user_locations: list, remote_allowed: bool) -> bool:
    """Checks whether a job listing matches location filters or allows all."""
    if not job_location or not user_locations:
        return True
    loc_lower = job_location.lower()
    
    if remote_allowed and any(r in loc_lower for r in ["remote", "hybrid", "anywhere", "wfh", "telecommute"]):
        return True
        
    user_locs_clean = [str(l).strip().lower() for l in user_locations if str(l).strip()]
    if not user_locs_clean or "india" in user_locs_clean or "all" in user_locs_clean:
        return True
        
    return any(loc in loc_lower for loc in user_locs_clean)


def title_matches(job_title: str, target_titles: list) -> bool:
    """Broad title matching heuristic."""
    if not target_titles:
        return True
    job_lower = (job_title or "").lower()
    for target in target_titles:
        target_clean = str(target).strip().lower()
        if not target_clean:
            continue
        if target_clean in job_lower:
            return True
        tokens = [w for w in re.findall(r"\b[a-z]{3,}\b", target_clean) if w not in ("and", "the", "for", "with", "lead", "senior", "manager")]
        if any(token in job_lower for token in tokens):
            return True
    return False


def calculate_ats_score(resume_text: str, job_title: str, job_desc: str, candidate_skills: list = None) -> int:
    """
    Computes a realistic dynamic ATS match percentage (35% - 95%).
    """
    if not resume_text:
        return 65

    resume_text_lower = resume_text.lower()
    job_full_text = f"{job_title or ''} {job_desc or ''}".lower()

    # 1. Title Similarity (35 points)
    title_words = [w for w in re.findall(r"\b[a-z]{3,}\b", (job_title or "").lower()) if w not in ("and", "the", "for", "with", "inc", "ltd")]
    if title_words:
        matched_title = sum(1 for w in title_words if w in resume_text_lower)
        title_score = (matched_title / len(title_words)) * 35.0
    else:
        title_score = 20.0

    # 2. Skill Overlap (45 points)
    if candidate_skills and len(candidate_skills) > 0:
        matched_skills = sum(1 for s in candidate_skills if str(s).lower() in job_full_text)
        skill_score = min(1.0, matched_skills / min(len(candidate_skills), 10)) * 45.0
    else:
        key_terms = set(re.findall(r"\b[a-z]{4,}\b", job_full_text)) - {"with", "that", "this", "from", "have", "will", "your", "about", "team", "work"}
        if key_terms:
            matched_terms = sum(1 for t in key_terms if t in resume_text_lower)
            skill_score = min(1.0, matched_terms / min(len(key_terms), 15)) * 45.0
        else:
            skill_score = 25.0

    # 3. Term Density (20 points)
    words = re.findall(r"\b[a-z]{4,}\b", job_full_text)
    freq = Counter(words)
    top_kw = [w for w, _ in freq.most_common(15) if w not in ("with", "that", "this", "from", "have", "will", "your", "about", "team", "work")]
    if top_kw:
        matched_kw = sum(1 for w in top_kw if w in resume_text_lower)
        density_score = (matched_kw / len(top_kw)) * 20.0
    else:
        density_score = 10.0

    total_score = int(round(title_score + skill_score + density_score))
    return max(35, min(total_score, 95))


def match_new_jobs_for_user(user_id: int) -> dict:
    """Evaluates all pool listings and updates user match decisions."""
    with db_session() as conn:
        profile = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        if not profile:
            return {"error": "No profile found.", "considered": 0, "matched": 0}

        resume_blob = profile["resume_blob"]
        resume_text = ""
        if resume_blob:
            try:
                resume_text = resume_tailor.extract_resume_text(resume_blob)
            except Exception:
                resume_text = ""

        skills = json_loads_safe(profile["must_have_skills"]) or []
        target_titles = json_loads_safe(profile["target_titles"]) or []
        user_locations = json_loads_safe(profile["acceptable_locations"]) or []
        remote_allowed = bool(profile["remote_first"])

        postings = conn.execute("SELECT * FROM job_postings").fetchall()
        if not postings:
            return {"considered": 0, "matched": 0}

        matched_count = 0
        for job in postings:
            # Check location filter (defaults to True if India/All is in locations)
            if not location_matches(job["location"], user_locations, remote_allowed):
                continue

            # Calculate individual dynamic ATS score
            score = calculate_ats_score(resume_text, job["title"], job["description"], skills)

            # Insert or update decision entry
            conn.execute(
                """INSERT INTO user_job_decisions 
                   (user_id, job_posting_id, base_ats_score, ats_score, decision)
                   VALUES (?, ?, ?, ?, 'undecided')
                   ON CONFLICT(user_id, job_posting_id) DO UPDATE SET
                   base_ats_score = ?, ats_score = COALESCE(ats_score, ?)""",
                (user_id, job["id"], score, score, score, score)
            )
            matched_count += 1

    return {"considered": len(postings), "matched": matched_count}


def get_active_jobs_for_user(user_id: int):
    """Returns all matched jobs for the candidate sorted by highest ATS score."""
    with db_session() as conn:
        rows = conn.execute(
            """SELECT jp.id, jp.title, jp.company, jp.location, jp.url, jp.description,
                      ujd.id as decision_id, ujd.base_ats_score, ujd.ats_score, ujd.was_tailored, ujd.decision
               FROM job_postings jp
               JOIN user_job_decisions ujd ON jp.id = ujd.job_posting_id
               WHERE ujd.user_id = ?
               ORDER BY COALESCE(ujd.ats_score, ujd.base_ats_score, 50) DESC
               LIMIT 100""",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]