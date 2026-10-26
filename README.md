# Theia

Theia is a source-faithful room-visualization app for evaluating Facebook Marketplace furniture, art, and other objects in a real home. Upload a room photo, paste a public Marketplace listing URL or image URL, and compare several supplied items together in a calibrated 2D preview.

## Current status

The app currently focuses on Facebook Marketplace so its extraction rules can be tested against one consistent source. It reads public Open Graph, JSON-LD, and Marketplace bootstrap metadata, extracts seller-stated dimensions, and uses a narrow GPT-6-Sol listing agent for category/pose/warning classification. Room uploads receive EXIF correction and optional panorama stitching. Users can click four visible floor corners for a planar correction, confirm room width/length/ceiling height in common units, and keep room/item libraries for the session. Item cutouts use an optional locally cached U²-Net model when available, with a conservative OpenCV fallback. It does not invent item details, authenticate to Facebook, scrape private listings, or claim that a 2D overlay proves fit. The agent falls back deterministically when no key is configured.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Open <http://127.0.0.1:8050> in a browser. Upload a room photo, paste a listing URL (or direct image URL), then choose **Add item to room**. Add several items, choose their pose, and drag them over the room preview. Set `PORTKEY_API_KEY` in the root `.env` to enable Sol calls; never commit the key.

## Deploy on Render

The repository includes `render.yaml` and `Procfile`. Create a new Render Web Service from this repository, keep the detected build/start commands, and add `PORTKEY_API_KEY` as a secret environment variable. Render supplies `PORT` automatically; the app binds to it through Gunicorn. The free service may sleep between visits, and session libraries reset when a browser session closes.

## Project map

- `app.py` — Dash application, public metadata reader, room calibration, and placement workflow
- `listing_agent.py` — GPT-6-Sol classifier, schema validation, safety fallback, and token log writer
- `assets/style.css` — visual system and responsive layout
- `assets/placement.js` — client-side drag behavior for the item overlay
- `ios/` — native RoomPlan capture design and implementation notes
- `report.html` — self-contained course report with architecture, tokenomics, safety, limits, and reproduction notes
- `slides.html` — self-contained ten-slide presentation
- `logs/agent_calls.jsonl` — local redacted usage/fallback evidence (created when listings are added)

## Planned milestones

1. 2D source-faithful placement (current): room image, public listing metadata/image, transparent object cutout, floor-anchored drag placement, and explicit limitations.
2. Automatic item scale (current): extract seller-stated dimensions from public metadata/description, otherwise use a labeled category estimate and a default room-size reference; users can replace the default with one confirmed room-width measurement for the session.
3. Multi-angle room capture (current): upload overlapping room views; Theia corrects each view and attempts a feature-based panorama, while preserving the original angles in session storage.
4. Session room and item libraries (current): keep room sets and up to 12 placed items in browser session storage so objects can be compared together.
5. Better item cutouts: user-confirmed background removal with a visible edit/undo step; the current GrabCut result is an assistive preview and can fail on complex backgrounds.
6. Scale and fit: use seller dimensions only when present; otherwise label the overlay as unscaled.
7. Native iPhone RoomPlan capture and optional AR preview.
8. Evidence and hardening: real-user tests, blocked/private URL tests, screenshots, and fresh-clone dry run.

## Privacy and safety direction

Room images and listing URLs may reveal private spaces or accounts. The app should process capture data locally where practical, minimize retention, and give users explicit delete/export controls. Dimensions and item attributes must show their source and confidence instead of presenting guesses as measurements.
