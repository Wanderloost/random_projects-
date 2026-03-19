"""
Privacy Officer Dataset Builder
=================================
Orchestrates all three data sources and merges them into a single,
deduplicated dataset of agency privacy officers.

Sources:
  1. OPM / FederalPay  → federal employees with privacy-related titles
  2. SeeThroughNY      → NY state/local government privacy officers
  3. Agency websites   → email addresses and confirmed officer names/titles

Merge strategy:
  - Records from sources 1 and 2 provide name + title + salary + agency.
  - Records from source 3 provide email + phone + confirmed title.
  - A fuzzy agency-name match joins source-3 email contacts onto
    source-1/2 employee records.

Output files:
  - opm_privacy_officers.json          (federal employees)
  - seethroughny_privacy_officers.json (NY state/local employees)
  - agency_privacy_contacts.json       (agency page email contacts)
  - privacy_officers_merged.json       (combined, deduplicated dataset)
  - privacy_officers_merged.csv        (same data as CSV)

Usage:
  python build_privacy_officer_dataset.py [--skip-opm] [--skip-sny]
                                          [--skip-agency] [--merge-only]

  --skip-opm     Skip fetching from FederalPay / OPM (use cached file)
  --skip-sny     Skip fetching from SeeThroughNY    (use cached file)
  --skip-agency  Skip scraping agency websites       (use cached file)
  --merge-only   Only run the merge step (all three cached files required)
"""

import argparse
import csv
import json
import os
import re
import sys
from typing import Optional


# ---------------------------------------------------------------------------
# Fuzzy agency-name matching helpers
# ---------------------------------------------------------------------------

