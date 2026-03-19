"""
Federal Agency Dataset Fetcher
===============================
Fetches open data about US federal agencies from two authoritative GSA sources:

1. GSA Digital Strategy agencies.json
   - Source: https://github.com/GSA/digital-strategy/blob/1/agencies.json
   - Fields: name, acronym (id), url
   - ~370+ agencies

2. GSA Federal Hierarchy Crosswalk (CSV)
   - Source: https://github.com/GSA/FederalHierarchy-Crosswalk
   - Fields: GSA SFP Name, OMB Name, Treasury Bureau Name, OMB Agency Code,
             OMB Bureau Code, Treasury Agency Code, Treasury Bureau Code,
             CGAC Agency Code, NIST Agency Code, Parent, and more (25 columns)
   - Comprehensive crosswalk across OMB, Treasury, CGAC, and NIST coding systems
"""

import json
import csv
import urllib.request

AGENCIES_JSON_URL = (
    "https://raw.githubusercontent.com/GSA/digital-strategy/1/agencies.json"
)
HIERARCHY_CSV_URL = (
    "https://raw.githubusercontent.com/GSA/FederalHierarchy-Crosswalk"
    "/master/federalhierarchycrosswalk.csv"
)


def fetch_agencies_json():
    """Fetch agency list with name, acronym, and URL from GSA digital-strategy."""
    print("Fetching agencies from GSA digital-strategy...")
    with urllib.request.urlopen(AGENCIES_JSON_URL) as response:
        data = json.loads(response.read().decode())
    agencies = data.get("agencies", [])
    print(f"  -> {len(agencies)} agencies loaded")
    return agencies


def fetch_hierarchy_csv():
    """Fetch federal hierarchy crosswalk CSV from GSA FederalHierarchy-Crosswalk."""
    print("Fetching federal hierarchy crosswalk CSV...")
    with urllib.request.urlopen(HIERARCHY_CSV_URL) as response:
        content = response.read().decode("latin-1")
    reader = csv.DictReader(content.splitlines())
    rows = list(reader)
    print(f"  -> {len(rows)} rows loaded, columns: {reader.fieldnames}")
    return rows


def save_json(data, filename):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved {filename}")


def save_csv(rows, filename):
    if not rows:
        return
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {filename}")


if __name__ == "__main__":
    # Dataset 1: agencies with acronyms
    agencies = fetch_agencies_json()
    save_json(agencies, "agencies.json")

    # Dataset 2: full hierarchy crosswalk
    hierarchy = fetch_hierarchy_csv()
    save_csv(hierarchy, "federal_hierarchy_crosswalk.csv")

    # Print a preview
    print("\n--- Sample agencies (name, acronym, url) ---")
    for agency in agencies[:10]:
        print(f"  {agency.get('id', 'N/A'):10s}  {agency.get('name')}")

    print("\n--- Crosswalk columns ---")
    if hierarchy:
        for col in hierarchy[0].keys():
            print(f"  {col}")
