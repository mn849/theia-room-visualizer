"""Build the self-contained report.html and slides.html.

    python docs/build_docs.py

* Images in docs/figures/ and screenshots in docs/screenshots/<name>.png|jpg
  are embedded as base64, so both HTML files open anywhere by double-click.
* Real log lines and test output are copied in from logs/ and
  docs/evidence/test_output.txt, so the evidence always matches the code.
* docs/report_config.json holds the repo link and the real-user write-up.
"""
from __future__ import annotations

import base64
import html
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
CONFIG = json.loads((DOCS / "report_config.json").read_text()) if (DOCS / "report_config.json").exists() else {}


def data_uri(path: Path) -> str:
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def screenshot(name: str) -> Path | None:
    for ext in (".png", ".jpg", ".jpeg"):
        path = DOCS / "screenshots" / f"{name}{ext}"
        if path.exists():
            return path
    return None


def figure(path: Path | None, caption: str, name: str = "") -> str:
    if path is None:
        return f'<figure><div class="shot-missing">Screenshot <strong>{html.escape(name)}</strong> goes here: {html.escape(caption)}<br><small>Save it as docs/screenshots/{html.escape(name)}.png and run python docs/build_docs.py</small></div></figure>'
    return f'<figure><img src="{data_uri(path)}" alt="{html.escape(caption)}"><figcaption>{html.escape(caption)}</figcaption></figure>'


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def log_block(kind: str) -> str:
    agent = jsonl(ROOT / "logs" / "agent_calls.jsonl")
    pipe = jsonl(ROOT / "logs" / "pipeline_events.jsonl")
    if kind == "agent":
        rows = [r for r in agent if r.get("status") == "success"]
        rows = [r for r in rows if r.get("model") == "gpt-6-luna"] or [r for r in rows if r.get("input_tokens") == 625] or rows
        rows = rows[-1:]
    elif kind == "room_upload":
        rows = [r for r in pipe if r.get("stage") == "room_upload"][-1:]
    elif kind == "room_scene":
        rows = [r for r in pipe if r.get("stage") == "room_scene" and str(r.get("layout_source", "")).startswith("clicked")][-1:] or [r for r in pipe if r.get("stage") == "room_scene"][-1:]
    else:
        rows = []
    return html.escape("\n".join(json.dumps(r) for r in rows) or "(no log line yet: run the app once)")


def test_block(kind: str) -> str:
    text = (DOCS / "evidence" / "test_output.txt").read_text() if (DOCS / "evidence" / "test_output.txt").exists() else ""
    if kind == "all":
        return html.escape(text)
    match = re.search(r"\$ python tests/test_agent_safety\.py\n(.*?)(?=\n\$ |\Z)", text, re.S)
    return html.escape(match.group(1).strip() if match else text)


def git_log() -> str:
    try:
        out = subprocess.run(["git", "log", "--date=format:%Y-%m-%d %H:%M", "--pretty=%ad  %s", "-n", "25"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except Exception:
        out = ""
    return html.escape(out.strip())


def render(template: str) -> str:
    repo = CONFIG.get("repo", "")
    template = template.replace("{{repo}}", f'<a href="{html.escape(repo)}">{html.escape(repo)}</a>' if repo else "<em>add the GitHub link in docs/report_config.json</em>")
    template = template.replace("{{repo_plain}}", html.escape(repo or "<your-repo-url>"))
    template = template.replace("{{user_evidence}}", CONFIG.get("user_evidence_html", '<div class="shot-missing">Real-user test: add who tried it, the task, time taken and a quote in docs/report_config.json</div>'))
    template = template.replace("{{git_log}}", git_log())
    template = re.sub(r"\{\{log:(\w+)\}\}", lambda m: log_block(m.group(1)), template)
    template = re.sub(r"\{\{test:(\w+)\}\}", lambda m: test_block(m.group(1)), template)
    template = re.sub(r"\{\{shot:([\w-]+)\|(.*?)\}\}", lambda m: figure(screenshot(m.group(1)), m.group(2), m.group(1)), template)
    template = re.sub(r"\{\{img:([^|]+)\|(.*?)\}\}", lambda m: figure(ROOT / m.group(1) if (ROOT / m.group(1)).exists() else None, m.group(2), m.group(1)), template)
    template = template.replace("{{slide_impact}}", CONFIG.get("slide_impact_html", '<div class="missing">Real-user result goes here (docs/report_config.json → slide_impact_html)</div>'))
    template = re.sub(r"\{\{slideshot:([\w-]+)\}\}", lambda m: f'<img class="shot" src="{data_uri(screenshot(m.group(1)))}">' if screenshot(m.group(1)) else f'<div class="missing">Screenshot: {m.group(1)}</div>', template)
    template = re.sub(r"\{\{src:([^}]+)\}\}", lambda m: data_uri(ROOT / m.group(1)) if (ROOT / m.group(1)).exists() else "", template)
    template = re.sub(r"\{\{shotsrc:([\w-]+)\}\}", lambda m: data_uri(screenshot(m.group(1))) if screenshot(m.group(1)) else "", template)
    return template


if __name__ == "__main__":
    for name in ("report", "slides"):
        source = DOCS / f"{name}_template.html"
        if source.exists():
            (ROOT / f"{name}.html").write_text(render(source.read_text()))
            print(f"wrote {name}.html")
    missing = [n for n in ("room-upload", "floor-corners", "add-listing", "photo-view", "orbit-view", "top-view", "item-card", "back-silhouette", "terminal-log", "view-2d", "heart-message") if not screenshot(n)]
    if missing:
        print("screenshots still missing:", ", ".join(missing))
