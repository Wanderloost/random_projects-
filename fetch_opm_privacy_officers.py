"""
OPM Privacy Officer Data Fetcher
=================================
Fetches federal employees with privacy-related job titles from two sources:

1. OPM FedScope / Federal Workforce Data (FWD) — aggregated occupation data
   - Source: https://www.fedscope.opm.gov / https://data.opm.gov
   - No individual names (suppressed for privacy), but gives agency-level counts
     and occupation codes for employees in privacy-related roles.

2. FederalPay.org — individual employee name + title + salary
   - Source: https://www.federalpay.org/employees
   - Powered by OPM EHRI dataset; names/titles/salaries are public under 5 U.S.C. § 552
   - Search: https://www.federalpay.org/employees?name=&title=privacy+officer

Output: opm_privacy_officers.json
  [
    {
      "source": "federalpay",
      "name": "Jane Doe",
      "title": "Privacy Officer",
      "agency": "Department of Health and Human Services",
      "salary": 125000,
      "year": 2023
    },
    ...
  ]
"""

import json
import re
import time
import urllib.request
import urllib.parse
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# FederalPay scraper
# ---------------------------------------------------------------------------

FEDERALPAY_SEARCH_URL = "https://www.federalpay.org/employees"
PRIVACY_TITLES = [
    "privacy officer",
    "chief privacy officer",
    "agency privacy officer",
    "senior agency official for privacy",
    "privacy analyst",
    "privacy program manager",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


class FederalPayTableParser(HTMLParser):
    """Parses the employee results table from FederalPay search results."""

    def __init__(self):
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.current_row = []
        self.current_cell = ""
        self.rows = []
        self.headers = []
        self._header_done = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "table":
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self.in_row = True
            self.current_row = []
        elif tag in ("td", "th") and self.in_row:
            self.in_cell = True
            self.current_cell = ""

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_table = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if self.current_row:
                if not self._header_done:
                    self.headers = self.current_row
                    self._header_done = True
                else:
                    self.rows.append(self.current_row)
        elif tag in ("td", "th") and self.in_cell:
            self.in_cell = False
            self.current_row.append(self.current_cell.strip())

    def handle_data(self, data):
        if self.in_cell:
            self.current_cell += data


def fetch_federalpay_employees(title_query: str, max_pages: int = 10) -> list[dict]:
    """
    Scrape FederalPay for employees matching a privacy-related title.

    Args:
        title_query: Job title search term (e.g., "privacy officer")
        max_pages: Maximum number of result pages to fetch

    Returns:
        List of employee dicts with keys: name, title, agency, salary, year
    """
    employees = []
    page = 1

    while page <= max_pages:
        params = urllib.parse.urlencode({
            "name": "",
            "title": title_query,
            "page": page,
        })
        url = f"{FEDERALPAY_SEARCH_URL}?{params}"
        print(f"  Fetching FederalPay page {page}: {url}")

        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as resp:
                html = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"  Warning: failed to fetch page {page}: {e}")
            break

        parser = FederalPayTableParser()
        parser.feed(html)

        if not parser.rows:
            print(f"  No more results on page {page}")
            break

        # Map column headers to indices
        headers_lower = [h.lower() for h in parser.headers]
        col_map = {}
        for col in ("name", "title", "agency", "salary", "year"):
            for i, h in enumerate(headers_lower):
                if col in h:
                    col_map[col] = i
                    break

        for row in parser.rows:
            record = {"source": "federalpay"}
            for col, idx in col_map.items():
                if idx < len(row):
                    val = row[idx].strip()
                    if col == "salary":
                        val = _parse_salary(val)
                    elif col == "year":
                        try:
                            val = int(val)
                        except ValueError:
                            val = None
                    record[col] = val
            employees.append(record)

        # Check for "next page" link in HTML
        if "next" not in html.lower() or f"page={page + 1}" not in html:
            break

        page += 1
        time.sleep(1)  # polite delay

    return employees


def _parse_salary(val: str) -> int | None:
    """Convert '$125,000' → 125000, or None if unparseable."""
    cleaned = re.sub(r"[^\d]", "", val)
    return int(cleaned) if cleaned else None


# ---------------------------------------------------------------------------
# OPM FedScope / FWD — occupation-level (no names, but agency counts)
# ---------------------------------------------------------------------------

