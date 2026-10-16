"""Deterministic offline responses for `--backend mock` (tests, dry runs, demos of the pipeline).

The content is obviously synthetic; it only exercises the mechanics.
"""

from __future__ import annotations

import json
import re

from .util import rng_for

FIRST = ["Alex", "Sam", "Lea", "Noah", "Ines", "Tom", "Mia", "Yuki", "Omar", "Ana", "Jon", "Zoe", "Ravi", "Eva"]
LAST = ["Martin", "Chen", "Dubois", "Okafor", "Silva", "Novak", "Kim", "Haddad", "Rossi", "Berg", "Lopez"]
MOODS = ["promising", "overpriced", "confusing", "exactly what I needed", "not for me", "worth a try", "risky"]


def mock_response(req) -> str:
    rng = rng_for("mock", req.key)
    m = req.meta
    kind = req.kind
    if kind == "crowd":
        people = []
        for i in range(m.get("count", 3)):
            people.append({
                "name": f"{rng.choice(FIRST)} {rng.choice(LAST)}", "age": rng.randint(19, 64),
                "gender": rng.choice(["female", "male"]), "country": rng.choice(["France", "USA", "Germany", "Brazil"]),
                "profession": rng.choice(["designer", "developer", "teacher", "nurse", "founder", "student"]),
                "angle": f"mock angle {i}",
            })
        return json.dumps({"people": people})
    if kind == "persona":
        group = m.get("kind") == "group"
        return json.dumps({
            "bio": f"Mock bio for {m.get('name')}",
            "persona": f"{m.get('name')} is a mock {m.get('type')} persona used for offline testing. "
                       "They react to the subject with a temperament drawn at random.",
            "age": 30 if group else rng.randint(20, 65),
            "gender": "other" if group else rng.choice(["male", "female"]),
            "mbti": rng.choice(["INTJ", "ENFP", "ISTJ", "ESTP"]), "country": "France",
            "profession": m.get("type"), "interested_topics": ["technology", "pricing", "privacy"],
        })
    if kind == "time_config":
        return json.dumps({"total_simulation_hours": 24, "minutes_per_round": 60, "start_hour": 8,
                           "agents_per_hour_min": 2, "agents_per_hour_max": max(3, m.get("n", 6) // 2),
                           "peak_hours": [19, 20, 21, 22], "off_peak_hours": [0, 1, 2, 3, 4, 5],
                           "timezone": "UTC+1", "reasoning": "mock"})
    if kind == "event_config":
        types = m.get("types") or ["Person"]
        posts = [{"content": f"Mock kick-off post {i} about the launch", "poster_type": rng.choice(types)}
                 for i in range(m.get("n_posts", 3))]
        return json.dumps({"hot_topics": ["launch", "pricing"], "narrative_direction": "mock narrative",
                           "initial_posts": posts, "reasoning": "mock"})
    if kind == "agent_config":
        cfgs = [{"agent_id": aid, "activity_level": round(rng.uniform(0.5, 1.0), 2), "posts_per_hour": 0.5,
                 "comments_per_hour": 1.0, "active_hours": list(range(7, 24)), "response_delay_min": 1,
                 "response_delay_max": 30, "sentiment_bias": round(rng.uniform(-0.5, 0.5), 2),
                 "stance": rng.choice(["supportive", "opposing", "neutral", "observer"]),
                 "influence_weight": round(rng.uniform(0.8, 2.5), 2)} for aid in m.get("agent_ids", [])]
        return json.dumps({"agent_configs": cfgs})
    if kind in ("turn", "followup"):
        allowed = m.get("actions", ["DO_NOTHING"])
        posts = [int(x) for x in re.findall(r"\[post (\d+)\]", req.user)]
        comments = [int(x) for x in re.findall(r"\[comment (\d+)\]", req.user)]
        users = [int(x) for x in re.findall(r"user_id (\d+)", req.user)] or list(range(0, 6))
        actions = []
        for _ in range(rng.randint(1, 2)):
            t = rng.choice(allowed)
            a = {"type": t}
            if t in ("LIKE_POST", "DISLIKE_POST", "REPOST", "QUOTE_POST", "CREATE_COMMENT"):
                if not posts:
                    continue
                a["post_id"] = rng.choice(posts)
            if t in ("LIKE_COMMENT", "DISLIKE_COMMENT"):
                if not comments:
                    continue
                a["comment_id"] = rng.choice(comments)
            if t in ("FOLLOW", "MUTE"):
                a["user_id"] = rng.choice(users)
            if t in ("CREATE_POST", "QUOTE_POST", "CREATE_COMMENT"):
                a["content"] = f"{m.get('name')}: {m.get('subject')} looks {rng.choice(MOODS)}"
            if t in ("SEARCH_POSTS", "SEARCH_USER"):
                a["query"] = "launch"
            actions.append(a)
        return json.dumps({
            "thought": f"mock thought of {m.get('name')}",
            "sentiment": round(rng.uniform(-1, 1), 2),
            "probes": {k: rng.randint(0, 10) for k in m.get("probes", [])},
            "actions": actions or [{"type": "DO_NOTHING"}],
        })
    if kind == "archetypes":
        n = m.get("n", 3)
        return json.dumps({"archetypes": [{
            "id": f"a{i + 1}", "label": f"{m.get('name')} type {i + 1}", "share": 1 / n,
            "description": f"mock archetype {i + 1}", "voice": "short and casual", "interests": ["pricing", "privacy"],
            "professions": ["designer", "nurse"], "countries": ["France", "Spain"], "age_min": 20, "age_max": 60,
            "female_share": 0.5, "sentiment_mean": round(rng.uniform(-0.6, 0.6), 2), "sentiment_spread": 0.3,
            "activity": round(rng.uniform(0.2, 0.8), 2), "susceptibility": 0.3,
            "stance": rng.choice(["supportive", "opposing", "neutral"])} for i in range(n)]})
    if kind == "crowd_turn":
        out = {}
        for arch in m.get("archetypes", []):
            posts = m.get("posts", [])
            out[arch] = {
                "thought": f"mock collective thought of {arch}",
                "sentiment": round(rng.uniform(-1, 1), 2),
                "probes": {k: rng.randint(0, 10) for k in m.get("probes", [])},
                "reactions": {str(pid): {k: round(rng.uniform(0, 0.3), 2) for k in m.get("keys", ["like"])}
                              for pid in posts},
                "comments": {str(pid): [f"mock {arch} reply {j} to {pid}" for j in range(2)] for pid in posts[:3]},
                "new_post_rate": 0.05,
                "posts": [f"mock {arch} post {j}: {m.get('subject')} looks {rng.choice(MOODS)}" for j in range(2)],
            }
        return json.dumps({"archetypes": out})
    if kind in ("assess", "assess_group"):
        dims, lo, hi = m.get("dims", []), m.get("lo", []), m.get("hi", [])
        base = rng.uniform(0.3, 0.8)
        scores = {k: round(a + (b - a) * min(1, max(0, base + rng.uniform(-0.15, 0.15))))
                  for k, a, b in zip(dims, lo, hi)}
        out = {"scores": scores, "verdict": f"(mock) {m.get('name')} finds it {rng.choice(MOODS)}",
               "likes": [rng.choice(["the price", "the idea", "the design"])],
               "dislikes": [rng.choice(["too long", "unclear value", "privacy worries"])],
               "would_do": "mock next step"}
        if kind == "assess_group":
            out["spread"] = {k: 2 for k in dims}
        return json.dumps(out)
    if kind == "judge":
        return json.dumps({"found": [rng.random() < 0.5 for _ in range(m.get("n", 1))]})
    if kind == "baseline":
        dims, lo, hi = m.get("dims", []), m.get("lo", []), m.get("hi", [])
        base = rng.uniform(0.3, 0.8)
        return json.dumps({"scores": {k: round(a + (b - a) * base) for k, a, b in zip(dims, lo, hi)},
                           "objections": [rng.choice(["too expensive", "unclear value", "privacy worries"])]})
    if kind == "interview":
        return f"(mock answer from {m.get('name')}) I found {m.get('subject')} {rng.choice(MOODS)}."
    return "{}"
