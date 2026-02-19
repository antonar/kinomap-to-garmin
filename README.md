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
    -   Activity type → `Indoor Rowing`
    -   Event type → `training` (default) or `race`
    -   Title (derived from filename)
    -   Gear (idempotent)
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
  `--force-upload`   Force upload even if SHA match exists
  `--dry-run`        No upload or patching --- only matching logic

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
