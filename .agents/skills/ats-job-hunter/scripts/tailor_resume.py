#!/usr/bin/env python3
"""Tailor a master resume to a specific JD using Google Gemini, then generate ATS-friendly LaTeX PDF."""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from google import genai

load_dotenv()

GEMINI_MODEL = "gemini-2.0-flash"


def calculate_experience(career_start_date: str) -> str:
    """Calculate years of experience from career start date to today.

    Returns a string like '5.2' rounded down to 1 decimal place.
    """
    start = date.fromisoformat(career_start_date)
    today = date.today()
    delta_days = (today - start).days
    years = delta_days / 365.25
    # Round down to 1 decimal (e.g., 5.27 -> 5.2)
    years = math.floor(years * 10) / 10
    return str(years)


def resolve_experience_placeholder(master_resume: dict) -> dict:
    """Replace {{YEARS_OF_EXPERIENCE}} in summary with calculated value."""
    career_start = master_resume.get("basics", {}).get("careerStartDate", "")
    if not career_start:
        return master_resume

    years = calculate_experience(career_start)
    summary = master_resume.get("basics", {}).get("summary", "")
    if "{{YEARS_OF_EXPERIENCE}}" in summary:
        master_resume["basics"]["summary"] = summary.replace(
            "{{YEARS_OF_EXPERIENCE}}", years
        )
    return master_resume

