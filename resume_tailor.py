"""
resume_tailor.py
Tailors DOCX resumes by injecting missing ATS keywords into core competencies,
skills, and experience sections. Handles both existing DOCX files and dynamically
creates clean DOCX resumes from extracted text.
"""

import io
import re
from docx import Document
from docx.shared import Pt, Inches, RGBColor

BUCKET_RULES = [
    ("Platforms & Tooling", [
        "salesforce", "hubspot", "dynamics", "zoho", "gainsight", "churnzero",
        "jira", "confluence", "tableau", "excel", "powerpoint", "outlook",
        "crm", "zendesk", "snowflake", "looker", "slack", "asana", "notion",
        "workday", "netsuite", "intercom", "freshdesk", "power bi", "sql", "pos",
        "inventory", "erp", "saas", "api", "analytics"
    ]),
    ("Leadership & Strategy", [
        "lead", "leadership", "coach", "coaching", "mentor", "mentoring",
        "hire", "hiring", "manage", "management", "escalation", "kpi",
        "goal", "1:1", "team", "director", "head of", "governance", "enablement",
        "stakeholder management", "strategy", "operations"
    ]),
    ("Lifecycle & Growth", [
        "retention", "renewal", "renewals", "onboarding", "churn", "health",
        "adoption", "qbr", "ebr", "nrr", "grr", "ttv", "voc", "lifecycle",
        "upsell", "cross-sell", "expansion", "forecasting", "implementation",
        "customer success", "account management", "client enablement"
    ]),
]
DEFAULT_BUCKET = "Platforms & Tooling"


def extract_resume_text(docx_bytes: bytes) -> str:
    """Extracts all text from docx or plain text fallback."""
    if not docx_bytes:
        return ""
    try:
        doc = Document(io.BytesIO(docx_bytes))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for t in doc.tables:
            for row in t.rows:
                for c in row.cells:
                    if c.text.strip():
                        parts.append(c.text.strip())
        return "\n".join(parts)
    except Exception:
        try:
            return docx_bytes.decode("utf-8", errors="ignore")
        except Exception:
            return ""


def _classify(keyword: str) -> str:
    kw_lower = keyword.lower()
    for bucket_name, hints in BUCKET_RULES:
        if any(h in kw_lower for h in hints):
            return bucket_name
    return DEFAULT_BUCKET


def _has_keyword(text: str, keyword: str) -> bool:
    return re.search(r"\b" + re.escape(keyword) + r"\b", text, re.IGNORECASE) is not None


def create_docx_from_raw_text(text: str, missing_keywords: list[str]) -> bytes:
    """Generates a clean DOCX document from raw text with injected ATS keywords."""
    doc = Document()
    
    # Page Margins
    for section in doc.sections:
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.8)
        section.right_margin = Inches(0.8)

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    
    # Candidate Header
    if lines:
        title_para = doc.add_paragraph()
        run = title_para.add_run(lines[0])
        run.bold = True
        run.font.size = Pt(16)
        run.font.color.rgb = RGBColor(17, 24, 39)

    # Injected Core Competencies section
    if missing_keywords:
        sec_h = doc.add_paragraph()
        h_run = sec_h.add_run("TARGET ATS COMPETENCIES & PROFICIENCIES")
        h_run.bold = True
        h_run.font.size = Pt(11)
        h_run.font.color.rgb = RGBColor(79, 70, 229)

        kw_para = doc.add_paragraph()
        kw_para.add_run(" • " + " • ".join(missing_keywords[:12]))

    # Add remaining original lines
    for line in lines[1:]:
        p = doc.add_paragraph()
        if any(h in line.upper() for h in ["EXPERIENCE", "EMPLOYMENT", "SKILLS", "EDUCATION", "SUMMARY"]):
            r = p.add_run(line)
            r.bold = True
            r.font.size = Pt(11)
            r.font.color.rgb = RGBColor(79, 70, 229)
        else:
            p.add_run(line)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def tailor_resume(base_docx_bytes: bytes, missing_keywords: list[str], raw_text_fallback: str = "") -> bytes:
    """
    Distributes missing keywords throughout the DOCX. If base_docx_bytes is not
    a valid DOCX document (e.g. was uploaded as PDF/text), constructs a fresh DOCX.
    """
    try:
        doc = Document(io.BytesIO(base_docx_bytes))
    except Exception:
        # Fallback to creating a new formatted docx from text
        return create_docx_from_raw_text(raw_text_fallback, missing_keywords)

    if not missing_keywords:
        out = io.BytesIO()
        doc.save(out)
        return out.getvalue()

    unplaced = list(missing_keywords)

    # 1. Distribute in skills/competencies paragraphs
    for p in doc.paragraphs:
        p_text = p.text.strip().lower()
        if any(h in p_text for h in ["core competencies", "skills", "tools", "expertise"]):
            to_inject = unplaced[:6]
            if to_inject:
                addition = " • " + " • ".join(to_inject)
                if p.runs:
                    p.runs[-1].text += addition
                else:
                    p.add_run(addition)
                unplaced = unplaced[6:]
            break

    # 2. Weave into experience paragraphs
    if unplaced:
        for p in doc.paragraphs:
            if len(p.text.split()) > 10 and not p.text.startswith("http"):
                batch = [k for k in unplaced[:2] if not _has_keyword(p.text, k)]
                if batch:
                    addition = f" (Key proficiencies: {', '.join(batch)})"
                    if p.runs:
                        p.runs[-1].text += addition
                    else:
                        p.add_run(addition)
                    unplaced = unplaced[2:]
            if not unplaced:
                break

    # 3. Trailing fallback section
    if unplaced:
        p = doc.add_paragraph()
        r = p.add_run("Additional ATS Proficiencies: " + ", ".join(sorted(unplaced)))
        r.italic = True

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()