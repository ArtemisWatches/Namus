#!/usr/bin/env python3
"""
NamUs DAILY UPDATER — run by GitHub Actions every day.

Reads data/index.json to find the highest case ID seen per case type, then
probes only the next UPDATE_WINDOW IDs to discover newly added cases.
New cases are merged into the appropriate existing decade JSON files.
index.json is updated with the new max_valid_id and file list.

Because NamUs adds at most a handful of cases per day, this script makes
~500 requests instead of the ~80 000 needed for a full scrape.

Usage:
    python update_namus.py

Prerequisite: run scrape_namus.py at least once to build the initial dataset.
"""

import json
import logging
import sys
from datetime import datetime, timezone

from namus_utils import (
    CASE_TYPES,
    DATA_DIR,
    INDEX_FILE,
    build_session,
    case_filename,
    decade_label,
    extract_year,
    fetch_id_range,
    load_index,
    save_index,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Number of IDs above the current max to probe each run.
# NamUs adds ~5 cases/day across all states; 500 is very safe.
UPDATE_WINDOW = 500

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
# Merge helpers
# ---------------------------------------------------------------------------

def load_decade_file(case_type: str, label: str) -> dict:
    """Load an existing decade file, or return an empty envelope."""
    path = DATA_DIR / case_filename(case_type, label)
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_type": case_type,
        "decade": label,
        "count": 0,
        "cases": [],
    }


def merge_new_cases(new_cases: list[dict], case_type: str) -> list[str]:
    """
    Add each new case to its decade file, skipping any IDs already present.
    Returns the list of filenames that were written.
    """
    # Group incoming cases by decade label
    by_decade: dict[str, list[dict]] = {}
    for case in new_cases:
        year = extract_year(case, case_type)
        label = decade_label(year) if year is not None else "unknown"
        by_decade.setdefault(label, []).append(case)

    written: list[str] = []
    generated_at = datetime.now(timezone.utc).isoformat()

    for label, cases in by_decade.items():
        payload = load_decade_file(case_type, label)
        existing_ids = {c["id"] for c in payload["cases"]}
        truly_new = [c for c in cases if c["id"] not in existing_ids]

        if not truly_new:
            continue

        payload["cases"].extend(truly_new)
        payload["count"] = len(payload["cases"])
        payload["generated_at"] = generated_at

        filename = case_filename(case_type, label)
        path = DATA_DIR / filename
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

        log.info("  +%d new cases → %s (total %d)", len(truly_new), filename, payload["count"])
        written.append(filename)

    return written

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    log.info("NamUs daily update started at %s", datetime.now(timezone.utc).isoformat())

    if not INDEX_FILE.exists():
        log.error(
            "data/index.json not found. Run scrape_namus.py first to build the initial dataset."
        )
        sys.exit(1)

    session = build_session()
    index = load_index()
    files_in_index: set[str] = set(index.get("files", []))
    any_new = False

    for case_type in CASE_TYPES:
        meta = index.get(case_type, {})
        max_valid_id = meta.get("max_valid_id", 0)

        if max_valid_id == 0:
            log.warning(
                "%s: no max_valid_id in index — skipping. Run scrape_namus.py first.",
                case_type,
            )
            continue

        probe_start = max_valid_id + 1
        probe_end = max_valid_id + UPDATE_WINDOW
        log.info("%s — probing IDs %d..%d", case_type, probe_start, probe_end)

        try:
            new_cases = fetch_id_range(
                session, case_type, range(probe_start, probe_end + 1), log_interval=0
            )
        except Exception:
            log.exception("%s — fetch failed; skipping", case_type)
            continue

        if not new_cases:
            log.info("%s — no new cases found", case_type)
            continue

        any_new = True
        log.info("%s — %d new case(s) found", case_type, len(new_cases))

        written = merge_new_cases(new_cases, case_type)
        files_in_index.update(written)

        new_max = max(c["id"] for c in new_cases)
        index[case_type] = {
            **meta,
            "max_valid_id": new_max,
            "last_update": datetime.now(timezone.utc).isoformat(),
            "cases_fetched": meta.get("cases_fetched", 0) + len(new_cases),
        }

        # Write index after each case type in case a later one fails.
        index["generated_at"] = datetime.now(timezone.utc).isoformat()
        index["files"] = sorted(files_in_index)
        save_index(index)

    if any_new:
        log.info("Update complete — new cases added.")
    else:
        log.info("Update complete — dataset is already up to date.")


if __name__ == "__main__":
    main()
