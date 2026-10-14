"""Small shared helpers: JSON IO, tolerant JSON parsing, seeded RNGs, text utils."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import tempfile
from typing import Any, Iterable, Iterator, List, Optional


class CohorteError(Exception):
    """User-facing error: printed without a traceback by the CLI."""


# --------------------------------------------------------------------------- #
# File IO
# --------------------------------------------------------------------------- #

def read_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        if default is not None:
            return default
        raise CohorteError(f"File not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            raise CohorteError(f"Invalid JSON in {path}: {e}") from e


def write_json(path: str, data: Any) -> None:
    """Atomic write so an interrupted run never leaves a half-written state file."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def append_jsonl(path: str, records: Iterable[dict]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def read_jsonl(path: str) -> Iterator[dict]:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# --------------------------------------------------------------------------- #
# Tolerant JSON parsing (LLM output)
# --------------------------------------------------------------------------- #

def _close_truncated(content: str) -> str:
    """Close a JSON object cut off by max_tokens (same idea as MiroFish's fixer)."""
    content = content.strip()
    in_string = False
    escaped = False
    stack: List[str] = []
    for ch in content:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    if in_string:
        content += '"'
    content = re.sub(r",\s*$", "", content)
    return content + "".join(reversed(stack))


def parse_json_response(text: str) -> Any:
    """Parse JSON from an LLM reply: strips fences/prose, repairs truncation and stray newlines."""
    if text is None:
        raise ValueError("empty response")
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    start_candidates = [i for i in (s.find("{"), s.find("[")) if i >= 0]
    if not start_candidates:
        raise ValueError("no JSON object found")
    s = s[min(start_candidates):]
    attempts = [s]
    end = max(s.rfind("}"), s.rfind("]"))
    if end > 0:
        attempts.append(s[: end + 1])
    attempts.append(_close_truncated(s))
    for candidate in attempts:
        for variant in (candidate, re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", candidate)):
            try:
                return json.loads(variant, strict=False)
            except json.JSONDecodeError:
                continue
    raise ValueError("could not parse JSON from response")


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #

def stable_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:24]


def rng_for(*parts: Any) -> random.Random:
    """Deterministic RNG per (seed, platform, round, ...) so runs are reproducible and resumable."""
    key = "|".join(str(p) for p in parts)
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:16], 16))


def slugify(text: str, max_len: int = 40) -> str:
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    text = re.sub(r"[\s_-]+", "_", text)
    text = text.encode("ascii", "ignore").decode() or "user"
    return text[:max_len].strip("_") or "user"


_WORD_RE = re.compile(r"[\w']+", re.UNICODE)
_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "have", "your", "you", "are",
    "was", "were", "will", "they", "their", "about", "what", "when", "just", "into",
    "than", "then", "them", "been", "being", "also", "more", "most", "very", "much",
    "dans", "pour", "avec", "sont", "mais", "plus", "nous", "vous", "leur", "cette",
}


def keywords(text: str, min_len: int = 4) -> set:
    return {
        w for w in (m.group(0).lower() for m in _WORD_RE.finditer(text or ""))
        if len(w) >= min_len and w not in _STOPWORDS
    }


def clamp(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:  # NaN
        return default
    return max(lo, min(hi, v))


def to_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        if isinstance(value, str):
            m = re.search(r"-?\d+", value)
            if m:
                return int(m.group(0))
        return default


def coerce_str(value: Any) -> str:
    """Flatten whatever an LLM returned into a plain string (MiroFish _coerce_to_str)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "value", "description", "content", "summary", "name"):
            if key in value:
                out = coerce_str(value[key])
                if out:
                    return out
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return ", ".join(s for s in (coerce_str(v) for v in value) if s)
    return str(value)


def coerce_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for v in value:
            out.extend(coerce_str_list(v) if isinstance(v, (list, tuple)) else [coerce_str(v)])
        return [s for s in out if s]
    s = coerce_str(value)
    return [p.strip() for p in s.split(",") if p.strip()] if s else []


def truncate(text: str, n: int) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def fmt_clock(day: int, hour: int, minute: int = 0) -> str:
    return f"Day {day} {hour:02d}:{minute:02d}"
