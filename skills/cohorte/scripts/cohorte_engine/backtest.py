"""Backtest: does the cohort anticipate real reactions better than a single prompt?

A case is something whose outcome is known: one or more variants of a subject (two prices, two landing pages,
a film and its competitor...) shown to the same audience, and what really happened (which variant won, the real
metric of each variant, the objections that dominated). For each case Cohorte builds one panel from the case's
neutral context (never from a variant, so personas can't favour one), rates every variant with `assess`, and a
baseline rates the same variants with one expert prompt on the same model. Both are scored against reality:

- winner accuracy: did it pick the variant that really did better? (paired exact McNemar test vs the baseline)
- rank correlation: Spearman between predicted potential and the real metric, with a bootstrap interval on the gap
- objection recall: share of the real dominant objections that the predicted dislikes mention

The scorecard (scorecard.md / scorecard.json) is the V1 proof gate of the roadmap. It says "not proven" whenever the
sample is too small or the difference is not significant, and it keeps synthetic sanity cases apart from real ones.
"""

from __future__ import annotations

import glob
import json
import math
import os
import random
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import assess as assess_mod
from . import calibration
from . import prompts as P
from .environment import run_setup
from .llm import LLMClient, LLMRequest, resolve_model
from .subjects import SATISFACTION, get_subject, potential, satisfaction
from .util import CohorteError, coerce_str_list, parse_json_response, read_json, slugify, write_json
from .world import Sim, init_sim

GATE_MIN_CASES = 20
OBJECTIONS_PER_VARIANT = 8  # same budget for both methods, so recall compares like with like
DEFAULT_AUDIENCE = [
    {"name": "Target audience", "type": "Person", "count": 300,
     "summary": "The people this {noun} is meant for: interested, but busy and comparing alternatives."},
    {"name": "General public", "type": "Person", "count": 300,
     "summary": "Ordinary people who come across this {noun} without looking for it."},
    {"name": "Skeptics", "type": "Person", "count": 100,
     "summary": "People who distrust novelty, marketing and anything that wastes their time or money."},
    {"name": "Critic", "type": "Person", "count": 1,
     "summary": "An experienced reviewer who judges this kind of {noun} for a living."},
]

BASELINE_SYSTEM = """You are an experienced market and audience analyst. Predict how the audience described below
will react to a {noun}. Be realistic: most things get a lukewarm reception, and do not inflate scores.
{language_rule}
Return one valid JSON object only."""

BASELINE_USER = """## Audience
{audience}

## The {noun}
{seed}

## How people encounter it
{experience}

## {predict_who}
{questions}

Return JSON:
{{"scores": {{{score_keys}}},
  "objections": ["the main reasons people will reject or criticise it (short)"]}}"""

JUDGE_SYSTEM = """You check whether predicted objections cover the concerns that really dominated. A concern counts as
covered when at least one prediction expresses the same concern, even in other or more specific words ("89 € a
month is far above what it should cost" covers "price"; "asks for contacts, mic and photos first" covers
"permissions"). A merely related topic does not count. Judge each concern independently.
Return one valid JSON object only."""

JUDGE_USER = """## Concerns that really dominated
{concerns}

## Predicted objections
{predicted}

Return JSON: {{"found": [one true/false per numbered concern, in order]}}"""

LEAK_SYSTEM = """You answer only from knowledge you actually have. Never guess. Return one valid JSON object only."""

LEAK_USER = """Here is a {noun}:
{label}
{excerpt}

Do you have specific knowledge of how the public received it after release (audience or user ratings, review
scores, box office or sales)? If you are not sure, answer false.
Return JSON: {{"knows": true/false, "what_you_know": "one line, empty if false"}}"""

STOP = set("""the a an and or of to in on for with without is are be it its this that too not no very more less
than from as at by into their they them you your our we can will would should could just only also any some
about over under there here what which who when how why all much many""".split())


# --------------------------------------------------------------------------- #
# Cases
# --------------------------------------------------------------------------- #