TAILOR_PROMPT = """You are an expert ATS-optimized resume writer. Produce a tailored resume from the master resume JSON for the given JD. Maximize recruiter engagement: quantified achievements, strong action verbs, metrics in every bullet, strongest accomplishments first.

## DOMAIN-FOCUS STRATEGY
When the candidate's master resume spans multiple domains (e.g., web + mobile, backend + data, etc.), tailor by domain relevance:

**If the JD targets one specific domain (e.g., Frontend, Backend, Mobile, Data, etc.):**
- FOCUS 90% of the resume on experience matching that domain
- SELECT only projects from the matching domain
- REWRITE work bullets to emphasize achievements in that domain
- In skills section: domain-relevant skills should dominate. You MAY mention secondary domains as a brief line ("Also experienced in: ...") but do NOT give them prominence
- Do NOT lead with projects or bullets from other domains

**If the JD is mixed or doesn't clearly specify a single domain:**
- Balance domains proportionally based on JD emphasis
- Lead with whichever domain the JD mentions first or emphasizes more

The goal: make the resume look like this candidate has spent their ENTIRE career in the domain the JD asks for. Secondary domains are a bonus, not the headline.

## STRICT RULES
1. ONLY use information from the master resume — NEVER fabricate skills, companies, titles, or achievements. ZERO HALLUCINATION. Every skill, project, company, metric MUST exist in the master resume.
2. Every bullet MUST survive interview scrutiny — candidate will be asked to defend it for 45 minutes
3. Select the most relevant experience, skills, and projects for this specific JD based on the domain-focus strategy above
4. Use the EXACT skill names from the JD (e.g., if JD says "React", use "React" not "ReactJS")
5. Mirror job description vocabulary where the candidate honestly has the experience
6. Order skills by relevance to the JD (most relevant first)
7. Order work highlights by relevance to the JD (most relevant first)
8. Select only the most relevant projects (3-4 max) from the correct domain
9. Use consistent date format: "Mon YYYY" (e.g., "Nov 2023")
10. NEVER invent new skills, technologies, or metrics not present in the master resume

## BULLET POINT FORMULA (Google's X-Y-Z format)
"Accomplished [X] as measured by [Y], by doing [Z]"

Rules for every bullet:
- Start with action verb (Led, Architected, Built, Designed, Implemented, Migrated, Optimized, Scaled)
- NEVER use: "Responsible for", "Involved in", "Helped with", "Worked on"
- Include at least one metric/number per bullet from master resume
- First bullet per role MUST convey scope (team size, system scale, ownership)

## SUMMARY: 3-4 sentences. Level + years + stack + domain + best quantified win. Align to target role.
## SKILLS: Group by category, JD-relevant first. No proficiency bars.

## PAGE SIZE (ATS-friendly — strictly enforced)
- Target: FILL EXACTLY 2 full pages for 5+ years experience (1 page for < 5 years)
- NEVER exceed 2 pages — recruiters and ATS penalize 3+ page resumes
- NEVER leave page 2 half-empty — a half-empty page 2 looks worse than a dense 1-pager
- To FILL 2 pages properly (this is critical — generate enough content):
  - Summary: 3-4 sentences (4-5 lines)
  - Skills: 6-8 category lines
  - Work Experience: 7-8 bullets for current/main role, 5-6 bullets for previous role
  - Projects: 3 projects, 3-4 bullets each (detailed, metric-rich bullets)
  - Education: 1-2 lines
- Each bullet should be 1.5-2 lines long when rendered (not too short, not too long)
- If content is too short to fill 2 pages, ADD more relevant bullets from the master resume
- If content exceeds 2 pages, CUT the least relevant bullets — never shrink font or margins
- Every line must earn its place by showing impact, scale, or ownership

## PROJECT SELECTION (always pick high-impact)
Select ONLY 3 projects, ranked by these criteria (highest priority first):
1. **Scale & metrics**: Projects with quantifiable impact (1,805 tests, 22M+ users, 10,000+ records, 100% coverage)
2. **Tech stack match**: Projects using the JD's exact technologies
3. **Complexity**: Platform rewrites, distributed systems, AI/LLM, offline-first > simple CRUD apps
4. **Production impact**: Production apps serving real users > side projects > academic projects

NEVER include academic/hobby projects (Birthday Game, Password Manager, Pets Gallery, etc.) unless the JD specifically asks for those skills and no production project covers them.

High-impact project pool by domain:
- **Web/Full Stack**: VAPR (40+ APIs, 1,090 tests, sole dev), Service Dispatcher (1,805 tests, 7 envs, platform rewrite), TrekBooking (Next.js 15, 182 tests), AI Chat Assistant (Gemini, RAG, 5 tool modules)
- **Mobile**: RealCADENCE (offline-first iOS, 229+ Swift files), Kerb Driver (100% coverage, App Store), Micro Center (22M+ customers, 30 stores), Stripe Tap-to-Pay (NFC, pioneered before official plugin)
- **AI/LLM**: AI Chat Assistant (Gemini, RAG, ChromaDB, tool calling), BiteBloom (Gemini Vision), WP Agent Node (Gemini chatbot)

## OUTPUT FORMAT
Return ONLY valid JSON (no markdown, no code fences) with this exact structure:
{
  "summary": "Tailored professional summary (2-3 sentences)",
  "skills": [
    {"category": "Category Name", "items": ["Skill1", "Skill2", "Skill3"]}
  ],
  "work": [
    {
      "company": "Company Name",
      "position": "Job Title",
      "location": "City, Country",
      "start_date": "Mon YYYY",
      "end_date": "Present" or "Mon YYYY",
      "highlights": ["Bullet point 1", "Bullet point 2"]
    }
  ],
  "projects": [
    {
      "name": "Project Name",
      "technologies": "Tech1, Tech2, Tech3",
      "highlights": ["Bullet point 1", "Bullet point 2"]
    }
  ],
  "education": [
    {
      "institution": "University Name",
      "degree": "Degree Type in Field",
      "location": "City, State",
      "start_date": "YYYY",
      "end_date": "YYYY"
    }
  ]
}

## MASTER RESUME
{master_resume}

## JOB DESCRIPTION
Title: {job_title}
Company: {company}
Location: {location}
Description:
{job_description}
"""


def compress_resume_for_gemini(master_resume: dict) -> dict:
    """Strip non-essential fields to reduce token usage.

    Removes: _metadata, metrics objects, urls, academic/hobby project details.
    Keeps: basics, skills, work highlights, project names + highlights + keywords.
    Savings: ~6,000 tokens → ~3,500 tokens (~40% reduction).
    """
    compressed = {
        "basics": {
            "name": master_resume.get("basics", {}).get("name", ""),
            "label": master_resume.get("basics", {}).get("label", ""),
            "careerStartDate": master_resume.get("basics", {}).get("careerStartDate", ""),
            "summary": master_resume.get("basics", {}).get("summary", ""),
        },
        "skills": [
            {"name": s.get("name", ""), "keywords": s.get("keywords", [])}
            for s in master_resume.get("skills", [])
        ],
        "work": [
            {
                "company": w.get("company", ""),
                "position": w.get("position", ""),
                "location": w.get("location", ""),
                "startDate": w.get("startDate", ""),
                "endDate": w.get("endDate", ""),
                "highlights": w.get("highlights", []),
            }
            for w in master_resume.get("work", [])
        ],
        "projects": [
            {
                "name": p.get("name", ""),
                "keywords": p.get("keywords", []),
                "highlights": p.get("highlights", []),
            }
            for p in master_resume.get("projects", [])
        ],
        "education": [
            {
                "institution": e.get("institution", ""),
                "area": e.get("area", ""),
                "studyType": e.get("studyType", ""),
                "startDate": e.get("startDate", ""),
                "endDate": e.get("endDate", ""),
            }
            for e in master_resume.get("education", [])
        ],
    }
    return compressed


