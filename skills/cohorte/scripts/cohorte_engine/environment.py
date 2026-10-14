"""Stage 2 - environment setup: personas + simulation config (MiroFish steps 2a-2d).

  1. expand crowd archetypes (count > 1) into distinct individuals
  2. generate one persona per agent (individual vs institutional prompts)
  3. time configuration      (simulated duration, round length, daily rhythm)
  4. event configuration     (hot topics, narrative direction, kick-off posts)
  5. agent activity configs  (activity level, active hours, stance, influence) in batches
  6. assign kick-off posts / scheduled events to agents, build the initial follow graph
"""

from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

from . import prompts as P
from .llm import LLMClient, LLMRequest
from .util import (
    clamp, coerce_str, coerce_str_list, parse_json_response, rng_for, slugify, to_int, truncate, write_json,
)
from .world import Sim

MBTI_TYPES = [
    "INTJ", "INTP", "ENTJ", "ENTP", "INFJ", "INFP", "ENFJ", "ENFP",
    "ISTJ", "ISFJ", "ESTJ", "ESFJ", "ISTP", "ISFP", "ESTP", "ESFP",
]
MEDIA_HINTS = ("media", "outlet", "news", "journal", "newspaper", "press", "publisher", "magazine", "blog")
STANCES = ("supportive", "opposing", "neutral", "observer")
AGENT_BATCH = 15
CONTEXT_CHARS = 3000


