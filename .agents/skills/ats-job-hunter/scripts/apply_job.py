#!/usr/bin/env python3
"""Submit job applications via ATS platform APIs with form question answers."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()


def load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def build_answers_payload(answers: list[dict], platform: str) -> dict:
    """Convert answered questions into platform-specific submission payload."""
    payload = {}

    for a in answers:
        field_id = a.get("id", "")
        answer = a.get("answer", "")
        source = a.get("source", "")

        # Skip unanswered, file uploads, and hidden fields
        if answer in ("__NEEDS_REVIEW__", "__FILE_UPLOAD__", "") or source == "skipped":
            continue

        if platform == "greenhouse":
            # Greenhouse uses field name as key directly
            payload[field_id] = answer

        elif platform == "lever":
            # Lever uses customQuestions array or direct field names
            payload[field_id] = answer

        elif platform == "ashby":
            # Ashby uses field IDs
            payload[field_id] = answer

    return payload


def apply_greenhouse(
    job_id: str,
    board_token: str,
    applicant: dict,
    resume_pdf_path: str,
    answers_payload: dict,
    dry_run: bool,
) -> dict:
    """Submit application via Greenhouse public API."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}"

    payload = {
        "first_name": applicant.get("first_name", ""),
        "last_name": applicant.get("last_name", ""),
        "email": applicant.get("email", ""),
        "phone": applicant.get("phone", ""),
        **answers_payload,
    }

    print(f"  [Greenhouse] Endpoint: {url}")
    print(f"  [Greenhouse] Fields: {len(payload)} total ({len(answers_payload)} custom)")

    if dry_run:
        return {"status": "dry_run", "payload_keys": list(payload.keys())}

    # Multipart form for file upload
    files = {}
    if resume_pdf_path and Path(resume_pdf_path).exists():
        files["resume"] = (
            Path(resume_pdf_path).name,
            open(resume_pdf_path, "rb"),
            "application/pdf",
        )

    try:
        auth = (os.getenv("GREENHOUSE_API_KEY"), "") if os.getenv("GREENHOUSE_API_KEY") else None
        resp = requests.post(url, data=payload, files=files, auth=auth, timeout=60)
        return {"status": resp.status_code, "response": resp.text[:500]}
    except requests.RequestException as e:
        return {"status": "error", "message": str(e)}


def apply_lever(
    job_id: str,
    board_token: str,
    applicant: dict,
    resume_pdf_path: str,
    answers_payload: dict,
    dry_run: bool,
) -> dict:
    """Submit application via Lever postings API."""
    url = f"https://api.lever.co/v0/postings/{board_token}/{job_id}/apply"

    payload = {
        "name": f"{applicant.get('first_name', '')} {applicant.get('last_name', '')}",
        "email": applicant.get("email", ""),
        "phone": applicant.get("phone", ""),
        **answers_payload,
    }

    print(f"  [Lever] Endpoint: {url}")
    print(f"  [Lever] Fields: {len(payload)} total ({len(answers_payload)} custom)")

    if dry_run:
        return {"status": "dry_run", "payload_keys": list(payload.keys())}

    files = {}
    if resume_pdf_path and Path(resume_pdf_path).exists():
        files["resume"] = (
            Path(resume_pdf_path).name,
            open(resume_pdf_path, "rb"),
            "application/pdf",
        )

    try:
        auth = (os.getenv("GREENHOUSE_API_KEY"), "") if os.getenv("GREENHOUSE_API_KEY") else None
        resp = requests.post(url, data=payload, files=files, auth=auth, timeout=60)
        return {"status": resp.status_code, "response": resp.text[:500]}
    except requests.RequestException as e:
        return {"status": "error", "message": str(e)}


