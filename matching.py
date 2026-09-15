import re
import json
import math
from collections import Counter
from db import db_session, json_loads_safe
import resume_tailor

def calculate_ats_score(resume_text: str, job_title: str, job_desc: str, candidate_skills: list = None) -> int:
    """
    Calculates dynamic ATS score (0-100%) based on:
    1. Title alignment (35%)
    2. Skill & keyword frequency/overlap (45%)
    3. Content density & term matching (20%)
    """
    if not resume_text or not (job_title or job_desc):
        return 50

    resume_text_lower = resume_text.lower()
    job_full_text = f"{job_title} {job_desc or ''}".lower()

    # 1. Title Similarity (35 pts)
    title_score = 0.0
    job_title_words = [w for w in re.findall(r"\b[a-z]{3,}\b", job_title.lower()) if w not in ("and", "the", "for", "with")]
    if job_title_words:
        matched_title_words = sum(1 for w in job_title_words if w in resume_text_lower)
        title_score = (matched_title_words / len(job_title_words)) * 35.0

    # 2. Skill Overlap (45 pts)
    skill_score = 0.0
    if candidate_skills:
        matched_skills = sum(1 for s in candidate_skills if s.lower() in job_full_text)
        total_skills = len(candidate_skills) if len(candidate_skills) > 0 else 1
        skill_score = min(1.0, matched_skills / min(total_skills, 12)) * 45.0
    else:
        # Fallback to key industry terms in job
        key_terms = set(re.findall(r"\b[a-z]{4,}\b", job_full_text)) - {"with", "that", "this", "from", "have", "will", "your", "about"}
        if key_terms:
            matched_terms = sum(1 for t in key_terms if t in resume_text_lower)
            skill_score = (matched_terms / len(key_terms)) * 45.0

    # 3. Density / Keyword Frequency (20 pts)
    job_words = re.findall(r"\b[a-z]{3,}\b", job_full_text)
    job_freq = Counter(job_words)
    top_keywords = [word for word, _ in job_freq.most_common(20) if word not in ("the", "and", "you", "are", "our", "for", "with", "will")]
    
    if top_keywords:
        top_matches = sum(1 for kw in top_keywords if kw in resume_text_lower)
        density_score = (top_matches / len(top_keywords)) * 20.0
    else:
        density_score = 10.0

    total_score = int(round(title_score + skill_score + density_score))
    return max(15, min(total_score, 98))


def match_new_jobs_for_user(user_id: int) -> dict:
    with db_session() as conn:
        profile = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        if not profile or not profile["resume_blob"]:
            return {"error": "Upload a resume first.", "considered": 0, "matched": 0}

        resume_text = resume_tailor.extract_resume_text(profile["resume_blob"])
        skills = json_loads_safe(profile["must_have_skills"]) or []
        target_titles = json_loads_safe(profile["target_titles"]) or []
        
        postings = conn.execute("SELECT * FROM job_postings").fetchall()

        matched_count = 0
        for job in postings:
            # Calculate unique dynamic score per job
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