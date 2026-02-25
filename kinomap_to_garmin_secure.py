#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
import hashlib
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from garth.exc import GarthHTTPError

from garminconnect import Garmin

# Paths
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / ".kinomap_garmin.json"

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

# Load local env early so module defaults below can see .config values.
load_env_file(BASE_DIR / ".config" / "kinomap_to_garmin.env")

# Defaults (you can override with env vars if you want)
LEGACY_DEFAULT_GEAR_UUID = os.getenv("GEAR_UUID", "e188437497a041179d6ce51cf2024310")
DEFAULT_ROWING_GEAR_UUID = os.getenv("ROWING_GEAR_UUID", LEGACY_DEFAULT_GEAR_UUID).strip()
DEFAULT_TREADMILL_GEAR_UUID = os.getenv("TREADMILL_GEAR_UUID", LEGACY_DEFAULT_GEAR_UUID).strip()

# Validate that gear UUIDs are not empty
if not DEFAULT_ROWING_GEAR_UUID:
    raise SystemExit(
        "ERROR: Gear UUID for rowing is empty. "
        "Set ROWING_GEAR_UUID or GEAR_UUID in .config/kinomap_to_garmin.env"
    )
if not DEFAULT_TREADMILL_GEAR_UUID:
    raise SystemExit(
        "ERROR: Gear UUID for treadmill is empty. "
        "Set TREADMILL_GEAR_UUID or GEAR_UUID in .config/kinomap_to_garmin.env"
    )

DEFAULT_ROWING_PREFIX = os.getenv("TITLE_PREFIX", "Romaskin – ")
DEFAULT_TREADMILL_PREFIX = os.getenv("TREADMILL_TITLE_PREFIX", "Gåmølle - ")
RUNNING_ACTIVITY_TYPE_RAW = os.getenv("RUNNING_ACTIVITY_TYPE", "walking").strip().lower()
# Validate and normalize RUNNING_ACTIVITY_TYPE early to prevent invalid config from being used
ALLOWED_RUNNING_TYPES = {"", "keep", "imported", "none", "walking", "treadmill_running"}
DEFAULT_RUNNING_ACTIVITY_TYPE = (
    RUNNING_ACTIVITY_TYPE_RAW
    if RUNNING_ACTIVITY_TYPE_RAW in ALLOWED_RUNNING_TYPES
    else "walking"
)
if RUNNING_ACTIVITY_TYPE_RAW not in ALLOWED_RUNNING_TYPES:
    print(
        f"NB: Ugyldig RUNNING_ACTIVITY_TYPE='{RUNNING_ACTIVITY_TYPE_RAW}'. Bruker 'walking' som default.",
        file=sys.stderr,
    )

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

ACTIVITY_PAGE_SIZE = 200

def activity_exists(api: Garmin, activity_id: int) -> bool:
    try:
        api.get_activity_details(activity_id)
        return True
    except GarthHTTPError as e:
        # 404 = finnes ikke
        if "404" in str(e):
            return False
        # Andre HTTP-feil → re-raise
        raise

def load_db() -> dict:
    if not DB_PATH.exists():
        return {"hash_to_activity": {}, "gear_by_activity": {}}

    try:
        d = json.loads(DB_PATH.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            return {"hash_to_activity": {}, "gear_by_activity": {}}
        d.setdefault("hash_to_activity", {})
        d.setdefault("gear_by_activity", {})
        return d
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"hash_to_activity": {}, "gear_by_activity": {}}

def save_db(d: dict) -> None:
    tmp = DB_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(DB_PATH)

def ensure_gear_cached(api: Garmin, activity_id: int, gear_uuid: str) -> str:
    db = load_db()
    gear_map = db["gear_by_activity"]

    key = str(activity_id)
    if gear_map.get(key) == str(gear_uuid):
        return "already"

    api.add_gear_to_activity(gear_uuid, activity_id)
    gear_map[key] = str(gear_uuid)
    save_db(db)
    return "added"

def _extract_activity_gear_uuids(payload) -> list[str]:
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

