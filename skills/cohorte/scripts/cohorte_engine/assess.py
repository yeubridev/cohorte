"""Assessment panel: how the cohort rates the subject, and how much to trust that rating.

Every individual robot (named people, delegates, small crowds) and every crowd archetype experiences the subject
directly (it reads the article, lands on the page, watches the trailer as described) and rates it on the
dimensions of its subject kind. Answers are weighted by how many agents each respondent stands for.

The panel is repeated over several runs (different exposure contexts) and optionally several models. The spread
between runs and models, the completeness of the material and the respondent base feed the confidence index,
which stays capped at 60/100 until the kind is calibrated against real outcomes (see calibration.py).
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import calibration
from . import prompts as P
from .llm import LLMClient, LLMRequest, resolve_model
from .subjects import get_subject, potential
from .util import CohorteError, clamp, coerce_str, coerce_str_list, parse_json_response, read_json, truncate, write_json
from .world import Sim

CONTEXTS = [
    "You come across it on your phone in the evening, half paying attention.",
    "A friend sends it to you saying \"you should look at this\".",
    "You see it as a sponsored placement while doing something else.",
    "You find it while actively looking for something like it.",
    "Someone you follow online mentions it, with mixed comments under the post.",
    "You look at it on a computer during a work break, with a few minutes to spare.",
]
EVIDENCE_SCORE = {"full": 1.0, "partial": 0.7, "description": 0.4}
SEED_CHARS = 14000

ASSESS_SYSTEM_SUFFIX = """You are taking part in a private study about a {noun}. Stay fully in character: judge it
exactly as this person would, with their tastes, budget, habits, patience and blind spots. Being harsh, bored or
indifferent is fine when that is what this person would feel; do not be polite. Never mention being simulated.
{language_rule}
Return one valid JSON object only."""

ASSESS_USER = """## The {noun} you are judging
{seed}

## How you encounter it
{experience}
{context}

## Rate it honestly (integers)
{questions}

Return JSON:
{{"scores": {{{score_keys}}},
  "verdict": "one or two sentences, in your own words and voice",
  "likes": ["what works for you (short)"], "dislikes": ["what puts you off (short)"],
  "would_do": "what you would actually do next"}}"""

GROUP_SYSTEM = """You estimate how a group of real people reacts to a {noun}. You know the group's mindset and
voice. Estimate the group's average answers and how much members differ. Be realistic: groups are often
indifferent; do not inflate scores. Members never know they are simulated.
{language_rule}
Return one valid JSON object only."""

GROUP_USER = """## The group
"{label}" — part of the segment {segment} ({entity_type}), {members} people.
{description}
How they write: {voice}

## The {noun} they are judging
{seed}

## How they encounter it
{experience}
{context}

## Questions (each member answers on the given scale)
{questions}

Return JSON:
{{"scores": {{{score_keys}}}  (group means),
  "spread": {{{spread_keys}}}  (standard deviation within the group),
  "verdict": "one or two sentences a typical member would say, in their voice",
  "likes": ["..."], "dislikes": ["..."], "would_do": "what most of them actually do next"}}"""


def _questions(dims: List[Dict[str, Any]]) -> str:
    return "\n".join(f"- {d['key']} ({d['min']}-{d['max']}): {d['question']}" for d in dims)


def _subject(sim: Sim, config: Dict[str, Any], world: Dict[str, Any]) -> Dict[str, Any]:
    subject = read_json(sim.path("subject.json"), default={})
    kind = subject.get("kind") or world.get("settings", {}).get("subject_kind") or "generic"
    profile = get_subject(kind)
    subject.setdefault("kind", profile["kind"])
    subject.setdefault("evidence", world.get("settings", {}).get("evidence", "description"))
    return {**profile, **{k: v for k, v in subject.items() if k in ("title", "evidence", "sources")}}


def _respondents(config: Dict[str, Any], profiles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Individuals (weight 1) + crowd archetypes (weight = members). Institutions answer as stakeholders."""
    out = []
    crowd_members = Counter(c["archetype"] for c in config["agent_configs"] if c.get("tier") == "crowd")
    for p in profiles:
        if p.get("tier") == "crowd":
            continue
        out.append({"type": "stakeholder" if p["kind"] == "group" else "individual", "profile": p,
                    "weight": 0.0 if p["kind"] == "group" else 1.0, "segment": p["entity_name"]})
    segments = (config.get("crowd") or {}).get("segments", {})
    for key, a in (config.get("crowd") or {}).get("archetypes", {}).items():
        seg = segments.get(a["entity_id"], {})
        out.append({"type": "archetype", "key": key, "archetype": a, "weight": float(crowd_members.get(key, 0)),
                    "segment": seg.get("name", a["entity_id"]), "entity_type": seg.get("type", "")})
    return out


