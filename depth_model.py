"""Optional monocular depth (Depth Anything V2 Small, ONNX) for 3D room relief.

The app works without it: the room is then a textured box built from the
floor plane. When ``models/depth_anything_v2_small.onnx`` exists (download it
with ``python scripts/download_models.py``), the photo is also turned into a
depth-relief mesh so existing furniture stands out of the floor and correctly
hides items placed behind it. Inference runs locally on the CPU; no room photo
leaves the machine.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import numpy as np
from PIL import Image

PROJECT_DIR = Path(__file__).resolve().parent
MODEL_PATH = Path(os.getenv("THEIA_DEPTH_MODEL", PROJECT_DIR / "models" / "depth_anything_v2_small.onnx"))
INPUT_SIDE = 518  # multiple of the ViT patch size (14)
_session = None
_cache: dict[str, np.ndarray] = {}


def available() -> bool:
    if not MODEL_PATH.exists():
        return False
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _get_session():
    global _session
    if _session is None:
        import onnxruntime as ort
        _session = ort.InferenceSession(str(MODEL_PATH), providers=["CPUExecutionProvider"])
    return _session


def predict_disparity(image: Image.Image) -> np.ndarray | None:
    """Relative inverse depth (larger = closer) at model resolution, cached."""
    if not available():
        return None
    rgb = image.convert("RGB")
    key = hashlib.sha1(rgb.resize((64, 48)).tobytes() + str(rgb.size).encode()).hexdigest()
    if key in _cache:
        return _cache[key]
    scale = INPUT_SIDE / max(rgb.size)
    w = max(14, int(round(rgb.width * scale / 14)) * 14)
    h = max(14, int(round(rgb.height * scale / 14)) * 14)
    array = np.asarray(rgb.resize((w, h), Image.Resampling.BICUBIC), dtype=np.float32) / 255.0
    array = (array - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    tensor = array.transpose(2, 0, 1)[None].astype(np.float32)
    session = _get_session()
    output = session.run(None, {session.get_inputs()[0].name: tensor})[0]
    disparity = np.squeeze(np.asarray(output, dtype=np.float32))
    if disparity.ndim != 2:
        return None
    disparity = disparity - float(disparity.min())
    disparity = disparity / max(1e-6, float(disparity.max()))
    if len(_cache) > 16:
        _cache.clear()
    _cache[key] = disparity
    return disparity