def enforce_single_gear(api: Garmin, activity_id: int, keep_gear_uuid: str, gear_payload: dict | list | None = None) -> dict:
    """
    Ensure exactly one gear link remains on activity: keep_gear_uuid.
    Best effort: does not raise on individual unlink failures.
    
    If gear_payload is provided, use it (from cache). Otherwise fetch from API.
    This allows callers to cache and reuse get_activity_gear() results.
    """
    keep = str(keep_gear_uuid)
    removed = []
    failed = []
    add_failed = False

    if gear_payload is None:
        payload = api.get_activity_gear(activity_id)
    else:
        payload = gear_payload
    
    linked = _extract_activity_gear_uuids(payload)

    if keep not in linked:
        try:
            api.add_gear_to_activity(keep, activity_id)
            linked.append(keep)
        except Exception as e:
            failed.append((keep, f"{type(e).__name__}: {e}"))
            add_failed = True

    for gid in sorted(set(linked)):
        if gid == keep:
            continue
        try:
            api.remove_gear_from_activity(gid, activity_id)
            removed.append(gid)
        except Exception as e:
            failed.append((gid, f"{type(e).__name__}: {e}"))

    # Update database if target gear is successfully on the activity (either was already there or added)
    # Removal failures are tracked but don't prevent DB update since the target gear is correct
    if keep in linked and not add_failed:
        db = load_db()
        db.setdefault("gear_by_activity", {})[str(activity_id)] = keep
        save_db(db)

    return {
        "kept": keep,
        "removed": removed,
        "failed": failed,
    }

def parse_tcx_metadata(tcx: Path):
    ns = {"tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}
    tree = ET.parse(tcx)
    root = tree.getroot()

    activity = root.find(".//tcx:Activity", ns)
    tcx_sport = ""
    if activity is not None:
        tcx_sport = (activity.attrib.get("Sport") or "").strip().lower()

    creator_name = root.findtext(".//tcx:Creator/tcx:Name", default="", namespaces=ns).strip()

    laps = root.findall(".//tcx:Lap", ns)
    if not laps:
        raise SystemExit("Fant ingen <Lap> i TCX.")

    # Start = StartTime på første lap
    start_str = laps[0].attrib.get("StartTime")
    if not start_str:
        raise SystemExit("Mangler StartTime-attributt på første <Lap> i TCX.")
    dt = datetime.fromisoformat(start_str.replace("Z", "+00:00")).astimezone(timezone.utc)
    start_unix = int(dt.timestamp())

    # Summer varighet og distanse over alle laps
    dist = 0.0
    dur = 0.0
    for lap in laps:
        dm = lap.findtext("tcx:DistanceMeters", default="0", namespaces=ns)
        ts = lap.findtext("tcx:TotalTimeSeconds", default="0", namespaces=ns)
        try:
            dist += float(dm)
        except ValueError:
            pass
        try:
            dur += float(ts)
        except ValueError:
            pass

    return start_unix, dist, dur, tcx_sport, creator_name

def resolve_desired_activity_type(tcx_sport: str, creator_name: str) -> str | None:
    sport = (tcx_sport or "").strip().lower()
    creator = (creator_name or "").strip().lower()

    if sport == "rowing":
        return "indoor_rowing"

    # Kinomap tredemølleøkter eksporteres typisk som Sport="running"
    # (ofte med Creator=KinomapVirtualRun).
    if sport == "running" or creator == "kinomapvirtualrun":
        if DEFAULT_RUNNING_ACTIVITY_TYPE in {"", "keep", "imported", "none"}:
            return None
        # DEFAULT_RUNNING_ACTIVITY_TYPE is pre-validated at module level,
        # so it's guaranteed to be "walking" or "treadmill_running" here
        return DEFAULT_RUNNING_ACTIVITY_TYPE

    return None

def resolve_title_prefix(tcx_sport: str, creator_name: str = "") -> str:
    sport = (tcx_sport or "").strip().lower()
    creator = (creator_name or "").strip().lower()
    if sport == "running" or creator == "kinomapvirtualrun":
        return DEFAULT_TREADMILL_PREFIX
    return DEFAULT_ROWING_PREFIX

def resolve_gear_uuid(tcx_sport: str, creator_name: str = "") -> str:
    sport = (tcx_sport or "").strip().lower()
    creator = (creator_name or "").strip().lower()
    if sport == "running" or creator == "kinomapvirtualrun":
        return DEFAULT_TREADMILL_GEAR_UUID
    return DEFAULT_ROWING_GEAR_UUID

def print_run_config(
    tcx: Path,
    tcx_sport: str,
    creator_name: str,
    desired_type_key: str | None,
    event_type: str,
    title: str,
    desired_gear_uuid: str,
    force_upload: bool,
    dry_run: bool,
) -> None:
    print("CONFIG:")
    print(f"- tcx_file: {tcx.name}")
    print(f"- tcx_sport: {tcx_sport or 'ukjent'}")
    print(f"- creator: {creator_name or 'ukjent'}")
    print(f"- activity_type_target: {desired_type_key if desired_type_key else 'behold importert type'}")
    print(f"- event_type_target: {event_type}")
    print(f"- title_target: {title}")
    print(f"- gear_target_uuid: {desired_gear_uuid}")
    print("- duplicate_mode: sha256 -> metadata match -> upload")
    print(f"- force_upload: {str(force_upload).lower()}")
    print(f"- dry_run: {str(dry_run).lower()}")

def set_event_type(api: Garmin, activity_id: int, type_key: str):
    type_map = {
        "training": {"typeId": EVENT_TYPE_TRAINING, "typeKey": "training", "sortOrder": 7},
        "race": {"typeId": EVENT_TYPE_RACE, "typeKey": "race", "sortOrder": 5},
    }

    if type_key not in type_map:
        raise ValueError(f"Unknown event type key: {type_key}")

    url = f"{api.garmin_connect_activity}/{activity_id}"

    payload = {
        "activityId": activity_id,
        "eventTypeDTO": type_map[type_key],
    }

    return api.garth.put("connectapi", url, json=payload, api=True)

def parse_start_gmt(s: str) -> int:
    # "YYYY-MM-DD HH:MM:SS"
    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())

