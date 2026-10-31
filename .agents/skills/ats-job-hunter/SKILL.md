---
name: ats-job-hunter
description: >
  Automate job hunting across ATS platforms (Greenhouse, Lever, Ashby).
  Fetches open jobs, scores them against your resume, tailors your resume
  using Gemini AI, generates ATS-friendly LaTeX PDF, and applies automatically.
  Trigger on: "find jobs", "hunt jobs", "apply to jobs", "match resume",
  "job search", "ATS apply", "tailor resume", "write application email",
  "generate email"
version: "3.1.0"
author: "pr"
requires:
  tools:
    - python3
    - pip
    - pdflatex
---

# ATS Job Hunter

## Purpose
End-to-end job application automation:
1. Fetch open positions from ATS platforms (Greenhouse, Lever, Ashby) via their public APIs
2. Score each job against the user's master resume using semantic matching
3. Rank jobs by match score
4. Tailor the resume to the top-matching JD using Google Gemini AI
5. Generate ATS-friendly PDF via LaTeX
6. Submit the application via the ATS API

## Pre-Conditions
- Master resume exists at `{{WORKSPACE_PATH}}/resume/master-resume.json` (JSON Resume format)
- LaTeX template exists at `{{WORKSPACE_PATH}}/resume/templates/ats-friendly.tex`
- Job preferences configured at `{{WORKSPACE_PATH}}/config/preferences.yaml`
- Python dependencies installed: `pip install -r {{SKILL_PATH}}/scripts/requirements.txt`
- LaTeX installed: `brew install --cask mactex` or `brew install basictex`
- AI capabilities are provided by Antigravity (no separate API key needed)

## Instructions

### Step 1: Load Master Resume
1. Check for master resume at `{{WORKSPACE_PATH}}/resume/master-resume.json`
2. If only PDF exists, extract text using: `python {{SKILL_PATH}}/scripts/parse_resume.py --input {{WORKSPACE_PATH}}/resume/resume.pdf`
3. The master resume is the **complete superset** of all experience, skills, and projects — never modify it directly

### Step 2: Fetch Jobs from ATS Platforms (parallel + cached)
1. Run: `python {{SKILL_PATH}}/scripts/fetch_jobs.py --platforms greenhouse,lever,ashby`
2. The script queries these public API endpoints **in parallel** (5 threads):
   - **Greenhouse**: `https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs`
   - **Lever**: `https://api.lever.co/v0/postings/{company}?mode=json`
   - **Ashby**: `https://api.ashbyhq.com/posting-api/job-board/{board_slug}`
3. **Caching**: If jobs.json is less than 6 hours old, skip fetching entirely (use `--cache-hours 0` to force refresh)
4. User can specify target companies in `{{WORKSPACE_PATH}}/config/companies.yaml`
5. Automatic deduplication removes same job posted on multiple boards
6. Results saved to `{{WORKSPACE_PATH}}/output/jobs.json`

### Step 3: Filter + Score Jobs Against Resume
1. Run:
   ```
   python {{SKILL_PATH}}/scripts/score_jobs.py \
     --resume {{WORKSPACE_PATH}}/resume/master-resume.json \
     --jobs {{WORKSPACE_PATH}}/output/jobs.json \
     --preferences {{WORKSPACE_PATH}}/config/preferences.yaml
   ```
2. **Filtering** (from `config/preferences.yaml`):
   - Location filter: preferred locations, excluded locations, work mode
   - Role filter: target titles, seniority levels, excluded titles
   - Experience filter: max/min required years in JD
   - Keyword filter: must-have keywords, excluded keywords
   - Company filter: excluded companies, priority companies
   - Department filter: included/excluded departments
3. **Two-pass scoring**:
   - **Pass 1 — TF-IDF** (fast, all jobs): keyword overlap + skill matching + title relevance + experience fit
   - **Pass 2 — Gemini AI** (top N candidates): semantic understanding of role fit, domain relevance, skill depth, and seniority alignment. Returns reasoning and APPLY/MAYBE/SKIP recommendation.
4. **Score boosting** from preferences: priority companies get +10, keyword matches get +2 each
5. Scoring criteria (weighted):
   - **Skills match** (40%): Required/preferred skills vs resume skills
   - **Experience level match** (25%): Years of experience alignment
   - **Role relevance** (20%): Job title vs resume headline/past titles
   - **Domain fit** (15%): Domain/industry relevance
6. Output ranked list to `{{WORKSPACE_PATH}}/output/scored_jobs.json`
7. Display top 10 matches with scores, reasoning, and recommendations
8. Use `--force-tfidf` flag to skip Gemini scoring (faster, no API cost)

### Step 4: User Selects Jobs (Decision Point)
1. Present the top-ranked jobs with:
   - Job title, company, location
   - Match score (0-100)
   - Key matching skills
   - Key missing skills (gaps)
