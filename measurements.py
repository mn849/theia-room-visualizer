"""Read item measurements from Marketplace listing text, and fill gaps honestly.

Order of evidence
-----------------
1. Seller-stated numbers in the title/description, in any common format:
   "24 W x 16 D x 30 H", "Width: 60cm", "30 inches tall", "24"H", "seat height 18"
   (kept separately), "20 x 16 x 24 in" (assigned using the photo's shape).
2. If the seller gives only ONE of width/height, the other is deduced from the
   item's own photo: the cutout's width/height ratio, corrected for the usual
   listing-photo viewpoint (camera a little above and to the side shows some
   of the top and side, which makes items look deeper/taller than they are).
3. If the seller gives nothing, a typical size for the item category is used
   for the height, and the width again comes from the photo's proportions.

Every result says which of these it came from.
"""
from __future__ import annotations

import html
import re

INCH = 0.0254
UNIT = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": INCH, "inch": INCH, "inches": INCH, '"': INCH, "”": INCH, "''": INCH, "ft": 0.3048, "feet": 0.3048, "'": 0.3048}
UNIT_RE = r"(mm|cm|m|inches|inch|in|ft|feet|\"|”|'')?"
NUM = r"(\d+(?:\.\d+)?)"
LABEL = {"w": "width_m", "wide": "width_m", "width": "width_m", "l": "width_m", "long": "width_m", "length": "width_m",
         "h": "height_m", "high": "height_m", "height": "height_m", "tall": "height_m",
         "d": "depth_m", "deep": "depth_m", "depth": "depth_m"}

# Typical (height, width/height, depth/width) by category.
PRIORS = {
    "nightstand": (0.60, 0.80, 0.80), "bedside": (0.60, 0.80, 0.80), "side table": (0.55, 0.90, 0.90), "end table": (0.55, 0.90, 0.90),
    "coffee table": (0.45, 2.40, 0.55), "dining table": (0.75, 2.00, 0.55), "desk": (0.75, 1.70, 0.50), "table": (0.75, 1.60, 0.60),
    "sofa": (0.85, 2.40, 0.45), "couch": (0.85, 2.40, 0.45), "loveseat": (0.85, 1.80, 0.55), "armchair": (0.90, 0.95, 1.00),
    "dining chair": (0.90, 0.50, 1.05), "chair": (0.85, 0.65, 1.00), "stool": (0.65, 0.65, 1.00), "bench": (0.50, 2.30, 0.55),
    "dresser": (0.85, 1.50, 0.45), "chest": (1.00, 0.85, 0.55), "cabinet": (0.90, 1.00, 0.45), "bookcase": (1.80, 0.45, 0.35), "shelf": (1.50, 0.60, 0.40),
    "lamp": (1.50, 0.25, 1.00), "mirror": (1.00, 0.70, 0.05), "painting": (0.60, 1.30, 0.05), "art": (0.60, 1.30, 0.05), "print": (0.50, 0.80, 0.04),
    "rug": (0.02, 1.40, 0.70), "birdcage": (1.40, 0.40, 0.90), "plant stand": (0.80, 0.40, 1.00), "bed": (1.00, 1.50, 1.40),
}


def _to_m(value: str, unit: str | None, default_unit: float) -> float:
    return float(value) * (UNIT.get((unit or "").lower(), default_unit) if unit else default_unit)