# OPM FedScope raw employment dataset: pipe-delimited text files inside ZIP
# Each record: AGYSUB|LOC|AGELVL|EDLVL|GSEGRD|LOSLVL|OCC|OCCCAT|PATCO|
#              PPGRD|SALLVL|STEMOCC|SUPERVIS|TOA|WORKSCH|WORKSTAT|DATECODE|
#              EMPLOYMENT|SALARY
#
# The "OCC" field is a 4-digit occupation code.  Occupation 301 and 343 are the
# most common series for Privacy Officers, but titles are in a separate lookup.
#
# Raw data access (public): https://www.opm.gov/data/datasets/
# Direct ZIP (example, March 2024 employment status):
#   https://www.opm.gov/data/datasets/Files/758/6b1a51ba-e666-41f2-9224-e1284e4e054a.zip
#
# Because OPM data does not include individual names in its public bulk release,
# this function returns agency-level summaries keyed to privacy occupations.

OPM_OCCUPATION_TITLES = {
    # OCC code (as string) -> title description used in lookup tables
    # Privacy-adjacent series:
    "0301": "Miscellaneous Administration and Program",  # Most Privacy Officers
    "0343": "Management and Program Analysis",
    "2210": "Information Technology Management",        # Some IT privacy roles
    "0905": "General Attorney",                         # Privacy attorneys
    "0132": "Intelligence",
    "1811": "Criminal Investigation",
}

OPM_AGENCY_NAMES_URL = (
    "https://raw.githubusercontent.com/GSA/digital-strategy/1/agencies.json"
)


def fetch_opm_agency_lookup() -> dict[str, str]:
    """Load agency acronym → full name mapping from GSA digital-strategy."""
    with urllib.request.urlopen(OPM_AGENCY_NAMES_URL) as resp:
        data = json.loads(resp.read().decode())
    return {a["id"]: a["name"] for a in data.get("agencies", [])}


def note_opm_bulk_data():
    """
    Print guidance on accessing OPM bulk employment data.
    The bulk data doesn't include names but provides occupation-level counts
    by agency, useful for validating completeness of the privacy officer dataset.
    """
    print(
        "\nNOTE: OPM FedScope / Federal Workforce Data (FWD) provides employment"
        " statistics by occupation and agency, but does NOT include individual"
        " employee names in its public bulk data releases.\n"
        "  - FWD platform:     https://data.opm.gov\n"
        "  - Raw datasets:     https://www.opm.gov/data/datasets/\n"
        "  - Data definitions: https://www.fedscope.opm.gov/datadefn/\n"
        "\nPrivacy Officers are typically classified under:\n"
        "  - GS-0301 Miscellaneous Administration and Program\n"
        "  - GS-0343 Management and Program Analysis\n"
        "\nFor named employees, this script uses FederalPay.org (OPM EHRI data).\n"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_all_federal_privacy_officers() -> list[dict]:
    """
    Fetch all federal employees with privacy-related job titles from FederalPay.
    Returns a deduplicated list sorted by agency then name.
    """
    note_opm_bulk_data()

    all_employees: list[dict] = []

    for title in PRIVACY_TITLES:
        print(f"\nSearching FederalPay for title: '{title}'")
        results = fetch_federalpay_employees(title)
        print(f"  Found {len(results)} records")
        all_employees.extend(results)

    # Deduplicate by (name, agency, year)
    seen = set()
    deduped = []
    for emp in all_employees:
        key = (
            emp.get("name", "").lower(),
            emp.get("agency", "").lower(),
            emp.get("year"),
        )
        if key not in seen:
            seen.add(key)
            deduped.append(emp)

    # Sort by agency, then name
    deduped.sort(key=lambda e: (e.get("agency") or "", e.get("name") or ""))

    print(f"\nTotal unique federal privacy officers found: {len(deduped)}")
    return deduped


if __name__ == "__main__":
    employees = fetch_all_federal_privacy_officers()

    with open("opm_privacy_officers.json", "w") as f:
        json.dump(employees, f, indent=2)
    print(f"\nSaved opm_privacy_officers.json ({len(employees)} records)")

    # Preview
    print("\n--- Sample records ---")
    for emp in employees[:5]:
        print(
            f"  {emp.get('name', 'N/A'):40s}"
            f"  {emp.get('title', 'N/A'):35s}"
            f"  {emp.get('agency', 'N/A')}"
        )
