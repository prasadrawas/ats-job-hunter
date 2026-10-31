#!/usr/bin/env python3
"""Fetch job listings from LinkedIn's public jobs-guest endpoints.

WARNING: This script uses LinkedIn's public, unauthenticated job search endpoints
for personal/educational use only. LinkedIn's Terms of Service may restrict automated
access. Use responsibly, respect rate limits, and do not use for commercial scraping.

Endpoints (no auth required):
  Search: https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search
  Detail: https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}
"""

import argparse
import hashlib
import html
import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEARCH_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
)
DETAIL_URL = (
    "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# f_TPR values (time posted / recency filter)
RECENCY_MAP = {
    "1": "r86400",      # past 24 hours
    "7": "r604800",     # past week
    "14": "r1209600",   # past 2 weeks
    "30": "r2592000",   # past month
}

# f_WT values (work type)
WORK_TYPE_MAP = {
    "remote": "2",
    "onsite": "1",
    "hybrid": "3",
}

DEFAULT_LOCATIONS = [
    "San Francisco, CA",
    "New York, NY",
    "Remote",
]

MAX_RETRIES = 3
BACKOFF_BASE = 2  # seconds

CACHE_DIR = Path(os.environ.get(
    "LINKEDIN_CACHE_DIR",
    Path(__file__).resolve().parent.parent / "output" / ".linkedin_cache",
))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }


def clean_html(raw_html: str) -> str:
    """Strip HTML tags and unescape entities to plain text."""
    if not raw_html:
        return ""
    text = html.unescape(raw_html)
    # Replace block-level elements with newlines for readability
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(p|div|li|tr|h[1-6])>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _rate_sleep():
    """Sleep 1-2 seconds between requests to be polite."""
    time.sleep(1.0 + random.random())


def _request_with_retry(url: str, params: dict | None = None) -> requests.Response | None:
    """GET with exponential backoff on 429/5xx. Returns None on failure."""
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, headers=_headers(), timeout=30)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = BACKOFF_BASE ** (attempt + 1) + random.random()
                print(f"    HTTP {resp.status_code} — retrying in {wait:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            # Other client errors: give up
            print(f"    HTTP {resp.status_code} for {url[:80]}...")
            return None
        except requests.RequestException as exc:
            wait = BACKOFF_BASE ** (attempt + 1) + random.random()
            print(f"    Request error: {exc} — retrying in {wait:.1f}s")
            time.sleep(wait)
    print(f"    Failed after {MAX_RETRIES} retries: {url[:80]}...")
    return None


# ---------------------------------------------------------------------------
# Cache helpers (6-hour TTL, matching project convention)
# ---------------------------------------------------------------------------

def _cache_key(url: str, params: dict | None = None) -> str:
    raw = url + json.dumps(params or {}, sort_keys=True)
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key: str, max_age_hours: float = 6.0) -> str | None:
    path = CACHE_DIR / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        cached_at = datetime.fromisoformat(data["cached_at"])
        age_h = (datetime.now(timezone.utc) - cached_at).total_seconds() / 3600
        if age_h < max_age_hours:
            return data["body"]
    except (json.JSONDecodeError, KeyError, ValueError):
        pass
    return None


def _cache_put(key: str, body: str):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"cached_at": datetime.now(timezone.utc).isoformat(), "body": body}
    (CACHE_DIR / f"{key}.json").write_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# Parse search results HTML
# ---------------------------------------------------------------------------

