"""
search_jobs.py
Pulls raw job postings from Adzuna (real search API, needs free key) and
RemoteOK (open public API, no key needed). Returns a normalized list of
job dicts so the rest of the pipeline doesn't care which source they came from.

Normalized job dict shape:
{
  "source": "adzuna" | "remoteok",
  "id": str,                # stable unique id (prefixed by source)
  "title": str,
  "company": str,
  "location": str,
  "remote": bool,
  "url": str,                # link to the posting (apply-via-manual-click)
  "description": str,
  "posted_date": str | None,
  "salary_min": float | None,
  "salary_max": float | None,
  "apply_email": str | None  # only set if the posting itself lists one
}
"""

import requests
import re
import time
import html as html_module

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
REMOTEOK_URL = "https://remoteok.com/api"

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
HTML_TAG_RE = re.compile(r"<[^>]+>")


def _fix_mojibake(text):
    """
    Repairs the common 'â€"' style artifacts that show up when UTF-8 text
    (e.g. an em dash) gets misread as Latin-1 somewhere upstream.
    """
    if not text or "â" not in text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def _clean_html(raw):
    """RemoteOK descriptions come as raw HTML - strip tags and decode entities
    so descriptions are readable in the dashboard and score cleanly."""
    if not raw:
        return ""
    text = HTML_TAG_RE.sub(" ", raw)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _fix_mojibake(text)
    return text


def search_adzuna(query, country, app_id, app_key, results_per_query=20, where=None, max_pages=2):
    """Search one Adzuna country index for a query string."""
    jobs = []
    for page in range(1, max_pages + 1):
        url = ADZUNA_BASE.format(country=country, page=page)
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": results_per_query,
            "what": query,
            "content-type": "application/json",
        }
        if where:
            params["where"] = where
        try:
            resp = requests.get(url, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"[adzuna] request failed for '{query}' ({country}): {e}")
            break

        results = data.get("results", [])
        if not results:
            break

        for r in results:
            desc = r.get("description", "") or ""
            email_match = EMAIL_RE.search(desc)
            jobs.append({
                "source": "adzuna",
                "id": f"adzuna_{r.get('id')}",
                "title": r.get("title", "").strip(),
                "company": (r.get("company") or {}).get("display_name", "Unknown"),
                "location": (r.get("location") or {}).get("display_name", ""),
                "remote": "remote" in desc.lower() or "remote" in (r.get("title") or "").lower(),
                "url": r.get("redirect_url"),
                "description": desc,
                "posted_date": r.get("created"),
                "salary_min": r.get("salary_min"),
                "salary_max": r.get("salary_max"),
                "apply_email": email_match.group(0) if email_match else None,
            })

        if len(results) < results_per_query:
            break  # last page
        time.sleep(0.5)  # be polite to the API

    return jobs


def search_remoteok(query):
    """RemoteOK has one firehose endpoint; we filter client-side by query."""
    try:
        resp = requests.get(REMOTEOK_URL, headers={"User-Agent": "job-search-bot/1.0"}, timeout=20)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        print(f"[remoteok] request failed: {e}")
        return []

    jobs = []
    query_terms = query.lower().split()
    for r in data:
        if not isinstance(r, dict) or "position" not in r:
            continue  # first element is a legal-notice object, skip it
        desc = _clean_html(r.get("description", "") or "")
        haystack = " ".join([
            r.get("position", ""), r.get("company", ""),
            " ".join(r.get("tags", []) or []), desc
        ]).lower()
        if not any(term in haystack for term in query_terms):
            continue

        email_match = EMAIL_RE.search(desc)
        jobs.append({
            "source": "remoteok",
            "id": f"remoteok_{r.get('id')}",
            "title": r.get("position", "").strip(),
            "company": r.get("company", "Unknown"),
            "location": r.get("location") or "Remote",
            "remote": True,
            "url": r.get("url") or f"https://remoteok.com/remote-jobs/{r.get('id')}",
            "description": desc,
            "posted_date": r.get("date"),
            "salary_min": r.get("salary_min"),
            "salary_max": r.get("salary_max"),
            "apply_email": email_match.group(0) if email_match else None,
        })
    return jobs


def search_all(profile, config):
    """Run every target title against every enabled source, dedupe by id."""
    all_jobs = {}

    adzuna_cfg = config.get("adzuna", {})
    if adzuna_cfg.get("app_id") and adzuna_cfg.get("app_id") != "YOUR_ADZUNA_APP_ID":
        for country in adzuna_cfg.get("countries", ["in"]):
            for title in profile["target_titles"]:
                jobs = search_adzuna(
                    query=title,
                    country=country,
                    app_id=adzuna_cfg["app_id"],
                    app_key=adzuna_cfg["app_key"],
                    results_per_query=adzuna_cfg.get("results_per_query", 20),
                )
                for j in jobs:
                    all_jobs[j["id"]] = j
    else:
        print("[search_all] Adzuna not configured (add app_id/app_key to config.yaml) - skipping.")

    if config.get("remoteok", {}).get("enabled", True):
        # RemoteOK firehose is small enough to just query a couple of broad terms
        for title in ["customer success", "account management", "customer success manager"]:
            jobs = search_remoteok(title)
            for j in jobs:
                all_jobs[j["id"]] = j

    return list(all_jobs.values())
