"""Crowd tier: thousands of agents at the cost of a few LLM calls per round.

Every crowd member is a real agent of the simulation (own profile, follows, feed, opinion, likes,
comments, posts), but its decisions are not one LLM call each. Following the "LLM archetypes" approach
of large-population simulators (e.g. AgentTorch):

  setup   one LLM call per crowd segment describes its archetypes (sub-groups with mood, voice,
          activity, susceptibility); members are sampled from them with individual variation.
  round   members come online by time of day x activity; each gets its own ranked feed; then ONE
          LLM call per segment and platform sees the posts its online members are looking at and
          returns, per archetype, reaction probabilities per post, comment/post texts in their
          voice, the archetype's mood and probe answers. Each member then samples its own actions
          from those probabilities, modulated by its activity and by how much it agrees with the
          author, and moves its opinion toward its archetype's mood (bounded by susceptibility).
"""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from . import prompts as P
from .llm import LLMRequest
from .util import clamp, coerce_str, coerce_str_list, parse_json_response, rng_for, slugify, to_int, truncate

STANCES = ("supportive", "opposing", "neutral", "observer")
POSTS_PER_PROMPT = 10
COMMENT_TEXTS = 3
FEED_SIZE = 6
BASE_REACTION = {"like": 0.03, "dislike": 0.0, "repost": 0.0, "comment": 0.0}
HOUR_PATTERNS = [  # (weight, active hours)
    (0.40, [7, 8, 12, 13, 18, 19, 20, 21, 22, 23]),
    (0.25, list(range(9, 18)) + [20, 21]),
    (0.20, [10, 11, 14, 15, 16, 17, 20, 21, 22, 23, 0, 1]),
    (0.15, [6, 7, 8, 9, 12, 13, 19, 20]),
]
FIRST_F = ["Amelia", "Sofia", "Lea", "Chloe", "Ines", "Hannah", "Mia", "Yuki", "Aisha", "Fatima", "Camila", "Lucia",
           "Emma", "Olivia", "Nora", "Ana", "Priya", "Mei", "Zara", "Elena", "Julia", "Sara", "Ingrid", "Amara",
           "Chiara", "Marta", "Leila", "Hana", "Isabel", "Claire", "Anouk", "Freya", "Noor", "Rosa", "Lina", "Maya"]
FIRST_M = ["Lucas", "Noah", "Hugo", "Leo", "Omar", "Arjun", "Kenji", "Mateo", "Diego", "Tom", "Jonas", "Samuel",
           "Ethan", "Liam", "Ali", "Yusuf", "Ravi", "Wei", "Pablo", "Marco", "Felix", "David", "Karim", "Theo",
           "Luca", "Mohamed", "Daniel", "Ivan", "Jules", "Tariq", "Emil", "Bruno", "Kofi", "Andre", "Nils", "Raphael"]
LAST = ["Martin", "Bernard", "Dubois", "Garcia", "Rossi", "Muller", "Schmidt", "Silva", "Santos", "Kowalski", "Nowak",
        "Novak", "Jensen", "Hansen", "Nielsen", "Smith", "Brown", "Wilson", "Taylor", "Kim", "Lee", "Park", "Chen",
        "Wang", "Tanaka", "Sato", "Khan", "Ahmed", "Haddad", "Okafor", "Mensah", "Diallo", "Traore", "Lopez",
        "Fernandez", "Moreau", "Laurent", "Fischer", "Weber", "Costa", "Ferreira", "Petrov", "Ivanova", "Horvat",
        "Popescu", "Yilmaz", "Demir", "Cohen", "Levi", "Murphy", "Kelly", "Ozturk", "Nguyen", "Tran", "Singh",
        "Patel", "Sharma", "Andersen", "Lindqvist", "Virtanen", "Bakker", "De Vries", "Janssen", "Peeters"]


# --------------------------------------------------------------------------- #
# Setup: archetypes and members
# --------------------------------------------------------------------------- #

