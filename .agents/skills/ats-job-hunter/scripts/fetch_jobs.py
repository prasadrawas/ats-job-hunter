#!/usr/bin/env python3
"""Fetch open jobs from ATS platforms (Greenhouse, Lever, Ashby, LinkedIn) via public APIs.

Optimized: parallel fetching + caching to avoid redundant API calls.
"""

import argparse
import html
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

from fetch_linkedin import fetch_linkedin

load_dotenv()

ATS_ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{company}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{board_slug}",
}


def clean_html(raw_html: str) -> str:
    """Strip HTML tags and unescape HTML entities to plain text."""
    if not raw_html:
        return ""
    text = html.unescape(raw_html)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def load_companies(config_path: str) -> dict:
    config_file = Path(config_path)
    if not config_file.exists():
        print(f"Warning: Companies config not found at {config_path}")
        return {}
    with open(config_file) as f:
        return yaml.safe_load(f) or {}


def fetch_greenhouse_jobs(board_token: str) -> list[dict]:
    url = ATS_ENDPOINTS["greenhouse"].format(board_token=board_token)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "id": str(job["id"]),
                "title": job.get("title", ""),
                "location": job.get("location", {}).get("name", ""),
                "description": clean_html(job.get("content", "")),
                "url": job.get("absolute_url", ""),
                "department": (job.get("departments", [{}])[0].get("name", "")
                               if job.get("departments") else ""),
                "platform": "greenhouse",
                "company_token": board_token,
                "updated_at": job.get("updated_at", ""),
            }
            for job in data.get("jobs", [])
        ]
    except requests.RequestException as e:
        print(f"  Error fetching Greenhouse/{board_token}: {e}")
        return []


def fetch_lever_jobs(company: str) -> list[dict]:
    url = ATS_ENDPOINTS["lever"].format(company=company)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        postings = resp.json()
        return [
            {
                "id": posting.get("id", ""),
                "title": posting.get("text", ""),
                "location": posting.get("categories", {}).get("location", ""),
                "description": posting.get("descriptionPlain", ""),
                "url": posting.get("hostedUrl", ""),
                "department": posting.get("categories", {}).get("department", ""),
                "platform": "lever",
                "company_token": company,
                "updated_at": "",
            }
            for posting in postings
        ]
    except requests.RequestException as e:
        print(f"  Error fetching Lever/{company}: {e}")
        return []


def fetch_ashby_jobs(board_slug: str) -> list[dict]:
    url = ATS_ENDPOINTS["ashby"].format(board_slug=board_slug)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "id": str(job.get("id", "")),
                "title": job.get("title", ""),
                "location": job.get("location", ""),
                "description": job.get("descriptionPlain", job.get("description", "")),
                "url": job.get("jobUrl", ""),
                "department": job.get("departmentName", ""),
                "platform": "ashby",
                "company_token": board_slug,
                "updated_at": job.get("publishedAt", ""),
            }
            for job in data.get("jobs", [])
        ]
    except requests.RequestException as e:
        print(f"  Error fetching Ashby/{board_slug}: {e}")
        return []


FETCHERS = {
    "greenhouse": fetch_greenhouse_jobs,
    "lever": fetch_lever_jobs,
    "ashby": fetch_ashby_jobs,
}


