# ATS Job Hunter

End-to-end job application automation for Antigravity. Fetches open positions from ATS platforms (Greenhouse, Lever, Ashby), scores them against your resume using AI, tailors your resume per job, generates ATS-friendly LaTeX PDFs, and submits applications.

## What It Does

1. **Fetch** open jobs from Greenhouse, Lever, and Ashby public APIs (+ optional LinkedIn scraping)
2. **Score** each job against your master resume using TF-IDF + Google Gemini AI
3. **Tailor** your resume for selected jobs — Gemini rewrites bullets, selects relevant experience, mirrors JD keywords
4. **Generate** ATS-optimized single-column LaTeX PDF resumes
5. **Answer** application form questions automatically (standard fields + AI for open-ended questions)
6. **Apply** via ATS APIs with your tailored resume and answers

## Prerequisites

- Antigravity CLI
- Python 3.11+
- LaTeX (`brew install --cask mactex` on macOS, or `sudo apt install texlive-full` on Linux)

## Setup

### 1. Install Python dependencies

```bash
pip install -r .agents/skills/ats-job-hunter/scripts/requirements.txt
```

### 2. Add your resume

Create `resume/master-resume.json` following the [JSON Resume](https://jsonresume.org/schema/) format. See `resume/master-resume.example.json` for the expected structure.

This is your **complete** resume — every skill, role, project, and metric you have. The AI selects from this superset when tailoring for each job.

### 3. Configure your preferences

```bash
cp config/preferences.example.yaml config/preferences.yaml
cp config/answers.example.yaml config/answers.yaml
cp config/companies.example.yaml config/companies.yaml
```

Edit each file:

- **preferences.yaml** — Target locations, roles, seniority, keywords, salary floor, scoring settings
- **answers.yaml** — Work authorization, salary expectations, availability, links, standard question answers
- **companies.yaml** — Target companies by ATS platform (Greenhouse/Lever/Ashby board tokens)

## Usage

Open Antigravity in this directory and use natural language:

```
find jobs
hunt for senior react engineer roles
apply to top 3 matches
tailor resume for job <id>
write application email for <company>
```

The skill triggers on phrases like "find jobs", "hunt jobs", "apply to jobs", "tailor resume", "job search", etc.

## Project Structure

```
.
├── config/
│   ├── answers.example.yaml    # Application answers template
│   ├── companies.example.yaml  # Target companies template
│   └── preferences.example.yaml # Search preferences template
├── resume/
│   ├── master-resume.example.json  # Resume format reference
│   └── templates/
│       └── ats-friendly.tex    # LaTeX template for PDF generation
├── .agents/skills/ats-job-hunter/
│   ├── SKILL.md                # Skill definition (Antigravity reads this)
│   └── scripts/                # Python automation scripts
├── output/                     # Generated resumes, scores, logs (gitignored)
└── README.md
```

## How the Pipeline Works (End-to-End)

The entire pipeline is orchestrated by Antigravity via the skill definition in `SKILL.md`, but each step is a standalone Python script you can also run manually.

### Step 1: Load Master Resume

The pipeline reads your complete resume from `resume/master-resume.json` (JSON Resume format). This is the **superset** of all your experience — it is never modified. If you only have a PDF, `parse_resume.py` extracts structured data from it.

### Step 2: Fetch Jobs from ATS Platforms

```
python scripts/fetch_jobs.py --platforms greenhouse,lever,ashby
```

Queries public job board APIs **in parallel** (5 threads):

| Platform | API Endpoint |
|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{token}/jobs` |
| Lever | `api.lever.co/v0/postings/{company}?mode=json` |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{slug}` |

- Company board tokens come from your `config/companies.yaml`
- Results are cached for 6 hours (use `--cache-hours 0` to force refresh)
- Duplicates across platforms are automatically removed
- Optional LinkedIn scraping via `--linkedin-query` flag
- Output: `output/jobs.json`

### Step 3: Filter + Score Jobs

```
python scripts/score_jobs.py \
  --resume resume/master-resume.json \
  --jobs output/jobs.json \
  --preferences config/preferences.yaml
```

**Filtering** (from `preferences.yaml`): location, role titles, seniority, experience years, keywords, departments, excluded companies.

**Two-pass scoring**:
1. **Pass 1 — TF-IDF** (fast, all jobs): keyword overlap, skill matching, title relevance, experience fit
2. **Pass 2 — Gemini AI** (top N from Pass 1): semantic analysis of role fit, domain relevance, skill depth. Returns reasoning and an APPLY / MAYBE / SKIP recommendation

Scoring weights: Skills match (40%) + Experience level (25%) + Role relevance (20%) + Domain fit (15%). Priority companies and keyword matches get score boosts.

- Output: `output/scored_jobs.json` — ranked list with scores, reasoning, and recommendations

### Step 4: User Selects Jobs (Decision Point)

The pipeline presents the top-ranked jobs with title, company, score, matching skills, and skill gaps. **You choose** which jobs to apply to — no auto-applying without confirmation.

### Step 5: Tailor Resume + Generate PDF

```
python scripts/tailor_resume.py \
  --master-resume resume/master-resume.json \
  --job-id <job_id> \
  --jobs output/scored_jobs.json \
  --template resume/templates/ats-friendly.tex \
  --output-dir output/tailored
```

For each selected job:
1. Sends your master resume + the job description to Google Gemini
2. Gemini selects relevant experience, rewrites the summary and bullet points using JD keywords, picks the best projects — all from your real resume (zero hallucination)
3. If your resume spans multiple domains (e.g., web + mobile), it focuses 90% on the domain the JD targets
4. Injects tailored content into the ATS-friendly LaTeX template
5. Compiles to PDF via `pdflatex`

Output per job:
- `output/tailored/<company>_<job_id>_resume.tex`
- `output/tailored/<company>_<job_id>_resume.pdf`
- `output/tailored/<company>_<job_id>_tailored.json`

**ATS compliance**: single-column layout, standard section headings (`Summary`, `Technical Skills`, `Work Experience`, `Education`), no tables/graphics/icons, contact info in document body (not headers). Optimized for Greenhouse's strict parser — what passes Greenhouse passes everything else.

### Step 5b: Generate Cover Letter (Only If Required)

```
python scripts/generate_cover_letter.py \
  --master-resume resume/master-resume.json \
  --job-id <job_id> \
  --jobs output/scored_jobs.json \
  --output-dir output/tailored \
  --format both
```

Skipped by default. Only runs if the application form has a cover letter field. Generates a 3-4 paragraph, under 350-word cover letter — leads with strongest achievement, no cliches, facts only from your resume. Outputs both `.txt` and `.pdf`.

### Step 5c: Generate Application Email (Only If Asked)

Generates a 5-7 line email when the JD says to email your resume. Includes role name, 2-3 metrics mapped to JD requirements, and contact info.

### Step 6: ATS Parse Validation

```
python scripts/validate_ats.py --pdf output/tailored/<file>.pdf
```

Validates the generated PDF before submission:
- Text is extractable (not image-based)
- Sections appear in correct order when extracted
- Contact info (email, phone) found in text
- No column interleaving detected
- Standard section headings present

If validation fails, issues are reported and the resume is regenerated.

### Step 7: Fetch + Answer Application Form Questions

```
python scripts/answer_questions.py \
  --master-resume resume/master-resume.json \
  --job-id <job_id> \
  --jobs output/scored_jobs.json \
  --platform <platform> \
  --answers-config config/answers.yaml
```

Fetches the application form from the ATS API and answers questions using a 3-tier strategy:

| Tier | What | Example |
|---|---|---|
| **1 — Auto-fill** | Standard fields from your resume | Name, email, phone, LinkedIn |
| **2 — Config match** | Common questions from `answers.yaml` | Work authorization, salary, notice period |
| **3 — Gemini AI** | Open-ended / custom questions | "Why this role?", "Describe a challenge" |

For dropdown/select questions, the script maps your answer text to the correct option value. Questions that can't be auto-answered are flagged for your review.

- Output: `output/tailored/<company>_<job_id>_answers.json`

### Step 8: Submit Application

```
python scripts/apply_job.py \
  --resume-pdf output/tailored/<company>_<job_id>_resume.pdf \
  --job-id <job_id> \
  --platform <platform> \
  --answers output/tailored/<company>_<job_id>_answers.json \
  --master-resume resume/master-resume.json
```

Submits via the ATS API with the tailored resume PDF, all answered form fields, and cover letter (if required). Each platform uses its own submission format (multipart form data or JSON).

- **Dry-run mode** is on by default — simulates submission without actually posting
- Results logged to `output/applications_log.json`

### Pipeline Flow Diagram

```
master-resume.json + companies.yaml + preferences.yaml
        │
        ▼
┌─────────────────┐
│  1. Fetch Jobs   │──► output/jobs.json
│  (Greenhouse,    │    (cached 6h)
│   Lever, Ashby)  │
└────────┬────────┘
         ▼
┌─────────────────┐
│  2. Filter +     │──► output/scored_jobs.json
│  Score (TF-IDF   │    (ranked + APPLY/MAYBE/SKIP)
│  + Gemini AI)    │
└────────┬────────┘
         ▼
┌─────────────────┐
│  3. User Picks   │    "apply to top 3"
│  Jobs to Apply   │
└────────┬────────┘
         ▼
┌─────────────────┐
│  4. Tailor Resume│──► <company>_<id>_resume.pdf
│  (Gemini + LaTeX)│    <company>_<id>_tailored.json
└────────┬────────┘
         ▼
┌─────────────────┐
│  5. Validate ATS │    (re-generate if fails)
└────────┬────────┘
         ▼
┌─────────────────┐
│  6. Answer Form  │──► <company>_<id>_answers.json
│  Questions       │    (auto + config + Gemini)
└────────┬────────┘
         ▼
┌─────────────────┐
│  7. Submit       │──► applications_log.json
│  (dry-run default)│
└─────────────────┘
```

## License

MIT
