# Job Search Console — Public Web Version

A multi-user version of the local job-search tool: anyone visits a URL,
signs up, uploads their resume, and gets automated job search + ATS-scored
resume tailoring + application tracking - no local install required.
You get a separate **admin panel** to monitor everyone using it.

This is a genuinely different kind of project from the local desktop tool
(if you also have that version) - this one needs actual hosting, a
database, and real security practices around other people's credentials.
This README explains both how it works and how to actually put it online.

## How it solves the "one Adzuna key, many users" problem

Individual users never touch Adzuna directly, and the key is never sent to
any browser - it lives only in your server's environment variables.

More importantly: the app doesn't call Adzuna once per user search. Instead:
- There's ONE **shared pool** of job postings (`job_postings` table)
- An **admin action** (or a scheduled job, see below) refreshes that pool -
  this is the only thing that calls Adzuna/RemoteOK
- When a user clicks "Search," the app just **scores the existing pool**
  against their profile - pure local computation, zero API calls

So your API usage scales with how often you refresh the pool, not with
how many users are active. Ten people searching ten times a day still
costs zero extra Adzuna calls beyond your refresh schedule.

## Architecture

- **Flask** app, three blueprints: `auth` (signup/login), main `app.py`
  (per-user dashboard), `admin` (your monitoring panel)
- **SQLite** via Python's built-in `sqlite3` (no extra dependency). Fine
  for friends-and-testing scale. For real production with concurrent
  writers, migrate to Postgres (see below) - the SQL is deliberately plain
  to make that migration straightforward later.
- **Resumes stored as database blobs**, not files on disk - this means the
  app works on hosting platforms with ephemeral filesystems (most free
  tiers) without needing a persistent disk.
- **Gmail app passwords encrypted at rest** (Fernet symmetric encryption,
  key from `ENCRYPTION_KEY` env var - never hardcoded, never in git).
- **Sessions** are signed cookies (Flask's built-in session), keyed by
  `SECRET_KEY`.

## Local testing before you deploy

```bash
cd jobbot-web
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env`:
- Set `ADMIN_EMAIL` to the email you'll sign up with (that account
  automatically becomes admin)
- Generate an `ENCRYPTION_KEY`:
  ```bash
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  ```
- Set a random `SECRET_KEY` (any long random string)
- Adzuna keys optional (RemoteOK works with none)

Load the env vars and run:
```bash
export $(cat .env | grep -v '^#' | xargs)   # macOS/Linux
python app.py
```
On Windows PowerShell, set each variable individually:
```powershell
$env:ADMIN_EMAIL="you@example.com"
$env:ENCRYPTION_KEY="..."
$env:SECRET_KEY="..."
python app.py
```

Open http://127.0.0.1:5000, sign up with your `ADMIN_EMAIL` address first
(so your account is admin), then visit `/admin` to trigger the first job
pool refresh - without that, users will search against an empty pool.

## Deploying it publicly (Render.com example)

Render's free tier is one of the simpler options for this. Steps:

1. Push this folder to a GitHub repo.
2. On [render.com](https://render.com), **New → Web Service**, connect the repo.
3. Build command: `pip install -r requirements.txt`
   Start command: `gunicorn app:app`
4. Add environment variables in Render's dashboard (Settings → Environment):
   `SECRET_KEY`, `ADMIN_EMAIL`, `ENCRYPTION_KEY`, `ADZUNA_APP_ID`,
   `ADZUNA_APP_KEY` (optional), `DATABASE_PATH`.
5. **Persistence matters here**: Render's free tier filesystem is
   ephemeral - it resets on every deploy/restart, which would wipe your
   SQLite file and everyone's data. Two options:
   - Add a Render **persistent disk** (paid, but cheap) mounted at e.g.
     `/data`, and set `DATABASE_PATH=/data/jobbot.db`
   - Or migrate to Render's **free Postgres** addon for real persistence
     without paying for a disk (see migration note below)
6. Deploy. Sign up with your `ADMIN_EMAIL` address, go to `/admin`, click
   "Refresh job pool now" once to seed it.
7. Share the URL with friends.

Railway and Fly.io work similarly - the core requirement is the same:
whatever platform you pick, make sure the database persists across
restarts, or you'll lose all user data on every deploy.

### Migrating from SQLite to Postgres later

`db.py` is the only file that touches the database directly. To move to
Postgres: swap `sqlite3` for `psycopg2` (or `psycopg`), adjust the
connection function in `db.py`, and change the schema's `AUTOINCREMENT` to
`SERIAL`/`GENERATED ALWAYS AS IDENTITY`. Every other file just calls
`db_session()` / runs SQL through the connection, so this is a contained
change.

### Scheduling automatic pool refreshes (instead of manual admin clicks)

The simplest approach: use your hosting platform's cron/scheduled-job
feature (Render has "Cron Jobs," Railway has scheduled deployments) to hit
a refresh endpoint periodically. Since `/api/admin/refresh_pool` requires
an admin session, either:
- add a separate endpoint protected by a shared secret token instead of a
  login session, meant only for the scheduler to call, or
- run `python -c "from job_pool import refresh_job_pool; refresh_job_pool()"`
  directly as the scheduled job's command (bypasses the web layer
  entirely, simplest option)

## Admin panel (`/admin`)

Your `ADMIN_EMAIL` account gets a link to this in the header. It shows:
- Total users, total applications, emails sent, **email failures** (so you
  can spot and help anyone whose Gmail sending is broken)
- Shared job pool size and last refresh time, with a manual refresh button
- Every user, their setup status, application count, and failure count -
  click any user to see their full application history

## Security notes worth understanding, not just trusting

- Gmail app passwords are encrypted before storage, but whoever controls
  your server's `ENCRYPTION_KEY` and database can still decrypt them in
  principle (that's true of essentially any app storing credentials
  server-side). Be thoughtful about who has server access.
- There's no rate limiting on signup/login yet - for a small friends group
  this is fine; before wider public launch, add basic rate limiting
  (Flask-Limiter is a common choice) to reduce brute-force/spam risk.
- No email verification on signup - anyone can sign up with any email
  address (they just won't receive anything sent to it unless it's really
  theirs). Fine for friends testing; add verification before wider launch.
- `MAX_CONTENT_LENGTH` is capped at 8MB to prevent oversized upload abuse.

## What's NOT in this version (compared to the local tool)

- No `.eml` draft files saved to disk for review-before-send - since
  there's no persistent per-user filesystem here, "don't auto-send"
  currently just means the application is marked ready with the cover
  letter stored in the database, without an actual send attempt. This is
  a reasonable place to add a "download draft email" feature if wanted.
- No command-line/cron equivalent (`generate_review.py`,
  `apply_approved.py`) - everything runs through the web app now.

## Files

- `app.py` — main Flask app, per-user dashboard routes
- `auth.py` — signup/login/logout, session management
- `admin.py` — admin-only routes
- `db.py` — SQLite schema + connection helper
- `crypto_utils.py` — Gmail app password encryption
- `job_pool.py` — shared job pool refresh (the Adzuna-quota fix)
- `matching.py` — per-user scoring against the shared pool
- `applying.py` — ATS check, resume tailoring, cover letter, email send
- `profile_builder.py` — resume parsing/profile suggestions (bytes-based)
- `resume_tailor.py` — keyword-distribution resume editing (bytes-based)
- `ats_score.py`, `match_engine.py`, `search_jobs.py` — unchanged core logic
- `templates/` — login, signup, dashboard, admin pages