def _requests(resp: Dict[str, Any], subj: Dict[str, Any], seed: str, run: int, model: str,
              language: str) -> LLMRequest:
    dims = subj["dimensions"]
    context = CONTEXTS[run % len(CONTEXTS)]
    score_keys = ", ".join(f'"{d["key"]}": <{d["min"]}-{d["max"]}>' for d in dims)
    common = dict(noun=subj["noun"], seed=seed, experience=subj["experience"], context=context,
                  questions=_questions(dims), score_keys=score_keys)
    rule = P.language_rule(language)
    meta = {"dims": [d["key"] for d in dims], "lo": [d["min"] for d in dims], "hi": [d["max"] for d in dims]}
    if resp["type"] == "archetype":
        a = resp["archetype"]
        return LLMRequest(
            id=f"assess:r{run}:{resp['key']}", kind="assess_group", salt=model,
            system=[GROUP_SYSTEM.format(noun=subj["noun"], language_rule=rule)],
            user=GROUP_USER.format(label=a["label"], segment=resp["segment"], entity_type=resp["entity_type"],
                                   members=int(resp["weight"]), description=a["description"], voice=a["voice"],
                                   spread_keys=", ".join(f'"{d["key"]}": <sd>' for d in dims), **common),
            effort="low", max_tokens=4000, meta={**meta, "name": a["label"]},
        )
    p = resp["profile"]
    identity = ", ".join(x for x in [f"{p.get('age')} y/o" if p["kind"] == "individual" else "organisation",
                                     p.get("profession", ""), p.get("country", "")] if x)
    persona = (f"## Who you are\n{p['name']} ({p['entity_type']}; {identity})\nBio: {p.get('bio', '')}\n\n"
               f"{p.get('persona', '')}")
    return LLMRequest(
        id=f"assess:r{run}:a{p['agent_id']}", kind="assess", salt=model,
        system=[persona, ASSESS_SYSTEM_SUFFIX.format(noun=subj["noun"], language_rule=rule)],
        user=ASSESS_USER.format(**common), effort="low", max_tokens=3000, meta={**meta, "name": p["name"]},
    )