def find_uploaded_activity(
    api: Garmin,
    exp_start_unix: int,
    exp_distance_m: float,
    exp_duration_s: float,
    timeout_s: int = 90,
) -> int:
    dist_tol = 50.0   # meters
    dur_tol = 15.0    # seconds
    start_tol = 120   # seconds

    deadline = time.time() + timeout_s
    last_seen = None

    while time.time() < deadline:
        acts = api.get_activities(0, ACTIVITY_PAGE_SIZE)

        matches = []
        for a in acts:
            st = a.get("startTimeGMT")
            if not st:
                continue
            try:
                st_unix = parse_start_gmt(st)
            except Exception:
                continue

            dist = a.get("distance")
            dur = a.get("duration")
            if dist is None or dur is None:
                continue

            if abs(st_unix - exp_start_unix) <= start_tol \
               and abs(float(dist) - exp_distance_m) <= dist_tol \
               and abs(float(dur) - exp_duration_s) <= dur_tol:
                matches.append(a)

        if len(matches) == 1:
            return matches[0]["activityId"]

        if matches:
            last_seen = [
                (
                    m.get("activityId"),
                    m.get("startTimeGMT"),
                    m.get("distance"),
                    m.get("duration"),
                    (m.get("activityType") or {}).get("typeKey"),
                )
                for m in matches
            ]

        time.sleep(3)

    raise RuntimeError(
        f"Fant ikke entydig match for opplastet aktivitet innen {timeout_s}s. "
        f"Last matches: {last_seen}"
    )

def set_activity_type(api: Garmin, activity_id: int, type_key: str = "indoor_rowing"):
    type_map = {
        "indoor_rowing": {"typeId": ACTIVITY_TYPE_INDOOR_ROWING, "typeKey": "indoor_rowing", "parentTypeId": ACTIVITY_PARENT_TYPE_INDOOR_ROWING},
        "walking": {"typeId": ACTIVITY_TYPE_WALKING, "typeKey": "walking", "parentTypeId": ACTIVITY_PARENT_TYPE_WALKING},
        "treadmill_running": {"typeId": ACTIVITY_TYPE_TREADMILL_RUNNING, "typeKey": "treadmill_running", "parentTypeId": ACTIVITY_PARENT_TYPE_TREADMILL_RUNNING},
    }

    if type_key not in type_map:
        raise ValueError(f"Ukjent/støttes ikke type_key='{type_key}'")

    url = f"{api.garmin_connect_activity}/{activity_id}"

    payload = {
        "activityId": activity_id,
        "activityTypeDTO": type_map[type_key],
    }

    return api.garth.put("connectapi", url, json=payload, api=True)

