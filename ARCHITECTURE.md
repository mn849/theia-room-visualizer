# Theia architecture decision

Theia is native-first. The iPhone LiDAR capture is the source of truth for room geometry; the desktop web companion is the source of truth for Marketplace browsing and visualization.

## Data flow

1. The iPhone app uses RoomPlan to scan a room and validate coverage.
2. The app saves a room record locally and exports a versioned room-model payload.
3. The desktop companion loads the selected room model.
4. A user opens a Marketplace listing on their laptop and explicitly sends a listing image or URL to Theia.
5. The companion extracts the item, estimates dimensions with uncertainty, and renders it against the scaled room.
6. The user can return to the phone to rescan or correct the room model.

## Product boundaries

The browser integration should be user initiated. The first version should accept a pasted URL, screenshot, or selected listing image. A browser extension can add a “Send to Theia” action later, without requiring automated Marketplace scraping or Facebook login automation.

## Why this split

RoomPlan and LiDAR require native iOS APIs. A browser cannot provide equivalent metric capture. Marketplace browsing is more comfortable on a laptop, where users can compare listings and see a larger visualization. Keeping these responsibilities separate lets each surface use the right platform.

## Quality rules

- Preserve the RoomPlan coordinate system and units through every export/import.
- Keep seller-provided dimensions, measured dimensions, and inferred dimensions separate.
- Render a range when the listing does not establish scale.
- Never present an inferred measurement as exact.
- Keep room images and geometry private by default, with explicit export/delete actions.
