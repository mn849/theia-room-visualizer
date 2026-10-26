"""Download Theia's optional local models (not needed for the app to run).

    python scripts/download_models.py            # both models
    python scripts/download_models.py --depth    # depth model only (~100 MB)
    python scripts/download_models.py --cutout   # sharper cutout model (~170 MB)

* Depth Anything V2 Small (Apache-2.0) -> models/depth_anything_v2_small.onnx
  Adds 3D relief to room photos in the 3D view.
* ISNet general-use (via rembg) -> ~/.rembg/models/...
  Sharper item cutouts than U2-Net, especially thin legs and spindles.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DEPTH_URL = "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx"


def download_depth() -> None:
    target = ROOT / "models" / "depth_anything_v2_small.onnx"
    if target.exists():
        print(f"depth model already present: {target}")
        return
    target.parent.mkdir(exist_ok=True)
    print("downloading Depth Anything V2 Small ...")
    with requests.get(DEPTH_URL, stream=True, timeout=60) as response:
        response.raise_for_status()
        tmp = target.with_suffix(".part")
        with tmp.open("wb") as handle:
            for chunk in response.iter_content(1 << 20):
                handle.write(chunk)
        tmp.rename(target)
    print(f"saved {target}")


def download_cutout() -> None:
    try:
        from rembg import new_session
    except ImportError:
        print("rembg is not installed: pip install 'rembg[cpu]' first (optional)")
        return
    print("downloading isnet-general-use via rembg ...")
    new_session("isnet-general-use")
    print("cutout model ready")


if __name__ == "__main__":
    args = set(sys.argv[1:])
    if not args or "--depth" in args:
        download_depth()
    if not args or "--cutout" in args:
        download_cutout()
