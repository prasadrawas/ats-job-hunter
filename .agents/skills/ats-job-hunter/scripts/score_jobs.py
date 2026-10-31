#!/usr/bin/env python3
"""Score fetched jobs against a resume using Gemini AI + TF-IDF fallback, with job filtering."""

import argparse
import json
import math
import os
import re
import time
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

load_dotenv()

GEMINI_MODEL = "gemini-2.0-flash"

GEMINI_BATCH_SCORING_PROMPT = """You are a senior technical recruiter evaluating resume-to-job fit.

Score how well this candidate matches EACH of the following jobs on a scale of 0-100.

## SCORING CRITERIA (use these exact weights)
- **Skills Match (40%)**: How many required/preferred skills does the candidate have?
- **Experience Level (25%)**: Does the candidate's years and seniority match?
- **Role Relevance (20%)**: How closely do past titles and responsibilities align?
- **Domain/Industry Fit (15%)**: Does the candidate have relevant domain experience?

## RULES
- Be honest — 70+ is strong, 50-69 is decent, below 50 is weak
- Missing a "required" skill is worse than missing a "nice-to-have"

## CANDIDATE
Name: {name} | Title: {label} | Experience: {years}+ years
Skills: {skills}
Recent: {recent_role} at {recent_company}
Highlights: {achievements}

## JOBS TO SCORE
{jobs_block}

## OUTPUT FORMAT
Return ONLY a valid JSON array (no markdown, no code fences). One object per job, in the SAME ORDER as listed above:
[
  {{
    "job_index": 0,
    "score": 72,
    "matched_skills": ["Python", "React"],
    "missing_skills": ["Kubernetes"],
    "reasoning": "Strong match because...",
    "recommendation": "APPLY"
  }}
]

Keep reasoning under 80 chars per job. Recommendation must be APPLY, MAYBE, or SKIP.
"""


def load_preferences(path: str) -> dict:
    """Load job preferences from YAML config."""
    pref_path = Path(path)
    if not pref_path.exists():
        return {}
    with open(pref_path) as f:
        return yaml.safe_load(f) or {}


def load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def calculate_experience(career_start_date: str) -> float:
    """Calculate years of experience."""
    start = date.fromisoformat(career_start_date)
    delta_days = (date.today() - start).days
    return math.floor((delta_days / 365.25) * 10) / 10


# =============================================================================
# JOB FILTERING
# =============================================================================

def filter_jobs(jobs: list[dict], prefs: dict) -> list[dict]:
    """Filter jobs based on user preferences. Returns (passed, filtered_out_count)."""
    if not prefs:
        return jobs

    location_prefs = prefs.get("location", {})
    role_prefs = prefs.get("roles", {})
    exp_prefs = prefs.get("experience", {})
    keyword_prefs = prefs.get("keywords", {})
    company_prefs = prefs.get("companies", {})
    dept_prefs = prefs.get("departments", {})

    strict_location = location_prefs.get("strict", False)
    allowed_locations = [loc.lower() for loc in location_prefs.get("allowed", location_prefs.get("preferred", []))]
    excluded_locations = [loc.lower() for loc in location_prefs.get("excluded", [])]
    target_titles = [t.lower() for t in role_prefs.get("target_titles", [])]
    seniority = [s.lower() for s in role_prefs.get("seniority", [])]
    excluded_titles = [t.lower() for t in role_prefs.get("excluded_titles", [])]
    max_years = exp_prefs.get("max_required_years", 99)
    must_have_any = [k.lower() for k in keyword_prefs.get("must_have_any", [])]
    exclude_keywords = [k.lower() for k in keyword_prefs.get("exclude", [])]
    excluded_companies = [c.lower() for c in company_prefs.get("excluded", [])]
    included_depts = [d.lower() for d in dept_prefs.get("included", [])]
    excluded_depts = [d.lower() for d in dept_prefs.get("excluded", [])]

    passed = []
    for job in jobs:
        title_lower = job.get("title", "").lower()
        location_lower = job.get("location", "").lower()
        desc_lower = job.get("description", "").lower()
        dept_lower = job.get("department", "").lower()
        company_lower = job.get("company_token", "").lower()

        # --- Exclude filters (hard reject) ---

        # Excluded companies
        if excluded_companies and company_lower in excluded_companies:
            continue

        # Excluded title keywords
        if excluded_titles and any(ex in title_lower for ex in excluded_titles):
            continue

        # Excluded keywords in description
        if exclude_keywords and any(kw in desc_lower for kw in exclude_keywords):
            continue

        # Excluded departments
        if excluded_depts and any(d in dept_lower for d in excluded_depts):
            continue

        # Excluded locations
        if excluded_locations and any(loc in location_lower for loc in excluded_locations):
            continue

        # Strict location filter — job MUST match at least one allowed location
        if strict_location and allowed_locations:
            # Check location field + also scan description for remote mentions
            location_text = location_lower + " " + desc_lower[:500]
            if not any(loc in location_text for loc in allowed_locations):
                continue

        # Max experience filter
        years_match = re.findall(r'(\d+)\+?\s*years?', desc_lower)
        if years_match:
            max_required = max(int(y) for y in years_match)
            if max_required > max_years:
                continue

        # --- Include filters (soft — only if configured) ---

        # Target titles (at least one must match)
        if target_titles:
            if not any(t in title_lower for t in target_titles):
                continue

        # Must-have keywords (at least one in description)
        if must_have_any:
            if not any(kw in desc_lower for kw in must_have_any):
                continue

        # Included departments (at least one must match)
        if included_depts:
            if dept_lower and not any(d in dept_lower for d in included_depts):
                continue

        passed.append(job)

    return passed