def archetype_request(world: Dict[str, Any], ent: Dict[str, Any], n: int, context: str) -> LLMRequest:
    settings = world["settings"]
    return LLMRequest(
        id=f"archetypes:{ent['id']}", kind="archetypes",
        system=[P.PERSONA_SYSTEM + "\n\n" + P.language_rule(settings["language"])],
        user=P.ARCHETYPE_PROMPT.format(
            count=ent["count"], n=n, name=ent["name"], entity_type=ent["type"],
            summary=ent.get("summary") or "(none)", diversity=ent.get("diversity") or "realistic variety",
            context=context, subject=world["subject"], language_rule=P.language_rule(settings["language"]),
        ),
        effort="medium", max_tokens=12000, meta={"n": n, "name": ent["name"], "type": ent["type"]},
    )


def parse_archetypes(text: Optional[str], ent: Dict[str, Any], n: int) -> List[Dict[str, Any]]:
    items: List[Any] = []
    if text:
        try:
            data = parse_json_response(text)
            items = data.get("archetypes", []) if isinstance(data, dict) else data
        except ValueError:
            items = []
    out = []
    for i, a in enumerate(x for x in (items or []) if isinstance(x, dict)):
        stance = coerce_str(a.get("stance")).lower()
        lo = to_int(a.get("age_min"), 20) or 20
        hi = to_int(a.get("age_max"), 60) or 60
        out.append({
            "id": f"a{i + 1}",
            "label": coerce_str(a.get("label")) or f"{ent['name']} {i + 1}",
            "share": clamp(a.get("share"), 0.0, 1.0, 1.0 / max(1, n)),
            "description": coerce_str(a.get("description")) or ent.get("summary", ""),
            "voice": coerce_str(a.get("voice")),
            "interests": coerce_str_list(a.get("interests"))[:6],
            "professions": coerce_str_list(a.get("professions"))[:6],
            "countries": coerce_str_list(a.get("countries"))[:6],
            "age_min": max(14, min(lo, hi)), "age_max": min(95, max(lo, hi)),
            "female_share": clamp(a.get("female_share"), 0.0, 1.0, 0.5),
            "sentiment_mean": clamp(a.get("sentiment_mean"), -1.0, 1.0, 0.0),
            "sentiment_spread": clamp(a.get("sentiment_spread"), 0.0, 1.0, 0.3),
            "activity": clamp(a.get("activity"), 0.02, 1.0, 0.4),
            "susceptibility": clamp(a.get("susceptibility"), 0.0, 1.0, 0.3),
            "stance": stance if stance in STANCES else "neutral",
        })
    if not out:  # fallback: spread one generic segment over three moods
        for i, (label, mean, stance) in enumerate([("enthusiasts", 0.5, "supportive"),
                                                   ("undecided", 0.0, "neutral"),
                                                   ("skeptics", -0.5, "opposing")]):
            out.append({"id": f"a{i + 1}", "label": f"{ent['name']} - {label}", "share": 1 / 3,
                        "description": ent.get("summary", ""), "voice": "", "interests": [], "professions": [],
                        "countries": [], "age_min": 20, "age_max": 65, "female_share": 0.5,
                        "sentiment_mean": mean, "sentiment_spread": 0.3, "activity": 0.4,
                        "susceptibility": 0.3, "stance": stance})
    total = sum(a["share"] for a in out) or 1.0
    for a in out:
        a["share"] = a["share"] / total
    return out


def _allocate(shares: List[float], n: int) -> List[int]:
    raw = [s * n for s in shares]
    counts = [int(r) for r in raw]
    for i in sorted(range(len(raw)), key=lambda i: -(raw[i] - counts[i]))[: n - sum(counts)]:
        counts[i] += 1
    return counts


