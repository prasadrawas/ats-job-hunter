#!/usr/bin/env python3
"""Fetch application form questions from ATS APIs and generate answers.

Strategy:
1. Fetch the application form questions from the ATS API
2. Classify each question: standard (use config) vs custom (use Gemini)
3. Auto-answer standard questions from config/answers.yaml
4. Use Gemini AI for open-ended/custom questions
5. Flag questions needing human review
6. Output a complete answers payload ready for submission
"""

import argparse
import json
import math
import os
import re
from datetime import date
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv

load_dotenv()

GEMINI_MODEL = "gemini-2.0-flash"

GEMINI_BATCH_ANSWER_PROMPT = """You are answering job application questions on behalf of a candidate.

## RULES
1. Answer ONLY using facts from the candidate's resume — NEVER fabricate
2. Use specific metrics and examples from the resume
3. Be professional and confident, not desperate
4. For "Why this company/role?" — connect candidate experience to job requirements
5. PLATFORM: {platform}
   - If Lever: screening answers MUST be 50-150 words each (one-liners trigger auto-rejection)
   - If Greenhouse: concise answers OK (200-500 chars)
   - If Ashby: answers must EXACTLY match resume content (mismatches are flagged to recruiters)

## CANDIDATE
Name: {name} | Experience: {years}+ years
Skills: {skills}
Recent: {recent_role} at {recent_company}
Highlights: {achievements}

## JOB
Title: {job_title} | Company: {company}
Description: {job_excerpt}

## QUESTIONS TO ANSWER
{questions_block}

## OUTPUT FORMAT
Return ONLY a valid JSON array (no markdown, no code fences). One object per question:
[
  {{"question_index": 0, "answer": "Your concise answer here"}},
  {{"question_index": 1, "answer": "Another answer"}}
]
"""


# =============================================================================
# FETCH APPLICATION FORM QUESTIONS
# =============================================================================

def fetch_greenhouse_questions(board_token: str, job_id: str) -> list[dict]:
    """Fetch application form from Greenhouse API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}?questions=true"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        questions = []
        for q in data.get("questions", []):
            fields = q.get("fields", [])
            field = fields[0] if fields else {}
            questions.append({
                "id": field.get("name", ""),
                "label": q.get("label", ""),
                "type": field.get("type", "input_text"),
                "required": q.get("required", False),
                "options": [
                    {"value": v.get("value"), "label": v.get("label", "")}
                    for v in field.get("values", [])
                ],
                "platform": "greenhouse",
            })

        # Location questions
        for q in data.get("location_questions", []):
            fields = q.get("fields", [])
            field = fields[0] if fields else {}
            questions.append({
                "id": field.get("name", "location"),
                "label": q.get("label", "Location"),
                "type": field.get("type", "input_text"),
                "required": q.get("required", False),
                "options": [],
                "platform": "greenhouse",
            })

        return questions
    except requests.RequestException as e:
        print(f"Error fetching Greenhouse questions: {e}")
        return []


def fetch_lever_questions(company: str, posting_id: str) -> list[dict]:
    """Fetch application form from Lever API."""
    url = f"https://api.lever.co/v0/postings/{company}/{posting_id}/apply"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        questions = []
        for section in data if isinstance(data, list) else [data]:
            for field in section.get("fields", section.get("customQuestions", [])):
                questions.append({
                    "id": field.get("id", field.get("name", "")),
                    "label": field.get("text", field.get("label", "")),
                    "type": field.get("type", "text"),
                    "required": field.get("required", False),
                    "options": [
                        {"value": o.get("value", o), "label": o.get("text", str(o))}
                        for o in field.get("options", [])
                    ],
                    "platform": "lever",
                })
        return questions
    except requests.RequestException as e:
        print(f"Error fetching Lever questions: {e}")
        return []


def fetch_ashby_questions(board_slug: str, job_id: str) -> list[dict]:
    """Fetch application form from Ashby API."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board_slug}"
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        # Find the specific job posting
        target = None
        for job in data.get("jobs", []):
            if str(job.get("id")) == str(job_id):
                target = job
                break

        if not target:
            print(f"Job {job_id} not found on Ashby board {board_slug}")
            return []

        questions = []
        form_def = target.get("applicationFormDefinition", {})
        for section in form_def.get("sections", []):
            for field in section.get("fields", []):
                questions.append({
                    "id": field.get("id", ""),
                    "label": field.get("title", field.get("label", "")),
                    "type": field.get("type", "String"),
                    "required": field.get("isRequired", False),
                    "options": [
                        {"value": o.get("id", o.get("value")), "label": o.get("label", "")}
                        for o in field.get("selectableValues", [])
                    ],
                    "platform": "ashby",
                })
        return questions
    except requests.RequestException as e:
        print(f"Error fetching Ashby questions: {e}")
        return []


