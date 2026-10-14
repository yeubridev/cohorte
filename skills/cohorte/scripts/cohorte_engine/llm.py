"""LLM access for the engine.

Every call goes through `LLMClient.run_batch`, which:
  * looks the request up in a content-addressed cache (llm_cache.jsonl) so an
    interrupted run resumes without paying twice and replays are deterministic;
  * sends the missing requests concurrently to the selected backend;
  * records token usage and an estimated cost (usage.json).

Backends
  anthropic   Claude API via the official `anthropic` Python SDK (pip install anthropic).
  claude-cli  Headless Claude Code (`claude -p`), reuses the user's Claude Code login.
  manual      No network: writes pending_requests.json and stops; Claude (the
              assistant running the skill) answers them in responses.json, then
              the same command is re-run. Works anywhere, slow for big runs.
  mock        Offline deterministic fake responses, for tests and dry runs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .util import CohorteError, parse_json_response, read_json, read_jsonl, stable_hash, write_json

DEFAULT_MODEL = "claude-opus-5-5"

# USD per million tokens: (input, output, cache_read). Cache writes are billed at 1.25x input.
# Snapshot of public list prices (prompts <= 100K tokens); used only for estimates.
PRICING = {
    "claude-fable-5-1": (10.0, 50.0, 0.25),
    "claude-fable-5": (10.0, 50.0, 1.0),
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-opus-5": (5.0, 25.0, 0.50),
    "claude-opus-4-8": (5.0, 25.0, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    "claude-sonnet-5": (2.0, 10.0, 0.20),
    "claude-sonnet-4-6": (3.0, 15.0, 0.30),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
    "claude-haiku-4-5": (1.0, 5.0, 0.10),
}
MODEL_ALIASES = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
    "fable": "claude-fable-5-1",
}
# Models that accept server-side refusal fallbacks (`fallbacks: "default"`) on the Claude API.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-fable-5", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

BACKENDS = ("anthropic", "claude-cli", "manual", "mock")


def resolve_model(model: Optional[str]) -> str:
    model = (model or DEFAULT_MODEL).strip()
    return MODEL_ALIASES.get(model.lower(), model)


def price_for(model: str):
    model = resolve_model(model)
    if model in PRICING:
        return PRICING[model]
    for key, value in PRICING.items():
        if model.startswith(key):
            return value
    return PRICING[DEFAULT_MODEL]


@dataclass
class LLMRequest:
    id: str                      # stable, human-readable id (e.g. "turn:twitter:r12:a5")
    kind: str                    # persona | crowd | time_config | event_config | agent_config | turn | followup | interview
    system: List[str]            # system blocks; first = shared across agents, last = per-agent (cache friendly)
    user: str
    max_tokens: int = 4000
    effort: str = "low"
    expect_json: bool = True
    meta: Dict[str, Any] = field(default_factory=dict)  # hints for the mock backend only

    @property
    def key(self) -> str:
        return stable_hash(self.kind, *self.system, self.user)

    def system_text(self) -> str:
        return "\n\n".join(b for b in self.system if b)


class AwaitingResponses(Exception):
    """Raised by the manual backend when Claude has to answer pending requests."""

    def __init__(self, path: str, count: int):
        super().__init__(path)
        self.path = path
        self.count = count


class FatalLLMError(Exception):
    """Non-retryable backend failure (auth, unknown model, malformed request)."""


@dataclass
class CallResult:
    text: Optional[str]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost_usd: Optional[float] = None
    error: Optional[str] = None


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #

class AnthropicBackend:
    def __init__(self, model: str, use_fallbacks: bool = True, max_retries: int = 6):
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise CohorteError(
                "The 'anthropic' backend needs the official SDK: pip install anthropic "
                "(or use --backend claude-cli to reuse your Claude Code login)."
            ) from e
        import anthropic

        self.anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=max_retries, timeout=300.0)
        self.model = model
        self.use_fallbacks = use_fallbacks and model in FALLBACK_MODELS
        self.use_effort = True
        self._lock = threading.Lock()

    def _kwargs(self, req: LLMRequest) -> Dict[str, Any]:
        blocks = [{"type": "text", "text": b} for b in req.system if b]
        # Cache the shared rules block and the per-agent persona block: both repeat every round.
        for b in blocks[-2:]:
            b["cache_control"] = {"type": "ephemeral"}
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": req.max_tokens,
            "messages": [{"role": "user", "content": req.user}],
        }
        if blocks:
            kwargs["system"] = blocks
        if self.use_effort and req.effort:
            kwargs["output_config"] = {"effort": req.effort}
        return kwargs

    def complete(self, req: LLMRequest) -> CallResult:
        a = self.anthropic
        for _ in range(3):
            kwargs = self._kwargs(req)
            try:
                if self.use_fallbacks:
                    resp = self.client.beta.messages.create(
                        betas=[FALLBACK_BETA], fallbacks="default", **kwargs
                    )
                else:
                    resp = self.client.messages.create(**kwargs)
            except a.BadRequestError as e:
                msg = str(getattr(e, "message", e)).lower()
                # Degrade gracefully on models/proxies that reject optional parameters.
                with self._lock:
                    if self.use_fallbacks and ("fallback" in msg or "beta" in msg):
                        self.use_fallbacks = False
                        continue
                    if self.use_effort and ("effort" in msg or "output_config" in msg):
                        self.use_effort = False
                        continue
                raise FatalLLMError(f"Bad request: {getattr(e, 'message', e)}") from e
            except (a.AuthenticationError, a.PermissionDeniedError) as e:
                raise FatalLLMError(
                    "Claude API authentication failed. Set ANTHROPIC_API_KEY (or run `ant auth login`), "
                    "or use --backend claude-cli."
                ) from e
            except a.NotFoundError as e:
                raise FatalLLMError(f"Unknown model '{self.model}': {getattr(e, 'message', e)}") from e
            except a.RateLimitError as e:
                return CallResult(text=None, error=f"rate limited after retries: {e}")
            except a.APIStatusError as e:
                return CallResult(text=None, error=f"API error {e.status_code}: {getattr(e, 'message', e)}")
            except a.APIConnectionError as e:
                return CallResult(text=None, error=f"connection error: {e}")

            usage = resp.usage
            result = CallResult(
                text=None,
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            )
            if resp.stop_reason == "refusal":
                details = getattr(resp, "stop_details", None)
                result.error = f"refusal ({getattr(details, 'category', None)})"
                return result
            result.text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            return result
        raise FatalLLMError("Request rejected repeatedly; check model name and parameters.")


class ClaudeCliBackend:
    def __init__(self, model: str, timeout: int = 300):
        self.bin = shutil.which("claude")
        if not self.bin:
            raise CohorteError("The 'claude-cli' backend needs the `claude` command (Claude Code) on PATH.")
        self.model = model
        self.timeout = timeout
        # Run outside the user's project so its CLAUDE.md and settings don't leak into agent prompts.
        self.cwd = tempfile.mkdtemp(prefix="cohorte-cli-")

    def complete(self, req: LLMRequest) -> CallResult:
        cmd = [
            self.bin, "-p",
            "--output-format", "json",
            "--model", self.model,
            "--system-prompt", req.system_text() or "You are a helpful assistant.",
            "--tools", "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
        ]
        if req.effort:
            cmd += ["--effort", req.effort]
        try:
            proc = subprocess.run(
                cmd, input=req.user, capture_output=True, text=True,
                timeout=self.timeout, cwd=self.cwd,
            )
        except subprocess.TimeoutExpired:
            return CallResult(text=None, error="claude -p timed out")
        out = (proc.stdout or "").strip()
        try:
            data = json.loads(out.splitlines()[-1]) if out else {}
        except json.JSONDecodeError:
            data = {}
        if proc.returncode != 0 or not data or data.get("is_error"):
            err = data.get("result") or proc.stderr.strip() or out or f"exit code {proc.returncode}"
            low = str(err).lower()
            if "login" in low or "auth" in low or "api key" in low or "invalid model" in low:
                raise FatalLLMError(f"claude CLI failed: {err}")
            return CallResult(text=None, error=f"claude CLI error: {str(err)[:300]}")
        usage = data.get("usage") or {}
        return CallResult(
            text=data.get("result") or "",
            input_tokens=usage.get("input_tokens", 0) or 0,
            output_tokens=usage.get("output_tokens", 0) or 0,
            cache_read=usage.get("cache_read_input_tokens", 0) or 0,
            cache_write=usage.get("cache_creation_input_tokens", 0) or 0,
            cost_usd=data.get("total_cost_usd"),
        )


class MockBackend:
    def complete(self, req: LLMRequest) -> CallResult:
        from .mock import mock_response

        text = mock_response(req)
        return CallResult(text=text, input_tokens=len(req.user) // 4, output_tokens=len(text) // 4, cost_usd=0.0)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #

class LLMClient:
    def __init__(self, sim_dir: str, settings: Dict[str, Any]):
        self.sim_dir = sim_dir
        self.backend_name = settings.get("backend", "anthropic")
        if self.backend_name not in BACKENDS:
            raise CohorteError(f"Unknown backend '{self.backend_name}'. Choose one of: {', '.join(BACKENDS)}")
        self.model = resolve_model(settings.get("model"))
        self.concurrency = max(1, int(settings.get("concurrency", 8)))
        self.verbose = bool(settings.get("verbose", True))
        self.cache_path = os.path.join(sim_dir, "llm_cache.jsonl")
        self.usage_path = os.path.join(sim_dir, "usage.json")
        self.pending_path = os.path.join(sim_dir, "pending_requests.json")
        self.responses_path = os.path.join(sim_dir, "responses.json")
        self._lock = threading.Lock()
        self.cache: Dict[str, str] = {}
        for rec in read_jsonl(self.cache_path):
            if "key" in rec and isinstance(rec.get("text"), str):
                self.cache[rec["key"]] = rec["text"]
        self.usage = read_json(self.usage_path, default={
            "calls": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0, "by_kind": {},
        })
        self._backend = None
        self._settings = settings

    # -- backend ----------------------------------------------------------- #
    @property
    def backend(self):
        if self._backend is None:
            if self.backend_name == "anthropic":
                self._backend = AnthropicBackend(self.model, use_fallbacks=self._settings.get("fallbacks", True))
            elif self.backend_name == "claude-cli":
                self._backend = ClaudeCliBackend(self.model)
            elif self.backend_name == "mock":
                self._backend = MockBackend()
        return self._backend

    # -- bookkeeping ------------------------------------------------------- #
    def _store(self, req: LLMRequest, text: str) -> None:
        with self._lock:
            self.cache[req.key] = text
            with open(self.cache_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"key": req.key, "id": req.id, "kind": req.kind, "text": text}, ensure_ascii=False) + "\n")

    def _account(self, req: LLMRequest, res: CallResult) -> None:
        inp, out, cr = price_for(self.model)
        if res.cost_usd is not None:
            cost = float(res.cost_usd)
        else:
            cost = (
                res.input_tokens * inp + res.output_tokens * out
                + res.cache_read * cr + res.cache_write * inp * 1.25
            ) / 1_000_000
        with self._lock:
            u = self.usage
            u["calls"] += 1
            u["failed"] += 0 if res.text is not None else 1
            u["input_tokens"] += res.input_tokens
            u["output_tokens"] += res.output_tokens
            u["cache_read_tokens"] += res.cache_read
            u["cache_write_tokens"] += res.cache_write
            u["cost_usd"] = round(u["cost_usd"] + cost, 6)
            k = u["by_kind"].setdefault(req.kind, {"calls": 0, "cost_usd": 0.0})
            k["calls"] += 1
            k["cost_usd"] = round(k["cost_usd"] + cost, 6)
            u["model"] = self.model
            u["backend"] = self.backend_name

    def save_usage(self) -> None:
        with self._lock:
            write_json(self.usage_path, self.usage)

    # -- manual backend ------------------------------------------------------ #
    def _ingest_manual_responses(self, reqs: List[LLMRequest]) -> None:
        if not os.path.exists(self.responses_path):
            return
        responses = read_json(self.responses_path, default={})
        by_id = {r.id: r for r in reqs}
        consumed = []
        for rid, text in responses.items():
            req = by_id.get(rid)
            if req is None or not isinstance(text, str):
                continue
            if req.expect_json:
                try:
                    parse_json_response(text)
                except ValueError:
                    print(f"[cohorte] response for {rid} is not valid JSON; it stays pending.", file=sys.stderr)
                    continue
            self._store(req, text)
            consumed.append(rid)
        remaining = {k: v for k, v in responses.items() if k not in consumed}
        if remaining:
            write_json(self.responses_path, remaining)
        else:
            os.unlink(self.responses_path)

    def _write_pending(self, missing: List[LLMRequest], label: str) -> None:
        write_json(self.pending_path, {
            "label": label,
            "instructions": (
                "Answer each request in character, independently of the others (an agent only knows "
                "what its own prompt shows). Write a JSON object {\"<id>\": \"<raw response text>\"} to "
                f"{self.responses_path} and re-run the same cohorte command. Requests with expect_json=true "
                "must be answered with the JSON object their prompt asks for."
            ),
            "responses_file": self.responses_path,
            "requests": [
                {"id": r.id, "kind": r.kind, "expect_json": r.expect_json, "system": r.system_text(), "user": r.user}
                for r in missing
            ],
        })

    # -- main entry ------------------------------------------------------------ #
    def run_batch(self, reqs: List[LLMRequest], label: str = "") -> Dict[str, Optional[str]]:
        results: Dict[str, Optional[str]] = {}
        if not reqs:
            return results
        if self.backend_name == "manual":
            self._ingest_manual_responses(reqs)
        missing = []
        for r in reqs:
            if r.key in self.cache:
                results[r.id] = self.cache[r.key]
            else:
                missing.append(r)
        if not missing:
            if self.backend_name == "manual" and os.path.exists(self.pending_path):
                os.unlink(self.pending_path)
            return results
        if self.backend_name == "manual":
            self._write_pending(missing, label)
            raise AwaitingResponses(self.pending_path, len(missing))

        backend = self.backend
        failures: List[str] = []

        def call(req: LLMRequest) -> Optional[str]:
            res = backend.complete(req)
            if res.text is not None and req.expect_json:
                try:
                    parse_json_response(res.text)
                except ValueError:
                    self._account(req, res)
                    retry = LLMRequest(
                        id=req.id, kind=req.kind, system=req.system, max_tokens=req.max_tokens,
                        effort=req.effort, meta=req.meta,
                        user=req.user + "\n\nIMPORTANT: reply with one valid JSON object only, no prose.",
                    )
                    res = backend.complete(retry)
                    if res.text is not None:
                        try:
                            parse_json_response(res.text)
                        except ValueError:
                            res.error, res.text = "invalid JSON twice", None
            self._account(req, res)
            if res.text is None:
                failures.append(f"{req.id}: {res.error}")
                return None
            self._store(req, res.text)
            return res.text

        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futures = {pool.submit(call, r): r for r in missing}
            try:
                for fut in as_completed(futures):
                    req = futures[fut]
                    results[req.id] = fut.result()
            except FatalLLMError as e:
                for f in futures:
                    f.cancel()
                self.save_usage()
                raise CohorteError(str(e)) from e
        self.save_usage()
        if failures:
            for f in failures[:5]:
                print(f"[cohorte] LLM call failed: {f}", file=sys.stderr)
            if len(failures) > max(2, len(missing) // 2):
                raise CohorteError(
                    f"{len(failures)}/{len(missing)} LLM calls failed in '{label}'. Nothing was committed for this "
                    "step; fix the cause (rate limits? network?) and re-run - successful calls are cached."
                )
        return results
