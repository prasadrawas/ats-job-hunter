#!/usr/bin/env python3
"""Validate that a generated PDF resume is ATS-friendly.

Performs the "Notepad test" programmatically:
- Extracts text from PDF
- Checks section ordering
- Verifies contact info is parseable
- Detects column interleaving
"""

import argparse
import json
import re
import sys
from pathlib import Path

import pdfplumber


EXPECTED_SECTIONS_ORDER = [
    "summary",
    "skills",
    "experience",
    "projects",
    "education",
]

# Patterns ATS parsers look for
SECTION_PATTERNS = {
    "summary": r"(?i)\b(summary|professional summary|profile|objective)\b",
    "skills": r"(?i)\b(skills|technical skills|core competencies)\b",
    "experience": r"(?i)\b(work experience|experience|employment|professional experience)\b",
    "projects": r"(?i)\b(projects|key projects|selected projects)\b",
    "education": r"(?i)\b(education|academic|qualifications)\b",
}


def extract_text(pdf_path: str) -> str:
    """Extract all text from PDF."""
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
    return "\n".join(text_parts)


def check_text_extractable(text: str) -> tuple[bool, str]:
    """Check if meaningful text was extracted."""
    if not text or len(text.strip()) < 50:
        return False, "FAIL: PDF appears to be image-based or empty — no text extracted"
    return True, f"PASS: {len(text)} characters extracted"


def check_contact_info(text: str) -> tuple[bool, str]:
    """Check if email and phone are findable in extracted text."""
    issues = []
    email_match = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    if not email_match:
        issues.append("Email not found in extracted text")

    phone_match = re.search(r"[\+]?[\d\s\-\(\)]{10,}", text)
    if not phone_match:
        issues.append("Phone not found in extracted text")

    linkedin_match = re.search(r"linkedin\.com", text, re.IGNORECASE)
    if not linkedin_match:
        issues.append("LinkedIn URL not found (optional)")

    if issues:
        critical = [i for i in issues if "optional" not in i]
        if critical:
            return False, f"FAIL: {'; '.join(critical)}"
        return True, f"WARN: {'; '.join(issues)}"

    return True, f"PASS: Email ({email_match.group()}) and phone found"


def check_section_order(text: str) -> tuple[bool, str]:
    """Check that sections appear in the expected ATS-friendly order."""
    found_sections = []

    for section_name in EXPECTED_SECTIONS_ORDER:
        pattern = SECTION_PATTERNS.get(section_name, "")
        match = re.search(pattern, text)
        if match:
            found_sections.append((section_name, match.start()))

    # Check ordering
    if len(found_sections) < 3:
        return False, f"FAIL: Only {len(found_sections)} standard sections found — need at least 3"

    positions = [pos for _, pos in found_sections]
    is_ordered = all(positions[i] <= positions[i + 1] for i in range(len(positions) - 1))

    section_names = [name for name, _ in found_sections]

    if not is_ordered:
        return False, f"FAIL: Sections out of order: {section_names} — ATS will misparse"

    return True, f"PASS: {len(found_sections)} sections in correct order: {section_names}"


def check_no_interleaving(text: str) -> tuple[bool, str]:
    """Detect text interleaving that indicates column mixing."""
    lines = text.split("\n")
    short_alternating = 0

    for i in range(1, len(lines) - 1):
        curr = len(lines[i].strip())
        prev = len(lines[i - 1].strip())
        next_line = len(lines[i + 1].strip())

        # Detect pattern: long-short-long which suggests column interleaving
        if prev > 40 and curr < 15 and next_line > 40 and curr > 0:
            short_alternating += 1

    if short_alternating > 5:
        return False, f"FAIL: Detected {short_alternating} potential column interleaving patterns"

    return True, "PASS: No column interleaving detected"