QUESTION_FETCHERS = {
    "greenhouse": fetch_greenhouse_questions,
    "lever": fetch_lever_questions,
    "ashby": fetch_ashby_questions,
}


# =============================================================================
# CLASSIFY AND ANSWER QUESTIONS
# =============================================================================

# Standard field names that map directly to applicant info
STANDARD_FIELDS = {
    "first_name": lambda a, _: a.get("first_name", ""),
    "last_name": lambda a, _: a.get("last_name", ""),
    "email": lambda a, _: a.get("email", ""),
    "phone": lambda a, _: a.get("phone", ""),
    "name": lambda a, _: f"{a.get('first_name', '')} {a.get('last_name', '')}",
    "location": lambda _, ans: ans.get("relocation", {}).get("current_location", ""),
    "linkedin": lambda _, ans: ans.get("links", {}).get("linkedin", ""),
    "github": lambda _, ans: ans.get("links", {}).get("github", ""),
    "portfolio": lambda _, ans: ans.get("links", {}).get("portfolio", ""),
    "website": lambda _, ans: ans.get("links", {}).get("portfolio", ""),
}

# Label patterns that map to specific answer config paths
LABEL_MATCHERS = [
    (r"(?i)authorized .* work|work authorization|legally authorized",
     lambda ans: "Yes" if ans.get("work_authorization", {}).get("authorized_india") else "No"),
    (r"(?i)require .* sponsorship|visa sponsorship",
     lambda ans: "Yes" if ans.get("work_authorization", {}).get("requires_sponsorship") else "No"),
    (r"(?i)willing .* relocate|open to relocation",
     lambda ans: "Yes" if ans.get("relocation", {}).get("willing_to_relocate") else "No"),
    (r"(?i)preferred .* (work|arrangement|mode)",
     lambda ans: ans.get("relocation", {}).get("preferred_work_mode", "")),
    (r"(?i)earliest .* start|start date|when .* start",
     lambda ans: ans.get("availability", {}).get("earliest_start", "")),
    (r"(?i)notice period",
     lambda ans: ans.get("availability", {}).get("notice_period", "")),
    (r"(?i)highest .* (degree|education|level)",
     lambda ans: ans.get("education", {}).get("highest_degree", "")),
    (r"(?i)years .* (experience|professional)",
     lambda ans: str(ans.get("experience", {}).get("years", ""))),
    (r"(?i)current salary|salary .* current",
     lambda ans: ans.get("salary", {}).get("current", "")),
    (r"(?i)salary .* expectation|expected salary|desired salary|compensation",
     lambda ans: ans.get("salary", {}).get("expectation", "")),
]


def _resolve_salary(ans: dict, job: dict, field: str) -> str:
    """Return the configured salary value for the given field."""
    salary = ans.get("salary", {})

    if field == "current":
        return salary.get("current_usd", "")
    elif field == "expectation":
        return salary.get("expectation", "")
    elif field == "minimum":
        return str(salary.get("minimum_usd", ""))
    return ""


