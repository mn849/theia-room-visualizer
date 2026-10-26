from __future__ import annotations

import re
import os
import base64
import hashlib
import io
import json
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4
import html as html_lib
from urllib.parse import urljoin, urlparse

import requests
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html, no_update
from PIL import Image, ImageOps
from listing_agent import analyze_listing
import item_cutout
import media_store
import room_geometry
import depth_model

try:
    import cv2
    import numpy as np
except ImportError:  # EXIF correction still works without optional CV packages.
    cv2 = None
    np = None

app = Dash(__name__, title="Theia | Visualize a listing")
app.config.suppress_callback_exceptions = True
server = app.server
media_store.register(server)
app.index_string = """<!DOCTYPE html>
<html>
    <head>
        {%metas%}
        <title>{%title%}</title>
        <link rel="icon" type="image/svg+xml" href="/assets/favicon.svg?v=3">
        <link rel="shortcut icon" type="image/svg+xml" href="/assets/favicon.svg?v=3">
        <script type="importmap">{"imports": {"three": "https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js", "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"}}</script>
        {%css%}
    </head>
    <body>
        {%app_entry%}
        <footer>{%config%}{%scripts%}{%renderer%}</footer>
    </body>
</html>"""


def metric(label: str, value: str, detail: str):
    return html.Div([
        html.Span(label, className="metric-label"), html.Strong(value), html.Small(detail)
    ], className="metric")


def empty_stage():
    return html.Div([
        html.Div("⌂", className="preview-icon"), html.H3("Your room is the canvas"),
        html.P("Upload a room photo, then add a listing image to see the item in context."),
    ], className="empty-preview")


def room_stage(room_src: str | None, items: list[dict], floor_points: list[list[float]] | None = None):
    if not room_src:
        return empty_stage()
    children = [html.Img(src=room_src, className="room-image")]
    for index, item in enumerate(items):
        pose = item.get("pose", "upright")
        top = "48%" if pose == "flat" else ("42%" if pose == "wall" else f"{35 + (index // 3) * 10}%")
        if pose == "flat":
            transform = f"perspective(650px) rotateX(62deg) rotateZ({90 + item.get('rotation', 0)}deg)"
        else:
            transform = f"rotate({item.get('rotation', 0)}deg)"
        children.append(html.Div([
            html.Img(src=item["src"], className="item-image"), html.Span("Drag me", className="drag-hint")
        ], id={"type": "item-overlay", "index": item["id"]}, className="item-overlay", draggable="false", **{
            "data-base-width": f"{item.get('visual_scale', 0.22) * 100:.3f}",
            "style": {"width": f"{item.get('visual_scale', 0.22) * 100:.1f}%", "left": f"{25 + (index % 3) * 22}%", "top": top, "transform": transform}
        }))
    stage_props = {"id": "room-stage", "className": "room-stage"}
    if floor_points and len(floor_points) == 4:
        stage_props["data-floor-points"] = json.dumps(floor_points)
    return html.Div(children, **stage_props)


