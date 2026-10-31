#!/usr/bin/env python3
"""Generate a tailored cover letter using Google Gemini AI."""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from google import genai

load_dotenv()

GEMINI_MODEL = "gemini-2.0-flash"

COVER_LETTER_PROMPT = """You are an expert career coach who has helped 500+ engineers land roles at top tech companies.

Write a tailored cover letter for the following job, using ONLY facts from the candidate's resume.

## STRICT RULES
1. ONLY reference experience, skills, projects, and metrics from the resume — NEVER fabricate
2. Keep it to 3-4 paragraphs, under 350 words total
3. Tone: confident, professional, specific — NOT generic or sycophantic
4. NEVER start with "I am writing to express my interest" or "I am excited to apply" — these are cliché
5. NEVER use buzzwords: "passionate", "synergy", "go-getter", "dynamic", "leverage"
6. Lead with your strongest relevant achievement + metric that maps to the role
7. Connect 2-3 specific experiences to the job's key requirements
8. Show you understand what the company/team does — reference something specific from the JD
9. End with a confident close — not desperate ("I look forward to discussing how my experience...")
10. Use the candidate's actual numbers and metrics from their resume

## STRUCTURE
**Paragraph 1 (Hook):** Lead with a specific achievement that directly maps to the role's #1 requirement. Mention the role name and company.

**Paragraph 2 (Evidence):** Connect 2-3 specific experiences/projects to the JD's key requirements. Use metrics.

**Paragraph 3 (Why This Company):** Show understanding of the company/team's work. Explain why this specific role (not just any job).

**Paragraph 4 (Close):** Confident 1-2 sentence close. Reference availability for next steps.

## OUTPUT FORMAT
Return ONLY valid JSON (no markdown, no code fences) with this structure:
{{
  "subject_line": "Application for [Role] — [Your Name]",
  "greeting": "Dear [Hiring Team / specific name if known],",
  "body": "The full cover letter text with paragraph breaks as \\n\\n",
  "closing": "Best regards,",
  "name": "Candidate Name"
}}

## CANDIDATE RESUME
{resume}

## TARGET JOB
Title: {job_title}
Company: {company}
Location: {location}
Description:
{job_description}

## CANDIDATE'S YEARS OF EXPERIENCE
{years_of_experience}+ years
"""


def calculate_experience(career_start_date: str) -> str:
    """Calculate years of experience from career start date."""
    start = date.fromisoformat(career_start_date)
    today = date.today()
    delta_days = (today - start).days
    years = math.floor((delta_days / 365.25) * 10) / 10
    return str(years)


def call_gemini(resume: dict, job: dict, years: str) -> dict:
    """Generate cover letter via Gemini."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not found in .env")

    client = genai.Client(api_key=api_key)

    prompt = COVER_LETTER_PROMPT.format(
        resume=json.dumps(resume, indent=2),
        job_title=job.get("title", ""),
        company=job.get("company_token", ""),
        location=job.get("location", ""),
        job_description=job.get("description", ""),
        years_of_experience=years,
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    text = response.text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    return json.loads(text)


def escape_latex(text: str) -> str:
    """Escape special LaTeX characters."""
    if not text:
        return ""
    replacements = {
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    for char, escaped in replacements.items():
        text = text.replace(char, escaped)
    return text


def generate_latex(cover_letter: dict, resume_basics: dict) -> str:
    """Generate a clean LaTeX cover letter."""
    name = escape_latex(resume_basics.get("name", ""))
    email = escape_latex(resume_basics.get("email", ""))
    phone = escape_latex(resume_basics.get("phone", ""))
    location = resume_basics.get("location", {})
    city = escape_latex(f"{location.get('city', '')}, {location.get('region', '')}")

    profiles = {
        p.get("network", "").lower(): p.get("url", "")
        for p in resume_basics.get("profiles", [])
    }
    linkedin = escape_latex(profiles.get("linkedin", ""))
    portfolio = escape_latex(profiles.get("portfolio", ""))

    greeting = escape_latex(cover_letter.get("greeting", "Dear Hiring Team,"))
    body = cover_letter.get("body", "")
    # Split paragraphs and escape each
    paragraphs = body.split("\n\n")
    body_latex = "\n\n".join(escape_latex(p) for p in paragraphs)
    closing = escape_latex(cover_letter.get("closing", "Best regards,"))

    return rf"""\documentclass[11pt,a4paper]{{article}}

\usepackage[top=1in, bottom=1in, left=1in, right=1in]{{geometry}}
\usepackage[utf8]{{inputenc}}
\usepackage[T1]{{fontenc}}
\usepackage{{parskip}}
\usepackage{{hyperref}}

\hypersetup{{
    colorlinks=true,
    linkcolor=black,
    urlcolor=black,
    pdfborder={{0 0 0}}
}}

\pagestyle{{empty}}

\begin{{document}}

\begin{{flushleft}}
{{\large\bfseries {name}}}\\[0.3em]
{email} \quad | \quad {phone} \quad | \quad {city}\\
{linkedin} \quad | \quad {portfolio}
\end{{flushleft}}

