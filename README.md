# NamUs Data Scraper

![NAMUS](.github/Namus.png)

Fetches all **Missing Persons** and **Unidentified Persons** cases from the [National Missing and Unidentified Persons System (NamUs)](https://www.namus.gov) public API and stores them as JSON files organized by decade. A GitHub Actions workflow runs a lightweight daily update that only checks for newly added cases.

---

## Repository layout

```
.
├── scripts/
│   ├── scrape_namus.py          # one-time full scan (run locally to build initial dataset)
│   ├── update_namus.py          # daily incremental updater (run by GitHub Actions)
│   └── namus_utils.py           # shared session, date helpers, fetch logic
├── requirements.txt             # Python dependencies
├── data/
│   ├── missing_1960s.json
│   ├── missing_1970s.json
│   ├── ...
│   ├── unidentified_1960s.json
│   ├── ...
│   └── index.json               # manifest of all data files + scrape metadata
└── .github/
    └── workflows/
        └── update_namus.yml     # daily GitHub Actions workflow
```

---

## Quick start

**Step 1 — build the initial dataset (run once, locally):**

```bash
pip install -r requirements.txt
python scripts/scrape_namus.py
```

This scans ~130,000 Missing Persons IDs and ~130,000 Unidentified Persons IDs concurrently (20 workers). Expect 30–60 minutes. It writes all the decade JSON files and `data/index.json`.

**Step 2 — push to GitHub:**

```bash
git init
git add .
git commit -m "Initial NamUs dataset"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

After that, GitHub Actions runs `scripts/update_namus.py` every day at 06:00 UTC automatically.

---

## How the two scripts differ

| | `scripts/scrape_namus.py` | `scripts/update_namus.py` |
|---|---|---|
| **Purpose** | Build the full dataset from scratch | Add new cases discovered since the last run |
| **When to run** | Once locally; or to fully rebuild | Every day via GitHub Actions |
| **API requests** | ~260,000 (full range scan) | ~500 (window above last known ID) |
| **Runtime** | 30–60 minutes | < 1 minute |
| **Output** | Writes all decade files + `index.json` | Merges new cases into existing files, updates `index.json` |

The daily updater works by reading `max_valid_id` from `index.json` (set during the full scrape) and probing only the next 500 IDs per case type. NamUs adds roughly 5 cases per day nationwide, so this window is more than enough.

> **Note:** The updater detects *new* cases only. If an existing case is modified or resolved in NamUs, those changes will not be reflected until you run a full `scripts/scrape_namus.py` again. A monthly or quarterly re-scrape is recommended if data freshness for existing cases matters.

---

## Data files

Each JSON file covers one decade and one case type. The filename pattern is:

```
{missing|unidentified}_{decade}.json
```

Every file has the same envelope:

```json
{
  "generated_at": "2026-06-19T14:00:00+00:00",
  "case_type": "MissingPersons",
  "decade": "1990s",
  "count": 4821,
  "cases": [ ... ]
}
```

Cases with no usable date are written to `missing_unknown.json` / `unidentified_unknown.json`.

### index.json

Updated at the end of every run. Lists every data file and stores the `max_valid_id` that the daily updater needs to know where to start probing.

```json
{
  "generated_at": "2026-06-19T14:00:00+00:00",
  "files": [
    "missing_1960s.json",
    "missing_1970s.json",
    "missing_1980s.json",
    "missing_1990s.json",
    "missing_2000s.json",
    "missing_unknown.json",
    "unidentified_1960s.json",
    "unidentified_1970s.json",
    "..."
  ],
  "MissingPersons": {
    "max_valid_id": 32847,
    "upper_bound": 55000,
    "last_full_scrape": "2026-06-19T14:00:00+00:00",
    "cases_fetched": 26647
  },
  "UnidentifiedPersons": {
    "max_valid_id": 18293,
    "upper_bound": 25000,
    "last_full_scrape": "2026-06-19T14:00:00+00:00",
    "cases_fetched": 15498
  }
}
```

### Key fields per case

**Missing Persons** (`case_type: MissingPersons`)

| Field | Description |
|---|---|
| `id` | Integer case ID |
| `idFormatted` | Human-readable ID, e.g. `MP1234` |
| `sighting.date` | Date last seen (used for decade bucketing) |
| `subjectIdentification` | Name, age at disappearance |
| `subjectDescription` | Sex, ethnicity, height, weight |
| `circumstances.circumstancesOfDisappearance` | Narrative description |
| `caseIsResolved` | `true` if the case has been resolved |
| `modifiedDateTime` | Last time the NamUs record was updated |

**Unidentified Persons** (`case_type: UnidentifiedPersons`)

| Field | Description |
|---|---|
| `id` | Integer case ID |
| `idFormatted` | Human-readable ID, e.g. `UP5000` |
| `circumstances.dateFound` | Date remains were found (used for decade bucketing) |
| `subjectDescription.estimatedYearOfDeathFrom/To` | Estimated death year range |
| `subjectDescription.estimatedAgeFrom/To` | Estimated age range |
| `subjectDescription.sex`, `ethnicities` | Physical description |
| `detailsOfRecovery` | Location and manner of recovery |
| `modifiedDateTime` | Last time the NamUs record was updated |

---

## How the scraper works

NamUs exposes two relevant API surfaces:

- **Search endpoint** (`POST /search`) — returns an accurate total count but always returns empty results for unauthenticated requests.
- **Individual case endpoint** (`GET /cases/{id}`) — returns full case data with no authentication required.

Because IDs are non-contiguous (gaps exist where cases were archived or resolved), the full scraper scans every integer from `1` to a known upper bound rather than paginating. The daily updater scans only a small window above the last seen valid ID.

**Decade bucketing uses these date fields** (confirmed from live API):

| Case type | Primary date | Fallback |
|---|---|---|
| Missing | `sighting.date` | `createdDateTime` |
| Unidentified | `circumstances.dateFound` | `subjectDescription.estimatedYearOfDeathFrom` → `createdDateTime` |

---

## Automated daily updates (GitHub Actions)

The workflow in `.github/workflows/update_namus.yml` runs `scripts/update_namus.py` every day at **06:00 UTC**. It can also be triggered manually from the **Actions** tab.

Steps:
1. Checkout repository
2. Install dependencies
3. Run `scripts/update_namus.py` (probes ~500 IDs per case type)
4. Commit and push any changed files in `data/` — skips the commit if nothing changed

No secrets or personal access tokens are needed — the workflow uses the built-in `GITHUB_TOKEN` with `contents: write` permission.

---

## Data source

All data is sourced from the [NamUs public API](https://www.namus.gov) operated by the National Institute of Justice. NamUs is a free, online resource for missing persons and unidentified decedent cases. This scraper accesses only publicly available, unauthenticated endpoints.