def _parse(text: Optional[str], dims: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    try:
        data = parse_json_response(text)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("scores") if isinstance(data.get("scores"), dict) else data
    scores = {}
    for d in dims:
        if raw.get(d["key"]) is not None:
            scores[d["key"]] = clamp(raw[d["key"]], d["min"], d["max"], (d["min"] + d["max"]) / 2)
    if not scores:
        return None
    spread = data.get("spread") if isinstance(data.get("spread"), dict) else {}
    return {
        "scores": scores,
        "spread": {k: clamp(v, 0, 10, 0) for k, v in spread.items() if k in scores},
        "verdict": truncate(coerce_str(data.get("verdict")), 400),
        "likes": [truncate(x, 120) for x in coerce_str_list(data.get("likes"))[:4]],
        "dislikes": [truncate(x, 120) for x in coerce_str_list(data.get("dislikes"))[:4]],
        "would_do": truncate(coerce_str(data.get("would_do")), 240),
    }


def _weighted(rows: List[Dict[str, Any]], dims: List[Dict[str, Any]]) -> Dict[str, float]:
    out = {}
    for d in dims:
        num = den = 0.0
        for r in rows:
            v = r["answer"]["scores"].get(d["key"])
            if v is not None and r["weight"] > 0:
                num += v * r["weight"]
                den += r["weight"]
        if den:
            out[d["key"]] = round(num / den, 2)
    return out


def _norm(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text.lower()).strip()


def run_assessment(sim: Sim, llm_settings: Dict[str, Any], runs: int = 3, models: Optional[List[str]] = None,
                   dry_run: bool = False) -> Dict[str, Any]:
    sim.require("agents.json", "config.json")
    config, profiles, world = sim.config(), sim.agents(), sim.world()
    subj = _subject(sim, config, world)
    dims = subj["dimensions"]
    seed = sim.seed_text()
    seed = seed[:SEED_CHARS] + ("\n…(material truncated)" if len(seed) > SEED_CHARS else "")
    if not seed.strip():
        seed = world.get("simulation_requirement", "")
    respondents = _respondents(config, profiles)
    models = [resolve_model(m) for m in (models or [llm_settings.get("model")])]
    runs = max(1, runs)
    plan = [(run, models[run % len(models)]) for run in range(runs)]
    if dry_run:
        return {"calls": len(plan) * len(respondents), "respondents": len(respondents), "runs": runs, "models": models}

    clients = {m: LLMClient(sim.dir, {**llm_settings, "model": m}) for m in models}
    rows: List[Dict[str, Any]] = []
    for run, model in plan:
        reqs = [(r, _requests(r, subj, seed, run, model, config["language"])) for r in respondents]
        answers = clients[model].run_batch([q for _, q in reqs], label=f"assessment run {run + 1}/{runs} ({model})")
        for r, q in reqs:
            ans = _parse(answers.get(q.id), dims)
            if ans:
                rows.append({"run": run, "model": model, "weight": r["weight"], "segment": r["segment"],
                             "type": r["type"], "name": q.meta["name"], "answer": ans})
    if not rows:
        raise CohorteError("No usable answers came back; check the backend and try again.")

    # potential per run and per model
    per_run = {}
    for run, model in plan:
        rr = [x for x in rows if x["run"] == run]
        if any(x["weight"] > 0 for x in rr):
            per_run[run] = potential(_weighted(rr, dims), dims)
    pots = list(per_run.values())
    mean_pot = round(sum(pots) / len(pots), 1)
    sd = round(math.sqrt(sum((p - mean_pot) ** 2 for p in pots) / (len(pots) - 1)), 2) if len(pots) > 1 else None
    by_model = {m: round(sum(per_run[r] for r, mm in plan if mm == m and r in per_run) /
                         max(1, sum(1 for r, mm in plan if mm == m and r in per_run)), 1) for m in models}
    dim_means = _weighted(rows, dims)

    segments: Dict[str, Dict[str, Any]] = {}
    by_seg = defaultdict(list)
    for x in rows:
        if x["weight"] > 0:
            by_seg[x["segment"]].append(x)
    total_w = sum(x["weight"] for x in rows if x["run"] == plan[0][0])
    for seg, xs in by_seg.items():
        w = sum(x["weight"] for x in xs if x["run"] == plan[0][0])
        segments[seg] = {"share": round(w / total_w, 3) if total_w else 0, "scores": _weighted(xs, dims),
                         "potential": potential(_weighted(xs, dims), dims)}

    likes, dislikes = Counter(), Counter()
    label_of = {}
    for x in rows:
        for t in x["answer"]["likes"]:
            likes[_norm(t)] += x["weight"] or 1
            label_of.setdefault(_norm(t), t)
        for t in x["answer"]["dislikes"]:
            dislikes[_norm(t)] += x["weight"] or 1
            label_of.setdefault(_norm(t), t)
    quotes = [{"name": x["name"], "segment": x["segment"], "type": x["type"], "verdict": x["answer"]["verdict"],
               "would_do": x["answer"]["would_do"], "potential": potential(x["answer"]["scores"], dims)}
              for x in rows if x["run"] == plan[0][0] and x["answer"]["verdict"]]

    # confidence index
    home = sim.home
    stability = max(0.0, 1 - sd / 10) if sd is not None else 0.3
    evidence = EVIDENCE_SCORE.get(subj.get("evidence", "description"), 0.4)
    if len(models) > 1:
        vals = list(by_model.values())
        model_agree = max(0.0, 1 - (max(vals) - min(vals)) / 15)
    else:
        model_agree = None
    n_resp = len({(x["name"], x["segment"]) for x in rows if x["weight"] > 0})
    coverage = min(1.0, n_resp / 20)
    internal = 100 * (0.40 * stability + 0.25 * evidence + 0.20 * (model_agree if model_agree is not None else 0.5)
                      + 0.15 * coverage)
    cap = calibration.confidence_cap(home, subj["kind"])
    calibrated = calibration.apply(home, subj["kind"], mean_pot)
    confidence = round(min(internal, cap["cap"]))
    result = {
        "kind": subj["kind"], "title": subj.get("title", ""), "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "runs": runs, "models": models, "respondents": n_resp,
        "potential": {"mean": mean_pot, "sd_between_runs": sd, "per_run": pots, "by_model": by_model,
                      "low": round(min(pots), 1), "high": round(max(pots), 1)},
        "calibrated": calibrated,
        "dimensions": [{**d, "mean": dim_means.get(d["key"])} for d in dims],
        "segments": segments,
        "likes": [{"text": label_of[k], "weight": round(v, 1)} for k, v in likes.most_common(8)],
        "dislikes": [{"text": label_of[k], "weight": round(v, 1)} for k, v in dislikes.most_common(8)],
        "quotes": quotes,
        "stakeholders": [q for q in quotes if q["type"] == "stakeholder"],
        "confidence": {
            "index": confidence, "internal": round(internal), "cap": cap["cap"], "cap_reason": cap["reason"],
            "components": {"stability": round(stability, 2), "evidence": evidence,
                           "model_agreement": None if model_agree is None else round(model_agree, 2),
                           "coverage": round(coverage, 2)},
        },
        "outcomes_to_track": subj.get("outcomes", []),
    }
    write_json(sim.path("assessment.json"), result)
    md = render(result)
    with open(sim.path("assessment.md"), "w", encoding="utf-8") as f:
        f.write(md)
    return result


def _grade(index: int) -> str:
    return "high" if index >= 85 else "good" if index >= 70 else "moderate" if index >= 45 else "low"


def render(r: Dict[str, Any]) -> str:
    c = r["confidence"]
    pot = r["potential"]
    lines = [f"# Assessment — {r['title'] or r['kind']} ({r['kind']})",
             f"**Potential: {pot['mean']}/100** (runs ranged {pot['low']}–{pot['high']}) · "
             f"**Confidence index: {c['index']}/100 ({_grade(c['index'])})**",
             f"{r['respondents']} respondents standing for the whole cohort · {r['runs']} run(s) · "
             f"model(s): {', '.join(r['models'])}"]
    if r["calibrated"]:
        cal = r["calibrated"]
        lines.append(f"**Calibrated estimate:** {cal['estimate']}/100 (80% interval {cal['low']}–{cal['high']}), "
                     f"from {cal['n']} real outcomes, r = {cal['r']}, typical error ±{cal['mae']}")
        u = cal.get("in_units")
        if u:
            lines.append(f"In real units: **{u['estimate']} {u['metric']}** (80% interval {u['low']}–{u['high']})")
    comp = c["components"]
    lines.append(
        f"\nConfidence breakdown: stability across runs {comp['stability']} · completeness of material "
        f"{comp['evidence']} · model agreement {comp['model_agreement'] if comp['model_agreement'] is not None else 'not measured (use --models)'}"
        f" · respondent coverage {comp['coverage']} → internal {c['internal']}, capped at {c['cap']} "
        f"({c['cap_reason']}).")
    lines.append("\n## Dimensions\n| dimension | question | mean | scale |\n|---|---|---|---|")
    for d in r["dimensions"]:
        lines.append(f"| {d['key']} | {d['question']} | {d['mean'] if d['mean'] is not None else '–'} | "
                     f"{d['min']}-{d['max']} |")
    lines.append("\n## Segments\n| segment | share of cohort | potential |" + "".join(
        f" {d['key']} |" for d in r["dimensions"]) + "\n|---|---|---|" + "---|" * len(r["dimensions"]))
    for seg, s in sorted(r["segments"].items(), key=lambda kv: -kv[1]["share"]):
        lines.append(f"| {seg} | {s['share']:.1%} | {s['potential']} |" + "".join(
            f" {s['scores'].get(d['key'], '–')} |" for d in r["dimensions"]))
    if r["likes"]:
        lines.append("\n## What works\n" + "\n".join(f"- {x['text']} (weight {x['weight']})" for x in r["likes"]))
    if r["dislikes"]:
        lines.append("\n## What puts people off\n" + "\n".join(f"- {x['text']} (weight {x['weight']})"
                                                               for x in r["dislikes"]))
    lines.append("\n## Voices")
    ordered = sorted(r["quotes"], key=lambda q: q["potential"])
    picks = ordered[:4] + [q for q in ordered[::-1][:3] if q not in ordered[:4]]
    for q in picks:
        lines.append(f"> \"{q['verdict']}\" — {q['name']} ({q['segment']}, potential {q['potential']:.0f})")
        if q["would_do"]:
            lines.append(f">\n> Next: {q['would_do']}")
        lines.append("")
    lines.append(f"## To raise confidence\n- Track the real outcome ({', '.join(r['outcomes_to_track'])}) and record it "
                 f"with `calibrate add`.\n- Use `--runs 5` and `--models opus,sonnet` (or haiku) to measure stability "
                 f"and model agreement.\n- Ingest the full material (the page, the transcript, the text) rather than "
                 f"a description.")
    return "\n".join(lines)
