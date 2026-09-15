"""
matching.py
Evaluates job postings in the central database pool against the user's
parsed resume, selected locations, and target titles using real dynamic ATS
text similarity algorithms.
"""

import re
import json
from collections import Counter
from db import db_session, json_loads_safe
import resume_tailor


def _profile_row_to_dict(row):
    """Converts a profiles database row into a structured dictionary for applying/matching."""
    if not row:
        return {}
    d = dict(row)
    d["target_titles"] = json_loads_safe(d.get("target_titles")) or []
    d["must_have_skills"] = json_loads_safe(d.get("must_have_skills")) or []
    d["nice_to_have_skills"] = json_loads_safe(d.get("nice_to_have_skills")) or []
    d["acceptable_locations"] = json_loads_safe(d.get("acceptable_locations")) or []
    return d


def location_matches(job_location: str, user_locations: list, remote_allowed: bool) -> bool:
    """Checks whether a job listing matches user location filters or remote preference."""
    if not job_location:
        return True
    loc_lower = job_location.lower()
    
    if remote_allowed and any(r in loc_lower for r in ["remote", "hybrid", "anywhere", "wfh", "telecommute"]):
        return True
        
    if not user_locations or "india" in [l.lower() for l in user_locations]:
        return True
        
    return any(loc.lower() in loc_lower for loc in user_locations)


def title_matches(job_title: str, target_titles: list) -> bool:
    """Broad title matching heuristic supporting substring and token overlap."""
    if not target_titles:
        return True
    job_lower = (job_title or "").lower()
    for target in target_titles:
        target_clean = target.strip().lower()
        if target_clean in job_lower:
            return True
        target_tokens = [w for w in re.findall(r"\b[a-z]{4,}\b", target_clean)]
        if any(token in job_lower for token in target_tokens):
            return True
    return False


def calculate_ats_score(resume_text: str, job_title: str, job_desc: str, candidate_skills: list = None) -> int:
    """
    Computes a dynamic ATS match percentage (15% - 98%):
    - 35% Title alignment
    - 45% Skill and proficiency overlap
    - 20% Keyword density
    """
    if not resume_text or not (job_title or job_desc):
        return 50

    resume_text_lower = resume_text.lower()
    job_full_text = f"{job_title} {job_desc or ''}".lower()

    # 1. Title Similarity (35 points)
    title_score = 0.0
    job_title_words = [w for w in re.findall(r"\b[a-z]{3,}\b", job_title.lower()) if w not in ("and", "the", "for", "with", "lead", "senior")]
    if job_title_words:
        matched_title_words = sum(1 for w in job_title_words if w in resume_text_lower)
        title_score = (matched_title_words / len(job_title_words)) * 35.0
    else:
        title_score = 20.0

    # 2. Skill Overlap (45 points)
    skill_score = 0.0
    if candidate_skills:
        matched_skills = sum(1 for s in candidate_skills if s.lower() in job_full_text)
        total_eval_skills = min(len(candidate_skills), 15) if candidate_skills else 1
        skill_score = min(1.0, matched_skills / max(total_eval_skills, 1)) * 45.0
    else:
        key_terms = set(re.findall(r"\b[a-z]{4,}\b", job_full_text)) - {"with", "that", "this", "from", "have", "will", "your", "about"}
        if key_terms:
            matched_terms = sum(1 for t in key_terms if t in resume_text_lower)
            skill_score = (matched_terms / len(key_terms)) * 45.0

    # 3. Density / Keyword Frequency (20 points)
    job_words = re.findall(r"\b[a-z]{4,}\b", job_full_text)
    job_freq = Counter(job_words)
    top_keywords = [word for word, _ in job_freq.most_common(20) if word not in ("with", "that", "this", "from", "have", "will", "your", "about", "team", "work")]
    
    if top_keywords:
        top_matches = sum(1 for kw in top_keywords if kw in resume_text_lower)
        density_score = (top_matches / len(top_keywords)) * 20.0
    else:
        density_score = 10.0

    total_score = int(round(title_score + skill_score + density_score))
    return max(15, min(total_score, 98))


def match_new_jobs_for_user(user_id: int) -> dict:
    """Evaluates the shared job pool against user criteria and populates decisions."""
    with db_session() as conn:
        profile = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        if not profile or not profile["resume_blob"]:
            return {"error": "Upload and parse a resume first.", "considered": 0, "matched": 0}

        resume_text = resume_tailor.extract_resume_text(profile["resume_blob"])
        skills = json_loads_safe(profile["must_have_skills"]) or []
        target_titles = json_loads_safe(profile["target_titles"]) or []
        user_locations = json_loads_safe(profile["acceptable_locations"]) or []
        remote_allowed = bool(profile["remote_first"])

        postings = conn.execute("SELECT * FROM job_postings").fetchall()

        matched_count = 0
        for job in postings:
            if not location_matches(job["location"], user_locations, remote_allowed):
                continue
            if not title_matches(job["title"], target_titles):
                continue

            score = calculate_ats_score(resume_text, job["title"], job["description"], skills)

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
    """Fetches matched vacancies for the candidate sorted by highest ATS score."""
    with db_session() as conn:
        rows = conn.execute(
            """SELECT jp.*, ujd.id as decision_id, ujd.base_ats_score, ujd.ats_score, ujd.was_tailored, ujd.decision
               FROM job_postings jp
               JOIN user_job_decisions ujd ON jp.id = ujd.job_posting_id
               WHERE ujd.user_id = ?
               ORDER BY COALESCE(ujd.ats_score, ujd.base_ats_score) DESC""",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]