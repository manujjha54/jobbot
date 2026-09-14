"""
resume_tailor.py
Reads text out of the base resume .docx (for ATS scoring), and - when the
score is below threshold - produces a tailored copy with missing keywords
distributed naturally across the existing Core Competencies bullets, so the
version actually sent for that specific job reads like a normally-written
resume rather than a resume with a keyword dump bolted on.

This does NOT fabricate experience, invent accomplishments, or change your
job history. It appends terms to whichever existing skills bullet they
belong with (tools go with tools, leadership terms go with leadership,
etc.), extending that bullet's own text so formatting matches exactly.

You should still spot check tailored resumes occasionally (open one from
resumes_tailored/) to make sure nothing looks off before it goes out.
"""

import io
import re
from docx import Document

# Maps a keyword to the Core Competencies bullet it's most at home in, based
# on the bullet prefixes used in the base resume (see build_resume.js).
# Order matters: checked top to bottom, first match wins.
BUCKET_RULES = [
    ("Platforms & Tooling", [
        "salesforce", "hubspot", "dynamics", "zoho", "gainsight", "churnzero",
        "jira", "confluence", "tableau", "excel", "powerpoint", "outlook",
        "crm", "zendesk", "snowflake", "looker", "slack", "asana", "notion",
        "workday", "netsuite", "intercom", "freshdesk", "power bi", "sql",
    ]),
    ("Leadership & Strategy", [
        "lead", "leadership", "coach", "coaching", "mentor", "mentoring",
        "hire", "hiring", "manage", "management", "escalation", "kpi",
        "goal", "1:1", "team", "director", "head of",
    ]),
    ("Lifecycle & Growth", [
        "retention", "renewal", "renewals", "onboarding", "churn", "health",
        "adoption", "qbr", "ebr", "nrr", "grr", "ttv", "voc", "lifecycle",
        "upsell", "cross-sell", "expansion", "forecasting",
    ]),
]
DEFAULT_BUCKET = "Commercial"  # catch-all for domain/industry terms that don't fit above


def extract_resume_text(docx_bytes):
    doc = Document(io.BytesIO(docx_bytes))
    parts = []
    for p in doc.paragraphs:
        if p.text.strip():
            parts.append(p.text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n".join(parts)


def _find_paragraph_index(doc, text_contains):
    for i, p in enumerate(doc.paragraphs):
        if text_contains.lower() in p.text.lower():
            return i
    return None


def _classify(keyword):
    kw_lower = keyword.lower()
    for bucket_name, hints in BUCKET_RULES:
        if any(hint in kw_lower for hint in hints):
            return bucket_name
    return DEFAULT_BUCKET


def _bullet_already_has(paragraph_text, keyword):
    return re.search(r"\b" + re.escape(keyword) + r"\b", paragraph_text, re.IGNORECASE) is not None


def tailor_resume(base_docx_bytes, missing_keywords):
    """
    Distributes missing_keywords across the existing Core Competencies
    bullets by topic (tools with tools, leadership with leadership, etc.),
    appending each to the end of its matching bullet's own text run so
    formatting matches exactly. Anything that doesn't fit a specific bucket
    goes to the general "Commercial" bullet. No new standalone line is added.

    Takes and returns raw .docx bytes - no filesystem involved, so this
    works cleanly with DB-blob storage on ephemeral hosting.
    """
    doc = Document(io.BytesIO(base_docx_bytes))

    comp_idx = _find_paragraph_index(doc, "CORE COMPETENCIES")
    if comp_idx is None:
        # Fallback: resume doesn't have this exact section, just append
        # everything as one line at the end rather than fail silently.
        p = doc.add_paragraph()
        run = p.add_run("Additional Relevant Keywords: " + ", ".join(sorted(missing_keywords)))
        run.italic = True
        out = io.BytesIO()
        doc.save(out)
        return out.getvalue()

    # Map each competencies bullet paragraph by its leading label
    # (e.g. "Platforms & Tooling: Gainsight, ChurnZero, ...")
    bullet_paragraphs = {}
    i = comp_idx + 1
    while i < len(doc.paragraphs):
        text = doc.paragraphs[i].text.strip()
        if not text or ":" not in text:
            break
        label = text.split(":", 1)[0].strip()
        bullet_paragraphs[label] = doc.paragraphs[i]
        i += 1
        if i - comp_idx > 8:  # safety stop
            break

    def find_bucket_paragraph(bucket_name):
        for label, para in bullet_paragraphs.items():
            if bucket_name.lower() in label.lower():
                return para
        return None

    # Group keywords by target bullet paragraph
    grouped = {}
    for kw in sorted(missing_keywords):
        bucket = _classify(kw)
        target_para = find_bucket_paragraph(bucket) or find_bucket_paragraph(DEFAULT_BUCKET)
        if target_para is None:
            target_para = next(iter(bullet_paragraphs.values()), None)
        if target_para is None:
            continue
        key = id(target_para)
        if key not in grouped:
            grouped[key] = {"para": target_para, "keywords": []}
        if not _bullet_already_has(target_para.text, kw):
            grouped[key]["keywords"].append(kw)

    for entry in grouped.values():
        para = entry["para"]
        new_terms = entry["keywords"]
        if not new_terms:
            continue
        addition = ", " + ", ".join(new_terms)
        if para.runs:
            para.runs[-1].text += addition
        else:
            para.add_run(addition)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