def check_standard_headings(text: str) -> tuple[bool, str]:
    """Check for standard section heading names (not creative ones)."""
    creative_patterns = [
        r"(?i)\bmy journey\b",
        r"(?i)\bwhat i do\b",
        r"(?i)\btoolkit\b",
        r"(?i)\bmy story\b",
        r"(?i)\babout me\b",
        r"(?i)\bprofessional journey\b",
    ]

    found_creative = []
    for pattern in creative_patterns:
        if re.search(pattern, text):
            found_creative.append(pattern.replace(r"(?i)\b", "").replace(r"\b", ""))

    if found_creative:
        return False, f"FAIL: Creative headings detected: {found_creative} — use standard names"

    return True, "PASS: No non-standard section headings detected"


# ---------------------------------------------------------------------------
# Keyword Coverage Check (JD vs. generated PDF)
# ---------------------------------------------------------------------------

# Common synonym mappings for tech skills
SKILL_SYNONYMS = {
    "react": {"reactjs", "react.js"},
    "reactjs": {"react", "react.js"},
    "react.js": {"react", "reactjs"},
    "node": {"nodejs", "node.js"},
    "nodejs": {"node", "node.js"},
    "node.js": {"node", "nodejs"},
    "javascript": {"js"},
    "js": {"javascript"},
    "typescript": {"ts"},
    "ts": {"typescript"},
    "python": {"py"},
    "golang": {"go"},
    "go": {"golang"},
    "postgres": {"postgresql"},
    "postgresql": {"postgres"},
    "k8s": {"kubernetes"},
    "kubernetes": {"k8s"},
    "ci/cd": {"cicd", "ci cd", "continuous integration", "continuous deployment"},
    "rest": {"restful", "rest api", "rest apis"},
    "restful": {"rest", "rest api"},
    "aws": {"amazon web services"},
    "gcp": {"google cloud", "google cloud platform"},
    "vue": {"vuejs", "vue.js"},
    "vuejs": {"vue", "vue.js"},
    "vue.js": {"vue", "vuejs"},
    "next.js": {"nextjs", "next"},
    "nextjs": {"next.js", "next"},
    "fastapi": {"fast api"},
    "mongodb": {"mongo"},
    "mongo": {"mongodb"},
    "docker": {"containerization"},
    "terraform": {"iac", "infrastructure as code"},
    "flutter": {"dart"},
    "swift": {"swiftui", "ios"},
    "kotlin": {"android"},
    "graphql": {"graph ql"},
    "tailwind": {"tailwindcss", "tailwind css"},
    "tailwindcss": {"tailwind", "tailwind css"},
}


def extract_jd_keywords(job_data: dict) -> list[dict]:
    """Extract keywords from a scored job entry.

    Returns a list of dicts: [{"keyword": "Python", "priority": "required"}, ...]
    Pulls from matched_skills, missing_skills, and the description.
    """
    keywords = []
    seen = set()

    # Matched skills from scoring are relevant keywords
    for skill in job_data.get("matched_skills", []):
        key = skill.lower().strip()
        if key and key not in seen:
            keywords.append({"keyword": skill, "priority": "required"})
            seen.add(key)

    # Missing skills are also required/preferred
    for skill in job_data.get("missing_skills", []):
        key = skill.lower().strip()
        if key and key not in seen:
            keywords.append({"keyword": skill, "priority": "preferred"})
            seen.add(key)

    return keywords


def check_keyword_in_text(keyword: str, text_lower: str) -> str:
    """Check if a keyword appears in text, directly or via synonym.

    Returns: "covered", "synonym-only", or "missing"
    """
    kw_lower = keyword.lower().strip()

    # Direct match (word boundary)
    if re.search(r"\b" + re.escape(kw_lower) + r"\b", text_lower):
        return "covered"

    # Synonym match
    synonyms = SKILL_SYNONYMS.get(kw_lower, set())
    for syn in synonyms:
        if re.search(r"\b" + re.escape(syn) + r"\b", text_lower):
            return "synonym-only"

    return "missing"


