#!/usr/bin/env python3
"""
NamUs INITIAL SCRAPER — run once to build the full dataset.

Scans every integer case ID from 1 to an upper bound for both MissingPersons
and UnidentifiedPersons, groups results by decade, and writes JSON files to
./data/.  Saves max_valid_id and upper_bound to data/index.json so that
update_namus.py can run incremental daily updates without re-scanning
the full range.

Usage:
    python scrape_namus.py

Runtime: 15–30 minutes (80 000 concurrent HTTP requests, 20 workers).
Run this locally or as a one-off manual GitHub Actions trigger.
After the first run, use update_namus.py for daily updates.
"""

import json
import logging
import sys
from datetime import datetime, timezone

from namus_utils import (
    CASE_TYPES,
    DATA_DIR,
    build_session,
    case_filename,
    decade_label,
    extract_year,
    fetch_id_range,
    get_total_count,
    load_index,
    save_index,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Upper bounds derived from live probing (June 2026).
# Phase-2 diagnostics confirmed valid IDs at ~107 000 (MP) and ~120 000 (UP),
# so the full ID space is much wider and sparser than initially assumed.
DEFAULT_UPPER_BOUNDS = {
    "MissingPersons": 130_000,
    "UnidentifiedPersons": 130_000,
}

LOG_INTERVAL = 5_000

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-type processing
# ---------------------------------------------------------------------------

def group_by_decade(cases: list[dict], case_type: str) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {}
    for case in cases:
        year = extract_year(case, case_type)
        label = decade_label(year) if year is not None else "unknown"
        buckets.setdefault(label, []).append(case)
    return buckets


def save_buckets(buckets: dict[str, list[dict]], case_type: str) -> list[str]:
    """Write one JSON file per decade; return list of filenames written."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    written: list[str] = []

    for label, cases in sorted(buckets.items()):
        filename = case_filename(case_type, label)
        path = DATA_DIR / filename
        payload = {
            "generated_at": generated_at,
            "case_type": case_type,
            "decade": label,
            "count": len(cases),
            "cases": cases,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        log.info("  Wrote %4d cases → %s", len(cases), filename)
        written.append(filename)

    return written

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("NamUs full scrape started at %s", datetime.now(timezone.utc).isoformat())

    session = build_session()
    index = load_index()

    # Collect all filenames written across both case types so the index stays
    # consistent even if it already contained entries from a previous partial run.
    all_files: set[str] = set(index.get("files", []))

    for case_type in CASE_TYPES:
        upper_bound = index.get(case_type, {}).get(
            "upper_bound", DEFAULT_UPPER_BOUNDS[case_type]
        )
        api_count = get_total_count(session, case_type)
        log.info("%s — API total: %d | scanning IDs 1..%d", case_type, api_count, upper_bound)

        try:
            cases = fetch_id_range(session, case_type, range(1, upper_bound + 1), LOG_INTERVAL)
        except Exception:
            log.exception("%s — fetch failed; skipping this case type", case_type)
            continue

        log.info(
            "%s — found %d valid cases (%.0f%% hit rate)",
            case_type, len(cases), len(cases) / upper_bound * 100,
        )

        buckets = group_by_decade(cases, case_type)
        written = save_buckets(buckets, case_type)
        all_files.update(written)

        max_valid_id = max((c["id"] for c in cases), default=0)
        index[case_type] = {
            "max_valid_id": max_valid_id,
            "upper_bound": upper_bound,
            "last_full_scrape": datetime.now(timezone.utc).isoformat(),
            "cases_fetched": len(cases),
        }

        # Write index after each case type so a crash mid-run doesn't lose progress.
        index["generated_at"] = datetime.now(timezone.utc).isoformat()
        index["files"] = sorted(all_files)
        save_index(index)

    log.info("Full scrape complete. Data written to %s", DATA_DIR.resolve())


if __name__ == "__main__":
    main()
