#!/usr/bin/env python3
"""
Fix historical "Gå på tredemølle" activities on Garmin Connect.

This script:
1. Finds all activities named "Gå på tredemølle" since 2024-10-04
2. Lists them with current type, event type, and gear status
3. Optionally fixes them (--apply flag):
   - Sets activity type to 'walking'
   - Sets event type to 'training'
   - Attaches TREADMILL_GEAR_UUID
   - Enforces single-gear policy

Usage:
    ./fix_historical_treadmill_activities.py         # Dry-run (list only)
    ./fix_historical_treadmill_activities.py --apply # Apply fixes
"""

import argparse
import os
from datetime import datetime, timezone
from pathlib import Path

from garminconnect import Garmin

# ============================================================================
# Shared utilities (mirrored from kinomap_to_garmin_secure.py)
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent

def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE lines into environment (if not already set)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        val = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), val)

# Load local env early
load_env_file(BASE_DIR / ".config" / "kinomap_to_garmin.env")

# Defaults
LEGACY_DEFAULT_GEAR_UUID = os.getenv("GEAR_UUID", "e188437497a041179d6ce51cf2024310")
DEFAULT_TREADMILL_GEAR_UUID = os.getenv("TREADMILL_GEAR_UUID", LEGACY_DEFAULT_GEAR_UUID).strip()

# Validate that gear UUID is not empty
if not DEFAULT_TREADMILL_GEAR_UUID:
    raise SystemExit(
        "ERROR: Gear UUID for treadmill is empty. "
        "Set TREADMILL_GEAR_UUID or GEAR_UUID in .config/kinomap_to_garmin.env"
    )

# Event Type IDs (from reverse-engineering API responses)
EVENT_TYPE_TRAINING = 4
EVENT_TYPE_RACE = 1

ACTIVITY_PAGE_SIZE = 200

def _extract_activity_gear_uuids(payload) -> list[str]:
    """Extract gear UUIDs from API response."""
    items = []
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        for key in ("gear", "gearDTOs", "activityGear", "items"):
            v = payload.get(key)
            if isinstance(v, list):
                items = v
                break

    uuids = []
    for item in items:
        if not isinstance(item, dict):
            continue
        gid = item.get("uuid") or item.get("gearUUID") or item.get("gearUuid")
        if gid:
            uuids.append(str(gid))
    return uuids

def enforce_single_gear(api: Garmin, activity_id: int, keep_gear_uuid: str, verbose: bool = False) -> dict:
    """Ensure exactly one gear link remains on activity."""
    keep = str(keep_gear_uuid)
    removed = []
    failed = []

    if verbose:
        print(f"    [DEBUG] Fetching gear for activity {activity_id}...")
    
    payload = api.get_activity_gear(activity_id)
    linked = _extract_activity_gear_uuids(payload)
    
    if verbose:
        print(f"    [DEBUG] Current gear UUIDs: {linked}")
        print(f"    [DEBUG] Target gear UUID: {keep}")

    if keep not in linked:
        if verbose:
            print(f"    [DEBUG] Attempting to add gear {keep}...")
        try:
            result = api.add_gear_to_activity(keep, activity_id)
            linked.append(keep)
            if verbose:
                print(f"    [DEBUG] Add gear succeeded: {result}")
        except Exception as e:
            if verbose:
                print(f"    [DEBUG] Add gear FAILED: {type(e).__name__}: {e}")
            failed.append((keep, f"{type(e).__name__}: {e}"))

    for gid in sorted(set(linked)):
        if gid == keep:
            continue
        if verbose:
            print(f"    [DEBUG] Attempting to remove gear {gid}...")
        try:
            api.remove_gear_from_activity(gid, activity_id)
            removed.append(gid)
            if verbose:
                print(f"    [DEBUG] Remove gear succeeded")
        except Exception as e:
            if verbose:
                print(f"    [DEBUG] Remove gear FAILED: {type(e).__name__}: {e}")
            failed.append((gid, f"{type(e).__name__}: {e}"))

    if verbose:
        print(f"    [DEBUG] Result: removed={removed}, failed={failed}")
    
    return {
        "kept": keep,
        "removed": removed,
        "failed": failed,
    }

