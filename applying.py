"""
applying.py
The apply step for one user's approved jobs: ATS check against their resume,
auto-tailor if below their threshold, draft a cover letter, and either send
an email (if the posting lists one and the user has enabled + configured
Gmail sending) or mark it ready for manual apply via the posting's link.

Everything reads/writes the database - no files on disk, so this works
cleanly on ephemeral hosting.
"""

import json
import smtplib
from datetime import datetime
from email.message import EmailMessage

from db import db_session, json_loads_safe
from ats_score import score_resume
from resume_tailor import extract_resume_text, tailor_resume
from matching import _profile_row_to_dict

COVER_LETTER_TEMPLATE_PATH = "cover_letter_template.txt"


def _load_template():
    with open(COVER_LETTER_TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def _generate_cover_letter(job, profile_dict, profile_row, candidate_name):
    template = _load_template()
    top_skills = ", ".join(profile_dict["must_have_any_skills"][:5]) or "relevant experience"
    return template.format(
        candidate_name=candidate_name or "",
        job_title=job["title"],
        company=job["company"],
        years_experience=profile_row["years_experience"] or 0,
        top_skills=top_skills,
        location=profile_row["location"] or "",
    )


def _resolve_resume(job, profile_dict, profile_row, ats_min_score):
    """Returns (resume_bytes_to_use, base_score, final_score, was_tailored, tailored_filename, added_keywords)."""
    base_bytes = profile_row["resume_blob"]
    if not base_bytes:
        return None, None, None, False, None, []

    company_terms = [t for t in (job.get("company") or "").replace(",", " ").split() if len(t) > 2]
    base_text = extract_resume_text(base_bytes)
    score, matched, missing = score_resume(base_text, job.get("description", ""), profile_dict, exclude_terms=company_terms)

    if score >= ats_min_score or not missing:
        return base_bytes, score, score, False, None, []

    tailored_bytes = tailor_resume(base_bytes, missing)
    tailored_text = extract_resume_text(tailored_bytes)
    new_score, _, _ = score_resume(tailored_text, job.get("description", ""), profile_dict, exclude_terms=company_terms)

    safe_company = "".join(c if c.isalnum() else "_" for c in job["company"])[:40]
    filename = f"{safe_company}_{job['id']}_tailored.docx"
    return tailored_bytes, score, new_score, True, filename, sorted(missing)


def _send_email(job, cover_letter, profile_row, resume_bytes, resume_filename):
    from crypto_utils import decrypt
    gmail_email = profile_row["gmail_email"]
    app_password = decrypt(profile_row["gmail_app_password_encrypted"])
    if not gmail_email or not app_password:
        return "not_configured"

    msg = EmailMessage()
    msg["Subject"] = f"Application: {job['title']}"
    msg["From"] = gmail_email
    msg["To"] = job["apply_email"]
    msg.set_content(cover_letter)
    if resume_bytes:
        msg.add_attachment(
            resume_bytes, maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=resume_filename or "resume.docx",
        )

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
            smtp.starttls()
            smtp.login(gmail_email, app_password)
            smtp.send_message(msg)
        return "sent"
    except Exception as e:
        return f"error: {e}"


def apply_for_user(user_id):
    """
    Processes every 'approve'd, not-yet-applied job for this user. Returns
    a list of result dicts. Safe to call repeatedly - already-applied jobs
    are skipped.
    """
    with db_session() as conn:
        profile_row = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not profile_row or not user_row:
            return []

        to_apply = conn.execute(
            """SELECT ujd.*, jp.title, jp.company, jp.location, jp.remote, jp.url,
                      jp.description, jp.apply_email
               FROM user_job_decisions ujd
               JOIN job_postings jp ON jp.id = ujd.job_posting_id
               WHERE ujd.user_id = ? AND ujd.decision = 'approve' AND ujd.applied = 0""",
            (user_id,),
        ).fetchall()

    if not to_apply:
        return []

    profile_dict = _profile_row_to_dict(profile_row)
    ats_min = profile_row["ats_min_score"] or 90
    results = []

    for row in to_apply:
        job = dict(row)
        cover_letter = _generate_cover_letter(job, profile_dict, profile_row, user_row["name"])
        resume_bytes, base_score, final_score, was_tailored, tailored_filename, added_keywords = _resolve_resume(
            job, profile_dict, profile_row, ats_min
        )

        if job.get("apply_email"):
            if profile_row["send_emails_enabled"]:
                send_result = _send_email(job, cover_letter, profile_row, resume_bytes, tailored_filename)
                if send_result == "sent":
                    status, method = "sent", "email"
                elif send_result == "not_configured":
                    status, method = "Gmail not configured - draft saved", "email_draft"
                else:
                    status, method = f"Send failed: {send_result}", "email_failed"
            else:
                status, method = "Email sending disabled - draft saved", "email_draft"
        else:
            status, method = "Ready - apply manually via link", "manual_link"

        with db_session() as conn:
            conn.execute(
                """UPDATE user_job_decisions SET
                   applied = 1, ats_score = ?, base_ats_score = ?, was_tailored = ?,
                   added_keywords = ?, tailored_resume_blob = ?, tailored_resume_filename = ?,
                   cover_letter = ?, apply_method = ?, status = ?, applied_at = ?
                   WHERE id = ?""",
                (final_score, base_score, 1 if was_tailored else 0,
                 json.dumps(added_keywords), resume_bytes if was_tailored else None, tailored_filename,
                 cover_letter, method, status, datetime.utcnow().isoformat(), row["id"]),
            )

        results.append({
            "job_posting_id": job["job_posting_id"],
            "title": job["title"], "company": job["company"], "url": job["url"],
            "ats_score": final_score, "base_ats_score": base_score, "was_tailored": was_tailored,
            "added_keywords": added_keywords, "apply_method": method, "status": status,
        })

    return results