def make_members(ent: Dict[str, Any], archetypes: List[Dict[str, Any]], n: int, start_id: int, seed: int,
                 used_usernames: set) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Sample `n` crowd members (profiles, configs) from the segment's archetypes."""
    profiles, configs = [], []
    aid = start_id
    for arch, count in zip(archetypes, _allocate([a["share"] for a in archetypes], n)):
        for _ in range(count):
            rng = rng_for(seed, "crowd-member", ent["id"], aid)
            female = rng.random() < arch["female_share"]
            first = rng.choice(FIRST_F if female else FIRST_M)
            name = f"{first} {rng.choice(LAST)}"
            base = f"{slugify(first, 10)}_{slugify(name.split()[-1], 1)}"
            username = base
            while username in used_usernames:
                username = f"{base}{rng.randint(1, 9999)}"
            used_usernames.add(username)
            country = rng.choice(arch["countries"]) if arch["countries"] else ""
            profession = rng.choice(arch["professions"]) if arch["professions"] else ent["type"]
            age = rng.randint(arch["age_min"], arch["age_max"])
            bias = clamp(rng.gauss(arch["sentiment_mean"], arch["sentiment_spread"]), -1.0, 1.0, 0.0)
            hours = _pick_hours(rng)
            profiles.append({
                "agent_id": aid, "name": name, "username": username, "entity_id": ent["id"],
                "entity_name": ent["name"], "entity_type": ent["type"], "kind": "individual", "tier": "crowd",
                "archetype": f"{ent['id']}:{arch['id']}", "archetype_label": arch["label"],
                "bio": truncate(f"{profession}{', ' + country if country else ''}. {', '.join(arch['interests'][:3])}", 160),
                "persona": (f"{name}, {age}, {profession}{' from ' + country if country else ''}. One of "
                            f"'{arch['label']}' in the segment {ent['name']}: {arch['description']} "
                            f"How they write online: {arch['voice']}").strip(),
                "age": age, "gender": "female" if female else "male", "mbti": "", "country": country,
                "profession": profession, "interested_topics": arch["interests"], "sketch": None,
            })
            configs.append({
                "agent_id": aid, "entity_type": ent["type"], "tier": "crowd", "entity_id": ent["id"],
                "archetype": f"{ent['id']}:{arch['id']}",
                "activity_level": round(clamp(rng.gauss(arch["activity"], 0.15), 0.02, 1.0, 0.4), 3),
                "posts_per_hour": 0.2, "comments_per_hour": 0.5, "active_hours": hours,
                "response_delay_min": 1, "response_delay_max": 60,
                "sentiment_bias": round(bias, 3), "sentiment_offset": round(bias - arch["sentiment_mean"], 3),
                "stance": arch["stance"],
                "influence_weight": round(clamp(math.exp(rng.gauss(-0.2, 0.4)), 0.2, 3.0, 0.8), 3),
                "susceptibility": round(clamp(rng.gauss(arch["susceptibility"], 0.1), 0.0, 1.0, 0.3), 3),
            })
            aid += 1
    return profiles, configs


def _pick_hours(rng) -> List[int]:
    r, acc = rng.random(), 0.0
    for weight, hours in HOUR_PATTERNS:
        acc += weight
        if r <= acc:
            return sorted(hours)
    return sorted(HOUR_PATTERNS[0][1])


# --------------------------------------------------------------------------- #
# Simulation: activation, segment requests, sampling
# --------------------------------------------------------------------------- #

def crowd_active(crowd_cfgs: List[Dict[str, Any]], hour: int, multiplier: float, rate: float, rng) -> List[int]:
    active = []
    for c in crowd_cfgs:
        if hour in c["active_hours"] and rng.random() < min(0.95, rate * c["activity_level"] * multiplier * 2):
            active.append(c["agent_id"])
    return active


def reaction_keys(platform: str) -> Tuple[List[str], str]:
    if platform == "twitter":
        return ["like", "repost", "comment"], 'On this platform "comment" means quote-posting it with that text.'
    return ["like", "dislike", "comment"], '"comment" means replying in the thread with that text.'