def _progress(msg: str) -> None:
    print(f"[setup] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Context helpers
# --------------------------------------------------------------------------- #

def _entity_context(world: Dict[str, Any], seed: str, ent: Dict[str, Any]) -> str:
    """Stand-in for MiroFish's Zep graph search: facts, relations and seed passages about the entity."""
    names = {e["id"]: e["name"] for e in world["entities"]}
    parts = [f"Simulation requirement: {world['simulation_requirement']}"]
    if ent.get("facts"):
        parts.append("Known facts:\n" + "\n".join(f"- {f}" for f in ent["facts"]))
    rels = [
        f"- {names.get(r['source'], r['source'])} {r['type']} {names.get(r['target'], r['target'])}"
        + (f": {r['fact']}" if r.get("fact") else "")
        for r in world.get("relations", [])
        if ent["id"] in (r["source"], r["target"])
    ]
    if rels:
        parts.append("Relations:\n" + "\n".join(rels))
    if seed:
        name_low = ent["name"].lower()
        paras = [p.strip() for p in seed.split("\n\n") if p.strip()]
        hits = [p for p in paras if name_low in p.lower()]
        excerpt = "\n\n".join(hits) if hits else "\n\n".join(paras)
        parts.append("Seed material:\n" + excerpt)
    return "\n\n".join(parts)[:CONTEXT_CHARS]


def _world_context(world: Dict[str, Any], seed: str, limit: int = 6000) -> str:
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for e in world["entities"]:
        by_type.setdefault(e["type"], []).append(e)
    lines = [f"## Simulation requirement\n{world['simulation_requirement']}", "", f"## Entities"]
    for etype, ents in by_type.items():
        lines.append(f"### {etype} ({sum(e['count'] for e in ents)} agents)")
        for e in ents[:8]:
            crowd = f" [crowd of {e['count']}]" if e["count"] > 1 else ""
            lines.append(f"- {e['name']}{crowd}: {truncate(e.get('summary', ''), 160)}")
    ctx = "\n".join(lines)
    if seed:
        room = max(0, limit - len(ctx) - 40)
        ctx += "\n\n## Seed material\n" + seed[:room] + ("\n...(truncated)" if len(seed) > room else "")
    return ctx


# --------------------------------------------------------------------------- #
# 1. Crowds
# --------------------------------------------------------------------------- #

def _expand_entities(sim: Sim, llm: LLMClient, world: Dict[str, Any], seed: str) -> List[Dict[str, Any]]:
    settings = world["settings"]
    crowd_reqs = []
    for ent in world["entities"]:
        if ent["count"] > 1:
            crowd_reqs.append(LLMRequest(
                id=f"crowd:{ent['id']}", kind="crowd", system=[P.PERSONA_SYSTEM], effort="medium",
                max_tokens=8000,
                user=P.CROWD_SKETCH_PROMPT.format(
                    count=ent["count"], name=ent["name"], entity_type=ent["type"],
                    summary=ent.get("summary") or "(none)",
                    diversity=ent.get("diversity") or "realistic variety",
                    context=_entity_context(world, seed, ent),
                ) + "\n" + P.language_rule(settings["language"]),
                meta={"count": ent["count"], "name": ent["name"]},
            ))
    if crowd_reqs:
        _progress(f"expanding {len(crowd_reqs)} crowd archetype(s) into individuals")
    results = llm.run_batch(crowd_reqs, label="crowd expansion")

    members: List[Dict[str, Any]] = []
    for ent in world["entities"]:
        if ent["count"] == 1:
            members.append({"entity": ent, "name": ent["name"], "sketch": None})
            continue
        people = []
        text = results.get(f"crowd:{ent['id']}")
        if text:
            try:
                data = parse_json_response(text)
                people = data.get("people", data) if isinstance(data, dict) else data
            except ValueError:
                people = []
        people = [p for p in people if isinstance(p, dict) and p.get("name")][: ent["count"]]
        while len(people) < ent["count"]:
            people.append({"name": f"{ent['name']} #{len(people) + 1}", "angle": ""})
        for p in people:
            members.append({"entity": ent, "name": coerce_str(p["name"]), "sketch": p})
    return members


# --------------------------------------------------------------------------- #
# 2. Personas
# --------------------------------------------------------------------------- #

def _normalize_gender(value: Any, kind: str) -> str:
    if kind == "group":
        return "other"
    v = coerce_str(value).lower()
    if v in ("male", "man", "m", "男"):
        return "male"
    if v in ("female", "woman", "f", "女"):
        return "female"
    return "nonbinary" if v else "unspecified"


def _rule_based_profile(member: Dict[str, Any], rng) -> Dict[str, Any]:
    ent, sketch = member["entity"], member["sketch"] or {}
    if ent["kind"] == "group":
        return {
            "bio": truncate(ent.get("summary") or f"Official account of {ent['name']}.", 200),
            "persona": ent.get("summary") or f"{ent['name']} is an institutional account that communicates official positions.",
            "age": 30, "gender": "other", "mbti": "ISTJ", "country": "", "profession": ent["type"],
            "interested_topics": ["Official announcements", "Public affairs"],
        }
    return {
        "bio": truncate(sketch.get("angle") or ent.get("summary") or f"{ent['type']}", 200),
        "persona": f"{member['name']} is a {ent['type']}. {ent.get('summary', '')} {sketch.get('angle', '')}".strip(),
        "age": to_int(sketch.get("age"), rng.randint(20, 60)),
        "gender": _normalize_gender(sketch.get("gender") or rng.choice(["male", "female"]), "individual"),
        "mbti": rng.choice(MBTI_TYPES), "country": coerce_str(sketch.get("country")),
        "profession": coerce_str(sketch.get("profession")) or ent["type"],
        "interested_topics": ["General", "Social issues"],
    }


def _persona_request(world, seed, member, agent_id) -> LLMRequest:
    ent, sketch = member["entity"], member["sketch"]
    settings = world["settings"]
    attrs = json.dumps(ent.get("attributes") or {}, ensure_ascii=False) or "none"
    common = dict(
        name=member["name"], entity_type=ent["type"],
        summary=ent.get("summary") or "(none)", attributes=attrs,
        context=_entity_context(world, seed, ent), words=settings["persona_words"],
        language_rule=P.language_rule(settings["language"]),
    )
    if ent["kind"] == "group":
        user = P.PERSONA_GROUP_PROMPT.format(**common)
    else:
        sketch_txt = ""
        if sketch:
            sketch_txt = (
                "This person is one member of a crowd archetype. Their individual sketch: "
                + json.dumps(sketch, ensure_ascii=False) + "\n"
            )
        user = P.PERSONA_INDIVIDUAL_PROMPT.format(sketch=sketch_txt, **common)
    return LLMRequest(
        id=f"persona:{agent_id}", kind="persona",
        system=[P.PERSONA_SYSTEM + "\n\n" + P.language_rule(settings["language"])],
        user=user, effort="medium", max_tokens=6000,
        meta={"name": member["name"], "kind": ent["kind"], "type": ent["type"]},
    )


def _build_profiles(sim, llm, world, seed, members) -> List[Dict[str, Any]]:
    reqs = [_persona_request(world, seed, m, i) for i, m in enumerate(members)]
    _progress(f"generating {len(reqs)} personas")
    results = llm.run_batch(reqs, label="personas")
    seed_val = world["settings"]["seed"]
    profiles = []
    used_usernames = set()
    for i, m in enumerate(members):
        rng = rng_for(seed_val, "profile", i)
        data: Dict[str, Any] = {}
        text = results.get(f"persona:{i}")
        if text:
            try:
                parsed = parse_json_response(text)
                data = parsed if isinstance(parsed, dict) else {}
            except ValueError:
                data = {}
        if not data.get("persona"):
            data = {**_rule_based_profile(m, rng), **{k: v for k, v in data.items() if v}}
        ent = m["entity"]
        username = slugify(m["name"], 24)
        while username in used_usernames:
            username = f"{slugify(m['name'], 20)}_{rng.randint(10, 999)}"
        used_usernames.add(username)
        profiles.append({
            "agent_id": i,
            "name": m["name"],
            "username": username,
            "entity_id": ent["id"],
            "entity_name": ent["name"],
            "entity_type": ent["type"],
            "kind": ent["kind"],
            "bio": truncate(coerce_str(data.get("bio")) or ent.get("summary", ""), 220),
            "persona": coerce_str(data.get("persona")),
            "age": 30 if ent["kind"] == "group" else to_int(data.get("age"), 30),
            "gender": _normalize_gender(data.get("gender"), ent["kind"]),
            "mbti": coerce_str(data.get("mbti")).upper()[:4] or rng.choice(MBTI_TYPES),
            "country": coerce_str(data.get("country")),
            "profession": coerce_str(data.get("profession")) or ent["type"],
            "interested_topics": coerce_str_list(data.get("interested_topics"))[:8],
            "sketch": m["sketch"],
        })
    return profiles


# --------------------------------------------------------------------------- #
# 3. Time config
# --------------------------------------------------------------------------- #

def _default_time_config(n: int) -> Dict[str, Any]:
    return {
        "total_simulation_hours": 48, "minutes_per_round": 60, "start_hour": 8,
        "agents_per_hour_min": max(1, n // 15), "agents_per_hour_max": max(5, n // 5),
        "peak_hours": [19, 20, 21, 22], "off_peak_hours": [0, 1, 2, 3, 4, 5],
        "morning_hours": [6, 7, 8], "work_hours": list(range(9, 19)),
        "timezone": "local", "reasoning": "default rhythm",
    }


def _hours(value: Any, default: List[int]) -> List[int]:
    if not isinstance(value, list):
        return default
    out = sorted({h for h in (to_int(v) for v in value) if h is not None and 0 <= h <= 23})
    return out or default


def _parse_time_config(raw: Dict[str, Any], n: int, settings: Dict[str, Any]) -> Dict[str, Any]:
    d = _default_time_config(n)
    lo = to_int(raw.get("agents_per_hour_min"), d["agents_per_hour_min"])
    hi = to_int(raw.get("agents_per_hour_max"), d["agents_per_hour_max"])
    # Same guards as MiroFish: never more than the population, min < max.
    if lo > n:
        lo = max(1, n // 10)
    if hi > n:
        hi = max(lo + 1, n // 2)
    if lo >= hi:
        lo = max(1, hi // 2)
    tc = {
        "total_simulation_hours": int(clamp(raw.get("total_simulation_hours"), 6, 336, d["total_simulation_hours"])),
        "minutes_per_round": int(clamp(raw.get("minutes_per_round"), 15, 240, d["minutes_per_round"])),
        "start_hour": int(clamp(raw.get("start_hour"), 0, 23, 8)),
        "agents_per_hour_min": max(1, lo),
        "agents_per_hour_max": max(1, hi),
        "peak_hours": _hours(raw.get("peak_hours"), d["peak_hours"]),
        "off_peak_hours": _hours(raw.get("off_peak_hours"), d["off_peak_hours"]),
        "morning_hours": _hours(raw.get("morning_hours"), d["morning_hours"]),
        "work_hours": _hours(raw.get("work_hours"), d["work_hours"]),
        "peak_activity_multiplier": 1.5,
        "off_peak_activity_multiplier": 0.05,
        "morning_activity_multiplier": 0.4,
        "work_activity_multiplier": 0.7,
        "timezone": coerce_str(raw.get("timezone")) or "local",
        "reasoning": coerce_str(raw.get("reasoning")),
    }
    if settings.get("total_hours"):
        tc["total_simulation_hours"] = int(settings["total_hours"])
    if settings.get("minutes_per_round"):
        tc["minutes_per_round"] = int(settings["minutes_per_round"])
    if settings.get("start_hour") is not None:
        tc["start_hour"] = int(settings["start_hour"])
    tc["total_rounds"] = math.ceil(tc["total_simulation_hours"] * 60 / tc["minutes_per_round"])
    return tc


# --------------------------------------------------------------------------- #
# 5. Agent configs
# --------------------------------------------------------------------------- #

def _rule_agent_config(profile: Dict[str, Any]) -> Dict[str, Any]:
    etype = profile["entity_type"].lower()
    if profile["kind"] == "group" and any(h in etype for h in MEDIA_HINTS):
        return {"activity_level": 0.5, "posts_per_hour": 0.8, "comments_per_hour": 0.3,
                "active_hours": list(range(7, 24)), "response_delay_min": 5, "response_delay_max": 30,
                "sentiment_bias": 0.0, "stance": "observer", "influence_weight": 2.5}
    if profile["kind"] == "group":
        return {"activity_level": 0.2, "posts_per_hour": 0.1, "comments_per_hour": 0.05,
                "active_hours": list(range(9, 18)), "response_delay_min": 60, "response_delay_max": 240,
                "sentiment_bias": 0.0, "stance": "neutral", "influence_weight": 3.0}
    if any(h in etype for h in ("expert", "professor", "official", "journalist", "influencer", "analyst", "creator")):
        return {"activity_level": 0.5, "posts_per_hour": 0.3, "comments_per_hour": 0.5,
                "active_hours": list(range(8, 23)), "response_delay_min": 15, "response_delay_max": 90,
                "sentiment_bias": 0.0, "stance": "neutral", "influence_weight": 1.8}
    return {"activity_level": 0.7, "posts_per_hour": 0.5, "comments_per_hour": 1.2,
            "active_hours": [8, 9, 12, 13, 18, 19, 20, 21, 22, 23], "response_delay_min": 2,
            "response_delay_max": 20, "sentiment_bias": 0.0, "stance": "neutral", "influence_weight": 1.0}


def _agent_configs(llm, world, profiles, timezone) -> List[Dict[str, Any]]:
    settings = world["settings"]
    reqs = []
    for start in range(0, len(profiles), AGENT_BATCH):
        batch = profiles[start:start + AGENT_BATCH]
        agents_json = json.dumps([
            {"agent_id": p["agent_id"], "name": p["name"], "entity_type": p["entity_type"], "kind": p["kind"],
             "summary": truncate(p["bio"] + " " + p["persona"], 300)}
            for p in batch
        ], ensure_ascii=False, indent=1)
        reqs.append(LLMRequest(
            id=f"agent_config:{start}", kind="agent_config",
            system=[P.AGENT_CONFIG_SYSTEM + "\n\n" + P.language_rule(settings["language"])],
            user=P.AGENT_CONFIG_PROMPT.format(
                requirement=world["simulation_requirement"], timezone=timezone, agents_json=agents_json),
            effort="low", max_tokens=8000, meta={"agent_ids": [p["agent_id"] for p in batch]},
        ))
    _progress(f"generating activity configs ({len(reqs)} batch(es))")
    results = llm.run_batch(reqs, label="agent configs")
    llm_cfgs: Dict[int, Dict[str, Any]] = {}
    for text in results.values():
        if not text:
            continue
        try:
            data = parse_json_response(text)
        except ValueError:
            continue
        items = data.get("agent_configs", []) if isinstance(data, dict) else data
        for c in items if isinstance(items, list) else []:
            aid = to_int(c.get("agent_id")) if isinstance(c, dict) else None
            if aid is not None:
                llm_cfgs[aid] = c
    configs = []
    for p in profiles:
        rule = _rule_agent_config(p)
        raw = {**rule, **llm_cfgs.get(p["agent_id"], {})}
        stance = coerce_str(raw.get("stance")).lower()
        configs.append({
            "agent_id": p["agent_id"],
            "entity_type": p["entity_type"],
            "activity_level": clamp(raw.get("activity_level"), 0.01, 1.0, rule["activity_level"]),
            "posts_per_hour": clamp(raw.get("posts_per_hour"), 0.0, 10.0, rule["posts_per_hour"]),
            "comments_per_hour": clamp(raw.get("comments_per_hour"), 0.0, 20.0, rule["comments_per_hour"]),
            "active_hours": _hours(raw.get("active_hours"), rule["active_hours"]),
            "response_delay_min": int(clamp(raw.get("response_delay_min"), 0, 1440, rule["response_delay_min"])),
            "response_delay_max": int(clamp(raw.get("response_delay_max"), 0, 1440, rule["response_delay_max"])),
            "sentiment_bias": clamp(raw.get("sentiment_bias"), -1.0, 1.0, 0.0),
            "stance": stance if stance in STANCES else rule["stance"],
            "influence_weight": clamp(raw.get("influence_weight"), 0.1, 5.0, rule["influence_weight"]),
        })
    return configs


# --------------------------------------------------------------------------- #
# 4 + 6. Events, assignment, follow graph
# --------------------------------------------------------------------------- #

TYPE_ALIASES = {
    "official": ["official", "university", "governmentagency", "government"],
    "university": ["university", "official"],
    "mediaoutlet": ["mediaoutlet", "media"],
    "student": ["student", "person"],
    "professor": ["professor", "expert", "teacher"],
    "organization": ["organization", "ngo", "company", "group"],
    "person": ["person", "student", "alumni"],
}


def _assign_posters(posts, profiles, agent_cfgs) -> None:
    """MiroFish _assign_initial_post_agents: exact type, then aliases, then most influential agent."""
    by_type: Dict[str, List[int]] = {}
    for p in profiles:
        by_type.setdefault(p["entity_type"].lower(), []).append(p["agent_id"])
    influence = {c["agent_id"]: c["influence_weight"] for c in agent_cfgs}
    used: Dict[str, int] = {}

    def pick(t: str) -> Optional[int]:
        if t in by_type:
            ids = by_type[t]
            idx = used.get(t, 0)
            used[t] = idx + 1
            return ids[idx % len(ids)]
        return None

    for post in posts:
        if post.get("poster_agent_id") is not None:
            continue
        ptype = coerce_str(post.get("poster_type")).lower()
        aid = pick(ptype)
        if aid is None:
            for key, aliases in TYPE_ALIASES.items():
                if ptype == key or ptype in aliases:
                    for alias in aliases:
                        aid = pick(alias)
                        if aid is not None:
                            break
                if aid is not None:
                    break
        if aid is None:
            aid = max(influence, key=lambda k: influence[k]) if influence else 0
        post["poster_agent_id"] = aid


def _event_config(llm, world, seed, profiles, agent_cfgs) -> Dict[str, Any]:
    settings = world["settings"]
    n_generated = int(settings.get("initial_posts", 5))
    if world.get("initial_posts"):
        n_generated = min(n_generated, 2)
    type_examples: Dict[str, List[str]] = {}
    for p in profiles:
        type_examples.setdefault(p["entity_type"], [])
        if len(type_examples[p["entity_type"]]) < 3:
            type_examples[p["entity_type"]].append(p["name"])
    type_info = "\n".join(f"- {t}: {', '.join(ex)}" for t, ex in type_examples.items())
    req = LLMRequest(
        id="event_config", kind="event_config",
        system=[P.EVENT_CONFIG_SYSTEM + "\n\n" + P.language_rule(settings["language"])],
        user=P.EVENT_CONFIG_PROMPT.format(
            requirement=world["simulation_requirement"], context=_world_context(world, seed),
            type_info=type_info, n_posts=n_generated, language_rule=P.language_rule(settings["language"]),
        ),
        effort="medium", max_tokens=8000, meta={"types": list(type_examples), "n_posts": n_generated},
    )
    _progress("generating event configuration (hot topics, kick-off posts)")
    text = llm.run_batch([req], label="event config").get("event_config")
    raw: Dict[str, Any] = {}
    if text:
        try:
            parsed = parse_json_response(text)
            raw = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            raw = {}
    platforms = settings["platforms"]
    posts = []
    # Explicit posts from world.json first: these are usually the exact copy being tested.
    first_agent_of = {}
    for p in profiles:
        first_agent_of.setdefault(p["entity_id"], p["agent_id"])
    for wp in world.get("initial_posts", []):
        posts.append({
            "content": wp["content"], "poster_type": None,
            "poster_agent_id": first_agent_of.get(wp["poster"]) if wp.get("poster") else None,
            "platforms": wp["platforms"] or platforms, "source": "world",
        })
    for gp in (raw.get("initial_posts") or [])[:n_generated] if n_generated else []:
        if isinstance(gp, dict) and coerce_str(gp.get("content")):
            posts.append({
                "content": coerce_str(gp["content"]), "poster_type": coerce_str(gp.get("poster_type")) or "Person",
                "poster_agent_id": None, "platforms": platforms, "source": "generated",
            })
    _assign_posters(posts, profiles, agent_cfgs)

    events = []
    for ev in world.get("scheduled_events", []):
        events.append({
            "at_hour": ev.get("at_hour"), "at_round": ev.get("at_round"), "content": ev["content"],
            "poster_agent_id": first_agent_of.get(ev["poster"]) if ev.get("poster") else None,
            "platforms": ev["platforms"] or platforms, "label": ev.get("label", ""),
        })
    return {
        "hot_topics": coerce_str_list(raw.get("hot_topics"))[:12],
        "narrative_direction": coerce_str(raw.get("narrative_direction")),
        "initial_posts": posts,
        "scheduled_events": events,
        "reasoning": coerce_str(raw.get("reasoning")),
    }


def _follow_graph(world, profiles, agent_cfgs, seed_val) -> Dict[int, List[int]]:
    """Initial follows: related entities follow each other; people follow a few influential accounts."""
    by_entity: Dict[str, List[int]] = {}
    for p in profiles:
        by_entity.setdefault(p["entity_id"], []).append(p["agent_id"])
    follows: Dict[int, set] = {p["agent_id"]: set() for p in profiles}
    for rel in world.get("relations", []):
        for a in by_entity.get(rel["source"], []):
            for b in by_entity.get(rel["target"], []):
                if a != b:
                    follows[a].add(b)
                    follows[b].add(a)
    influence = {c["agent_id"]: c["influence_weight"] for c in agent_cfgs}
    top = sorted(influence, key=lambda k: -influence[k])[: max(3, len(profiles) // 5)]
    for p in profiles:
        rng = rng_for(seed_val, "follow", p["agent_id"])
        for t in top:
            if t != p["agent_id"] and rng.random() < 0.35:
                follows[p["agent_id"]].add(t)
        # members of the same crowd know a couple of peers
        peers = [a for a in by_entity[p["entity_id"]] if a != p["agent_id"]]
        for peer in rng.sample(peers, min(2, len(peers))):
            follows[p["agent_id"]].add(peer)
    return {k: sorted(v) for k, v in follows.items()}


PLATFORM_DEFAULTS = {
    "twitter": {"recency_weight": 0.4, "popularity_weight": 0.3, "relevance_weight": 0.3,
                "viral_threshold": 10, "echo_chamber_strength": 0.5},
    "reddit": {"recency_weight": 0.3, "popularity_weight": 0.4, "relevance_weight": 0.3,
               "viral_threshold": 15, "echo_chamber_strength": 0.6},
}


def run_setup(sim: Sim, llm: LLMClient) -> Dict[str, Any]:
    sim.require("world.json")
    world = sim.world()
    seed = sim.seed_text()
    settings = world["settings"]

    members = _expand_entities(sim, llm, world, seed)
    profiles = _build_profiles(sim, llm, world, seed, members)
    n = len(profiles)

    _progress("generating time configuration")
    time_req = LLMRequest(
        id="time_config", kind="time_config",
        system=[P.TIME_CONFIG_SYSTEM + "\n\n" + P.language_rule(settings["language"])],
        user=P.TIME_CONFIG_PROMPT.format(
            context=_world_context(world, seed, 4000), max_agents=max(1, int(n * 0.9)),
            requested_hours=settings.get("total_hours") or "decide",
            requested_minutes=settings.get("minutes_per_round") or "decide",
        ),
        effort="low", max_tokens=3000, meta={"n": n},
    )
    raw_time: Dict[str, Any] = {}
    text = llm.run_batch([time_req], label="time config").get("time_config")
    if text:
        try:
            parsed = parse_json_response(text)
            raw_time = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            raw_time = {}
    time_config = _parse_time_config(raw_time, n, settings)

    agent_cfgs = _agent_configs(llm, world, profiles, time_config["timezone"])
    event_config = _event_config(llm, world, seed, profiles, agent_cfgs)

    follows = _follow_graph(world, profiles, agent_cfgs, settings["seed"])
    for p, c in zip(profiles, agent_cfgs):
        rng = rng_for(settings["seed"], "followers", p["agent_id"])
        base = 1500 if p["kind"] == "group" else 150
        p["followers"] = int(base * c["influence_weight"] ** 2 * rng.uniform(0.6, 1.4))
        p["following"] = follows[p["agent_id"]]

    config = {
        "requirement": world["simulation_requirement"],
        "title": world["title"],
        "subject": world["subject"],
        "language": settings["language"],
        "seed": settings["seed"],
        "platforms": settings["platforms"],
        "time_config": time_config,
        "event_config": event_config,
        "agent_configs": agent_cfgs,
        "platform_configs": {p: dict(PLATFORM_DEFAULTS[p]) for p in settings["platforms"]},
        "probes": world.get("probes", []),
        "turn": {
            "max_actions": int(settings["max_actions_per_turn"]),
            "memory_size": int(settings["memory_size"]),
            "feed_size": settings["feed_size"],
            "effort": "low",
            "max_tokens": 4000,
        },
    }
    write_json(sim.path("agents.json"), profiles)
    write_json(sim.path("config.json"), config)
    meta = sim.meta()
    meta["status"] = "ready"
    sim.save_meta(meta)
    _progress(f"done: {n} agents, {time_config['total_rounds']} rounds "
              f"({time_config['total_simulation_hours']}h, {time_config['minutes_per_round']} min/round)")
    return config
