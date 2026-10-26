"""Source-faithful item cutouts.

Why the old cutouts lost detail
-------------------------------
* Listing photos were shrunk twice (900 px, then 800 px) before segmentation.
* The model's soft alpha was hard-thresholded at 8/255, producing jagged edges.
* A wide morphological *close* filled the gaps between spindles, legs and
  frames with the background wall, while the GrabCut fallback ran a
  morphological *open* that erased thin legs.
* The gallery picker chose whichever photo had ~35 % foreground, which was
  often a detail shot rather than the seller's main photo.

What this module does instead
-----------------------------
* Keeps up to 1600 px on the long side and only ever *removes background*;
  item pixels are copied from the seller photo unchanged.
* Prefers the strongest locally installed segmentation model (BiRefNet ->
  ISNet -> U2-Net) and falls back to GrabCut when none is installed.
* Keeps soft alpha. Enclosed holes are decided by colour: a hole that looks
  like the photo background (a gap between spindles) stays transparent; a hole
  that does not (pale upholstery the model was unsure about) is restored.
* Removes only tiny floating specks, never thin connected parts.
* Optionally decontaminates edge colours so the old wall colour does not halo.
* Reports quality numbers so the UI can fall back to the untouched photo.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    from rembg import new_session as _new_session, remove as _rembg_remove
except Exception:  # rembg is optional
    _new_session = None
    _rembg_remove = None

try:
    from pymatting import estimate_foreground_ml as _estimate_foreground
except Exception:
    _estimate_foreground = None

MAX_SIDE = 1600
MODEL_PREFERENCE = ("birefnet-general-lite", "isnet-general-use", "u2net")
_sessions: dict[str, object] = {}


def _model_file(name: str) -> Path | None:
    homes = []
    if os.getenv("U2NET_HOME"):
        homes.append(Path(os.path.expanduser(os.environ["U2NET_HOME"])))
    xdg = os.getenv("XDG_DATA_HOME")
    rembg_home = Path(os.path.expanduser(os.getenv("REMBG_HOME", os.path.join(xdg, "rembg") if xdg else "~/.rembg")))
    homes += [rembg_home / "models" / name, Path(os.path.expanduser("~/.u2net"))]
    for home in homes:
        path = home / f"{name}.onnx"
        if path.exists():
            return path
    return None


def available_model() -> str | None:
    """Return the best segmentation model already on disk (never downloads)."""
    if _rembg_remove is None:
        return None
    forced = os.getenv("THEIA_CUTOUT_MODEL")
    if forced:
        return forced if _model_file(forced) else None
    return next((name for name in MODEL_PREFERENCE if _model_file(name)), None)


def _model_alpha(image: Image.Image, model: str) -> np.ndarray:
    if model not in _sessions:
        _sessions[model] = _new_session(model)
    mask = _rembg_remove(image.convert("RGB"), session=_sessions[model], only_mask=True, post_process_mask=False)
    mask = mask if isinstance(mask, Image.Image) else Image.fromarray(np.asarray(mask))
    if mask.size != image.size:
        mask = mask.resize(image.size, Image.Resampling.BILINEAR)
    return np.asarray(mask.convert("L"), dtype=np.float32) / 255.0


def _grabcut_alpha(rgb: np.ndarray) -> np.ndarray:
    """GrabCut at working resolution, upsampled to a soft full-resolution mask."""
    height, width = rgb.shape[:2]
    scale = min(1.0, 800 / max(height, width))
    small = cv2.resize(rgb, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA) if scale < 1 else rgb
    h, w = small.shape[:2]
    bgr = cv2.cvtColor(small, cv2.COLOR_RGB2BGR)
    border = max(6, int(min(w, h) * 0.04))
    mask = np.full((h, w), cv2.GC_PR_FGD, np.uint8)
    mask[:border, :] = cv2.GC_BGD; mask[-border:, :] = cv2.GC_BGD
    mask[:, :border] = cv2.GC_BGD; mask[:, -border:] = cv2.GC_BGD
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(bgr, mask, None, bgd, fgd, 6, cv2.GC_INIT_WITH_MASK)
    hard = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1.0, 0.0).astype(np.float32)
    soft = cv2.resize(hard, (width, height), interpolation=cv2.INTER_LINEAR)
    return cv2.GaussianBlur(soft, (0, 0), max(0.6, 0.6 / scale))


def _background_lab(lab: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, float]:
    h, w = alpha.shape
    band = max(3, int(min(h, w) * 0.04))
    ring = np.zeros_like(alpha, dtype=bool)
    ring[:band, :] = ring[-band:, :] = True
    ring[:, :band] = ring[:, -band:] = True
    candidates = ring & (alpha < 0.15)
    if candidates.sum() < 50:
        candidates = ring
    samples = lab[candidates]
    centre = np.median(samples, axis=0)
    spread = float(np.median(np.linalg.norm(samples - centre, axis=1)))
    return centre, spread


def refine_alpha(rgb: np.ndarray, alpha: np.ndarray) -> tuple[np.ndarray, dict]:
    """Repair model uncertainty without inventing or erasing item pixels."""
    stats = {"holes_restored": 0, "holes_cleared": 0, "specks_removed": 0}
    if cv2 is None:
        return alpha, stats
    alpha = alpha.astype(np.float32).copy()
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB).astype(np.float32)
    bg, spread = _background_lab(lab, alpha)
    dist = np.linalg.norm(lab - bg, axis=2)
    threshold = max(14.0, spread * 2.5)

    solid = (alpha >= 0.5).astype(np.uint8)
    # Pixels reachable from the frame without crossing the item are outside.
    padded = np.pad((1 - solid).astype(np.uint8), 1, constant_values=1)
    flood_mask = np.zeros((padded.shape[0] + 2, padded.shape[1] + 2), np.uint8)
    cv2.floodFill(padded, flood_mask, (0, 0), 2)
    enclosed = (padded[1:-1, 1:-1] == 1)
    count, labels, comp_stats, _ = cv2.connectedComponentsWithStats(enclosed.astype(np.uint8), connectivity=4)
    for index in range(1, count):
        region = labels == index
        if comp_stats[index, cv2.CC_STAT_AREA] < 4:
            continue
        unlike_background = float((dist[region] > threshold).mean())
        if unlike_background > 0.65:
            alpha[region] = np.maximum(alpha[region], 1.0)  # e.g. pale seat fabric
            stats["holes_restored"] += 1
        elif unlike_background < 0.3:
            alpha[region & (dist <= threshold)] = 0.0  # real see-through gap
            stats["holes_cleared"] += 1

    # Weak-alpha pixels attached to the item that clearly differ from a uniform
    # background are item pixels (common on cream upholstery).
    if spread < 12:
        near_item = cv2.dilate(solid, np.ones((5, 5), np.uint8)) > 0
        weak = (alpha > 0.06) & (alpha < 0.5) & near_item & (dist > threshold * 2)
        alpha[weak] = np.maximum(alpha[weak], 0.9)

    # Drop only tiny floating specks; thin legs stay because they are connected.
    binary = (alpha > 0.25).astype(np.uint8)
    count, labels, comp_stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if count > 2:
        areas = comp_stats[1:, cv2.CC_STAT_AREA]
        limit = max(30, int(areas.max() * 0.002))
        for index in range(1, count):
            if comp_stats[index, cv2.CC_STAT_AREA] < limit:
                alpha[labels == index] = 0.0
                stats["specks_removed"] += 1
    return np.clip(alpha, 0.0, 1.0), stats


def _decontaminate(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    if _estimate_foreground is None or rgb.shape[0] * rgb.shape[1] > 1_400_000:
        return rgb
    try:
        fg = _estimate_foreground(rgb.astype(np.float64) / 255.0, alpha.astype(np.float64))
        out = rgb.copy()
        edge = (alpha > 0.02) & (alpha < 0.98)  # only touch semi-transparent edge pixels
        out[edge] = np.clip(fg[edge] * 255.0, 0, 255).astype(np.uint8)
        return out
    except Exception:
        return rgb


def make_cutout(image: Image.Image) -> tuple[Image.Image, dict]:
    """Return (RGBA cutout, report). The report says how trustworthy it is."""
    image = image.convert("RGB")
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    rgb = np.asarray(image, dtype=np.uint8)
    report: dict = {"width": image.width, "height": image.height}
    model = available_model()
    alpha = None
    if model:
        try:
            alpha = _model_alpha(image, model)
            report["method"] = f"{model} segmentation"
        except Exception as exc:  # fall through to GrabCut
            report["model_error"] = type(exc).__name__
    if alpha is None:
        if cv2 is None:
            report.update(method="no segmentation available", ok=False, coverage=1.0)
            return image.convert("RGBA"), report
        alpha = _grabcut_alpha(rgb)
        report["method"] = "GrabCut fallback (install a segmentation model for sharper edges)"
    alpha, fixes = refine_alpha(rgb, alpha)
    report.update(fixes)
    coverage = float((alpha > 0.5).mean())
    h, w = alpha.shape
    band = max(2, int(min(h, w) * 0.01))
    edge_contact = float(np.concatenate([alpha[:band].ravel(), alpha[-band:].ravel(), alpha[:, :band].ravel(), alpha[:, -band:].ravel()]).mean())
    report["coverage"] = round(coverage, 3)
    report["edge_contact"] = round(edge_contact, 3)
    report["ok"] = 0.02 < coverage < 0.95
    colour = _decontaminate(rgb, alpha) if report["ok"] else rgb
    rgba = np.dstack([colour, (alpha * 255).round().astype(np.uint8)])
    cutout = Image.fromarray(rgba, mode="RGBA")
    bbox = cutout.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if bbox and report["ok"]:
        pad = max(4, int(min(cutout.size) * 0.015))
        cutout = cutout.crop((max(0, bbox[0] - pad), max(0, bbox[1] - pad), min(w, bbox[2] + pad), min(h, bbox[3] + pad)))
    report["aspect"] = round(cutout.width / max(1, cutout.height), 4)
    return cutout, report
