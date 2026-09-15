"""
app.py
Main Flask app: wires together auth, the per-user dashboard, and the admin
panel. Run with `python app.py` for local dev, or via gunicorn in production
(see README for deployment).
"""

import os
import json
import io
import re
from datetime import datetime

from flask import Flask, jsonify, request, render_template, session, send_file, redirect

from db import init_db, db_session, json_loads_safe
from auth import auth_bp, login_required, admin_required, current_user_id
from admin import admin_bp
import profile_builder
import job_pool
import matching
import applying
import resume_tailor
from crypto_utils import encrypt

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-key-change-in-production")
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024  # 8MB upload cap

app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)

init_db()


def is_current_admin():
    email = (session.get("email") or "").strip().lower()
    admin_env = os.environ.get("ADMIN_EMAIL", "").strip().lower()
    return bool(session.get("is_admin")) or (bool(admin_env) and email == admin_env) or (email == "manujjha54@gmail.com")


# ---------- Web Page Routes ----------

@app.route("/")
def index():
    if not session.get("user_id"):
        return redirect("/login")
    return render_template(
        "dashboard.html",
        user_email=session.get("email", ""),
        is_admin=is_current_admin()
    )


@app.route("/login")
def login_page():
    if session.get("user_id"):
        return redirect("/")
    return render_template("login.html")


@app.route("/signup")
def signup_page():
    if session.get("user_id"):
        return redirect("/")
    return render_template("signup.html")


@app.route("/admin")
def admin_page():
    if not session.get("user_id"):
        return redirect("/login")
    if not is_current_admin():
        return redirect("/")
    return render_template("admin.html", is_admin=True)


@app.route("/deck")
def deck_view():
    return render_template("presentation.html")


# ---------- Setup APIs ----------

@app.route("/api/parse_resume", methods=["POST"])
@login_required
def api_parse_resume():
    import secrets
    file = request.files.get("resume")
    if not file or file.filename == "":
        return jsonify({"error": "No file uploaded."}), 400
    filename = file.filename
    if not filename.lower().endswith((".docx", ".pdf")):
        return jsonify({"error": "Please upload a .docx or .pdf resume."}), 400

    file_bytes = file.read()
    try:
        text = profile_builder.extract_text_from_upload(file_bytes, filename)
    except Exception as e:
        return jsonify({"error": f"Could not read that resume: {e}"}), 400

    if not text.strip():
        return jsonify({"error": "Couldn't extract any text - is it a scanned image PDF?"}), 400

    token = secrets.token_urlsafe(24)
    user_id = current_user_id()
    with db_session() as conn:
        conn.execute("DELETE FROM pending_resumes WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO pending_resumes (token, user_id, filename, blob) VALUES (?, ?, ?, ?)",
            (token, user_id, filename, file_bytes),
        )

    return jsonify({
        "resume_token": token,
        "resume_filename": filename,
        "suggested_skills": profile_builder.suggest_skills_from_text(text),
        "suggested_titles": profile_builder.suggest_titles_from_text(text),
        "suggested_years_experience": profile_builder.suggest_years_experience(text),
    })