# =============================================================================
# TF-IDF SCORING (fast, local, no API cost)
# =============================================================================

def extract_resume_text(resume: dict) -> str:
    """Convert JSON Resume to plain text."""
    parts = []
    basics = resume.get("basics", {})
    if s := basics.get("summary", ""):
        parts.append(s)
    for skill_group in resume.get("skills", []):
        if isinstance(skill_group, dict):
            parts.append(skill_group.get("name", ""))
            parts.extend(skill_group.get("keywords", []))
    for work in resume.get("work", []):
        parts.append(work.get("position", ""))
        parts.append(work.get("summary", ""))
        parts.extend(work.get("highlights", []))
    return " ".join(filter(None, parts))


def extract_resume_skills(resume: dict) -> set[str]:
    """Extract flat set of skill keywords."""
    skills = set()
    for skill_group in resume.get("skills", []):
        if isinstance(skill_group, dict):
            skills.add(skill_group.get("name", "").lower())
            skills.update(kw.lower() for kw in skill_group.get("keywords", []))
    return skills


def extract_job_skills(description: str) -> set[str]:
    """Extract likely skill keywords from JD."""
    common_skills = {
        "python", "java", "javascript", "typescript", "react", "node", "nodejs",
        "aws", "gcp", "azure", "docker", "kubernetes", "sql", "nosql", "mongodb",
        "postgresql", "redis", "graphql", "rest", "api", "ci/cd", "git",
        "machine learning", "deep learning", "nlp", "tensorflow", "pytorch",
        "scala", "go", "golang", "rust", "c++", "swift", "kotlin",
        "django", "flask", "fastapi", "spring", "rails", "vue", "angular",
        "terraform", "ansible", "jenkins", "github actions", "datadog",
        "kafka", "rabbitmq", "elasticsearch", "spark", "airflow",
        "figma", "html", "css", "sass", "tailwind", "nextjs", "remix",
        "flutter", "dart", "react native", "swiftui", "celery", "pydantic",
        "sqlalchemy", "laravel", "express", "supabase", "firebase",
    }
    desc_lower = description.lower()
    return {skill for skill in common_skills if skill in desc_lower}


