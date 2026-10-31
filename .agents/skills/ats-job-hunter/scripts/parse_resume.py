#!/usr/bin/env python3
"""Parse a PDF resume into JSON Resume format."""

import argparse
import json
import re
from pathlib import Path

import pdfplumber


def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract all text from a PDF file."""
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
    return "\n".join(text_parts)


def extract_email(text: str) -> str:
    """Extract email address from text."""
    match = re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text)
    return match.group(0) if match else ""


def extract_phone(text: str) -> str:
    """Extract phone number from text."""
    match = re.search(r'[\+]?[\d\s\-\(\)]{10,}', text)
    return match.group(0).strip() if match else ""


def parse_to_json_resume(text: str) -> dict:
    """Convert extracted text to JSON Resume schema.

    Note: This is a basic heuristic parser. For production use,
    integrate an LLM API call for more accurate parsing.
    """
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    resume = {
        "$schema": "https://raw.githubusercontent.com/jsonresume/resume-schema/v1.0.0/schema.json",
        "basics": {
            "name": lines[0] if lines else "",
            "email": extract_email(text),
            "phone": extract_phone(text),
            "summary": "",
        },
        "skills": [],
        "work": [],
        "education": [],
    }

    # Simple section detection
    current_section = None
    section_content = []

    section_headers = {
        "summary": ["summary", "about", "objective", "profile"],
        "skills": ["skills", "technical skills", "technologies", "competencies"],
        "work": ["experience", "work experience", "employment", "professional experience"],
        "education": ["education", "academic", "qualifications"],
    }

    for line in lines[1:]:  # skip name
        line_lower = line.lower().strip(":")
        matched_section = None
        for section, keywords in section_headers.items():
            if any(line_lower == kw or line_lower.startswith(kw) for kw in keywords):
                matched_section = section
                break

        if matched_section:
            # Save previous section content
            if current_section and section_content:
                _assign_section(resume, current_section, section_content)
            current_section = matched_section
            section_content = []
        elif current_section:
            section_content.append(line)

    # Save last section
    if current_section and section_content:
        _assign_section(resume, current_section, section_content)

    return resume


def _assign_section(resume: dict, section: str, content: list[str]):
    """Assign parsed content to the appropriate resume section."""
    if section == "summary":
        resume["basics"]["summary"] = " ".join(content)
    elif section == "skills":
        # Treat each line or comma-separated group as a skill group
        for line in content:
            skills = [s.strip() for s in re.split(r'[,|•·]', line) if s.strip()]
            if skills:
                resume["skills"].append({
                    "name": skills[0],
                    "keywords": skills,
                })
    elif section == "work":
        # Simple: each block separated by dates could be a work entry
        resume["work"].append({
            "company": "",
            "position": "",
            "summary": " ".join(content[:3]),
            "highlights": content,
        })
    elif section == "education":
        resume["education"].append({
            "institution": content[0] if content else "",
            "area": content[1] if len(content) > 1 else "",
            "studyType": "",
        })


def main():
    parser = argparse.ArgumentParser(description="Parse PDF resume to JSON Resume format")
    parser.add_argument("--input", required=True, help="Path to PDF resume")
    parser.add_argument("--output", default=None, help="Output JSON path (default: same dir as input)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: File not found: {args.input}")
        return

    print(f"Parsing resume from {args.input}...")
    text = extract_text_from_pdf(args.input)
    print(f"Extracted {len(text)} characters")

    resume = parse_to_json_resume(text)

    output_path = Path(args.output) if args.output else input_path.with_suffix(".json")
    with open(output_path, "w") as f:
        json.dump(resume, f, indent=2)

    print(f"JSON Resume saved to {output_path}")
    print(f"Skills found: {len(resume['skills'])} groups")
    print(f"Work entries: {len(resume['work'])}")
    print(f"Education entries: {len(resume['education'])}")
    print("\nPlease review and manually refine the generated JSON for accuracy.")


if __name__ == "__main__":
    main()