def _read_seed(case_dir: str, item: Dict[str, Any]) -> str:
    if item.get("seed_text"):
        return str(item["seed_text"]).strip()
    if item.get("seed"):
        path = os.path.join(case_dir, item["seed"])
        if not os.path.exists(path):
            raise CohorteError(f"Seed file not found: {path}")
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def normalize_case(raw: Dict[str, Any], case_dir: str) -> Dict[str, Any]:
    if not isinstance(raw, dict) or not raw.get("id"):
        raise CohorteError("Each case needs an 'id'.")
    cid = str(raw["id"])
    profile = get_subject(raw.get("kind") or "generic")
    variants = raw.get("variants") or [{"id": "main", **{k: raw[k] for k in ("seed", "seed_text") if k in raw}}]
    out_vars = []
    for i, v in enumerate(variants):
        vid = str(v.get("id") or chr(ord("A") + i))
        text = _read_seed(case_dir, v)
        if not text:
            raise CohorteError(f"Case '{cid}', variant '{vid}': needs 'seed_text' or a 'seed' file.")
        out_vars.append({"id": vid, "seed_text": text, "label": v.get("label", vid)})
    outcome = raw.get("outcome") or {}
    scale = outcome.get("scale") or [0, 100]
    if len(scale) != 2 or float(scale[1]) <= float(scale[0]):
        raise CohorteError(f"Case '{cid}': outcome.scale must be [min, max] with max > min.")
    values = {str(k): float(v) for k, v in (outcome.get("values") or {}).items()}
    ids = {v["id"] for v in out_vars}
    unknown = set(values) - ids
    if unknown:
        raise CohorteError(f"Case '{cid}': outcome.values has unknown variant(s) {sorted(unknown)}.")
    winner = outcome.get("winner")
    if winner is None and len(values) >= 2 and len(set(values.values())) > 1:
        winner = max(values, key=values.get) if outcome.get("higher_is_better", True) else min(values, key=values.get)
    if winner is not None and str(winner) not in ids:
        raise CohorteError(f"Case '{cid}': outcome.winner '{winner}' is not a variant id.")
    objections = []
    for o in outcome.get("objections") or []:
        alts = o if isinstance(o, list) else [o]
        alts = [str(a).strip() for a in alts if str(a).strip()]
        if alts:
            objections.append(alts)
    if winner is None and not values and not objections:
        raise CohorteError(f"Case '{cid}': outcome needs 'winner', 'values' or 'objections'.")
    target = outcome.get("target", "potential")
    if target not in ("potential", "satisfaction"):
        raise CohorteError(f"Case '{cid}': outcome.target must be 'potential' or 'satisfaction'.")
    if target == "satisfaction" and profile["kind"] not in SATISFACTION:
        raise CohorteError(f"Case '{cid}': kind '{profile['kind']}' has no satisfaction dimensions.")
    audience = raw.get("audience") or [dict(a, summary=a["summary"].format(noun=profile["noun"]))
                                       for a in DEFAULT_AUDIENCE]
    return {
        "id": cid, "slug": slugify(cid, 48).replace("_", "-"), "kind": profile["kind"], "noun": profile["noun"],
        "title": raw.get("title") or cid, "context": (raw.get("context") or raw.get("title") or cid).strip(),
        "question": raw.get("question") or "", "date": raw.get("date", ""), "source": raw.get("source", ""),
        "synthetic": bool(raw.get("synthetic", False)), "leak_safe": bool(raw.get("leak_safe", False)),
        "evidence": raw.get("evidence", "full"), "language": raw.get("language", "English"),
        "audience": audience, "variants": out_vars,
        "target": target, "split": raw.get("split", ""),
        "outcome": {"metric": outcome.get("metric", ""), "scale": [float(scale[0]), float(scale[1])],
                    "values": values, "winner": None if winner is None else str(winner),
                    "objections": objections, "higher_is_better": outcome.get("higher_is_better", True)},
    }


def load_cases(path: str) -> List[Dict[str, Any]]:
    """A directory of case files (*.json, or <case>/case.json), a single .json (object or list) or a .jsonl."""
    files: List[str] = []
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "*.json")) + glob.glob(os.path.join(path, "*", "case.json")))
    elif os.path.exists(path):
        files = [path]
    if not files:
        raise CohorteError(f"No cases found at {path} (expected *.json files or <case>/case.json).")
    cases, seen = [], set()
    for f in files:
        base = os.path.dirname(os.path.abspath(f))
        with open(f, encoding="utf-8") as fh:
            if f.endswith(".jsonl"):
                raws = [json.loads(line) for line in fh if line.strip()]
            else:
                data = json.load(fh)
                raws = data if isinstance(data, list) else [data]
        for raw in raws:
            c = normalize_case(raw, base)
            if c["id"] in seen:
                raise CohorteError(f"Duplicate case id '{c['id']}'.")
            seen.add(c["id"])
            cases.append(c)
    return cases


# --------------------------------------------------------------------------- #
# Predictions
# --------------------------------------------------------------------------- #

def _world(case: Dict[str, Any]) -> Dict[str, Any]:
    question = case["question"] or f"How will the audience react to this {case['noun']}: {case['title']}?"
    entities = [{"id": f"e{i + 1}", "name": a["name"], "type": a.get("type", "Person"),
                 "count": int(a.get("count", 1)), "summary": a.get("summary", ""),
                 **({"kind": a["kind"]} if a.get("kind") else {})} for i, a in enumerate(case["audience"])]
    return {"simulation_requirement": question, "title": case["title"], "entities": entities,
            "settings": {"language": case["language"], "delegates": 2, "archetypes_per_crowd": 4,
                         "subject_kind": case["kind"], "platforms": ["twitter"], "initial_posts": 0}}