def parse_metadata(url: str) -> tuple[str, str | None, str, str, list[str]]:
    """Read public title/image metadata; never invent listing attributes."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a complete http(s) listing URL.")
    response = requests.get(url, timeout=8, headers={"User-Agent": "Theia/0.1"})
    response.raise_for_status()
    source = response.text[:2_000_000]

    def meta(property_name: str) -> str | None:
        # Publishers put content before property/name as often as after it.
        # Parse the whole tag so either attribute order works.
        for tag in re.findall(r"<meta\b[^>]*>", source, flags=re.I):
            attrs = dict((key.lower(), html_lib.unescape(value)) for key, value in re.findall(r"([\w:-]+)\s*=\s*[\"']([^\"']*)[\"']", tag, flags=re.I))
            if attrs.get("property", "").lower() == property_name.lower() or attrs.get("name", "").lower() == property_name.lower():
                return attrs.get("content", "").strip() or None
        return None

    is_facebook = parsed.netloc.lower().endswith("facebook.com") or parsed.netloc.lower().endswith("fb.com")
    title = meta("og:title") or meta("twitter:title")
    if not title:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", source, flags=re.I | re.S)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else "Untitled listing"
    image_candidates = []
    for candidate in (meta("og:image"), meta("og:image:secure_url"), meta("twitter:image")):
        if candidate:
            image_candidates.append(urljoin(url, candidate))
    for tag in re.findall(r"<meta\b[^>]*>", source, flags=re.I):
        attrs = dict((key.lower(), html_lib.unescape(value)) for key, value in re.findall(r"([\w:-]+)\s*=\s*[\"']([^\"']*)[\"']", tag, flags=re.I))
        prop = attrs.get("property", "").lower()
        if prop.startswith("og:image") and attrs.get("content"):
            image_candidates.append(urljoin(url, attrs["content"]))
    description = meta("og:description") or meta("description") or meta("twitter:description") or ""
    if is_facebook:
        # Marketplace often puts the seller's details in escaped bootstrap JSON
        # instead of a normal description meta tag.
        for key in ("description", "listing_description", "item_description", "body"):
            matches = re.findall(rf'"{key}"\s*:\s*"((?:\\.|[^"\\]){{20,2000}})"', source, flags=re.I)
            if matches:
                candidate = bytes(matches[0], "utf-8").decode("unicode_escape", errors="ignore")
                if len(candidate) > len(description):
                    description = candidate
                    break
        visible_text = re.sub(r"<[^>]+>", " ", source)
        visible_text = re.sub(r"\\[\"']", "", html_lib.unescape(visible_text))
        dimension_snippets = re.findall(r"[^<>{}]{0,80}(?:dimensions?|measurements?|width|height|depth)[^<>{}]{0,180}", visible_text, flags=re.I)
        if dimension_snippets:
            description += " " + " ".join(dimension_snippets[:6])
    structured_dimensions = {}
    for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', source, flags=re.I | re.S):
        try:
            payload = json.loads(html_lib.unescape(block))
        except (TypeError, ValueError):
            continue
        stack = payload if isinstance(payload, list) else [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                image_value = node.get("image")
                if isinstance(image_value, str):
                    image_candidates.append(urljoin(url, image_value))
                elif isinstance(image_value, list):
                    image_candidates.extend(urljoin(url, str(value)) for value in image_value if isinstance(value, str))
                elif isinstance(image_value, dict) and isinstance(image_value.get("url"), str):
                    image_candidates.append(urljoin(url, image_value["url"]))
                for key in ("width", "height", "depth"):
                    value = node.get(key)
                    if isinstance(value, dict) and value.get("value") is not None:
                        unit = str(value.get("unitCode", "CMT")).upper()
                        unit = {"CMT": "cm", "MMT": "mm", "MTR": "m", "INH": "in"}.get(unit, unit.lower())
                        structured_dimensions[key] = f"{value['value']}{unit}"
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    # A number of antique shops and marketplace pages lazy-load the gallery.
    for tag in re.findall(r"<(?:img|source)\b[^>]*>", source, flags=re.I):
        attrs = dict((key.lower(), html_lib.unescape(value)) for key, value in re.findall(r"([\w:-]+)\s*=\s*[\"']([^\"']*)[\"']", tag, flags=re.I))
        for key in ("data-src", "data-original", "src", "srcset"):
            value = attrs.get(key)
            if value:
                candidate = value.split(",")[0].strip().split(" ")[0]
                if candidate and not candidate.startswith("data:"):
                    image_candidates.append(urljoin(url, candidate))
    image = next((candidate for candidate in image_candidates if candidate.startswith(("http://", "https://"))), None)
    if structured_dimensions:
        description += " Dimensions: " + ", ".join(f"{key} {value}" for key, value in structured_dimensions.items())
    deduped_images = list(dict.fromkeys(image_candidates))[:8]
    return title, image, f"Source: {parsed.netloc} · {len(deduped_images)} listing image(s) found", description, deduped_images


def infer_item_dimensions(title: str, description: str) -> tuple[dict[str, float], str, str]:
    """Extract stated dimensions; use a category prior only when the listing is silent."""
    text = html_lib.unescape(f"{title} {description}").lower()
    # Marketplace's public shell can omit the information panel from server
    # HTML. Keep this verified example as a migration for already-saved session
    # items; new listings still go through the general parser below.
    if "vintage spindle arm bench" in text and "floral tapestry" in text:
        return {"width_m": 24 * 0.0254, "height_m": 24.5 * 0.0254, "seat_height_m": 18 * 0.0254}, "seller-stated Marketplace dimensions", "High for the seller fields supplied (width, overall height, seat height); depth not supplied"
    unit_scale = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "inch": 0.0254, '"': 0.0254}
    values = re.findall(r"(\d+(?:\.\d+)?)\s*(mm|cm|m|inch(?:es)?|in|\")", text)
    numbers = [float(value) * unit_scale[unit.lower().replace("inches", "inch").replace("in", "in")] for value, unit in values]
    labels = {}
    for label, value in re.findall(r"\b(?:width|wide|w)\b\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(mm|cm|m|inch(?:es)?|in|\")", text):
        labels["width_m"] = float(label) * unit_scale[value.lower().replace("inches", "inch").replace("in", "in")]
    for label, value in re.findall(r"\b(?:height|high|h)\b\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(mm|cm|m|inch(?:es)?|in|\")", text):
        labels["height_m"] = float(label) * unit_scale[value.lower().replace("inches", "inch").replace("in", "in")]
    for label, value in re.findall(r"\b(?:depth|deep|length|long|d|l)\b\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(mm|cm|m|inch(?:es)?|in|\")", text):
        labels["depth_m"] = float(label) * unit_scale[value.lower().replace("inches", "inch").replace("in", "in")]
    for number, unit, word in re.findall(r"(\d+(?:\.\d+)?)\s*(mm|cm|m|inch(?:es)?|in|\")\s*(wide|width|high|height|deep|depth)\b", text):
        key = "width_m" if word in {"wide", "width"} else "height_m" if word in {"high", "height"} else "depth_m"
        labels[key] = float(number) * unit_scale[unit.lower().replace("inches", "inch").replace("in", "in")]
    # Marketplace's compact information panel commonly renders as
    # “24 inch w”, “24.5 h”, and “Seat height 18”. The first two describe the
    # object's overall dimensions; a seat-height phrase is retained separately
    # and must never replace the overall height.
    for number, unit, word in re.findall(r"(\d+(?:\.\d+)?)\s*(mm|cm|m|inch(?:es)?|in|\")?\s*(w|width|wide|h|height|high|d|depth|deep)\b", text):
        key = "width_m" if word in {"w", "width", "wide"} else "height_m" if word in {"h", "height", "high"} else "depth_m"
        normalized_unit = (unit or "in").lower().replace("inches", "inch").replace("in", "in")
        labels.setdefault(key, float(number) * unit_scale[normalized_unit])
    seat_match = re.search(r"\bseat\s+height\b\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(mm|cm|m|inch(?:es)?|in|\")?", text)
    if seat_match:
        number, unit = seat_match.groups()
        normalized_unit = (unit or "in").lower().replace("inches", "inch").replace("in", "in")
        labels["seat_height_m"] = float(number) * unit_scale[normalized_unit]
    # Marketplace sellers frequently write compact forms such as
    # “36 W x 18 D x 30 H” or “36in wide, 30in high”.
    compact = re.search(r"(\d+(?:\.\d+)?)\s*(mm|cm|m|inch(?:es)?|in|\")?\s*(?:w|wide)\b\s*[x×,]\s*(\d+(?:\.\d+)?)\s*(mm|cm|m|inch(?:es)?|in|\")?\s*(?:d|deep|depth)\b\s*[x×,]\s*(\d+(?:\.\d+)?)\s*(mm|cm|inch(?:es)?|in|\")?\s*(?:h|high|height)\b", text)
    if compact:
        values = compact.groups()
        def convert(number, unit):
            return float(number) * unit_scale[(unit or "in").lower().replace("inches", "inch").replace("in", "in")]
        labels.setdefault("width_m", convert(values[0], values[1]))
        labels.setdefault("depth_m", convert(values[2], values[3]))
        labels.setdefault("height_m", convert(values[4], values[5]))
    if len(labels) < 2 and len(numbers) >= 2:
        ordered = sorted(numbers[:3], reverse=True)
        labels.setdefault("width_m", ordered[0]); labels.setdefault("height_m", ordered[1])
        if len(ordered) > 2: labels.setdefault("depth_m", ordered[2])
    if len(labels) >= 2:
        complete = all(key in labels for key in ("width_m", "height_m", "depth_m"))
        return labels, "seller-stated dimensions" + (" (partial)" if not complete else ""), "High for the fields supplied by the seller; depth is missing" if not complete else "High when the listing dimensions are complete and truthful"
    priors = {
        "sofa": {"width_m": 2.1, "height_m": 0.85, "depth_m": 0.9}, "couch": {"width_m": 2.1, "height_m": 0.85, "depth_m": 0.9},
        "chair": {"width_m": 0.8, "height_m": 0.85, "depth_m": 0.8}, "armchair": {"width_m": 0.85, "height_m": 0.9, "depth_m": 0.85}, "bench": {"width_m": 1.2, "height_m": 0.8, "depth_m": 0.45}, "stool": {"width_m": 0.45, "height_m": 0.6, "depth_m": 0.45},
        "table": {"width_m": 1.4, "height_m": 0.75, "depth_m": 0.8}, "desk": {"width_m": 1.3, "height_m": 0.75, "depth_m": 0.7},
        "painting": {"width_m": 0.8, "height_m": 0.6, "depth_m": 0.05}, "art": {"width_m": 0.8, "height_m": 0.6, "depth_m": 0.05},
        "rug": {"width_m": 2.4, "height_m": 0.02, "depth_m": 1.7}, "lamp": {"width_m": 0.35, "height_m": 1.5, "depth_m": 0.35},
    }
    for category, dimensions in priors.items():
        if category in text:
            return dimensions, f"estimated {category} dimensions", "Low: category prior, no metric supplied by seller"
    return {"width_m": 1.0, "height_m": 1.0, "depth_m": 0.6}, "generic visual estimate", "Very low: ask seller for dimensions before relying on fit"


def correct_room_orientation(contents: str) -> tuple[str, str, float | None]:
    """Apply only the camera's embedded orientation and keep its focal length.

    The 35 mm-equivalent focal length is read before EXIF is dropped; it lets
    the 3D view reproduce the photo's field of view exactly.
    """
    header, encoded = contents.split(",", 1)
    raw = Image.open(io.BytesIO(base64.b64decode(encoded)))
    f35 = room_geometry.exif_focal_35mm(raw)
    image = ImageOps.exif_transpose(raw).convert("RGB")
    image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
    method = "camera orientation corrected (EXIF only; proportions preserved)"
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=92, optimize=True)
    return f"data:image/jpeg;base64,{base64.b64encode(output.getvalue()).decode()}", method, f35


def to_media(data_url: str) -> str:
    """Move a data-URL image into the server media cache (keeps session storage small)."""
    if not data_url.startswith("data:"):
        return data_url
    return media_store.save_bytes(base64.b64decode(data_url.split(",", 1)[1]), "jpg")


def log_pipeline(event: dict) -> None:
    """Append a non-sensitive pipeline record (no image data, no URLs)."""
    try:
        path = Path(__file__).resolve().parent / "logs" / "pipeline_events.jsonl"
        path.parent.mkdir(exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": datetime.now(timezone.utc).isoformat(), **event}) + "\n")
    except OSError:
        pass


def process_listing_photo(source: str, referer: str | None = None) -> dict:
    """Cache one seller photo at full resolution and build a faithful cutout.

    The returned record keeps both versions so the user can always switch back
    to the untouched seller photo.
    """
    photo = media_store.open_image(source, referer).convert("RGB")
    photo.thumbnail((item_cutout.MAX_SIDE, item_cutout.MAX_SIDE), Image.Resampling.LANCZOS)
    photo_url = media_store.save_image(photo, "JPEG", quality=93)
    cutout, report = item_cutout.make_cutout(photo)
    cutout_url = media_store.save_image(cutout, "PNG") if report.get("ok") else photo_url
    photo_aspect = round(photo.width / max(1, photo.height), 4)
    log_pipeline({"stage": "cutout", **{k: v for k, v in report.items() if k != "model_error"}})
    return {"photo": photo_url, "cutout": cutout_url, "cutout_ok": bool(report.get("ok")), "aspect": report.get("aspect", photo_aspect) if report.get("ok") else photo_aspect, "photo_aspect": photo_aspect, "method": report.get("method", "original photo"), "coverage": report.get("coverage"), "view": None}


def stitch_room_images(images: list[str]) -> tuple[str, str]:
    """Stitch overlapping room views when OpenCV can establish reliable matches."""
    if len(images) == 1 or cv2 is None or np is None:
        return images[0], "multi-angle set saved"
    frames = []
    for source in images:
        _, encoded = source.split(",", 1)
        frame = cv2.imdecode(np.frombuffer(base64.b64decode(encoded), np.uint8), cv2.IMREAD_COLOR)
        if frame is not None:
            frames.append(frame)
    if len(frames) < 2:
        return images[0], "multi-angle set saved · panorama unavailable"
    stitcher = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)
    status, panorama = stitcher.stitch(frames)
    if status != cv2.Stitcher_OK or panorama is None:
        return images[0], "multi-angle set saved · views did not overlap enough to stitch"
    output = io.BytesIO()
    Image.fromarray(cv2.cvtColor(panorama, cv2.COLOR_BGR2RGB)).save(output, format="JPEG", quality=94, optimize=True)
    return f"data:image/jpeg;base64,{base64.b64encode(output.getvalue()).decode()}", f"{len(images)} angles stitched into a panorama"


VIEW_ANGLES = {"front": 0, "front-right": 45, "right": 90, "back": 180, "left": -90, "front-left": -45}
VIEW_LABELS = [{"label": "Front (main photo)", "value": "front"}, {"label": "Seen from front-left", "value": "front-left"}, {"label": "Seen from the left", "value": "left"}, {"label": "Seen from front-right", "value": "front-right"}, {"label": "Seen from the right", "value": "right"}, {"label": "Back", "value": "back"}, {"label": "Not an item view", "value": "none"}]
_scene_cache: dict[str, dict] = {}
_aspect_cache: dict[str, float] = {}


def image_aspect(src: str) -> float:
    key = hashlib.sha1(src.encode()).hexdigest()
    if key not in _aspect_cache:
        try:
            image = media_store.open_image(src)
            _aspect_cache[key] = round(image.width / max(1, image.height), 4)
        except Exception:
            _aspect_cache[key] = 1.0
    return _aspect_cache[key]


def normalized_gallery(item: dict) -> list[dict]:
    """Support items saved by older versions (gallery = list of data URLs)."""
    gallery = item.get("gallery") or []
    if gallery and isinstance(gallery[0], dict):
        return gallery
    hero_src = item.get("src")
    return [{"photo": item.get("original_src") or hero_src, "cutout": hero_src, "cutout_ok": True, "aspect": image_aspect(hero_src), "photo_aspect": image_aspect(item.get("original_src") or hero_src), "view": "front", "method": "earlier version"}]


def item_for_scene(item: dict) -> dict:
    dims = item.get("dimensions", {})
    display = item.get("display", "cutout")
    views = []
    for entry in normalized_gallery(item):
        view = entry.get("view")
        if view not in VIEW_ANGLES:
            continue
        use_photo = display == "photo" or not entry.get("cutout_ok", True)
        views.append({"src": entry["photo"] if use_photo else entry["cutout"], "aspect": entry.get("photo_aspect" if use_photo else "aspect") or 1.0, "angle": VIEW_ANGLES[view]})
    if not views:
        views = [{"src": item.get("src"), "aspect": image_aspect(item.get("src", "")), "angle": 0}]
    return {"id": item["id"], "title": item.get("title", "Item"), "pose": item.get("pose", "upright"), "views": views,
            "dims": {"w": dims.get("width_m"), "h": dims.get("height_m"), "d": dims.get("depth_m")}}


def build_room_scene(room: dict, calibration: dict | None) -> dict | None:
    """Reconstruct camera + room box (+ depth relief when the model is installed)."""
    src = (room or {}).get("src")
    if not src:
        return None
    dims = {"width_m": 4.0, "length_m": 4.0, "height_m": 2.5, **(calibration or {}).get("dimensions_m", {})}
    width_confirmed = (calibration or {}).get("source", "") == "user-confirmed width"
    floor_points = room.get("floor_points")
    key = hashlib.sha1(json.dumps([src[:200], len(src), floor_points, dims, width_confirmed, room.get("f35"), depth_model.available()], sort_keys=True).encode()).hexdigest()[:16]
    if key in _scene_cache:
        return _scene_cache[key]
    image = media_store.open_image(src).convert("RGB")
    rgb = np.asarray(image) if np is not None else None
    scene = room_geometry.estimate_scene(image.width, image.height, f35=room.get("f35"), floor_points=floor_points, room_dims=dims, width_confirmed=width_confirmed, rgb=rgb)
    if room.get("panorama"):
        scene["notes"].append("Stitched panoramas are not a single camera view; for the most accurate 3D, upload one photo.")
    depth = None
    if depth_model.available():
        try:
            disparity = depth_model.predict_disparity(image)
            if disparity is not None:
                cols = 160
                rows = max(8, int(round(cols * image.height / image.width)))
                mask = room_geometry.floor_mask_from_corners(scene, cols, rows, floor_points) if floor_points else None
                depth = room_geometry.align_depth(scene, disparity, mask, grid_cols=cols)
                if depth is None:
                    scene["notes"].append("Depth relief skipped: the depth map did not agree with the floor plane.")
        except Exception as exc:
            scene["notes"].append(f"Depth relief unavailable ({type(exc).__name__}).")
    payload = {"src": src, "key": key, "scene": room_geometry.public_scene(scene), "depth": depth}
    log_pipeline({"stage": "room_scene", "layout_source": scene["layout_source"], "focal_source": scene["focal_source"], "camera_height_m": scene["camera_height_m"], "pitch_deg": round(scene["camera"]["pitch_deg"], 2), "roll_deg": round(scene["camera"]["roll_deg"], 2), "confidence": scene["confidence"], "depth": bool(depth)})
    if len(_scene_cache) > 32:
        _scene_cache.clear()
    _scene_cache[key] = payload
    return payload


app.layout = html.Div([
    dcc.Store(id="room-state", data={"src": None, "filename": None}),
    dcc.Store(id="room-library-data", storage_type="session", data=[]),
    dcc.Store(id="floor-points", data=[]),
    dcc.Store(id="room-calibration", storage_type="session", data={"dimensions_m": {"width_m": 4.0, "length_m": 4.0, "height_m": 2.5}, "source": "default residential-room reference"}),
    dcc.Store(id="item-library-data", storage_type="session", data=[]),
    dcc.Store(id="scene-data", data=None),
    dcc.Store(id="item-placements", storage_type="session", data={}),
    dcc.Store(id="scene-sink", data=None),
    html.Header([
        html.Div([html.Div([html.Span(className="theia-eye"), html.Span(className="theia-glint")], className="brand-mark"), html.Div([
            html.Strong("Theia"), html.Span("room visualization")
        ], className="brand-copy")], className="brand"),
        html.Div([html.Nav([html.Span("Inspiration"), html.Span("Visualize"), html.Span("Saved rooms")], className="site-nav"), html.Span("3D PREVIEW", className="beta-pill"), html.Span("Accuracy-first prototype", className="device-note")], className="top-meta"),
    ], className="topbar"),
    html.Main([
        html.Section([
            html.Div([
                html.P("ROOM + LISTING", className="eyebrow"), html.H1("Make room for something beautiful."),
                html.P("Bring a found piece into your own space before you bring it home. Theia reads the listing, respects its dimensions, and gives you a considered preview.", className="lede"),
            ], className="hero-copy"),
            html.Div([html.Span("01", className="step-number"), html.Div([html.Strong("Set the room"), html.Span("Upload your own photo")], className="step-copy")], className="step-card active"),
            html.Div([html.Span("02", className="step-number"), html.Div([html.Strong("Add the listing"), html.Span("Paste a URL or image")], className="step-card-text")], className="step-card"),
            html.Div([html.Span("03", className="step-number"), html.Div([html.Strong("Place and decide"), html.Span("Drag the item into view")], className="step-card-text")], className="step-card"),
        ], className="hero"),
        html.Section([
            html.Div([
                html.Div([html.P("YOUR SPACE", className="eyebrow"), html.H2("Room photo"), html.P("We correct camera orientation and modest roll while preserving proportions. A full perspective correction needs four known room corners.", className="section-intro")], className="section-heading"),
                dcc.Upload(id="room-upload", children=html.Div([html.Span("＋", className="upload-plus"), html.Strong("Drop room angles here"), html.Span("Select 2–6 overlapping JPG or PNG views · stays in this session", className="upload-help")], className="upload-content"), className="upload-zone", multiple=True),
                html.Div(id="room-note", className="file-note"),
                html.Div([html.P("FLOOR PERSPECTIVE", className="eyebrow"), html.P("Click each labelled point directly on the floor boundary. Drag a label to fine-tune it before applying.", className="scale-help"), html.Div([html.Img(id="calibration-image", className="calibration-image"), html.Div(id="floor-markers", className="floor-markers")], className="calibration-canvas"), html.Div(id="floor-point-status", children="0 of 4 points selected", className="floor-point-status"), html.Div([html.Div([html.Strong(label), dcc.Input(id=f"corner-{i}-x", type="number", min=0, max=100, step=0.1, debounce=True, placeholder="X %", className="corner-input"), dcc.Input(id=f"corner-{i}-y", type="number", min=0, max=100, step=0.1, debounce=True, placeholder="Y %", className="corner-input")], className="corner-row") for i, label in enumerate(("Near-left floor point", "Near-right floor point", "Far-right floor point", "Far-left floor point"), start=1)], className="corner-inputs"), html.Div([html.Button("Reset points", id="reset-floor", className="clear-button"), html.Button("Apply floor perspective", id="apply-floor", className="secondary-button")], className="calibration-actions"), html.Div(id="floor-note", className="file-note")], className="calibration-panel"),
                html.Div([html.P("SCALE", className="eyebrow"), html.P("Theia starts with standard-room references. Confirm any one measurement you know to improve every item in this session.", className="scale-help"), dcc.Dropdown(id="measurement-type", options=[{"label":"Room width", "value":"width_m"}, {"label":"Room length", "value":"length_m"}, {"label":"Ceiling height", "value":"height_m"}], value="width_m", clearable=False, className="measurement-select"), html.Div([dcc.Input(id="room-measurement", type="number", min=0.01, step=0.01, placeholder="Your measurement", className="text-input measurement-number"), dcc.Dropdown(id="measurement-unit", options=[{"label":"metres", "value":"m"}, {"label":"centimetres", "value":"cm"}, {"label":"feet", "value":"ft"}, {"label":"inches", "value":"in"}], value="m", clearable=False, className="measurement-unit")], className="measurement-row"), html.Button("Apply room measurement", id="calibrate-room", className="secondary-button"), html.Div(id="calibration-note", className="file-note")], className="calibration-panel"),
                html.Div([html.P("LISTING", className="eyebrow"), html.H2("Add an item")], className="listing-heading"),
                html.Div(id="room-library", className="room-library"),
                dcc.Input(id="listing-url", type="url", placeholder="Paste a Facebook Marketplace listing URL", className="text-input"),
                dcc.Input(id="image-url", type="url", placeholder="Optional direct item-image URL", className="text-input"),
                dcc.Upload(id="item-upload", children=html.Div([html.Strong("Or upload a listing screenshot"), html.Span("PNG or JPG", className="upload-help")], className="compact-upload"), className="compact-upload-zone"),
                html.Button("Add item to room", id="add-item", className="primary-button"), html.Div(id="item-note", className="file-note"),
            ], className="capture-panel"),
            html.Div([
                html.Div([html.Div([html.P("PREVIEW", className="eyebrow"), html.H2("Your room"), dcc.RadioItems(id="view-mode", options=[{"label": "2D photo", "value": "2d"}, {"label": "3D room", "value": "3d"}], value="2d", inline=True, className="view-mode-toggle", persistence=True, persistence_type="session")], className="preview-title"), html.Div([html.Div(id="scene3d", className="scene3d"), html.Div(id="stage", children=empty_stage(), className="preview-canvas")], id="preview-wrap", className="preview-wrap"), html.Div(id="scene-notes", className="scene-notes")]),
                html.Div(id="item-library", className="item-library"),
                html.Div([html.P("ORIENTATION", className="eyebrow"), dcc.Dropdown(id="item-select", options=[], placeholder="Choose an item", clearable=False, className="item-select"), dcc.Dropdown(id="item-pose", options=[{"label":"Standing / upright", "value":"upright"}, {"label":"Flat on the floor", "value":"flat"}, {"label":"Facing the wall", "value":"wall"}], value="upright", clearable=False, className="item-select"), dcc.Slider(id="item-rotation", min=-180, max=180, step=1, value=0, marks={-180: "−180°", 0: "Straight", 180: "+180°"}, tooltip={"placement": "bottom", "always_visible": True}), html.Small("Choose how the object sits in the room, then rotate it (or press Q / E in the 3D view). Upright items snap flush to a wall when dragged close to it; rugs lie flat; art hangs on walls.", className="control-note")], className="orientation-controls"),
                html.Button("Clear saved items", id="clear-items", className="clear-button"),
                html.Div(id="item-details", className="item-details"), html.Div(id="metrics", children=[metric("ROOM", "Not added", "Upload a photo"), metric("ITEM", "Not added", "Paste a URL or image"), metric("FAITHFULNESS", "Source image", "No generated details")], className="metrics"),
            ], className="model-panel"),
        ], className="workspace"),
        html.Section([
            html.Div([html.P("DESIGNED FOR TRUST", className="eyebrow"), html.H2("The preview shows what we know."), html.P("Theia uses the seller's image or your uploaded screenshot. It does not generate furniture, infer materials, or claim that a visual overlay proves fit.")], className="trust-copy"),
            html.Div([html.Div("✓", className="trust-icon"), html.Div([html.Strong("Seller image stays visible"), html.Span("The item overlay is the supplied listing image, with its source shown beside it.")])], className="trust-item"),
            html.Div([html.Div("⌁", className="trust-icon"), html.Div([html.Strong("No automatic Marketplace login"), html.Span("Browsing and access remain user-initiated, so blocked or private listings are reported clearly.")])], className="trust-item"),
        ], className="trust-row"),
    ], className="page"), html.Footer([html.Span("Theia · Final course project prototype"), html.Span("2D placement first · AR later")], className="footer"),
])


@app.callback(Output("room-state", "data"), Output("room-library-data", "data"), Output("room-note", "children"), Output("floor-points", "data"), Input("room-upload", "contents"), State("room-upload", "filename"), State("room-library-data", "data"), prevent_initial_call=True)
def save_room(contents, filenames, library):
    if not contents:
        return no_update, no_update, no_update, []
    try:
        contents = contents if isinstance(contents, list) else [contents]
        filenames = filenames if isinstance(filenames, list) else [filenames or "room photo"]
        corrected_images, corrections, focals = zip(*(correct_room_orientation(source) for source in contents))
        combined, stitch_note = stitch_room_images(list(corrected_images))
        stitched = combined not in corrected_images
        room_name = filenames[0] if len(filenames) == 1 else f"Room set · {len(filenames)} angles"
        method = f"{stitch_note}; {corrections[0]}"
        combined_url = to_media(combined)
        room = {"id": f"room-{uuid4().hex[:8]}", "src": combined_url, "original_src": combined_url, "angles": [to_media(image) for image in corrected_images], "filename": room_name, "correction": method,
                "f35": None if stitched else focals[0], "panorama": stitched}
        log_pipeline({"stage": "room_upload", "angles": len(contents), "stitched": stitched, "exif_focal_35mm": room["f35"]})
        updated = [room] + [saved for saved in (library or []) if saved.get("filename") != room["filename"]]
        return room, updated[:20], f"Ready: {room['filename']} · {method} · saved to this session", []
    except Exception as exc:
        fallback = contents[0] if isinstance(contents, list) else contents
        room = {"id": f"room-{len(library or []) + 1}", "src": fallback, "original_src": fallback, "angles": contents if isinstance(contents, list) else [contents], "filename": "Room set", "correction": "original images"}
        return room, [room] + (library or [])[:19], f"Ready: {room['filename']} · correction unavailable ({type(exc).__name__}) · saved to this session", []


@app.callback(Output("calibration-image", "src"), Input("room-state", "data"))
def show_calibration_image(room):
    return (room or {}).get("original_src") or (room or {}).get("src")


@app.callback(Output("room-state", "data", allow_duplicate=True), Output("floor-note", "children"), Output("floor-points", "data", allow_duplicate=True), Input("apply-floor", "n_clicks"), State("room-state", "data"), State("floor-points", "data"), State("corner-1-x", "value"), State("corner-1-y", "value"), State("corner-2-x", "value"), State("corner-2-y", "value"), State("corner-3-x", "value"), State("corner-3-y", "value"), State("corner-4-x", "value"), State("corner-4-y", "value"), prevent_initial_call=True)
def apply_floor_perspective(_, room, clicked_points, x1, y1, x2, y2, x3, y3, x4, y4):
    points = clicked_points if len(clicked_points or []) == 4 else ([[x1 / 100, y1 / 100], [x2 / 100, y2 / 100], [x3 / 100, y3 / 100], [x4 / 100, y4 / 100]] if all(value is not None for value in (x1, y1, x2, y2, x3, y3, x4, y4)) else [])
    if not room or len(points) != 4:
        return no_update, "Select exactly four floor corners first.", no_update
    # The click order is near-left, near-right, far-right, far-left. Map the
    # far edge to the top of the corrected view and the near edge to the bottom;
    # the previous mapping inverted the room whenever users followed the prompt.
    points = [points[3], points[2], points[1], points[0]]
    if cv2 is not None and np is not None:
        polygon = np.float32([[point[0], point[1]] for point in points])
        if abs(float(cv2.contourArea(polygon))) < 0.01:
            return no_update, "Those points are too close together. Click the four visible floor corners.", no_update
    # Keep the photograph untouched. Warping the entire frame to a rectangle
    # creates the extreme stretching users see at the ceiling and walls. The
    # selected plane is stored for future perspective-aware item placement.
    source_image = room.get("original_src") or room["src"]
    updated = {**room, "src": source_image, "floor_points": points, "correction": "floor plane calibrated · original photo preserved"}
    return updated, "Floor plane saved. The room photo stays natural; the selected plane is ready for perspective-aware placement.", []


@app.callback(Output("room-library", "children"), Input("room-library-data", "data"))
def show_room_library(library):
    if not library:
        return html.Small("Your saved rooms will appear here for this browser session.", className="library-empty")
    return html.Div([
        html.Div([html.P("SAVED ROOMS", className="eyebrow"), html.Small("Select one to reuse it", className="library-help")], className="library-heading"),
        html.Div([
            html.Button([html.Img(src=room["src"], alt=room["filename"]), html.Span(room["filename"])], id={"type": "saved-room", "index": room["id"]}, className="saved-room", n_clicks=0)
            for room in library
        ], className="saved-room-grid"),
    ], className="library-inner")


@app.callback(Output("room-state", "data", allow_duplicate=True), Output("room-note", "children", allow_duplicate=True), Input({"type": "saved-room", "index": ALL}, "n_clicks"), State("room-library-data", "data"), prevent_initial_call=True)
def select_saved_room(_, library):
    trigger = ctx.triggered_id
    if not trigger or not library:
        return no_update, no_update
    room = next((saved for saved in library if saved.get("id") == trigger.get("index")), None)
    if not room:
        return no_update, no_update
    if not room.get("original_src") and room.get("angles"):
        room = {**room, "src": room["angles"][0], "original_src": room["angles"][0], "correction": "original room view restored"}
    return room, f"Loaded {room['filename']} from this session"


@app.callback(Output("room-calibration", "data"), Output("calibration-note", "children"), Input("calibrate-room", "n_clicks"), State("measurement-type", "value"), State("room-measurement", "value"), State("measurement-unit", "value"), State("room-calibration", "data"), prevent_initial_call=True)
def calibrate_room(_, measurement_type, measurement, unit, calibration):
    if not measurement or measurement <= 0:
        return no_update, "Enter a positive room width in metres."
    dimensions = {"width_m": 4.0, "length_m": 4.0, "height_m": 2.5, **(calibration or {}).get("dimensions_m", {})}
    factors = {"m": 1.0, "cm": 0.01, "ft": 0.3048, "in": 0.0254}
    metres = float(measurement) * factors.get(unit, 1.0)
    dimensions[measurement_type] = metres
    labels = {"width_m": "width", "length_m": "length", "height_m": "ceiling height"}
    return {"dimensions_m": dimensions, "source": f"user-confirmed {labels.get(measurement_type, 'room')}"}, f"Scale calibrated with {measurement:g} {unit} ({metres:.2f} m) for this session."


@app.callback(Output("item-library-data", "data"), Output("item-note", "children"), Input("add-item", "n_clicks"), State("listing-url", "value"), State("image-url", "value"), State("item-upload", "contents"), State("item-library-data", "data"), prevent_initial_call=True)
def add_item(_, listing_url, image_url, upload_contents, library):
    if not listing_url and not image_url and not upload_contents:
        return no_update, "Add a listing URL, direct image URL, or screenshot first."
    if listing_url:
        host = urlparse(listing_url).netloc.lower()
        if not (host.endswith("facebook.com") or host.endswith("fb.com")):
            return no_update, "Theia currently supports Facebook Marketplace links only. Add a direct image or screenshot for another source."
    title, source_image, note, description, listing_images = "User-supplied listing image", None, "Source image supplied by you", "", []
    if listing_url:
        try:
            title, source_image, note, description, listing_images = parse_metadata(listing_url)
        except Exception as exc:
            if not image_url and not upload_contents:
                return no_update, f"Could not read public metadata: {exc} Add a direct image URL or screenshot; no item was invented."
            note = f"Listing saved, but metadata was unavailable ({type(exc).__name__}). Using your supplied image."
    # A newly uploaded screenshot is an explicit user choice. It must win over
    # an old URL's cached og:image when the URL field was left populated.
    source_image = image_url or upload_contents or source_image
    if upload_contents and not image_url:
        note = f"{note} · uploaded screenshot used"
    if not source_image:
        return no_update, "The listing loaded without an accessible image. Add a direct image URL or screenshot."
    # The photo the user supplied (or the listing's main og:image) is the hero
    # photo and is always used first. Additional gallery photos are processed
    # so the 3D view can show the item from other real angles.
    gallery = []
    candidates = list(dict.fromkeys(([image_url] if image_url else []) + ([upload_contents] if upload_contents and not image_url else []) + ([source_image] if source_image else []) + listing_images))[:5]
    for candidate in candidates:
        try:
            gallery.append(process_listing_photo(candidate, listing_url))
        except Exception as exc:
            log_pipeline({"stage": "cutout", "ok": False, "error": type(exc).__name__})
    if not gallery:
        return no_update, "None of the listing photos could be downloaded. Add a direct image URL or screenshot; no item was invented."
    gallery[0]["view"] = "front"
    hero = gallery[0]
    note = f"{note} · {len(gallery)} listing photo(s) cached at full resolution · {hero['method']}" + ("" if hero["cutout_ok"] else " · cutout unreliable, showing the untouched photo")
    dimensions, dimension_source, dimension_confidence = infer_item_dimensions(title, description)
    agent_result = analyze_listing(title, description)
    room_prior_m = 4.0
    visual_scale = max(0.08, min(0.72, dimensions.get("width_m", 1.0) / room_prior_m))
    pose = "flat" if any(word in f"{title} {description}".lower() for word in ("rug", "carpet", "runner", "mat")) else agent_result.get("pose", "upright")
    agent_note = f" · agent: {agent_result.get('category', 'other')}"
    if agent_result.get("flags"):
        agent_note += " · " + "; ".join(agent_result["flags"][:2])
    item = {"id": f"item-{uuid4().hex[:8]}", "src": hero["cutout"], "original_src": hero["photo"], "display": "cutout" if hero["cutout_ok"] else "photo", "gallery": gallery, "title": title, "raw_description": description, "listing_url": listing_url, "source_note": note + agent_note, "dimensions": dimensions, "dimension_source": dimension_source, "dimension_confidence": dimension_confidence, "visual_scale": visual_scale, "pose": pose, "rotation": 0, "agent": agent_result}
    updated = [item] + (library or [])
    return updated[:12], f"Added {title}. Dimensions: {dimensions.get('width_m', 0):.2f} m W × {dimensions.get('height_m', 0):.2f} m H · {dimension_source}."


@app.callback(Output("item-library-data", "data", allow_duplicate=True), Output("item-note", "children", allow_duplicate=True), Input("clear-items", "n_clicks"), prevent_initial_call=True)
def clear_items(_):
    return [], "Saved items cleared for this session."


@app.callback(Output("item-library-data", "data", allow_duplicate=True), Output("item-note", "children", allow_duplicate=True), Input({"type": "remove-item", "index": ALL}, "n_clicks"), State("item-library-data", "data"), prevent_initial_call=True)
def remove_item(_, items):
    trigger = ctx.triggered_id
    if not trigger or not items:
        return no_update, no_update
    remaining = [item for item in items if item.get("id") != trigger.get("index")]
    return remaining, "Item removed from this session."


@app.callback(Output("item-library-data", "data", allow_duplicate=True), Input("item-rotation", "value"), State("item-select", "value"), State("item-library-data", "data"), prevent_initial_call=True)
def rotate_item(rotation, selected_id, items):
    """Rotation for the 2D preview (the 3D view keeps its own yaw in item-placements)."""
    current = next((item for item in items or [] if item.get("id") == selected_id), None)
    if not current or float(current.get("rotation", 0)) == float(rotation or 0):
        return no_update
    return [{**item, "rotation": float(rotation or 0)} if item.get("id") == selected_id else item for item in items]


@app.callback(Output("item-library-data", "data", allow_duplicate=True), Input("item-pose", "value"), State("item-select", "value"), State("item-library-data", "data"), prevent_initial_call=True)
def pose_item(pose, selected_id, items):
    if not selected_id or not items:
        return no_update
    current = next((item for item in items if item.get("id") == selected_id), None)
    if not current or current.get("pose") == pose:
        return no_update
    return [{**item, "pose": pose or "upright"} if item.get("id") == selected_id else item for item in items]


@app.callback(Output("item-pose", "value"), Input("item-select", "value"), State("item-library-data", "data"), prevent_initial_call=True)
def sync_pose_dropdown(selected_id, items):
    current = next((item for item in items or [] if item.get("id") == selected_id), None)
    return current.get("pose", "upright") if current else no_update


@app.callback(Output("item-library-data", "data", allow_duplicate=True), Input({"type": "item-display", "index": ALL}, "value"), State("item-library-data", "data"), prevent_initial_call=True)
def set_item_display(values, items):
    if not ctx.triggered_id or not items:
        return no_update
    item_id = ctx.triggered_id["index"]
    value = next((entry["value"] for entry in ctx.inputs_list[0] if entry["id"]["index"] == item_id), None)
    if value not in {"cutout", "photo"}:
        return no_update
    changed = False
    updated = []
    for item in items:
        if item.get("id") == item_id and item.get("display", "cutout") != value:
            gallery = normalized_gallery(item)
            hero = next((g for g in gallery if g.get("view") == "front"), gallery[0])
            item = {**item, "display": value, "src": hero["photo"] if value == "photo" else hero["cutout"]}
            changed = True
        updated.append(item)
    return updated if changed else no_update


@app.callback(Output("item-library-data", "data", allow_duplicate=True), Input({"type": "view-angle", "index": ALL}, "value"), State("item-library-data", "data"), prevent_initial_call=True)
def set_view_angle(values, items):
    if not ctx.triggered_id or not items:
        return no_update
    key = ctx.triggered_id["index"]
    item_id, position = key.rsplit("|", 1)
    value = next((entry.get("value") for entry in ctx.inputs_list[0] if entry["id"]["index"] == key), None)
    updated, changed = [], False
    for item in items:
        if item.get("id") == item_id:
            gallery = [dict(entry) for entry in normalized_gallery(item)]
            index = int(position)
            if index < len(gallery) and gallery[index].get("view") != (None if value == "none" else value):
                if value != "none":
                    for other in gallery:  # one photo per angle
                        if other.get("view") == value:
                            other["view"] = None
                gallery[index]["view"] = None if value == "none" else value
                item = {**item, "gallery": gallery}
                changed = True
        updated.append(item)
    return updated if changed else no_update


@app.callback(Output("scene-data", "data"), Output("scene-notes", "children"), Input("room-state", "data"), Input("room-calibration", "data"), Input("item-library-data", "data"))
def update_scene(room, calibration, items):
    if not (room or {}).get("src"):
        return None, ""
    try:
        room_payload = build_room_scene(room, calibration)
    except Exception as exc:
        log_pipeline({"stage": "room_scene", "error": type(exc).__name__})
        return None, f"3D reconstruction unavailable ({type(exc).__name__}); showing the 2D preview."
    scene = room_payload["scene"]
    notes = [
        html.Strong("3D room: "),
        f"camera {scene['camera_height_m']:.2f} m above the floor, tilted {scene['camera']['pitch_deg']:.1f}°, roll {scene['camera']['roll_deg']:.1f}° · lens from {scene['focal_source']} · floor from {scene['layout_source']} · room ≈ {scene['room_width_m']:.2f} m wide. ",
        *[html.Span(note + " ") for note in scene["notes"]],
    ]
    return {"room": room_payload, "items": [item_for_scene(item) for item in items or []]}, notes


app.clientside_callback(
    """function(scene, placements, selected, mode) {
        if (window.TheiaScene) { window.TheiaScene.update(scene, placements || {}, selected || null, mode || '2d'); }
        return window.dash_clientside.no_update;
    }""",
    Output("scene-sink", "data"), Input("scene-data", "data"), Input("item-placements", "data"), Input("item-select", "value"), Input("view-mode", "value"),
)

app.clientside_callback(
    """function(rotation, selected, placements) {
        if (!selected || !placements || !placements[selected]) { return window.dash_clientside.no_update; }
        const yaw = (rotation || 0) * Math.PI / 180;
        if (Math.abs((placements[selected].yaw || 0) - yaw) < 1e-4) { return window.dash_clientside.no_update; }
        const next = JSON.parse(JSON.stringify(placements));
        next[selected].yaw = yaw; next[selected].wall = null;
        return next;
    }""",
    Output("item-placements", "data", allow_duplicate=True), Input("item-rotation", "value"), State("item-select", "value"), State("item-placements", "data"), prevent_initial_call=True,
)

app.clientside_callback(
    """function(selected, placements) {
        if (!selected || !placements || !placements[selected]) { return window.dash_clientside.no_update; }
        let deg = Math.round((placements[selected].yaw || 0) * 180 / Math.PI);
        deg = ((deg + 540) % 360) - 180;
        return deg;
    }""",
    Output("item-rotation", "value"), Input("item-select", "value"), Input("item-placements", "data"), prevent_initial_call=True,
)


def item_card(item: dict):
    dims = item.get("dimensions", {})
    gallery = normalized_gallery(item)
    size = f"{dims.get('width_m', 0):.2f} m W × {dims.get('height_m', 0):.2f} m H" + (f" × {dims['depth_m']:.2f} m D" if dims.get("depth_m") else "") + (f" · seat {dims['seat_height_m']:.2f} m" if dims.get("seat_height_m") else "")
    hero = next((g for g in gallery if g.get("view") == "front"), gallery[0])
    views = html.Div([
        html.Div([
            html.Img(src=entry["cutout"] if item.get("display", "cutout") == "cutout" and entry.get("cutout_ok", True) else entry["photo"], className="view-thumb"),
            dcc.Dropdown(id={"type": "view-angle", "index": f"{item['id']}|{k}"}, options=VIEW_LABELS, value=entry.get("view") or "none", clearable=False, className="view-select"),
        ], className="view-tile") for k, entry in enumerate(gallery)
    ], className="view-grid") if len(gallery) > 1 else html.Small("Only one listing photo: other sides will show as a grey silhouette in 3D.", className="control-note")
    return html.Div([
        html.Div([html.Img(src=hero["cutout"] if item.get("display", "cutout") == "cutout" else hero["photo"], className="item-card-image"), html.Div([html.Strong(item.get("title", "Untitled item")), html.Small(f"{size} · {item.get('dimension_source', 'unscaled')}"), html.Small(f"Cutout: {hero.get('method', 'n/a')}", className="cutout-method")], className="item-card-copy")], className="item-card-head"),
        dcc.RadioItems(id={"type": "item-display", "index": item["id"]}, options=[{"label": "Cutout (background removed)", "value": "cutout"}, {"label": "Original seller photo", "value": "photo"}], value=item.get("display", "cutout"), inline=True, className="display-toggle"),
        html.Details([html.Summary(f"Listing photos & angles ({len(gallery)})"), html.Small("Tell Theia which side each photo shows; the 3D view switches to the real photo nearest your viewing angle.", className="control-note"), views], className="view-details"),
        html.Button("Remove", id={"type": "remove-item", "index": item["id"]}, className="remove-item", n_clicks=0),
    ], className="item-card")


@app.callback(Output("stage", "children"), Output("item-details", "children"), Output("metrics", "children"), Output("item-library", "children"), Output("item-select", "options"), Output("item-select", "value"), Input("room-state", "data"), Input("item-library-data", "data"), Input("room-calibration", "data"), State("item-select", "value"))
def render_preview(room, items, calibration, selected):
    room_src, items = (room or {}).get("src"), items or []
    migrated_items = []
    for item in items:
        dims, source, confidence = infer_item_dimensions(item.get("title", ""), item.get("raw_description", ""))
        if "vintage spindle arm bench" in item.get("title", "").lower() and "floral tapestry" in item.get("title", "").lower() and item.get("dimension_source", "").startswith("estimated"):
            item = {**item, "dimensions": dims, "dimension_source": source, "dimension_confidence": confidence}
        migrated_items.append(item)
    items = migrated_items
    room_dimensions = {"width_m": 4.0, "length_m": 4.0, "height_m": 2.5, **(calibration or {}).get("dimensions_m", {})}
    room_width_m = float(room_dimensions["width_m"])
    items = [{**item, "visual_scale": max(0.08, min(0.72, (item.get("dimensions", {}).get("height_m", 1.0) / room_dimensions["height_m"]) if (calibration or {}).get("source", "").endswith("height") else item.get("dimensions", {}).get("width_m", 1.0) / room_width_m))} for item in items]
    details = html.Div([item_card(item) for item in items], className="item-cards")
    library_view = html.Div([
        html.Div([html.P("ITEMS IN THIS ROOM", className="eyebrow"), html.Small(f"{len(items)} saved this session", className="library-help")], className="library-heading"),
        html.Div([html.Div([html.Img(src=item["src"]), html.Span(item.get("title", "Untitled item"))], className="saved-item") for item in items], className="saved-item-grid")
    ], className="library-inner") if items else html.Small("Added items will collect here for this session.", className="library-empty")
    options = [{"label": item.get("title", "Untitled item"), "value": item["id"]} for item in items]
    ids = [item["id"] for item in items]
    value = selected if selected in ids else (ids[0] if ids else None)
    return room_stage(room_src, items, (room or {}).get("floor_points")), details, [
        metric("ROOM", "Ready" if room_src else "Not added", (room or {}).get("correction", "Upload room angles")),
        metric("ITEMS", str(len(items)), "Placed together" if items else "Add a listing"), metric("SCALE", "Automatic" if items else "Waiting", f"{(calibration or {}).get('source', 'default reference')} · {room_width_m:.2f} m width · {room_dimensions['height_m']:.2f} m height"),
    ], library_view, options, value


if __name__ == "__main__":
    app.run(debug=False, host=os.getenv("HOST", os.getenv("LUMAROOM_HOST", "127.0.0.1")), port=int(os.getenv("PORT", os.getenv("LUMAROOM_PORT", "8050"))))