2. **ASK the user** which jobs to apply to (do NOT auto-apply without confirmation)
3. User can say "apply to top 3" or select specific jobs

### Step 5: Tailor Resume with Gemini AI + Generate ATS-Friendly PDF
1. For each selected job, run:
   ```
   python {{SKILL_PATH}}/scripts/tailor_resume.py \
     --master-resume {{WORKSPACE_PATH}}/resume/master-resume.json \
     --job-id <job_id> \
     --jobs {{WORKSPACE_PATH}}/output/scored_jobs.json \
     --template {{WORKSPACE_PATH}}/resume/templates/ats-friendly.tex \
     --output-dir {{WORKSPACE_PATH}}/output/tailored
   ```
2. The script does the following:
   a. Sends master resume + JD to Google Gemini API
   b. Gemini selects relevant experience, rewrites summary and bullet points for the target role
   c. Gemini outputs structured JSON with tailored content
   d. Script injects tailored content into the ATS-friendly LaTeX template
   e. Compiles LaTeX to PDF using `pdflatex`
3. Output files per job:
   - `output/tailored/<company>_<job_id>_resume.tex` (LaTeX source)
   - `output/tailored/<company>_<job_id>_resume.pdf` (final PDF)
   - `output/tailored/<company>_<job_id>_tailored.json` (tailored JSON for records)
4. Show tailoring summary and diff to user for approval before proceeding

#### ATS-Friendly Resume Rules (Based on Research)
The LaTeX template and Gemini tailoring MUST follow these rules for maximum ATS parse rate:

**Layout (95-99% parse rate across Workday, Greenhouse, Lever, iCIMS, Ashby):**
- Single-column layout ONLY — no sidebars, no two-column, no minipage side-by-side
- Contact info (name, email, phone, city, LinkedIn) in document BODY — NEVER in headers/footers
- Simple bullets (•) with consistent indentation
- Clean whitespace between sections

**Section Headings (exact standard names required by Lever/Greenhouse parsers):**
- Use EXACTLY these heading names in this order:
  1. "Summary" (or "Professional Summary")
  2. "Skills" (or "Technical Skills")
  3. "Work Experience" (or "Experience")
  4. "Projects" (optional)
  5. "Education"
  6. "Certifications" (optional)
- NEVER use creative names like "My Journey", "What I Do", "Toolkit"

**Typography:**
- System-safe fonts: Arial, Calibri, Helvetica, or LaTeX default (Computer Modern)
- Body text: 10-12pt, Headings: 14-16pt
- No decorative fonts, no condensed fonts, no font sizes below 10pt

**Content:**
- Reverse-chronological order (most recent first)
- Consistent date format: "Month YYYY" (e.g., "Nov 2023")
- Real selectable text only — no images, no icons, no skill bars, no star ratings
- No tables for layout (ATS reads cell-by-cell, not row-by-row)
- No graphics, charts, or logos

**File:**
- Text-based PDF (not scanned, not image-flattened)
- The "Notepad test": copy-paste all text from PDF into plain text editor — if sections are out of order, ATS will see the same scramble

**Keywords:**
- Match JD keywords naturally in context — NEVER use hidden text or keyword stuffing (modern ATS flags this as spam and rejects)
- Use exact skill names from JD (e.g., if JD says "React", use "React" not "ReactJS")

**Per-ATS Special Instructions:**

**Greenhouse (strictest — optimize for this first, others will pass automatically):**
- Conservative rule-based parser — converts PDF top-to-bottom, left-to-right
- Unrecognized section headings become "Notes" and are IGNORED by keyword filters
- Structural parse failure from unconventional formatting = candidate not added at all
- MUST use exact standard headings, strict single-column, no creative formatting
- Match JD terminology EXACTLY — keyword scorecards score by presence + proximity

**Lever (lenient layout, strict on screening questions):**
- More forgiving on layout than Greenhouse
- CRITICAL: Lever roles often include extensive screening questions — incomplete or one-line answers trigger auto-rejection
- Write 50-150 words per screening question (not one-liners)
- Mirror JD vocabulary in screening answers, not just resume
- Include LinkedIn, GitHub, Portfolio links explicitly in header — Lever auto-extracts these

**Ashby (ML-based, validates structured fields):**
- Modern ML parser tolerates contemporary design but validates structured data aggressively
- Asks for structured fields (location, title, years, work authorization) and VALIDATES them against resume content
- Mismatches between form fields and resume are surfaced as errors to recruiters
- AI surfaces near-miss keywords ("Postgres" vs "PostgreSQL") — use EXACT terms
- Vague keyword matches appear as errors, not partial matches

**Universal rule:** Build resume for Greenhouse first. What passes Greenhouse will sail through Lever and Ashby. The reverse is NOT true.

