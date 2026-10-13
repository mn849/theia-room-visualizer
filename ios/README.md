# Native iPhone capture module

The production room scanner should be a native iOS component built with Swift, ARKit, RealityKit, and Apple's RoomPlan framework. RoomPlan is the system capability that turns a LiDAR-assisted scan into structured room geometry and detected objects.

Planned responsibilities:

1. Confirm the device has a supported LiDAR scanner.
2. Guide a slow sweep around the room and show coverage feedback.
3. Save a `CapturedRoom`/USDZ representation with walls, openings, surfaces, and detected furniture.
4. Run quality checks for incomplete walls, poor tracking, reflective surfaces, and insufficient floor coverage.
5. Export a compact JSON representation and optional preview for the web app.

The first version should target iPhone 12 Pro/Pro Max and later Pro/Pro Max generations with LiDAR. The exact supported model list should be checked against Apple's current documentation when the Xcode project is created.
