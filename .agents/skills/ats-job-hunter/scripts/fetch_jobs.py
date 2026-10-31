#!/usr/bin/env python3
"""Fetch open jobs from ATS platforms and job boards via public APIs.

Supports 10 ATS platforms (company-specific) and 8 job board aggregators (global search).
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


# ---------------------------------------------------------------------------
# Job Board Aggregator Fetchers (global search, no company token needed)
# ---------------------------------------------------------------------------

def fetch_remoteok_jobs(query: str = "") -> list[dict]:
    """Fetch from RemoteOK — free, no auth, returns JSON array."""
    url = "https://remoteok.com/api"
    params = {}
    if query:
        params["tags"] = query
    try:
        resp = requests.get(url, params=params, timeout=30,
                            headers={"User-Agent": "ATS-Job-Hunter/1.0"})
        resp.raise_for_status()
        data = resp.json()
        # First element is metadata, skip it
        jobs = data[1:] if len(data) > 1 else []
        return [
            {
                "id": str(job.get("id", "")),
                "title": job.get("position", ""),
                "location": "Remote",
                "description": clean_html(job.get("description", "")),
                "url": job.get("url", f"https://remoteok.com/remote-jobs/{job.get('id', '')}"),
                "department": "",
                "platform": "remoteok",
                "company_token": job.get("company", "").lower().replace(" ", "-"),
                "company_name": job.get("company", ""),
                "updated_at": job.get("date", ""),
                "salary_min": job.get("salary_min", ""),
                "salary_max": job.get("salary_max", ""),
                "tags": job.get("tags", []),
            }
            for job in jobs
        ]
    except requests.RequestException as e:
        print(f"  Error fetching RemoteOK: {e}")
        return []


def fetch_himalayas_jobs(query: str = "", limit: int = 50) -> list[dict]:
    """Fetch from Himalayas — free, no auth, cursor-paginated."""
    base_url = "https://himalayas.app/jobs/api"
    try:
        all_jobs = []
        cursor = None
        fetched = 0

        while fetched < limit:
            params = {"limit": min(20, limit - fetched)}
            if query:
                params["keyword"] = query
            if cursor:
                params["cursor"] = cursor

            resp = requests.get(base_url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            page_jobs = data.get("jobs", [])
            if not page_jobs:
                break

            for job in page_jobs:
                all_jobs.append({
                    "id": str(job.get("id", "")),
                    "title": job.get("title", ""),
                    "location": job.get("location", "Remote"),
                    "description": clean_html(job.get("description", "")),
                    "url": job.get("applicationUrl", job.get("url", "")),
                    "department": job.get("category", ""),
                    "platform": "himalayas",
                    "company_token": job.get("companySlug", ""),
                    "company_name": job.get("companyName", ""),
                    "updated_at": job.get("pubDate", ""),
                    "salary_min": job.get("minSalary", ""),
                    "salary_max": job.get("maxSalary", ""),
                })

            cursor = data.get("cursor")
            fetched += len(page_jobs)
            if not cursor:
                break
            time.sleep(0.5)

        return all_jobs
    except requests.RequestException as e:
        print(f"  Error fetching Himalayas: {e}")
        return []


def fetch_arbeitnow_jobs(query: str = "", limit: int = 100) -> list[dict]:
    """Fetch from Arbeitnow — free, no auth, paginated."""
    try:
        all_jobs = []
        page = 1

        while len(all_jobs) < limit:
            resp = requests.get(f"https://arbeitnow.com/api/job-board-api?page={page}", timeout=30)
            resp.raise_for_status()
            data = resp.json()
            page_jobs = data.get("data", [])
            if not page_jobs:
                break

            for job in page_jobs:
                if query and query.lower() not in (job.get("title", "") + " " + job.get("description", "")).lower():
                    continue
                all_jobs.append({
                    "id": job.get("slug", ""),
                    "title": job.get("title", ""),
                    "location": job.get("location", ""),
                    "description": clean_html(job.get("description", "")),
                    "url": job.get("url", ""),
                    "department": ", ".join(job.get("tags", [])),
                    "platform": "arbeitnow",
                    "company_token": job.get("company_name", "").lower().replace(" ", "-"),
                    "company_name": job.get("company_name", ""),
                    "updated_at": job.get("created_at", ""),
                    "remote": job.get("remote", False),
                })

            if not data.get("links", {}).get("next"):
                break
            page += 1
            time.sleep(0.5)

        return all_jobs[:limit]
    except requests.RequestException as e:
        print(f"  Error fetching Arbeitnow: {e}")
        return []


def fetch_jobicy_jobs(query: str = "", geo: str = "", limit: int = 50) -> list[dict]:
    """Fetch from Jobicy — free, no auth, last 7 days of remote jobs."""
    params = {"count": min(limit, 50)}
    if query:
        params["tag"] = query
    if geo:
        params["geo"] = geo
    try:
        resp = requests.get("https://jobicy.com/api/v2/remote-jobs", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "id": str(job.get("id", "")),
                "title": job.get("jobTitle", ""),
                "location": job.get("jobGeo", "Remote"),
                "description": clean_html(job.get("jobDescription", "")),
                "url": job.get("url", ""),
                "department": job.get("jobIndustry", [""])[0] if job.get("jobIndustry") else "",
                "platform": "jobicy",
                "company_token": job.get("companyName", "").lower().replace(" ", "-"),
                "company_name": job.get("companyName", ""),
                "updated_at": job.get("pubDate", ""),
                "salary_min": job.get("annualSalaryMin", ""),
                "salary_max": job.get("annualSalaryMax", ""),
                "job_type": job.get("jobType", ""),
            }
            for job in data.get("jobs", [])
        ]
    except requests.RequestException as e:
        print(f"  Error fetching Jobicy: {e}")
        return []


def fetch_workingnomads_jobs(query: str = "") -> list[dict]:
    """Fetch from Working Nomads — free, no auth."""
    try:
        resp = requests.get("https://www.workingnomads.com/api/exposed_jobs/", timeout=30)
        resp.raise_for_status()
        jobs = resp.json()
        results = []
        for job in jobs:
            title = job.get("title", "")
            desc = job.get("description", "")
            if query and query.lower() not in (title + " " + desc).lower():
                continue
            results.append({
                "id": str(job.get("id", "")),
                "title": title,
                "location": job.get("location", "Remote"),
                "description": clean_html(desc),
                "url": job.get("url", ""),
                "department": job.get("category_name", ""),
                "platform": "workingnomads",
                "company_token": job.get("company_name", "").lower().replace(" ", "-"),
                "company_name": job.get("company_name", ""),
                "updated_at": job.get("pub_date", ""),
            })
        return results
    except requests.RequestException as e:
        print(f"  Error fetching WorkingNomads: {e}")
        return []


def fetch_remotive_jobs(query: str = "", limit: int = 50) -> list[dict]:
    """Fetch from Remotive — free, no auth, ~35 jobs, 24h delayed."""
    params = {"limit": limit}
    if query:
        params["search"] = query
    try:
        resp = requests.get("https://remotive.com/api/remote-jobs", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return [
            {
                "id": str(job.get("id", "")),
                "title": job.get("title", ""),
                "location": job.get("candidate_required_location", "Remote"),
                "description": clean_html(job.get("description", "")),
                "url": job.get("url", ""),
                "department": job.get("category", ""),
                "platform": "remotive",
                "company_token": job.get("company_name", "").lower().replace(" ", "-"),
                "company_name": job.get("company_name", ""),
                "updated_at": job.get("publication_date", ""),
                "job_type": job.get("job_type", ""),
                "salary": job.get("salary", ""),
            }
            for job in data.get("jobs", [])
        ]
    except requests.RequestException as e:
        print(f"  Error fetching Remotive: {e}")
        return []


def fetch_themuse_jobs(query: str = "", category: str = "Engineering", limit: int = 100) -> list[dict]:
    """Fetch from The Muse — free, no auth, paginated."""
    try:
        all_jobs = []
        page = 0

        while len(all_jobs) < limit:
            params = {"page": page}
            if category:
                params["category"] = category
            resp = requests.get("https://www.themuse.com/api/public/jobs", params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                break

            for job in results:
                locations = [loc.get("name", "") for loc in job.get("locations", [])]
                all_jobs.append({
                    "id": str(job.get("id", "")),
                    "title": job.get("name", ""),
                    "location": "; ".join(locations) if locations else "",
                    "description": clean_html(job.get("contents", "")),
                    "url": job.get("refs", {}).get("landing_page", ""),
                    "department": ", ".join(cat.get("name", "") for cat in job.get("categories", [])),
                    "platform": "themuse",
                    "company_token": job.get("company", {}).get("short_name", ""),
                    "company_name": job.get("company", {}).get("name", ""),
                    "updated_at": job.get("publication_date", ""),
                    "levels": [lvl.get("name", "") for lvl in job.get("levels", [])],
                })

            page += 1
            if page >= data.get("page_count", 1):
                break
            time.sleep(0.3)

        return all_jobs[:limit]
    except requests.RequestException as e:
        print(f"  Error fetching TheMuse: {e}")
        return []


def fetch_hn_whos_hiring(query: str = "") -> list[dict]:
    """Fetch from Hacker News 'Who is hiring?' monthly thread via Algolia API."""
    try:
        # Find the latest "Who is hiring?" thread
        search_resp = requests.get(
            "https://hn.algolia.com/api/v1/search_by_date",
            params={"tags": "story", "query": "Ask HN Who is hiring", "hitsPerPage": 1},
            timeout=15,
        )
        search_resp.raise_for_status()
        hits = search_resp.json().get("hits", [])
        if not hits:
            print("  HN: No 'Who is hiring?' thread found")
            return []

        story_id = hits[0]["objectID"]
        story_title = hits[0].get("title", "")
        print(f"  HN: Found thread '{story_title}' (ID: {story_id})")

        # Fetch all children (job comments)
        item_resp = requests.get(
            f"https://hn.algolia.com/api/v1/items/{story_id}",
            timeout=30,
        )
        item_resp.raise_for_status()
        item = item_resp.json()
        children = item.get("children", [])

        jobs = []
        for comment in children:
            text = comment.get("text", "")
            if not text or len(text) < 20:
                continue
            if query and query.lower() not in text.lower():
                continue

            # Try to extract company name from first line
            first_line = clean_html(text).split(".")[0].split("|")[0].strip()
            company = first_line[:60] if first_line else "Unknown"

            jobs.append({
                "id": str(comment.get("id", "")),
                "title": first_line[:100],
                "location": "See posting",
                "description": clean_html(text),
                "url": f"https://news.ycombinator.com/item?id={comment.get('id', '')}",
                "department": "",
                "platform": "hackernews",
                "company_token": company.lower().replace(" ", "-")[:30],
                "company_name": company,
                "updated_at": comment.get("created_at", ""),
            })
        return jobs
    except requests.RequestException as e:
        print(f"  Error fetching HN Who's Hiring: {e}")
        return []


JOB_BOARD_FETCHERS = {
    "remoteok": fetch_remoteok_jobs,
    "himalayas": fetch_himalayas_jobs,
    "arbeitnow": fetch_arbeitnow_jobs,
    "jobicy": fetch_jobicy_jobs,
    "workingnomads": fetch_workingnomads_jobs,
    "remotive": fetch_remotive_jobs,
    "themuse": fetch_themuse_jobs,
    "hackernews": fetch_hn_whos_hiring,
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
    # Job board aggregator options
    parser.add_argument("--job-boards", type=str, default=None,
                        help="Comma-separated job boards to search (remoteok,himalayas,arbeitnow,jobicy,workingnomads,remotive,themuse,hackernews). Use 'all' for all.")
    parser.add_argument("--job-board-query", type=str, default=None,
                        help="Search query for job board aggregators (e.g. 'python engineer')")
    parser.add_argument("--job-board-limit", type=int, default=50,
                        help="Max jobs per job board (default: 50)")
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

    # Parse job board selection
    job_board_names = []
    if args.job_boards:
        if args.job_boards.lower() == "all":
            job_board_names = list(JOB_BOARD_FETCHERS.keys())
        else:
            job_board_names = [b.strip() for b in args.job_boards.split(",") if b.strip() in JOB_BOARD_FETCHERS]

    if not fetch_tasks and not job_board_names and not args.linkedin_query:
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

    # Job board aggregator fetch (if configured)
    if job_board_names:
        board_query = args.job_board_query or ""
        board_limit = args.job_board_limit
        print(f"\nFetching from {len(job_board_names)} job boards{f' (query: {board_query!r})' if board_query else ''}...")

        with ThreadPoolExecutor(max_workers=min(len(job_board_names), 5)) as executor:
            future_to_board = {}
            for board_name in job_board_names:
                fetcher = JOB_BOARD_FETCHERS[board_name]
                if board_name in ("himalayas", "arbeitnow", "themuse"):
                    future = executor.submit(fetcher, board_query, board_limit)
                elif board_name == "jobicy":
                    future = executor.submit(fetcher, board_query, "", board_limit)
                else:
                    future = executor.submit(fetcher, board_query)
                future_to_board[future] = board_name

            for future in as_completed(future_to_board):
                board_name = future_to_board[future]
                try:
                    jobs = future.result()
                    all_jobs.extend(jobs)
                    print(f"  {board_name}: {len(jobs)} jobs")
                except Exception as e:
                    print(f"  {board_name}: ERROR — {e}")

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
