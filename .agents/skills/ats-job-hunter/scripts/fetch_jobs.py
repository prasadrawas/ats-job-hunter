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
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{company}/postings",
    "bamboohr": "https://{company}.bamboohr.com/careers/list",
    "recruitee": "https://{company}.recruitee.com/api/offers/",
    "breezyhr": "https://{company}.breezy.hr/json",
    "rippling": "https://api.rippling.com/platform/api/ats/v2/board/{slug}/jobs?groupJobsByLocation=true&page=0&pageSize=1000",
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


def fetch_workday_jobs(tenant_config: str) -> list[dict]:
    """Fetch from Workday. tenant_config format: 'tenant.wdN/site' e.g. 'netflix.wd5/External'"""
    try:
        parts = tenant_config.split("/", 1)
        tenant_host = parts[0]  # e.g. 'netflix.wd5'
        site = parts[1] if len(parts) > 1 else "External"
        base_url = f"https://{tenant_host}.myworkdayjobs.com"
        search_url = f"{base_url}/wday/cxs/{tenant_host.split('.')[0]}/{site}/jobs"

        all_jobs = []
        offset = 0
        limit = 20  # Workday max per page

        while True:
            payload = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
            resp = requests.post(search_url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            job_postings = data.get("jobPostings", [])
            if not job_postings:
                break

            for job in job_postings:
                title = job.get("title", "")
                loc_parts = job.get("locationsText", "") or job.get("bulletFields", [""])[0]
                posted = job.get("postedOn", "")
                external_path = job.get("externalPath", "")
                job_url = f"{base_url}/en-US{external_path}" if external_path else ""

                all_jobs.append({
                    "id": external_path.split("/")[-1] if external_path else str(offset),
                    "title": title,
                    "location": loc_parts if isinstance(loc_parts, str) else "",
                    "description": "",  # Workday list endpoint doesn't include descriptions
                    "url": job_url,
                    "department": "",
                    "platform": "workday",
                    "company_token": tenant_config,
                    "updated_at": posted,
                })

            offset += limit
            total = data.get("total", 0)
            if offset >= total or offset >= 2000:  # Workday caps at ~2000
                break
            time.sleep(0.5)  # rate limit

        return all_jobs
    except requests.RequestException as e:
        print(f"  Error fetching Workday/{tenant_config}: {e}")
        return []


def fetch_smartrecruiters_jobs(company: str) -> list[dict]:
    url = ATS_ENDPOINTS["smartrecruiters"].format(company=company)
    try:
        all_jobs = []
        offset = 0
        limit = 100

        while True:
            resp = requests.get(url, params={"offset": offset, "limit": limit}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            postings = data.get("content", [])
            if not postings:
                break

            for posting in postings:
                loc = posting.get("location", {})
                location = loc.get("city", "")
                if loc.get("region"):
                    location += f", {loc['region']}"
                if loc.get("country"):
                    location += f", {loc['country']}"

                all_jobs.append({
                    "id": posting.get("id", ""),
                    "title": posting.get("name", ""),
                    "location": location,
                    "description": "",  # list endpoint doesn't include description
                    "url": posting.get("ref", ""),
                    "department": posting.get("department", {}).get("label", ""),
                    "platform": "smartrecruiters",
                    "company_token": company,
                    "updated_at": posting.get("releasedDate", ""),
                })

            offset += limit
            total = data.get("totalFound", 0)
            if offset >= total:
                break

        # Fetch descriptions for each job (SmartRecruiters requires per-job detail call)
        for job in all_jobs:
            if job["id"]:
                try:
                    detail_url = f"{url}/{job['id']}"
                    detail_resp = requests.get(detail_url, timeout=15)
                    if detail_resp.ok:
                        detail = detail_resp.json()
                        desc_parts = []
                        for section in detail.get("jobAd", {}).get("sections", {}).values():
                            if isinstance(section, dict) and section.get("text"):
                                desc_parts.append(clean_html(section["text"]))
                        job["description"] = " ".join(desc_parts)
                    time.sleep(0.3)
                except requests.RequestException:
                    pass

        return all_jobs
    except requests.RequestException as e:
        print(f"  Error fetching SmartRecruiters/{company}: {e}")
        return []


def fetch_bamboohr_jobs(company: str) -> list[dict]:
    url = ATS_ENDPOINTS["bamboohr"].format(company=company)
    try:
        resp = requests.get(url, timeout=30, headers={"Accept": "application/json"})
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for job in data.get("result", []):
            jobs.append({
                "id": str(job.get("id", "")),
                "title": job.get("jobOpeningName", ""),
                "location": job.get("location", {}).get("city", ""),
                "description": "",
                "url": f"https://{company}.bamboohr.com/careers/{job.get('id', '')}",
                "department": job.get("departmentLabel", ""),
                "platform": "bamboohr",
                "company_token": company,
                "updated_at": "",
            })

        # Fetch descriptions per job
        for job in jobs:
            if job["id"]:
                try:
                    detail_url = f"https://{company}.bamboohr.com/careers/{job['id']}/detail"
                    detail_resp = requests.get(detail_url, timeout=15, headers={"Accept": "application/json"})
                    if detail_resp.ok:
                        detail = detail_resp.json()
                        job["description"] = clean_html(detail.get("result", {}).get("description", ""))
                    time.sleep(0.3)
                except requests.RequestException:
                    pass

        return jobs
    except requests.RequestException as e:
        print(f"  Error fetching BambooHR/{company}: {e}")
        return []


def fetch_recruitee_jobs(company: str) -> list[dict]:
    url = ATS_ENDPOINTS["recruitee"].format(company=company)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "id": str(offer.get("id", "")),
                "title": offer.get("title", ""),
                "location": offer.get("location", ""),
                "description": clean_html(offer.get("description", "")),
                "url": offer.get("careers_url", offer.get("url", "")),
                "department": offer.get("department", ""),
                "platform": "recruitee",
                "company_token": company,
                "updated_at": offer.get("published_at", ""),
            }
            for offer in data.get("offers", [])
        ]
    except requests.RequestException as e:
        print(f"  Error fetching Recruitee/{company}: {e}")
        return []


