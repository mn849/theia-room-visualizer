#!/bin/bash
# Double-click this file in Finder to start Theia, then open http://127.0.0.1:8050
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -r requirements.txt
fi
source .venv/bin/activate
(sleep 4; open "http://127.0.0.1:8050") &
python app.py
echo
echo "Theia stopped. Copy any error above and paste it to Claude."
read -n 1 -s -r -p "Press any key to close this window"