def _audience_text(case: Dict[str, Any]) -> str:
    total = sum(int(a.get("count", 1)) for a in case["audience"]) or 1
    return "\n".join(f"- {a['name']} ({int(a.get('count', 1)) / total:.0%}): {a.get('summary', '')}"
                     for a in case["audience"])


def _baseline_request(case: Dict[str, Any], seed: str, run: int, model: str) -> LLMRequest:
    subj = get_subject(case["kind"])
    dims = subj["dimensions"]
    return LLMRequest(
        id=f"baseline:{run}", kind="baseline", salt=f"{model}:{run}",
        system=[BASELINE_SYSTEM.format(noun=subj["noun"], language_rule=P.language_rule(case["language"]))],
        user=BASELINE_USER.format(
            audience=_audience_text(case), noun=subj["noun"], seed=seed[:assess_mod.SEED_CHARS],
            predict_who=("Predict the average answers of the people who actually choose it (those who go to see it, "
                         "buy it or install it), not of everyone (integers)" if case["target"] == "satisfaction"
                         else "Predict the audience's average answer to each question (integers)"),
            experience=subj["experience"], questions=assess_mod._questions(dims),
            score_keys=", ".join(f'"{d["key"]}": <{d["min"]}-{d["max"]}>' for d in dims)),
        effort="low", max_tokens=2000,
        meta={"dims": [d["key"] for d in dims], "lo": [d["min"] for d in dims], "hi": [d["max"] for d in dims],
              "name": "baseline"},
    )


def _baseline(case: Dict[str, Any], sim: Sim, settings: Dict[str, Any], runs: int) -> Dict[str, Dict[str, Any]]:
    dims = get_subject(case["kind"])["dimensions"]
    client = LLMClient(sim.dir, {**settings, "verbose": False})
    reqs = {}
    for v in case["variants"]:
        for run in range(runs):
            q = _baseline_request(case, v["seed_text"], run, client.model)
            q.id = f"baseline:{v['id']}:{run}"
            reqs[q.id] = (v["id"], q)
    answers = client.run_batch([q for _, q in reqs.values()], label=f"{case['id']}: baseline")
    out: Dict[str, Dict[str, Any]] = {}
    for qid, (vid, _) in reqs.items():
        text = answers.get(qid)
        try:
            data = parse_json_response(text) if text else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            continue
        parsed = assess_mod._parse(json.dumps({"scores": data.get("scores") or {}}), dims)
        if not parsed:
            continue
        slot = out.setdefault(vid, {"potentials": [], "objections": [], "sats": []})
        slot["potentials"].append(potential(parsed["scores"], dims))
        sat = satisfaction([{"weight": 1.0, "scores": parsed["scores"]}], dims, case["kind"])
        if sat is not None:
            slot["sats"].append(sat)
        slot.setdefault("by_run", []).append(coerce_str_list(data.get("objections"))[:6])
    for slot in out.values():
        slot["potential"] = round(sum(slot["potentials"]) / len(slot["potentials"]), 2)
        sats = slot.pop("sats")
        slot["satisfaction"] = round(sum(sats) / len(sats), 2) if sats else None
        runs_obj = slot.pop("by_run", [])
        slot["objections"] = [r[i] for i in range(6) for r in runs_obj if i < len(r)]  # rank 1 of each run first
    return out


def _cohorte(case: Dict[str, Any], sim: Sim, settings: Dict[str, Any], runs: int,
             models: Optional[List[str]]) -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(sim.path("config.json")):
        drafts = os.path.join(sim.home, "drafts", case["slug"])
        os.makedirs(drafts, exist_ok=True)
        write_json(os.path.join(drafts, "world.json"), _world(case))
        with open(os.path.join(drafts, "context.md"), "w", encoding="utf-8") as f:
            f.write(case["context"])
        write_json(os.path.join(drafts, "subject.json"),
                   {"kind": case["kind"], "title": case["title"], "evidence": case["evidence"], "sources": []})
        if not sim.exists:  # keep the LLM cache of a setup that was interrupted
            init_sim(sim, os.path.join(drafts, "world.json"), [os.path.join(drafts, "context.md")], settings,
                     subject_path=os.path.join(drafts, "subject.json"))
        run_setup(sim, LLMClient(sim.dir, settings), panel_only=True)
    out = {}
    for v in case["variants"]:
        with open(sim.path("seed.md"), "w", encoding="utf-8") as f:
            f.write(v["seed_text"])
        res = assess_mod.run_assessment(sim, settings, runs, models)
        shutil.copyfile(sim.path("assessment.json"), sim.path(f"assessment-{slugify(v['id'], 24)}.json"))
        out[v["id"]] = {"potential": res["potential"]["mean"], "sd": res["potential"]["sd_between_runs"],
                        "satisfaction": (res.get("satisfaction") or {}).get("mean"),
                        "objections": [d["text"] for d in res["dislikes"]],
                        "confidence": res["confidence"]["index"]}
    return out


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