def parse_stated(title: str, description: str) -> tuple[dict[str, float], list[float]]:
    """Return labelled seller dimensions (metres) plus unlabelled AxBxC numbers."""
    text = html.unescape(f"{title}\n{description}").lower().replace("×", "x")
    # Web-page noise ("width:100px", "height: 50%") is not a measurement.
    text = re.sub(r"\d+(?:\.\d+)?\s*(?:px|%|r?em|vh|vw|pt)\b", " ", text)
    # Facebook timestamps ("Listed 1w ago", "3 d ago", "2 h ago") are not sizes;
    # "1w ago" was being read as a 1-inch-wide table.
    text = re.sub(r"\b\d+\s*(?:w|d|h|m|y|mo|min|mins|hr|hrs|wk|wks|weeks?|days?|hours?|months?|years?)\s+ago\b", " ", text)
    # Sizes found in the seller's own text take priority over everything else.
    seller = re.search(r"seller sizes:([^\n]*)", text)
    if seller:
        own, _ = parse_stated(seller.group(1), "")
        if "width_m" in own and "height_m" in own:
            return own, []
    text = re.sub(r"seat\s*height\s*[:=]?\s*\d+(?:\.\d+)?\s*(?:mm|cm|m|inches|inch|in|\"|”)?", " ", text)  # never mistake seat height for height
    default_unit = 0.01 if re.search(r"\d\s*cm\b", text) and not re.search(r"\d\s*(?:in|inch|inches|\"|”)\b", text) else INCH
    found: dict[str, float] = {}

    def put(label: str, value: str, unit: str | None):
        key = LABEL.get(label.strip(". ").lower())
        if key and key not in found:
            metres = _to_m(value, unit, default_unit)
            if 0.02 <= metres <= 6:
                found[key] = metres

    # "24 W", "24in wide", "24" H", "60 cm tall"
    for value, unit, label in re.findall(NUM + r"\s*" + UNIT_RE + r"\s*(width|wide|w|height|high|tall|h|depth|deep|d|length|long|l)\b\.?", text):
        put(label, value, unit)
    # "width: 24 in", "H 30", "depth - 40cm"
    for label, value, unit in re.findall(r"\b(width|w|height|h|depth|d|length|l)\b\.?\s*[:=\-]?\s*" + NUM + r"\s*" + UNIT_RE, text):
        put(label, value, unit)
    # Unlabelled "20 x 16 x 24 in" / "60x40 cm"
    unlabelled: list[float] = []
    pattern = NUM + r"\s*" + UNIT_RE + r"\s*x\s*" + NUM + r"\s*" + UNIT_RE + r"(?:\s*x\s*" + NUM + r"\s*" + UNIT_RE + r")?"
    matches = list(re.finditer(pattern, text))
    # Prefer a size that states its unit ("5 x 8 ft") over a bare one ("5x8").
    match = next((m for m in matches if any(m.groups()[1::2])), matches[0] if matches else None)
    if match and not found:
        groups = match.groups()
        trailing = next((u for u in reversed(groups[1::2]) if u), None)
        for value, unit in zip(groups[0::2], groups[1::2]):
            if value:
                metres = _to_m(value, unit or trailing, default_unit)
                if 0.02 <= metres <= 6:
                    unlabelled.append(metres)
    seat = re.search(r"seat\s*height\s*[:=]?\s*" + NUM + r"\s*" + UNIT_RE, html.unescape(f"{title} {description}").lower())
    if seat:
        found["seat_height_m"] = _to_m(seat.group(1), seat.group(2), default_unit)
    return found, unlabelled


def category_of(title: str, description: str, agent_category: str | None = None) -> str | None:
    text = f"{title} {agent_category or ''}".lower()
    for name in PRIORS:  # dict order puts specific names ("coffee table") before generic ("table")
        if re.search(rf"\b{name}s?\b", text):
            return name
    text = f"{text} {description[:300].lower()}"
    for name in PRIORS:
        if re.search(rf"\b{name}s?\b", text):
            return name
    return None


def _view_factor(aspect: float, depth_ratio: float) -> float:
    """How much wider-than-true a typical listing photo makes an item look.

    Listing photos are usually taken from slightly above (~10-15 degrees) and
    slightly to the side (~20 degrees): the photo shows a sliver of the top and
    of one side. Projected width ~ W + D*sin(20deg); projected height ~
    H + D*sin(12deg). Returns k such that W = aspect * H / k.
    """
    r = max(0.0, min(depth_ratio, 1.5))
    return 1.0 + r * (0.34 - 0.21 * aspect)


