"""
LinkedIn + JobStreet Job Alert -> Telegram

Checks LinkedIn and JobStreet for new postings matching your keywords/location,
and pushes new ones to Telegram. Runs on GitHub Actions on a schedule.
"""

import os
import sys
import json
import time
import html
import re
import requests
from bs4 import BeautifulSoup

# ----------------------------- CONFIG -----------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

SEEN_FILE = "seen_jobs.json"

KEYWORDS = [
    # Core network titles
    "Network Engineer",
    "Network Administrator",
    "Network Support Engineer",
    "Network Operations",
    "NOC Engineer",

    # Alternative names for the same role
    "Datacomm Engineer",
    "Data Communication",
    "Infrastructure Engineer",
    "IT Infrastructure",
    "Network Infrastructure",
    "Systems Engineer",
    "Platform Engineer",
    "Field Engineer",
    "Transmission Engineer",
    "IP Engineer",
    "Pre-Sales Engineer",         # common at Huawei, Cisco partners
    "ICT Engineer",
    "ICT Officer",

    # Data center adjacent
    "Data Center Engineer",
    "DC Operations",

    # Security (your existing ones, kept)
    "Cybersecurity",
    "Cyber Security",
    "SOC Analyst",
    "Security Analyst",
    "Information Security",
    "Network Security",
    "IT Security",

    # Fresh grad entry points
    "Fresh Graduate",
    "Graduate Trainee",
    "Graduate Programme",
    "Management Trainee",
    "Associate Engineer",
]
# LinkedIn uses a single centre-point + radius.
# JobStreet searches KL and Selangor separately and merges results.
LINKEDIN_LOCATION = "Kuala Lumpur, Malaysia"
JOBSTREET_LOCATIONS = ["Kuala Lumpur", "Selangor"]
DISTANCE_MILES = 25  # ~40km, closest LinkedIn option to your 50km ask

TIME_WINDOW_SECONDS = 3 * 60 * 60  # 3 hours look-back per run

RESULTS_PER_KEYWORD = 25

# Titles containing any of these are silently skipped (case-insensitive)
EXCLUDE_TITLE_KEYWORDS = [
    # Seniority — you're not ready for these
    "senior", "sr.", "lead", "principal", "head of", "director",
    "manager", "supervisor", "vp", "vice president",
    "5+ years", "7+ years", "10+ years",

    # Wrong domains entirely
    "software", "frontend", "backend", "fullstack", "full stack",
    "mobile", "android", "ios", "flutter", "react", "devops",
    "data scientist", "data engineer", "machine learning engineer",
    "mechanical", "electrical", "civil", "structural", "quantity surveyor",
    "food", "kitchen", "chef", "barista", "crew", "cashier",
    "security guard", "security officer", "physical security",
    "marketing", "sales", "business development", "bd",
    "hr", "human resource", "recruitment", "talent acquisition",
    "credit", "loan", "mortgage", "insurance", "actuarial",
    "driver", "despatch", "cleaner", "housekeeping",
    "japanese", "mandarin", "korean", "arabic",
    "internship", "intern",
]

# Jobs from these companies get a ⭐ priority flag in the Telegram message.
# Add any new company here — no code change needed anywhere else.
TARGET_COMPANIES = [
    # Central bank / regulators
    "bank negara malaysia", "bnm",

    # Commercial banks
    "maybank", "malayan banking",
    "cimb", "cimb group",
    "public bank",
    "rhb", "rhb bank",
    "ambank", "ambank group",
    "hong leong bank", "hong leong",
    "alliance bank",
    "hsbc malaysia", "hsbc",
    "standard chartered",
    "affin bank",
    "mbsb", "mbsb bank berhad",
    "kenanga",

    # Islamic banks
    "bank islam",

    # Digital banks
    "tng digital", "tng",
    "gxbank",
    "ryt bank",
    "bigpay",
    "boost",

    # Payment infra
    "paynet",
    "setel",

    # GLCs / investment
    "khazanah",
    "pnb", "permodalan nasional berhad",

    # Telcos
    "maxis",
    "celcomdigi", "celcom",
    "telekom malaysia", "tm",
    "time dotcom", "time",
    "u mobile",

    # Network / security vendors
    "cisco",
    "huawei",
    "fortinet",
    "palo alto networks", "palo alto",
    "juniper networks", "juniper",
    "f5 networks",

    # Cloud / big tech (aspirational)
    "microsoft",
    "google",
    "amazon", "aws",
    "dell technologies", "dell",
    "hpe", "hewlett packard enterprise",
    "amd",

    # System integrators / MSPs
    "accenture",
    "bridgenet solutions sdn bhd", "bridgenet",
    "ntt malaysia", "ntt",
    "dimension data",
    "dxc technology", "dxc",
    "ibm malaysia", "ibm",
    "avanade",

    # Government cybersecurity agencies
    "cybersecurity malaysia",
    "nacsa",
    "mdec",
    "mcmc",

    # Data centers
    "aims data centre", "aims",

    # Fintech / ride-hailing infra
    "grabpay", "grab",
    "slb", "schlumberger",

    # Automotive (low priority)
    "mercedes-benz malaysia",
]