def set_activity_type(api: Garmin, activity_id: int, type_key: str = "walking"):
    """Set activity type on Garmin Connect."""
    type_map = {
        "indoor_rowing": {"typeId": 32, "typeKey": "indoor_rowing", "parentTypeId": 29},
        "walking": {"typeId": 9, "typeKey": "walking", "parentTypeId": 17},
        "treadmill_running": {"typeId": 18, "typeKey": "treadmill_running", "parentTypeId": 1},
    }

    if type_key not in type_map:
        raise ValueError(f"Unknown type_key: {type_key}")

    url = f"{api.garmin_connect_activity}/{activity_id}"
    payload = {
        "activityId": activity_id,
        "activityTypeDTO": type_map[type_key],
    }
    return api.garth.put("connectapi", url, json=payload, api=True)

def set_event_type(api: Garmin, activity_id: int, type_key: str = "training"):
    """Set event type (training/race) on Garmin Connect."""
    type_map = {
        "training": {"typeId": EVENT_TYPE_TRAINING, "typeKey": "training", "sortOrder": 7},
        "race": {"typeId": EVENT_TYPE_RACE, "typeKey": "race", "sortOrder": 5},
    }

    if type_key not in type_map:
        raise ValueError(f"Unknown event type_key: {type_key}")

    url = f"{api.garmin_connect_activity}/{activity_id}"
    payload = {
        "activityId": activity_id,
        "eventTypeDTO": type_map[type_key],
    }
    return api.garth.put("connectapi", url, json=payload, api=True)

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

def get_gear_display_name(api: Garmin, gear_uuid: str) -> str:
    """Get display name for gear UUID."""
    try:
        # Try to find gear in user's gear list
        # This is a best-effort lookup
        if gear_uuid == DEFAULT_TREADMILL_GEAR_UUID:
            return "gåmølle"
        return gear_uuid[:8] + "..."
    except Exception:
        return gear_uuid[:8] + "..."

def get_historical_treadmill_activities(api: Garmin, since_date: str) -> list[dict]:
    """
    Fetch all activities with name "Gå på tredemølle" since since_date.
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
            if act.get("activityName", "").strip() != "Gå på tredemølle":
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

def main():
    ap = argparse.ArgumentParser(
        description="Fix historical 'Gå på tredemølle' activities with correct type and gear."
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

    # Load credentials
    load_env_file(BASE_DIR / ".config" / "kinomap_to_garmin.env")
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")

    if not email or not password:
        raise SystemExit("Set GARMIN_EMAIL and GARMIN_PASSWORD.")

    # Login
    api = Garmin(email, password)
    api.login()

    # Fetch activities
    print("Fetching historical 'Gå på tredemølle' activities since 2024-10-04…\n")
    activities = get_historical_treadmill_activities(api, "2024-10-04")

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
            gear_uuids = _extract_activity_gear_uuids(gear_payload)
        except Exception:
            gear_uuids = []

        needs_type_fix = current_type != "walking"
        needs_event_type_fix = current_event_type != "training"
        needs_gear_fix = DEFAULT_TREADMILL_GEAR_UUID not in gear_uuids

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
    print(f"Historical treadmill activities since 2024-10-04:\n")
    print(f"{'ID':>12}  {'Date':<19}  {'Current Type':<15}  {'Gear':<20}  {'Duration':<12}")
    print("=" * 95)

    for act in activities:
        aid = act.get("activityId")
        start_time = act.get("startTimeGMT", "unknown")
        current_type = get_activity_type_key(act)
        duration = format_duration(act.get("duration", 0))

        try:
            gear_payload = api.get_activity_gear(aid)
            gear_uuids = _extract_activity_gear_uuids(gear_payload)
            if DEFAULT_TREADMILL_GEAR_UUID in gear_uuids:
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
                single = enforce_single_gear(api, aid, DEFAULT_TREADMILL_GEAR_UUID, verbose=args.verbose)
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
