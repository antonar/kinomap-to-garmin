"""
Shared utilities for Garmin Connect API interactions.

This module contains common functions and constants used across multiple
scripts for interacting with Garmin Connect activities.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from garminconnect import Garmin

# Garmin Connect Activity Type IDs (from reverse-engineering API responses)
# These are used when setting activity types via PUT request to /activity/{id}
ACTIVITY_TYPE_INDOOR_ROWING = 32
ACTIVITY_PARENT_TYPE_INDOOR_ROWING = 29
ACTIVITY_TYPE_WALKING = 9
ACTIVITY_PARENT_TYPE_WALKING = 17
ACTIVITY_TYPE_TREADMILL_RUNNING = 18
ACTIVITY_PARENT_TYPE_TREADMILL_RUNNING = 1

# Garmin Connect Event Type IDs (training vs race)
EVENT_TYPE_TRAINING = 4
EVENT_TYPE_RACE = 1

# Default page size for fetching activities
ACTIVITY_PAGE_SIZE = 200


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


def _extract_activity_gear_uuids(payload) -> list[str]:
    """
    Extract gear UUIDs from Garmin API response.
    
    Args:
        payload: API response from get_activity_gear() - can be list or dict
        
    Returns:
        List of gear UUIDs (as strings)
    """
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


def enforce_single_gear(
    api: Garmin,
    activity_id: int,
    keep_gear_uuid: str,
    gear_payload: dict | list | None = None,
    verbose: bool = False,
    db_path: Path | None = None,
) -> dict:
    """
    Ensure exactly one gear link remains on activity: keep_gear_uuid.
    
    Best effort: does not raise on individual unlink failures.
    
    Args:
        api: Garmin API client
        activity_id: Activity ID to modify
        keep_gear_uuid: UUID of gear to keep
        gear_payload: Optional cached gear payload (from get_activity_gear)
        verbose: Print debug messages if True
        db_path: Optional path to local DB for caching (if None, no DB update)
        
    Returns:
        dict with keys: "kept", "removed", "failed"
    """
    keep = str(keep_gear_uuid)
    removed = []
    failed = []
    add_failed = False

    if verbose:
        print(f"    [DEBUG] Fetching gear for activity {activity_id}...")

    if gear_payload is None:
        payload = api.get_activity_gear(activity_id)
    else:
        payload = gear_payload
    
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
            add_failed = True

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

    # Update database if target gear is successfully on the activity and DB path provided
    # Removal failures are tracked but don't prevent DB update since the target gear is correct
    if db_path and keep in linked and not add_failed:
        db = load_db(db_path)
        db.setdefault("gear_by_activity", {})[str(activity_id)] = keep
        save_db(db_path, db)
    
    if verbose:
        print(f"    [DEBUG] Result: removed={removed}, failed={failed}")

    return {
        "kept": keep,
        "removed": removed,
        "failed": failed,
    }


def set_activity_type(api: Garmin, activity_id: int, type_key: str = "indoor_rowing"):
    """
    Set activity type on Garmin Connect.
    
    Args:
        api: Garmin API client
        activity_id: Activity ID to modify
        type_key: One of "indoor_rowing", "walking", "treadmill_running"
        
    Returns:
        API response
    """
    type_map = {
        "indoor_rowing": {
            "typeId": ACTIVITY_TYPE_INDOOR_ROWING,
            "typeKey": "indoor_rowing",
            "parentTypeId": ACTIVITY_PARENT_TYPE_INDOOR_ROWING,
        },
        "walking": {
            "typeId": ACTIVITY_TYPE_WALKING,
            "typeKey": "walking",
            "parentTypeId": ACTIVITY_PARENT_TYPE_WALKING,
        },
        "treadmill_running": {
            "typeId": ACTIVITY_TYPE_TREADMILL_RUNNING,
            "typeKey": "treadmill_running",
            "parentTypeId": ACTIVITY_PARENT_TYPE_TREADMILL_RUNNING,
        },
    }

    if type_key not in type_map:
        raise ValueError(f"Unknown type_key: '{type_key}'")

    url = f"{api.garmin_connect_activity}/{activity_id}"
    payload = {
        "activityId": activity_id,
        "activityTypeDTO": type_map[type_key],
    }
    return api.garth.put("connectapi", url, json=payload, api=True)


def set_event_type(api: Garmin, activity_id: int, type_key: str = "training"):
    """
    Set event type (training/race) on Garmin Connect.
    
    Args:
        api: Garmin API client
        activity_id: Activity ID to modify
        type_key: One of "training", "race"
        
    Returns:
        API response
    """
    type_map = {
        "training": {
            "typeId": EVENT_TYPE_TRAINING,
            "typeKey": "training",
            "sortOrder": 7,
        },
        "race": {
            "typeId": EVENT_TYPE_RACE,
            "typeKey": "race",
            "sortOrder": 5,
        },
    }

    if type_key not in type_map:
        raise ValueError(f"Unknown event type_key: '{type_key}'")

    url = f"{api.garmin_connect_activity}/{activity_id}"
    payload = {
        "activityId": activity_id,
        "eventTypeDTO": type_map[type_key],
    }
    return api.garth.put("connectapi", url, json=payload, api=True)


def load_db(db_path: Path) -> dict:
    """
    Load local database from JSON file.
    
    Returns dict with keys: "hash_to_activity", "gear_by_activity"
    """
    if not db_path.exists():
        return {"hash_to_activity": {}, "gear_by_activity": {}}

    try:
        d = json.loads(db_path.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {"hash_to_activity": {}, "gear_by_activity": {}}
        d.setdefault("hash_to_activity", {})
        d.setdefault("gear_by_activity", {})
        return d
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"hash_to_activity": {}, "gear_by_activity": {}}


def save_db(db_path: Path, d: dict) -> None:
    """Save database to JSON file atomically."""
    tmp = db_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(db_path)
