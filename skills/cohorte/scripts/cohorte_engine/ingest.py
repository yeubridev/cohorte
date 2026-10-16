"""Turn the thing being tested into seed material: web pages, documents, subtitles, images, videos.

Writes <out>/seed.md (what agents will know) and <out>/subject.json (kind, sources, evidence level).

Evidence levels (used by the confidence index):
  full         the agents see the subject itself (article text, page content, full transcript)
  partial      part of it (an excerpt, images or frames Claude still has to describe, a trailer)
  description  only a description of it (pitch, synopsis, idea)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
import zipfile
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

from .subjects import get_subject
from .util import CohorteError, write_json

MAX_TEXT = 20000
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
TEXT_EXT = {".txt", ".md", ".markdown", ".rst"}
EVIDENCE_RANK = {"description": 0, "partial": 1, "full": 2}


class _Page(HTMLParser):
    """Extract what a visitor actually sees and reads from an HTML page."""

    SKIP = {"script", "style", "noscript", "svg", "template"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.meta: Dict[str, str] = {}
        self.headings: List[Tuple[str, str]] = []
        self.buttons: List[str] = []
        self.images_alt: List[str] = []
        self.forms = 0
        self.links = 0
        self.text: List[str] = []
        self._stack: List[str] = []
        self._buf: List[str] = []
        self._capture: Optional[str] = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self._stack.append(tag)
        if tag == "meta":
            key = a.get("name") or a.get("property")
            if key and a.get("content"):
                self.meta[key.lower()] = a["content"].strip()
        elif tag == "img" and a.get("alt"):
            self.images_alt.append(a["alt"].strip())
        elif tag == "form":
            self.forms += 1
        elif tag == "a":
            self.links += 1
        if tag in ("title", "h1", "h2", "h3", "button") or (tag == "a" and "btn" in (a.get("class") or "")):
            self._capture, self._buf = tag, []

    def handle_endtag(self, tag):
        if self._capture == tag:
            text = " ".join("".join(self._buf).split())
            if text:
                if tag == "title":
                    self.title = text
                elif tag in ("h1", "h2", "h3"):
                    self.headings.append((tag, text))
                else:
                    self.buttons.append(text)
            self._capture = None
        if tag in self._stack:
            while self._stack and self._stack.pop() != tag:
                pass

    def handle_data(self, data):
        if any(t in self.SKIP for t in self._stack):
            return
        if self._capture:
            self._buf.append(data)
        chunk = " ".join(data.split())
        if chunk:
            self.text.append(chunk)


def _html_to_md(html: str, source: str) -> Tuple[str, Dict[str, Any]]:
    p = _Page()
    p.feed(html)
    body = " ".join(p.text)
    lines = [f"### Page: {p.title or source}", f"Source: {source}"]
    if p.meta.get("description") or p.meta.get("og:description"):
        lines.append(f"Meta description: {p.meta.get('description') or p.meta.get('og:description')}")
    if p.headings:
        lines.append("Headings:\n" + "\n".join(f"- {t.upper()}: {h}" for t, h in p.headings[:40]))
    if p.buttons:
        uniq = list(dict.fromkeys(p.buttons))[:20]
        lines.append("Buttons / calls to action: " + " | ".join(uniq))
    if p.images_alt:
        lines.append("Images (alt text): " + " | ".join(p.images_alt[:20]))
    lines.append(f"Forms: {p.forms} · links: {p.links} · words: {len(body.split())}")
    lines.append("Visible text:\n" + body[:MAX_TEXT] + (" …(truncated)" if len(body) > MAX_TEXT else ""))
    return "\n\n".join(lines), {"type": "html", "title": p.title, "words": len(body.split()),
                                "evidence": "full" if len(body.split()) > 80 else "partial"}


def _fetch(url: str) -> Tuple[str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (cohorte ingest)"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            ctype = r.headers.get("content-type", "")
            data = r.read(3_000_000)
    except Exception as e:  # network errors, HTTP errors
        raise CohorteError(f"Could not fetch {url}: {e}") from e
    charset = re.search(r"charset=([\w-]+)", ctype)
    return data.decode(charset.group(1) if charset else "utf-8", errors="replace"), ctype


_TS = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


def _subtitles(path: str) -> Tuple[str, Dict[str, Any]]:
    raw = open(path, encoding="utf-8", errors="replace").read()
    last = 0.0
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        stamps = _TS.findall(line)
        if stamps:
            h, m, s, _ = stamps[-1]
            last = max(last, int(h) * 3600 + int(m) * 60 + int(s))
            continue
        if not line or line.isdigit() or line.upper() == "WEBVTT" or line.startswith(("NOTE", "STYLE")):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        if lines and lines[-1] == line:
            continue
        lines.append(line)
    text = " ".join(lines)
    words = len(text.split())
    minutes = last / 60 if last else 0
    meta = {"type": "transcript", "duration_min": round(minutes, 1), "words": words,
            "words_per_min": round(words / minutes, 1) if minutes else None,
            "evidence": "full" if words > 150 else "partial"}
    head = f"### Transcript: {os.path.basename(path)}\nDuration ≈ {meta['duration_min']} min · {words} words"
    if meta["words_per_min"]:
        head += f" · {meta['words_per_min']} words/min"
    return head + "\n\n" + text[:MAX_TEXT * 2], meta


def _docx(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


def _pdf(path: str) -> str:
    try:
        from pypdf import PdfReader  # optional dependency
    except ImportError as e:
        raise CohorteError(
            f"{path}: PDF text extraction needs `pip install pypdf`. Alternatively Claude can read the PDF and save "
            "its text as a .md file to ingest."
        ) from e
    return "\n".join((page.extract_text() or "") for page in PdfReader(path).pages)


def _video(path: str, frames_dir: str, n_frames: int = 8) -> Tuple[str, Dict[str, Any]]:
    meta: Dict[str, Any] = {"type": "video", "evidence": "partial", "frames": []}
    lines = [f"### Video: {os.path.basename(path)}"]
    if shutil.which("ffprobe"):
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                              "default=nw=1:nk=1", path], capture_output=True, text=True)
        try:
            meta["duration_min"] = round(float(out.stdout.strip()) / 60, 2)
            lines.append(f"Duration ≈ {meta['duration_min']} min")
        except ValueError:
            pass
    if shutil.which("ffmpeg") and meta.get("duration_min"):
        os.makedirs(frames_dir, exist_ok=True)
        total = meta["duration_min"] * 60
        for i in range(n_frames):
            t = total * (i + 0.5) / n_frames
            frame = os.path.join(frames_dir, f"{os.path.splitext(os.path.basename(path))[0]}_{i + 1:02d}.jpg")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                            "-vf", "scale=640:-1", frame], capture_output=True)
            if os.path.exists(frame):
                meta["frames"].append({"file": frame, "at_s": round(t, 1)})
        lines.append("Key frames extracted (Claude: describe each one below, in order):")
        lines += [f"- [{f['at_s']}s] {f['file']} → DESCRIBE" for f in meta["frames"]]
    else:
        lines.append("ffmpeg not available: provide subtitles (.srt/.vtt) and a scene-by-scene description.")
    return "\n".join(lines), meta


def ingest(sources: List[str], kind: str, out_dir: str, title: Optional[str] = None,
           notes: Optional[str] = None) -> Dict[str, Any]:
    if not sources:
        raise CohorteError("Give at least one source (URL or file).")
    profile = get_subject(kind)
    os.makedirs(out_dir, exist_ok=True)
    sections: List[str] = []
    items: List[Dict[str, Any]] = []
    to_describe: List[str] = []
    for src in sources:
        if re.match(r"https?://", src):
            body, ctype = _fetch(src)
            if "html" in ctype or body.lstrip().startswith("<"):
                md, meta = _html_to_md(body, src)
            else:
                md, meta = f"### Document: {src}\n\n{body[:MAX_TEXT]}", {"type": "text", "evidence": "full"}
            meta["source"] = src
        elif not os.path.exists(src):
            raise CohorteError(f"Source not found: {src}")
        else:
            ext = os.path.splitext(src)[1].lower()
            name = os.path.basename(src)
            if ext in (".html", ".htm"):
                md, meta = _html_to_md(open(src, encoding="utf-8", errors="replace").read(), name)
            elif ext in (".srt", ".vtt"):
                md, meta = _subtitles(src)
            elif ext == ".docx":
                text = _docx(src)
                md, meta = f"### Document: {name}\n\n{text[:MAX_TEXT]}", {"type": "docx", "evidence": "full"}
            elif ext == ".pdf":
                text = _pdf(src)
                md, meta = f"### Document: {name}\n\n{text[:MAX_TEXT]}", {"type": "pdf", "evidence": "full"}
            elif ext in IMAGE_EXT:
                md = f"### Image: {name}\n{os.path.abspath(src)} → DESCRIBE (what a viewer sees, text on it, mood)"
                meta = {"type": "image", "evidence": "partial"}
                to_describe.append(os.path.abspath(src))
            elif ext in VIDEO_EXT:
                md, meta = _video(src, os.path.join(out_dir, "frames"))
                to_describe += [f["file"] for f in meta.get("frames", [])]
            elif ext == ".csv":
                with open(src, encoding="utf-8", errors="replace") as f:
                    head = "".join(f.readline() for _ in range(60))
                md, meta = f"### Data: {name} (first rows)\n\n```\n{head}```", {"type": "csv", "evidence": "partial"}
            else:
                text = open(src, encoding="utf-8", errors="replace").read()
                md = f"### Document: {name}\n\n{text[:MAX_TEXT]}"
                meta = {"type": "text", "evidence": "full" if len(text.split()) > 150 else "description"}
            meta["source"] = os.path.abspath(src)
        sections.append(md)
        items.append(meta)
    evidence = max((m.get("evidence", "description") for m in items), key=lambda e: EVIDENCE_RANK[e])
    subject = {
        "kind": profile["kind"], "noun": profile["noun"], "title": title or items[0].get("title") or "",
        "evidence": evidence, "sources": items, "to_describe": to_describe, "outcomes": profile["outcomes"],
    }
    header = [f"# Subject under test: {subject['title'] or profile['noun']}", f"Kind: {profile['kind']}"]
    if notes:
        header.append(f"Context: {notes}")
    seed = "\n\n".join(["\n".join(header)] + sections)
    with open(os.path.join(out_dir, "seed.md"), "w", encoding="utf-8") as f:
        f.write(seed + "\n")
    write_json(os.path.join(out_dir, "subject.json"), subject)
    return subject


def subject_summary(subject: Dict[str, Any]) -> str:
    return json.dumps({k: subject[k] for k in ("kind", "title", "evidence")}, ensure_ascii=False)
