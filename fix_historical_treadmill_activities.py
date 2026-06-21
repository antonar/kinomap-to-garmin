#!/usr/bin/env python3
"""
Fix historical Garmin Connect activities filtered by a configurable name and start date.

This script:
1. Finds all activities matching HISTORICAL_ACTIVITY_NAME
    (default: "Gå på tredemølle") since HISTORICAL_SINCE_DATE
    (default: "2024-10-04")
2. Lists them with current type, event type, and gear status
3. Optionally fixes them (--apply flag):
   - Sets activity type to 'walking'
   - Sets event type to 'training'
   - Attaches TREADMILL_GEAR_UUID
   - Enforces single-gear policy

Usage:
    ./fix_historical_treadmill_activities.py         # Dry-run (list only)
    ./fix_historical_treadmill_activities.py --apply # Apply fixes

Environment variables (optional):
    HISTORICAL_ACTIVITY_NAME=Gå på tredemølle
    HISTORICAL_SINCE_DATE=2024-10-04
"""

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from garminconnect import Garmin
from garmin_utils import (
    load_env_file,
    garmin_login,
    extract_activity_gear_uuids,
    enforce_single_gear,
    set_activity_type,
    set_event_type,
    ACTIVITY_PAGE_SIZE,
)

# ============================================================================
# Configuration and setup
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent
TOKEN_DIR = BASE_DIR / ".garth_tokens"

LEGACY_DEFAULT_GEAR_UUID = "e188437497a041179d6ce51cf2024310"

DEFAULT_HISTORICAL_ACTIVITY_NAME = "Gå på tredemølle"
DEFAULT_HISTORICAL_SINCE_DATE = "2024-10-04"

# ============================================================================
# Main logic
# ============================================================================

def get_activity_type_key(activity: dict) -> str:
    """Extract activity type key from activity summary."""
    return (
        (activity.get("activityType") or {}).get("typeKey")
        or (activity.get("activityTypeDTO") or {}).get("typeKey")
        or activity.get("activityTypeKey")
        or "unknown"
    )

def get_event_type_key(activity: dict) -> str:
    """Extract event type key from activity summary."""
    return (
        (activity.get("eventType") or {}).get("typeKey")
        or (activity.get("eventTypeDTO") or {}).get("typeKey")
        or "unknown"
    )

