#!/usr/bin/env python3
"""CSV-based application tracker — manages job application lifecycle.

Tracks applications through: drafted -> applied -> screening -> interview -> offer/rejected/withdrawn.
Provides duplicate checking, status filtering, and tabular display.
"""

import argparse
import csv
import sys
from datetime import date
from pathlib import Path

TRACKER_PATH = "output/tracker.csv"

COLUMNS = [
    "date",
    "company",
    "role",
    "platform",
    "location",
    "status",
    "fit_score",
    "cv_file",
    "cover_letter_file",
    "source_url",
    "notes",
]

VALID_STATUSES = [
    "drafted",
    "applied",
    "screening",
    "interview",
    "offer",
    "rejected",
    "no_response",
    "withdrawn",
]


def ensure_tracker(path: Path) -> None:
    """Create the tracker CSV with headers if it does not exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=COLUMNS)
            writer.writeheader()


def read_tracker(path: Path) -> list[dict]:
    """Read all rows from the tracker CSV."""
    if not path.exists():
        return []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def write_tracker(path: Path, rows: list[dict]) -> None:
    """Write all rows back to the tracker CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def find_entry(rows: list[dict], company: str, role: str) -> int | None:
    """Find index of an existing entry by company + role (case-insensitive)."""
    company_lower = company.lower().strip()
    role_lower = role.lower().strip()
    for i, row in enumerate(rows):
        if (row.get("company", "").lower().strip() == company_lower
                and row.get("role", "").lower().strip() == role_lower):
            return i
    return None


def add_entry(args, tracker_path: Path) -> None:
    """Add a new application entry to the tracker."""
    ensure_tracker(tracker_path)
    rows = read_tracker(tracker_path)

    company = args.company or ""
    role = args.role or ""

    if not company or not role:
        print("Error: --company and --role are required for --add", file=sys.stderr)
        sys.exit(1)

    # Duplicate check
    if find_entry(rows, company, role) is not None:
        print(f"DUPLICATE: {company} / {role} is already tracked.")
        print("Use --update to change its status, or --check to inspect.")
        return

    status = args.status or "drafted"
    if status not in VALID_STATUSES:
        print(f"Error: Invalid status '{status}'. Valid: {', '.join(VALID_STATUSES)}", file=sys.stderr)
        sys.exit(1)

    row = {
        "date": date.today().isoformat(),
        "company": company,
        "role": role,
        "platform": args.platform or "",
        "location": args.location or "",
        "status": status,
        "fit_score": args.score or "",
        "cv_file": args.cv or "",
        "cover_letter_file": args.cover_letter or "",
        "source_url": args.url or "",
        "notes": args.notes or "",
    }

    rows.append(row)
    write_tracker(tracker_path, rows)
    print(f"Added: {company} / {role} [{status}]")


def update_entry(args, tracker_path: Path) -> None:
    """Update the status (and optionally notes) of an existing entry."""
    ensure_tracker(tracker_path)
    rows = read_tracker(tracker_path)

    company = args.company or ""
    role = args.role or ""

    if not company or not role:
        print("Error: --company and --role are required for --update", file=sys.stderr)
        sys.exit(1)

    idx = find_entry(rows, company, role)
    if idx is None:
        print(f"NOT FOUND: No entry for {company} / {role}")
        print("Use --add to create a new entry.")
        return

    old_status = rows[idx].get("status", "")

    if args.status:
        if args.status not in VALID_STATUSES:
            print(f"Error: Invalid status '{args.status}'. Valid: {', '.join(VALID_STATUSES)}", file=sys.stderr)
            sys.exit(1)
        rows[idx]["status"] = args.status

    if args.notes:
        existing_notes = rows[idx].get("notes", "")
        if existing_notes:
            rows[idx]["notes"] = f"{existing_notes}; {args.notes}"
        else:
            rows[idx]["notes"] = args.notes

    # Update CV / cover letter paths if provided
    if args.cv:
        rows[idx]["cv_file"] = args.cv
    if args.cover_letter:
        rows[idx]["cover_letter_file"] = args.cover_letter

    write_tracker(tracker_path, rows)
    new_status = rows[idx].get("status", "")
    print(f"Updated: {company} / {role} [{old_status} -> {new_status}]")


