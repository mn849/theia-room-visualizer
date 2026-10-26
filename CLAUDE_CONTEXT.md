# Theia project handoff

Theia is a Facebook Marketplace focused room-visualization Dash app. It supports room uploads, session room/item libraries, listing metadata and dimensions, multiple listing images, furniture cutouts, unit conversion, click-and-drag floor point calibration, perspective-aware item scaling, and a gpt-6-luna advisory listing agent and seller-message agent through Portkey.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python app.py
```

The production command is in `Procfile`; Render configuration is in `render.yaml`. `PORTKEY_API_KEY` must remain an environment variable.

## Important files

- `app.py`: Dash application, Marketplace metadata parsing, dimensions, cutouts, room calibration, preview.
- `listing_agent.py`: gpt-6-luna Responses API agent, safety validation, fallback, and logs.
- `assets/calibration.js`: labelled draggable floor points.
- `assets/placement.js`: item drag and depth scaling.
- `assets/style.css`: Theia visual system.
- `report.html`: course report.
- `slides.html`: presentation.
- `README.md`: setup and deployment instructions.

## Current known limits

Facebook Marketplace pages can be login-gated and dynamically rendered. Seller dimensions remain primary; missing dimensions are labelled rather than invented. Cutouts are assistive and should be reviewed. The app is a 2D visualisation prototype, not a complete 3D reconstruction.
