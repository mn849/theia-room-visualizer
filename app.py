from __future__ import annotations

import base64
import io
import importlib.util
import pkgutil
from pathlib import Path

from dash import Dash, Input, Output, State, dcc, html, no_update
from PIL import Image

if not hasattr(pkgutil, "find_loader"):
    pkgutil.find_loader = lambda name: importlib.util.find_spec(name)

ROOT = Path(__file__).resolve().parent
app = Dash(__name__, title="LumaRoom | Room scanner")
server = app.server


def metric(label: str, value: str, detail: str):
    return html.Div([html.Span(label, className="metric-label"), html.Strong(value), html.Small(detail)], className="metric")


def empty_preview():
    return html.Div([
        html.Div("⌂", className="preview-icon"),
        html.H3("Your room model will appear here"),
        html.P("Upload a room photo to create a first-pass model. The native iPhone version will replace this image workflow with an Apple RoomPlan scan."),
    ], className="empty-preview")


app.layout = html.Div(
    [
        dcc.Store(id="scan-state", data={"status": "idle", "progress": 0, "filename": None, "size": None}),
        dcc.Interval(id="scan-clock", interval=450, n_intervals=0, disabled=True),
        html.Header(
            [
                html.Div([html.Div("LR", className="brand-mark"), html.Div([html.Strong("LumaRoom"), html.Span("room intelligence")], className="brand-copy")], className="brand"),
                html.Div([html.Span("PRIVATE BETA", className="beta-pill"), html.Span("iPhone LiDAR workflow", className="device-note")], className="top-meta"),
            ], className="topbar"
        ),
        html.Main(
            [
                html.Section(
                    [
                        html.Div([html.P("ROOM CAPTURE", className="eyebrow"), html.H1("Make your room measurable."), html.P("Build a reusable room model before you shop. LumaRoom turns a quick capture into a clear spatial baseline for furniture decisions.", className="lede")], className="hero-copy"),
                        html.Div([html.Span("01", className="step-number"), html.Div([html.Strong("Capture once"), html.Span("Save each room for reuse")], className="step-copy")], className="step-card active"),
                        html.Div([html.Span("02", className="step-number"), html.Div([html.Strong("Review the model"), html.Span("Check confidence and dimensions")], className="step-card-text")], className="step-card"),
                        html.Div([html.Span("03", className="step-number"), html.Div([html.Strong("Compare furniture"), html.Span("Coming in the next milestone")], className="step-card-text")], className="step-card"),
                    ], className="hero"
                ),
                html.Section(
                    [
                        html.Div(
                            [
                                html.Div([html.P("ROOM SETUP", className="eyebrow"), html.H2("Start with a room photo"), html.P("For this prototype, upload a clear photo. The iPhone build will guide a slow LiDAR sweep around the room and produce the same saved-room format.", className="section-intro")], className="section-heading"),
                                dcc.Upload(id="room-upload", children=html.Div([html.Span("＋", className="upload-plus"), html.Strong("Drop a room photo here"), html.Span("or choose a file · JPG, PNG, HEIC", className="upload-help")], className="upload-content"), className="upload-zone", multiple=False),
                                html.Div(id="file-note", className="file-note"),
                                html.Button("Create room model", id="scan-button", className="primary-button", n_clicks=0, disabled=True),
                                html.P("Tip: face the camera toward a corner and include the floor where possible.", className="tip"),
                            ], className="capture-panel"
                        ),
                        html.Div(
                            [
                                html.Div([html.P("ROOM MODEL", className="eyebrow"), html.H2("Living room"), html.Span("DRAFT · PHOTO BASELINE", className="status-pill")], className="preview-title"),
                                html.Div(id="preview", children=empty_preview(), className="preview-canvas"),
                                html.Div(id="metrics", children=[metric("MODEL STATUS", "Not started", "Waiting for a capture"), metric("CONFIDENCE", "—", "No geometry measured"), metric("NEXT STEP", "Scan", "Upload a room photo")], className="metrics"),
                            ], className="model-panel"
                        ),
                    ], className="workspace"
                ),
                html.Section(
                    [
                        html.Div([html.P("DESIGNED FOR TRUST", className="eyebrow"), html.H2("Every estimate has a confidence level."), html.P("The scanner keeps known measurements separate from visual estimates. When the native capture is connected, you will be able to review walls, openings, and detected furniture before using the room for shopping.")], className="trust-copy"),
                        html.Div([html.Div("✓", className="trust-icon"), html.Div([html.Strong("Private by default"), html.Span("Room images stay in your workspace until you choose to export them.")])], className="trust-item"),
                        html.Div([html.Div("⌁", className="trust-icon"), html.Div([html.Strong("Built for iteration"), html.Span("Save a room once, then test many future listings against it.")])], className="trust-item"),
                    ], className="trust-row"
                ),
            ], className="page"
        ),
        html.Footer([html.Span("LumaRoom · Final course project prototype"), html.Span("Native capture: Apple RoomPlan milestone")], className="footer"),
    ]
)