### Step 5b: Generate Cover Letter (ONLY if required by application form)
**SKIP this step unless** the application form (from Step 7) explicitly has a cover letter field or the JD says "cover letter required". Do NOT generate cover letters by default — it wastes Gemini tokens.

To check: run Step 7 (fetch application form questions) FIRST. If a cover letter field exists, then run:
   ```
   python {{SKILL_PATH}}/scripts/generate_cover_letter.py \
     --master-resume {{WORKSPACE_PATH}}/resume/master-resume.json \
     --job-id <job_id> \
     --jobs {{WORKSPACE_PATH}}/output/scored_jobs.json \
     --output-dir {{WORKSPACE_PATH}}/output \
     --format both
   ```
The script:
   a. Sends resume + JD to Gemini with strict cover letter rules
   b. Generates a 3-4 paragraph, under 350 word cover letter
   c. Outputs both plain text (.txt) and LaTeX PDF (.pdf)
Cover letter rules enforced:
   - Leads with strongest relevant achievement + metric (no cliché openers)
   - Connects 2-3 specific experiences to JD requirements with numbers
   - NEVER fabricates — only uses facts from master resume
   - NEVER uses "passionate", "synergy", "I am excited to apply"

### Step 5c: Generate Application Email (ONLY when explicitly asked)
**SKIP unless** the user explicitly asks to "write email", "generate email", or the JD says to email the resume (e.g., "share your resume at xyz@company.com").

Generate a short, impactful email and save as `email.txt` in the per-job output directory.

Email rules:
- **5-7 lines max** — hiring managers skim, not read
- Opening: role name + location, one line
- Body: 2-3 sentences with your strongest metrics mapped to JD requirements (numbers, scale, impact)
- Closing: location, notice period, one line
- Sign-off: name, phone, LinkedIn — no portfolio essay
- NEVER use "passionate", "I believe", "I am confident", "Dear Sir/Madam"
- Use hiring manager's first name if known from JD
- Subject line format: `Application — {Role Title} | {Your Name}`
- All facts from master resume only — no fabrication
- If JD has a contact email, set it as the `To:` field

### Step 6: ATS Parse Validation
1. After generating PDF, run: `python {{SKILL_PATH}}/scripts/validate_ats.py --pdf {{WORKSPACE_PATH}}/output/tailored/<file>.pdf`
2. Validation checks:
   - Text extractable from PDF (not image-based)
   - Sections appear in correct order when text is extracted
   - Contact info (email, phone) found in extracted text
   - No text interleaving detected (column mixing)
   - Standard section headings present
3. If validation fails, report issues and regenerate

### Step 7: Fetch & Answer Application Form Questions
1. For each job to apply to, run:
   ```
   python {{SKILL_PATH}}/scripts/answer_questions.py \
     --master-resume {{WORKSPACE_PATH}}/resume/master-resume.json \
     --job-id <job_id> \
     --jobs {{WORKSPACE_PATH}}/output/scored_jobs.json \
     --platform <platform> \
     --answers-config {{WORKSPACE_PATH}}/config/answers.yaml \
     --cover-letter {{WORKSPACE_PATH}}/output/tailored/<company>_<job_id>_cover_letter.txt
   ```
2. The script fetches the application form from the ATS API:
   - **Greenhouse**: `GET /boards/{token}/jobs/{id}?questions=true` — returns custom questions with field types (text, select, multi-select, file)
   - **Lever**: `GET /postings/{company}/{id}/apply` — returns form fields and custom questions
   - **Ashby**: `GET /posting-api/job-board/{slug}` — returns applicationFormDefinition with field types (String, Email, ValueSelect, etc.)
3. Questions are answered using a 3-tier strategy:
   - **Tier 1 — Auto-fill**: Standard fields (name, email, phone, LinkedIn, GitHub) from master resume
   - **Tier 2 — Config match**: Common questions (work authorization, relocation, salary, years of experience, start date) from `config/answers.yaml`
   - **Tier 3 — Gemini AI**: Open-ended/custom questions ("Why this role?", "Describe a challenge", "What makes you qualified?") answered by Gemini using resume + JD context
4. For select/dropdown questions, the script maps answer text to the correct option value
5. Questions that can't be auto-answered are flagged as `needs_review`
6. Output: `output/tailored/<company>_<job_id>_answers.json`
7. **ASK the user** to review flagged questions before submission

### Step 8: Submit Application
1. For each approved application, run:
   ```
   python {{SKILL_PATH}}/scripts/apply_job.py \
     --resume-pdf {{WORKSPACE_PATH}}/output/tailored/<company>_<job_id>_resume.pdf \
     --job-id <job_id> \
     --platform <platform> \
     --answers {{WORKSPACE_PATH}}/output/tailored/<company>_<job_id>_answers.json \
     --master-resume {{WORKSPACE_PATH}}/resume/master-resume.json
   ```