def call_gemini(master_resume: dict, job: dict) -> dict:
    """Send compressed resume + JD to Gemini and get tailored content back."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY not found in environment. "
            "Set it in .env file: GEMINI_API_KEY=your_key_here"
        )

    client = genai.Client(api_key=api_key)

    # Compress resume to save ~40% tokens
    compressed = compress_resume_for_gemini(master_resume)

    prompt = TAILOR_PROMPT.format(
        master_resume=json.dumps(compressed),
        job_title=job.get("title", ""),
        company=job.get("company_token", ""),
        location=job.get("location", ""),
        job_description=job.get("description", ""),
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )

    # Parse JSON from response
    text = response.text.strip()
    # Remove markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)

    return json.loads(text)


def escape_latex(text: str) -> str:
    """Escape special LaTeX characters in text."""
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


def format_skills_latex(skills: list[dict]) -> str:
    """Format skills as simple lines — no tables (ATS reads tables cell-by-cell)."""
    lines = []
    for group in skills:
        category = escape_latex(group.get("category", ""))
        items = ", ".join(escape_latex(s) for s in group.get("items", []))
        lines.append(f"\\textbf{{{category}:}} {items}")
    return " \\\\[0.15em]\n".join(lines)


def format_work_latex(work: list[dict]) -> str:
    """Format work experience entries."""
    entries = []
    for job in work:
        company = escape_latex(job.get("company", ""))
        position = escape_latex(job.get("position", ""))
        location = escape_latex(job.get("location", ""))
        start = escape_latex(job.get("start_date", ""))
        end = escape_latex(job.get("end_date", "Present"))
        date_range = f"{start} -- {end}"

        highlights = job.get("highlights", [])
        bullets = "\n".join(
            f"  \\item {escape_latex(h)}" for h in highlights
        )

        entry = f"""\\jobtitle{{{position}}}{{{date_range}}}{{{company}}}{{{location}}}
\\begin{{itemize}}
{bullets}
\\end{{itemize}}
\\vspace{{0.3em}}"""
        entries.append(entry)

    return "\n\n".join(entries)


def format_projects_latex(projects: list[dict]) -> str:
    """Format project entries. Returns empty string if no projects."""
    if not projects:
        return ""

    entries = []
    for proj in projects:
        name = escape_latex(proj.get("name", ""))
        tech = escape_latex(proj.get("technologies", ""))
        highlights = proj.get("highlights", [])
        bullets = "\n".join(
            f"  \\item {escape_latex(h)}" for h in highlights
        )

        entry = f"""\\projecttitle{{{name}}}{{{tech}}}
