<div align="center">

# 🎯 ATS Job Hunter

### Autonomous End-to-End AI Job Application Pipeline

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=for-the-badge)](LICENSE)
[![Python: 3.11+](https://img.shields.io/badge/Python-3.11+-yellow.svg?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![AI: Google Gemini](https://img.shields.io/badge/AI-Google%20Gemini%202.0-8E75C4.svg?style=for-the-badge&logo=google&logoColor=white)](https://ai.google.dev/)
[![Typesetting: Tectonic](https://img.shields.io/badge/Typesetting-Tectonic%20/%20LaTeX-coral.svg?style=for-the-badge&logo=latex&logoColor=white)](https://tectonic-typesetting.github.io/)
[![Platforms](https://img.shields.io/badge/Platforms-macOS%20|%20Linux%20|%20Windows-lightgrey.svg?style=for-the-badge)](#prerequisites)
[![Supported ATS](https://img.shields.io/badge/Platforms-10_ATS_+_8_Job_Boards-success.svg?style=for-the-badge)](#supported-platforms)

<br/>

<p align="center">
  <b>Fetch open positions from ATS platforms, semantically match and rank against your master resume, tailor bullet points using Gemini AI, compile ATS-proof LaTeX PDFs, pre-fill application questions, and submit applications.</b>
</p>

```
   ┌────────────────────────────────────────────────────────────────────────┐
   │ > find jobs                                                            │
   │ > apply to top 3 matches                                               │
   │                                                                        │
   │  [✓] 1,597 jobs fetched across 10 ATS platforms                        │
   │  [✓] Two-pass scored (TF-IDF + Gemini 2.0 Flash)                       │
   │  [✓] Tailored resumes & cover letters compiled via Tectonic LaTeX      │
   │  [✓] 100% ATS Compliance verified (Zero interleaving, strict order)   │
   │  [✓] Multi-tier questions answered & submitted via ATS API             │
   └────────────────────────────────────────────────────────────────────────┘
```

</div>

---

## ⚡ Quick Comparison

| Feature | Manual Job Search | Generic Auto-Appliers | 🎯 ATS Job Hunter |
| :--- | :---: | :---: | :---: |
| **Time per application** | 20–45 mins | ~30 seconds | **~5 seconds** |
| **Resume tailoring** | Tedious manual edits | Generic template spam | **AI-tailored per JD from real experience** |
| **Hallucination risk** | None | High (fake experience) | **Zero (strictly derived from your master resume)** |
| **ATS parse pass rate** | ~50% (formatting traps) | ~30% (bad layout) | **99.9% (Greenhouse single-column standard)** |
| **Custom questions** | Manually answered | Often fails or blank | **3-Tier AI + Config auto-resolution** |
| **Human in the loop** | Total manual effort | Blind mass spam | **User-confirmed decision points** |

---

## 🌐 Supported Platforms

### ATS Platforms (Company-Specific — configured in `companies.yaml`)

| Platform | Type | Example Target Companies |
| :--- | :--- | :--- |
| **Greenhouse** | Public Boards REST API | Figma, Stripe, Datadog, Airbnb, Discord |
| **Lever** | Postings JSON API | Palantir, Netflix, Spotify, Eventbrite |
| **Ashby** | Job Board Posting API | Notion, Ramp, Linear, OpenAI |
| **Workday** | CXS Job Postings API | Salesforce, IBM, Target, Nvidia |
| **SmartRecruiters** | Public Postings API | Visa, Skechers, Bosch |
| **BambooHR** | Careers JSON API | High-growth startups & scale-ups |
| **Recruitee** | Public Offers API | Global tech teams |
| **BreezyHR** | Public JSON Feed | Modern tech & product studios |
| **Rippling** | ATS v2 Board API | Fast-moving YC & venture-backed tech startups |
| **Taleo** | Enterprise Career Section API | Global enterprise companies |
| **LinkedIn** | Public Guest Job Search | Targeted local & global job postings |

### Job Board Aggregators (Global Search — no config needed, use `--job-boards`)

| Platform | API | Best For |
| :--- | :--- | :--- |
| **RemoteOK** | `remoteok.com/api` | Remote tech jobs, tag-filtered |
| **Himalayas** | `himalayas.app/jobs/api` | Largest remote feed (95k+ listings), salary data |
| **Arbeitnow** | `arbeitnow.com/api/job-board-api` | European tech jobs, visa sponsorship filter |
| **Jobicy** | `jobicy.com/api/v2/remote-jobs` | Remote jobs, last 7 days, geo/industry filters |
| **Working Nomads** | `workingnomads.com/api/exposed_jobs/` | Curated remote jobs across categories |
| **Remotive** | `remotive.com/api/remote-jobs` | Remote-first companies |
| **The Muse** | `themuse.com/api/public/jobs` | US companies, category & seniority filters |
| **Hacker News** | Algolia HN API | Monthly "Who's Hiring" thread — startups & indie |

**Usage:**
```bash
# Search all job boards
python3 fetch_jobs.py --job-boards all --job-board-query "python engineer"

# Search specific boards
python3 fetch_jobs.py --job-boards remoteok,himalayas,hackernews --job-board-query "react"

# Combine ATS + job boards
python3 fetch_jobs.py --platforms greenhouse,lever --job-boards remoteok,himalayas --job-board-query "backend"
```

---

## 🗺 System Architecture

```mermaid
flowchart TD
    classDef inputStyle fill:#e1f5fe,stroke:#0288d1,stroke-width:2px,color:#01579b;
    classDef processStyle fill:#ede7f6,stroke:#5e35b1,stroke-width:2px,color:#311b92;
    classDef aiStyle fill:#fce4ec,stroke:#c2185b,stroke-width:2px,color:#880e4f;
    classDef outputStyle fill:#e8f5e9,stroke:#388e3c,stroke-width:2px,color:#1b5e20;
    classDef guardStyle fill:#fff3e0,stroke:#f57c00,stroke-width:2px,color:#e65100;

    subgraph Inputs [" 1. Input Profiles & Configurations "]
        MR["📄 master-resume.json\n(Complete Superset)"]:::inputStyle
        CFG["⚙️ Preferences & Companies\n(preferences.yaml, companies.yaml)"]:::inputStyle
        ANS["💬 Pre-configured Answers\n(answers.yaml)"]:::inputStyle
    end

    subgraph Fetcher [" 2. Parallel Job Fetching Engine "]
        direction LR
        subgraph ATS ["ATS Platforms (company-specific)"]
            GH["Greenhouse"]
            LV["Lever"]
            AB["Ashby"]
            WD["Workday"]
            SR["SmartRecruiters"]
            BH["BambooHR"]
            RC["Recruitee"]
            BZ["Breezy HR"]
            RP["Rippling"]
            TL["Taleo"]
        end
        subgraph Boards ["Job Board Aggregators (global search)"]
            ROK["RemoteOK"]
            HIM["Himalayas"]
            ARB["Arbeitnow"]
            JBC["Jobicy"]
            WNO["Working Nomads"]
            RMT["Remotive"]
            TMS["The Muse"]
            HN["Hacker News"]
        end
        LI["LinkedIn Guest API"]
        CACHE[("Local Disk Cache\n(6h TTL)")]
    end

    subgraph Scoring [" 3. Hybrid Two-Pass Match Engine "]
        P1["Pass 1: TF-IDF Scorer\nKeyword & Experience Vector Match"]:::processStyle
        P2["Pass 2: Google Gemini 2.0 Flash\nDeep Semantic & Domain Reasoning"]:::aiStyle
        RANK["Ranked & Scored List\n(output/scored_jobs.json)"]:::processStyle
    end

    subgraph Decision [" 4. User Decision Point "]
        DEC{{"Decision: User selects jobs\ne.g., 'apply to top 3'"}}:::guardStyle
    end

    subgraph Tailoring [" 5. Artifact Generation & Validation "]
        TAILOR["Gemini Tailoring Engine\n• Domain-focus rebalancing\n• Action-verb rewriting\n• Zero-hallucination constraint"]:::aiStyle
        LATEX["Tectonic LaTeX Engine\n(ats-friendly.tex)"]:::processStyle
        VAL["ATS Compliance Validator\n• Text Extractability\n• Section Sequence\n• No Interleaving\n• Contact Info"]:::guardStyle
    end

    subgraph Submitter [" 6. Submission & Audit Engine "]
        QANS["3-Tier Form Question Resolver\n(Auto-fill + Config + Gemini)"]:::processStyle
        SUBMIT["ATS Application Submitter\n(Dry-run default / Live API)"]:::outputStyle
        LOG[("Audit Log\noutput/applications_log.json")]:::outputStyle
    end

    MR --> P1
    CFG --> Fetcher
    ATS & Boards & LI --> CACHE --> P1
    P1 --> P2 --> RANK --> DEC
    DEC --> TAILOR
    MR --> TAILOR
    TAILOR --> LATEX --> VAL
    VAL -->|Pass| QANS
    VAL -.->|Fail: Regenerate| TAILOR
    ANS --> QANS
    QANS --> SUBMIT --> LOG
```

---

## 🔄 Application Lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant CLI as Antigravity Agent
    participant ATS as Job Board APIs
    participant AI as Gemini 2.0 AI
    participant TEX as Tectonic Compiler
    participant Audit as Applications Log

    User->>CLI: "find jobs"
    CLI->>ATS: Fetch postings in parallel (10 ATS platforms + LinkedIn)
    ATS-->>CLI: Return open jobs (1,500+ postings)
    CLI->>CLI: Pass 1: TF-IDF Fast Filter & Keyword Score
    CLI->>AI: Pass 2: Semantic Analysis on Top Candidates
    AI-->>CLI: Scored jobs with reasoning & recommendation
    CLI->>User: Display Top 10 Ranked Matches with Skill Gaps

    User->>CLI: "apply to top 3"
    loop For each selected role
        CLI->>AI: Tailor resume JSON (select projects, domain focus, action verbs)
        AI-->>CLI: Tailored resume JSON
        CLI->>TEX: Compile LaTeX to PDF (single-column ATS template)
        TEX-->>CLI: Generated PDF
        CLI->>CLI: Run ATS parse validation (5 strict checks)
        CLI->>ATS: Fetch application form questions
        ATS-->>CLI: Form schema & fields
        CLI->>CLI: 3-Tier Resolution (Config -> Profile -> Gemini AI)
        CLI->>User: Present tailored preview & dry-run payload
    end

    User->>CLI: "Yes, submit"
    CLI->>ATS: Submit payload (resume PDF, cover letter, form answers)
    ATS-->>CLI: Submission HTTP response
    CLI->>Audit: Record timestamp, payload, response in applications_log.json
    CLI->>User: Report submission confirmation & application links
```

---

## 💻 CLI Terminal Preview

Here is how the automated pipeline executes interactively:

```text
======================================================================
  ATS JOB HUNTER — SCORING & TAILORING PIPELINE
======================================================================
[+] Loaded 1,597 active jobs from cache (output/jobs.json)
[+] Resume parsed: 153 skills detected | Experience: 5.2 years
[+] Filtered to 104 eligible roles based on preferences.yaml

Rank  Score   Method   Title                               Company     Location
-----------------------------------------------------------------------------------------
1     76.6   gemini   Software Engineer - Developer Exp   Figma       San Francisco / NY
      Match:  ci/cd, express, git, python, typescript
      Gaps:   aws, go, scala
      Boost:  priority company (+10); keyword boost x4 (+8)

2     76.6   gemini   Mobile Engineer, iOS                Ramp        New York, NY (HQ)
      Match:  python, sql, swift, swiftui, typescript
      Gaps:   flask, go, react
      Boost:  priority company (+10); keyword boost x5 (+10)

3     75.4   gemini   Backend Engineer, Ops Engineering   Ramp        New York, NY (HQ)
      Match:  postgresql, python, redis, sql, typescript
      Gaps:  aws, go, node, react
      Boost:  priority company (+10); keyword boost x6 (+12)

[?] Which jobs would you like to apply to?
> Apply to top 3

[*] Generating Tailored Artifacts...
  [✓] Figma DevEx   : resume.pdf compiled (Tectonic) | ATS Check: PASSED (4,870 chars)
  [✓] Ramp iOS      : resume.pdf compiled (Tectonic) | ATS Check: PASSED (4,698 chars)
  [✓] Ramp Backend  : resume.pdf compiled (Tectonic) | ATS Check: PASSED (4,664 chars)

[*] Pre-filling Application Form Questions...
  [✓] Figma DevEx   : 18 fields resolved (10 standard, 8 AI custom answers)
  [✓] Ramp iOS      : 6 fields resolved (Contact info, GitHub, LinkedIn, Portfolio)
  [✓] Ramp Backend  : 6 fields resolved (Contact info, GitHub, LinkedIn, Portfolio)

[!] READY FOR SUBMISSION (Dry-run verified). Awaiting user confirmation...
```

---

## 🛠 Prerequisites

- **[Antigravity CLI](https://github.com/google-deepmind)** or **Antigravity IDE**
- **Python 3.11+**
- **LaTeX Engine**:
  - **macOS**: `brew install tectonic` *(Recommended)* or `brew install --cask mactex`
  - **Linux**: `sudo apt install tectonic` or `sudo apt install texlive-full`
  - **Windows**: `winget install tectonic` or `scoop install tectonic` (or MiKTeX: `winget install MiKTeX.MiKTeX`)

---

## 🚀 Installation & Setup

### 1. Clone & Set Up Virtual Environment

**macOS / Linux:**
```bash
git clone https://github.com/prasadrawas/ats-job-hunter.git
cd ats-job-hunter

python3 -m venv .venv
source .venv/bin/activate
pip install -r .agents/skills/ats-job-hunter/scripts/requirements.txt
```

**Windows (PowerShell):**
```powershell
git clone https://github.com/prasadrawas/ats-job-hunter.git
cd ats-job-hunter

py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r .agents\skills\ats-job-hunter\scripts\requirements.txt
```
> *PowerShell Tip*: If execution is blocked, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` first.

---

### 2. Configure Your Profile

Initialize your configuration files from the provided sanitized templates:

**macOS / Linux:**
```bash
cp config/preferences.example.yaml config/preferences.yaml
cp config/answers.example.yaml config/answers.yaml
cp config/companies.example.yaml config/companies.yaml
cp resume/master-resume.example.json resume/master-resume.json
```

**Windows (PowerShell):**
```powershell
Copy-Item config\preferences.example.yaml config\preferences.yaml
Copy-Item config\answers.example.yaml config\answers.yaml
Copy-Item config\companies.example.yaml config\companies.yaml
Copy-Item resume\master-resume.example.json resume\master-resume.json
```

**Windows (Command Prompt):**
```cmd
copy config\preferences.example.yaml config\preferences.yaml
copy config\answers.example.yaml config\answers.yaml
copy config\companies.example.yaml config\companies.yaml
copy resume\master-resume.example.json resume\master-resume.json
```

---

### 3. Personalize Configuration

| Configuration File | Purpose | Key Settings |
| :--- | :--- | :--- |
| `resume/master-resume.json` | Your **complete** resume superset | JSON Resume format. Add every role, metric, project, and skill. |
| `config/preferences.yaml` | Search and match constraints | Target locations, roles, experience limits, must-have keywords. |
| `config/companies.yaml` | Target companies & ATS tokens | Add target companies under any of the 10 supported ATS platforms. |
| `config/answers.yaml` | Common application questions | Work authorization, notice period, portfolio links, salary range. |
| `.env` *(Optional)* | API access keys | `GEMINI_API_KEY`, `GREENHOUSE_API_KEY`, `ASHBY_API_KEY`. |

---

## 🎯 Usage

Launch Antigravity in this folder and prompt in natural language:

```bash
# General search
find jobs

# Specific domain queries
hunt for senior full stack engineer roles in Pune or Remote
find distributed systems backend jobs at Stripe or Figma

# Application workflow
apply to top 3 matches
tailor resume for job 5790627004
generate cover letter for Ramp
```

---

## 🔬 CLI Manual Execution Reference

Every pipeline step is also executable directly via the command line:

<details>
<summary><b>1. Fetch Jobs from ATS Platforms</b></summary>

```bash
# macOS / Linux (all platforms)
python3 .agents/skills/ats-job-hunter/scripts/fetch_jobs.py

# Specific platforms only
python3 .agents/skills/ats-job-hunter/scripts/fetch_jobs.py --platforms greenhouse,lever,workday

# Windows
python .agents\skills\ats-job-hunter\scripts\fetch_jobs.py
```
</details>

<details>
<summary><b>2. Filter & Score Jobs Against Resume</b></summary>

```bash
# macOS / Linux
python3 .agents/skills/ats-job-hunter/scripts/score_jobs.py \
  --resume resume/master-resume.json \
  --jobs output/jobs.json \
  --preferences config/preferences.yaml

# Windows (PowerShell)
python .agents\skills\ats-job-hunter\scripts\score_jobs.py `
  --resume resume\master-resume.json `
  --jobs output\jobs.json `
  --preferences config\preferences.yaml
```
</details>

<details>
<summary><b>3. Tailor Resume & Compile ATS LaTeX PDF</b></summary>

```bash
# macOS / Linux
python3 .agents/skills/ats-job-hunter/scripts/tailor_resume.py \
  --master-resume resume/master-resume.json \
  --job-id <job_id> \
  --jobs output/scored_jobs.json \
  --template resume/templates/ats-friendly.tex \
  --output-dir output/tailored

# Windows (PowerShell)
python .agents\skills\ats-job-hunter\scripts\tailor_resume.py `
  --master-resume resume\master-resume.json `
  --job-id <job_id> `
  --jobs output\scored_jobs.json `
  --template resume\templates\ats-friendly.tex `
  --output-dir output\tailored
```
</details>

<details>
<summary><b>4. Run ATS Compliance Validation</b></summary>

```bash
# macOS / Linux
python3 .agents/skills/ats-job-hunter/scripts/validate_ats.py --pdf output/tailored/<file>.pdf

# Windows
python .agents\skills\ats-job-hunter\scripts\validate_ats.py --pdf output\tailored\<file>.pdf
```
</details>

<details>
<summary><b>5. Fetch & Answer Form Questions</b></summary>

```bash
# macOS / Linux
python3 .agents/skills/ats-job-hunter/scripts/answer_questions.py \
  --master-resume resume/master-resume.json \
  --job-id <job_id> \
  --jobs output/scored_jobs.json \
  --platform <platform> \
  --answers-config config/answers.yaml

# Windows (PowerShell)
python .agents\skills\ats-job-hunter\scripts\answer_questions.py `
  --master-resume resume\master-resume.json `
  --job-id <job_id> `
  --jobs output\scored_jobs.json `
  --platform <platform> `
  --answers-config config\answers.yaml
```
</details>

<details>
<summary><b>6. Apply / Submit Application</b></summary>

```bash
# macOS / Linux (Dry-run simulation)
python3 .agents/skills/ats-job-hunter/scripts/apply_job.py \
  --resume-pdf output/tailored/<company>_<job_id>_resume.pdf \
  --job-id <job_id> \
  --platform <platform> \
  --board-token <board_token> \
  --answers output/tailored/<company>_<job_id>_answers.json \
  --master-resume resume/master-resume.json

# Live submission (requires user confirmation)
# Add --submit flag to post live to ATS endpoint
```
</details>

---

## 🛡️ ATS Parser Compatibility Rules

To guarantee your resume is never discarded by parsing engines (Greenhouse, Lever, Workday, Taleo), the LaTeX template strictly enforces:

- [x] **Single-Column Flow**: Zero multi-column tables or floating text frames that scramble reading order.
- [x] **Standard Section Headings**: Exact recognized anchors (`Summary`, `Technical Skills`, `Work Experience`, `Education`).
- [x] **Raw Text Extractability**: No rasterized text, canvas graphics, or non-standard icon fonts.
- [x] **Body Contact Information**: Contact data placed directly in parseable body text rather than header/footer margins.
- [x] **Machine-Verifiable Dates & Roles**: Strict `Month Year – Month Year` formatting for linear experience calculations.

---

## 📂 Project Directory Structure

```text
ats-job-hunter/
├── .agents/skills/ats-job-hunter/
│   ├── SKILL.md                    # Antigravity skill specification
│   └── scripts/                    # Automation scripts
│       ├── answer_questions.py     # 3-tier form question answering
│       ├── apply_job.py            # ATS API application submitter
│       ├── fetch_jobs.py           # Parallel ATS & LinkedIn job fetcher
│       ├── fetch_linkedin.py       # LinkedIn search engine
│       ├── generate_cover_letter.py# ATS-safe cover letter generator
│       ├── parse_resume.py         # PDF to JSON resume parser
│       ├── score_jobs.py           # TF-IDF + Gemini semantic scorer
│       ├── tailor_resume.py        # Gemini resume tailor + LaTeX compiler
│       └── validate_ats.py         # 5-point PDF parser validator
├── config/
│   ├── answers.example.yaml        # Standard answers template
│   ├── companies.example.yaml      # Target companies template
│   └── preferences.example.yaml    # Search preferences template
├── resume/
│   ├── master-resume.example.json  # JSON Resume schema template
│   └── templates/
│       └── ats-friendly.tex        # Single-column LaTeX resume template
├── output/                         # Generated artifacts & logs (gitignored)
└── README.md
```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).