2. The script submits the application with:
   - Tailored resume PDF
   - All answered form fields (standard + custom)
   - Cover letter (if required by form)
3. Application methods by platform:
   - **Greenhouse**: POST multipart/form-data with resume file + all field answers
   - **Lever**: POST multipart/form-data with resume file + custom question answers
   - **Ashby**: POST JSON/multipart with resume file + form field answers
4. Dry-run mode (default) simulates submission without actually posting
5. Log results to `{{WORKSPACE_PATH}}/output/applications_log.json`
6. Report success/failure for each application

## Resume Content Rules (Research-Backed for High-Paying Roles)

### Page Length (strictly enforced)
- **Under 5 years experience**: EXACTLY 1 page
- **5+ years experience**: EXACTLY 2 pages (recruiters prefer 2-page 2.3x over 1-page for senior roles)
- **NEVER exceed 2 pages** — ATS and recruiters penalize 3+ page resumes
- If content overflows: cut least relevant bullets and projects — never shrink font or margins
- **Rule: not length, but density** — every line must earn its place by showing impact, scale, or ownership

### Bullet Point Formula (Google's X-Y-Z)
"Accomplished [X] as measured by [Y], by doing [Z]"
- EVERY bullet starts with a concrete action verb: Led, Architected, Built, Designed, Implemented, Migrated, Optimized, Reduced, Scaled, Shipped
- EVERY bullet includes at least one metric/number where available
- FIRST bullet under each role conveys scope: team size, system scale, ownership
- NEVER use: "Responsible for", "Involved in", "Helped with", "Worked on"
- Quantify: latency, req/s, revenue, cost savings, team size, test count, uptime, users

### Must-Include Sections (in this exact order)
1. **Contact Info** — name, email, phone, city, LinkedIn, GitHub (in document body, NOT header/footer)
2. **Professional Summary** — 2-3 sentences: level, years, stack, domain, scale + best quantified win
3. **Technical Skills** — grouped by category, no proficiency bars/ratings, JD-relevant first
4. **Work Experience** — reverse chronological, 3-6 bullets per role, last 5-10 years detailed
5. **Projects** — 3 high-impact projects ONLY (ranked by: scale/metrics > tech stack match > complexity > production impact). Never include academic/hobby projects.
6. **Education** — degree, institution, dates (at bottom unless recent grad)

### What NOT to Include
- Objective statements (use Summary instead)
- References section
- Photos or headshots
- Self-assessed skill ratings (stars, bars, percentages)
- Tables, text boxes, graphics, icons
- Buzzwords: "best of breed", "go-getter", "team player", "synergy"

### Key Data Points
- Tailored resumes convert at 5.75% vs 2.68% generic (115% improvement)
- Resumes with quantified achievements get 40% more interview requests
- 36% of resumes have zero metrics — this is where differentiation happens
- Recruiters spend 6-7 seconds on initial scan (F-pattern: top-left, across, down)
- Employment gaps: one honest line gets +58.1% callbacks vs silence
- Referrals convert at 40% vs 3% cold apply — distribution beats polish

## Domain-Focus Strategy
When the candidate's master resume spans multiple domains (e.g., web + mobile, backend + data, etc.), tailor by domain relevance:
- **Primary-domain role**: Focus 90% on matching domain experience. Other domains become a brief "Also experienced in" line.
- **Secondary-domain role**: Flip the focus — 90% on that domain.
- **Mixed role**: Balance proportionally based on JD emphasis.
- Goal: the tailored resume should read as if the candidate spent their entire career in the JD's domain.

## Rules
- ZERO HALLUCINATION — Gemini may only select, reorder, and rephrase existing experience from master-resume.json
- NEVER invent skills, companies, metrics, or achievements not present in master-resume.json
- ALWAYS apply the domain-focus strategy above when tailoring
- ALWAYS generate single-column, ATS-friendly layout — no exceptions
- ALWAYS run ATS validation before submitting any resume
- ALWAYS ask user confirmation before submitting any application
- ALWAYS show the tailoring summary and diff before applying
- Respect rate limits on ATS APIs (max 1 request/second)
- Store no credentials in skill files — use .env or environment variables
- If an API requires authentication, inform the user and guide them to set it up

## End State
- Jobs filtered by preferences (location, role, experience, keywords, departments)
- User has a ranked list with Gemini AI reasoning + APPLY/MAYBE/SKIP recommendations in `output/scored_jobs.json`
- ATS-friendly tailored PDF resumes in `output/tailored/`
- Tailored cover letters (PDF + text) in `output/tailored/`
- ATS validation passed for all generated PDFs
- Applications submitted and logged in `output/applications_log.json`
- Summary report: jobs applied, success/failure status, next steps