def parse_job_cards(html_text: str) -> list[dict]:
    """Extract job cards from LinkedIn search HTML.

    Each card is inside a <li> with class containing 'jobs-search__results-list'
    or a <div class="base-card">.  Key data attributes and nested elements:
      - data-entity-urn="urn:li:jobPosting:XXXXXXX"  -> job id
      - <a class="base-card__full-link" href="...">   -> url
      - <h3 class="base-search-card__title">           -> title
      - <h4 class="base-search-card__subtitle">        -> company
      - <span class="job-search-card__location">        -> location
      - <time datetime="...">                           -> date
    """
    jobs = []

    # Split on base-card boundaries
    cards = re.split(r'<(?:li|div)[^>]*class="[^"]*base-card[^"]*"', html_text)

    for card in cards[1:]:  # skip preamble before first card
        job: dict = {}

        # Job ID from entity URN
        m = re.search(r'data-entity-urn="urn:li:jobPosting:(\d+)"', card)
        if not m:
            # Fallback: extract from href
            m = re.search(r'/jobs/view/(\d+)', card)
        if m:
            job["id"] = m.group(1)
        else:
            continue  # skip card without an ID

        # URL
        m = re.search(r'href="(https?://[^"]*?/jobs/view/[^"]*)"', card)
        if m:
            job["url"] = m.group(1).split("?")[0]
        else:
            job["url"] = f"https://www.linkedin.com/jobs/view/{job['id']}"

        # Title
        m = re.search(r'base-search-card__title[^>]*>(.*?)</(?:h3|span|a)', card, re.DOTALL)
        if m:
            job["title"] = clean_html(m.group(1)).strip()
        else:
            job["title"] = ""

        # Company
        m = re.search(r'base-search-card__subtitle[^>]*>(.*?)</(?:h4|span|a)', card, re.DOTALL)
        if m:
            job["company_token"] = clean_html(m.group(1)).strip()
        else:
            job["company_token"] = ""

        # Location
        m = re.search(r'job-search-card__location[^>]*>(.*?)</span', card, re.DOTALL)
        if m:
            job["location"] = clean_html(m.group(1)).strip()
        else:
            job["location"] = ""

        # Date
        m = re.search(r'<time[^>]*datetime="([^"]*)"', card)
        if m:
            job["updated_at"] = m.group(1)
        else:
            job["updated_at"] = ""

        job["platform"] = "linkedin"
        job["department"] = ""
        job["description"] = ""  # filled later from detail page

        jobs.append(job)

    return jobs


# ---------------------------------------------------------------------------
# Fetch detail page for full description
# ---------------------------------------------------------------------------

def fetch_job_detail(job_id: str, cache_hours: float = 6.0) -> str:
    """Return plain-text job description from LinkedIn detail page."""
    url = DETAIL_URL.format(job_id=job_id)
    key = _cache_key(url)
    cached = _cache_get(key, cache_hours)
    if cached is not None:
        return cached

    _rate_sleep()
    resp = _request_with_retry(url)
    if resp is None:
        return ""

    body = resp.text

    # Extract the description section
    desc = ""
    m = re.search(
        r'class="(?:description__text|show-more-less-html__markup)[^"]*"[^>]*>(.*?)</(?:div|section)',
        body,
        re.DOTALL,
    )
    if m:
        desc = clean_html(m.group(1))
    else:
        # Broader fallback: look for any large content block
        m = re.search(r'<div[^>]*class="[^"]*description[^"]*"[^>]*>(.*?)</div>', body, re.DOTALL)
        if m:
            desc = clean_html(m.group(1))

    _cache_put(key, desc)
    return desc


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search_linkedin_jobs(
    query: str,
    location: str = "Remote",
    work_type: str | None = None,
    recency_days: str | None = None,
    limit: int = 25,
    cache_hours: float = 6.0,
) -> list[dict]:
    """Search LinkedIn and return list of job dicts (without full descriptions)."""

    params: dict = {
        "keywords": query,
        "location": location,
    }
    if recency_days and recency_days in RECENCY_MAP:
        params["f_TPR"] = RECENCY_MAP[recency_days]
    if work_type and work_type.lower() in WORK_TYPE_MAP:
        params["f_WT"] = WORK_TYPE_MAP[work_type.lower()]

    all_cards: list[dict] = []
    page_size = 25  # LinkedIn returns ~25 per page
    offset = 0

    while len(all_cards) < limit:
        params["start"] = str(offset)
        cache_key = _cache_key(SEARCH_URL, params)
        cached_html = _cache_get(cache_key, cache_hours)

        if cached_html is not None:
            html_text = cached_html
        else:
            _rate_sleep()
            resp = _request_with_retry(SEARCH_URL, params)
            if resp is None:
                break
            html_text = resp.text
            _cache_put(cache_key, html_text)

        cards = parse_job_cards(html_text)
        if not cards:
            break  # no more results

        all_cards.extend(cards)
        offset += page_size

    return all_cards[:limit]