def match_standard_field(question: dict, applicant: dict, answers_config: dict, job: dict = None) -> str | None:
    """Try to match a question to a standard field or config answer."""
    field_id = question.get("id", "").lower()
    label = question.get("label", "")
    job = job or {}

    # Direct field name match
    for key, resolver in STANDARD_FIELDS.items():
        if key in field_id:
            value = resolver(applicant, answers_config)
            if value:
                return value

    # Salary — location-aware (INR for India jobs, USD for international)
    if re.search(r"(?i)current salary|salary .* current", label):
        value = _resolve_salary(answers_config, job, "current")
        if value:
            return value
    if re.search(r"(?i)salary .* expectation|expected salary|desired salary|compensation", label):
        value = _resolve_salary(answers_config, job, "expectation")
        if value:
            return value

    # Label pattern matching (skip salary patterns — handled above)
    for pattern, resolver in LABEL_MATCHERS:
        if "salary" in pattern:
            continue
        if re.search(pattern, label):
            value = resolver(answers_config)
            if value:
                return str(value)

    # Skill-specific years questions
    skill_years = answers_config.get("experience", {}).get("skill_years", {})
    skill_match = re.search(r"(?i)years .* experience .* ([\w\+\#\/\.]+)", label)
    if skill_match:
        skill = skill_match.group(1)
        for config_skill, years in skill_years.items():
            if config_skill.lower() == skill.lower():
                return str(years)

    # Custom patterns from config
    for pattern_rule in answers_config.get("patterns", []):
        pattern = pattern_rule.get("pattern", "")
        answer = pattern_rule.get("answer", "")
        if pattern and re.search(pattern, label, re.IGNORECASE):
            if answer == "__GEMINI__":
                return None  # fall through to Gemini
            if answer == "__COVER_LETTER__":
                return "__COVER_LETTER__"
            return str(answer)

    return None


def select_option(question: dict, answer_text: str) -> str | int | list | None:
    """For select/multi-select questions, find the best matching option."""
    options = question.get("options", [])
    if not options:
        return answer_text

    answer_lower = answer_text.lower().strip()

    # Exact match
    for opt in options:
        if opt.get("label", "").lower().strip() == answer_lower:
            return opt.get("value")

    # Partial match
    for opt in options:
        label = opt.get("label", "").lower()
        if answer_lower in label or label in answer_lower:
            return opt.get("value")

    # Yes/No mapping
    if answer_lower in ("yes", "true", "1"):
        for opt in options:
            if opt.get("label", "").lower() in ("yes", "true"):
                return opt.get("value")
    if answer_lower in ("no", "false", "0"):
        for opt in options:
            if opt.get("label", "").lower() in ("no", "false"):
                return opt.get("value")

    return answer_text