def _normalize_agency_name(name: str) -> str:
    """Lowercase, remove punctuation and common stop-words for fuzzy matching."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    # Remove common fillers
    for filler in (
        "department of", "dept of", "office of", "bureau of",
        "administration", "agency", "commission", "authority",
        "united states", "u s ", "u.s.", "federal", "national",
        "new york", "ny ", " ny",
    ):
        name = name.replace(filler, " ")
    return re.sub(r"\s+", " ", name).strip()


def _agency_match_score(a: str, b: str) -> float:
    """
    Simple Jaccard similarity on word-tokens between two normalized
    agency names.  Returns 0.0–1.0.
    """
    ta = set(_normalize_agency_name(a).split())
    tb = set(_normalize_agency_name(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _best_agency_match(
    agency_name: str,
    contact_list: list[dict],
    threshold: float = 0.35,
) -> Optional[dict]:
    """
    Find the best-matching agency contact from contact_list for agency_name.
    Returns the contact dict or None if no match exceeds threshold.
    """
    best_score = 0.0
    best_contact = None
    for contact in contact_list:
        score = _agency_match_score(agency_name, contact.get("agency", ""))
        if score > best_score:
            best_score = score
            best_contact = contact
    if best_score >= threshold:
        return best_contact
    return None


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def merge_datasets(
    federal: list[dict],
    state: list[dict],
    agency_contacts: list[dict],
) -> list[dict]:
    """
    Merge the three source datasets into a unified schema.

    Unified schema per record:
      source          - "federalpay" | "seethroughny" | "agency_website"
      name            - Full name (if known)
      title           - Civil service / job title
      email           - Best email found (may be functional, not personal)
      phone           - Contact phone
      salary          - Annual salary in dollars (int or None)
      year            - Data year (int or None)
      agency          - Agency full name
      agency_id       - Agency acronym/code
      agency_url      - Agency homepage URL
      privacy_page_url- Agency privacy office page URL
      sub_agency      - Sub-agency / division (if available)
      state           - "Federal" | "NY" | etc.
      notes           - Any extra context
    """
    merged: list[dict] = []

    # ---- Federal employees (FederalPay / OPM) ----
    for emp in federal:
        agency = emp.get("agency", "")
        contact = _best_agency_match(agency, agency_contacts)
        merged.append({
            "source": emp.get("source", "federalpay"),
            "name": emp.get("name"),
            "title": emp.get("title"),
            "email": contact.get("email") if contact else None,
            "phone": contact.get("phone") if contact else None,
            "salary": emp.get("salary"),
            "year": emp.get("year"),
            "agency": agency,
            "agency_id": contact.get("agency_id") if contact else None,
            "agency_url": contact.get("agency_url") if contact else None,
            "privacy_page_url": contact.get("privacy_page_url") if contact else None,
            "sub_agency": emp.get("sub_agency"),
            "state": "Federal",
            "notes": None,
        })

    # ---- NY state/local employees (SeeThroughNY) ----
    for emp in state:
        agency = emp.get("agency", "")
        # For SeeThroughNY records, try to match with known NY agency contacts
        contact = _best_agency_match(agency, [
            c for c in agency_contacts if c.get("agency_id", "").startswith("NYS")
            or c.get("agency_id", "").startswith("NYC")
        ])
        merged.append({
            "source": emp.get("source", "seethroughny"),
            "name": emp.get("name"),
            "title": emp.get("title"),
            "email": contact.get("email") if contact else None,
            "phone": contact.get("phone") if contact else None,
            "salary": emp.get("salary"),
            "year": emp.get("year"),
            "agency": agency,
            "agency_id": contact.get("agency_id") if contact else None,
            "agency_url": contact.get("agency_url") if contact else None,
            "privacy_page_url": contact.get("privacy_page_url") if contact else None,
            "sub_agency": emp.get("sub_agency"),
            "state": emp.get("state", "NY"),
            "notes": None,
        })

    # ---- Agency website contacts without a matching employee record ----
    # Add as standalone records so no confirmed contacts are lost
    matched_agency_ids = set()
    for r in merged:
        if r.get("agency_id"):
            matched_agency_ids.add(r["agency_id"])

    for contact in agency_contacts:
        if contact.get("agency_id") in matched_agency_ids:
            continue  # Already represented via employee records
        if not contact.get("name") and not contact.get("email"):
            continue  # Nothing useful

        merged.append({
            "source": "agency_website",
            "name": contact.get("name"),
            "title": contact.get("title"),
            "email": contact.get("email"),
            "phone": contact.get("phone"),
            "salary": None,
            "year": None,
            "agency": contact.get("agency"),
            "agency_id": contact.get("agency_id"),
            "agency_url": contact.get("agency_url"),
            "privacy_page_url": contact.get("privacy_page_url"),
            "sub_agency": None,
            "state": (
                "NY" if (contact.get("agency_id") or "").startswith("NYS")
                    or (contact.get("agency_id") or "").startswith("NYC")
                else "Federal"
            ),
            "notes": "From agency privacy page only; no matching payroll record",
        })

    # ---- Final deduplication by (name.lower, agency.lower, year) ----
    seen: set[tuple] = set()
    deduped: list[dict] = []
    for r in merged:
        key = (
            (r.get("name") or "").lower().strip(),
            (r.get("agency") or "").lower().strip(),
            r.get("year"),
        )
        # Allow empty-name records through (they're agency-level contacts)
        if key[0] and key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    deduped.sort(key=lambda r: (
        r.get("state") or "",
        r.get("agency") or "",
        r.get("name") or "",
    ))

    return deduped


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _load_json(path: str) -> list[dict]:
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    print(f"  Warning: {path} not found; using empty list")
    return []


def _save_json(data: list[dict], path: str):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {path} ({len(data)} records)")


def _save_csv(data: list[dict], path: str):
    if not data:
        return
    # Collect all keys across all records (some may be missing in some records)
    fieldnames = list(dict.fromkeys(
        k for record in data for k in record.keys()
    ))
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in data:
            writer.writerow({k: record.get(k, "") for k in fieldnames})
    print(f"Saved {path} ({len(data)} records)")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Build the agency privacy officer dataset from multiple sources."
    )
    parser.add_argument("--skip-opm",    action="store_true",
                        help="Skip OPM/FederalPay fetch (use cached opm_privacy_officers.json)")
    parser.add_argument("--skip-sny",    action="store_true",
                        help="Skip SeeThroughNY scrape (use cached seethroughny_privacy_officers.json)")
    parser.add_argument("--skip-agency", action="store_true",
                        help="Skip agency website scrape (use cached agency_privacy_contacts.json)")
    parser.add_argument("--merge-only",  action="store_true",
                        help="Only run merge; requires all three cached JSON files")
    args = parser.parse_args()

    if args.merge_only:
        args.skip_opm = args.skip_sny = args.skip_agency = True

    # Step 1 — Federal employees (OPM / FederalPay)
    if not args.skip_opm:
        print("\n=== Step 1: Fetching federal privacy officers (OPM / FederalPay) ===")
        from fetch_opm_privacy_officers import fetch_all_federal_privacy_officers
        federal = fetch_all_federal_privacy_officers()
        _save_json(federal, "opm_privacy_officers.json")
    else:
        print("\n=== Step 1: Loading cached OPM data ===")
        federal = _load_json("opm_privacy_officers.json")
        print(f"  Loaded {len(federal)} federal employee records")

    # Step 2 — NY state/local employees (SeeThroughNY)
    if not args.skip_sny:
        print("\n=== Step 2: Fetching NY privacy officers (SeeThroughNY) ===")
        from fetch_seethroughny import fetch_all_seethroughny_privacy_officers
        state = fetch_all_seethroughny_privacy_officers()
        _save_json(state, "seethroughny_privacy_officers.json")
    else:
        print("\n=== Step 2: Loading cached SeeThroughNY data ===")
        state = _load_json("seethroughny_privacy_officers.json")
        print(f"  Loaded {len(state)} NY employee records")

    # Step 3 — Agency website email/contact scraping
    if not args.skip_agency:
        print("\n=== Step 3: Scraping agency privacy office pages ===")
        from fetch_agency_privacy_contacts import fetch_all_agency_privacy_contacts
        agency_contacts = fetch_all_agency_privacy_contacts()
        _save_json(agency_contacts, "agency_privacy_contacts.json")
    else:
        print("\n=== Step 3: Loading cached agency contacts ===")
        agency_contacts = _load_json("agency_privacy_contacts.json")
        print(f"  Loaded {len(agency_contacts)} agency contact records")

    # Step 4 — Merge
    print("\n=== Step 4: Merging all sources ===")
    merged = merge_datasets(federal, state, agency_contacts)
    print(f"Total merged records: {len(merged)}")

    _save_json(merged, "privacy_officers_merged.json")
    _save_csv(merged, "privacy_officers_merged.csv")

    # Summary statistics
    print("\n=== Summary ===")
    by_state = {}
    for r in merged:
        s = r.get("state") or "Unknown"
        by_state[s] = by_state.get(s, 0) + 1
    for s, count in sorted(by_state.items()):
        print(f"  {s:15s}: {count} records")

    with_email  = sum(1 for r in merged if r.get("email"))
    with_name   = sum(1 for r in merged if r.get("name"))
    print(f"\n  Records with name:  {with_name}/{len(merged)}")
    print(f"  Records with email: {with_email}/{len(merged)}")


if __name__ == "__main__":
    main()