def _ranks(xs: List[float]) -> List[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) < 3:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    return round(sxy / math.sqrt(sxx * syy), 3) if sxx and syy else None


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact p-value on discordant pairs (A right & B wrong vs the reverse)."""
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def _keywords(text: str) -> List[str]:
    return [w[:5] for w in re.findall(r"[a-zà-ÿ0-9€$]+", text.lower()) if len(w) >= 3 and w not in STOP]


def objection_hit(alternatives: List[str], predicted: List[str]) -> bool:
    """A real objection counts as found if every keyword of one of its phrasings appears in one prediction."""
    preds = [set(_keywords(p)) for p in predicted]
    for alt in alternatives:
        kws = set(_keywords(alt))
        if kws and any(kws <= p for p in preds):
            return True
    return False


def _top(objections: List[str]) -> List[str]:
    seen, out = set(), []
    for o in objections:
        k = " ".join(_keywords(o))
        if k and k not in seen:
            seen.add(k)
            out.append(o)
    return out[:OBJECTIONS_PER_VARIANT]


def _judge_requests(cases: List[Dict[str, Any]], preds: Dict[str, Any], methods: Tuple[str, ...], votes: int = 1):
    reqs = {}
    for c in cases:
        objs = c["outcome"]["objections"]
        if not objs or c["id"] not in preds:
            continue
        ids = [v["id"] for v in c["variants"]]
        for m in methods:
            p = preds[c["id"]].get(m) or {}
            predicted = sum((_top(p[i]["objections"]) for i in ids if i in p), [])
            for vote in range(votes):
                q = LLMRequest(
                    id=f"judge:{c['id']}:{m}:{vote}", kind="judge", system=[JUDGE_SYSTEM], effort="low", max_tokens=800,
                    salt=f"vote{vote}" if vote else "",
                    user=JUDGE_USER.format(
                        concerns="\n".join(f"{n + 1}. {o[0]}" + (f" (or: {' / '.join(o[1:])})" if o[1:] else "")
                                           for n, o in enumerate(objs)),
                        predicted="\n".join(f"- {x}" for x in predicted) or "(none)"),
                    meta={"n": len(objs)})
                reqs[q.id] = (c["id"], m, len(objs), q)
    return reqs


def judge(cases: List[Dict[str, Any]], preds: Dict[str, Any], out_dir: str, settings: Dict[str, Any],
          methods: Tuple[str, ...] = ("cohorte", "baseline"), votes: int = 1) -> Dict[Tuple[str, str], List[bool]]:
    """Blind LLM judge: same prompt for both methods, never told which method wrote a list. With votes > 1, each
    list is judged independently several times and a concern counts as found on a strict majority."""
    reqs = _judge_requests(cases, preds, methods, votes)
    if not reqs:
        return {}
    os.makedirs(os.path.join(out_dir, "judge"), exist_ok=True)
    client = LLMClient(os.path.join(out_dir, "judge"), {**settings, "verbose": False})
    answers = client.run_batch([r[3] for r in reqs.values()], label="objection judge")
    ballots: Dict[Tuple[str, str], List[List[bool]]] = {}
    for qid, (cid, m, n, _) in reqs.items():
        try:
            data = parse_json_response(answers.get(qid) or "")
            found = data.get("found") if isinstance(data, dict) else None
        except ValueError:
            found = None
        if isinstance(found, list) and len(found) == n:
            ballots.setdefault((cid, m), []).append([x is True or str(x).lower() == "true" for x in found])
    return {k: [sum(col) * 2 > len(b) for col in zip(*b)] for k, b in ballots.items()}


def leak_check(cases: List[Dict[str, Any]], out_dir: str, settings: Dict[str, Any]) -> Dict[str, List[str]]:
    """Ask the model whether it already knows how each real variant was received. Cases it recognises can't prove
    prediction (the model may be remembering, not predicting) and are left out of the leak-safe subset."""
    reqs = {}
    for c in cases:
        if c["synthetic"]:
            continue
        noun = get_subject(c["kind"])["noun"]
        for v in c["variants"]:
            q = LLMRequest(id=f"leak:{c['id']}:{v['id']}", kind="leak_check", system=[LEAK_SYSTEM], effort="low",
                           max_tokens=400, user=LEAK_USER.format(noun=noun, label=v["label"],
                                                                excerpt=v["seed_text"][:400]),
                           meta={"name": v["label"]})
            reqs[q.id] = (c["id"], v["id"], q)
    if not reqs:
        return {}
    os.makedirs(os.path.join(out_dir, "judge"), exist_ok=True)
    client = LLMClient(os.path.join(out_dir, "judge"), {**settings, "verbose": False})
    answers = client.run_batch([r[2] for r in reqs.values()], label="leak check")
    out: Dict[str, List[str]] = {}
    for qid, (cid, vid, _) in reqs.items():
        try:
            data = parse_json_response(answers.get(qid) or "")
        except ValueError:
            continue
        if isinstance(data, dict) and (data.get("knows") is True or str(data.get("knows")).lower() == "true"):
            out.setdefault(cid, []).append(vid)
    return out


def _value(p: Dict[str, Any], target: str) -> float:
    v = p.get("satisfaction") if target == "satisfaction" else None
    return p["potential"] if v is None else v


def _winner(pred: Dict[str, Dict[str, Any]], ids: List[str], target: str) -> Optional[str]:
    scores = {i: _value(pred[i], target) for i in ids if i in pred}
    if len(scores) < 2:
        return None
    best = max(scores.values())
    top = [i for i, s in scores.items() if s == best]
    return top[0] if len(top) == 1 else None  # a tie picks nobody


def score(cases: List[Dict[str, Any]], preds: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]],
          methods: Tuple[str, ...] = ("cohorte", "baseline"),
          judged: Optional[Dict[Tuple[str, str], List[bool]]] = None,
          leaks: Optional[Dict[str, List[str]]] = None) -> Dict[str, Any]:
    rows = []
    for c in cases:
        if c["id"] not in preds:
            continue
        ids = [v["id"] for v in c["variants"]]
        recognised = (leaks or {}).get(c["id"], [])
        row = {"id": c["id"], "kind": c["kind"], "synthetic": c["synthetic"], "recognised": recognised,
               "split": c["split"], "leak_safe": c["leak_safe"] and not recognised and c["split"] != "dev",
               "winner": c["outcome"]["winner"],
               "n_variants": len(ids)}
        for m in methods:
            p = preds[c["id"]].get(m) or {}
            pw = _winner(p, ids, c["target"])
            row[m] = {
                "potentials": {i: _value(p[i], c["target"]) for i in ids if i in p},
                "picked": pw,
                "correct": None if c["outcome"]["winner"] is None else pw == c["outcome"]["winner"],
                "objections_found_kw": [
                    objection_hit(o, sum((_top(p[i]["objections"]) for i in ids if i in p), []))
                    for o in c["outcome"]["objections"]],
            }
            row[m]["objections_found"] = (judged or {}).get((c["id"], m)) or row[m]["objections_found_kw"]
        rows.append(row)

    def summary(subset: List[Dict[str, Any]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"cases": len(subset)}
        wins = [r for r in subset if r["winner"] is not None]
        out["winner_cases"] = len(wins)
        out["chance_accuracy"] = round(sum(1 / r["n_variants"] for r in wins) / len(wins), 3) if wins else None
        for m in methods:
            right = sum(1 for r in wins if r[m]["correct"])
            found = [x for r in subset for x in r[m]["objections_found"]]
            xs, ys = [], []
            for r in subset:
                case = next(c for c in cases if c["id"] == r["id"])
                lo, hi = case["outcome"]["scale"]
                for vid, val in case["outcome"]["values"].items():
                    if vid in r[m]["potentials"]:
                        xs.append(r[m]["potentials"][vid])
                        norm = (val - lo) / (hi - lo) * 100
                        ys.append(norm if case["outcome"]["higher_is_better"] else 100 - norm)
            found_kw = [x for r in subset for x in r[m]["objections_found_kw"]]
            with_obj = [r for r in subset if r[m]["objections_found"]]
            out[m] = {"winner_accuracy": round(right / len(wins), 3) if wins else None, "winner_right": right,
                      "objection_recall": round(sum(found) / len(found), 3) if found else None,
                      "objection_recall_keywords": round(sum(found_kw) / len(found_kw), 3) if found_kw else None,
                      "top_objection_found": (round(sum(1 for r in with_obj if r[m]["objections_found"][0])
                                                    / len(with_obj), 3) if with_obj else None),
                      "objections_total": len(found), "spearman": spearman(xs, ys), "points": len(xs)}
        if len(methods) == 2:
            a, b = methods
            only_a = sum(1 for r in wins if r[a]["correct"] and not r[b]["correct"])
            only_b = sum(1 for r in wins if r[b]["correct"] and not r[a]["correct"])
            out["paired"] = {f"only_{a}": only_a, f"only_{b}": only_b, "p_value": mcnemar_exact(only_a, only_b)}
            out["spearman_gap"] = _bootstrap_gap(subset, cases, a, b)
            more_a = sum(1 for r in subset if sum(r[a]["objections_found"]) > sum(r[b]["objections_found"]))
            more_b = sum(1 for r in subset if sum(r[b]["objections_found"]) > sum(r[a]["objections_found"]))
            out["objection_sign"] = {f"{a}_more": more_a, f"{b}_more": more_b,
                                     "p_value": mcnemar_exact(more_a, more_b)}
        return out

    real = [r for r in rows if not r["synthetic"]]
    result = {"rows": rows, "all": summary(rows), "real": summary(real) if real else None,
              "synthetic": summary([r for r in rows if r["synthetic"]]) if any(r["synthetic"] for r in rows) else None,
              "leak_safe": summary([r for r in real if r["leak_safe"]]) if any(r["leak_safe"] for r in real) else None}
    for split in ("eval", "dev"):
        subset = [r for r in real if r["split"] == split]
        result[split] = summary(subset) if subset else None
    result["verdict"] = verdict(result, methods)
    return result


def _bootstrap_gap(subset, cases, a, b, n_boot: int = 2000) -> Optional[Dict[str, float]]:
    pts = []
    for r in subset:
        case = next(c for c in cases if c["id"] == r["id"])
        lo, hi = case["outcome"]["scale"]
        for vid, val in case["outcome"]["values"].items():
            if vid in r[a]["potentials"] and vid in r[b]["potentials"]:
                y = (val - lo) / (hi - lo) * 100
                pts.append((r[a]["potentials"][vid], r[b]["potentials"][vid],
                            y if case["outcome"]["higher_is_better"] else 100 - y))
    if len(pts) < 5:
        return None
    rng = random.Random(0)
    gaps = []
    for _ in range(n_boot):
        s = [pts[rng.randrange(len(pts))] for _ in pts]
        ra, rb = spearman([p[0] for p in s], [p[2] for p in s]), spearman([p[1] for p in s], [p[2] for p in s])
        if ra is not None and rb is not None:
            gaps.append(ra - rb)
    if not gaps:
        return None
    gaps.sort()
    return {"mean": round(sum(gaps) / len(gaps), 3), "low90": round(gaps[int(0.05 * len(gaps))], 3),
            "high90": round(gaps[int(0.95 * len(gaps)) - 1], 3)}


def verdict(result: Dict[str, Any], methods: Tuple[str, ...]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    syn = result.get("synthetic")
    if syn and syn["winner_cases"]:
        acc = syn[methods[0]]["winner_accuracy"]
        out["sanity"] = {"pass": acc is not None and acc >= 0.9, "accuracy": acc,
                         "text": f"planted differences detected in {syn[methods[0]]['winner_right']}/"
                                 f"{syn['winner_cases']} synthetic cases (gate: ≥90%)"}
    obj_pool = result.get("leak_safe") or {}
    sign = obj_pool.get("objection_sign")
    if obj_pool.get(methods[0], {}).get("objections_total"):
        a_, b_ = methods[0], methods[-1]
        ra, rb = obj_pool[a_]["objection_recall"], obj_pool[b_]["objection_recall"]
        p = sign["p_value"] if sign else 1.0
        status = ("better" if ra > rb and p < 0.05 else "worse" if rb > ra and p < 0.05 else "equivalent")
        out["objections"] = {"status": status, "text": (
            f"objection recall {ra:.0%} vs {rb:.0%} on {obj_pool['cases']} leak-safe cases; per case "
            f"{sign[a_ + '_more']} vs {sign[b_ + '_more']}, sign test p = {p}")}
    real = result.get("real")
    if real and not real["winner_cases"] and "objections" in out:
        return out  # an objection-only suite: no winner-picking claim to judge
    if not real:
        out["proof"] = {"status": "no real cases", "text": "No real historical cases yet: nothing is proven about "
                        "real-world prediction. Add cases with known outcomes (see benchmarks/README.md)."}
        return out
    # proof is judged on leak-safe cases only: the model may remember, not predict, the others
    pool = result.get("leak_safe") or {"winner_cases": 0}
    a, b = methods[0], methods[-1]
    n = pool["winner_cases"]
    if n < GATE_MIN_CASES:
        out["proof"] = {"status": "not proven", "text": f"{n} leak-safe real case(s) with a known winner "
                        f"(gate needs ≥{GATE_MIN_CASES}; {real['winner_cases']} real in total)"}
        return out
    acc_a, acc_b = pool[a]["winner_accuracy"], pool[b]["winner_accuracy"]
    p = pool["paired"]["p_value"]
    chance = pool["chance_accuracy"]
    if acc_a > acc_b and p < 0.05:
        status, text = "proven", f"{a} beats {b} on {n} leak-safe cases ({acc_a:.0%} vs {acc_b:.0%}, p = {p})"
    elif acc_a > acc_b:
        status, text = "not proven", (f"{a} is ahead on {n} leak-safe cases ({acc_a:.0%} vs {acc_b:.0%}) but not "
                                      f"significantly (p = {p})")
    else:
        status, text = "failed", (f"{a} does not beat {b} on {n} leak-safe cases ({acc_a:.0%} vs {acc_b:.0%}, "
                                  f"chance {chance:.0%}): narrow the claim")
    out["proof"] = {"status": status, "text": text}
    return out


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def _panel(case: Dict[str, Any]) -> Tuple[int, int]:
    """(assessment respondents, setup calls) for a case, mirroring setup's tiering with the backtest settings."""
    respondents, personas, sketches = 0, 0, 0
    for a in case["audience"]:
        cnt = int(a.get("count", 1))
        if cnt > 12:
            respondents += 2 + max(2, min(4, cnt // 20 or 2))
            personas += 2
            sketches += 2
        else:
            respondents += cnt
            personas += cnt
            sketches += 1 if cnt > 1 else 0
    return respondents, sketches + personas


def plan(cases: List[Dict[str, Any]], runs: int) -> Dict[str, Any]:
    setup = assess = baseline = 0
    for c in cases:
        resp, calls = _panel(c)
        setup += calls
        assess += resp * runs * len(c["variants"])
        baseline += runs * len(c["variants"])
    return {"cases": len(cases), "variants": sum(len(c["variants"]) for c in cases), "setup_calls": setup,
            "assess_calls": assess, "baseline_calls": baseline, "total_calls": setup + assess + baseline}


def _spent(home: str) -> float:
    total = 0.0
    for f in glob.glob(os.path.join(home, "sims", "*", "usage.json")) + [os.path.join(home, "..", "judge", "usage.json")]:
        total += float(read_json(f, default={}).get("cost_usd", 0.0))
    return total


def run_backtest(cases: List[Dict[str, Any]], out_dir: str, settings: Dict[str, Any], runs: int = 2,
                 models: Optional[List[str]] = None, budget: Optional[float] = None,
                 calibrate_home: Optional[str] = None, parallel: int = 4, judge_mode: str = "llm",
                 judge_votes: int = 1) -> Dict[str, Any]:
    home = os.path.join(out_dir, "home")
    os.makedirs(home, exist_ok=True)
    settings = {**settings, "model": resolve_model(settings.get("model"))}
    preds_path = os.path.join(out_dir, "predictions.json")
    preds: Dict[str, Any] = read_json(preds_path, default={})
    lock = threading.Lock()
    skipped: List[str] = []
    todo = [c for c in cases if c["id"] not in preds]

    def work(i_case: Tuple[int, Dict[str, Any]]) -> None:
        i, case = i_case
        if budget is not None and _spent(home) >= budget:
            with lock:
                skipped.append(case["id"])
            return
        print(f"[backtest] start {case['id']} ({i + 1}/{len(todo)}, {case['kind']}, "
              f"{len(case['variants'])} variant(s))", flush=True)
        sim = Sim(home, f"bt-{case['slug']}"[:64])
        pred = {"cohorte": _cohorte(case, sim, settings, runs, models),
                "baseline": _baseline(case, sim, settings, runs)}
        with lock:
            preds[case["id"]] = pred
            write_json(preds_path, preds)
        print(f"[backtest] done {case['id']}", flush=True)

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        list(pool.map(work, enumerate(todo)))  # list() re-raises the first error
    spent = _spent(home)
    stopped = (f"budget of ${budget:.2f} reached; {len(skipped)} case(s) not run" if skipped else None)
    judged = judge(cases, preds, out_dir, settings, votes=judge_votes) if judge_mode == "llm" else {}
    leaks = leak_check([c for c in cases if c["id"] in preds], out_dir, settings)
    result = score(cases, preds, judged=judged, leaks=leaks)
    result["judge"] = (("blind LLM judge" + (f", majority of {judge_votes} votes" if judge_votes > 1 else ""))
                       if judge_mode == "llm" else "keyword match")
    result.update({"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "runs": runs,
                   "model": settings["model"], "models": models or [settings["model"]],
                   "backend": settings.get("backend"), "cost_usd": round(spent, 4), "stopped": stopped})
    if settings.get("backend") == "mock":
        result["verdict"]["mock"] = "mock backend: random answers, these numbers mean nothing"
    if calibrate_home:
        result["calibrated_pairs"] = _record_calibration(calibrate_home, cases, preds)
    write_json(os.path.join(out_dir, "scorecard.json"), result)
    with open(os.path.join(out_dir, "scorecard.md"), "w", encoding="utf-8") as f:
        f.write(render(result, cases))
    return result


def _record_calibration(home: str, cases: List[Dict[str, Any]], preds: Dict[str, Any]) -> int:
    """Real cases with real values become calibration pairs for their kind (synthetic ones never do)."""
    n = 0
    for c in cases:
        if c["synthetic"] or c["id"] not in preds:
            continue
        lo, hi = c["outcome"]["scale"]
        for vid, val in c["outcome"]["values"].items():
            p = preds[c["id"]]["cohorte"].get(vid)
            if p:
                calibration.add(home, c["kind"], f"backtest:{c['id']}:{vid}", p["potential"], val, lo, hi,
                                c["outcome"]["metric"], "backtest")
                n += 1
    return n


def _pct(x: Optional[float]) -> str:
    return "–" if x is None else f"{x:.0%}"


def render(r: Dict[str, Any], cases: List[Dict[str, Any]]) -> str:
    v = r["verdict"]
    lines = [f"# Cohorte scorecard · {r['at']}",
             f"Model {', '.join(r['models'])} ({r['backend']}) · {r['runs']} run(s) per variant · "
             f"{r['all']['cases']} case(s) · objections scored by {r.get('judge', 'keyword match')} "
             f"(top {OBJECTIONS_PER_VARIANT} per variant for both methods) · cost ${r['cost_usd']:.2f}"]
    if v.get("mock"):
        lines.append(f"\n> **{v['mock']}**")
    if r.get("stopped"):
        lines.append(f"\n> Stopped early: {r['stopped']}. Re-run with a higher --budget to resume.")
    lines.append("\n## Verdict")
    if v.get("proof"):
        lines.append(f"- **Real-world proof: {v['proof']['status']}**: {v['proof']['text']}")
    if v.get("objections"):
        lines.append(f"- **Objection discovery vs one prompt: {v['objections']['status']}**: {v['objections']['text']}")
    if v.get("sanity"):
        lines.append(f"- **Sanity gate: {'pass' if v['sanity']['pass'] else 'fail'}**: {v['sanity']['text']}")
    for label, key in (("Evaluation split (unseen before the run)", "eval"), ("Dev split (seen: never proof)", "dev"),
                       ("All real cases", "real"), ("Leak-safe real cases (counted for proof)", "leak_safe"),
                       ("Synthetic sanity cases", "synthetic")):
        s = r.get(key)
        if not s:
            continue
        lines.append(f"\n## {label} ({s['cases']})\n| metric | Cohorte | single-prompt baseline |\n|---|---|---|")
        c, b = s["cohorte"], s["baseline"]
        if s["winner_cases"]:
            lines.append(f"| winner accuracy ({s['winner_cases']} cases, chance {_pct(s['chance_accuracy'])}) | "
                         f"{_pct(c['winner_accuracy'])} | {_pct(b['winner_accuracy'])} |")
        if c["objections_total"]:
            lines.append(f"| objection recall ({c['objections_total']} objections) | {_pct(c['objection_recall'])} | "
                         f"{_pct(b['objection_recall'])} |")
        if c["points"]:
            lines.append(f"| rank correlation with real metric ({c['points']} points) | "
                         f"{'–' if c['spearman'] is None else c['spearman']} | "
                         f"{'–' if b['spearman'] is None else b['spearman']} |")
        if c["objections_total"]:
            lines.append(f"| top complaint found | {_pct(c['top_objection_found'])} | {_pct(b['top_objection_found'])} |")
            lines.append(f"| objection recall, exact keywords | {_pct(c['objection_recall_keywords'])} | "
                         f"{_pct(b['objection_recall_keywords'])} |")
            sg = s.get("objection_sign")
            if sg:
                lines.append(f"\nPer case: Cohorte found more complaints in {sg['cohorte_more']} case(s), the "
                             f"baseline in {sg['baseline_more']} · sign test p = {sg['p_value']}")
        pr = s.get("paired")
        if pr and s["winner_cases"]:
            lines.append(f"\nPaired test on winners: Cohorte alone right {pr['only_cohorte']}×, baseline alone right "
                         f"{pr['only_baseline']}× · exact McNemar p = {pr['p_value']}")
        gap = s.get("spearman_gap")
        if gap:
            lines.append(f"Correlation gap (Cohorte − baseline): {gap['mean']:+} (90% bootstrap {gap['low90']:+} to "
                         f"{gap['high90']:+})")
    lines.append("\n## Cases\n| case | kind | type | real winner | Cohorte (potential per variant) | "
                 "baseline | objections found (C / B) |\n|---|---|---|---|---|---|---|")
    for row in r["rows"]:
        def cell(m: str) -> str:
            pots = " · ".join(f"{k} {val:.0f}" for k, val in row[m]["potentials"].items())
            mark = "" if row[m]["correct"] is None else (" ✓" if row[m]["correct"] else " ✗")
            return pots + mark
        found_c = sum(row["cohorte"]["objections_found"])
        found_b = sum(row["baseline"]["objections_found"])
        total = len(row["cohorte"]["objections_found"])
        kind_label = "synthetic" if row["synthetic"] else ("real, leak-safe" if row["leak_safe"] else
                                                            "real, known to model" if row["recognised"] else "real")
        lines.append(f"| {row['id']} | {row['kind']} | {kind_label} | "
                     f"{row['winner'] or '–'} | {cell('cohorte')} | {cell('baseline')} | "
                     f"{f'{found_c} / {found_b} of {total}' if total else '–'} |")
    lines.append("\n## How to read this\n"
                 "- Both methods see exactly the same material and use the same model; only the method differs.\n"
                 "- Synthetic cases have planted, obvious differences: passing them shows the engine can detect a "
                 "difference, not that it predicts reality.\n"
                 "- Only real cases with outcomes the model can't have memorised (leak-safe) prove prediction. "
                 f"The gate needs ≥{GATE_MIN_CASES} of them and p < 0.05.")
    return "\n".join(lines)