def fetch_breezyhr_jobs(company: str) -> list[dict]:
    url = ATS_ENDPOINTS["breezyhr"].format(company=company)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        jobs = resp.json()
        return [
            {
                "id": job.get("id", ""),
                "title": job.get("name", ""),
                "location": (job.get("location", {}) or {}).get("name", "") if isinstance(job.get("location"), dict) else job.get("location", ""),
                "description": job.get("description", ""),
                "url": job.get("url", f"https://{company}.breezy.hr/p/{job.get('id', '')}"),
                "department": job.get("department", ""),
                "platform": "breezyhr",
                "company_token": company,
                "updated_at": job.get("published_date", ""),
            }
            for job in jobs
        ]
    except requests.RequestException as e:
        print(f"  Error fetching BreezyHR/{company}: {e}")
        return []


def fetch_rippling_jobs(slug: str) -> list[dict]:
    url = ATS_ENDPOINTS["rippling"].format(slug=slug)
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        jobs = []
        for job in data if isinstance(data, list) else data.get("jobs", data.get("data", [])):
            loc = job.get("workLocation", job.get("location", ""))
            if isinstance(loc, dict):
                loc = loc.get("name", "")
            jobs.append({
                "id": job.get("uuid", job.get("id", "")),
                "title": job.get("name", job.get("title", "")),
                "location": loc,
                "description": clean_html(job.get("description", "")),
                "url": job.get("url", ""),
                "department": job.get("department", ""),
                "platform": "rippling",
                "company_token": slug,
                "updated_at": "",
            })
        return jobs
    except requests.RequestException as e:
        print(f"  Error fetching Rippling/{slug}: {e}")
        return []


def fetch_taleo_jobs(tenant_config: str) -> list[dict]:
    """Fetch from Taleo. tenant_config format: 'tenant/csName' e.g. 'drhorton/2'"""
    try:
        parts = tenant_config.split("/", 1)
        tenant = parts[0]
        cs_name = parts[1] if len(parts) > 1 else "2"
        url = f"https://{tenant}.taleo.net/careersection/rest/jobboard/searchjobs?lang=en&portal={cs_name}"

        payload = {
            "multilineEnabled": False,
            "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
            "fieldData": {"fields": {"KEYWORD": "", "LOCATION": "", "ORGANIZATION": ""}},
            "filterSelectionParam": {"searchFilterRecords": []},
            "advancedSearchFiltersSelectionParam": {"searchFilterRecords": []},
            "pageNo": 1,
            "is498702498": False,
        }

        resp = requests.post(url, json=payload, timeout=30,
                             headers={"Content-Type": "application/json", "tz": "GMT"})
        resp.raise_for_status()
        data = resp.json()

        jobs = []
        for row in data.get("requisitionList", []):
            jobs.append({
                "id": str(row.get("contestNo", "")),
                "title": row.get("column", [{"value": ""}])[0].get("value", "") if row.get("column") else "",
                "location": row.get("column", [{}, {"value": ""}])[1].get("value", "") if len(row.get("column", [])) > 1 else "",
                "description": "",
                "url": f"https://{tenant}.taleo.net/careersection/{cs_name}/jobdetail.ftl?job={row.get('contestNo', '')}",
                "department": "",
                "platform": "taleo",
                "company_token": tenant_config,
                "updated_at": "",
            })
        return jobs
    except requests.RequestException as e:
        print(f"  Error fetching Taleo/{tenant_config}: {e}")
        return []


FETCHERS = {
    "greenhouse": fetch_greenhouse_jobs,
    "lever": fetch_lever_jobs,
    "ashby": fetch_ashby_jobs,
    "workday": fetch_workday_jobs,
    "smartrecruiters": fetch_smartrecruiters_jobs,
    "bamboohr": fetch_bamboohr_jobs,
    "recruitee": fetch_recruitee_jobs,
    "breezyhr": fetch_breezyhr_jobs,
    "rippling": fetch_rippling_jobs,
    "taleo": fetch_taleo_jobs,
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
    parser.add_argument("--platforms", default="greenhouse,lever,ashby,workday,smartrecruiters,bamboohr,recruitee,breezyhr,rippling,taleo")
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
