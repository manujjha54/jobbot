"""
auth.py
Simple email+password authentication using Flask sessions (signed cookies)
and werkzeug's password hashing. No third-party auth service needed.

The first account created with an email matching the ADMIN_EMAIL env var
is automatically marked as admin - that's how you bootstrap your own admin
access without a separate promotion step.
"""

import os
import re
from functools import wraps

from flask import Blueprint, request, jsonify, session, render_template
from werkzeug.security import generate_password_hash, check_password_hash

from db import db_session

auth_bp = Blueprint("auth", __name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Please log in first."}), 401
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return jsonify({"error": "Please log in first."}), 401
        if not session.get("is_admin"):
            return jsonify({"error": "Admin access only."}), 403
        return f(*args, **kwargs)
    return wrapper


def current_user_id():
    return session.get("user_id")


@auth_bp.route("/login")
def login_page():
    return render_template("login.html")


@auth_bp.route("/signup")
def signup_page():
    return render_template("signup.html")


@auth_bp.route("/api/signup", methods=["POST"])
def api_signup():
    body = request.get_json(force=True)
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    name = (body.get("name") or "").strip()

    if not EMAIL_RE.match(email):
        return jsonify({"error": "Enter a valid email address."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    is_admin = 1 if (ADMIN_EMAIL and email == ADMIN_EMAIL) else 0

    with db_session() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return jsonify({"error": "An account with that email already exists - try logging in."}), 400

        cur = conn.execute(
            "INSERT INTO users (email, password_hash, name, is_admin) VALUES (?, ?, ?, ?)",
            (email, generate_password_hash(password), name, is_admin),
        )
        user_id = cur.lastrowid
        conn.execute("INSERT INTO profiles (user_id) VALUES (?)", (user_id,))

    session["user_id"] = user_id
    session["email"] = email
    session["is_admin"] = bool(is_admin)
    return jsonify({"ok": True, "is_admin": bool(is_admin)})


@auth_bp.route("/api/login", methods=["POST"])
def api_login():
    body = request.get_json(force=True)
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    with db_session() as conn:
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Incorrect email or password."}), 401

    is_admin = bool(user["is_admin"])
    # Auto-promote if the user's email matches ADMIN_EMAIL
    if ADMIN_EMAIL and user["email"].strip().lower() == ADMIN_EMAIL:
        is_admin = True
        with db_session() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (user["id"],))

    session["user_id"] = user["id"]
    session["email"] = user["email"]
    session["is_admin"] = is_admin
    return jsonify({"ok": True, "is_admin": is_admin})


@auth_bp.route("/api/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@auth_bp.route("/api/me")
def api_me():
    if not session.get("user_id"):
        return jsonify({"logged_in": False})
    return jsonify({
        "logged_in": True,
        "email": session.get("email"),
        "is_admin": bool(session.get("is_admin")),
    })
