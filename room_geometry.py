"""Single-photo room reconstruction: camera, floor plane, and a room box.

Coordinate conventions
----------------------
* Image: pixel coordinates centred on the principal point (assumed to be the
  image centre), ``u`` to the right and ``v`` downwards.
* OpenCV camera ("cv"): x right, y down, z forward.
* World (three.js): metres, y up, the floor is y = 0, the camera stands at
  x = z = 0 and looks roughly toward -z.

How the camera is recovered (best available evidence first)
-----------------------------------------------------------
1. Focal length: the photo's EXIF 35 mm-equivalent focal length, else the
   focal length implied by the four clicked floor corners (a rectangle's
   two vanishing points must be orthogonal), else a typical 26 mm phone lens.
2. Floor plane: from the four clicked floor corners (homography of a
   rectangle), else the vertical vanishing point found from straight vertical
   edges in the photo (gives pitch and roll), else a level camera.
3. Metric scale: the user's confirmed room width, else a 1.45 m phone height.

The result is a pure-JSON scene description used by assets/scene3d.js.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

PHONE_HEIGHT_M = 1.45
DEFAULT_F35 = 26.0
FULL_FRAME_DIAGONAL_MM = 43.27


def focal_px_from_f35(f35: float, width: int, height: int) -> float:
    return float(f35) / FULL_FRAME_DIAGONAL_MM * math.hypot(width, height)


def exif_focal_35mm(image) -> float | None:
    """Read FocalLengthIn35mmFilm (tag 41989) before EXIF data is stripped."""
    try:
        exif = image.getexif()
        value = exif.get_ifd(0x8769).get(41989) or exif.get(41989)
        value = float(value) if value else None
        return value if value and 8 <= value <= 200 else None
    except Exception:
        return None


def _homography(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    rows = []
    for (x, y), (u, v) in zip(src, dst):
        rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, _, vt = np.linalg.svd(np.asarray(rows, dtype=np.float64))
    matrix = vt[-1].reshape(3, 3)
    return matrix / matrix[2, 2]


def _normalize(vector: np.ndarray) -> np.ndarray:
    return vector / max(1e-12, float(np.linalg.norm(vector)))


def vertical_up_from_lines(rgb: np.ndarray, focal: float) -> tuple[np.ndarray | None, int]:
    """Estimate the world-up direction (cv camera coords) from vertical edges."""
    if cv2 is None:
        return None, 0
    height, width = rgb.shape[:2]
    scale = min(1.0, 1000 / max(height, width))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if scale < 1:
        gray = cv2.resize(gray, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 60, 160)
    min_len = 0.06 * math.hypot(*gray.shape)
    segments = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=40, minLineLength=min_len, maxLineGap=4)
    if segments is None:
        return None, 0
    normals, weights = [], []
    for x1, y1, x2, y2 in segments.reshape(-1, 4).astype(np.float64) / scale:
        dx, dy = x2 - x1, y2 - y1
        if abs(dx) > abs(dy) * math.tan(math.radians(25)):
            continue  # only near-vertical segments
        a = np.array([(x1 - width / 2) / focal, (y1 - height / 2) / focal, 1.0])
        b = np.array([(x2 - width / 2) / focal, (y2 - height / 2) / focal, 1.0])
        normals.append(_normalize(np.cross(a, b)))
        weights.append(math.hypot(dx, dy))
    if len(normals) < 6:
        return None, len(normals)
    normals, weights = np.asarray(normals), np.asarray(weights)
    rng = np.random.default_rng(7)
    best, best_score = None, 0.0
    tolerance = math.sin(math.radians(1.2))
    for _ in range(300):
        i, j = rng.choice(len(normals), 2, replace=False)
        direction = np.cross(normals[i], normals[j])
        if np.linalg.norm(direction) < 1e-6:
            continue
        direction = _normalize(direction)
        inliers = np.abs(normals @ direction) < tolerance
        score = float(weights[inliers].sum())
        if score > best_score:
            best, best_score = inliers, score
    if best is None or best.sum() < 6:
        return None, 0
    weighted = normals[best] * np.sqrt(weights[best])[:, None]
    _, _, vt = np.linalg.svd(weighted)
    up = _normalize(vt[-1])
    if up[1] > 0:  # world up must point toward image-top (cv -y)
        up = -up
    if math.degrees(math.acos(min(1.0, -up[1]))) > 35:
        return None, int(best.sum())  # implausible tilt; do not trust it
    return up, int(best.sum())


def _pose_from_up(up: np.ndarray, cam_height: float, forward_hint: np.ndarray | None = None) -> dict[str, np.ndarray]:
    forward = np.array([0.0, 0.0, 1.0]) if forward_hint is None else forward_hint
    forward = _normalize(forward - (forward @ up) * up)
    z_axis = -forward
    x_axis = _normalize(np.cross(up, z_axis))
    return {"X": x_axis, "Y": up, "Z": z_axis, "foot": -cam_height * up, "height": cam_height}


def _to_world(pose: dict, point_cv: np.ndarray) -> np.ndarray:
    rel = point_cv - pose["foot"]
    return np.array([pose["X"] @ rel, pose["Y"] @ rel, pose["Z"] @ rel])


def _camera_json(pose: dict, focal: float, width: int, height: int) -> dict[str, Any]:
    # Columns of the three.js camera basis expressed in world coordinates.
    R = np.vstack([pose["X"], pose["Y"], pose["Z"]])  # rows: world axes in cv coords
    cv_x, cv_y, cv_z = R[:, 0], R[:, 1], R[:, 2]
    fov_y = math.degrees(2 * math.atan((height / 2) / focal))
    pitch = math.degrees(math.asin(float(np.clip(R[1, 2], -1, 1))))  # forward's world-up component
    roll = math.degrees(math.atan2(float(R[1, 0]), -float(R[1, 1])))
    return {
        "position": [0.0, float(pose["height"]), 0.0],
        "basis": {"x": cv_x.tolist(), "y": (-cv_y).tolist(), "z": (-cv_z).tolist()},
        "fov_y": fov_y, "focal_px": focal, "width": width, "height": height,
        "pitch_deg": pitch, "roll_deg": roll,
    }


def _ray_floor(pose: dict, u: float, v: float, focal: float) -> np.ndarray | None:
    ray = np.array([u / focal, v / focal, 1.0])
    denom = float(pose["Y"] @ ray)
    if denom >= -1e-6:
        return None  # ray at or above the horizon
    t = -pose["height"] / denom
    return _to_world(pose, ray * t)


def _line_at_z(a: np.ndarray, b: np.ndarray, z: float) -> np.ndarray:
    if abs(b[1] - a[1]) < 1e-6:
        return np.array([a[0], z])
    t = (z - a[1]) / (b[1] - a[1])
    return a + (b - a) * t


def estimate_scene(width: int, height: int, *, f35: float | None = None, floor_points: list | None = None,
                   room_dims: dict | None = None, width_confirmed: bool = False,
                   rgb: np.ndarray | None = None) -> dict[str, Any]:
    """Return camera pose, room box and evidence notes for one photo."""
    dims = {"width_m": 4.0, "length_m": 4.0, "height_m": 2.5, **(room_dims or {})}
    notes: list[str] = []
    focal_source = "EXIF 35 mm focal length" if f35 else None
    focal = focal_px_from_f35(f35, width, height) if f35 else None
    corners_px = None
    if floor_points and len(floor_points) == 4:
        # Stored order: far-left, far-right, near-right, near-left (fractions).
        corners_px = np.array([[float(x) * width - width / 2, float(y) * height - height / 2] for x, y in floor_points])
    pose = None
    layout_source = "default"
    world_corners = None

    if corners_px is not None:
        far_left, far_right, near_right, near_left = corners_px
        plane = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float64)
        H = _homography(plane, np.array([near_left, near_right, far_right, far_left]))
        if focal is None:
            denominator = H[2, 0] * H[2, 1]
            if abs(denominator) > 1e-12:
                f2 = -(H[0, 0] * H[0, 1] + H[1, 0] * H[1, 1]) / denominator
                # Only trust lens estimates in the range of real phone cameras (about
                # 45-105 degrees across); sloppy clicks otherwise give a telephoto
                # lens, which makes every item look gigantic.
                if f2 > 0 and 0.4 * max(width, height) < math.sqrt(f2) < 1.25 * max(width, height):
                    focal, focal_source = math.sqrt(f2), "floor-corner vanishing points"
        if focal is None:
            focal, focal_source = focal_px_from_f35(DEFAULT_F35, width, height), "typical 26 mm phone lens (assumed)"
        K_inv = np.diag([1 / focal, 1 / focal, 1.0])
        M = K_inv @ H
        m1, m2, m3 = M[:, 0], M[:, 1], M[:, 2]
        up = _normalize(np.cross(m1, m2))
        if up @ (-m3) < 0:
            up = -up
        dist_units = abs(float(up @ m3))  # camera-to-floor distance in homography units
        if width_confirmed:
            lam = float(np.linalg.norm(m1)) / dims["width_m"]
            scale_source = "your confirmed room width"
        else:
            lam = dist_units / PHONE_HEIGHT_M
            scale_source = f"typical {PHONE_HEIGHT_M:.2f} m phone height (confirm room width to improve)"
        cam_height = dist_units / lam
        far_mid = (m1 * 0.5 + m2 + m3) / lam
        near_mid = (m1 * 0.5 + m3) / lam
        pose = _pose_from_up(up, cam_height, far_mid - near_mid)
        refined = None
        # Four hand-clicked points are rarely a perfect rectangle, and that
        # error mostly shows up as camera *roll*. Vertical edges in the photo
        # measure roll reliably, but not pitch: listing photos are often
        # cropped, which moves the horizon away from the image centre. So we
        # take roll from the vertical edges and keep the clicks' pitch, then
        # accept the result only if the room it implies is plausible.
        if rgb is not None:
            vp_up, support = vertical_up_from_lines(rgb, focal)
            if vp_up is not None and support >= 6:
                roll_vp = math.atan2(float(vp_up[0]), float(-vp_up[1]))
                uz = float(np.clip(up[2], -0.95, 0.95))
                r = math.sqrt(1 - uz * uz)
                candidate = _normalize(np.array([r * math.sin(roll_vp), -r * math.cos(roll_vp), uz]))
                clicks = (far_left, far_right, near_right, near_left)
                if all(float(candidate @ np.array([u / focal, v / focal, 1.0])) < -1e-3 for u, v in clicks):
                    trial = _pose_from_up(candidate, 1.0, far_mid - near_mid)
                    hits = [_ray_floor(trial, u, v, focal) for u, v in clicks]
                    if all(h is not None for h in hits):
                        far_len = float(np.linalg.norm(hits[1] - hits[0]))
                        trial_height = dims["width_m"] / far_len if width_confirmed else PHONE_HEIGHT_M
                        depth = max(-h[2] for h in hits) * trial_height
                        width_m = far_len * trial_height
                        if 0.5 < trial_height < 2.5 and depth < 15 and 1.2 < width_m < 15:
                            cam_height = trial_height
                            pose = _pose_from_up(candidate, cam_height, far_mid - near_mid)
                            refined = f"{support} vertical edges"
        world_corners = []
        for u, v in (far_left, far_right, near_right, near_left):
            hit = _ray_floor(pose, u, v, focal)
            world_corners.append(hit)
        if any(c is None for c in world_corners) or not (0.4 < cam_height < 3.0):
            notes.append("The clicked floor corners gave an implausible camera; using automatic estimate instead.")
            pose, world_corners = None, None
        else:
            layout_source = "clicked floor corners" + (f" + roll from {refined}" if refined else "")
            if not width_confirmed:
                dims["width_m"] = float(np.linalg.norm(world_corners[1] - world_corners[0]))
            notes.append(f"Scale from {scale_source}.")

    if pose is None:
        if focal is None:
            focal, focal_source = focal_px_from_f35(DEFAULT_F35, width, height), "typical 26 mm phone lens (assumed)"
        up, support = (vertical_up_from_lines(rgb, focal) if rgb is not None else (None, 0))
        if up is not None:
            layout_source = f"vertical edges in the photo ({support} lines)"
        else:
            pitch = math.radians(-4.0)
            up = np.array([0.0, -math.cos(pitch), math.sin(pitch)])
            layout_source = "level-camera assumption"
            notes.append("Few straight vertical edges found; assumed a level camera. Click four floor corners for an accurate floor.")
        pose = _pose_from_up(up, PHONE_HEIGHT_M)
        notes.append("Floor height from a typical 1.45 m phone height.")

    camera = _camera_json(pose, focal, width, height)
    room_h = float(dims["height_m"])
    behind = 0.9  # keep a little room behind the camera so orbiting stays inside
    if world_corners is not None:
        fl, fr, nr, nl = [np.array([c[0], c[2]]) for c in world_corners]
        z_near = max(behind, nl[1] + 0.2, nr[1] + 0.2)
        left_near, right_near = _line_at_z(nl, fl, z_near), _line_at_z(nr, fr, z_near)
        floor = [fl, fr, right_near, left_near]
    else:
        half = float(dims["width_m"]) / 2
        back = -max(1.5, float(dims["length_m"]) - behind)
        floor = [np.array([-half, back]), np.array([half, back]), np.array([half, behind]), np.array([-half, behind])]
    floor_xz = [[round(float(p[0]), 4), round(float(p[1]), 4)] for p in floor]
    walls = [
        {"name": "back", "a": floor_xz[0], "b": floor_xz[1]},
        {"name": "right", "a": floor_xz[1], "b": floor_xz[2]},
        {"name": "near", "a": floor_xz[2], "b": floor_xz[3]},
        {"name": "left", "a": floor_xz[3], "b": floor_xz[0]},
    ]
    clicked = layout_source.startswith("clicked floor corners")
    confidence = "measured" if clicked and width_confirmed else ("good" if clicked else "rough")
    return {
        "camera": camera, "floor": floor_xz, "walls": walls, "ceiling_m": room_h,
        "layout_source": layout_source, "focal_source": focal_source, "confidence": confidence,
        "camera_height_m": round(float(pose["height"]), 3), "notes": notes,
        "room_width_m": round(float(dims["width_m"]), 3),
        "_pose": pose, "_focal": focal,
    }


def public_scene(scene: dict) -> dict:
    return {k: v for k, v in scene.items() if not k.startswith("_")}


def align_depth(scene: dict, disparity: np.ndarray, floor_mask: np.ndarray | None = None, grid_cols: int = 160) -> dict | None:
    """Convert relative disparity into metric camera depth using the floor plane.

    Floor pixels have a known depth from the camera model, so we fit
    1/z = a * disparity + b on them (robustly) and apply it everywhere.
    Returns a compact grid for the browser, or None when the fit is unreliable.
    """
    pose, focal = scene["_pose"], scene["_focal"]
    cam = scene["camera"]
    width, height = cam["width"], cam["height"]
    rows = max(8, int(round(grid_cols * height / width)))
    if cv2 is not None:
        disp = cv2.resize(disparity.astype(np.float32), (grid_cols, rows), interpolation=cv2.INTER_AREA)
    else:
        ys = np.linspace(0, disparity.shape[0] - 1, rows).astype(int)
        xs = np.linspace(0, disparity.shape[1] - 1, grid_cols).astype(int)
        disp = disparity[np.ix_(ys, xs)].astype(np.float32)
    us = (np.arange(grid_cols) + 0.5) / grid_cols * width - width / 2
    vs = (np.arange(rows) + 0.5) / rows * height - height / 2
    uu, vv = np.meshgrid(us, vs)
    rays = np.stack([uu / focal, vv / focal, np.ones_like(uu)], axis=-1)
    denom = rays @ pose["Y"]
    floor_z = np.where(denom < -1e-4, -pose["height"] / np.minimum(denom, -1e-4), np.nan)  # z along optical axis (ray z = 1)
    if floor_mask is None:
        floor_mask = np.zeros_like(disp, dtype=bool)
        floor_mask[int(rows * 0.82):, int(grid_cols * 0.2):int(grid_cols * 0.8)] = True
    elif floor_mask.shape != disp.shape and cv2 is not None:
        floor_mask = cv2.resize(floor_mask.astype(np.uint8), (grid_cols, rows), interpolation=cv2.INTER_NEAREST) > 0
    sample = floor_mask & np.isfinite(floor_z) & (floor_z > 0.2) & (floor_z < 20)
    if sample.sum() < 30:
        return None
    x, y = disp[sample], 1.0 / floor_z[sample]
    keep = np.ones_like(x, dtype=bool)
    a = b = None
    for _ in range(4):
        A = np.vstack([x[keep], np.ones(keep.sum())]).T
        a, b = np.linalg.lstsq(A, y[keep], rcond=None)[0]
        residual = np.abs(a * x + b - y)
        keep = residual <= np.percentile(residual, 70)
    if a is None or a <= 0:
        return None
    inv = a * disp + b
    depth = np.where(inv > 1 / 25.0, 1.0 / np.maximum(inv, 1e-6), 25.0)
    depth = np.clip(depth, 0.25, 25.0)
    millimetres = np.round(depth * 1000).astype("<u2")
    import base64
    return {"cols": grid_cols, "rows": rows, "depth_mm_b64": base64.b64encode(millimetres.tobytes()).decode(), "fit": {"a": float(a), "b": float(b), "samples": int(sample.sum())}}


def floor_mask_from_corners(scene: dict, cols: int, rows: int, floor_points: list) -> np.ndarray | None:
    if cv2 is None or not floor_points or len(floor_points) != 4:
        return None
    mask = np.zeros((rows, cols), np.uint8)
    polygon = np.array([[float(x) * cols, float(y) * rows] for x, y in floor_points], dtype=np.int32)
    cv2.fillPoly(mask, [polygon], 1)
    return mask > 0