# JobStreet salary filter:
#   - salary shown AND below this -> skip
#   - no salary shown             -> post (you decide)
MIN_SALARY_MYR = 3500

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# -----------------------------------------------------------------


def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f).get("seen_ids", []))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def save_seen(seen_ids):
    trimmed = list(seen_ids)[-1000:]
    with open(SEEN_FILE, "w") as f:
        json.dump({"seen_ids": trimmed}, f, indent=2)


def parse_salary_myr(salary_text):
    """
    Try to extract the lower-bound monthly salary from a freeform string.
    Returns an int (MYR/month) or None if unparseable.
    Examples handled:
        "MYR 3,000 - MYR 5,000 per month"  -> 3000
        "RM3500/month"                       -> 3500
        "MYR 42,000 per year"               -> 3500
    """
    if not salary_text:
        return None
    text = salary_text.replace(",", "").upper()
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    if not numbers:
        return None
    lower = numbers[0]
    # Rough annual-to-monthly conversion
    if "YEAR" in text or "ANNUAL" in text or lower > 50000:
        lower = lower // 12
    return lower


# ----------------------------------------------------------------- LinkedIn

def fetch_jobs_linkedin(keyword):
    url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    params = {
        "keywords": keyword,
        "location": LINKEDIN_LOCATION,
        "distance": DISTANCE_MILES,
        "f_TPR": f"r{TIME_WINDOW_SECONDS}",
        "start": 0,
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[warn] LinkedIn request failed for '{keyword}': {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    jobs = []

    for card in soup.find_all("li")[:RESULTS_PER_KEYWORD]:
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
                "id": f"li_{job_id}",
                "title": title_tag.get_text(strip=True),
                "company": company_tag.get_text(strip=True) if company_tag else "Unknown",
                "location": location_tag.get_text(strip=True) if location_tag else "Unknown",
                "salary_text": "",
                "link": link_tag["href"].split("?")[0],
                "posted": posted_tag.get_text(strip=True) if posted_tag else "recently",
                "matched_keyword": keyword,
                "source": "LinkedIn",
            })
        except AttributeError:
            continue

    return jobs


# ----------------------------------------------------------------- JobStreet