def infer_dimensions(title: str, description: str, aspect: float | None = None, agent_category: str | None = None, agent_dims: dict | None = None) -> tuple[dict[str, float], str, str]:
    """Return (dimensions in metres, source label, confidence label).

    agent_dims: sizes gpt-6-luna read from the full listing text, already
    verified to appear literally in that text. They take priority over the
    regex parser, which is kept as the fallback."""
    stated, unlabelled = parse_stated(title, description)
    if agent_dims and ("width_m" in agent_dims or "height_m" in agent_dims):
        stated = {**{k: v for k, v in stated.items() if k == "seat_height_m"}, **agent_dims}
        unlabelled = []
    category = category_of(title, description, agent_category)
    _, prior_ratio, depth_ratio = PRIORS.get(category, (None, 1.0, 0.6))
    aspect = aspect if aspect and 0.05 < aspect < 20 else None

    if unlabelled and not any(k in stated for k in ("width_m", "height_m")):
        values = unlabelled[:3]
        if len(values) == 3:
            # Choose which number is height so width/height best matches the photo.
            best = None
            for h_index in range(3):
                rest = [v for i, v in enumerate(values) if i != h_index]
                w, d = max(rest), min(rest)
                target = aspect * _view_factor(aspect, d / max(w, 1e-6)) if aspect else prior_ratio
                score = abs((w / values[h_index]) - target)
                if best is None or score < best[0]:
                    best = (score, w, values[h_index], d)
            stated.update(width_m=best[1], height_m=best[2], depth_m=best[3])
        elif len(values) == 2:
            a, b = values
            if aspect:
                stated.update(width_m=a, height_m=b) if abs(a / b - aspect) <= abs(b / a - aspect) else stated.update(width_m=b, height_m=a)
            else:
                stated.update(width_m=max(a, b), height_m=min(a, b))

    if category == "rug":
        sizes = sorted(unlabelled[:2] or [v for k, v in stated.items() if k in ("width_m", "height_m", "depth_m")], reverse=True)
        if len(sizes) >= 2:
            return {"width_m": sizes[0], "depth_m": sizes[1], "height_m": 0.01}, "seller-stated rug size", "High: seller's own numbers"
    # Sanity check: page numbers that are far too small (or large) for this
    # kind of item are not its measurements (e.g. "2 drawers", hardware sizes).
    prior_h = PRIORS.get(category, (0.8,))[0]  # unknown item: assume furniture-sized
    if prior_h and prior_h > 0.3:
        for key in ("width_m", "height_m"):
            if key in stated and not (0.25 * prior_h <= stated[key] <= 2.5 * prior_h + 0.5):
                stated.pop(key)
    has_w, has_h = "width_m" in stated, "height_m" in stated
    dims = dict(stated)
    if has_w and has_h:
        dims.setdefault("depth_m", min(dims["width_m"], dims["width_m"] * depth_ratio))
        complete = "depth_m" in stated
        label = "seller-stated dimensions (read from the listing by gpt-6-luna, verified against the text)" if agent_dims else "seller-stated dimensions"
        return dims, label + ("" if complete else ", depth estimated"), "High: seller's own numbers" + ("" if complete else "; depth is a typical ratio")
    if has_h and not has_w:
        k = _view_factor(aspect or prior_ratio, depth_ratio)
        dims["width_m"] = dims["height_m"] * (aspect or prior_ratio) / k
        dims.setdefault("depth_m", dims["width_m"] * depth_ratio)
        how = "the item's photo proportions (corrected for camera angle)" if aspect else f"a typical {category or 'item'} shape"
        return dims, f"seller-stated height; width deduced from {how}", "Medium: one seller measurement, the rest deduced"
    if has_w and not has_h:
        k = _view_factor(aspect or prior_ratio, depth_ratio)
        dims["height_m"] = dims["width_m"] * k / (aspect or prior_ratio)
        dims.setdefault("depth_m", dims["width_m"] * depth_ratio)
        how = "the item's photo proportions (corrected for camera angle)" if aspect else f"a typical {category or 'item'} shape"
        return dims, f"seller-stated width; height deduced from {how}", "Medium: one seller measurement, the rest deduced"
    prior_h = PRIORS.get(category, (0.8, 1.0, 0.6))[0]
    dims["height_m"] = prior_h
    k = _view_factor(aspect or prior_ratio, depth_ratio)
    dims["width_m"] = prior_h * (aspect or prior_ratio) / k
    dims["depth_m"] = dims["width_m"] * depth_ratio
    label = f"estimated: typical {category} height, width from photo proportions" if category else "estimated from photo proportions (no measurements in listing)"
    return dims, label, "Low: no seller measurements; ask the seller before relying on fit"
