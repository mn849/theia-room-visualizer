# LumaRoom iPhone app

This is the primary product surface for room capture. It will be a native SwiftUI app using Apple RoomPlan and ARKit on LiDAR-equipped iPhones.

The current machine has Swift command-line tools but does not have the full Xcode application installed, so the Xcode project cannot be built or deployed from this environment yet. The first native implementation should be opened and run on a Mac with Xcode and a supported iPhone.

## First native milestone

- Check LiDAR/device support.
- Guide a slow room sweep with coverage feedback.
- Run `RoomCaptureSession` and display the live `RoomCaptureView`.
- Finalize a scan into a `CapturedRoom`.
- Show a review screen with walls, openings, detected furniture, and dimensions.
- Save a named room locally.
- Export a versioned room-model payload for the desktop companion.

## Device target

iPhone 12 Pro/Pro Max and later Pro/Pro Max generations with LiDAR. Verify Apple's current supported-device documentation when creating the Xcode target.