def tfidf_score_job(resume: dict, resume_text: str, resume_skills: set[str], job: dict) -> dict:
    """Score a job using TF-IDF + skill overlap (fast, no API)."""
    job_desc = job.get("description", "")

    # Skills match
    job_skills = extract_job_skills(job_desc)
    if job_skills:
        skills_score = len(resume_skills & job_skills) / len(job_skills)
    else:
        skills_score = 0.5

    matched_skills = sorted(resume_skills & job_skills)
    missing_skills = sorted(job_skills - resume_skills)

    # Title relevance
    resume_titles = [w.get("position", "") for w in resume.get("work", [])]
    job_title = job.get("title", "")
    job_words = set(re.findall(r'\w+', job_title.lower()))
    title_score = 0.5
    for title in resume_titles:
        title_words = set(re.findall(r'\w+', title.lower()))
        overlap = len(job_words & title_words) / max(len(job_words), 1)
        title_score = max(title_score, overlap)

    # TF-IDF
    if resume_text.strip() and job_desc.strip():
        vectorizer = TfidfVectorizer(stop_words="english", max_features=5000)
        matrix = vectorizer.fit_transform([resume_text, job_desc])
        keywords_score = float(cosine_similarity(matrix[0:1], matrix[1:2])[0][0])
    else:
        keywords_score = 0.0

    # Experience
    years_match = re.findall(r'(\d+)\+?\s*years?', job_desc.lower())
    career_start = resume.get("basics", {}).get("careerStartDate", "")
    resume_years = calculate_experience(career_start) if career_start else 5.0
    if years_match:
        required = max(int(y) for y in years_match)
        exp_score = min(1.0, resume_years / max(required, 1))
    else:
        exp_score = 0.5

    total = 0.40 * skills_score + 0.25 * exp_score + 0.20 * title_score + 0.15 * keywords_score

    return {
        **job,
        "match_score": round(total * 100, 1),
        "scoring_method": "tfidf",
        "scores": {
            "skills": round(skills_score * 100, 1),
            "experience": round(exp_score * 100, 1),
            "title": round(title_score * 100, 1),
            "keywords": round(keywords_score * 100, 1),
        },
        "matched_skills": matched_skills,
        "missing_skills": missing_skills,
        "reasoning": "",
        "recommendation": "",
    }


# =============================================================================
# GEMINI SCORING (accurate, uses API quota)
# =============================================================================

