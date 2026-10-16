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
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import assess as assess_mod
from . import calibration
from . import prompts as P
from .environment import run_setup
from .llm import LLMClient, LLMRequest, resolve_model
from .subjects import get_subject, potential
from .util import CohorteError, coerce_str_list, parse_json_response, read_json, slugify, write_json
from .world import Sim, init_sim

GATE_MIN_CASES = 20
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

## Predict the audience's average answer to each question (integers)
{questions}

Return JSON:
{{"scores": {{{score_keys}}},
  "objections": ["the main reasons people will reject or criticise it (short)"]}}"""

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
    audience = raw.get("audience") or [dict(a, summary=a["summary"].format(noun=profile["noun"]))
                                       for a in DEFAULT_AUDIENCE]
    return {
        "id": cid, "slug": slugify(cid, 48).replace("_", "-"), "kind": profile["kind"], "noun": profile["noun"],
        "title": raw.get("title") or cid, "context": (raw.get("context") or raw.get("title") or cid).strip(),
        "question": raw.get("question") or "", "date": raw.get("date", ""), "source": raw.get("source", ""),
        "synthetic": bool(raw.get("synthetic", False)), "leak_safe": bool(raw.get("leak_safe", False)),
        "evidence": raw.get("evidence", "full"), "language": raw.get("language", "English"),
        "audience": audience, "variants": out_vars,
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
        slot = out.setdefault(vid, {"potentials": [], "objections": []})
        slot["potentials"].append(potential(parsed["scores"], dims))
        slot["objections"].extend(coerce_str_list(data.get("objections"))[:6])
    for slot in out.values():
        slot["potential"] = round(sum(slot["potentials"]) / len(slot["potentials"]), 2)
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
        run_setup(sim, LLMClient(sim.dir, settings))
    out = {}
    for v in case["variants"]:
        with open(sim.path("seed.md"), "w", encoding="utf-8") as f:
            f.write(v["seed_text"])
        res = assess_mod.run_assessment(sim, settings, runs, models)
        shutil.copyfile(sim.path("assessment.json"), sim.path(f"assessment-{slugify(v['id'], 24)}.json"))
        out[v["id"]] = {"potential": res["potential"]["mean"], "sd": res["potential"]["sd_between_runs"],
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


def _winner(pred: Dict[str, Dict[str, Any]], ids: List[str], higher_is_better: bool) -> Optional[str]:
    scores = {i: pred[i]["potential"] for i in ids if i in pred}
    if len(scores) < 2:
        return None
    best = max(scores.values())
    top = [i for i, s in scores.items() if s == best]
    return top[0] if len(top) == 1 else None  # a tie picks nobody


def score(cases: List[Dict[str, Any]], preds: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]],
          methods: Tuple[str, ...] = ("cohorte", "baseline")) -> Dict[str, Any]:
    rows = []
    for c in cases:
        if c["id"] not in preds:
            continue
        ids = [v["id"] for v in c["variants"]]
        row = {"id": c["id"], "kind": c["kind"], "synthetic": c["synthetic"], "leak_safe": c["leak_safe"],
               "winner": c["outcome"]["winner"]}
        for m in methods:
            p = preds[c["id"]].get(m) or {}
            pw = _winner(p, ids, c["outcome"]["higher_is_better"])
            row[m] = {
                "potentials": {i: p[i]["potential"] for i in ids if i in p},
                "picked": pw,
                "correct": None if c["outcome"]["winner"] is None else pw == c["outcome"]["winner"],
                "objections_found": [objection_hit(o, sum((p[i]["objections"] for i in ids if i in p), []))
                                     for o in c["outcome"]["objections"]],
            }
        rows.append(row)

    def summary(subset: List[Dict[str, Any]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"cases": len(subset)}
        wins = [r for r in subset if r["winner"] is not None]
        out["winner_cases"] = len(wins)
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
            out[m] = {"winner_accuracy": round(right / len(wins), 3) if wins else None, "winner_right": right,
                      "objection_recall": round(sum(found) / len(found), 3) if found else None,
                      "objections_total": len(found), "spearman": spearman(xs, ys), "points": len(xs)}
        if len(methods) == 2:
            a, b = methods
            only_a = sum(1 for r in wins if r[a]["correct"] and not r[b]["correct"])
            only_b = sum(1 for r in wins if r[b]["correct"] and not r[a]["correct"])
            out["paired"] = {f"only_{a}": only_a, f"only_{b}": only_b, "p_value": mcnemar_exact(only_a, only_b)}
            out["spearman_gap"] = _bootstrap_gap(subset, cases, a, b)
        return out

    real = [r for r in rows if not r["synthetic"]]
    result = {"rows": rows, "all": summary(rows), "real": summary(real) if real else None,
              "synthetic": summary([r for r in rows if r["synthetic"]]) if any(r["synthetic"] for r in rows) else None,
              "leak_safe": summary([r for r in real if r["leak_safe"]]) if any(r["leak_safe"] for r in real) else None}
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
    real = result.get("real")
    if not real:
        out["proof"] = {"status": "no real cases", "text": "No real historical cases yet: nothing is proven about "
                        "real-world prediction. Add cases with known outcomes (see benchmarks/README.md)."}
        return out
    a, b = methods[0], methods[-1]
    n = real["winner_cases"]
    acc_a, acc_b = real[a]["winner_accuracy"], real[b]["winner_accuracy"]
    p = real.get("paired", {}).get("p_value", 1.0)
    if n < GATE_MIN_CASES:
        status, text = "not proven", f"only {n} real case(s) with a known winner (gate needs ≥{GATE_MIN_CASES})"
    elif acc_a is not None and acc_b is not None and acc_a > acc_b and p < 0.05:
        status, text = "proven", f"{a} beats {b} on {n} real cases ({acc_a:.0%} vs {acc_b:.0%}, p = {p})"
    elif acc_a is not None and acc_b is not None and acc_a > acc_b:
        status, text = "not proven", f"{a} is ahead ({acc_a:.0%} vs {acc_b:.0%}) but not significantly (p = {p})"
    else:
        status, text = "failed", f"{a} does not beat {b} on real cases ({acc_a} vs {acc_b}); narrow the claim"
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
    return respondents, sketches + personas + 2 + math.ceil(personas / 15)


def plan(cases: List[Dict[str, Any]], runs: int) -> Dict[str, Any]:
    setup = assess = baseline = 0
    for c in cases:
        resp, calls = _panel(c)
        setup += calls
        assess += resp * runs * len(c["variants"])
        baseline += runs * len(c["variants"])
    return {"cases": len(cases), "variants": sum(len(c["variants"]) for c in cases), "setup_calls": setup,
            "assess_calls": assess, "baseline_calls": baseline, "total_calls": setup + assess + baseline}


def _usage(sim: Sim) -> float:
    return float(read_json(sim.path("usage.json"), default={}).get("cost_usd", 0.0))


def run_backtest(cases: List[Dict[str, Any]], out_dir: str, settings: Dict[str, Any], runs: int = 2,
                 models: Optional[List[str]] = None, budget: Optional[float] = None,
                 calibrate_home: Optional[str] = None) -> Dict[str, Any]:
    home = os.path.join(out_dir, "home")
    os.makedirs(home, exist_ok=True)
    settings = {**settings, "model": resolve_model(settings.get("model"))}
    preds_path = os.path.join(out_dir, "predictions.json")
    preds: Dict[str, Any] = read_json(preds_path, default={})
    spent, stopped = 0.0, None
    for i, case in enumerate(cases):
        sim = Sim(home, f"bt-{case['slug']}"[:64])
        if case["id"] not in preds:
            if budget is not None and spent >= budget:
                stopped = f"budget of ${budget:.2f} reached after {i} case(s)"
                break
            print(f"[backtest] case {i + 1}/{len(cases)}: {case['id']} ({case['kind']}, "
                  f"{len(case['variants'])} variant(s))", flush=True)
            preds[case["id"]] = {"cohorte": _cohorte(case, sim, settings, runs, models),
                                 "baseline": _baseline(case, sim, settings, runs)}
            write_json(preds_path, preds)
        if sim.exists:
            spent += _usage(sim)
    result = score(cases, preds)
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
             f"{r['all']['cases']} case(s) · cost ${r['cost_usd']:.2f}"]
    if v.get("mock"):
        lines.append(f"\n> **{v['mock']}**")
    if r.get("stopped"):
        lines.append(f"\n> Stopped early: {r['stopped']}. Re-run with a higher --budget to resume.")
    lines.append("\n## Verdict")
    if v.get("proof"):
        lines.append(f"- **Real-world proof: {v['proof']['status']}**: {v['proof']['text']}")
    if v.get("sanity"):
        lines.append(f"- **Sanity gate: {'pass' if v['sanity']['pass'] else 'fail'}**: {v['sanity']['text']}")
    for label, key in (("Real cases", "real"), ("Leak-safe real cases", "leak_safe"), ("Synthetic sanity cases", "synthetic")):
        s = r.get(key)
        if not s:
            continue
        lines.append(f"\n## {label} ({s['cases']})\n| metric | Cohorte | single-prompt baseline |\n|---|---|---|")
        c, b = s["cohorte"], s["baseline"]
        lines.append(f"| winner accuracy ({s['winner_cases']} cases) | {_pct(c['winner_accuracy'])} | "
                     f"{_pct(b['winner_accuracy'])} |")
        lines.append(f"| objection recall ({c['objections_total']} objections) | {_pct(c['objection_recall'])} | "
                     f"{_pct(b['objection_recall'])} |")
        lines.append(f"| rank correlation with real metric ({c['points']} points) | "
                     f"{'–' if c['spearman'] is None else c['spearman']} | "
                     f"{'–' if b['spearman'] is None else b['spearman']} |")
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
        lines.append(f"| {row['id']} | {row['kind']} | {'synthetic' if row['synthetic'] else 'real'} | "
                     f"{row['winner'] or '–'} | {cell('cohorte')} | {cell('baseline')} | "
                     f"{f'{found_c} / {found_b} of {total}' if total else '–'} |")
    lines.append("\n## How to read this\n"
                 "- Both methods see exactly the same material and use the same model; only the method differs.\n"
                 "- Synthetic cases have planted, obvious differences: passing them shows the engine can detect a "
                 "difference, not that it predicts reality.\n"
                 "- Only real cases with outcomes the model can't have memorised (leak-safe) prove prediction. "
                 f"The gate needs ≥{GATE_MIN_CASES} of them and p < 0.05.")
    return "\n".join(lines)
