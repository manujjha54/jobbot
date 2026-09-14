"""
match_engine.py
Scores each raw job posting against the candidate profile (0.0 - 1.0).
Pure keyword/heuristic scoring - no external calls, no API cost, fast enough
to run over hundreds of postings a day.
"""

import re


def _normalize(text):
    return re.sub(r"[^a-z0-9\s]", " ", (text or "").lower())


def score_job(job, profile):
    title = _normalize(job.get("title", ""))
    desc = _normalize(job.get("description", ""))
    location = _normalize(job.get("location", ""))
    text = f"{title} {desc}"

    # Hard excludes
    for bad in profile.get("exclude_keywords", []):
        if bad.lower() in text:
            return 0.0, ["excluded: " + bad]

    # Hard domain gate: without this, a completely unrelated role (e.g. "IT
    # Manager") can rack up enough partial credit from generic word overlap
    # ("manager"), seniority, and remote/location bonuses alone to clear the
    # threshold without ever being a real match for the field. Require at
    # least one real anchor phrase from the profile's actual domain.
    anchors = [_normalize(a) for a in profile.get("role_anchor_terms", [])]
    if anchors and not any(a in text for a in anchors):
        return 0.0, ["no core role anchor term found"]

    reasons = []
    score = 0.0

    # 1. Title match against target titles (heaviest weight, up to 0.4)
    title_score = 0.0
    for target in profile["target_titles"]:
        target_norm = _normalize(target)
        target_words = set(target_norm.split())
        title_words = set(title.split())
        overlap = len(target_words & title_words) / max(len(target_words), 1)
        if overlap > title_score:
            title_score = overlap
    score += 0.4 * title_score
    if title_score > 0.5:
        reasons.append("strong title match")

    # 2. Seniority keyword present in title (0.15)
    if any(k in title for k in profile.get("seniority_keywords", [])):
        score += 0.15
        reasons.append("seniority match")

    # 3. Must-have skills present anywhere (up to 0.25, scaled by coverage)
    must_have = [s.lower() for s in profile.get("must_have_any_skills", [])]
    hits = sum(1 for s in must_have if s in text)
    if must_have:
        coverage = min(hits / max(len(must_have) * 0.3, 1), 1.0)  # need ~30% coverage for full credit
        score += 0.25 * coverage
        if hits >= 3:
            reasons.append(f"{hits} core skills matched")

    # 4. Nice-to-have skills (up to 0.1)
    nice = [s.lower() for s in profile.get("nice_to_have_skills", [])]
    nice_hits = sum(1 for s in nice if s in text)
    if nice:
        score += 0.1 * min(nice_hits / max(len(nice) * 0.3, 1), 1.0)

    # 5. Location / remote fit (0.1)
    loc_prefs = profile.get("location_preferences", {})
    acceptable = [_normalize(l) for l in loc_prefs.get("acceptable_locations", [])]
    if job.get("remote") and loc_prefs.get("remote_first"):
        score += 0.1
        reasons.append("remote")
    elif any(a in location for a in acceptable):
        score += 0.1
        reasons.append("location match")

    return round(min(score, 1.0), 3), reasons


def filter_and_rank(jobs, profile, threshold=0.35):
    scored = []
    for job in jobs:
        s, reasons = score_job(job, profile)
        if s >= threshold:
            job_copy = dict(job)
            job_copy["match_score"] = s
            job_copy["match_reasons"] = reasons
            scored.append(job_copy)
    scored.sort(key=lambda j: j["match_score"], reverse=True)
    return scored