def gemini_score_jobs(resume: dict, jobs: list[dict], batch_size: int = 5) -> list[dict]:
    """Score jobs using Gemini AI in batches (sends resume once per batch, not per job).

    With batch_size=5 and 30 jobs: 6 API calls instead of 30 = ~80% token savings.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("Warning: GEMINI_API_KEY not set — falling back to TF-IDF scoring")
        return []

    client = genai.Client(api_key=api_key)

    basics = resume.get("basics", {})
    career_start = basics.get("careerStartDate", "")
    years = str(calculate_experience(career_start)) if career_start else "5"

    # Build compact resume summary (sent once per batch)
    skills_flat = []
    for sg in resume.get("skills", []):
        if isinstance(sg, dict):
            skills_flat.extend(sg.get("keywords", []))
    skills_text = ", ".join(skills_flat[:40])

    work = resume.get("work", [])
    recent = work[0] if work else {}
    achievements = "; ".join(recent.get("highlights", [])[:4])[:400]

    # Process in batches
    scored = []
    total_batches = math.ceil(len(jobs) / batch_size)

    for batch_idx in range(total_batches):
        batch_start = batch_idx * batch_size
        batch_jobs = jobs[batch_start:batch_start + batch_size]

        print(f"  Gemini batch [{batch_idx + 1}/{total_batches}]: {len(batch_jobs)} jobs")

        # Build jobs block — compact format, 1500 chars per JD max
        jobs_block_parts = []
        for i, job in enumerate(batch_jobs):
            desc = job.get("description", "")[:1500]
            jobs_block_parts.append(
                f"### JOB {i}\n"
                f"Title: {job.get('title', '')}\n"
                f"Company: {job.get('company_token', '')}\n"
                f"Location: {job.get('location', '')}\n"
                f"Description: {desc}\n"
            )
        jobs_block = "\n".join(jobs_block_parts)

        prompt = GEMINI_BATCH_SCORING_PROMPT.format(
            name=basics.get("name", ""),
            label=basics.get("label", ""),
            years=years,
            skills=skills_text,
            recent_role=recent.get("position", ""),
            recent_company=recent.get("company", ""),
            achievements=achievements,
            jobs_block=jobs_block,
        )

        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
            )
            text = response.text.strip()
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
            results = json.loads(text)

            if not isinstance(results, list):
                results = [results]

            for result in results:
                idx = result.get("job_index", 0)
                if idx < len(batch_jobs):
                    job = batch_jobs[idx]
                    scored.append({
                        **job,
                        "match_score": result.get("score", 0),
                        "scoring_method": "gemini",
                        "scores": {
                            "skills": result.get("skills_score", 0),
                            "experience": result.get("experience_score", 0),
                            "title": result.get("role_score", 0),
                            "domain": result.get("domain_score", 0),
                        },
                        "matched_skills": result.get("matched_skills", []),
                        "missing_skills": result.get("missing_skills", []),
                        "reasoning": result.get("reasoning", ""),
                        "recommendation": result.get("recommendation", ""),
                    })

            for job_name in [j.get('title', '')[:30] for j in batch_jobs]:
                print(f"    scored: {job_name}")

        except Exception as e:
            print(f"    Batch error: {e} — skipping batch")
            continue

        time.sleep(1)  # rate limiting between batches

    return scored


# =============================================================================
# SCORE BOOSTING
# =============================================================================

def apply_boosts(scored_jobs: list[dict], prefs: dict) -> list[dict]:
    """Apply preference-based score boosts."""
    scoring = prefs.get("scoring", {})
    company_prefs = prefs.get("companies", {})
    keyword_prefs = prefs.get("keywords", {})

    priority_companies = [c.lower() for c in company_prefs.get("priority", [])]
    boost_keywords = [k.lower() for k in keyword_prefs.get("boost", [])]
    priority_boost = scoring.get("priority_company_boost", 10)
    keyword_boost_per = scoring.get("keyword_boost", 2)

    for job in scored_jobs:
        boost = 0
        company = job.get("company_token", "").lower()
        desc_lower = job.get("description", "").lower()

        # Priority company boost
        if company in priority_companies:
            boost += priority_boost
            job["_boosted_reason"] = job.get("_boosted_reason", [])
            job["_boosted_reason"].append(f"priority company (+{priority_boost})")

        # Keyword boost
        kw_matches = sum(1 for kw in boost_keywords if kw in desc_lower)
        if kw_matches > 0:
            kw_boost = min(kw_matches * keyword_boost_per, 15)  # cap at 15
            boost += kw_boost
            job["_boosted_reason"] = job.get("_boosted_reason", [])
            job["_boosted_reason"].append(f"keyword boost x{kw_matches} (+{kw_boost})")

        job["base_score"] = job["match_score"]
        job["match_score"] = min(100, job["match_score"] + boost)
        job["boost_applied"] = boost

    return scored_jobs


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Score jobs against resume with filtering")
    parser.add_argument("--resume", required=True, help="Path to master resume JSON")
    parser.add_argument("--jobs", required=True, help="Path to fetched jobs JSON")
    parser.add_argument("--preferences", default="config/preferences.yaml",
                        help="Path to preferences YAML")
    parser.add_argument("--output", default="output/scored_jobs.json", help="Output path")
    parser.add_argument("--top", type=int, default=10, help="Show top N results")
    parser.add_argument("--force-tfidf", action="store_true",
                        help="Force TF-IDF only (skip Gemini)")
    args = parser.parse_args()

    resume = load_json(args.resume)
    jobs_data = load_json(args.jobs)
    prefs = load_preferences(args.preferences)
    jobs = jobs_data.get("jobs", [])

    print(f"Loaded {len(jobs)} jobs from {args.jobs}")

    # Step 1: Filter jobs based on preferences
    filtered = filter_jobs(jobs, prefs)
    filtered_out = len(jobs) - len(filtered)
    print(f"After filtering: {len(filtered)} jobs ({filtered_out} excluded by preferences)\n")

    if not filtered:
        print("No jobs passed filters. Adjust config/preferences.yaml.")
        return

    # Step 2: TF-IDF scoring (fast pass on all jobs)
    resume_text = extract_resume_text(resume)
    resume_skills = extract_resume_skills(resume)
    print(f"Resume skills: {len(resume_skills)} detected")
    print("Running TF-IDF scoring...\n")

    tfidf_scored = [tfidf_score_job(resume, resume_text, resume_skills, job) for job in filtered]
    tfidf_scored.sort(key=lambda j: j["match_score"], reverse=True)

    # Step 3: Gemini scoring (on top N from TF-IDF, in batches)
    scoring_prefs = prefs.get("scoring", {})
    use_gemini = scoring_prefs.get("use_gemini", True) and not args.force_tfidf
    gemini_max = scoring_prefs.get("gemini_max_jobs", 15)
    gemini_per_batch = scoring_prefs.get("gemini_jobs_per_batch", 5)

    if use_gemini:
        candidates = tfidf_scored[:gemini_max]
        num_batches = math.ceil(len(candidates) / gemini_per_batch)
        print(f"\nRunning Gemini AI scoring: {len(candidates)} jobs in {num_batches} batches ({gemini_per_batch}/batch)...")

        try:
            from google import genai
            gemini_results = gemini_score_jobs(resume, candidates, batch_size=gemini_per_batch)

            if gemini_results:
                gemini_by_id = {j["id"]: j for j in gemini_results}
                for i, job in enumerate(tfidf_scored):
                    if job["id"] in gemini_by_id:
                        tfidf_scored[i] = gemini_by_id[job["id"]]

                print(f"Gemini scored {len(gemini_results)} jobs successfully\n")
            else:
                print("Gemini scoring returned no results — using TF-IDF scores\n")
        except Exception as e:
            print(f"Gemini scoring failed: {e} — using TF-IDF scores\n")
    else:
        print("Gemini scoring disabled — using TF-IDF only\n")

    # Step 4: Apply boosts
    scored = apply_boosts(tfidf_scored, prefs)
    scored.sort(key=lambda j: j["match_score"], reverse=True)

    # Step 5: Apply minimum score filter
    min_score = scoring_prefs.get("min_score", 0)
    if min_score > 0:
        before = len(scored)
        scored = [j for j in scored if j["match_score"] >= min_score]
        print(f"Min score filter ({min_score}): {before - len(scored)} jobs removed\n")

    # Display
    print(f"{'Rank':<5} {'Score':<7} {'Method':<8} {'Title':<35} {'Company':<15} {'Location':<20} {'Rec'}")
    print("-" * 110)
    for i, job in enumerate(scored[:args.top], 1):
        rec = job.get("recommendation", "")
        method = job.get("scoring_method", "tfidf")[:6]
        boost_info = f" (+{job['boost_applied']})" if job.get("boost_applied", 0) > 0 else ""
        print(
            f"{i:<5} {job['match_score']:<7}{method:<8} "
            f"{job['title'][:33]:<35} "
            f"{job['company_token'][:13]:<15} "
            f"{job.get('location', 'N/A')[:18]:<20} "
            f"{rec}"
        )
        if job.get("matched_skills"):
            print(f"      Match: {', '.join(job['matched_skills'][:8])}")
        if job.get("missing_skills"):
            print(f"      Gaps:  {', '.join(job['missing_skills'][:5])}")
        if job.get("reasoning"):
            print(f"      Why:   {job['reasoning'][:100]}")
        if boost_info:
            reasons = "; ".join(job.get("_boosted_reason", []))
            print(f"      Boost: {reasons}")
        print()

    # Save
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({
            "total": len(scored),
            "filtered_out": filtered_out,
            "scoring_method": "gemini+tfidf" if use_gemini else "tfidf",
            "jobs": scored,
        }, f, indent=2)

    print(f"Scored jobs saved to {args.output}")
    print(f"Total: {len(scored)} jobs | Filtered out: {filtered_out} | Method: {'gemini+tfidf' if use_gemini else 'tfidf'}")


if __name__ == "__main__":
    main()
