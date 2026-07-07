#!/usr/bin/env python3
"""
Daily job alert: searches several job APIs for entry-level 3D environment
artist roles, filters against config.yaml, dedupes against seen_jobs.json,
and emails a digest of anything new.

Required environment variables (set as GitHub Actions secrets):
  GMAIL_ADDRESS        - the Gmail account that SENDS the email
  GMAIL_APP_PASSWORD   - a Gmail "app password" (not the normal password)
  RECIPIENT_EMAIL      - where the digest goes (can equal GMAIL_ADDRESS)
Optional (enables the Adzuna source):
  ADZUNA_APP_ID, ADZUNA_APP_KEY - free at https://developer.adzuna.com
"""

import html
import json
import os
import smtplib
import ssl
import sys
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent
SEEN_FILE = ROOT / "seen_jobs.json"
TIMEOUT = 20
HEADERS = {"User-Agent": "personal-job-alert-script/1.0"}


# ----------------------------------------------------------------------
# Filtering helpers
# ----------------------------------------------------------------------

def title_matches(title: str, cfg) -> bool:
    t = title.lower()
    if any(bad in t for bad in cfg["exclude_keywords"]):
        return False
    return any(good in t for good in cfg["include_keywords"])


def location_matches(location: str, remote: bool, cfg) -> bool:
    if remote and cfg.get("include_remote", True):
        return True
    loc = (location or "").lower()
    return any(want in loc for want in cfg["locations"])


# ----------------------------------------------------------------------
# Sources — each returns a list of dicts:
#   {id, title, company, location, url, source, remote(bool)}
# and appends to `errors` on failure instead of crashing the run.
# ----------------------------------------------------------------------