def batch_answer_with_gemini(
    questions: list[dict],
    resume: dict,
    job: dict,
    years: str,
    platform: str = "greenhouse",
) -> dict[int, str]:
    """Use Gemini to answer ALL open-ended questions in ONE API call.

    Returns: {question_index: answer_text}
    Saves ~80% tokens vs per-question calls (resume+JD sent once, not per question).
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {}

    if not questions:
        return {}

    try:
        from google import genai
        client = genai.Client(api_key=api_key)

        basics = resume.get("basics", {})
        skills_flat = []
        for sg in resume.get("skills", []):
            if isinstance(sg, dict):
                skills_flat.extend(sg.get("keywords", []))

        work = resume.get("work", [])
        recent = work[0] if work else {}

        # Build questions block
        q_parts = []
        for i, q in enumerate(questions):
            q_parts.append(f"Q{i}: {q.get('label', '')} (type: {q.get('type', 'text')})")
        questions_block = "\n".join(q_parts)

        prompt = GEMINI_BATCH_ANSWER_PROMPT.format(
            name=basics.get("name", ""),
            years=years,
            skills=", ".join(skills_flat[:30]),
            recent_role=recent.get("position", ""),
            recent_company=recent.get("company", ""),
            achievements="; ".join(recent.get("highlights", [])[:4])[:400],
            job_title=job.get("title", ""),
            company=job.get("company_token", ""),
            job_excerpt=job.get("description", "")[:1000],
            questions_block=questions_block,
            platform=platform,
        )

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

        return {r.get("question_index", i): r.get("answer", "") for i, r in enumerate(results)}
    except Exception as e:
        print(f"    Gemini batch answer error: {e}")
        return {}


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def process_questions(
    questions: list[dict],
    resume: dict,
    job: dict,
    answers_config: dict,
    cover_letter_text: str = "",
    platform: str = "greenhouse",
) -> list[dict]:
    """Process all questions: auto-answer where possible, Gemini for the rest."""
    basics = resume.get("basics", {})
    applicant = {
        "first_name": basics.get("name", "").split()[0] if basics.get("name") else "",
        "last_name": " ".join(basics.get("name", "").split()[1:]) if basics.get("name") else "",
        "email": basics.get("email", ""),
        "phone": basics.get("phone", ""),
    }

    career_start = basics.get("careerStartDate", "")
    if career_start:
        delta = (date.today() - date.fromisoformat(career_start)).days
        years = str(math.floor((delta / 365.25) * 10) / 10)
    else:
        years = str(answers_config.get("experience", {}).get("years", "5"))

    # First pass: resolve standard answers, collect Gemini questions
    results = []
    gemini_questions = []  # (index_in_results, question)

    for q in questions:
        label = q.get("label", "")
        q_type = q.get("type", "")
        required = q.get("required", False)

        # Skip file upload fields
        if q_type in ("input_file", "File"):
            results.append({
                **q,
                "answer": "__FILE_UPLOAD__",
                "source": "file_upload",
                "needs_review": False,
            })
            continue

        # Skip hidden fields
        if q_type == "input_hidden":
            results.append({
                **q,
                "answer": "",
                "source": "skipped",
                "needs_review": False,
            })
            continue

        # Try standard matching first
        answer = match_standard_field(q, applicant, answers_config, job)

        if answer == "__COVER_LETTER__":
            answer = cover_letter_text if cover_letter_text else "__NEEDS_REVIEW__"
            source = "cover_letter"
        elif answer is not None:
            source = "config"
        else:
            # Mark for Gemini batch — don't call yet
            answer = "__PENDING_GEMINI__"
            source = "gemini"
            gemini_questions.append((len(results), q))

        # Append result (Gemini ones get answer filled in second pass)
        needs_review = answer in ("__NEEDS_REVIEW__", "__PENDING_GEMINI__")
        if answer != "__PENDING_GEMINI__" and q.get("options"):
            answer = select_option(q, str(answer))

        results.append({
            **q,
            "answer": answer,
            "source": source,
            "needs_review": needs_review or (required and not answer),
        })

    # Second pass: batch all Gemini questions in ONE API call
    if gemini_questions:
        print(f"    Batching {len(gemini_questions)} questions for Gemini (1 API call)...")
        gemini_qs = [q for _, q in gemini_questions]
        gemini_answers = batch_answer_with_gemini(gemini_qs, resume, job, years, platform)

        for batch_idx, (result_idx, q) in enumerate(gemini_questions):
            answer = gemini_answers.get(batch_idx, "__NEEDS_REVIEW__")
            results[result_idx]["answer"] = answer
            if answer and answer != "__NEEDS_REVIEW__":
                results[result_idx]["source"] = "gemini"
            else:
                results[result_idx]["needs_review"] = True

            # For select fields, map Gemini answer text to option value
            if results[result_idx].get("options") and results[result_idx]["answer"] != "__NEEDS_REVIEW__":
                results[result_idx]["answer"] = select_option(
                    results[result_idx], str(results[result_idx]["answer"])
                )

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Fetch and answer ATS application form questions"
    )
    parser.add_argument("--master-resume", required=True, help="Path to master resume JSON")
    parser.add_argument("--job-id", required=True, help="Job ID")
    parser.add_argument("--jobs", required=True, help="Path to scored jobs JSON")
    parser.add_argument("--platform", required=True, help="ATS platform")
    parser.add_argument("--answers-config", default="config/answers.yaml",
                        help="Path to answers config")
    parser.add_argument("--cover-letter", default="", help="Path to cover letter text file")
    parser.add_argument("--output-dir", default="output/tailored", help="Output directory")
    args = parser.parse_args()

    with open(args.master_resume) as f:
        resume = json.load(f)
    with open(args.jobs) as f:
        jobs_data = json.load(f)

    answers_config = {}
    if Path(args.answers_config).exists():
        with open(args.answers_config) as f:
            answers_config = yaml.safe_load(f) or {}

    cover_letter_text = ""
    if args.cover_letter and Path(args.cover_letter).exists():
        with open(args.cover_letter) as f:
            cover_letter_text = f.read()

    # Find target job
    target_job = None
    for job in jobs_data.get("jobs", []):
        if str(job.get("id")) == str(args.job_id):
            target_job = job
            break

    if not target_job:
        print(f"Error: Job {args.job_id} not found")
        return

    platform = args.platform.lower()
    company = target_job.get("company_token", "")

    print(f"Fetching application form: {target_job['title']} at {company} ({platform})")

    # Step 1: Fetch questions
    if platform not in QUESTION_FETCHERS:
        print(f"Error: Unknown platform '{platform}'")
        return

    fetcher = QUESTION_FETCHERS[platform]
    questions = fetcher(company, args.job_id)
    print(f"Found {len(questions)} form fields\n")

    if not questions:
        print("No application questions found (or API returned empty form)")
        print("The job may require manual application on the company website.")
        return

    # Step 2: Process questions
    print("Answering questions...")
    answered = process_questions(
        questions, resume, target_job, answers_config, cover_letter_text, platform
    )

    # Step 3: Display results
    needs_review = [a for a in answered if a.get("needs_review")]
    auto_answered = [a for a in answered if not a.get("needs_review")]

    print(f"\n{'=' * 70}")
    print(f"APPLICATION FORM ANSWERS — {target_job['title']} at {company}")
    print(f"{'=' * 70}")

    for a in answered:
        status = "REVIEW" if a.get("needs_review") else "OK"
        req = "*" if a.get("required") else " "
        source = a.get("source", "")
        answer_preview = str(a.get("answer", ""))[:80]
        print(f"  [{status:6}] {req} {a.get('label', '')[:40]:<42} → {answer_preview:<50} ({source})")

    print(f"\n  Auto-answered: {len(auto_answered)}")
    print(f"  Needs review:  {len(needs_review)}")

    if needs_review:
        print(f"\n  ⚠ The following questions need your review:")
        for a in needs_review:
            print(f"    - {a.get('label', '')} (required: {a.get('required', False)})")

    # Step 4: Save into human-readable directory per job
    job_title = target_job.get("title", "unknown")
    today = date.today().isoformat()
    title_slug = re.sub(r'[^a-z0-9]+', '-', job_title.lower()).strip('-')[:50]
    dir_name = f"{company}_{title_slug}_{today}"

    output_dir = Path(args.output_dir) / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "answers.json"

    with open(output_path, "w") as f:
        json.dump({
            "job_id": args.job_id,
            "company": company,
            "platform": platform,
            "total_questions": len(answered),
            "auto_answered": len(auto_answered),
            "needs_review": len(needs_review),
            "answers": answered,
        }, f, indent=2)

    print(f"\nAnswers saved to {output_path}")
    if needs_review:
        print(f"Edit {output_path} to fill in questions marked REVIEW, then run apply_job.py")


if __name__ == "__main__":
    main()