def segment_request(platform, entity: Dict[str, Any], arch_meta: Dict[str, Dict[str, Any]],
                    online: Dict[str, List[int]], exposures: Counter, round_no: int, clock: str,
                    config: Dict[str, Any], render, moods: Dict[str, float],
                    history: Dict[str, List[str]]) -> Tuple[LLMRequest, List[int]]:
    keys, note = reaction_keys(platform.name)
    post_ids = [pid for pid, _ in exposures.most_common(POSTS_PER_PROMPT)]
    lines = []
    for arch_id, members in online.items():
        a = arch_meta[arch_id]
        recent = " Recently: " + " / ".join(history.get(arch_id, [])[-2:]) if history.get(arch_id) else ""
        lines.append(f"- {a['id']} \"{a['label']}\" ({a['share']:.0%} of segment, mood {moods.get(arch_id, 0):+.2f}, "
                     f"{len(members)} online): {a['description']} Voice: {a['voice']}{recent}")
    posts = "\n\n".join(render(pid, f" · seen by {exposures[pid]} of them") for pid in post_ids) or "(nothing)"
    probes = config.get("probes", [])
    req = LLMRequest(
        id=f"crowd:{platform.name}:r{round_no}:{entity['id']}", kind="crowd_turn",
        system=[P.CROWD_SYSTEM.format(platform_desc=P.PLATFORM_DESCRIPTIONS[platform.name],
                                      language_rule=P.language_rule(config["language"]))],
        user=P.CROWD_USER.format(
            clock=clock, platform_name=platform.name, subject=config["subject"], segment=entity["name"],
            entity_type=entity["type"], members_online=sum(len(m) for m in online.values()),
            segment_summary=truncate(entity.get("summary", ""), 400), archetypes="\n".join(lines), posts=posts,
            comment_note=note, probe_schema="".join("  " + line + "\n" for line in P.probe_schema(probes).splitlines()),
            reaction_keys=", ".join(f'"{k}": <0-1>' for k in keys),
        ),
        effort="low", max_tokens=12000,
        meta={"archetypes": list(online), "posts": post_ids, "keys": keys,
              "probes": [p["key"] for p in probes], "subject": config["subject"], "platform": platform.name},
    )
    return req, post_ids


