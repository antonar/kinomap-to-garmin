# 🚣 Kinomap → Garmin Connect (TCX uploader)

A small CLI tool to upload **Kinomap TCX** files to **Garmin Connect**,
with deterministic duplicate detection and idempotent gear handling.

Designed for personal use, but structured and documented for clarity.

------------------------------------------------------------------------

## ⚡ Quick Start

1) Create a credentials file:

    .config/kinomap_to_garmin.env

with at least:

    GARMIN_EMAIL=your@email.com
    GARMIN_PASSWORD=yourpassword

2) Run a Kinomap upload (the wrapper creates `.venv` and installs requirements automatically):

``` bash
./run_kinomap.sh "tcx/<file>.tcx" --show-config --sanity
```

(`tcx/` is recommended for a tidy project root, but any file path works.)

3) Run the historical fix if needed:

``` bash
./run_historical.sh --apply
```

4) Run any script via the generic wrapper:

``` bash
./run_in_venv.sh <script.py> [args]
```

------------------------------------------------------------------------

## ✨ Features

-   Uploads original **TCX** files (no FIT rebuilding)
-   Preserves all sensor data (HR, power, cadence, calories, etc.)
-   Deterministic duplicate detection (SHA256 + metadata matching)
-   Automatically sets:
    -   Activity type → `Indoor Rowing` for `Sport="rowing"`
    -   Activity type → `walking` for `Sport="running"` (Kinomap treadmill sessions)
    -   Event type → `training` (default) or `race`
    -   Title (derived from filename, sport-aware prefix)
    -   Gear (idempotent)
    -   Enforces one gear per activity (removes stale extra gear links)
-   Atomic local state handling
-   Safe handling of Garmin API edge cases (409, 404, network errors)

------------------------------------------------------------------------

## 📁 Project Structure

    kinomap-to-garmin/
    ├── .config/
    │   └── kinomap_to_garmin.env
    ├── .kinomap_garmin.json
    ├── kinomap_to_garmin_secure.py
    ├── run_in_venv.sh
    ├── run_historical.sh
    ├── run_kinomap.sh
    ├── requirements.txt
    ├── tcx/                      # optional, recommended for storing TCX files
    └── .venv/

------------------------------------------------------------------------

## ⚙️ Setup

### 1️⃣ Clone the repository

``` bash
git clone <repo-url>
cd kinomap-to-garmin
```

------------------------------------------------------------------------

### 2️⃣ Configure credentials 🔐

Create:

    .config/kinomap_to_garmin.env

With:

    GARMIN_EMAIL=your@email.com
    GARMIN_PASSWORD=yourpassword

Optional title prefixes:

    TITLE_PREFIX=Romaskin – 
    TREADMILL_TITLE_PREFIX=Gåmølle - 

Optional gear UUIDs per sport:

    ROWING_GEAR_UUID=<uuid-for-romaskin>
    TREADMILL_GEAR_UUID=<uuid-for-gåmølle>
    # fallback for both if per-sport values are not set:
    GEAR_UUID=<legacy-default>

Optional mapping for Kinomap `Sport="running"` sessions:

    RUNNING_ACTIVITY_TYPE=walking

Allowed values:

    walking            # default - map Kinomap running → Garmin walking
    treadmill_running  # map Kinomap running → Garmin treadmill running
    keep               # keep Garmin-imported type (no override)
    imported           # synonym for 'keep' (advanced)
    none               # synonym for 'keep' (advanced)
    ""                 # empty string - treated as 'keep'

Optional historical cleanup filter (used by `run_historical.sh` / historical script):

    HISTORICAL_ACTIVITY_NAME=Gå på tredemølle
    HISTORICAL_SINCE_DATE=2024-10-04

Notes:
- `HISTORICAL_ACTIVITY_NAME` should match the exact Garmin activity title you want to target.
- `HISTORICAL_SINCE_DATE` must be in `YYYY-MM-DD` format.

**Note:** Event type is always set to `training` for Kinomap TCX uploads (use `--race` flag to override).

Set secure permissions:

``` bash
chmod 600 .config/kinomap_to_garmin.env
```

Credentials are loaded automatically at runtime.

------------------------------------------------------------------------

### 3️⃣ (Optional) Install dependencies manually 📦

``` bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Pinned direct dependencies:

    garminconnect==0.2.38
    garth==0.5.21

------------------------------------------------------------------------

## 🚀 Usage (Reference)

General wrapper for all Python scripts in the project:

``` bash
./run_in_venv.sh <script.py> [args]
```

Alias for the historical script:

``` bash
./run_historical.sh [--apply] [--limit N] [--verbose]
```

Kinomap uploader (backwards-compatible alias):

``` bash
./run_kinomap.sh tcx/<file.tcx> [options]
```

You can also pass a TCX file from any other location.

### Options

  Flag               Description
  ------------------ ------------------------------------------------
  `--race`           Set event type to `race` (default: `training`)
  `--sanity`         Print TCX vs Garmin summary comparison
  `--show-config`    Print resolved config (type/title/gear) before run
  `--force-upload`   Force upload even if SHA match exists
  `--dry-run`        No upload or patching --- only matching logic

### Recommended commands

Treadmill / walking session:

``` bash
./run_kinomap.sh "tcx/York City Walls part 2_2--21924602.tcx" --show-config --sanity
```

Rowing session:

``` bash
./run_kinomap.sh "tcx/<rowing-file>.tcx" --show-config --sanity
```

Race event type (optional):

``` bash
./run_kinomap.sh "tcx/<file>.tcx" --race --show-config --sanity
```

### Batch / loop over many files

Dry-run all TCX files first:

``` bash
for f in tcx/*.tcx; do
    ./run_kinomap.sh "$f" --show-config --dry-run
done
```

If your files are elsewhere:

``` bash
for f in /path/to/files/*.tcx; do
    ./run_kinomap.sh "$f" --show-config --dry-run
done
```

Upload all TCX files with sanity output:

``` bash
for f in tcx/*.tcx; do
    ./run_kinomap.sh "$f" --show-config --sanity
done
```

------------------------------------------------------------------------

## ⚠️ Gotchas / Troubleshooting

| Error message / symptom | Likely cause | What to do |
| --- | --- | --- |
| `No activities found.` (historical script) | No activity matches both `HISTORICAL_ACTIVITY_NAME` and `HISTORICAL_SINCE_DATE`. | Verify exact activity title in Garmin Connect and widen/adjust `HISTORICAL_SINCE_DATE`. |
| `ERROR: HISTORICAL_SINCE_DATE must be YYYY-MM-DD` | Invalid date format in env configuration. | Set `HISTORICAL_SINCE_DATE` to a valid value such as `2024-10-04`. |
| `Set GARMIN_EMAIL and GARMIN_PASSWORD.` | Missing credentials in `.config/kinomap_to_garmin.env`. | Add both variables and re-run. |
| `Could not set gear ...` or gear fix not applied | Garmin rejects gear link for that activity (often date-related constraints). | Check the gear's **First use date** in Garmin Connect and ensure it is compatible with the activity date. |
| Upload reports duplicate / no new upload | Duplicate detection matched existing activity via hash/metadata. | This is expected behaviour; use `--force-upload` only when you intentionally want to retry upload logic. |

------------------------------------------------------------------------

## Historical Activity Cleanup

For existing Kinomap treadmill activities uploaded before sport-aware support was added,
use the historical cleanup utility:

```bash
./run_historical.sh
```

Generic alternative:

```bash
./run_in_venv.sh fix_historical_treadmill_activities.py
```

### Dry-run mode (default)

Lists all historical treadmill activities needing fixes:

```bash
./run_historical.sh
```

Output shows:
- Activity ID, date, current type, gear status, duration
- Which activities need type fixes vs gear-only fixes

### Apply fixes

Automatically correct all historical activities:

```bash
./run_historical.sh --apply
```

Generic alternative:

```bash
./run_in_venv.sh fix_historical_treadmill_activities.py --apply
```

This will:
1. Set activity type to `walking` (if incorrect)
2. Set event type to `training` (if incorrect - same as Kinomap TCX uploads)
3. Enforce single gear link to `gåmølle` (treadmill gear UUID from env)

**Note:** The utility filters by `HISTORICAL_ACTIVITY_NAME` and `HISTORICAL_SINCE_DATE`
(defaults: `Gå på tredemølle` and `2024-10-04`).

------------------------------------------------------------------------

## 🔁 Duplicate Handling

The tool prevents duplicate uploads using:

1.  **SHA256 hash of the TCX file**
2.  Deterministic matching based on:
    -   start time
    -   distance
    -   duration

Local state is stored in:

    .kinomap_garmin.json

The file is written atomically and tolerates corruption.

------------------------------------------------------------------------

## 🧠 Design Notes

-   Uses original TCX upload to preserve all Garmin-computed metrics.
-   Avoids FIT reconstruction entirely.
-   Does not rely on "latest activity" assumptions.
-   Fails safely on network/API errors.
-   Keeps all state local and minimal.

------------------------------------------------------------------------

## 🎯 Scope

This is a focused personal automation tool --- not a general-purpose
Garmin SDK wrapper.

It is intentionally simple, explicit, and self-contained.

------------------------------------------------------------------------

## 🙏 Acknowledgements

Developed with assistance from ChatGPT.