def sanity_print_match(
    api: Garmin,
    aid: int,
    exp_start_unix: int,
    exp_dist: float,
    exp_dur: float,
    expected_gear_uuid: str | None = None,
    acts: list | None = None,
    cached_gear_payload: dict | list | None = None,
):
    """
    Printer:
      - TCX vs Garmin (summary) diffs
      - noen nøkkelstats fra summary
      - gear-status fra lokal DB (samme DB som SHA256->activityId)

    Forutsetter at du har:
      - parse_start_gmt()
      - load_db()  (returnerer dict med "gear_by_activity")
      
    If acts is provided, use it (from cache). Otherwise fetch from API.
    If cached_gear_payload is provided, use it (from cache). Otherwise fetch from API.
    This allows callers to cache and reuse get_activities() and get_activity_gear() results.
    """
    if acts is None:
        acts = api.get_activities(0, ACTIVITY_PAGE_SIZE)
    a = next((x for x in acts if x.get("activityId") == aid), None)

    if not a:
        print("SANITY: Fant ikke activityId i get_activities-lista (kan være paging).")
        return

    def fmt(x):
        if x is None:
            return None
        try:
            xf = float(x)
            # HR/power/distance/duration ser bedre ut med én desimal
            if abs(xf) >= 10:
                return f"{xf:.1f}"
            return f"{xf:.3f}"
        except Exception:
            return x

    def type_key(obj):
        return (
            (obj.get("activityType") or {}).get("typeKey")
            or (obj.get("activityTypeDTO") or {}).get("typeKey")
            or obj.get("activityTypeKey")
        )

    # ---- Start time ----
    st = a.get("startTimeGMT")
    st_unix = None
    if st:
        try:
            st_unix = parse_start_gmt(st)
        except Exception:
            st_unix = None

    # ---- Distance & duration ----
    got_dist = a.get("distance")
    got_dur = a.get("duration")

    print("\nSANITY: TCX vs Garmin (summary)")
    print(f"- activityId: {aid}")

    if st_unix is not None:
        print(f"- startTimeGMT: {st}  (Δ {st_unix - exp_start_unix:+d}s)")
    else:
        print(f"- startTimeGMT: {st}  (kunne ikke parse til unix)")

    if got_dist is not None:
        d = float(got_dist) - float(exp_dist)
        print(f"- distance:    got={fmt(got_dist)} m, exp={fmt(exp_dist)} m  (Δ {d:+.1f} m)")
    else:
        print(f"- distance:    got=None, exp={fmt(exp_dist)} m")

    if got_dur is not None:
        d = float(got_dur) - float(exp_dur)
        print(f"- duration:    got={fmt(got_dur)} s, exp={fmt(exp_dur)} s  (Δ {d:+.1f} s)")
    else:
        print(f"- duration:    got=None, exp={fmt(exp_dur)} s")

    # ---- Metadata ----
    print(f"- typeKey:     {type_key(a)}")
    print(f"- eventType:   {a.get('eventType')}")
    print(f"- name:        {a.get('activityName')}")
    print(f"- calories:    {fmt(a.get('calories'))}")

    # ---- Stats (summary) ----
    for k in ["averageHR", "maxHR", "averagePower", "maxPower", "normalizedPower"]:
        if k in a:
            print(f"- {k}: {fmt(a.get(k))}")

    # ---- Gear (lokal DB-cache) ----
    try:
        db = load_db()
        g = db.get("gear_by_activity", {}).get(str(aid))
        print(f"- gear (cache): {g if g else 'ukjent'}")
    except Exception as e:
        print(f"- gear (cache): kunne ikke leses ({type(e).__name__}: {e})")

    # ---- Gear (Garmin API, best effort) ----
    try:
        if cached_gear_payload is not None:
            gear_payload = cached_gear_payload
        else:
            gear_payload = api.get_activity_gear(aid)

        items = []
        if isinstance(gear_payload, list):
            items = gear_payload
        elif isinstance(gear_payload, dict):
            for key in ("gear", "gearDTOs", "activityGear", "items"):
                v = gear_payload.get(key)
                if isinstance(v, list):
                    items = v
                    break

        if not items:
            print("- gear (api): ingen registrert")
        else:
            rendered = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                gid = item.get("uuid") or item.get("gearUUID") or item.get("gearUuid") or item.get("id")
                gname = (
                    item.get("displayName")
                    or item.get("gearName")
                    or item.get("customMakeModel")
                    or item.get("nickname")
                    or "ukjent"
                )
                rendered.append(f"{gname} ({gid if gid else 'uten-uuid'})")

            if rendered:
                print(f"- gear (api): {', '.join(rendered)}")
            else:
                print("- gear (api): data mottatt, men ukjent format")
    except Exception as e:
        print(f"- gear (api): utilgjengelig ({type(e).__name__}: {e})")

    # ---- Gear linkage check (mer robust) ----
    if expected_gear_uuid:
        try:
            used_by_gear = api.get_gear_activities(expected_gear_uuid, limit=1000)
            found = False
            if isinstance(used_by_gear, list):
                for entry in used_by_gear:
                    if not isinstance(entry, dict):
                        continue
                    entry_aid = entry.get("activityId") or entry.get("activity_id") or entry.get("id")
                    if entry_aid is not None and int(entry_aid) == int(aid):
                        found = True
                        break
            print(f"- gear (expected link): {'OK' if found else 'IKKE funnet i gear-historikk'} ({expected_gear_uuid})")
        except Exception as e:
            print(f"- gear (expected link): utilgjengelig ({type(e).__name__}: {e})")

    # ---- Optional: details stats (ikke gear) ----
    try:
        details = api.get_activity_details(aid)
        for k in [
            "averageHR", "maxHR",
            "averagePower", "maxPower",
            "normalizedPower",
            "averageCadence", "maxCadence",
            "averageStrokeCadence", "maxStrokeCadence",
        ]:
            if k in details:
                print(f"- {k} (details): {fmt(details.get(k))}")
    except Exception:
        # Ikke kritisk; summary-print er hovedpoenget
        pass