def parse_segment(text: Optional[str], arch_ids: List[str], probes: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    data: Dict[str, Any] = {}
    if text:
        try:
            parsed = parse_json_response(text)
            data = parsed.get("archetypes", parsed) if isinstance(parsed, dict) else {}
        except ValueError:
            data = {}
    if isinstance(data, list):
        data = {coerce_str(x.get("id")): x for x in data if isinstance(x, dict)}
    for arch_id in arch_ids:
        raw = data.get(arch_id) if isinstance(data, dict) else None
        raw = raw if isinstance(raw, dict) else {}
        reactions = {}
        for pid, r in (raw.get("reactions") or {}).items() if isinstance(raw.get("reactions"), dict) else []:
            p = to_int(pid)
            if p is not None and isinstance(r, dict):
                reactions[p] = {k: clamp(r.get(k), 0.0, 0.9, 0.0) for k in ("like", "dislike", "repost", "comment")}
        comments = {}
        for pid, texts in (raw.get("comments") or {}).items() if isinstance(raw.get("comments"), dict) else []:
            p = to_int(pid)
            if p is not None:
                comments[p] = [t for t in coerce_str_list(texts) if t][:COMMENT_TEXTS]
        probe_vals = {}
        rp = raw.get("probes") if isinstance(raw.get("probes"), dict) else {}
        for pr in probes:
            if rp.get(pr["key"]) is not None:
                probe_vals[pr["key"]] = clamp(rp[pr["key"]], pr["min"], pr["max"], (pr["min"] + pr["max"]) / 2)
        out[arch_id] = {
            "ok": bool(raw),
            "thought": truncate(coerce_str(raw.get("thought")), 400),
            "sentiment": clamp(raw["sentiment"], -1.0, 1.0, 0.0) if raw.get("sentiment") is not None else None,
            "probes": probe_vals,
            "reactions": reactions,
            "comments": comments,
            "new_post_rate": clamp(raw.get("new_post_rate"), 0.0, 0.3, 0.0),
            "posts": [t for t in coerce_str_list(raw.get("posts")) if t][:COMMENT_TEXTS] if isinstance(
                raw.get("posts"), list) else [],
        }
    return out


def sample_member(aid: int, cfg: Dict[str, Any], feed: List[int], platform, decision: Dict[str, Any],
                  texts: Dict[str, Any], probes_def: List[Dict[str, Any]], max_actions: int, rng
                  ) -> Tuple[List[Dict[str, Any]], Optional[float], Dict[str, float]]:
    """Draw one member's actions from its archetype's decision; returns (actions, sentiment, probes)."""
    act = 0.5 + cfg["activity_level"]
    mine = platform.sentiment.get(aid, cfg["sentiment_bias"])
    actions: List[Dict[str, Any]] = []
    twitter = platform.name == "twitter"
    for pid in feed:
        if len(actions) >= max_actions:
            break
        post = platform.posts.get(pid)
        if post is None:
            continue
        r = decision["reactions"].get(pid, BASE_REACTION)
        theirs = platform.sentiment.get(post["author"]) if post["author"] is not None else None
        agree = 0.5 if theirs is None else 1 - abs(mine - theirs) / 2
        if rng.random() < r.get("comment", 0) * act:
            pool = texts["comments"].get(pid)
            if pool:
                text = pool.pop(0)
                actions.append({"type": "QUOTE_POST" if twitter else "CREATE_COMMENT", "post_id": pid, "content": text})
                continue
        if not twitter and rng.random() < r.get("dislike", 0) * act * (0.6 + 0.8 * (1 - agree)):
            actions.append({"type": "DISLIKE_POST", "post_id": pid})
            continue
        if rng.random() < r.get("like", 0) * act * (0.6 + 0.8 * agree):
            actions.append({"type": "LIKE_POST", "post_id": pid})
            if (post["author"] is not None and post["author"] not in platform.follows.get(aid, set())
                    and rng.random() < 0.04 * agree):
                actions.append({"type": "FOLLOW", "user_id": post["author"]})
        if twitter and rng.random() < r.get("repost", 0) * act * (0.6 + 0.8 * agree):
            actions.append({"type": "REPOST", "post_id": pid})
    if len(actions) < max_actions and texts["posts"] and rng.random() < decision["new_post_rate"] * act:
        actions.append({"type": "CREATE_POST", "content": texts["posts"].pop(0)})
    sentiment = None
    if decision["sentiment"] is not None:
        target = clamp(decision["sentiment"] + cfg.get("sentiment_offset", 0.0), -1.0, 1.0, 0.0)
        weight = cfg.get("susceptibility", 0.3) * (1.0 if feed else 0.5)
        sentiment = round(mine + weight * (target - mine), 3)
    probe_vals = {}
    for pr in probes_def:
        if pr["key"] in decision["probes"]:
            span = pr["max"] - pr["min"]
            v = decision["probes"][pr["key"]] + cfg.get("sentiment_offset", 0.0) * span / 4 + rng.gauss(0, span / 20)
            probe_vals[pr["key"]] = round(clamp(v, pr["min"], pr["max"], v), 1)
    return actions[:max_actions] or [{"type": "DO_NOTHING"}], sentiment, probe_vals


def arch_meta_from_config(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return config.get("crowd", {}).get("archetypes", {})


def describe(config: Dict[str, Any]) -> str:
    crowd = config.get("crowd") or {}
    return json.dumps({k: v["label"] for k, v in crowd.get("archetypes", {}).items()}, ensure_ascii=False)