\\begin{{itemize}}
{bullets}
\\end{{itemize}}
\\vspace{{0.3em}}"""
        entries.append(entry)

    section = f"\\section{{Projects}}\n" + "\n\n".join(entries)
    return section


def format_education_latex(education: list[dict]) -> str:
    """Format education entries."""
    entries = []
    for edu in education:
        institution = escape_latex(edu.get("institution", ""))
        degree = escape_latex(edu.get("degree", ""))
        location = escape_latex(edu.get("location", ""))
        start = escape_latex(str(edu.get("start_date", "")))
        end = escape_latex(str(edu.get("end_date", "")))
        date_range = f"{start} -- {end}" if start and end else end or start

        entry = f"\\edutitle{{{degree}}}{{{date_range}}}{{{institution}}}{{{location}}}"
        entries.append(entry)

    return "\n\n".join(entries)


def fill_template(template_path: str, master_resume: dict, tailored: dict) -> str:
    """Inject tailored content into LaTeX template."""
    with open(template_path) as f:
        template = f.read()

    basics = master_resume.get("basics", {})
    location = basics.get("location", {})
    profiles = {p.get("network", "").lower(): p.get("url", "") for p in basics.get("profiles", [])}

    loc_parts = [location.get("city", ""), location.get("region", ""), location.get("countryCode", "")]
    location_str = ", ".join(p for p in loc_parts if p)

    # Build compact link display text (short labels, clickable hrefs)
    linkedin_url = profiles.get("linkedin", "")
    github_url = profiles.get("github", "")
    portfolio_url = profiles.get("portfolio", "")

    def make_link(url: str, display_url: str) -> str:
        """Create clickable link with visible URL text (ATS reads the display text)."""
        if not url:
            return ""
        escaped_url = url.replace("%", "\\%").replace("#", "\\#").replace("&", "\\&")
        return f"\\href{{{escaped_url}}}{{{escape_latex(display_url)}}}"

    def shorten_url(url: str) -> str:
        """Strip https:// and www. for compact display while keeping ATS-readable."""
        return url.replace("https://", "").replace("http://", "").replace("www.", "").rstrip("/")

    linkedin_display = make_link(linkedin_url, shorten_url(linkedin_url)) if linkedin_url else ""
    github_display = make_link(github_url, shorten_url(github_url)) if github_url else ""
    portfolio_display = make_link(portfolio_url, shorten_url(portfolio_url)) if portfolio_url else ""

    replacements = {
        "<<NAME>>": escape_latex(basics.get("name", "")),
        "<<EMAIL>>": escape_latex(basics.get("email", "")),
        "<<PHONE>>": escape_latex(basics.get("phone", "")),
        "<<LOCATION>>": escape_latex(location_str),
        "<<LINKEDIN_DISPLAY>>": linkedin_display,
        "<<GITHUB_DISPLAY>>": github_display,
        "<<PORTFOLIO_DISPLAY>>": portfolio_display,
        "<<SUMMARY>>": escape_latex(tailored.get("summary", "")),
        "<<SKILLS>>": format_skills_latex(tailored.get("skills", [])),
        "<<WORK_EXPERIENCE>>": format_work_latex(tailored.get("work", [])),
        "<<PROJECTS_SECTION>>": format_projects_latex(tailored.get("projects", [])),
        "<<EDUCATION>>": format_education_latex(tailored.get("education", [])),
    }

    result = template
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value)

    return result


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


def show_tailoring_summary(master: dict, tailored: dict, job: dict):
    """Display what was changed during tailoring."""
    print("\n" + "=" * 60)
    print(f"TAILORING SUMMARY")
    print(f"Target: {job.get('title', 'N/A')} at {job.get('company_token', 'N/A')}")
    print("=" * 60)

    print(f"\nSummary: {tailored.get('summary', '')[:100]}...")

    print(f"\nSkills ({len(tailored.get('skills', []))} categories):")
    for group in tailored.get("skills", []):
        print(f"  {group.get('category', '')}: {', '.join(group.get('items', [])[:5])}...")

    print(f"\nWork Experience ({len(tailored.get('work', []))} entries):")
    for work in tailored.get("work", []):
        print(f"  {work.get('position', '')} at {work.get('company', '')}")
        print(f"    Top bullet: {work.get('highlights', [''])[0][:80]}...")

    print(f"\nProjects ({len(tailored.get('projects', []))} selected):")
    for proj in tailored.get("projects", []):
        print(f"  {proj.get('name', '')}")

    print(f"\nEducation ({len(tailored.get('education', []))} entries):")
    for edu in tailored.get("education", []):
        print(f"  {edu.get('degree', '')} — {edu.get('institution', '')}")
    print()