\vspace{{0.5em}}
\today

\vspace{{1em}}

{greeting}

\vspace{{0.5em}}

{body_latex}

\vspace{{1em}}

{closing}\\[0.5em]
{name}

\end{{document}}
"""


def compile_latex(tex_path: str, output_dir: str) -> str:
    """Compile LaTeX to PDF using tectonic or pdflatex."""
    tex_file = Path(tex_path)
    out_dir = Path(output_dir)
    pdf_in_output = out_dir / tex_file.with_suffix(".pdf").name

    # Try tectonic first
    tectonic_bin = shutil.which("tectonic") or "/opt/homebrew/bin/tectonic"
    if Path(tectonic_bin).exists():
        res = subprocess.run(
            [tectonic_bin, "-o", str(out_dir), str(tex_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if pdf_in_output.exists():
            return str(pdf_in_output)
        print(f"Tectonic error:\n{res.stderr}")

    # Fallback to pdflatex
    pdflatex_bin = shutil.which("pdflatex") or "/Library/TeX/texbin/pdflatex"
    if Path(pdflatex_bin).exists():
        for _ in range(2):
            subprocess.run(
                [
                    pdflatex_bin,
                    "-interaction=nonstopmode",
                    "-output-directory", str(out_dir),
                    str(tex_path),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        if pdf_in_output.exists():
            return str(pdf_in_output)

    raise RuntimeError(
        "Neither tectonic nor pdflatex succeeded in generating PDF."
    )


def main():
    parser = argparse.ArgumentParser(description="Generate a tailored cover letter")
    parser.add_argument("--master-resume", required=True, help="Path to master resume JSON")
    parser.add_argument("--job-id", required=True, help="Job ID to write cover letter for")
    parser.add_argument("--jobs", required=True, help="Path to scored jobs JSON")
    parser.add_argument("--output-dir", default="output/tailored", help="Output directory")
    parser.add_argument("--format", choices=["pdf", "txt", "both"], default="both",
                        help="Output format")
    args = parser.parse_args()

    with open(args.master_resume) as f:
        resume = json.load(f)
    with open(args.jobs) as f:
        jobs_data = json.load(f)

    # Find target job
    target_job = None
    for job in jobs_data.get("jobs", []):
        if str(job.get("id")) == str(args.job_id):
            target_job = job
            break

    if not target_job:
        print(f"Error: Job ID {args.job_id} not found")
        return

    # Calculate experience
    career_start = resume.get("basics", {}).get("careerStartDate", "")
    years = calculate_experience(career_start) if career_start else "5"

    print(f"Generating cover letter for: {target_job['title']} at {target_job['company_token']}")

    # Generate via Gemini
    cover_letter = call_gemini(resume, target_job, years)

    # Output — use human-readable directory per job
    company = target_job.get("company_token", "unknown")
    job_title = target_job.get("title", "unknown")
    today = date.today().isoformat()
    title_slug = re.sub(r'[^a-z0-9]+', '-', job_title.lower()).strip('-')[:50]
    dir_name = f"{company}_{title_slug}_{today}"

    output_dir = Path(args.output_dir) / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save JSON
    json_path = output_dir / "cover_letter.json"
    with open(json_path, "w") as f:
        json.dump(cover_letter, f, indent=2)

    # Save plain text
    if args.format in ("txt", "both"):
        txt_path = output_dir / "cover_letter.txt"
        with open(txt_path, "w") as f:
            f.write(f"Subject: {cover_letter.get('subject_line', '')}\n\n")
            f.write(f"{cover_letter.get('greeting', '')}\n\n")
            f.write(f"{cover_letter.get('body', '')}\n\n")
            f.write(f"{cover_letter.get('closing', '')}\n")
            f.write(f"{cover_letter.get('name', '')}\n")
        print(f"Text cover letter: {txt_path}")

    # Save PDF
    if args.format in ("pdf", "both"):
        latex_content = generate_latex(cover_letter, resume.get("basics", {}))
        tex_path = output_dir / "cover_letter.tex"
        with open(tex_path, "w") as f:
            f.write(latex_content)

        pdf_path = compile_latex(str(tex_path), str(output_dir))
        print(f"PDF cover letter: {pdf_path}")

        # Clean aux files
        for ext in [".aux", ".log", ".out"]:
            aux = output_dir / f"cover_letter{ext}"
            if aux.exists():
                aux.unlink()

    # Display preview
    print(f"\n{'=' * 60}")
    print(f"Subject: {cover_letter.get('subject_line', '')}")
    print(f"{'=' * 60}")
    print(f"\n{cover_letter.get('greeting', '')}\n")
    print(cover_letter.get("body", ""))
    print(f"\n{cover_letter.get('closing', '')}")
    print(cover_letter.get("name", ""))
    print(f"{'=' * 60}")
    print(f"\nWord count: {len(cover_letter.get('body', '').split())}")


if __name__ == "__main__":
    main()
