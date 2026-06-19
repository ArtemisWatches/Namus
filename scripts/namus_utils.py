"""
Shared utilities for the NamUs scraper scripts.

Exported:
  BASE_URL, DATA_DIR, INDEX_FILE, MAX_WORKERS, CASE_TYPES
  build_session()
  get_case(session, case_type, case_id)   -> (id, data | None)
  get_total_count(session, case_type)     -> int
  fetch_id_range(session, case_type, id_range) -> list[dict]
  extract_year(case, case_type)           -> int | None
  decade_label(year)                      -> str
  case_filename(case_type, label)         -> str
  load_index()                            -> dict
  save_index(index)
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE_URL = "https://www.namus.gov/api/CaseSets/NamUs"
DATA_DIR = Path(__file__).parent.parent / "data"
INDEX_FILE = DATA_DIR / "index.json"
MAX_WORKERS = 20
CASE_TYPES = ["MissingPersons", "UnidentifiedPersons"]

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0",
    "Accept": "application/json",
}

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=MAX_WORKERS + 5))
    s.headers.update(_HEADERS)
    return s

# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def get_case(session: requests.Session, case_type: str, case_id: int) -> tuple[int, dict | None]:
    """Fetch one case. Returns (case_id, data) or (case_id, None) on 404."""
    try:
        r = session.get(f"{BASE_URL}/{case_type}/cases/{case_id}", timeout=30)
        if r.status_code == 404:
            return case_id, None
        r.raise_for_status()
        return case_id, r.json()
    except Exception as exc:
        log.debug("Error fetching %s/%d: %s", case_type, case_id, exc)
        return case_id, None


def get_total_count(session: requests.Session, case_type: str) -> int:
    """The search endpoint returns an accurate count even though it won't return data."""
    try:
        r = session.post(
            f"{BASE_URL}/{case_type}/search",
            json={"take": 1, "skip": 0, "projections": [], "predicates": []},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("count", 0)
    except Exception:
        return 0

# ---------------------------------------------------------------------------
# Concurrent range fetch
# ---------------------------------------------------------------------------

def fetch_id_range(
    session: requests.Session,
    case_type: str,
    id_range: range,
    log_interval: int = 5000,
) -> list[dict]:
    """Fetch every ID in id_range concurrently; return non-404 cases."""
    cases: list[dict] = []
    skipped = 0
    done = 0
    total = len(id_range)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(get_case, session, case_type, i): i for i in id_range}
        for future in as_completed(futures):
            _, data = future.result()
            done += 1
            if data is not None:
                cases.append(data)
            else:
                skipped += 1
            if log_interval and done % log_interval == 0:
                log.info(
                    "  %d/%d (%.0f%%)  —  %d found, %d skipped",
                    done, total, done / total * 100, len(cases), skipped,
                )

    return cases

# ---------------------------------------------------------------------------
# Date / decade helpers
# ---------------------------------------------------------------------------

def _parse_year(value) -> int | None:
    if value is None:
        return None
    s = str(value).strip()
    if len(s) >= 4 and s[:4].isdigit():
        year = int(s[:4])
        if 1900 <= year <= datetime.now().year + 1:
            return year
    return None


def extract_year(case: dict, case_type: str) -> int | None:
    """
    Date fields confirmed from live API (June 2026):
      MissingPersons:      sighting.date  (last seen)
      UnidentifiedPersons: circumstances.dateFound
                           subjectDescription.estimatedYearOfDeathFrom
    Falls back to createdDateTime (NamUs entry date) as last resort.
    """
    if case_type == "MissingPersons":
        candidates = [
            case.get("sighting", {}).get("date"),
            case.get("createdDateTime"),
        ]
    else:
        candidates = [
            case.get("circumstances", {}).get("dateFound"),
            case.get("subjectDescription", {}).get("estimatedYearOfDeathFrom"),
            case.get("createdDateTime"),
        ]
    for val in candidates:
        year = _parse_year(val)
        if year is not None:
            return year
    return None


def decade_label(year: int) -> str:
    return f"{(year // 10) * 10}s"


def case_filename(case_type: str, label: str) -> str:
    """Return the bare filename for a decade bucket, e.g. 'missing_1990s.json'."""
    prefix = "missing" if case_type == "MissingPersons" else "unidentified"
    return f"{prefix}_{label}.json"

# ---------------------------------------------------------------------------
# index.json I/O
# ---------------------------------------------------------------------------

def load_index() -> dict:
    if INDEX_FILE.exists():
        try:
            return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_index(index: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(json.dumps(index, indent=2), encoding="utf-8")
    log.info("index.json updated (%d files listed)", len(index.get("files", [])))
