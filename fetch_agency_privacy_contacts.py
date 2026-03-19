"""
Agency Privacy Office Contact Scraper
=======================================
Scrapes individual federal and NY state agency websites to find
Privacy Officer contact information: name, title, email, phone.

For federal agencies, searches known privacy office URL patterns:
  /privacy, /privacy-policy, /privacy-office, /about/privacy, etc.

Seeded from agencies.json (GSA digital-strategy agency list).

Output: agency_privacy_contacts.json
  [
    {
      "source": "agency_website",
      "name": "Jane Doe",
      "title": "Senior Agency Official for Privacy",
      "email": "privacy@agency.gov",
      "phone": "202-555-1234",
      "agency": "Department of Health and Human Services",
      "agency_id": "HHS",
      "agency_url": "https://www.hhs.gov",
      "privacy_page_url": "https://www.hhs.gov/privacy",
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
# Known agency privacy office pages (curated from FPC.gov + manual research)
# Supplements the generic scraper with confirmed working URLs.
# ---------------------------------------------------------------------------

KNOWN_AGENCY_PRIVACY_PAGES: list[dict] = [
    # Cabinet departments
    {"id": "USDA",  "name": "Dept. of Agriculture",             "url": "https://www.usda.gov/privacy-policy"},
    {"id": "DOC",   "name": "Dept. of Commerce",                "url": "https://www.commerce.gov/opog/privacy-office"},
    {"id": "DOD",   "name": "Dept. of Defense",                 "url": "https://privacy.defense.gov/"},
    {"id": "DOE",   "name": "Dept. of Education",               "url": "https://studentprivacy.ed.gov/"},
    {"id": "DOEn",  "name": "Dept. of Energy",                  "url": "https://www.energy.gov/privacy"},
    {"id": "HHS",   "name": "Dept. of Health & Human Services", "url": "https://www.hhs.gov/privacy/index.html"},
    {"id": "DHS",   "name": "Dept. of Homeland Security",       "url": "https://www.dhs.gov/privacy"},
    {"id": "HUD",   "name": "Dept. of Housing & Urban Dev.",    "url": "https://www.hud.gov/privacy_policy"},
    {"id": "DOI",   "name": "Dept. of the Interior",            "url": "https://www.doi.gov/privacy"},
    {"id": "DOJ",   "name": "Dept. of Justice",                 "url": "https://www.justice.gov/privacy-policy"},
    {"id": "DOL",   "name": "Dept. of Labor",                   "url": "https://www.dol.gov/general/privacynotice"},
    {"id": "DOS",   "name": "Dept. of State",                   "url": "https://www.state.gov/privacy-policy/"},
    {"id": "DOT",   "name": "Dept. of Transportation",          "url": "https://www.transportation.gov/privacy"},
    {"id": "TREAS", "name": "Dept. of the Treasury",            "url": "https://home.treasury.gov/footer/privacy-act"},
    {"id": "VA",    "name": "Dept. of Veterans Affairs",        "url": "https://www.va.gov/privacy/"},
    # Independent agencies
    {"id": "EPA",   "name": "Environmental Protection Agency",  "url": "https://www.epa.gov/privacy"},
    {"id": "FTC",   "name": "Federal Trade Commission",         "url": "https://www.ftc.gov/policy/privacy-policy"},
    {"id": "GSA",   "name": "General Services Administration",  "url": "https://www.gsa.gov/reference/gsa-privacy-program"},
    {"id": "NASA",  "name": "NASA",                             "url": "https://www.nasa.gov/privacy/"},
    {"id": "NRC",   "name": "Nuclear Regulatory Commission",    "url": "https://www.nrc.gov/about-nrc/privacy.html"},
    {"id": "NSF",   "name": "National Science Foundation",      "url": "https://www.nsf.gov/policies/privacy.jsp"},
    {"id": "OPM",   "name": "Office of Personnel Management",   "url": "https://www.opm.gov/privacy/"},
    {"id": "SBA",   "name": "Small Business Administration",    "url": "https://www.sba.gov/about-sba/sba-performance/open-government/privacy-policy"},
    {"id": "SSA",   "name": "Social Security Administration",   "url": "https://www.ssa.gov/privacy.html"},
    # NY State agencies (for cross-reference with SeeThroughNY)
    {"id": "NYS-ITS",  "name": "NY Office of Information Technology Services", "url": "https://its.ny.gov/privacy"},
    {"id": "NYS-DOH",  "name": "NY Dept. of Health",                            "url": "https://www.health.ny.gov/privacy/"},
    {"id": "NYS-DOCCS","name": "NY Dept. of Corrections",                       "url": "https://doccs.ny.gov/privacy-policy"},
    {"id": "NYS-DMV",  "name": "NY Dept. of Motor Vehicles",                    "url": "https://dmv.ny.gov/privacy-policy"},
    {"id": "NYC-DOITT","name": "NYC Office of Technology & Innovation",          "url": "https://www.nyc.gov/site/doitt/about/privacy-policy.page"},
]

# Candidate path suffixes to try when scraping an agency's base URL
PRIVACY_PATH_CANDIDATES = [
    "/privacy",
    "/privacy-office",
    "/privacy-policy",
    "/about/privacy",
    "/privacy/index.html",
    "/information-technology/privacy",
    "/resources/privacy",
    "/footer/privacy",
    "/general/privacy",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ---------------------------------------------------------------------------
# Regex patterns for extracting contact info from HTML text
# ---------------------------------------------------------------------------

EMAIL_RE = re.compile(
    r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,6}\b'
)

PHONE_RE = re.compile(
    r'\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4}'
)

# Patterns to identify the privacy officer name/title in text
NAME_CONTEXT_RE = re.compile(
    r'(?:privacy officer|saop|senior agency official|chief privacy officer|'
    r'privacy program manager|privacy analyst)[:\s,\-]+([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)',
    re.IGNORECASE,
)
TITLE_CONTEXT_RE = re.compile(
    r'(?:name|officer|contact|director)[:\s]+([A-Z][a-z]+(?:\s[A-Z][a-z]+)+)',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# HTML parser — extracts text + links from a page
# ---------------------------------------------------------------------------

class ContactPageParser(HTMLParser):
    """Extract plain text and mailto: links from a privacy contact page."""

    def __init__(self):
        super().__init__()
        self.text_chunks: list[str] = []
        self.mailto_links: list[str] = []
        self._skip_tags = {"script", "style", "head"}
        self._current_skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._skip_tags:
            self._current_skip += 1
        if tag == "a":
            for name, val in attrs:
                if name == "href" and val and val.lower().startswith("mailto:"):
                    email = val[7:].split("?")[0].strip()
                    if email:
                        self.mailto_links.append(email)

    def handle_endtag(self, tag):
        if tag in self._skip_tags:
            self._current_skip = max(0, self._current_skip - 1)

    def handle_data(self, data):
        if self._current_skip == 0:
            stripped = data.strip()
            if stripped:
                self.text_chunks.append(stripped)

    @property
    def full_text(self) -> str:
        return " ".join(self.text_chunks)


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _extract_emails(html: str, text: str, mailto_links: list[str]) -> list[str]:
    """Collect all .gov emails from mailto links + text."""
    emails: set[str] = set()

    for e in mailto_links:
        e = e.lower().strip()
        if e.endswith(".gov") or ".gov" in e:
            emails.add(e)

    for m in EMAIL_RE.finditer(text):
        e = m.group(0).lower()
        if ".gov" in e:
            emails.add(e)

    # Also check raw HTML for obfuscated emails (AT → @, DOT → .)
    deobfuscated = (
        html
        .replace(" [at] ", "@").replace("[at]", "@")
        .replace(" [dot] ", ".").replace("[dot]", ".")
        .replace(" AT ", "@").replace(" DOT ", ".")
    )
    for m in EMAIL_RE.finditer(deobfuscated):
        e = m.group(0).lower()
        if ".gov" in e:
            emails.add(e)

    return sorted(emails)


def _extract_phones(text: str) -> list[str]:
    return list({m.group(0) for m in PHONE_RE.finditer(text)})


def _extract_officer_name(text: str) -> str | None:
    """Try to extract a privacy officer name from surrounding context."""
    m = NAME_CONTEXT_RE.search(text)
    if m:
        return m.group(1).strip()
    return None


def _extract_officer_title(text: str) -> str | None:
    """Identify the specific privacy officer title mentioned on the page."""
    for title in (
        "Senior Agency Official for Privacy",
        "Chief Privacy Officer",
        "Agency Privacy Officer",
        "Privacy Program Manager",
        "Privacy Officer",
        "Privacy Analyst",
    ):
        if title.lower() in text.lower():
            return title
    return None


# ---------------------------------------------------------------------------
# HTTP fetch with fallback
# ---------------------------------------------------------------------------

def _fetch_html(url: str, timeout: int = 20) -> str | None:
    """Fetch URL; return HTML string or None on error."""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get("Content-Type", "")
            if "html" not in ct and "text" not in ct:
                return None
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"    fetch error ({url}): {e}")
        return None


# ---------------------------------------------------------------------------
# Per-agency scraper
# ---------------------------------------------------------------------------

def scrape_agency_privacy_contact(
    agency_id: str,
    agency_name: str,
    privacy_url: str,
    agency_base_url: str | None = None,
) -> dict | None:
    """
    Scrape a single agency privacy page and extract contact details.
    Falls back to trying candidate paths on the agency's base URL.

    Returns a contact dict or None if nothing useful found.
    """
    html = _fetch_html(privacy_url)

    # If primary URL failed and we have a base URL, try candidate paths
    if not html and agency_base_url:
        parsed = urllib.parse.urlparse(agency_base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        for path in PRIVACY_PATH_CANDIDATES:
            candidate = base + path
            if candidate == privacy_url:
                continue
            print(f"    Trying fallback: {candidate}")
            html = _fetch_html(candidate)
            if html:
                privacy_url = candidate
                break

    if not html:
        return None

    parser = ContactPageParser()
    parser.feed(html)
    text = parser.full_text

    emails = _extract_emails(html, text, parser.mailto_links)
    phones = _extract_phones(text)
    name = _extract_officer_name(text)
    title = _extract_officer_title(text)

    if not emails and not name:
        return None  # Nothing useful found

    return {
        "source": "agency_website",
        "name": name,
        "title": title,
        "email": emails[0] if emails else None,
        "all_emails": emails,
        "phone": phones[0] if phones else None,
        "agency": agency_name,
        "agency_id": agency_id,
        "agency_url": agency_base_url or privacy_url,
        "privacy_page_url": privacy_url,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_all_agency_privacy_contacts(
    extra_agencies: list[dict] | None = None,
    agencies_json_path: str = "agencies.json",
) -> list[dict]:
    """
    Scrape privacy contact info from all known agency privacy pages,
    supplemented by agencies from the local agencies.json.

    Args:
        extra_agencies:      Additional agency dicts to scrape.
        agencies_json_path:  Path to GSA agencies.json file.

    Returns:
        List of contact dicts (one per agency where info was found).
    """
    # Start with curated list
    to_scrape = list(KNOWN_AGENCY_PRIVACY_PAGES)

    # Add agencies from agencies.json that aren't already covered
    known_ids = {a["id"] for a in to_scrape}
    try:
        with open(agencies_json_path) as f:
            gsa_agencies = json.load(f)
        for a in gsa_agencies:
            aid = a.get("id", "")
            if aid not in known_ids and a.get("url"):
                to_scrape.append({
                    "id": aid,
                    "name": a.get("name", aid),
                    "url": None,  # Will try candidate paths
                    "_base_url": a.get("url"),
                })
    except FileNotFoundError:
        print(f"Warning: {agencies_json_path} not found; using curated list only.")

    if extra_agencies:
        to_scrape.extend(extra_agencies)

    contacts: list[dict] = []
    total = len(to_scrape)

    for i, agency in enumerate(to_scrape, 1):
        aid = agency.get("id", "?")
        aname = agency.get("name", aid)
        privacy_url = agency.get("url") or ""
        base_url = agency.get("_base_url") or agency.get("url") or ""

        print(f"[{i}/{total}] {aname} ({aid})")

        if not privacy_url and not base_url:
            print("  Skipping: no URL")
            continue

        # For agencies from GSA list without a known privacy URL, derive base
        if not privacy_url and base_url:
            parsed = urllib.parse.urlparse(base_url)
            privacy_url = f"{parsed.scheme}://{parsed.netloc}/privacy"

        contact = scrape_agency_privacy_contact(
            agency_id=aid,
            agency_name=aname,
            privacy_url=privacy_url,
            agency_base_url=base_url,
        )

        if contact:
            print(
                f"  Found: {contact.get('name') or '(no name)'}"
                f" | {contact.get('email') or '(no email)'}"
            )
            contacts.append(contact)
        else:
            print("  No privacy contact found")

        time.sleep(0.5)  # polite delay

    print(f"\nTotal agencies with privacy contact found: {len(contacts)}/{total}")
    return contacts


if __name__ == "__main__":
    contacts = fetch_all_agency_privacy_contacts()

    with open("agency_privacy_contacts.json", "w") as f:
        json.dump(contacts, f, indent=2)
    print(f"\nSaved agency_privacy_contacts.json ({len(contacts)} records)")

    print("\n--- Sample records ---")
    for c in contacts[:5]:
        print(
            f"  {c.get('agency_id', 'N/A'):10s}"
            f"  {c.get('name') or '(no name)':35s}"
            f"  {c.get('email') or '(no email)'}"
        )