def fetch_jobs_jobstreet(keyword):
    """
    Scrape JobStreet Malaysia public search results.
    Tries Next.js embedded JSON first, then falls back to HTML card parsing.
    Searches KL and Selangor separately and merges.
    """
    all_jobs = []
    seen_in_call = set()

    for loc in JOBSTREET_LOCATIONS:
        params = {
            "q": keyword,
            "l": loc,
            "daterange": "3",
        }
        try:
            resp = requests.get(
                "https://www.jobstreet.com.my/jobs",
                params=params,
                headers=HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[warn] JobStreet request failed for '{keyword}' in {loc}: {e}")
            time.sleep(2)
            continue

        soup = BeautifulSoup(resp.text, "html.parser")
        parsed = False

        # --- Attempt 1: Next.js __NEXT_DATA__ JSON ---
        next_tag = soup.find("script", id="__NEXT_DATA__")
        if next_tag:
            try:
                data = json.loads(next_tag.string)
                page_props = data.get("props", {}).get("pageProps", {})

                # Try several known paths the JSON might use
                jobs_raw = (
                    page_props.get("jobDetails")
                    or page_props.get("initialData", {}).get("jobs")
                    or page_props.get("searchResult", {}).get("jobs")
                    or []
                )

                for j in jobs_raw:
                    try:
                        raw_id = j.get("id") or j.get("jobId") or ""
                        job_id = f"js_{raw_id}"
                        if not raw_id or job_id in seen_in_call:
                            continue

                        advertiser = j.get("advertiser") or {}
                        salary_text = (
                            j.get("salary")
                            or j.get("salaryLabel")
                            or j.get("salaryDetails")
                            or ""
                        )
                        seen_in_call.add(job_id)
                        all_jobs.append({
                            "id": job_id,
                            "title": j.get("title") or j.get("jobTitle") or "",
                            "company": advertiser.get("description") or j.get("companyName") or "Unknown",
                            "location": j.get("suburb") or j.get("location") or loc,
                            "salary_text": salary_text,
                            "link": f"https://www.jobstreet.com.my/job/{raw_id}",
                            "posted": j.get("listingDate") or "recently",
                            "matched_keyword": keyword,
                            "source": "JobStreet",
                        })
                        parsed = True
                    except (KeyError, TypeError):
                        continue
            except (json.JSONDecodeError, AttributeError):
                pass

        # --- Attempt 2: HTML card fallback ---
        if not parsed:
            for card in soup.select("article[data-job-id], div[data-job-id]"):
                try:
                    raw_id = card.get("data-job-id", "")
                    job_id = f"js_{raw_id}"
                    if not raw_id or job_id in seen_in_call:
                        continue

                    title_el = card.select_one("h1,h2,h3,[data-automation='jobTitle']")
                    company_el = card.select_one("[data-automation='jobCompany']")
                    loc_el = card.select_one("[data-automation='jobLocation']")
                    salary_el = card.select_one("[data-automation='jobSalary']")
                    link_el = card.select_one("a[href*='/job/']")

                    seen_in_call.add(job_id)
                    all_jobs.append({
                        "id": job_id,
                        "title": title_el.get_text(strip=True) if title_el else "",
                        "company": company_el.get_text(strip=True) if company_el else "Unknown",
                        "location": loc_el.get_text(strip=True) if loc_el else loc,
                        "salary_text": salary_el.get_text(strip=True) if salary_el else "",
                        "link": "https://www.jobstreet.com.my" + link_el["href"] if link_el else "",
                        "posted": "recently",
                        "matched_keyword": keyword,
                        "source": "JobStreet",
                    })
                except (AttributeError, KeyError):
                    continue

        time.sleep(2)

    return all_jobs


# ----------------------------------------------------------------- Filters

def is_relevant(job):
    title = job["title"].lower()
    loc = job["location"].lower()

    # Exclude seniority/experience keywords from title
    if any(kw in title for kw in EXCLUDE_TITLE_KEYWORDS):
        return False

    # Physical roles only
    if "remote" in loc:
        return False

    # Malaysia only
    if any(c in loc for c in ["singapore", "indonesia", "thailand", "philippines"]):
        return False

    # JobStreet salary filter: skip ONLY if salary explicitly shown AND below threshold
    salary_text = job.get("salary_text", "")
    if salary_text:
        lower_bound = parse_salary_myr(salary_text)
        if lower_bound is not None and lower_bound < MIN_SALARY_MYR:
            return False

    return True


# ----------------------------------------------------------------- Telegram

def send_telegram(job):
    source = job.get("source", "LinkedIn")
    icon = "\U0001F535" if source == "LinkedIn" else "\U0001F7E1"  # 🔵 🟡

    # Priority flag if company matches your target list
    company_lower = job.get("company", "").lower()
    is_priority = any(tc in company_lower for tc in TARGET_COMPANIES)
    priority_tag = "\u2B50 <b>PRIORITY COMPANY</b>\n" if is_priority else ""

    salary_line = ""
    if job.get("salary_text"):
        salary_line = f"\U0001F4B0 {html.escape(job['salary_text'])}\n"

    text = (
        f"{priority_tag}{icon} <b>[{source}]</b>  \U0001F195 <b>{html.escape(job['title'])}</b>\n"
        f"{icon} <b>[{source}]</b>  \U0001F195 <b>{html.escape(job['title'])}</b>\n"
        f"\U0001F3E2 {html.escape(job['company'])}\n"
        f"\U0001F4CD {html.escape(job['location'])}\n"
        f"{salary_line}"
        f"\U0001F552 Posted: {html.escape(job['posted'])}\n"
        f"\U0001F50E Keyword: {html.escape(job['matched_keyword'])}\n"
        f'<a href="{job["link"]}">Apply / View</a>'
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
        print(f"[warn] Telegram send failed: {e}")


def send_test_message():
    send_telegram({
        "title": "Setup check \u2705",
        "company": "Job Alert Bot",
        "location": "Kuala Lumpur, Malaysia",
        "salary_text": "MYR 4,000 - MYR 6,000",
        "posted": "just now",
        "matched_keyword": "this is a test — not a real job",
        "link": "https://www.linkedin.com/jobs/",
        "source": "LinkedIn",
    })
    send_telegram({
        "title": "Setup check \u2705",
        "company": "Job Alert Bot",
        "location": "Selangor, Malaysia",
        "salary_text": "",
        "posted": "just now",
        "matched_keyword": "this is a test — not a real job",
        "link": "https://www.jobstreet.com.my/jobs",
        "source": "JobStreet",
    })
    print("Two test messages sent (one LinkedIn, one JobStreet). Check Telegram.")


# ----------------------------------------------------------------- Main

def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[error] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars missing.")
        sys.exit(1)

    if "--test" in sys.argv:
        send_test_message()
        return

    seen = load_seen()
    new_seen = set(seen)
    new_jobs = []

    for kw in KEYWORDS:
        # LinkedIn
        for job in fetch_jobs_linkedin(kw):
            if job["id"] not in seen and job["id"] not in new_seen and is_relevant(job):
                new_jobs.append(job)
                new_seen.add(job["id"])
        time.sleep(2)

        # JobStreet
        for job in fetch_jobs_jobstreet(kw):
            if job["id"] not in seen and job["id"] not in new_seen and is_relevant(job):
                new_jobs.append(job)
                new_seen.add(job["id"])
        time.sleep(2)

    print(f"Found {len(new_jobs)} new matching job(s).")
    for job in new_jobs:
        send_telegram(job)
        time.sleep(1)

    save_seen(new_seen)


if __name__ == "__main__":
    main()