def apply_ashby(
    job_id: str,
    board_token: str,
    applicant: dict,
    resume_pdf_path: str,
    answers_payload: dict,
    dry_run: bool,
) -> dict:
    """Submit application via Ashby API."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board_token}/application"

    payload = {
        "jobPostingId": job_id,
        "name": f"{applicant.get('first_name', '')} {applicant.get('last_name', '')}",
        "email": applicant.get("email", ""),
        "phone": applicant.get("phone", ""),
        **answers_payload,
    }

    print(f"  [Ashby] Endpoint: {url}")
    print(f"  [Ashby] Fields: {len(payload)} total ({len(answers_payload)} custom)")

    if dry_run:
        return {"status": "dry_run", "payload_keys": list(payload.keys())}

    files = {}
    if resume_pdf_path and Path(resume_pdf_path).exists():
        files["resume"] = (
            Path(resume_pdf_path).name,
            open(resume_pdf_path, "rb"),
            "application/pdf",
        )

    try:
        resp = requests.post(url, data=payload, files=files, timeout=60)
        return {"status": resp.status_code, "response": resp.text[:500]}
    except requests.RequestException as e:
        return {"status": "error", "message": str(e)}


APPLIERS = {
    "greenhouse": apply_greenhouse,
    "lever": apply_lever,
    "ashby": apply_ashby,
}


def main():
    parser = argparse.ArgumentParser(description="Apply to a job via ATS API")
    parser.add_argument("--resume-pdf", required=True, help="Path to tailored resume PDF")
    parser.add_argument("--job-id", required=True, help="Job ID to apply to")
    parser.add_argument("--platform", required=True, help="ATS platform name")
    parser.add_argument("--board-token", default="", help="Board token/slug for Greenhouse/Ashby")
    parser.add_argument("--answers", default="", help="Path to answered questions JSON")
    parser.add_argument("--master-resume", default="resume/master-resume.json",
                        help="Path to master resume JSON (for applicant info)")
    parser.add_argument("--log", default="output/applications_log.json",
                        help="Applications log file")
    parser.add_argument("--submit", action="store_true", default=False,
                        help="Actually submit the application (disables dry-run)")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Simulate without actually submitting")
    args = parser.parse_args()

    # Default to dry_run unless --submit is explicitly passed
    dry_run = not args.submit

    # Load applicant info from master resume
    master = load_json(args.master_resume)
    basics = master.get("basics", {})
    applicant = {
        "first_name": basics.get("name", "").split()[0] if basics.get("name") else "",
        "last_name": " ".join(basics.get("name", "").split()[1:]) if basics.get("name") else "",
        "email": basics.get("email", ""),
        "phone": basics.get("phone", ""),
    }

    # Load answered questions if provided
    answers_payload = {}
    answers_meta = {}
    if args.answers and Path(args.answers).exists():
        answers_data = load_json(args.answers)
        answers_list = answers_data.get("answers", [])

        # Check for unreviewed questions
        needs_review = [a for a in answers_list if a.get("needs_review")]
        if needs_review:
            print(f"WARNING: {len(needs_review)} questions still need review:")
            for a in needs_review:
                print(f"  - {a.get('label', '')} (required: {a.get('required', False)})")
            print(f"\nEdit {args.answers} to fill in answers marked 'needs_review', then re-run.")
            print("Continuing with available answers...\n")

        answers_payload = build_answers_payload(answers_list, args.platform.lower())
        answers_meta = {
            "total_questions": answers_data.get("total_questions", 0),
            "auto_answered": answers_data.get("auto_answered", 0),
            "needs_review": answers_data.get("needs_review", 0),
        }

    platform = args.platform.lower()
    if platform not in APPLIERS:
        print(f"Error: Unknown platform '{platform}'. Supported: {list(APPLIERS.keys())}")
        return

    # Duplicate application guard
    log_path = Path(args.log)
    if log_path.exists():
        with open(log_path) as f:
            existing_log = json.load(f)
        applied_jobs = [
            a for a in existing_log.get("applications", [])
            if a.get("job_id") == args.job_id and not a.get("dry_run", True)
        ]
        if applied_jobs:
            last = applied_jobs[-1]
            print(f"SKIPPED: Already applied to job {args.job_id} on {last.get('timestamp', 'unknown')}")
            print(f"Platform: {last.get('platform', '')} | Result: {last.get('result', {}).get('status', '')}")
            print(f"To re-apply, remove this entry from {args.log}")
            return

    print(f"Applying to job {args.job_id} via {platform}")
    print(f"Resume PDF: {args.resume_pdf}")
    print(f"Custom fields: {len(answers_payload)}")

    if dry_run:
        print("[DRY RUN] Submission simulated\n")
    else:
        print("[LIVE SUBMISSION] Submitting application to ATS API...\n")

    board_token = args.board_token or answers_data.get("company", applicant.get("company_token", args.job_id))
    applier = APPLIERS[platform]
    result = applier(
        job_id=args.job_id,
        board_token=board_token,
        applicant=applicant,
        resume_pdf_path=args.resume_pdf,
        answers_payload=answers_payload,
        dry_run=dry_run,
    )

    # Log the application
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "job_id": args.job_id,
        "platform": platform,
        "resume_pdf": args.resume_pdf,
        "dry_run": dry_run,
        "answers_summary": answers_meta,
        "result": result,
    }

    log_path = Path(args.log)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if log_path.exists():
        with open(log_path) as f:
            log_data = json.load(f)
    else:
        log_data = {"applications": []}

    log_data["applications"].append(log_entry)

    with open(log_path, "w") as f:
        json.dump(log_data, f, indent=2)

    print(f"\nApplication logged to {args.log}")
    print(f"Result: {json.dumps(result, indent=2)}")


if __name__ == "__main__":
    main()