def fetch_adzuna(cfg, errors):
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        errors.append("Adzuna skipped (no ADZUNA_APP_ID / ADZUNA_APP_KEY set)")
        return []
    jobs = []
    country = cfg.get("adzuna_country", "us")
    for phrase in cfg.get("adzuna_searches", []):
        try:
            r = requests.get(
                f"https://api.adzuna.com/v1/api/jobs/{country}/search/1",
                params={
                    "app_id": app_id,
                    "app_key": app_key,
                    "what_phrase": phrase,
                    "where": cfg.get("adzuna_where", ""),
                    "distance": int(cfg.get("adzuna_distance_km", 80)),
                    "results_per_page": 50,
                    "max_days_old": int(cfg.get("adzuna_max_days_old", 30)),
                    "content-type": "application/json",
                },
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            for item in r.json().get("results", []):
                jobs.append({
                    "id": f"adzuna:{item.get('id')}",
                    "title": item.get("title", "").replace("<strong>", "").replace("</strong>", ""),
                    "company": (item.get("company") or {}).get("display_name", "Unknown"),
                    "location": (item.get("location") or {}).get("display_name", ""),
                    "url": item.get("redirect_url", ""),
                    "source": "Adzuna",
                    "remote": False,
                })
        except Exception as e:
            errors.append(f"Adzuna search '{phrase}' failed: {e}")
    return jobs


def fetch_remotive(cfg, errors):
    jobs = []
    for phrase in cfg.get("remotive_searches", []):
        try:
            r = requests.get(
                "https://remotive.com/api/remote-jobs",
                params={"search": phrase, "limit": 50},
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            for item in r.json().get("jobs", []):
                jobs.append({
                    "id": f"remotive:{item.get('id')}",
                    "title": item.get("title", ""),
                    "company": item.get("company_name", "Unknown"),
                    "location": item.get("candidate_required_location", "Remote"),
                    "url": item.get("url", ""),
                    "source": "Remotive (remote)",
                    "remote": True,
                })
        except Exception as e:
            errors.append(f"Remotive search '{phrase}' failed: {e}")
    return jobs


def fetch_greenhouse(cfg, errors):
    jobs = []
    for board in cfg.get("greenhouse_boards", []):
        try:
            r = requests.get(
                f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            if r.status_code == 404:
                errors.append(f"Greenhouse board '{board}' not found - fix token in config.yaml")
                continue
            r.raise_for_status()
            for item in r.json().get("jobs", []):
                loc = (item.get("location") or {}).get("name", "")
                jobs.append({
                    "id": f"greenhouse:{board}:{item.get('id')}",
                    "title": item.get("title", ""),
                    "company": board.replace("-", " ").title(),
                    "location": loc,
                    "url": item.get("absolute_url", ""),
                    "source": "Company board (Greenhouse)",
                    "remote": "remote" in loc.lower(),
                })
        except Exception as e:
            errors.append(f"Greenhouse board '{board}' failed: {e}")
    return jobs


def fetch_lever(cfg, errors):
    jobs = []
    for board in cfg.get("lever_boards", []):
        try:
            r = requests.get(
                f"https://api.lever.co/v0/postings/{board}?mode=json",
                headers=HEADERS,
                timeout=TIMEOUT,
            )
            if r.status_code == 404:
                errors.append(f"Lever board '{board}' not found - fix token in config.yaml")
                continue
            r.raise_for_status()
            for item in r.json():
                loc = (item.get("categories") or {}).get("location", "") or ""
                jobs.append({
                    "id": f"lever:{board}:{item.get('id')}",
                    "title": item.get("text", ""),
                    "company": board.replace("-", " ").title(),
                    "location": loc,
                    "url": item.get("hostedUrl", ""),
                    "source": "Company board (Lever)",
                    "remote": "remote" in loc.lower(),
                })
        except Exception as e:
            errors.append(f"Lever board '{board}' failed: {e}")
    return jobs


def fetch_jazzhr(cfg, errors):
    """JazzHR career pages ({token}.applytojob.com) are simple server-rendered
    HTML; extract posting links with a regex. No public JSON API."""
    import re
    jobs = []
    for board in cfg.get("jazzhr_boards", []) or []:
        token = board.get("token")
        if not token:
            continue
        company = board.get("company", token.title())
        loc = board.get("location_note", "")
        try:
            r = requests.get(f"https://{token}.applytojob.com/", headers=HEADERS, timeout=TIMEOUT)
            if r.status_code == 404:
                errors.append(f"JazzHR board '{token}' not found - fix token in config.yaml")
                continue
            r.raise_for_status()
            pattern = rf'href="(https://{re.escape(token)}\.applytojob\.com/apply/([A-Za-z0-9]+)[^"]*)"[^>]*>([^<]+)</a>'
            found = re.findall(pattern, r.text)
            if not found:
                errors.append(f"JazzHR board '{token}' returned no parseable postings - page layout may have changed")
            for url, job_id, title in found:
                title = title.strip()
                if not title or title.lower() in ("apply", "apply now", "learn more"):
                    continue
                jobs.append({
                    "id": f"jazzhr:{token}:{job_id}",
                    "title": title,
                    "company": company,
                    "location": loc,
                    "url": url,
                    "source": "Company board (JazzHR)",
                    "remote": "remote" in loc.lower(),
                })
        except Exception as e:
            errors.append(f"JazzHR board '{token}' failed: {e}")
    return jobs


# ----------------------------------------------------------------------
# Email
# ----------------------------------------------------------------------

def build_email(new_jobs, errors) -> str:
    parts = [
        "<div style='font-family:Arial,Helvetica,sans-serif;max-width:640px'>",
        f"<h2 style='margin-bottom:4px'>🎨 {len(new_jobs)} new job match"
        f"{'es' if len(new_jobs) != 1 else ''} — {date.today():%B %d, %Y}</h2>",
        "<p style='color:#555;margin-top:0'>Entry-level 3D / environment artist "
        "roles matching your keywords and locations.</p>",
    ]
    by_source = {}
    for j in new_jobs:
        by_source.setdefault(j["source"], []).append(j)
    for source, jobs in sorted(by_source.items()):
        parts.append(f"<h3 style='border-bottom:2px solid #1F4E79;color:#1F4E79;"
                     f"padding-bottom:2px'>{html.escape(source)}</h3>")
        for j in jobs:
            parts.append(
                "<p style='margin:10px 0'>"
                f"<a href='{html.escape(j['url'])}' style='font-size:15px;font-weight:bold'>"
                f"{html.escape(j['title'])}</a><br>"
                f"<span style='color:#333'>{html.escape(j['company'])}</span>"
                f" &nbsp;·&nbsp; <span style='color:#777'>{html.escape(j['location'] or 'Location unlisted')}</span>"
                "</p>"
            )
    if errors:
        parts.append("<hr><p style='color:#999;font-size:11px'>Source notes:<br>"
                     + "<br>".join(html.escape(e) for e in errors) + "</p>")
    parts.append("</div>")
    return "".join(parts)


def send_email(html_body: str, n_jobs: int):
    sender = os.environ["GMAIL_ADDRESS"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    recipient = os.environ.get("RECIPIENT_EMAIL", sender)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🎨 {n_jobs} new 3D artist job match{'es' if n_jobs != 1 else ''} — {date.today():%b %d}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.attach(MIMEText(html_body, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    cfg["include_keywords"] = [k.lower() for k in cfg["include_keywords"]]
    cfg["exclude_keywords"] = [k.lower() for k in cfg["exclude_keywords"]]
    cfg["locations"] = [k.lower() for k in cfg["locations"]]

    seen = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.exists() else set()

    errors = []
    all_jobs = (
        fetch_adzuna(cfg, errors)
        + fetch_remotive(cfg, errors)
        + fetch_greenhouse(cfg, errors)
        + fetch_lever(cfg, errors)
        + fetch_jazzhr(cfg, errors)
    )

    new_jobs, new_ids = [], []
    for j in all_jobs:
        if j["id"] in seen or not j.get("url"):
            continue
        if not title_matches(j["title"], cfg):
            continue
        if not location_matches(j["location"], j["remote"], cfg):
            continue
        seen.add(j["id"])
        new_ids.append(j["id"])
        new_jobs.append(j)

    print(f"Fetched {len(all_jobs)} postings; {len(new_jobs)} new matches.")
    for e in errors:
        print(f"  note: {e}")

    if new_jobs:
        send_email(build_email(new_jobs, errors), len(new_jobs))
        print("Digest email sent.")
    else:
        print("No new matches today - no email sent.")
        # To get an email even on empty days, uncomment:
        # send_email(build_email([], errors), 0)

    # Persist seen ids (keep the file from growing forever: cap at 5000)
    SEEN_FILE.write_text(json.dumps(sorted(seen)[-5000:], indent=0))


if __name__ == "__main__":
    try:
        main()
    except KeyError as e:
        print(f"Missing required environment variable: {e}", file=sys.stderr)
        sys.exit(1)
