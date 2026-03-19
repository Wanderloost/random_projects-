"""
SeeThroughNY Privacy Officer Scraper
=====================================
Scrapes New York State and local government employee payroll data from
SeeThroughNY (seethroughny.net), published by the Empire Center for Public Policy.

Data coverage:
  - NY State agencies (2008–present)
  - NY City agencies (2008–2022)
  - Public authorities, school districts, counties, cities, towns, villages

Searches for employees with privacy-related job titles:
  "privacy officer", "chief privacy officer", "privacy analyst", etc.

Source: https://www.seethroughny.net/payrolls

Output: seethroughny_privacy_officers.json
  [
    {
      "source": "seethroughny",
      "name": "Jane Doe",
      "title": "Privacy Officer",
      "agency": "Office of Information Technology Services",
      "employer_type": "State",
      "salary": 115000,
      "year": 2023,
      "state": "NY"
    },
    ...
  ]

Note: SeeThroughNY does not expose a public API. This scraper uses the
      site's HTML search interface. Run from a machine with open internet
      access (not behind a restrictive proxy).
"""

import json
import re
import time
import urllib.request
import urllib.parse
from html.parser import HTMLParser


BASE_URL = "https://www.seethroughny.net"
SEARCH_URL = f"{BASE_URL}/payrolls"

PRIVACY_TITLE_QUERIES = [
    "privacy officer",
    "chief privacy officer",
    "privacy analyst",
    "privacy program manager",
    "privacy coordinator",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.seethroughny.net/payrolls",
}


# ---------------------------------------------------------------------------
# HTML parser for SeeThroughNY results table
# ---------------------------------------------------------------------------

class SeeThroughNYParser(HTMLParser):
    """
    Parses employee records from the SeeThroughNY payroll search results table.

    SeeThroughNY renders results as an HTML table with columns:
      Name | Employer | Title | Rate | Paid | Year | Sub Agency
    """

    def __init__(self):
        super().__init__()
        self.records = []
        self.headers = []
        self._header_captured = False
        self._in_table = False
        self._in_thead = False
        self._in_tbody = False
        self._in_tr = False
        self._in_cell = False
        self._cell_text = ""
        self._current_row = []
        self._table_class_found = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")

        # SeeThroughNY wraps results in a div/table with class "table" or similar
        if tag == "table":
            self._in_table = True
            self._table_class_found = True
        elif tag == "thead" and self._in_table:
            self._in_thead = True
        elif tag == "tbody" and self._in_table:
            self._in_tbody = True
        elif tag == "tr" and self._in_table:
            self._in_tr = True
            self._current_row = []
        elif tag in ("td", "th") and self._in_tr:
            self._in_cell = True
            self._cell_text = ""

    def handle_endtag(self, tag):
        if tag == "table":
            self._in_table = False
            self._in_thead = False
            self._in_tbody = False
        elif tag == "thead":
            self._in_thead = False
        elif tag == "tbody":
            self._in_tbody = False
        elif tag == "tr" and self._in_tr:
            self._in_tr = False
            if self._current_row:
                if not self._header_captured and self._in_thead:
                    self.headers = [h.strip() for h in self._current_row]
                    self._header_captured = True
                elif self._header_captured:
                    self.records.append(list(self._current_row))
        elif tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            self._current_row.append(self._cell_text.strip())

    def handle_data(self, data):
        if self._in_cell:
            self._cell_text += data


def _col_index(headers: list[str], *candidates: str) -> int | None:
    """Return index of first header matching any candidate substring (case-insensitive)."""
    for candidate in candidates:
        for i, h in enumerate(headers):
            if candidate.lower() in h.lower():
                return i
    return None


def _parse_salary(val: str) -> int | None:
    """Convert '$115,000.00' or '115000' → 115000."""
    cleaned = re.sub(r"[^\d]", "", val.split(".")[0])
    return int(cleaned) if cleaned else None


def _parse_next_page_url(html: str, current_page: int) -> str | None:
    """
    Extract the URL for the next results page from SeeThroughNY HTML.
    The site uses query-string pagination: ?page=2, ?page=3, etc., or
    uses a "next" link within the pagination nav.
    """
    # Look for rel="next" link
    m = re.search(r'<a[^>]+rel=["\']next["\'][^>]*href=["\']([^"\']+)["\']', html, re.I)
    if m:
        href = m.group(1)
        if href.startswith("http"):
            return href
        return f"{BASE_URL}{href}"

    # Look for page={next} in any pagination link
    next_page = current_page + 1
    m = re.search(
        rf'href=["\']([^"\']*[?&]page={next_page}[^"\']*)["\']', html, re.I
    )
    if m:
        href = m.group(1)
        if href.startswith("http"):
            return href
        return f"{BASE_URL}{href}"

    return None


# ---------------------------------------------------------------------------
# Fetch logic
# ---------------------------------------------------------------------------