def deduplicate_jobs(jobs: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for job in jobs:
        key = (job.get("title", "").lower().strip(), job.get("company_token", "").lower())
        if key not in seen:
            seen.add(key)
            unique.append(job)
    return unique


def is_cache_valid(output_path: str, max_age_hours: float) -> bool:
    """Check if cached jobs file is still fresh."""
    path = Path(output_path)
    if not path.exists():
        return False
    try:
        with open(path) as f:
            data = json.load(f)
        fetched_at = data.get("fetched_at", "")
        if not fetched_at:
            return False
        fetched_time = datetime.fromisoformat(fetched_at)
        age_hours = (datetime.now(timezone.utc) - fetched_time).total_seconds() / 3600
        return age_hours < max_age_hours
    except (json.JSONDecodeError, ValueError):
        return False


def main():
    parser = argparse.ArgumentParser(description="Fetch jobs from ATS platforms")
    parser.add_argument("--platforms", default="greenhouse,lever,ashby")
    parser.add_argument("--companies-config", default="config/companies.yaml")
    parser.add_argument("--output", default="output/jobs.json")
    parser.add_argument("--deduplicate", action="store_true", default=True)
    parser.add_argument("--cache-hours", type=float, default=6,
                        help="Use cached jobs if less than N hours old (0 to disable)")
    parser.add_argument("--max-workers", type=int, default=5,
                        help="Max parallel fetch threads (default: 5)")
    # LinkedIn options
    parser.add_argument("--linkedin-query", type=str, default=None,
                        help="LinkedIn search keywords (e.g. 'python developer'). Enables LinkedIn fetching.")
    parser.add_argument("--linkedin-locations", nargs="+",
                        default=None,
                        help="LinkedIn locations to search (reads from preferences.yaml if not specified)")
    parser.add_argument("--linkedin-remote", choices=["remote", "hybrid", "onsite"],
                        default=None, help="LinkedIn work type filter")
    parser.add_argument("--linkedin-jobage", choices=["1", "7", "14", "30"],
                        default=None, help="LinkedIn max job age in days")
    parser.add_argument("--linkedin-limit", type=int, default=25,
                        help="Max LinkedIn jobs per location (default: 25)")
    args = parser.parse_args()

    # Check cache first
    if args.cache_hours > 0 and is_cache_valid(args.output, args.cache_hours):
        with open(args.output) as f:
            data = json.load(f)
        print(f"Using cached jobs ({data.get('total', 0)} jobs, fetched at {data.get('fetched_at', 'unknown')})")
        print(f"Cache valid for {args.cache_hours}h. Use --cache-hours 0 to force refresh.")
        return

    platforms = [p.strip() for p in args.platforms.split(",")]
    companies = load_companies(args.companies_config)

    # Build fetch tasks: list of (platform, token) pairs
    fetch_tasks = []
    for platform in platforms:
        if platform not in FETCHERS:
            print(f"Unknown platform: {platform}, skipping")
            continue
        tokens = companies.get(platform, [])
        if not tokens:
            print(f"No companies configured for {platform}, skipping")
            continue
        for token in tokens:
            fetch_tasks.append((platform, token))

    if not fetch_tasks and not args.linkedin_query:
        print("No fetch tasks to run.")
        return

    # Parallel fetch from ATS platforms
    all_jobs = []
    start_time = time.time()

    if fetch_tasks:
        print(f"Fetching from {len(fetch_tasks)} sources in parallel (max {args.max_workers} threads)...\n")

        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            future_to_task = {}
            for platform, token in fetch_tasks:
                fetcher = FETCHERS[platform]
                future = executor.submit(fetcher, token)
                future_to_task[future] = (platform, token)

            for future in as_completed(future_to_task):
                platform, token = future_to_task[future]
                try:
                    jobs = future.result()
                    all_jobs.extend(jobs)
                    print(f"  {platform}/{token}: {len(jobs)} jobs")
                except Exception as e:
                    print(f"  {platform}/{token}: ERROR — {e}")

    elapsed = time.time() - start_time
    print(f"\nFetched {len(all_jobs)} jobs from ATS platforms in {elapsed:.1f}s")

    # LinkedIn fetch (if configured)
    if args.linkedin_query:
        print(f"\nFetching LinkedIn jobs: '{args.linkedin_query}' in {', '.join(args.linkedin_locations)}")
        li_start = time.time()
        try:
            linkedin_jobs = fetch_linkedin(
                query=args.linkedin_query,
                locations=args.linkedin_locations,
                work_type=args.linkedin_remote,
                recency_days=args.linkedin_jobage,
                limit=args.linkedin_limit,
                cache_hours=args.cache_hours,
            )
            all_jobs.extend(linkedin_jobs)
            li_elapsed = time.time() - li_start
            print(f"  LinkedIn: {len(linkedin_jobs)} jobs in {li_elapsed:.1f}s")
        except Exception as e:
            print(f"  LinkedIn fetch error: {e}")

    # Deduplicate
    if args.deduplicate:
        before = len(all_jobs)
        all_jobs = deduplicate_jobs(all_jobs)
        dupes = before - len(all_jobs)
        if dupes > 0:
            print(f"Removed {dupes} duplicates")

    # Write output with timestamp
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({
            "total": len(all_jobs),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "jobs": all_jobs,
        }, f, indent=2)

    print(f"Total: {len(all_jobs)} unique jobs saved to {args.output}")


if __name__ == "__main__":
    main()