def find_existing_activity(api: Garmin, exp_start_unix: int, exp_distance_m: float, exp_duration_s: float, acts: list | None = None):
    """Find existing activity by matching start time, distance, and duration.
    
    If acts is provided, use it (from cache). Otherwise fetch from API.
    This allows callers to cache and reuse get_activities() results.
    """
    if acts is None:
        acts = api.get_activities(0, ACTIVITY_PAGE_SIZE)

    dist_tol = 50.0
    dur_tol = 15.0
    start_tol = 120

    for a in acts:
        st = a.get("startTimeGMT")
        if not st:
            continue
        try:
            st_unix = parse_start_gmt(st)
        except Exception:
            continue

        dist = a.get("distance")
        dur = a.get("duration")
        if dist is None or dur is None:
            continue

        if abs(st_unix - exp_start_unix) <= start_tol \
           and abs(float(dist) - exp_distance_m) <= dist_tol \
           and abs(float(dur) - exp_duration_s) <= dur_tol:
            return a["activityId"]

    return None

def needs_patch(summary: dict, title: str, event_type: str, desired_type_key: str | None) -> dict:
    """
    Returnerer hvilke ting som må oppdateres.
    """
    result = {
        "type": False,
        "title": False,
        "event": False,
    }

    # Type
    type_key = (
        (summary.get("activityType") or {}).get("typeKey")
        or (summary.get("activityTypeDTO") or {}).get("typeKey")
        or summary.get("activityTypeKey")
    )
    if desired_type_key is not None and type_key != desired_type_key:
        result["type"] = True

    # Title
    if summary.get("activityName") != title:
        result["title"] = True

    # Event type
    ev = (
        (summary.get("eventType") or {}).get("typeKey")
        or (summary.get("eventTypeDTO") or {}).get("typeKey")
    )

    if ev != event_type:
        result["event"] = True

    return result

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()