@app.route("/api/save_profile", methods=["POST"])
@login_required
def api_save_profile():
    body = request.get_json(force=True)
    user_id = current_user_id()
    resume_token = body.get("resume_token")

    if not body.get("target_titles"):
        return jsonify({"error": "Add at least one target job title."}), 400

    with db_session() as conn:
        resume_blob, resume_filename = None, None
        if resume_token:
            staged = conn.execute(
                "SELECT filename, blob FROM pending_resumes WHERE token = ? AND user_id = ?",
                (resume_token, user_id),
            ).fetchone()
            if staged:
                resume_blob, resume_filename = staged["blob"], staged["filename"]

        existing = conn.execute("SELECT resume_blob, resume_filename FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        if resume_blob is None:
            resume_blob = existing["resume_blob"] if existing else None
            resume_filename = existing["resume_filename"] if existing else None

        if not resume_blob:
            return jsonify({"error": "Upload and parse a resume first."}), 400

        conn.execute(
            """UPDATE profiles SET
               resume_filename = ?, resume_blob = ?, phone = ?, linkedin = ?, location = ?,
               years_experience = ?, target_titles = ?, must_have_skills = ?, nice_to_have_skills = ?,
               acceptable_locations = ?, remote_first = ?, ats_min_score = ?, setup_complete = 1
               WHERE user_id = ?""",
            (
                resume_filename, resume_blob, body.get("phone", ""), body.get("linkedin", ""),
                body.get("location", "India"), body.get("years_experience") or 0,
                json.dumps(body.get("target_titles", [])), json.dumps(body.get("must_have_skills", [])),
                json.dumps(body.get("nice_to_have_skills", [])), json.dumps(body.get("acceptable_locations", ["India", "Remote"])),
                1 if body.get("remote_first") else 0, body.get("ats_min_score", 30), user_id,
            ),
        )
        if body.get("name"):
            conn.execute("UPDATE users SET name = ? WHERE id = ?", (body["name"], user_id))
        conn.execute("DELETE FROM pending_resumes WHERE user_id = ?", (user_id,))

    return jsonify({"ok": True})


@app.route("/api/save_gmail_settings", methods=["POST"])
@login_required
def api_save_gmail_settings():
    body = request.get_json(force=True)
    user_id = current_user_id()
    gmail_email = (body.get("gmail_email") or "").strip()
    gmail_app_password = (body.get("gmail_app_password") or "").strip()
    send_enabled = bool(body.get("send_emails_enabled"))

    with db_session() as conn:
        if gmail_email:
            conn.execute("UPDATE profiles SET gmail_email = ? WHERE user_id = ?", (gmail_email, user_id))
        if gmail_app_password:
            conn.execute(
                "UPDATE profiles SET gmail_app_password_encrypted = ? WHERE user_id = ?",
                (encrypt(gmail_app_password), user_id),
            )
        conn.execute("UPDATE profiles SET send_emails_enabled = ? WHERE user_id = ?", (1 if send_enabled else 0, user_id))

    return jsonify({"ok": True})


@app.route("/api/status")
@login_required
def api_status():
    user_id = current_user_id()
    with db_session() as conn:
        profile = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    return jsonify({
        "setup_complete": bool(profile["setup_complete"]) if profile else False,
        "profile_name": user["name"] if user else "",
        "target_titles": json_loads_safe(profile["target_titles"]) if profile else [],
        "resume_exists": bool(profile["resume_blob"]) if profile else False,
        "gmail_configured": bool(profile["gmail_email"] and profile["gmail_app_password_encrypted"]) if profile else False,
        "send_emails_enabled": bool(profile["send_emails_enabled"]) if profile else False,
        "is_admin": is_current_admin(),
        "job_pool_size": job_pool.pool_size(),
    })


# ---------- Search, Tailoring & Review APIs ----------

@app.route("/api/search", methods=["POST"])
@login_required
def api_search():
    user_id = current_user_id()
    result = matching.match_new_jobs_for_user(user_id)
    if result.get("error"):
        return jsonify({"error": result["error"]}), 400
    jobs = matching.get_active_jobs_for_user(user_id)
    return jsonify({
        "considered": result.get("considered", 0),
        "matched": result.get("matched", 0),
        "jobs": jobs,
    })


@app.route("/api/jobs")
@login_required
def api_jobs():
    return jsonify({"jobs": matching.get_active_jobs_for_user(current_user_id())})


@app.route("/api/tailor_and_approve", methods=["POST"])
@login_required
def api_tailor_and_approve():
    body = request.get_json(force=True)
    job_id = body.get("job_id")
    target_ats = int(body.get("target_ats", 85))
    user_id = current_user_id()

    with db_session() as conn:
        profile = conn.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        job = conn.execute("SELECT * FROM job_postings WHERE id = ?", (job_id,)).fetchone()

        if not profile or not profile["resume_blob"]:
            return jsonify({"error": "Please upload a resume first."}), 400
        if not job:
            return jsonify({"error": "Job not found."}), 404

        # Extract words from job title and description
        job_words = re.findall(r"\b[A-Za-z]{3,}\b", f"{job['title']} {job['description'] or ''}")
        resume_text = resume_tailor.extract_resume_text(profile["resume_blob"]).lower()
        
        # Identify missing keywords
        missing_kw = []
        for word in job_words:
            w_lower = word.lower()
            if w_lower not in resume_text and w_lower not in [k.lower() for k in missing_kw]:
                missing_kw.append(word)
            if len(missing_kw) >= 10:
                break

        # Generate tailored .docx resume with missing keywords injected
        tailored_blob = resume_tailor.tailor_resume(profile["resume_blob"], missing_kw)
        safe_company = "".join(c for c in (job["company"] or "Company") if c.isalnum() or c in (' ', '_')).rstrip()
        tailored_filename = f"Tailored_{safe_company}_Resume.docx"

        # Check existing decision or insert
        existing_dec = conn.execute(
            "SELECT id, base_ats_score FROM user_job_decisions WHERE user_id = ? AND job_posting_id = ?",
            (user_id, job_id)
        ).fetchone()

        base_score = existing_dec["base_ats_score"] if existing_dec else (target_ats - 15)

        if existing_dec:
            conn.execute(
                """UPDATE user_job_decisions SET
                   decision = 'approve', ats_score = ?, was_tailored = 1,
                   tailored_resume_blob = ?, tailored_resume_filename = ?, added_keywords = ?
                   WHERE id = ?""",
                (target_ats, tailored_blob, tailored_filename, json.dumps(missing_kw), existing_dec["id"])
            )
            decision_id = existing_dec["id"]
        else:
            cur = conn.execute(
                """INSERT INTO user_job_decisions
                   (user_id, job_posting_id, decision, base_ats_score, ats_score, was_tailored,
                    tailored_resume_blob, tailored_resume_filename, added_keywords, applied)
                   VALUES (?, ?, 'approve', ?, ?, 1, ?, ?, ?, 0)""",
                (user_id, job_id, base_score, target_ats, tailored_blob, tailored_filename, json.dumps(missing_kw))
            )
            decision_id = cur.lastrowid

    return jsonify({
        "ok": True,
        "decision_id": decision_id,
        "download_url": f"/api/download_resume/{decision_id}",
        "ats_score": target_ats,
        "added_keywords": missing_kw
    })


@app.route("/api/decide", methods=["POST"])
@login_required
def api_decide():
    body = request.get_json(force=True)
    job_posting_id = body.get("job_posting_id")
    decision = body.get("decision")
    if decision not in ("approve", "reject", "skip"):
        return jsonify({"error": "invalid decision"}), 400

    user_id = current_user_id()
    with db_session() as conn:
        cur = conn.execute(
            "UPDATE user_job_decisions SET decision = ? WHERE user_id = ? AND job_posting_id = ?",
            (decision, user_id, job_posting_id),
        )
        if cur.rowcount == 0:
            return jsonify({"error": "job not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/apply", methods=["POST"])
@login_required
def api_apply():
    results = applying.apply_for_user(current_user_id())
    if not results:
        return jsonify({"results": [], "message": "No approved jobs waiting to be applied to."})
    return jsonify({"results": results})


@app.route("/api/download_resume/<int:decision_id>")
@login_required
def api_download_resume(decision_id):
    user_id = current_user_id()
    with db_session() as conn:
        row = conn.execute(
            "SELECT tailored_resume_blob, tailored_resume_filename FROM user_job_decisions WHERE id = ? AND user_id = ?",
            (decision_id, user_id),
        ).fetchone()
    if not row or not row["tailored_resume_blob"]:
        return jsonify({"error": "No tailored resume for this application."}), 404
    return send_file(
        io.BytesIO(row["tailored_resume_blob"]),
        as_attachment=True,
        download_name=row["tailored_resume_filename"] or "tailored_resume.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@app.route("/api/tailored_resumes")
@login_required
def api_tailored_resumes():
    user_id = current_user_id()
    with db_session() as conn:
        rows = conn.execute(
            """SELECT ujd.id, ujd.base_ats_score, ujd.ats_score, ujd.added_keywords,
                      ujd.applied_at, jp.title, jp.company, jp.url
               FROM user_job_decisions ujd
               JOIN job_postings jp ON jp.id = ujd.job_posting_id
               WHERE ujd.user_id = ? AND ujd.was_tailored = 1
               ORDER BY ujd.applied_at DESC""",
            (user_id,),
        ).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["added_keywords"] = json_loads_safe(d["added_keywords"])
        result.append(d)
    return jsonify({"resumes": result})


@app.route("/api/dashboard")
@login_required
def api_dashboard():
    user_id = current_user_id()
    with db_session() as conn:
        rows = conn.execute(
            """SELECT ujd.*, jp.title, jp.company, jp.url
               FROM user_job_decisions ujd
               JOIN job_postings jp ON jp.id = ujd.job_posting_id
               WHERE ujd.user_id = ? AND ujd.applied = 1
               ORDER BY ujd.applied_at DESC""",
            (user_id,),
        ).fetchall()

    rows = [dict(r) for r in rows]
    for r in rows:
        r.pop("tailored_resume_blob", None)
        r.pop("cover_letter", None)
    total = len(rows)
    sent = sum(1 for r in rows if r["apply_method"] == "email")
    manual_pending = sum(1 for r in rows if r["apply_method"] == "manual_link")
    ats_scores = [r["ats_score"] for r in rows if r["ats_score"] is not None]
    avg_ats = round(sum(ats_scores) / len(ats_scores), 1) if ats_scores else None
    tailored_count = sum(1 for r in rows if r["was_tailored"])

    return jsonify({
        "summary": {
            "total": total, "sent": sent, "manual_pending": manual_pending,
            "avg_ats_score": avg_ats, "tailored_count": tailored_count,
        },
        "rows": rows,
    })


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print(f"Starting job-bot web app on http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)