def fetch_details_parallel(
    jobs: list[dict],
    max_workers: int = 3,
    cache_hours: float = 6.0,
) -> list[dict]:
    """Fetch full descriptions in parallel (respecting rate limits)."""
    if not jobs:
        return jobs

    print(f"  Fetching {len(jobs)} job descriptions (max {max_workers} workers)...")

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(fetch_job_detail, job["id"], cache_hours): i
            for i, job in enumerate(jobs)
        }
        for future in as_completed(future_map):
            idx = future_map[future]
            try:
                jobs[idx]["description"] = future.result()
            except Exception as exc:
                print(f"    Detail fetch error for job {jobs[idx].get('id')}: {exc}")

    return jobs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_linkedin(
    query: str,
    locations: list[str] | None = None,
    work_type: str | None = None,
    recency_days: str | None = None,
    limit: int = 25,
    cache_hours: float = 6.0,
    detail_workers: int = 3,
    fetch_descriptions: bool = True,
) -> list[dict]:
    """High-level: search + detail fetch. Returns list of normalized job dicts."""
    if locations is None:
        locations = DEFAULT_LOCATIONS

    all_jobs: list[dict] = []

    for loc in locations:
        print(f"  LinkedIn search: '{query}' in {loc}")
        cards = search_linkedin_jobs(
            query=query,
            location=loc,
            work_type=work_type,
            recency_days=recency_days,
            limit=limit,
            cache_hours=cache_hours,
        )
        print(f"    Found {len(cards)} listings")
        all_jobs.extend(cards)

    # Deduplicate by job ID (same job may appear in multiple location searches)
    seen_ids: set[str] = set()
    unique: list[dict] = []
    for j in all_jobs:
        if j["id"] not in seen_ids:
            seen_ids.add(j["id"])
            unique.append(j)
    all_jobs = unique

    if fetch_descriptions:
        all_jobs = fetch_details_parallel(all_jobs, max_workers=detail_workers, cache_hours=cache_hours)

    return all_jobs


def main():
    parser = argparse.ArgumentParser(
        description="Fetch jobs from LinkedIn public endpoints (no auth required)"
    )
    parser.add_argument("--query", required=True, help="Search keywords (e.g. 'python developer')")
    parser.add_argument(
        "--location",
        nargs="+",
        default=None,
        help="Locations to search. Use multiple values for multi-city. Defaults to preferences or 'Remote'.",
    )
    parser.add_argument(
        "--remote",
        choices=["remote", "hybrid", "onsite"],
        default=None,
        help="Work type filter",
    )
    parser.add_argument(
        "--jobage",
        choices=["1", "7", "14", "30"],
        default=None,
        help="Max job age in days",
    )
    parser.add_argument("--limit", type=int, default=25, help="Max jobs per location (default: 25)")
    parser.add_argument("--output", default="output/linkedin_jobs.json", help="Output JSON path")
    parser.add_argument("--cache-hours", type=float, default=6.0, help="Cache TTL in hours (0 to disable)")
    parser.add_argument("--no-details", action="store_true", help="Skip fetching full descriptions")
    parser.add_argument("--workers", type=int, default=3, help="Parallel detail-fetch workers (default: 3)")
    args = parser.parse_args()

    print("LinkedIn Job Search")
    print("=" * 40)
    print(f"Query: {args.query}")
    print(f"Locations: {', '.join(args.location)}")
    if args.remote:
        print(f"Work type: {args.remote}")
    if args.jobage:
        print(f"Job age: last {args.jobage} day(s)")
    print()

    jobs = fetch_linkedin(
        query=args.query,
        locations=args.location,
        work_type=args.remote,
        recency_days=args.jobage,
        limit=args.limit,
        cache_hours=args.cache_hours,
        detail_workers=args.workers,
        fetch_descriptions=not args.no_details,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        "total": len(jobs),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "platform": "linkedin",
        "query": args.query,
        "locations": args.location,
        "jobs": jobs,
    }

    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\nTotal: {len(jobs)} jobs saved to {args.output}")


if __name__ == "__main__":
    main()
