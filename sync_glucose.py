"""Sync glucose readings from one LibreLinkUp patient to Supabase."""

import argparse
import os
from getpass import getpass
from urllib.parse import urlsplit

import requests

from pylibrelinkup import PyLibreLinkUp


def setting(name, prompt, interactive, *, secret=False):
    value = os.environ.get(name)
    if not value and interactive:
        value = getpass(prompt) if secret else input(prompt).strip()
    if not value:
        raise SystemExit(f"Set {name} or run with --prompt.")
    return value


def upsert(url, key, readings, *, update_existing):
    rows = {}
    for reading in readings:
        timestamp = reading.factory_timestamp.isoformat()
        row = {
            "recorded_at": timestamp,
            "glucose_mg_dl": round(reading.value_in_mg_per_dl),
        }
        if update_existing:
            row["trend_arrow"] = int(reading.trend)
        rows[timestamp] = row

    if not rows:
        return 0

    resolution = "merge-duplicates" if update_existing else "ignore-duplicates"
    response = requests.post(
        f"{url}/rest/v1/glucose_readings",
        params={"on_conflict": "recorded_at"},
        headers={"apikey": key, "Prefer": f"resolution={resolution},return=minimal"},
        json=list(rows.values()),
        timeout=30,
        allow_redirects=False,
    )
    if not 200 <= response.status_code < 300:
        raise SystemExit(f"Supabase rejected the batch (HTTP {response.status_code}).")
    return len(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", action="store_true", help="Prompt for missing settings")
    args = parser.parse_args()

    url = setting("SUPABASE_URL", "Supabase project URL: ", args.prompt).rstrip("/")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".supabase.co")
        or parsed.netloc != parsed.hostname
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit("Use the HTTPS project URL ending in .supabase.co.")
    key = setting("SUPABASE_SECRET_KEY", "Supabase secret key: ", args.prompt, secret=True)
    if not key.startswith("sb_secret_"):
        raise SystemExit("Use a Supabase secret key beginning with sb_secret_.")

    client = PyLibreLinkUp(
        email=setting("LIBRELINKUP_EMAIL", "LibreLinkUp email: ", args.prompt),
        password=setting(
            "LIBRELINKUP_PASSWORD", "LibreLinkUp password: ", args.prompt, secret=True
        ),
    )
    try:
        client.authenticate()
        patients = client.get_patients()
        patient_id = os.environ.get("LIBRELINKUP_PATIENT_ID")
        if patient_id:
            patients = [patient for patient in patients if str(patient.patient_id) == patient_id]
        if len(patients) != 1:
            raise SystemExit(
                "Expected one matching patient. Check LibreLinkUp sharing and "
                "set LIBRELINKUP_PATIENT_ID if the account has multiple patients."
            )

        patient = patients[0]
        latest = client.latest(patient_identifier=patient)
        graph = client.graph(patient_identifier=patient)
        logbook = client.logbook(patient_identifier=patient)
    except Exception as error:
        raise SystemExit(f"LibreLinkUp fetch failed ({type(error).__name__}).") from None

    try:
        # Preserve trend data when history includes a previously synced reading.
        upsert(url, key, [*logbook, *graph], update_existing=False)
        upsert(url, key, [latest], update_existing=True)
    except requests.RequestException as error:
        raise SystemExit(f"Supabase request failed ({type(error).__name__}).") from None

    print("Glucose sync completed.")


if __name__ == "__main__":
    main()