def get_historical_activities_by_name(api: Garmin, activity_name: str, since_date: str) -> list[dict]:
    """
    Fetch all activities with matching activity_name since since_date.
    since_date format: "2024-10-04"
    """
    cutoff = datetime.strptime(since_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    cutoff_unix = int(cutoff.timestamp())

    activities = []
    start = 0

    while True:
        batch = api.get_activities(start, ACTIVITY_PAGE_SIZE)
        if not batch:
            break

        for act in batch:
            # Check name
            if act.get("activityName", "").strip() != activity_name:
                continue

            # Check date
            st = act.get("startTimeGMT")
            if not st:
                continue

            try:
                dt = datetime.fromisoformat(st.replace("Z", "+00:00"))
                if int(dt.timestamp()) < cutoff_unix:
                    # We've gone back far enough, stop
                    return activities
            except Exception:
                continue

            activities.append(act)

        start += ACTIVITY_PAGE_SIZE

    return activities

def format_duration(seconds: float) -> str:
    """Format duration in seconds to HH:MM:SS or MM:SS."""
    s = int(seconds)
    hours = s // 3600
    minutes = (s % 3600) // 60
    secs = s % 60

    if hours > 0:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    return f"{minutes}m {secs:02d}s"


def load_historical_filter_config() -> tuple[str, str]:
    """Load and validate historical activity filter settings from environment."""
    activity_name = os.getenv("HISTORICAL_ACTIVITY_NAME", DEFAULT_HISTORICAL_ACTIVITY_NAME).strip()
    if not activity_name:
        raise SystemExit(
            "ERROR: HISTORICAL_ACTIVITY_NAME is empty. "
            "Set HISTORICAL_ACTIVITY_NAME (in the environment or in .config/kinomap_to_garmin.env when using wrapper scripts)"
        )

    since_date = os.getenv("HISTORICAL_SINCE_DATE", DEFAULT_HISTORICAL_SINCE_DATE).strip()
    try:
        datetime.strptime(since_date, "%Y-%m-%d")
    except ValueError:
        raise SystemExit(
            "ERROR: HISTORICAL_SINCE_DATE must be YYYY-MM-DD. "
            f"Got: '{since_date}'"
        )

    return activity_name, since_date


def load_runtime_config() -> tuple[str, str, str, str, str]:
    """Load and validate runtime configuration from environment and env file."""
    load_env_file(BASE_DIR / ".config" / "kinomap_to_garmin.env")

    legacy_default_gear_uuid = os.getenv("GEAR_UUID", LEGACY_DEFAULT_GEAR_UUID)
    treadmill_gear_uuid = os.getenv("TREADMILL_GEAR_UUID", legacy_default_gear_uuid).strip()
    if not treadmill_gear_uuid:
        raise SystemExit(
            "ERROR: Gear UUID for treadmill is empty. "
            "Set TREADMILL_GEAR_UUID or GEAR_UUID (in the environment or in .config/kinomap_to_garmin.env when using wrapper scripts)"
        )

    activity_name, since_date = load_historical_filter_config()

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        raise SystemExit(
            "ERROR: GARMIN_EMAIL or GARMIN_PASSWORD is missing. "
            "Set GARMIN_EMAIL and GARMIN_PASSWORD (in the environment or in .config/kinomap_to_garmin.env when using wrapper scripts)"
        )

    return activity_name, since_date, treadmill_gear_uuid, email, password

def main():
    ap = argparse.ArgumentParser(
        description=(
            "Fix historical treadmill/walking activities matching HISTORICAL_ACTIVITY_NAME and date window, "
            "setting type to 'walking', event type to 'training', and attaching the treadmill gear."
        )
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Apply fixes. Without this, only dry-run (list).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit --apply to N activities (for testing). Default: apply all.",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Print debug info for API calls.",
    )
    args = ap.parse_args()

    activity_name, since_date, treadmill_gear_uuid, email, password = load_runtime_config()

    # Login
    api = garmin_login(email, password, TOKEN_DIR)

    # Fetch activities
    print(
        f"Fetching historical activities named '{activity_name}' "
        f"since {since_date}…\n"
    )
    activities = get_historical_activities_by_name(
        api,
        activity_name,
        since_date,
    )

    if not activities:
        print("No activities found.")
        return

    # Analyze each activity
    need_fixing = []
    already_correct = []

    for act in activities:
        aid = act.get("activityId")
        name = act.get("activityName")
        start_time = act.get("startTimeGMT", "unknown")
        current_type = get_activity_type_key(act)
        current_event_type = get_event_type_key(act)
        duration = act.get("duration", 0)

        # Check gear
        try:
            gear_payload = api.get_activity_gear(aid)
            gear_uuids = extract_activity_gear_uuids(gear_payload)
        except Exception:
            gear_uuids = []

        needs_type_fix = current_type != "walking"
        needs_event_type_fix = current_event_type != "training"
        needs_gear_fix = treadmill_gear_uuid not in gear_uuids

        if needs_type_fix or needs_event_type_fix or needs_gear_fix:
            need_fixing.append({
                "id": aid,
                "date": start_time,
                "current_type": current_type,
                "current_event_type": current_event_type,
                "gear_uuids": gear_uuids,
                "duration": duration,
                "needs_type": needs_type_fix,
                "needs_event_type": needs_event_type_fix,
                "needs_gear": needs_gear_fix,
            })
        else:
            already_correct.append(act)

    # Display results
    print(
        f"Historical activities named '{activity_name}' "
        f"since {since_date}:\n"
    )
    print(f"{'ID':>12}  {'Date':<19}  {'Current Type':<15}  {'Gear':<20}  {'Duration':<12}")
    print("=" * 95)

    for act in activities:
        aid = act.get("activityId")
        start_time = act.get("startTimeGMT", "unknown")
        current_type = get_activity_type_key(act)
        duration = format_duration(act.get("duration", 0))

        try:
            gear_payload = api.get_activity_gear(aid)
            gear_uuids = extract_activity_gear_uuids(gear_payload)
            if treadmill_gear_uuid in gear_uuids:
                gear_str = "gåmølle ✓"
            elif gear_uuids:
                gear_str = f"{gear_uuids[0][:8]}… (annet)"
            else:
                gear_str = "(ingen)"
        except Exception:
            gear_str = "(error)"

        # Mark if needs fixing
        marker = ""
        for item in need_fixing:
            if item["id"] == aid:
                marker = " ← FIX"
                break

        print(
            f"{aid:>12}  {start_time:<19}  {current_type:<15}  {gear_str:<20}  {duration:<12}{marker}"
        )

    print("\n" + "=" * 95)
    print(f"\nSummary:")
    print(f"- Total activities: {len(activities)}")
    print(f"- Already correct: {len(already_correct)}")
    print(f"- Need fixing: {len(need_fixing)}")

    if need_fixing:
        print(f"\nActivities needing fixes:")
        for item in need_fixing:
            fixes = []
            if item["needs_type"]:
                fixes.append(f"type ({item['current_type']} → walking)")
            if item["needs_event_type"]:
                fixes.append(f"event type ({item['current_event_type']} → training)")
            if item["needs_gear"]:
                fixes.append("gear (add gåmølle)")
            print(f"  - ID {item['id']}: {', '.join(fixes)}")

    if not args.apply:
        if need_fixing:
            print(f"\nRun with --apply to fix {len(need_fixing)} activities.")
        return

    # Apply fixes
    if not need_fixing:
        print("\nNothing to fix!")
        return

    activities_to_fix = need_fixing
    if args.limit is not None and args.limit > 0:
        activities_to_fix = need_fixing[:args.limit]
        print(f"\nApplying fixes to {len(activities_to_fix)} of {len(need_fixing)} activities (--limit {args.limit})…\n")
    else:
        print(f"\nApplying fixes to {len(need_fixing)} activities…\n")

    for item in activities_to_fix:
        aid = item["id"]
        fixes_applied = []
        had_errors = False

        # Fix type
        if item["needs_type"]:
            try:
                set_activity_type(api, aid, "walking")
                fixes_applied.append("type→walking")
            except Exception as e:
                print(f"  ✗ ID {aid}: Could not set type: {e}")
                had_errors = True

        # Fix event type
        if item["needs_event_type"]:
            try:
                set_event_type(api, aid, "training")
                fixes_applied.append("event_type→training")
            except Exception as e:
                print(f"  ✗ ID {aid}: Could not set event type: {e}")
                had_errors = True

        # Fix gear
        if item["needs_gear"]:
            try:
                single = enforce_single_gear(api, aid, treadmill_gear_uuid, verbose=args.verbose)
                if single["failed"]:
                    gear_errors = [f"{gid}: {err}" for gid, err in single["failed"]]
                    print(f"  ✗ ID {aid}: Could not set gear: {'; '.join(gear_errors)}")
                    had_errors = True
                else:
                    fixes_applied.append("gear→gåmølle")
            except Exception as e:
                print(f"  ✗ ID {aid}: Could not set gear: {e}")
                had_errors = True

        # Only show success if ALL operations succeeded
        if fixes_applied and not had_errors:
            print(f"  ✓ ID {aid}: {', '.join(fixes_applied)}")
        elif fixes_applied and had_errors:
            print(f"  ⚠ ID {aid}: Partial success: {', '.join(fixes_applied)} (but some operations failed)")

    print(f"\nFixed {len(activities_to_fix)} activities!")

if __name__ == "__main__":
    main()
