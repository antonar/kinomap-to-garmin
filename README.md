# 🚣 Kinomap → Garmin Connect (TCX uploader)

A small CLI tool to upload **Kinomap TCX** files to **Garmin Connect**,
with deterministic duplicate detection and idempotent gear handling.

Designed for personal use, but structured and documented for clarity.

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
    ├── run_kinomap.sh
    ├── requirements.txt
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

**Note:** Event type is always set to `training` for Kinomap TCX uploads (use `--race` flag to override).

Set secure permissions:

``` bash
chmod 600 .config/kinomap_to_garmin.env
```

Credentials are loaded automatically at runtime.

------------------------------------------------------------------------

### 3️⃣ Install dependencies 📦

``` bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

Pinned direct dependencies:

    garminconnect==0.2.38
    garth==0.5.21

------------------------------------------------------------------------

## 🚀 Usage

``` bash
./run_kinomap.sh <file.tcx> [options]
```

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
./run_kinomap.sh "York City Walls part 2_2--21924602.tcx" --show-config --sanity
```

Rowing session:

``` bash
./run_kinomap.sh "<rowing-file>.tcx" --show-config --sanity
```

Race event type (optional):

``` bash
./run_kinomap.sh "<file>.tcx" --race --show-config --sanity
```

### Batch / loop over many files

Dry-run all TCX files first:

``` bash
for f in *.tcx; do
    ./run_kinomap.sh "$f" --show-config --dry-run
done
```

Upload all TCX files with sanity output:

``` bash
for f in *.tcx; do
    ./run_kinomap.sh "$f" --show-config --sanity
done
```

------------------------------------------------------------------------

## Historical Activity Cleanup

For existing Kinomap treadmill activities uploaded before sport-aware support was added,
use the historical cleanup utility:

```bash
python3 fix_historical_treadmill_activities.py
```

### Dry-run mode (default)

Lists all historical treadmill activities needing fixes:

```bash
python3 fix_historical_treadmill_activities.py
```

Output shows:
- Activity ID, date, current type, gear status, duration
- Which activities need type fixes vs gear-only fixes

### Apply fixes

Automatically correct all historical activities:

```bash
python3 fix_historical_treadmill_activities.py --apply
```

This will:
1. Set activity type to `walking` (if incorrect)
2. Set event type to `training` (if incorrect - same as Kinomap TCX uploads)
3. Enforce single gear link to `gåmølle` (treadmill gear UUID from env)

**Note:** The utility searches for activities with name `"Gå på tredemølle"` 
since **2024-10-04** (the date sport-aware support was added).

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
