#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Install it first, for example:  sudo apt install python3 python3-venv"
  exit 1
fi
python3 unfollow_app.py "$@"
