#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${HERE}/.venv"
REQ_FILE="${HERE}/requirements.txt"
REQ_HASH_FILE="${VENV}/.requirements.sha256"

usage() {
  cat <<EOF
Usage:
  ./run_in_venv.sh <script.py> [script-args...]

Examples:
  ./run_in_venv.sh kinomap_to_garmin_secure.py "file.tcx" --show-config --sanity
  ./run_in_venv.sh fix_historical_treadmill_activities.py --apply
EOF
}

SCRIPT_REL="${1:-}"
if [[ -z "${SCRIPT_REL}" ]]; then
  usage
  exit 1
fi
shift

if [[ "${SCRIPT_REL}" = /* ]]; then
  SCRIPT_PATH="${SCRIPT_REL}"
else
  SCRIPT_PATH="${HERE}/${SCRIPT_REL}"
fi

if [[ ! -f "${SCRIPT_PATH}" ]]; then
  echo "ERROR: Script not found: ${SCRIPT_REL}" >&2
  usage
  exit 1
fi

created_venv=0
if [[ ! -x "${VENV}/bin/python" ]]; then
  python3 -m venv "${VENV}"
  created_venv=1
fi

if [[ -f "${REQ_FILE}" ]]; then
  req_hash="$(sha256sum "${REQ_FILE}" | awk '{print $1}')"
  existing_hash=""
  if [[ -f "${REQ_HASH_FILE}" ]]; then
    existing_hash="$(cat "${REQ_HASH_FILE}")"
  fi

  if [[ ${created_venv} -eq 1 || "${req_hash}" != "${existing_hash}" ]]; then
    "${VENV}/bin/python" -m pip install -U pip >/dev/null
    "${VENV}/bin/python" -m pip install -r "${REQ_FILE}" >/dev/null
    printf '%s\n' "${req_hash}" > "${REQ_HASH_FILE}"
  fi
fi

exec "${VENV}/bin/python" "${SCRIPT_PATH}" "$@"