def save_job_posting(job: dict, output_dir: Path) -> None:
    """Archive the original job posting as markdown for interview prep.

    Saves the full JD to job_posting.md in the per-job output directory so the
    candidate can review exactly what description was used for tailoring.
    """
    title = job.get("title", "Unknown Role")
    company = job.get("company_token", "Unknown Company")
    location = job.get("location", "")
    url = job.get("url", "")
    description = job.get("description", "")

    lines = [
        f"# {title}",
        f"**Company:** {company}",
    ]
    if location:
        lines.append(f"**Location:** {location}")
    if url:
        lines.append(f"**URL:** {url}")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Job Description")
    lines.append("")
    lines.append(description)

    posting_path = output_dir / "job_posting.md"
    with open(posting_path, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Job posting archived: {posting_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Tailor resume using Gemini AI and generate ATS-friendly LaTeX PDF"
    )
    parser.add_argument("--master-resume", required=True, help="Path to master resume JSON")
    parser.add_argument("--job-id", required=True, help="Job ID to tailor for")
    parser.add_argument("--jobs", required=True, help="Path to scored jobs JSON")
    parser.add_argument("--template", required=True, help="Path to LaTeX template")
    parser.add_argument("--output-dir", default="output/tailored", help="Output directory")
    args = parser.parse_args()

    # Load data
    with open(args.master_resume) as f:
        master_resume = json.load(f)
    with open(args.jobs) as f:
        jobs_data = json.load(f)

    # Resolve dynamic experience calculation
    master_resume = resolve_experience_placeholder(master_resume)
    career_start = master_resume.get("basics", {}).get("careerStartDate", "")
    if career_start:
        years = calculate_experience(career_start)
        print(f"Experience: {years}+ years (career start: {career_start})")

    # Find target job
    target_job = None
    for job in jobs_data.get("jobs", []):
        if str(job.get("id")) == str(args.job_id):
            target_job = job
            break

    if not target_job:
        print(f"Error: Job ID {args.job_id} not found in {args.jobs}")
        return

    print(f"Tailoring resume for: {target_job['title']} at {target_job['company_token']}")
    print(f"Match score: {target_job.get('match_score', 'N/A')}")

    # Duplicate guard — check if already tailored for this job
    company = target_job.get("company_token", "unknown")
    job_title = target_job.get("title", "unknown")
    today = date.today().isoformat()
    title_slug = re.sub(r'[^a-z0-9]+', '-', job_title.lower()).strip('-')[:50]
    dir_name = f"{company}_{title_slug}_{today}"
    output_dir = Path(args.output_dir) / dir_name

    existing_pdf = output_dir / "resume.pdf"
    if existing_pdf.exists():
        print(f"\nALREADY TAILORED: {existing_pdf}")
        print(f"Resume for this job was already generated today.")
        print(f"To regenerate, delete: {output_dir}/")
        return

    # Step 1: Call Gemini to tailor content
    print("\nCalling Gemini AI to tailor resume content...")
    tailored = call_gemini(master_resume, target_job)

    # Show summary
    show_tailoring_summary(master_resume, tailored, target_job)

    # Step 2: Fill LaTeX template
    print("Generating LaTeX from template...")
    latex_content = fill_template(args.template, master_resume, tailored)

    # Step 3: Save files (output_dir already set by duplicate guard above)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save tailored JSON
    json_path = output_dir / "tailored.json"
    with open(json_path, "w") as f:
        json.dump(tailored, f, indent=2)
    print(f"Tailored JSON saved: {json_path}")

    # Save LaTeX source
    tex_path = output_dir / "resume.tex"
    with open(tex_path, "w") as f:
        f.write(latex_content)
    print(f"LaTeX source saved: {tex_path}")

    # Step 4: Compile to PDF
    print("Compiling LaTeX to PDF...")
    pdf_path = compile_latex(str(tex_path), str(output_dir))
    print(f"ATS-friendly PDF generated: {pdf_path}")

    # Clean up LaTeX aux files
    for ext in [".aux", ".log", ".out"]:
        aux_file = output_dir / f"resume{ext}"
        if aux_file.exists():
            aux_file.unlink()

    # Save job metadata for reference
    meta_path = output_dir / "job_meta.json"
    with open(meta_path, "w") as f:
        json.dump({
            "job_id": target_job.get("id", ""),
            "title": job_title,
            "company": company,
            "platform": target_job.get("platform", ""),
            "location": target_job.get("location", ""),
            "url": target_job.get("url", ""),
            "match_score": target_job.get("match_score", ""),
            "tailored_at": today,
        }, f, indent=2)

    # Archive the original job posting for interview prep
    save_job_posting(target_job, output_dir)

    print(f"\nDone! Resume tailored for {job_title} at {company}")
    print(f"Output: {output_dir}/")
    print(f"PDF: {pdf_path}")


if __name__ == "__main__":
    main()
