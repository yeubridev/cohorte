"""Stage 3 - the round-based simulation loop (MiroFish run_parallel_simulation.py, OASIS-style).

Each round = `minutes_per_round` simulated minutes. Per platform and round:
  1. scheduled events ("god's-eye" injections) due this round are published;
  2. agents are activated by time of day x activity level (MiroFish get_active_agents_for_round);
  3. every active agent sees its own feed / notifications / memory and decides 1-3 actions
     (all agents of a round act on the same snapshot, like OASIS' env.step);
  4. agents who searched get the results and one follow-up decision;
  5. actions are applied in agent-id order, memories and notifications updated, state saved.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from . import prompts as P
from .llm import LLMClient, LLMRequest
from .platform import INFO_ACTIONS, PLATFORM_ACTIONS, Platform
from .util import (
    CohorteError, append_jsonl, clamp, coerce_str, fmt_clock, keywords, parse_json_response, read_json,
    read_jsonl, rng_for, truncate, write_json,
)
from .world import Sim, now_iso

ACTION_ALIASES = {
    "POST": "CREATE_POST", "TWEET": "CREATE_POST", "LIKE": "LIKE_POST", "UPVOTE": "LIKE_POST",
    "DOWNVOTE": "DISLIKE_POST", "DISLIKE": "DISLIKE_POST", "RETWEET": "REPOST", "SHARE": "REPOST",
    "QUOTE": "QUOTE_POST", "COMMENT": "CREATE_COMMENT", "REPLY": "CREATE_COMMENT", "NOTHING": "DO_NOTHING",
    "SEARCH": "SEARCH_POSTS", "NONE": "DO_NOTHING", "IDLE": "DO_NOTHING",
}


# --------------------------------------------------------------------------- #
# Clock & activation
# --------------------------------------------------------------------------- #

def clock_for(tc: Dict[str, Any], round_no: int) -> Tuple[int, int, int]:
    minutes = tc.get("start_hour", 8) * 60 + round_no * tc["minutes_per_round"]
    day = minutes // 1440 + 1
    hour = (minutes // 60) % 24
    return day, hour, minutes % 60


def activity_multiplier(tc: Dict[str, Any], hour: int) -> float:
    if hour in tc.get("peak_hours", []):
        return tc.get("peak_activity_multiplier", 1.5)
    if hour in tc.get("off_peak_hours", []):
        return tc.get("off_peak_activity_multiplier", 0.05)
    if hour in tc.get("morning_hours", []):
        return tc.get("morning_activity_multiplier", 0.4)
    if hour in tc.get("work_hours", []):
        return tc.get("work_activity_multiplier", 0.7)
    return 1.0


def select_active(config: Dict[str, Any], hour: int, rng) -> List[int]:
    tc = config["time_config"]
    target = int(rng.uniform(tc["agents_per_hour_min"], tc["agents_per_hour_max"]) * activity_multiplier(tc, hour))
    candidates = [
        c["agent_id"] for c in config["agent_configs"]
        if hour in c["active_hours"] and rng.random() < c["activity_level"]
    ]
    return sorted(rng.sample(candidates, min(target, len(candidates)))) if candidates else []


def round_for_event(tc: Dict[str, Any], ev: Dict[str, Any]) -> int:
    if ev.get("at_round") is not None:
        return int(ev["at_round"])
    return int(float(ev.get("at_hour") or 0) * 60 // tc["minutes_per_round"])


# --------------------------------------------------------------------------- #
# Prompt building
# --------------------------------------------------------------------------- #

def _sentiment_label(bias: float) -> str:
    if bias > 0.4:
        return "positive / enthusiastic"
    if bias > 0.1:
        return "slightly positive"
    if bias < -0.4:
        return "negative / critical"
    if bias < -0.1:
        return "slightly negative"
    return "neutral"


def _hours_label(hours: List[int]) -> str:
    if not hours:
        return "rarely"
    spans, start, prev = [], hours[0], hours[0]
    for h in hours[1:] + [None]:
        if h is not None and h == prev + 1:
            prev = h
            continue
        spans.append(f"{start:02d}-{prev + 1:02d}h" if prev != start else f"{start:02d}h")
        if h is not None:
            start = prev = h
    return ", ".join(spans)


class TurnBuilder:
    def __init__(self, config: Dict[str, Any], profiles: List[Dict[str, Any]]):
        self.config = config
        self.profiles = {p["agent_id"]: p for p in profiles}
        self.cfgs = {c["agent_id"]: c for c in config["agent_configs"]}
        self.turn = config["turn"]
        self.probes = config.get("probes", [])
        self.rules: Dict[str, str] = {}
        for plat in config["platforms"]:
            actions = PLATFORM_ACTIONS[plat]
            self.rules[plat] = P.TURN_RULES.format(
                platform_desc=P.PLATFORM_DESCRIPTIONS[plat],
                language_rule=P.language_rule(config["language"]),
                subject=config["subject"],
                probe_schema=P.probe_schema(self.probes),
                max_actions=self.turn["max_actions"],
                action_docs="\n".join(f"- {P.ACTION_DOCS[a]}" for a in actions),
            )
        hot = " ".join(config["event_config"].get("hot_topics", []))
        self.topic_words = keywords(f"{config['subject']} {hot}")

    def persona_block(self, aid: int, platform: Platform) -> str:
        p, c = self.profiles[aid], self.cfgs[aid]
        identity = ", ".join(x for x in [
            f"{p['age']} y/o" if p["kind"] == "individual" else "institutional account",
            p["gender"] if p["kind"] == "individual" else "",
            p.get("profession", ""), p.get("country", ""), f"MBTI {p['mbti']}" if p.get("mbti") else "",
        ] if x)
        return P.PERSONA_BLOCK.format(
            name=p["name"], username=p["username"], user_id=aid, identity=identity, bio=p["bio"],
            persona=p["persona"], active_hours=_hours_label(c["active_hours"]),
            posts_per_hour=round(c["posts_per_hour"], 2), comments_per_hour=round(c["comments_per_hour"], 2),
            stance=c["stance"], sentiment_label=_sentiment_label(c["sentiment_bias"]),
            followers=platform.followers_of(aid),
        )

    def interest(self, aid: int) -> set:
        p = self.profiles[aid]
        return self.topic_words | keywords(" ".join(p.get("interested_topics", [])) + " " + p.get("profession", ""))

    def observation(self, aid: int, platform: Platform, round_no: int, rng) -> Tuple[str, List[int]]:
        tc = self.config["time_config"]
        day, hour, minute = clock_for(tc, round_no)
        size = self.turn["feed_size"].get(platform.name, 8) if isinstance(self.turn["feed_size"], dict) \
            else int(self.turn["feed_size"])
        feed_ids = platform.feed(aid, round_no, size, self.interest(aid), rng)
        with_comments = 3 if platform.name == "reddit" else 0
        feed = "\n\n".join(
            platform.render_post(pid, round_no, tc["minutes_per_round"], aid, with_comments) for pid in feed_ids
        ) or "(nothing new in your feed)"
        memory = platform.memory.get(aid, [])[-self.turn["memory_size"]:]
        notes = platform.notifications.get(aid, [])
        user = P.TURN_USER.format(
            clock=fmt_clock(day, hour, minute), platform_name=platform.name,
            memory="\n".join(f"- {m}" for m in memory) or "- (you have not been active here yet)",
            notifications="\n".join(f"- {n}" for n in notes) or "- (none)",
            trending="", feed=feed,
        )
        return user, feed_ids

    def request(self, aid: int, platform: Platform, round_no: int, user: str, suffix: str = "") -> LLMRequest:
        return LLMRequest(
            id=f"turn:{platform.name}:r{round_no}:a{aid}{suffix}", kind="followup" if suffix else "turn",
            system=[self.rules[platform.name], self.persona_block(aid, platform)], user=user,
            effort=self.turn.get("effort", "low"), max_tokens=self.turn.get("max_tokens", 4000),
            meta={"name": self.profiles[aid]["name"], "platform": platform.name,
                  "actions": PLATFORM_ACTIONS[platform.name] if not suffix
                  else [a for a in PLATFORM_ACTIONS[platform.name] if a not in INFO_ACTIONS],
                  "probes": [p["key"] for p in self.probes], "subject": self.config["subject"],
                  "language": self.config["language"]},
        )


# --------------------------------------------------------------------------- #
# Decision parsing
# --------------------------------------------------------------------------- #

def parse_decision(text: Optional[str], allowed: List[str], probes: List[Dict[str, Any]], max_actions: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"thought": "", "sentiment": None, "probes": {}, "actions": [{"type": "DO_NOTHING"}],
                           "parse_error": None}
    if text is None:
        out["parse_error"] = "no response"
        return out
    try:
        data = parse_json_response(text)
    except ValueError as e:
        out["parse_error"] = str(e)
        return out
    if isinstance(data, list):
        data = {"actions": data}
    if not isinstance(data, dict):
        out["parse_error"] = "unexpected JSON"
        return out
    out["thought"] = truncate(coerce_str(data.get("thought")), 600)
    if data.get("sentiment") is not None:
        out["sentiment"] = round(clamp(data.get("sentiment"), -1.0, 1.0, 0.0), 3)
    raw_probes = data.get("probes") if isinstance(data.get("probes"), dict) else {}
    for p in probes:
        v = raw_probes.get(p["key"], data.get(p["key"]))
        if v is not None:
            out["probes"][p["key"]] = clamp(v, p["min"], p["max"], (p["min"] + p["max"]) / 2)
    actions = data.get("actions") or ([data["action"]] if data.get("action") else [])
    if isinstance(actions, dict):
        actions = [actions]
    out["actions"] = []
    for a in actions if isinstance(actions, list) else []:
        if isinstance(a, str):
            a = {"type": a}
        if not isinstance(a, dict):
            continue
        t = coerce_str(a.get("type") or a.get("action") or a.get("name")).upper().replace(" ", "_")
        t = ACTION_ALIASES.get(t, t)
        if t not in allowed:
            continue
        flat = {"type": t}
        for nested in ("args", "arguments", "params", "parameters"):
            if isinstance(a.get(nested), dict):
                flat.update(a[nested])
        flat.update({k: v for k, v in a.items() if k not in ("type", "action", "name", "args", "arguments",
                                                              "params", "parameters")})
        out["actions"].append(flat)
        if len(out["actions"]) >= max_actions:
            break
    if not out["actions"]:
        out["actions"] = [{"type": "DO_NOTHING"}]
    return out


def info_results(platform: Platform, action: Dict[str, Any], aid: int, round_no: int, tc, rng) -> Tuple[str, str]:
    t = action["type"]
    if t == "SEARCH_POSTS":
        q = str(action.get("query") or action.get("keyword") or "")
        ids = platform.search_posts(q)
        title = f"## Search results for posts matching \"{q}\""
    elif t == "SEARCH_USER":
        q = str(action.get("query") or action.get("username") or "")
        users = platform.search_users(q)
        lines = [
            f"- user_id {u} {platform.handle(u)} ({platform.profiles[u]['name']}, "
            f"{platform.profiles[u]['entity_type']}): {truncate(platform.profiles[u].get('bio', ''), 120)}"
            for u in users
        ]
        return f"## Users matching \"{q}\"", "\n".join(lines) or "(no users found)"
    elif t == "TREND":
        ids = platform.trending(round_no)
        title = "## Trending posts"
    else:  # REFRESH
        ids = platform.feed(aid, round_no, 6, set(), rng)
        title = "## Your refreshed feed"
    body = "\n\n".join(platform.render_post(i, round_no, tc["minutes_per_round"], aid, 2) for i in ids)
    platform.mark_seen(aid, ids)
    return title, body or "(nothing found)"


# --------------------------------------------------------------------------- #
# Run loop
# --------------------------------------------------------------------------- #

def load_state(sim: Sim, config: Dict[str, Any], profiles: List[Dict[str, Any]]) -> Dict[str, Any]:
    raw = read_json(sim.path("state.json"), default={"round": 0, "initialized": False, "platforms": {},
                                                      "injected": []})
    enriched = _profiles_with_config(config, profiles)
    raw["objects"] = {
        plat: Platform(plat, enriched, config["platform_configs"].get(plat, {}), raw["platforms"].get(plat))
        for plat in config["platforms"]
    }
    return raw


def save_state(sim: Sim, state: Dict[str, Any]) -> None:
    data = {k: v for k, v in state.items() if k != "objects"}
    data["platforms"] = {name: plat.to_dict() for name, plat in state["objects"].items()}
    data["updated_at"] = now_iso()
    write_json(sim.path("state.json"), data)


def _profiles_with_config(config, profiles):
    cfgs = {c["agent_id"]: c for c in config["agent_configs"]}
    return [{**p, "influence_weight": cfgs.get(p["agent_id"], {}).get("influence_weight", 1.0)} for p in profiles]


def _truncate_turns(sim: Sim, upto_round: int) -> None:
    """Drop turn records from rounds that were not committed (crash between append and save)."""
    path = sim.path("turns.jsonl")
    if not os.path.exists(path):
        return
    records = list(read_jsonl(path))
    keep = [r for r in records if r.get("round", -1) < upto_round]
    if len(keep) != len(records):
        os.replace(path, path + ".bak")
        append_jsonl(path, keep)


def _seed_record(platform: Platform, pid: int, round_no: int, clock: Tuple[int, int, int], source: str,
                 author: Optional[int], profiles: Dict[int, dict], label: str = "") -> Dict[str, Any]:
    prof = profiles.get(author, {}) if author is not None else {}
    return {
        "round": round_no, "day": clock[0], "hour": clock[1], "platform": platform.name, "source": source,
        "agent_id": author, "agent_name": prof.get("name", "NEWS"), "username": prof.get("username"),
        "entity_type": prof.get("entity_type", "News"), "thought": label, "sentiment": None, "probes": {},
        "actions": [{"type": "CREATE_POST", "args": {"content": platform.posts[pid]["content"]}, "ok": True,
                     "context": {"post_id": pid}}],
    }


def run_simulation(sim: Sim, llm: LLMClient, max_rounds: Optional[int] = None,
                   until_round: Optional[int] = None) -> Dict[str, Any]:
    sim.require("agents.json", "config.json")
    config, profiles = sim.config(), sim.agents()
    prof_by_id = {p["agent_id"]: p for p in profiles}
    tc = config["time_config"]
    total = tc["total_rounds"]
    state = load_state(sim, config, profiles)
    start = state["round"]
    if start >= total:
        print(f"[run] simulation already complete ({total}/{total} rounds).")
        return state
    end = total
    if until_round is not None:
        end = min(end, max(start, until_round))
    if max_rounds is not None:
        end = min(end, start + max_rounds)
    _truncate_turns(sim, start)
    builder = TurnBuilder(config, profiles)
    turn_cfg = config["turn"]
    seed = config["seed"]
    meta = sim.meta()
    meta["status"] = "running"
    sim.save_meta(meta)

    for r in range(start, end):
        clk = clock_for(tc, r)
        clock_txt = fmt_clock(*clk)
        records: List[Dict[str, Any]] = []
        plats: Dict[str, Platform] = state["objects"]

        # 1. kick-off posts (round 0) and scheduled god's-eye events
        if not state["initialized"]:
            for post in config["event_config"].get("initial_posts", []):
                for name in post.get("platforms") or config["platforms"]:
                    if name in plats:
                        pid = plats[name].create_post(post.get("poster_agent_id"), post["content"], r, clock_txt)
                        records.append(_seed_record(plats[name], pid, r, clk, "initial", post.get("poster_agent_id"),
                                                    prof_by_id))
                        if post.get("poster_agent_id") is not None:
                            plats[name]._add_memory(post["poster_agent_id"], f"{clock_txt} You posted [post {pid}]: "
                                                    f"\"{truncate(post['content'], 220)}\"")
            state["initialized"] = True
        for idx, ev in enumerate(config["event_config"].get("scheduled_events", [])):
            key = f"event:{idx}"
            if key in state["injected"] or round_for_event(tc, ev) > r:
                continue
            for name in ev.get("platforms") or config["platforms"]:
                if name in plats:
                    pid = plats[name].create_post(ev.get("poster_agent_id"), ev["content"], r, clock_txt,
                                                  kind="post" if ev.get("poster_agent_id") is not None else "news")
                    records.append(_seed_record(plats[name], pid, r, clk, "event", ev.get("poster_agent_id"),
                                                prof_by_id, ev.get("label", "")))
            state["injected"].append(key)

        # 2-3. activation + first decision
        pending: List[Tuple[str, int, LLMRequest, str, List[int]]] = []
        for name, plat in plats.items():
            active = select_active(config, clk[1], rng_for(seed, name, r, "active"))
            for aid in active:
                user, feed_ids = builder.observation(aid, plat, r, rng_for(seed, name, r, aid, "feed"))
                pending.append((name, aid, builder.request(aid, plat, r, user), user, feed_ids))
        responses = llm.run_batch([p[2] for p in pending], label=f"round {r + 1}")

        decisions: Dict[Tuple[str, int], Dict[str, Any]] = {}
        followups: List[Tuple[str, int, LLMRequest]] = []
        for name, aid, req, user, feed_ids in pending:
            d = parse_decision(responses.get(req.id), PLATFORM_ACTIONS[name], config.get("probes", []),
                               turn_cfg["max_actions"])
            decisions[(name, aid)] = d
            plats[name].mark_seen(aid, feed_ids)
            info = [a for a in d["actions"] if a["type"] in INFO_ACTIONS]
            if info:
                title, body = info_results(plats[name], info[0], aid, r, tc, rng_for(seed, name, r, aid, "info"))
                d["info"] = {"type": info[0]["type"], "query": info[0].get("query", "")}
                probe_keys = ', "probes"' if config.get("probes") else ""
                fu_user = user + f"\n\nYou chose {info[0]['type']}.\n\n" + P.FOLLOWUP_USER.format(
                    result_title=title, results=body, probe_keys=probe_keys)
                followups.append((name, aid, builder.request(aid, plats[name], r, fu_user, suffix=":followup")))
        if followups:
            fu_resp = llm.run_batch([f[2] for f in followups], label=f"round {r + 1} follow-ups")
            for name, aid, req in followups:
                allowed = [a for a in PLATFORM_ACTIONS[name] if a not in INFO_ACTIONS]
                fd = parse_decision(fu_resp.get(req.id), allowed, config.get("probes", []), turn_cfg["max_actions"])
                d = decisions[(name, aid)]
                d["actions"] = [a for a in d["actions"] if a["type"] not in INFO_ACTIONS] + fd["actions"]
                d["actions"] = d["actions"][: turn_cfg["max_actions"] + 1]
                if fd["thought"]:
                    d["thought"] = (d["thought"] + " " + fd["thought"]).strip()
                if fd["sentiment"] is not None:
                    d["sentiment"] = fd["sentiment"]
                d["probes"].update(fd["probes"])

        # 5. apply in deterministic order
        summary: Dict[str, List[int]] = {name: [0, 0] for name in plats}
        # Clear what agents just read first, so notifications created this round reach them next time.
        for (name, aid) in decisions:
            plats[name].consume_notifications(aid)
        for (name, aid) in sorted(decisions, key=lambda k: (k[0], k[1])):
            d, plat = decisions[(name, aid)], plats[name]
            applied = []
            for action in d["actions"]:
                if action["type"] in INFO_ACTIONS:
                    continue
                applied.append(plat.apply(aid, action, r, clock_txt))
            if d.get("info"):
                applied.insert(0, {"type": d["info"]["type"], "args": {"query": d["info"]["query"]}, "ok": True,
                                   "context": {}})
            if d["sentiment"] is not None:
                plat.sentiment[aid] = d["sentiment"]
            if d["thought"]:
                plat._add_memory(aid, f"{clock_txt} (you thought: {truncate(d['thought'], 160)})")
            prof = prof_by_id[aid]
            records.append({
                "round": r, "day": clk[0], "hour": clk[1], "platform": name, "source": "agent",
                "agent_id": aid, "agent_name": prof["name"], "username": prof["username"],
                "entity_type": prof["entity_type"], "thought": d["thought"], "sentiment": d["sentiment"],
                "probes": d["probes"], "actions": applied, "parse_error": d.get("parse_error"),
            })
            summary[name][0] += 1
            summary[name][1] += sum(1 for a in applied if a["ok"] and a["type"] != "DO_NOTHING")

        state["round"] = r + 1
        save_state(sim, state)
        append_jsonl(sim.path("turns.jsonl"), records)
        parts = " · ".join(f"{n}: {s[0]} online, {s[1]} actions" for n, s in summary.items())
        print(f"[run] round {r + 1}/{total} · {clock_txt} · {parts} · cost so far ${llm.usage['cost_usd']:.2f}",
              flush=True)

    meta = sim.meta()
    meta["status"] = "completed" if state["round"] >= total else "paused"
    sim.save_meta(meta)
    return state


def inject_event(sim: Sim, content: str, at_hour: Optional[float], at_round: Optional[int],
                 poster: Optional[str], platforms: Optional[List[str]], label: str = "") -> Dict[str, Any]:
    """Add a god's-eye event to config.json (takes effect at its round, even mid-run)."""
    sim.require("agents.json", "config.json")
    config, profiles = sim.config(), sim.agents()
    poster_id = None
    if poster:
        from .analysis import resolve_agent
        poster_id = resolve_agent(profiles, poster)["agent_id"]
    state = read_json(sim.path("state.json"), default={"round": 0})
    if at_round is None and at_hour is None:
        at_round = state["round"]
    ev = {"at_hour": at_hour, "at_round": at_round, "content": content, "poster_agent_id": poster_id,
          "platforms": platforms or config["platforms"], "label": label}
    r = round_for_event(config["time_config"], ev)
    if r < state["round"]:
        raise CohorteError(f"Round {r} has already been simulated (next round is {state['round']}).")
    config["event_config"].setdefault("scheduled_events", []).append(ev)
    write_json(sim.path("config.json"), config)
    return {**ev, "round": r}
