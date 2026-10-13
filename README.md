# LumaRoom

LumaRoom is a room-scanning and furniture-visualization app for evaluating secondhand furniture in a real home. The current milestone is a polished room-capture prototype. It accepts a room photo, creates a reviewable draft baseline, and reserves the data boundary that the native iPhone RoomPlan scanner will use.

## Current status

The web prototype is the first milestone. It demonstrates the capture flow, progress feedback, room-model review state, confidence language, and reusable-room concept. It does not claim to measure a room from a single photo. The native iOS module in `ios/` is the planned source of scaled geometry.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Open <http://127.0.0.1:8050> in a browser. Upload a JPG or PNG room photo, then choose **Create room model**.

For the course grading environment, the app should load `.env` from either its own project directory or the parent submissions directory. The current prototype does not make model calls yet; future integrations must use a key supplied through those locations and must never commit `.env`.

## Project map

- `app.py` — Dash application and capture workflow
- `assets/style.css` — visual system and responsive layout
- `ios/` — native RoomPlan capture design and implementation notes
- `report.html` — report scaffold; evidence will be added as features are tested
- `slides.html` — reveal.js slide scaffold
- `requirements.txt` — pinned dependency ranges
- `.env.example` — required environment variable names without values

## Planned milestones

1. Room capture prototype (current): upload flow, progress state, draft preview, confidence language.
2. Native iPhone RoomPlan capture: scan walls, floors, openings, and furniture; export a structured room model.
3. Saved rooms: name, reopen, and update a room scan.
4. Furniture intake: user-provided Marketplace screenshots or URL, extraction, and uncertainty-aware dimensions.
5. Placement: scaled furniture previews and fit checks.
6. Evidence and hardening: real-user test, token/cost logs, safety tests, screenshots, and fresh-clone dry run.

## Privacy and safety direction

Room images and scans may reveal private spaces. The eventual app should process capture data on-device where practical, minimize retention, and give users an explicit delete/export control. Dimension estimates must show their source and confidence instead of presenting guesses as measurements.
