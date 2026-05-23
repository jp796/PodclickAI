"""
Bulk-create custom fields in GoHighLevel for the Lead Scoring system.

Setup:
  1. pip install requests
  2. Set GHL_TOKEN and GHL_LOCATION_ID below (or use env vars)
  3. python create_ghl_fields.py

Safe to re-run: it checks for existing fields by name before creating.
"""

import os
import sys
import json
import requests
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CONFIG — fill these in
# ============================================================
GHL_TOKEN = os.getenv("GHL_TOKEN", "pit-YOUR_TOKEN_HERE")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID", "YOUR_LOCATION_ID_HERE")

BASE_URL = "https://services.leadconnectorhq.com"
API_VERSION = "2021-07-28"

HEADERS = {
    "Authorization": f"Bearer {GHL_TOKEN}",
    "Version": API_VERSION,
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# ============================================================
# FIELDS TO CREATE
# ============================================================
# dataType options: TEXT (single line), LARGE_TEXT (multi line),
# SINGLE_OPTIONS (dropdown), DATE, NUMERICAL, CHECKBOX, etc.

FIELDS = [
    {
        "name": "Lead Tier",
        "dataType": "SINGLE_OPTIONS",
        "placeholder": "A / B / C / D",
        "options": ["A", "B", "C", "D"],
    },
    {
        "name": "Motivation Signal",
        "dataType": "TEXT",
        "placeholder": "e.g. behind on payments, tired landlord",
    },
    {
        "name": "Suggested Action",
        "dataType": "TEXT",
        "placeholder": "e.g. call back Tuesday AM",
    },
    {
        "name": "Why Now",
        "dataType": "LARGE_TEXT",
        "placeholder": "One-line pre-call brief",
    },
    {
        "name": "Last Scored Date",
        "dataType": "DATE",
        "placeholder": "",
    },
    {
        "name": "Scoring Version",
        "dataType": "TEXT",
        "placeholder": "e.g. v1",
    },
]

# ============================================================
# FUNCTIONS
# ============================================================

def get_existing_fields():
    """Fetch existing custom fields to avoid duplicates."""
    url = f"{BASE_URL}/locations/{GHL_LOCATION_ID}/customFields"
    r = requests.get(url, headers=HEADERS)
    if r.status_code != 200:
        print(f"❌ Failed to fetch existing fields: {r.status_code}")
        print(r.text)
        sys.exit(1)
    return r.json().get("customFields", [])


def create_field(field_def):
    """Create one custom field."""
    url = f"{BASE_URL}/locations/{GHL_LOCATION_ID}/customFields"
    payload = {
        "name": field_def["name"],
        "dataType": field_def["dataType"],
        "model": "contact",
        "placeholder": field_def.get("placeholder", ""),
    }
    if "options" in field_def:
        payload["options"] = field_def["options"]

    r = requests.post(url, headers=HEADERS, json=payload)
    if r.status_code in (200, 201):
        data = r.json().get("customField", {})
        return data
    else:
        print(f"   ❌ Error {r.status_code}: {r.text}")
        return None


def main():
    if GHL_TOKEN.startswith("pit-YOUR") or GHL_LOCATION_ID.startswith("YOUR"):
        print("❌ Set GHL_TOKEN and GHL_LOCATION_ID before running.")
        sys.exit(1)

    print(f"🔍 Checking existing custom fields in location {GHL_LOCATION_ID}...")
    existing = get_existing_fields()
    existing_names = {f.get("name", "").lower() for f in existing}
    print(f"   Found {len(existing)} existing fields.\n")

    created = []
    skipped = []

    for field in FIELDS:
        name = field["name"]
        if name.lower() in existing_names:
            # Find the existing field's ID for the summary
            match = next((f for f in existing if f.get("name", "").lower() == name.lower()), {})
            skipped.append({"name": name, "id": match.get("id", "?")})
            print(f"⏭️  Skipping '{name}' — already exists (id: {match.get('id', '?')})")
            continue

        print(f"➕ Creating '{name}'...")
        result = create_field(field)
        if result:
            created.append({"name": name, "id": result.get("id")})
            print(f"   ✅ Created (id: {result.get('id')})")

    # ============================================================
    # SUMMARY — copy these IDs into your Make scenario
    # ============================================================
    print("\n" + "=" * 60)
    print("FIELD IDs FOR YOUR MAKE SCENARIO:")
    print("=" * 60)
    all_fields = created + skipped
    for f in all_fields:
        print(f"  {f['name']:<22} → {f['id']}")

    # Also save to a JSON file for easy reference
    with open("ghl_field_ids.json", "w") as f:
        json.dump(all_fields, f, indent=2)
    print(f"\n💾 Saved to ghl_field_ids.json")
    print(f"\n📊 {len(created)} created, {len(skipped)} skipped (already existed)")


if __name__ == "__main__":
    main()