def list_entries(args, tracker_path: Path) -> None:
    """Print all tracked applications as a formatted table."""
    rows = read_tracker(tracker_path)

    if not rows:
        print("No applications tracked yet. Use --add to start.")
        return

    # Filter by status if provided
    if args.status:
        rows = [r for r in rows if r.get("status", "").lower() == args.status.lower()]
        if not rows:
            print(f"No applications with status '{args.status}'.")
            return

    # Table header
    header = f"{'Date':<12} {'Company':<18} {'Role':<30} {'Status':<13} {'Score':<7} {'Platform':<12} {'Location'}"
    print(header)
    print("-" * len(header))

    for row in rows:
        print(
            f"{row.get('date', ''):<12} "
            f"{row.get('company', '')[:16]:<18} "
            f"{row.get('role', '')[:28]:<30} "
            f"{row.get('status', ''):<13} "
            f"{row.get('fit_score', ''):<7} "
            f"{row.get('platform', '')[:10]:<12} "
            f"{row.get('location', '')[:25]}"
        )

    print(f"\nTotal: {len(rows)} application(s)")


def check_entry(args, tracker_path: Path) -> None:
    """Check if a specific company/role is already tracked."""
    rows = read_tracker(tracker_path)

    company = args.company or ""
    role = args.role or ""

    if not company or not role:
        print("Error: --company and --role are required for --check", file=sys.stderr)
        sys.exit(1)

    idx = find_entry(rows, company, role)
    if idx is not None:
        row = rows[idx]
        print(f"FOUND: {company} / {role}")
        print(f"  Date:     {row.get('date', '')}")
        print(f"  Status:   {row.get('status', '')}")
        print(f"  Score:    {row.get('fit_score', '')}")
        print(f"  Platform: {row.get('platform', '')}")
        print(f"  CV:       {row.get('cv_file', '')}")
        print(f"  Notes:    {row.get('notes', '')}")
    else:
        print(f"NOT FOUND: {company} / {role} is not tracked.")


def main():
    parser = argparse.ArgumentParser(
        description="CSV-based job application tracker"
    )

    # Commands (mutually exclusive)
    cmd_group = parser.add_mutually_exclusive_group(required=True)
    cmd_group.add_argument("--add", action="store_true", help="Add a new application entry")
    cmd_group.add_argument("--update", action="store_true", help="Update status of existing entry")
    cmd_group.add_argument("--list", action="store_true", help="List all tracked applications")
    cmd_group.add_argument("--check", action="store_true", help="Check if company/role is already tracked")

    # Entry fields
    parser.add_argument("--company", help="Company name")
    parser.add_argument("--role", help="Job title / role")
    parser.add_argument("--platform", help="ATS platform (greenhouse, lever, ashby, etc.)")
    parser.add_argument("--location", help="Job location")
    parser.add_argument("--status", help=f"Application status: {', '.join(VALID_STATUSES)}")
    parser.add_argument("--score", help="Fit score from scoring step")
    parser.add_argument("--cv", help="Path to tailored CV/resume PDF")
    parser.add_argument("--cover-letter", help="Path to cover letter PDF")
    parser.add_argument("--url", help="Job posting URL")
    parser.add_argument("--notes", help="Additional notes")
    parser.add_argument("--tracker", default=TRACKER_PATH, help="Path to tracker CSV")

    args = parser.parse_args()
    tracker_path = Path(args.tracker)

    if args.add:
        add_entry(args, tracker_path)
    elif args.update:
        update_entry(args, tracker_path)
    elif args.list:
        list_entries(args, tracker_path)
    elif args.check:
        check_entry(args, tracker_path)


if __name__ == "__main__":
    main()
