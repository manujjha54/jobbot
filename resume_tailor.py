"""
resume_tailor.py
Reads text out of the base resume .docx (for ATS scoring), and produces a tailored
copy with missing keywords distributed naturally across the Core Competencies bullets
and experience sections throughout the resume.
"""

import io
import re
from docx import Document

BUCKET_RULES = [
    ("Platforms & Tooling", [
        "salesforce", "hubspot", "dynamics", "zoho", "gainsight", "churnzero",
        "jira", "confluence", "tableau", "excel", "powerpoint", "outlook",
        "crm", "zendesk", "snowflake", "looker", "slack", "asana", "notion",
        "workday", "netsuite", "intercom", "freshdesk", "power bi", "sql", "pos",
        "inventory", "erp", "saas"
    ]),
    ("Leadership & Strategy", [
        "lead", "leadership", "coach", "coaching", "mentor", "mentoring",
        "hire", "hiring", "manage", "management", "escalation", "kpi",
        "goal", "1:1", "team", "director", "head of", "governance", "enablement"
    ]),
    ("Lifecycle & Growth", [
        "retention", "renewal", "renewals", "onboarding", "churn", "health",
        "adoption", "qbr", "ebr", "nrr", "grr", "ttv", "voc", "lifecycle",
        "upsell", "cross-sell", "expansion", "forecasting", "implementation",
        "customer success", "account management"
    ]),
]
DEFAULT_BUCKET = "Platforms & Tooling"


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


def _classify(keyword):
    kw_lower = keyword.lower()
    for bucket_name, hints in BUCKET_RULES:
        if any(hint in kw_lower for hint in hints):
            return bucket_name
    return DEFAULT_BUCKET


def _has_keyword(text, keyword):
    return re.search(r"\b" + re.escape(keyword) + r"\b", text, re.IGNORECASE) is not None


def tailor_resume(base_docx_bytes, missing_keywords):
    """
    Distributes missing keywords throughout the document:
    1. Injects categorised keywords into Core Competencies / Skills sections.
    2. Weaves remaining keywords into experience bullets naturally.
    """
    if not missing_keywords:
        return base_docx_bytes

    doc = Document(io.BytesIO(base_docx_bytes))
    unplaced_keywords = list(missing_keywords)

    # 1. Search for Skills / Competencies section
    skills_header_idx = None
    for i, p in enumerate(doc.paragraphs):
        p_text = p.text.strip().lower()
        if any(term in p_text for term in ["core competencies", "technical skills", "skills & tools", "key skills"]):
            skills_header_idx = i
            break

    if skills_header_idx is not None:
        # Collect paragraphs under the skills section
        i = skills_header_idx + 1
        while i < len(doc.paragraphs):
            p = doc.paragraphs[i]
            p_text = p.text.strip()
            if not p_text or len(p_text) > 250:
                break

            for kw in list(unplaced_keywords):
                bucket = _classify(kw)
                # Check if current paragraph matches category bucket
                if (bucket.lower() in p_text.lower() or ":" in p_text) and not _has_keyword(p_text, kw):
                    addition = f", {kw}"
                    if p.runs:
                        p.runs[-1].text += addition
                    else:
                        p.add_run(addition)
                    unplaced_keywords.remove(kw)
            i += 1
            if i - skills_header_idx > 8:
                break

    # 2. Weave any remaining keywords into experience bullet points
    if unplaced_keywords:
        for p in doc.paragraphs:
            p_text = p.text.strip()
            # Identify work experience bullet points
            if len(p_text.split()) >= 10 and not p_text.startswith("http"):
                kw_batch = unplaced_keywords[:2]
                to_add = [k for k in kw_batch if not _has_keyword(p_text, k)]
                if to_add:
                    addition = f" (Key proficiencies: {', '.join(to_add)})"
                    if p.runs:
                        p.runs[-1].text += addition
                    else:
                        p.add_run(addition)
                    for k in to_add:
                        if k in unplaced_keywords:
                            unplaced_keywords.remove(k)
            if not unplaced_keywords:
                break

    # 3. Fallback: append any remaining terms
    if unplaced_keywords:
        p = doc.add_paragraph()
        run = p.add_run("Additional ATS Keywords: " + ", ".join(sorted(unplaced_keywords)))
        run.italic = True

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()