@app.callback(
    Output("scan-button", "disabled"), Output("file-note", "children"),
    Input("room-upload", "contents"), State("room-upload", "filename"),
)
def show_file(contents, filename):
    if not contents:
        return True, "No photo selected yet."
    return False, f"Ready: {filename or 'room photo'}"


@app.callback(
    Output("scan-clock", "disabled"), Output("scan-state", "data"),
    Input("scan-button", "n_clicks"), State("room-upload", "contents"), State("room-upload", "filename"),
    prevent_initial_call=True,
)
def start_scan(n_clicks, contents, filename):
    if not contents:
        return True, no_update
    try:
        encoded = contents.split(",", 1)[1]
        image = Image.open(io.BytesIO(base64.b64decode(encoded)))
        size = f"{image.width} × {image.height}px"
    except Exception:
        size = "Image metadata unavailable"
    return False, {"status": "scanning", "progress": 0, "filename": filename or "room photo", "size": size, "contents": contents}


@app.callback(
    Output("scan-clock", "disabled", allow_duplicate=True), Output("scan-state", "data", allow_duplicate=True),
    Output("preview", "children"), Output("metrics", "children"),
    Input("scan-clock", "n_intervals"), State("scan-state", "data"),
    prevent_initial_call=True,
)
def advance_scan(ticks, state):
    if not state or state.get("status") != "scanning":
        return no_update, no_update, no_update, no_update
    progress = min(100, state.get("progress", 0) + 20)
    updated = {**state, "progress": progress, "status": "complete" if progress >= 100 else "scanning"}
    bar = html.Div([html.Div(style={"width": f"{progress}%"})], className="progress-track")
    if progress < 100:
        preview = html.Div([html.Div("◌", className="scanning-icon"), html.H3("Reading the room…"), html.P("Checking perspective, floor visibility, and room boundaries."), bar, html.Span(f"{progress}% complete", className="progress-label")], className="scan-progress")
        metrics = [metric("MODEL STATUS", "Scanning", "Building a draft baseline"), metric("CONFIDENCE", "Calibrating", "Waiting for geometry checks"), metric("NEXT STEP", "Review", "Available when scan completes")]
    else:
        preview = html.Div([html.Img(src=state.get("contents"), className="room-image"), html.Div([html.Span("PHOTO BASELINE", className="image-tag"), html.Span("RoomPlan capture slot ready", className="image-tag muted")], className="image-overlay")], className="image-preview")
        metrics = [metric("MODEL STATUS", "Draft saved", state.get("filename", "Room photo")), metric("CONFIDENCE", "Visual", "LiDAR capture will add scale"), metric("NEXT STEP", "Review", "Confirm the room before shopping")]
    return progress >= 100, updated, preview, metrics


if __name__ == "__main__":
    app.run(debug=False, port=8050)
