"""
LinkedIn Job Alert -> Telegram

Checks LinkedIn's public ("guest") job search for new postings matching
your keywords/location, and pushes new ones to you on Telegram.

Designed to run on a schedule via GitHub Actions (see .github/workflows/job_alert.yml)
so it keeps working even when your PC is off.

Config (edit the constants below to change keywords / location / radius):
"""

import os
import sys
import json
import time
import html
import requests
from bs4 import BeautifulSoup

# ----------------------------- CONFIG -----------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

SEEN_FILE = "seen_jobs.json"

# Treated as OR: a job is interesting if it matches ANY of these keywords
KEYWORDS = ["Fresh Graduate", "Network Engineer", "Trainee", "Programme"]

# LinkedIn's "distance" filter only accepts fixed steps (5/10/25/50/75/100 miles).
# 25 miles (~40km) is the closest standard option to your ~50km ask, centered on KL,
# which covers most of the Klang Valley (PJ, Shah Alam, Subang, Klang, Cyberjaya, Putrajaya, etc).
LOCATION = "Kuala Lumpur, Malaysia"
DISTANCE_MILES = 25

# Look-back window per run. Wider than the run interval so we never miss a
# posting that slipped in right at the edge of two scheduled runs. Duplicate
# postings across runs are filtered out via seen_jobs.json.
TIME_WINDOW_SECONDS = 3 * 60 * 60  # 3 hours

RESULTS_PER_KEYWORD = 25

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}

# --------------------------------------------------------------------


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f).get("seen_ids", []))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def save_seen(seen_ids):
    # Keep the file from growing forever - retain the most recent 1000 ids
    trimmed = list(seen_ids)[-1000:]
    with open(SEEN_FILE, "w") as f:
        json.dump({"seen_ids": trimmed}, f, indent=2)


def fetch_jobs(keyword):
    url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    params = {
        "keywords": keyword,
        "location": LOCATION,
        "distance": DISTANCE_MILES,
        "f_TPR": f"r{TIME_WINDOW_SECONDS}",
        "start": 0,
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[warn] request failed for '{keyword}': {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.find_all("li")
    jobs = []

    for card in cards[:RESULTS_PER_KEYWORD]:
        try:
            base = card.find("div", class_="base-card")
            job_id = (base.get("data-entity-urn", "") if base else "").split(":")[-1]

            title_tag = card.find("h3", class_="base-search-card__title")
            company_tag = card.find("h4", class_="base-search-card__subtitle")
            location_tag = card.find("span", class_="job-search-card__location")
            link_tag = card.find("a", class_="base-card__full-link")
            posted_tag = card.find("time")

            if not job_id or not link_tag or not title_tag:
                continue

            jobs.append({
                "id": job_id,
                "title": title_tag.get_text(strip=True),
                "company": company_tag.get_text(strip=True) if company_tag else "Unknown",
                "location": location_tag.get_text(strip=True) if location_tag else "Unknown",
                "link": link_tag["href"].split("?")[0],
                "posted": posted_tag.get_text(strip=True) if posted_tag else "recently",
                "matched_keyword": keyword,
            })
        except AttributeError:
            continue

    return jobs


def is_relevant(job):
    loc = job["location"].lower()

    # You want physical roles, not remote
    if "remote" in loc:
        return False

    # Malaysia-only safety net (location filter already targets KL/Selangor,
    # this just guards against odd cross-border results)
    blocked_countries = ["singapore", "indonesia", "thailand", "philippines"]
    if any(c in loc for c in blocked_countries):
        return False

    return True


def send_telegram(job):
    text = (
        f"\U0001F195 <b>{html.escape(job['title'])}</b>\n"
        f"\U0001F3E2 {html.escape(job['company'])}\n"
        f"\U0001F4CD {html.escape(job['location'])}\n"
        f"\U0001F552 Posted: {html.escape(job['posted'])}\n"
        f"\U0001F50E Matched keyword: {html.escape(job['matched_keyword'])}\n"
        f'<a href="{job["link"]}">Apply / View on LinkedIn</a>'
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, data=payload, timeout=15)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"[warn] telegram send failed: {e}")


def send_test_message():
    test_job = {
        "title": "Setup check \u2705",
        "company": "LinkedIn Job Alert Bot",
        "location": LOCATION,
        "posted": "just now",
        "matched_keyword": "this is a test, not a real job",
        "link": "https://www.linkedin.com/jobs/",
    }
    send_telegram(test_job)
    print("Test message sent. Check Telegram.")


def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[error] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars are missing.")
        sys.exit(1)

    if "--test" in sys.argv:
        send_test_message()
        return

    seen = load_seen()
    new_seen = set(seen)
    new_jobs = []

    for kw in KEYWORDS:
        jobs = fetch_jobs(kw)
        time.sleep(2)  # be polite between requests
        for job in jobs:
            if job["id"] in seen or job["id"] in new_seen:
                continue
            if not is_relevant(job):
                continue
            new_jobs.append(job)
            new_seen.add(job["id"])

    print(f"Found {len(new_jobs)} new matching job(s).")
    for job in new_jobs:
        send_telegram(job)
        time.sleep(1)

    save_seen(new_seen)


if __name__ == "__main__":
    main()
