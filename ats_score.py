"""
ats_score.py
A lightweight ATS (Applicant Tracking System) keyword-match scorer.

Real ATS platforms (Workday, Taleo, Greenhouse's own parser, etc.) mostly
work by extracting important terms from the job description - hard skills,
tools, certifications, role-specific nouns - and checking how many of them
appear in the resume text. This module approximates that:

  1. Pull "important keywords" out of a job description using:
     - a curated domain vocabulary (from profile.json's skill lists, so
       it's tuned to YOUR field rather than generic)
     - acronyms (2-6 uppercase letters, e.g. NRR, QBR, CRM, SaaS)
     - frequency-based noun-ish tokens that appear multiple times in the JD
  2. Check what fraction of those appear (as substrings, case-insensitive)
     in the resume text
  3. Return a 0-100 score + the list of keywords missing from the resume

This is a heuristic, not a guarantee of what any specific real ATS will
score - but it's the same core mechanic (keyword coverage) that tools like
Jobscan use, and it's what you can actually control by editing your resume.
"""

import re
from collections import Counter

STOPWORDS = set("""
a an the and or but if then else for of to in on at by with without within
is are was were be been being this that these those it its as from into
your you we our their they he she his her them i me my mine ours yours
will would can could should shall may might must have has had do does did
not no yes so than too very just about over under between across per via
role team work working years experience including etc such more most all
any some each other another new job company position looking seeking
required preferred plus strong excellent ability skills responsibilities
requirements who what when where why how
""".split())

ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}s?\b")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z+/\-]{2,}")
TITLECASE_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
COMMON_SENTENCE_STARTERS = {
    "We", "Our", "The", "This", "You", "Your", "They", "It", "As", "In", "For",
    "If", "Requirements", "Responsibilities", "Bonus", "Experience", "Strong",
    "Familiarity", "Must", "Should", "Ability", "Please", "About",
}
ACRONYM_EXCLUDE = {
    "USA", "PM", "AM", "ID", "HR", "EEO", "LLC", "INC", "CA", "NY", "US", "UK",
    "FAQ", "TBD", "ASAP", "EOD", "COB",
}
# Job-posting structural/boilerplate terms that aren't actual skills or
# requirements, but get mis-picked-up as "proper nouns" from section headers
# like "Position Overview" or "Requisition ID: 12345".
BOILERPLATE_TERMS = {
    "overview", "position", "requisition", "summary", "description",
    "location", "department", "employment", "type", "level", "category",
    "date", "posted", "apply", "company", "job", "title", "status",
    "full", "part", "equal", "opportunity", "employer", "disability",
    "veteran", "applicant", "applicants", "candidate", "candidates",
    "resume", "cover", "letter", "salary", "range", "benefits",
    "compensation", "eligibility", "eligible", "id", "ref", "reference",
}


def _load_domain_vocab(profile):
    vocab = set()
    for key in ("must_have_any_skills", "nice_to_have_skills"):
        for term in profile.get(key, []):
            vocab.add(term.lower())
    return vocab


def extract_keywords(job_description, profile, max_keywords=25, exclude_terms=None):
    """Return the set of 'important' keywords an ATS would likely check for."""
    text = job_description or ""
    text_lower = text.lower()
    domain_vocab = _load_domain_vocab(profile)
    exclude_lower = {t.lower() for t in (exclude_terms or [])}

    keywords = set()

    # 1. Domain vocabulary terms that actually appear in this JD
    for term in domain_vocab:
        if term in text_lower:
            keywords.add(term)

    # 2. Acronyms as-is (case matters for these, e.g. NRR, QBR, CRM, SaaS, KPI)
    for m in ACRONYM_RE.findall(text):
        if m.upper() not in ACRONYM_EXCLUDE and m.lower() not in BOILERPLATE_TERMS:
            keywords.add(m)

    # 3. Frequency-based single-word terms (appearing 2+ times, not stopwords)
    words = [w.lower() for w in WORD_RE.findall(text)]
    freq = Counter(w for w in words if w not in STOPWORDS and w not in BOILERPLATE_TERMS and len(w) > 3)
    for word, count in freq.most_common(max_keywords):
        if count >= 2:
            keywords.add(word)

    # 4. Capitalized words appearing mid-sentence are usually proper nouns
    #    (tool/product names like "Snowflake", "Zendesk") - English doesn't
    #    capitalize ordinary words outside sentence-initial position.
    sentences = re.split(r"(?<=[.!?\n])\s+", text)
    for sentence in sentences:
        words_in_sentence = sentence.strip().split()
        for w in words_in_sentence[1:]:  # skip the first word of each sentence
            cleaned = w.strip(",.;:()")
            if cleaned.endswith("'s") or cleaned.endswith("’s"):
                cleaned = cleaned[:-2]
            if (TITLECASE_RE.fullmatch(cleaned)
                    and cleaned not in COMMON_SENTENCE_STARTERS
                    and cleaned.lower() not in BOILERPLATE_TERMS):
                keywords.add(cleaned)

    # Drop anything matching the hiring company's own name (or excluded terms) -
    # e.g. "Autodesk" showing up as a "missing skill" for an Autodesk job is noise.
    if exclude_lower:
        keywords = {k for k in keywords if k.lower() not in exclude_lower}

    # Drop anything matching the hiring company's own name (or excluded terms) -
    # e.g. "Autodesk" showing up as a "missing skill" for an Autodesk job is noise.
    if exclude_lower:
        keywords = {k for k in keywords if k.lower() not in exclude_lower}

    return _dedupe_case_insensitive(keywords)


def _dedupe_case_insensitive(keywords):
    """
    If the same word appears both as e.g. 'Construction' and 'construction'
    (picked up by two different extraction rules), keep only one - preferring
    the capitalized/proper-noun form since it reads more naturally in a resume.
    """
    best_by_lower = {}
    for kw in keywords:
        key = kw.lower()
        if key not in best_by_lower or (kw[0].isupper() and not best_by_lower[key][0].isupper()):
            best_by_lower[key] = kw
    return set(best_by_lower.values())


def score_resume(resume_text, job_description, profile, exclude_terms=None):
    """
    Returns (score_0_to_100, matched_keywords_set, missing_keywords_set)
    """
    keywords = extract_keywords(job_description, profile, exclude_terms=exclude_terms)
    if not keywords:
        return 100.0, set(), set()  # nothing to check against, don't penalize

    resume_lower = (resume_text or "").lower()
    matched = {k for k in keywords if k.lower() in resume_lower}
    missing = keywords - matched

    score = round(100 * len(matched) / len(keywords), 1)
    return score, matched, missing