def check_keyword_coverage(text: str, job_data: dict) -> tuple[bool, str]:
    """Check how well the resume PDF covers JD keywords.

    Returns (passed, message) where passed is True if coverage >= 60%.
    """
    keywords = extract_jd_keywords(job_data)

    if not keywords:
        return True, "SKIP: No JD keywords available for comparison"

    text_lower = text.lower()

    # Build coverage table
    table_rows = []
    covered_count = 0
    synonym_count = 0
    missing_count = 0

    for entry in keywords:
        kw = entry["keyword"]
        priority = entry["priority"]
        status = check_keyword_in_text(kw, text_lower)

        if status == "covered":
            covered_count += 1
        elif status == "synonym-only":
            synonym_count += 1
        else:
            missing_count += 1

        table_rows.append((kw, priority, status))

    total = len(keywords)
    # Synonyms count as half coverage
    coverage_pct = round(((covered_count + synonym_count * 0.5) / total) * 100, 1)

    # Print coverage table
    print(f"\n  {'Keyword':<25} {'Priority':<12} {'Status'}")
    print(f"  {'-' * 25} {'-' * 12} {'-' * 15}")
    for kw, priority, status in table_rows:
        marker = "+" if status == "covered" else ("~" if status == "synonym-only" else "-")
        print(f"  {marker} {kw:<23} {priority:<12} {status}")
    print(f"\n  Coverage: {coverage_pct}% ({covered_count} covered, {synonym_count} synonym-only, {missing_count} missing)")

    passed = coverage_pct >= 60.0
    summary = f"{'PASS' if passed else 'FAIL'}: Keyword coverage {coverage_pct}% ({covered_count}/{total} direct, {synonym_count} synonyms, {missing_count} missing)"
    return passed, summary


def main():
    parser = argparse.ArgumentParser(description="Validate ATS-friendliness of a resume PDF")
    parser.add_argument("--pdf", required=True, help="Path to PDF resume to validate")
    parser.add_argument("--job-id", help="Job ID to check keyword coverage against")
    parser.add_argument("--jobs", help="Path to scored jobs JSON (for keyword coverage)")
    args = parser.parse_args()

    print(f"ATS Validation: {args.pdf}")
    print("=" * 60)

    # Extract text
    try:
        text = extract_text(args.pdf)
    except Exception as e:
        print(f"FAIL: Could not read PDF — {e}")
        sys.exit(1)

    # Run all checks
    checks = [
        ("Text Extraction", check_text_extractable(text)),
        ("Contact Info", check_contact_info(text)),
        ("Section Order", check_section_order(text)),
        ("Column Interleaving", check_no_interleaving(text)),
        ("Standard Headings", check_standard_headings(text)),
    ]

    # Keyword coverage check (if job data provided)
    job_data = None
    if args.job_id and args.jobs:
        jobs_path = Path(args.jobs)
        if jobs_path.exists():
            with open(jobs_path) as f:
                jobs_file = json.load(f)
            for job in jobs_file.get("jobs", []):
                if str(job.get("id")) == str(args.job_id):
                    job_data = job
                    break
            if job_data:
                checks.append(("Keyword Coverage", check_keyword_coverage(text, job_data)))
            else:
                print(f"  Warning: Job ID {args.job_id} not found in {args.jobs} — skipping keyword check")
        else:
            print(f"  Warning: Jobs file not found: {args.jobs} — skipping keyword check")

    all_passed = True
    for name, (passed, message) in checks:
        status = "+" if passed else "x"
        print(f"  {status} {name}: {message}")
        if not passed:
            all_passed = False

    print()
    # Plain text dump for manual "Notepad test"
    print("-" * 60)
    print("NOTEPAD TEST (first 500 chars of extracted text):")
    print("-" * 60)
    print(text[:500])
    print("-" * 60)

    if all_passed:
        print("\nRESULT: All ATS checks passed")
        sys.exit(0)
    else:
        print("\nRESULT: Some checks failed — review issues above")
        sys.exit(1)


if __name__ == "__main__":
    main()
