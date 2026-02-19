#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${HERE}/.venv"

# Create virtualenv if missing
if [[ ! -x "${VENV}/bin/python" ]]; then
  python3 -m venv "${VENV}"
  "${VENV}/bin/python" -m pip install -U pip
  "${VENV}/bin/python" -m pip install garminconnect garth
fi

exec "${VENV}/bin/python" "${HERE}/kinomap_to_garmin_secure.py" "$@"
