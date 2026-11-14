#!/bin/bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. A window will ask to install the developer tools; accept it, then double-click this file again."
  xcode-select --install 2>/dev/null
  read -p "Press Enter to close..." _
  exit 1
fi
python3 unfollow_app.py "$@"
