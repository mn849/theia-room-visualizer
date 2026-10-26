# Theia

Theia is a source-faithful room-visualization app for evaluating Facebook Marketplace furniture, art, and other objects in a real home. Upload a room photo, paste a public Marketplace listing URL or image URL, and compare several supplied items together in a calibrated 2D preview.

## Current status

The app currently focuses on Facebook Marketplace so its extraction rules can be tested against one consistent source. It reads public Open Graph, JSON-LD, and Marketplace bootstrap metadata, extracts seller-stated dimensions, and uses a narrow gpt-6-luna listing agent (the course-approved model) for category/pose/warning classification. Room uploads receive EXIF correction and optional panorama stitching. Users can click four visible floor corners for a planar correction, confirm room width/length/ceiling height in common units, and keep room/item libraries for the session. Item cutouts use an optional locally cached U²-Net model when available, with a conservative OpenCV fallback. It does not invent item details, authenticate to Facebook, scrape private listings, or claim that a 2D overlay proves fit. The agent falls back deterministically when no key is configured.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

Open <http://127.0.0.1:8050> in a browser. Upload a room photo, paste a listing URL (or direct image URL), then choose **Add item to room**. Add several items, choose their pose, and drag them over the room preview. Set `PORTKEY_API_KEY` in the root `.env` (or the folder above the repo) to enable gpt-6-luna calls; never commit the key.

## 3D room view

The preview is a 3D reconstruction of your room photo that opens at the exact viewpoint the photo was taken from, so it first looks like the photo itself.

- **2D photo / 3D room** toggle at the top of the preview (2D is the default).
- **3D walk-through:** opens at the photo's own viewpoint. Drag to look around, scroll or W/S to walk, A/D to step sideways, and **Reset view** returns to the photo. Movement stays within about 2 m of where the photo was taken, the range a single photo can show convincingly.
- **Drag an item** to move it on the floor. Upright items snap flush against a wall (hold **Alt** to stop snapping); rugs lie flat; art (“Facing the wall”) slides along walls. **Q / E** or the rotation slider turn the selected item. A red footprint means the item does not fit inside the floor.
- **Better accuracy:** click the four floor corners and confirm one room measurement. Without clicks, Theia estimates the camera tilt from vertical edges in the photo and assumes a 1.45 m phone height. The note under the preview always says which evidence was used.
- **Items stay faithful:** an item is always the seller's own photo at the seller's dimensions. In each item card you can switch between the cutout and the untouched seller photo, and tag extra listing photos (left side, back, …); while orbiting, Theia shows the real photo nearest your viewing angle. Sides with no listing photo are drawn as a grey silhouette instead of being invented.
- three.js is loaded from the jsDelivr CDN, so the 3D view needs an internet connection; without it the 2D preview is shown.

## Up to 15 items, saved on this device

Add up to 15 listings to one room; they stay where you put them when you add more. Rooms, items, positions and hearts are saved in the browser (local storage), so they are still there after a reload or restart. Images are kept on the server for 7 days (`THEIA_MEDIA_TTL_HOURS`). At 15 items Theia asks you to remove one before adding another.

## Heart an item → message the seller

Click **♡ Love it** on an item card to save it. Theia drafts a short 2–4 sentence first message to the seller: it asks if the item is still available, asks for any measurement the listing is missing, and offers to pick it up. You can edit the text, choose **Try another wording**, **Copy message**, and open the listing to paste it into Messenger. Theia never sends messages itself. Drafts come from `message_agent.py` (gpt-6-luna). The agent never invents a price or condition, and its reply is rejected if it isn't 2–4 sentences or contains a link or phone number. With no API key it uses a natural-sounding template instead.

### Optional local models (not needed to run the app)

```bash
pip install "rembg[cpu]" onnxruntime   # sharper cutouts + depth inference
python scripts/download_models.py       # ISNet cutout model + Depth Anything V2 Small (~270 MB)
```

With the depth model present, the room photo also becomes a depth-relief mesh, so existing furniture stands up in 3D and hides items placed behind it. Everything runs locally; no photo is sent to a third party.

### Tests

```bash
python tests/test_geometry.py   # camera/floor recovery against synthetic rooms
python tests/test_cutout.py     # spindle gaps stay transparent, thin legs and pale seats stay solid
```

## Deploy on Render

The repository includes `render.yaml` and `Procfile`. Create a new Render Web Service from this repository, keep the detected build/start commands, and add `PORTKEY_API_KEY` as a secret environment variable. Render supplies `PORT` automatically; the app binds to it through Gunicorn. The free service may sleep between visits, and session libraries reset when a browser session closes.

## Project map

- `app.py` — Dash application, public metadata reader, room calibration, and placement workflow
- `message_agent.py` — gpt-6-luna seller-message drafter for hearted items (validated, template fallback)
- `listing_agent.py` — gpt-6-luna classifier, schema validation, safety fallback, and token log writer
- `assets/style.css` — visual system and responsive layout
- `assets/placement.js` — 2D fallback drag behaviour (used only if the 3D viewer cannot load)
- `assets/scene3d.js` — three.js 3D room viewer (walk-through camera, item dragging, wall snapping)
- `room_geometry.py` — single-photo camera + floor + room-box reconstruction
- `depth_model.py` — optional Depth Anything V2 relief for the 3D room
- `item_cutout.py` — source-faithful cutouts (soft alpha, colour-aware hole repair)
- `media_store.py` — full-resolution image cache served at `/media/<id>` (auto-purged after 24 h)
- `logs/pipeline_events.jsonl` — non-sensitive cutout/3D reconstruction records (method, coverage, camera estimate)
- `ios/` — native RoomPlan capture design and implementation notes
- `report.html` — self-contained course report with architecture, tokenomics, safety, limits, and reproduction notes
- `slides.html` — self-contained ten-slide presentation
- `logs/agent_calls.jsonl` — local redacted usage/fallback evidence (created when listings are added)

## Planned milestones

1. 2D source-faithful placement (done; now the fallback view): room image, public listing metadata/image, transparent object cutout, floor-anchored drag placement, and explicit limitations.
2. Automatic item scale (current): extract seller-stated dimensions from public metadata/description, otherwise use a labeled category estimate and a default room-size reference; users can replace the default with one confirmed room-width measurement for the session.
3. Multi-angle room capture (current): upload overlapping room views; Theia corrects each view and attempts a feature-based panorama, while preserving the original angles in session storage.
4. Session room and item libraries (current): keep room sets and up to 12 placed items in browser session storage so objects can be compared together.
5. Better item cutouts (current): full-resolution, soft-alpha cutouts with colour-aware hole repair and a one-click switch back to the untouched seller photo.
6. 3D room (current): single-photo camera/floor reconstruction, walk-through 3D view, wall snapping, view-dependent listing photos.
7. Scale and fit: use seller dimensions only when present; otherwise label the overlay as unscaled.
8. Native iPhone RoomPlan capture and optional AR preview.
9. Evidence and hardening: real-user tests, blocked/private URL tests, screenshots, and fresh-clone dry run.

## Privacy and safety direction

Room images and listing URLs may reveal private spaces or accounts. The app should process capture data locally where practical, minimize retention, and give users explicit delete/export controls. Dimensions and item attributes must show their source and confidence instead of presenting guesses as measurements.
