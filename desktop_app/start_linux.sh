#!/bin/sh
cd "$(dirname "$0")/.." || exit 1
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is needed for the source launcher; use the standalone Linux release instead."
  exit 1
fi
exec python3 desktop_app/launch.py