def _fetch_url(url: str) -> str:
    """Fetch URL with browser-like headers; return decoded HTML string."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_seethroughny_by_title(title_query: str, max_pages: int = 20) -> list[dict]:
    """
    Search SeeThroughNY for employees matching a privacy-related title.

    The search form at /payrolls accepts a title parameter:
      ?n=<name>&t=<title>&e=<employer>&y=<year>
    where 't' is the title/position filter.

    Args:
        title_query: Position/title to search for (e.g., "privacy officer")
        max_pages:   Max result pages to fetch

    Returns:
        List of employee record dicts
    """
    params = urllib.parse.urlencode({"t": title_query})
    start_url = f"{SEARCH_URL}?{params}"

    employees = []
    url = start_url
    page = 1

    while url and page <= max_pages:
        print(f"  SeeThroughNY page {page}: {url}")
        try:
            html = _fetch_url(url)
        except Exception as e:
            print(f"  Warning: failed to fetch page {page}: {e}")
            break

        parser = SeeThroughNYParser()
        parser.feed(html)

        if not parser.records:
            # Try alternate parsing: look for JSON embedded in the page
            json_records = _try_extract_json(html)
            if json_records:
                employees.extend(json_records)
            else:
                print(f"  No records on page {page}; stopping.")
            break

        # Map columns
        headers = parser.headers
        i_name      = _col_index(headers, "name")
        i_employer  = _col_index(headers, "employer", "agency")
        i_title     = _col_index(headers, "title", "position")
        i_rate      = _col_index(headers, "rate", "base")
        i_paid      = _col_index(headers, "paid", "total")
        i_year      = _col_index(headers, "year")
        i_subagency = _col_index(headers, "sub agency", "subagency", "department")

        for row in parser.records:
            def cell(idx):
                return row[idx].strip() if idx is not None and idx < len(row) else ""

            salary_raw = cell(i_paid) or cell(i_rate)
            record = {
                "source": "seethroughny",
                "name": cell(i_name),
                "title": cell(i_title),
                "agency": cell(i_employer),
                "sub_agency": cell(i_subagency),
                "salary": _parse_salary(salary_raw),
                "year": _try_int(cell(i_year)),
                "state": "NY",
            }
            employees.append(record)

        # Check for next page
        next_url = _parse_next_page_url(html, page)
        url = next_url
        page += 1

        if next_url:
            time.sleep(1)  # polite delay between pages

    return employees


def _try_extract_json(html: str) -> list[dict]:
    """
    Attempt to extract JSON data embedded in the page source.
    Some modern sites inject data as window.__INITIAL_STATE__ or similar.
    """
    patterns = [
        r'window\.__INITIAL_STATE__\s*=\s*({.*?});\s*</script>',
        r'window\.data\s*=\s*(\[.*?\]);\s*</script>',
        r'var\s+payrollData\s*=\s*(\[.*?\]);',
    ]
    for pattern in patterns:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(1))
                if isinstance(data, list):
                    # Normalize to our schema
                    out = []
                    for item in data:
                        out.append({
                            "source": "seethroughny",
                            "name": item.get("name", item.get("Name", "")),
                            "title": item.get("title", item.get("Title", "")),
                            "agency": item.get("employer", item.get("Employer", "")),
                            "sub_agency": item.get("subAgency", ""),
                            "salary": _parse_salary(
                                str(item.get("paid", item.get("Paid", "")))
                            ),
                            "year": _try_int(
                                str(item.get("year", item.get("Year", "")))
                            ),
                            "state": "NY",
                        })
                    return out
            except (json.JSONDecodeError, KeyError):
                pass
    return []


def _try_int(val: str) -> int | None:
    try:
        return int(val.strip())
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_all_seethroughny_privacy_officers() -> list[dict]:
    """
    Fetch all NY state/local government employees with privacy-related titles
    from SeeThroughNY.
    Returns a deduplicated list sorted by agency then name.
    """
    all_employees: list[dict] = []

    for title in PRIVACY_TITLE_QUERIES:
        print(f"\nSearching SeeThroughNY for title: '{title}'")
        results = fetch_seethroughny_by_title(title)
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

    deduped.sort(key=lambda e: (e.get("agency") or "", e.get("name") or ""))
    print(f"\nTotal unique NY privacy officers found: {len(deduped)}")
    return deduped


if __name__ == "__main__":
    employees = fetch_all_seethroughny_privacy_officers()

    with open("seethroughny_privacy_officers.json", "w") as f:
        json.dump(employees, f, indent=2)
    print(f"\nSaved seethroughny_privacy_officers.json ({len(employees)} records)")

    print("\n--- Sample records ---")
    for emp in employees[:5]:
        print(
            f"  {emp.get('name', 'N/A'):40s}"
            f"  {emp.get('title', 'N/A'):35s}"
            f"  {emp.get('agency', 'N/A')}"
        )
