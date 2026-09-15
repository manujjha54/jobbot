"""
admin.py
Admin-only views: see every user, their application activity, trigger a
shared job-pool refresh on demand, and spot problems (e.g. a user whose
Gmail sending keeps failing, or who hasn't gotten any matches).
Access is gated by the is_admin flag on the user's account (see auth.py -
set via the ADMIN_EMAIL environment variable at signup time).
"""

from flask import Blueprint, jsonify, render_template

from db import db_session
from auth import admin_required
import job_pool

admin_bp = Blueprint("admin", __name__)


@admin_bp.route("/admin")
@admin_required
def admin_page():
    return render_template("admin.html")


@admin_bp.route("/api/admin/overview")
@admin_required
def api_admin_overview():
    with db_session() as conn:
        user_count = conn.execute("SELECT COUNT(*) as n FROM users").fetchone()["n"]
        setup_complete_count = conn.execute("SELECT COUNT(*) as n FROM profiles WHERE setup_complete = 1").fetchone()["n"]
        total_applications = conn.execute("SELECT COUNT(*) as n FROM user_job_decisions WHERE applied = 1").fetchone()["n"]
        emails_sent = conn.execute("SELECT COUNT(*) as n FROM user_job_decisions WHERE apply_method = 'email'").fetchone()["n"]
        email_failures = conn.execute("SELECT COUNT(*) as n FROM user_job_decisions WHERE apply_method = 'email_failed'").fetchone()["n"]
        avg_ats = conn.execute("SELECT AVG(ats_score) as a FROM user_job_decisions WHERE ats_score IS NOT NULL").fetchone()["a"]

    return jsonify({
        "user_count": user_count,
        "setup_complete_count": setup_complete_count,
        "total_applications": total_applications,
        "emails_sent": emails_sent,
        "email_failures": email_failures,
        "avg_ats_score": round(avg_ats, 1) if avg_ats else None,
        "job_pool_size": job_pool.pool_size(),
        "last_refresh": job_pool.last_refresh_info(),
    })


@admin_bp.route("/api/admin/users")
@admin_required
def api_admin_users():
    with db_session() as conn:
        users = conn.execute(
            """SELECT u.id, u.email, u.name, u.created_at, u.is_admin,
                      p.setup_complete, p.send_emails_enabled,
                      (SELECT COUNT(*) FROM user_job_decisions d WHERE d.user_id = u.id AND d.applied = 1) as applications,
                      (SELECT COUNT(*) FROM user_job_decisions d WHERE d.user_id = u.id AND d.apply_method = 'email_failed') as failures
               FROM users u
               LEFT JOIN profiles p ON p.user_id = u.id
               ORDER BY u.created_at DESC"""
        ).fetchall()
    return jsonify({"users": [dict(r) for r in users]})


@admin_bp.route("/api/admin/user/<int:user_id>/applications")
@admin_required
def api_admin_user_applications(user_id):
    with db_session() as conn:
        rows = conn.execute(
            """SELECT ujd.*, jp.title, jp.company, jp.url
               FROM user_job_decisions ujd
               JOIN job_postings jp ON jp.id = ujd.job_posting_id
               WHERE ujd.user_id = ? AND ujd.applied = 1
               ORDER BY ujd.applied_at DESC""",
            (user_id,),
        ).fetchall()
    return jsonify({"rows": [dict(r) for r in rows]})


@admin_bp.route("/api/admin/refresh_pool", methods=["POST"])
@admin_required
def api_admin_refresh_pool():
    try:
        result = job_pool.refresh_job_pool()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