def is_conflict_409(e: Exception) -> bool:
    # Robust: sjekk response.status_code hvis den finnes (requests/garth), ellers fall tilbake på tekst.
    resp = getattr(e, "response", None)
    if resp is not None:
        code = getattr(resp, "status_code", None)
        if code == 409:
            return True

    msg = str(e)
    return (" 409 " in f" {msg} ") or ("409" in msg and "Conflict" in msg)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tcx", help="Kinomap TCX file")
    ap.add_argument("--race", action="store_true", help="Set event type to race (default: training)")
    ap.add_argument("--sanity", action="store_true", help="Print TCX vs Garmin diffs + cache info")
    ap.add_argument("--show-config", action="store_true", help="Print resolved config before matching/upload")
    ap.add_argument("--force-upload", action="store_true", help="Try upload even if we find duplicates (may hit 409)")
    ap.add_argument("--dry-run", action="store_true", help="Parse + duplicate-check only; do not upload or patch")
    args = ap.parse_args()

    tcx = Path(args.tcx)
    if not tcx.exists():
        raise SystemExit(f"Fant ikke: {tcx}")

    # Load credentials from ~/.config/kinomap_to_garmin.env if present
    load_env_file(BASE_DIR / ".config" / "kinomap_to_garmin.env")

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        raise SystemExit("Sett GARMIN_EMAIL og GARMIN_PASSWORD.")

    # Desired metadata
    event_type = "race" if args.race else "training"

    # Parse TCX metadata
    exp_start_unix, exp_dist, exp_duration, tcx_sport, creator_name = parse_tcx_metadata(tcx)
    desired_type_key = resolve_desired_activity_type(tcx_sport, creator_name)
    title = f"{resolve_title_prefix(tcx_sport, creator_name)}{tcx.stem}"
    desired_gear_uuid = resolve_gear_uuid(tcx_sport, creator_name)

    if args.show_config:
        print_run_config(
            tcx=tcx,
            tcx_sport=tcx_sport,
            creator_name=creator_name,
            desired_type_key=desired_type_key,
            event_type=event_type,
            title=title,
            desired_gear_uuid=desired_gear_uuid,
            force_upload=args.force_upload,
            dry_run=args.dry_run,
        )
        print()

    # Login
    api = Garmin(email, password)
    api.login()

    # ---- DRY RUN ----
    if args.dry_run:
        print("DRY RUN:")
        print(f"- tcx_sport:  {tcx_sport or 'ukjent'}")
        print(f"- creator:    {creator_name or 'ukjent'}")
        print(f"- type_target:{desired_type_key if desired_type_key else 'behold importert type'}")
        print(f"- gear_target:{desired_gear_uuid}")
        print(f"- start_unix: {exp_start_unix}")
        print(f"- distance:   {exp_dist}")
        print(f"- duration:   {exp_duration}")

        file_hash = sha256_file(tcx)
        db = load_db()
        hash_to_activity = db.get("hash_to_activity", {})
        aid = None

        if not args.force_upload and file_hash in hash_to_activity:
            candidate = hash_to_activity[file_hash]
            if activity_exists(api, candidate):
                aid = candidate
                print(f"- SHA256 match: {aid}")
            else:
                print(f"- SHA256 match pekte på slettet/ukjent activityId={candidate} (vil falle tilbake til matching/upload)")

        # Cache get_activities() result for reuse in matching and sanity checks (issue #9 optimization)
        acts = None
        if aid is None and not args.force_upload:
            acts = api.get_activities(0, ACTIVITY_PAGE_SIZE)
            existing_id = find_existing_activity(api, exp_start_unix, exp_dist, exp_duration, acts=acts)
            if existing_id:
                aid = existing_id
                print(f"- Matching treff: {aid}")

        if aid is None:
            print("- Ingen eksisterende aktivitet funnet (ville lastet opp)")

        if args.sanity and aid is not None:
            sanity_print_match(api, aid, exp_start_unix, exp_dist, exp_duration, desired_gear_uuid, acts=acts)

        return

    # ---- Determine aid: SHA256 -> matching -> upload (with 409 fallback) ----
    file_hash = sha256_file(tcx)
    db = load_db()
    hash_to_activity = db.setdefault("hash_to_activity", {})

    aid = None
    acts = None  # Cache get_activities() result for reuse (issue #9 optimization)

    # 1) SHA256 first
    if not args.force_upload and file_hash in hash_to_activity:
        candidate = hash_to_activity[file_hash]
        if activity_exists(api, candidate):
            aid = candidate
            print(f"SHA256 match: bruker tidligere activityId={aid}")
        else:
            print(
                f"NB: SHA256 match pekte på slettet/ukjent activityId={candidate}. Fjerner fra db.",
                file=sys.stderr,
            )
            del hash_to_activity[file_hash]
            save_db(db)

    # 2) Fallback: deterministic matching (with cached activities)
    if aid is None and not args.force_upload:
        acts = api.get_activities(0, ACTIVITY_PAGE_SIZE)  # Fetch once, reuse
        existing_id = find_existing_activity(api, exp_start_unix, exp_dist, exp_duration, acts=acts)
        if existing_id:
            aid = existing_id
            print(f"Fant eksisterende aktivitet via matching: {aid}")

    # 3) Upload if needed (or forced)
    if aid is None:
        print("Laster opp TCX…")
        try:
            api.upload_activity(str(tcx))
            aid = find_uploaded_activity(api, exp_start_unix, exp_dist, exp_duration, timeout_s=90)
        except Exception as e:
            # Force-upload can legitimately hit Garmin duplicate protection (409)
            if is_conflict_409(e):
                print("NB: Upload ga 409 Conflict (duplikat). Faller tilbake til matching…", file=sys.stderr)
                # Reuse cached acts if available, otherwise fetch
                if acts is None:
                    acts = api.get_activities(0, ACTIVITY_PAGE_SIZE)
                existing_id = find_existing_activity(api, exp_start_unix, exp_dist, exp_duration, acts=acts)
                if not existing_id:
                    raise RuntimeError(
                        "Fikk 409 Conflict, men klarte ikke finne eksisterende aktivitet via matching."
                    ) from e
                aid = existing_id
            else:
                raise

    # ✅ Persist hash->aid so next run becomes a SHA hit (even if we matched/uploaded/409-fellbacked)
    prev = hash_to_activity.get(file_hash)
    if prev != aid:
        hash_to_activity[file_hash] = aid
        save_db(db)

    # ---- Fetch summary and patch only if needed ----
    # Reuse cached acts if available, otherwise fetch (issue #9 optimization)
    if acts is None:
        acts = api.get_activities(0, ACTIVITY_PAGE_SIZE)
    summary = next((x for x in acts if x.get("activityId") == aid), None)
    if not summary:
        raise RuntimeError("Fant ikke aktivitet i get_activities-lista etter at aid ble bestemt.")

    patch_flags = needs_patch(summary, title, event_type, desired_type_key)

    if patch_flags.get("type") and desired_type_key is not None:
        try:
            set_activity_type(api, aid, desired_type_key)
        except Exception as e:
            print("NB: Klarte ikke sette Aktivitetstype:", e, file=sys.stderr)

    if patch_flags.get("title"):
        try:
            api.set_activity_name(aid, title)
        except Exception as e:
            print("NB: Klarte ikke sette tittel:", e, file=sys.stderr)

    if patch_flags.get("event"):
        try:
            set_event_type(api, aid, event_type)
        except Exception as e:
            print("NB: Klarte ikke sette Hendelsestype:", e, file=sys.stderr)

    # ---- Gear (idempotent via lokal cache + single-gear policy) ----
    try:
        gear_status = ensure_gear_cached(api, aid, desired_gear_uuid)
        print("Gear: allerede satt (cache)" if gear_status == "already" else "Gear: lagt til")
        
        # Fetch gear payload AFTER ensure_gear_cached to avoid stale data (issue #6 optimization)
        gear_payload = api.get_activity_gear(aid)

        single = enforce_single_gear(api, aid, desired_gear_uuid, gear_payload=gear_payload)
        if single["removed"]:
            print(f"Gear: fjernet ekstra koblinger ({', '.join(single['removed'])})")
        if single["failed"]:
            print(
                "NB: Klarte ikke fjerne alle ekstra gear-koblinger: "
                + "; ".join([f"{gid} [{err}]" for gid, err in single["failed"]]),
                file=sys.stderr,
            )
    except Exception as e:
        print("NB: Klarte ikke sette utstyr:", e, file=sys.stderr)
        gear_payload = None  # Reset cache if error occurred

    # ---- Sanity ----
    if args.sanity:
        time.sleep(2)
        # Reuse cached acts and gear_payload if available (issue #5 and #9 optimizations)
        sanity_print_match(api, aid, exp_start_unix, exp_dist, exp_duration, desired_gear_uuid, acts=acts, cached_gear_payload=gear_payload)

    print(f"OK: activityId={aid}")
    print(f"TCX sport: {tcx_sport or 'ukjent'}")
    print(f"Activity type target: {desired_type_key if desired_type_key else 'behold importert type'}")
    print(f"Title: {title}")
    print(f"Event type: {event_type}")
    print(f"Gear UUID: {desired_gear_uuid}")

if __name__ == "__main__":
    main()
