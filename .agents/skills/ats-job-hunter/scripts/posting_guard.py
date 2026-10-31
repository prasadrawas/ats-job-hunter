#!/usr/bin/env python3
"""Security gate for job postings — detects hidden text, prompt injection, and obfuscation.

Checks job descriptions for:
- Hidden text patterns (HTML comments, invisible Unicode characters)
- Prompt injection attempts (system/assistant directives, ignore-previous patterns)
- Hidden URLs embedded in the description
- Invisible characters between visible text
- Mixed-script text that may hide keywords

Run this BEFORE scoring to catch malicious or misleading job postings.
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path


# ---------------------------------------------------------------------------
# Invisible / zero-width Unicode characters
# ---------------------------------------------------------------------------
INVISIBLE_CHARS = {
    "\u200B": "ZERO WIDTH SPACE",
    "\u200C": "ZERO WIDTH NON-JOINER",
    "\u200D": "ZERO WIDTH JOINER",
    "\uFEFF": "BYTE ORDER MARK / ZERO WIDTH NO-BREAK SPACE",
    "\u200E": "LEFT-TO-RIGHT MARK",
    "\u200F": "RIGHT-TO-LEFT MARK",
    "\u2060": "WORD JOINER",
    "\u2061": "FUNCTION APPLICATION",
    "\u2062": "INVISIBLE TIMES",
    "\u2063": "INVISIBLE SEPARATOR",
    "\u2064": "INVISIBLE PLUS",
    "\u00AD": "SOFT HYPHEN",
    "\u034F": "COMBINING GRAPHEME JOINER",
    "\u180E": "MONGOLIAN VOWEL SEPARATOR",
}

# ---------------------------------------------------------------------------
# Prompt injection patterns
# ---------------------------------------------------------------------------
INJECTION_PATTERNS = [
    (r"(?i)ignore\s+(all\s+)?previous\s+(instructions?|prompts?|context)", "ignore-previous directive"),
    (r"(?i)disregard\s+(all\s+)?previous", "disregard-previous directive"),
    (r"(?i)^system\s*:", "system: prefix (prompt injection)"),
    (r"(?i)^assistant\s*:", "assistant: prefix (prompt injection)"),
    (r"(?i)^user\s*:", "user: prefix (prompt injection)"),
    (r"(?i)you\s+are\s+(now\s+)?a\s+", "role reassignment (you are a...)"),
    (r"(?i)forget\s+(everything|all|your)\s+(you|instructions?|training)", "forget-instructions directive"),
    (r"(?i)new\s+instructions?\s*:", "new instructions directive"),
    (r"(?i)override\s+(previous|system|all)", "override directive"),
    (r"(?i)do\s+not\s+follow\s+(your|the|previous)", "do-not-follow directive"),
    (r"(?i)\bact\s+as\s+(if\s+you\s+are|a)\b", "act-as directive"),
    (r"(?i)pretend\s+(you\s+are|to\s+be)", "pretend directive"),
    (r"(?i)from\s+now\s+on,?\s+(you|ignore|respond)", "from-now-on directive"),
]

# ---------------------------------------------------------------------------
# Hidden URL patterns
# ---------------------------------------------------------------------------
HIDDEN_URL_PATTERNS = [
    # URLs inside HTML tags that wouldn't render visibly
    r'<[^>]*(?:href|src|action)\s*=\s*["\']?(https?://[^"\'\s>]+)',
    # Markdown-style hidden links
    r'\[(?:\s*)\]\((https?://[^\)]+)\)',
    # Zero-width separated URLs
    r'https?://[\w\.\-]+(?:[\u200B-\u200D\uFEFF][\w\.\-]+)+',
]


def check_invisible_chars(text: str) -> list[dict]:
    """Detect invisible Unicode characters in the text."""
    findings = []
    for char, name in INVISIBLE_CHARS.items():
        positions = [i for i, c in enumerate(text) if c == char]
        if positions:
            # Show context around the first occurrence
            first = positions[0]
            context_start = max(0, first - 15)
            context_end = min(len(text), first + 15)
            context = text[context_start:context_end].replace(char, f"[{name}]")

            findings.append({
                "type": "invisible_char",
                "severity": "high",
                "char_name": name,
                "codepoint": f"U+{ord(char):04X}",
                "count": len(positions),
                "context": context,
            })
    return findings


def check_html_comments(text: str) -> list[dict]:
    """Detect HTML comments that may hide content."""
    findings = []
    comments = re.findall(r"<!--(.*?)-->", text, re.DOTALL)
    for comment in comments:
        stripped = comment.strip()
        if stripped:
            findings.append({
                "type": "html_comment",
                "severity": "medium",
                "content": stripped[:200],
            })
    return findings


def check_prompt_injection(text: str) -> list[dict]:
    """Detect prompt injection patterns."""
    findings = []
    lines = text.split("\n")
    for line_num, line in enumerate(lines, 1):
        for pattern, description in INJECTION_PATTERNS:
            if re.search(pattern, line):
                findings.append({
                    "type": "prompt_injection",
                    "severity": "critical",
                    "pattern": description,
                    "line": line_num,
                    "content": line.strip()[:200],
                })
                break  # one finding per line is enough
    return findings


def check_hidden_urls(text: str) -> list[dict]:
    """Detect URLs that are hidden or obfuscated."""
    findings = []
    for pattern in HIDDEN_URL_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for url in matches:
            findings.append({
                "type": "hidden_url",
                "severity": "medium",
                "url": url[:300],
            })
    return findings


def check_invisible_between_visible(text: str) -> list[dict]:
    """Detect invisible characters injected between visible characters.

    For example: "P‌y‌t‌h‌o‌n" with ZWNJs between each letter.
    """
    findings = []
    invisible_set = set(INVISIBLE_CHARS.keys())

    # Look for patterns: visible + invisible + visible (3+ times in a row)
    runs = []
    current_run = 0
    current_start = 0

    for i, char in enumerate(text):
        if char in invisible_set:
            if current_run == 0:
                current_start = i
            current_run += 1
        else:
            if current_run > 0:
                # Check if visible chars surround the invisible run
                if current_start > 0 and i < len(text):
                    before = text[current_start - 1]
                    after = text[i] if i < len(text) else ""
                    if before.isalnum() and after.isalnum():
                        runs.append((current_start, current_run))
            current_run = 0

    if len(runs) >= 3:
        # Reconstruct the word by removing invisible chars
        sample_start = max(0, runs[0][0] - 5)
        sample_end = min(len(text), runs[-1][0] + runs[-1][1] + 5)
        raw = text[sample_start:sample_end]
        cleaned = "".join(c for c in raw if c not in invisible_set)

        findings.append({
            "type": "invisible_between_visible",
            "severity": "high",
            "count": len(runs),
            "cleaned_sample": cleaned[:100],
        })

    return findings


def check_mixed_scripts(text: str) -> list[dict]:
    """Detect mixed-script text that might be hiding keywords.

    Example: using Cyrillic 'а' (U+0430) instead of Latin 'a' to sneak in keywords.
    """
    findings = []

    # Split into words and check each for mixed scripts
    words = re.findall(r"\S+", text)
    suspicious_words = []

    for word in words:
        if len(word) < 3:
            continue

        scripts = set()
        for char in word:
            if char.isalpha():
                try:
                    script = unicodedata.name(char, "").split()[0]
                    scripts.add(script)
                except ValueError:
                    pass

        # Flag words that mix Latin with Cyrillic, Greek, etc.
        if "LATIN" in scripts and len(scripts) > 1:
            non_latin = scripts - {"LATIN"}
            suspicious_words.append({
                "word": word,
                "scripts": sorted(scripts),
                "non_latin": sorted(non_latin),
            })

    if suspicious_words:
        findings.append({
            "type": "mixed_script",
            "severity": "high",
            "count": len(suspicious_words),
            "samples": suspicious_words[:5],
        })

    return findings


def clean_text(text: str) -> str:
    """Return a cleaned version of the text with hidden content removed."""
    cleaned = text

    # Remove HTML comments
    cleaned = re.sub(r"<!--.*?-->", "", cleaned, flags=re.DOTALL)

    # Remove invisible characters
    invisible_set = set(INVISIBLE_CHARS.keys())
    cleaned = "".join(c for c in cleaned if c not in invisible_set)

    # Strip excessive whitespace left behind
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)

    return cleaned.strip()


def scan_text(text: str) -> dict:
    """Run all security checks on the given text.

    Returns:
        dict with verdict, findings list, and cleaned_text.
    """
    all_findings = []
    all_findings.extend(check_invisible_chars(text))
    all_findings.extend(check_html_comments(text))
    all_findings.extend(check_prompt_injection(text))
    all_findings.extend(check_hidden_urls(text))
    all_findings.extend(check_invisible_between_visible(text))
    all_findings.extend(check_mixed_scripts(text))

    verdict = "flagged" if all_findings else "clean"
    cleaned = clean_text(text) if all_findings else text

    return {
        "verdict": verdict,
        "findings": all_findings,
        "cleaned_text": cleaned,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Security gate for job postings — detect hidden text, injection, and obfuscation"
    )

    # Input: either a job from the jobs file, or raw text
    parser.add_argument("--job-id", help="Job ID to check from jobs file")
    parser.add_argument("--jobs", help="Path to jobs JSON file")
    parser.add_argument("--text", help="Raw text to scan (alternative to --job-id/--jobs)")
    parser.add_argument("--output", help="Write JSON result to this path (optional)")
    args = parser.parse_args()

    # Resolve input text
    text = None
    job_meta = {}

    if args.text:
        text = args.text
    elif args.job_id and args.jobs:
        jobs_path = Path(args.jobs)
        if not jobs_path.exists():
            print(f"Error: Jobs file not found: {args.jobs}", file=sys.stderr)
            sys.exit(1)

        with open(jobs_path) as f:
            jobs_data = json.load(f)

        for job in jobs_data.get("jobs", []):
            if str(job.get("id")) == str(args.job_id):
                text = job.get("description", "")
                job_meta = {
                    "job_id": job.get("id"),
                    "title": job.get("title", ""),
                    "company": job.get("company_token", ""),
                }
                break

        if text is None:
            print(f"Error: Job ID {args.job_id} not found in {args.jobs}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.error("Provide either --text or both --job-id and --jobs")

    if not text:
        print(json.dumps({"verdict": "clean", "findings": [], "cleaned_text": ""}))
        return

    # Run scan
    result = scan_text(text)

    # Add job metadata if available
    if job_meta:
        result["job"] = job_meta

    # Display results
    if result["verdict"] == "clean":
        print(f"CLEAN: No security issues detected.")
    else:
        print(f"FLAGGED: {len(result['findings'])} issue(s) found:")
        for i, finding in enumerate(result["findings"], 1):
            severity = finding.get("severity", "unknown").upper()
            ftype = finding.get("type", "unknown")
            detail = ""
            if ftype == "invisible_char":
                detail = f"{finding['char_name']} x{finding['count']}"
            elif ftype == "html_comment":
                detail = finding["content"][:80]
            elif ftype == "prompt_injection":
                detail = f"line {finding['line']}: {finding['pattern']}"
            elif ftype == "hidden_url":
                detail = finding["url"][:80]
            elif ftype == "invisible_between_visible":
                detail = f"{finding['count']} invisible insertions, cleaned: {finding['cleaned_sample']}"
            elif ftype == "mixed_script":
                detail = f"{finding['count']} mixed-script words"
            print(f"  [{severity}] {ftype}: {detail}")

    # Output JSON
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nResult saved to {args.output}")
    else:
        # Always print the JSON to stdout for programmatic consumption
        print(f"\n{json.dumps(result, indent=2)}")


if __name__ == "__main__":
    main()
