"""Stage 4/5 tools: what Claude (as MiroFish's ReportAgent) uses to observe the simulated world.

MiroFish ReportAgent tools  ->  cohorte commands
  insight_forge (deep, multi-angle)  ->  Claude decomposes the question and combines the tools below
  panorama_search (whole evolution)  ->  timeline, stats
  quick_search (verify a fact)       ->  search, thread, agent
  interview_agents (first person)    ->  interview
"""

from __future__ import annotations

import math
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import prompts as P
from .llm import LLMClient, LLMRequest, price_for, resolve_model
from .platform import Platform
from .simulation import TurnBuilder, clock_for, load_state, select_active
from .util import CohorteError, append_jsonl, fmt_clock, keywords, read_json, read_jsonl, rng_for, truncate
from .world import Sim

ENGAGING = ("CREATE_POST", "CREATE_COMMENT", "QUOTE_POST", "REPOST", "LIKE_POST", "DISLIKE_POST",
            "LIKE_COMMENT", "DISLIKE_COMMENT", "FOLLOW", "MUTE")


# --------------------------------------------------------------------------- #
# Loading helpers
# --------------------------------------------------------------------------- #

class Run:
    def __init__(self, sim: Sim):
        sim.require("agents.json", "config.json")
        self.sim = sim
        self.config = sim.config()
        self.profiles = sim.agents()
        self.by_id = {p["agent_id"]: p for p in self.profiles}
        self.state = load_state(sim, self.config, self.profiles)
        self.plats: Dict[str, Platform] = self.state["objects"]
        self.turns = list(sim.turns())
        self.tc = self.config["time_config"]

    def agent_turns(self, platform: Optional[str] = None) -> List[dict]:
        return [t for t in self.turns if t["source"] == "agent" and (not platform or t["platform"] == platform)]

    def clock(self, round_no: int) -> str:
        return fmt_clock(*clock_for(self.tc, round_no))

    def handle(self, platform: str, aid: Optional[int]) -> str:
        return self.plats[platform].handle(aid) if platform in self.plats else str(aid)


def resolve_agent(profiles: List[Dict[str, Any]], ref: Any) -> Dict[str, Any]:
    ref_s = str(ref).strip().lstrip("@").replace("u/", "", 1)
    if ref_s.isdigit():
        for p in profiles:
            if p["agent_id"] == int(ref_s):
                return p
    low = ref_s.lower()
    for p in profiles:
        if p["username"].lower() == low or p["name"].lower() == low:
            return p
    matches = [p for p in profiles if low in p["name"].lower() or low in p["username"].lower()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        raise CohorteError(f"'{ref}' is ambiguous: " + ", ".join(f"{p['agent_id']}={p['name']}" for p in matches[:8]))
    raise CohorteError(f"No agent matches '{ref}'.")


def _mean(values: Iterable[float]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def _fmt(v: Optional[float], signed: bool = True) -> str:
    if v is None:
        return "–"
    return f"{v:+.2f}" if signed else f"{v:.2f}"


def _table(headers: List[str], rows: List[List[Any]]) -> str:
    if not rows:
        return "_(none)_"
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "/").replace("\n", " ") for c in r) + " |")
    return "\n".join(out)


def _latest_by_agent(turns: List[dict], field: str = "sentiment") -> Dict[int, Any]:
    latest: Dict[int, Any] = {}
    for t in turns:
        if t.get(field) not in (None, {}):
            latest[t["agent_id"]] = t[field]
    return latest


def _first_by_agent(turns: List[dict], field: str = "sentiment") -> Dict[int, Any]:
    first: Dict[int, Any] = {}
    for t in turns:
        if t.get(field) not in (None, {}) and t["agent_id"] not in first:
            first[t["agent_id"]] = t[field]
    return first


# --------------------------------------------------------------------------- #
# metrics (shared by stats and compare)
# --------------------------------------------------------------------------- #

def metrics(run: Run) -> Dict[str, Any]:
    turns = run.agent_turns()
    actions = Counter()
    for t in turns:
        for a in t["actions"]:
            if a.get("ok", True):
                actions[a["type"]] += 1
    latest = _latest_by_agent(turns)
    sentiments = list(latest.values())
    probes_latest: Dict[str, List[float]] = defaultdict(list)
    for d in _latest_by_agent(turns, "probes").values():
        for k, v in d.items():
            probes_latest[k].append(v)
    by_type: Dict[str, List[float]] = defaultdict(list)
    for aid, s in latest.items():
        by_type[run.by_id[aid]["entity_type"]].append(s)
    posts = sum(p.stats_counts()[0] for p in run.plats.values())
    comments = sum(p.stats_counts()[1] for p in run.plats.values())
    return {
        "rounds_done": run.state["round"],
        "rounds_total": run.tc["total_rounds"],
        "agents": len(run.profiles),
        "agent_turns": len(turns),
        "idle_share": round(sum(1 for t in turns if all(a["type"] == "DO_NOTHING" for a in t["actions"]))
                            / len(turns), 3) if turns else None,
        "posts": posts,
        "comments": comments,
        "actions": dict(actions),
        "sentiment_latest_mean": _mean(sentiments),
        "sentiment_first_mean": _mean(_first_by_agent(turns).values()),
        "polarization_std": round(statistics.pstdev(sentiments), 3) if len(sentiments) > 1 else None,
        "share_positive": round(sum(1 for s in sentiments if s > 0.2) / len(sentiments), 3) if sentiments else None,
        "share_negative": round(sum(1 for s in sentiments if s < -0.2) / len(sentiments), 3) if sentiments else None,
        "probes_latest_mean": {k: _mean(v) for k, v in probes_latest.items()},
        "sentiment_by_type": {k: _mean(v) for k, v in sorted(by_type.items())},
        "parse_errors": sum(1 for t in turns if t.get("parse_error")),
    }


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def status(sim: Sim) -> str:
    sim.require()
    meta = sim.meta()
    lines = [f"# {meta['name']} — {meta.get('title', '')}", f"status: {meta.get('status')} · created {meta.get('created_at')}"]
    if meta.get("parent"):
        lines.append(f"forked from: {meta['parent']}")
    llm = meta.get("llm", {})
    lines.append(f"llm: backend={llm.get('backend')} model={llm.get('model')} concurrency={llm.get('concurrency')}")
    if os.path.exists(sim.path("config.json")):
        cfg = sim.config()
        st = read_json(sim.path("state.json"), default={"round": 0})
        tc = cfg["time_config"]
        lines.append(f"progress: round {st['round']}/{tc['total_rounds']} "
                     f"({tc['total_simulation_hours']}h simulated, {tc['minutes_per_round']} min/round) "
                     f"· now {fmt_clock(*clock_for(tc, st['round']))}")
        lines.append(f"agents: {len(sim.agents())} · platforms: {', '.join(cfg['platforms'])}")
    usage = read_json(sim.path("usage.json"), default={})
    if usage:
        total_in = usage.get("input_tokens", 0) + usage.get("cache_read_tokens", 0) + usage.get("cache_write_tokens", 0)
        lines.append(f"llm usage: {usage.get('calls', 0)} calls ({usage.get('failed', 0)} failed) · "
                     f"{total_in:,} in ({usage.get('cache_read_tokens', 0):,} cache reads) / "
                     f"{usage.get('output_tokens', 0):,} out tokens · est. ${usage.get('cost_usd', 0):.2f}")
    if os.path.exists(sim.path("pending_requests.json")):
        pend = read_json(sim.path("pending_requests.json"))
        lines.append(f"manual backend: {len(pend['requests'])} pending request(s) in pending_requests.json")
    for f in ("world.json", "seed.md", "agents.json", "config.json", "state.json", "turns.jsonl", "report.md"):
        lines.append(f"  [{'x' if os.path.exists(sim.path(f)) else ' '}] {f}")
    return "\n".join(lines)


def show(sim: Sim, full: bool = False) -> str:
    sim.require("agents.json", "config.json")
    cfg, profiles = sim.config(), sim.agents()
    cfgs = {c["agent_id"]: c for c in cfg["agent_configs"]}
    tc, ev = cfg["time_config"], cfg["event_config"]
    out = [f"# {cfg['title']}", f"**Prediction question:** {cfg['requirement']}",
           f"**Subject tracked:** {cfg['subject']} · **language:** {cfg['language']} · "
           f"**platforms:** {', '.join(cfg['platforms'])}", ""]
    out.append(f"## Time\n{tc['total_simulation_hours']}h simulated · {tc['minutes_per_round']} min/round · "
               f"{tc['total_rounds']} rounds · starts {tc['start_hour']:02d}:00 · {tc['agents_per_hour_min']}-"
               f"{tc['agents_per_hour_max']} agents online per round (before time-of-day multipliers) · "
               f"peak {tc['peak_hours']} · timezone {tc.get('timezone')}\n")
    rows = []
    for p in profiles:
        c = cfgs[p["agent_id"]]
        rows.append([p["agent_id"], p["name"], "@" + p["username"], p["entity_type"], c["stance"],
                     f"{c['sentiment_bias']:+.1f}", c["activity_level"], c["influence_weight"],
                     truncate(p["bio"], 70 if not full else 400)])
    out.append("## Agents\n" + _table(["id", "name", "handle", "type", "stance", "bias", "activity", "influence", "bio"], rows))
    out.append("\n## Kick-off posts")
    for post in ev.get("initial_posts", []):
        who = profiles[post["poster_agent_id"]]["name"] if post.get("poster_agent_id") is not None else "NEWS"
        out.append(f"- ({post.get('source')}) **{who}** on {', '.join(post.get('platforms', []))}: "
                   f"\"{truncate(post['content'], 300)}\"")
    if ev.get("scheduled_events"):
        out.append("\n## Scheduled events (god's-eye injections)")
        for e in ev["scheduled_events"]:
            who = profiles[e["poster_agent_id"]]["name"] if e.get("poster_agent_id") is not None else "NEWS"
            when = f"round {e['at_round']}" if e.get("at_round") is not None else f"hour {e['at_hour']}"
            out.append(f"- {when} · {who}: \"{truncate(e['content'], 200)}\"")
    if ev.get("hot_topics"):
        out.append(f"\n**Hot topics:** {', '.join(ev['hot_topics'])}")
    if ev.get("narrative_direction"):
        out.append(f"**Expected narrative:** {ev['narrative_direction']}")
    if cfg.get("probes"):
        out.append("\n## Probes (private questions every agent answers each turn)")
        for p in cfg["probes"]:
            out.append(f"- `{p['key']}` ({p['min']}-{p['max']}): {p['question']}")
    if full:
        out.append("\n## Personas")
        for p in profiles:
            out.append(f"\n### {p['agent_id']}. {p['name']} (@{p['username']}, {p['entity_type']})\n{p['persona']}")
    return "\n".join(out)


def stats(sim: Sim, platform: Optional[str] = None) -> str:
    run = Run(sim)
    m = metrics(run)
    turns = run.agent_turns(platform)
    subject = run.config["subject"]
    out = [f"# Stats — {run.config['title']}",
           f"rounds {m['rounds_done']}/{m['rounds_total']} · agents {m['agents']} · agent turns {len(turns)} · "
           f"idle share {_fmt(m['idle_share'], False)} · parse errors {m['parse_errors']}", ""]

    rows = []
    for name, plat in run.plats.items():
        if platform and name != platform:
            continue
        c = Counter(a["type"] for t in run.agent_turns(name) for a in t["actions"] if a.get("ok", True))
        posts, comments = plat.stats_counts()
        rows.append([name, posts, comments, c["LIKE_POST"] + c["LIKE_COMMENT"], c["DISLIKE_POST"] + c["DISLIKE_COMMENT"],
                     c["REPOST"], c["QUOTE_POST"], c["FOLLOW"], c["SEARCH_POSTS"] + c["SEARCH_USER"] + c["TREND"],
                     c["DO_NOTHING"]])
    out.append("## Activity\n" + _table(["platform", "posts", "comments", "likes", "dislikes", "reposts", "quotes",
                                         "follows", "searches", "idle"], rows))

    first, latest = _first_by_agent(turns), _latest_by_agent(turns)
    out.append(f"\n## Sentiment toward {subject} (agents' private self-report, -1..+1)")
    out.append(f"mean: first report {_fmt(_mean(first.values()))} → latest {_fmt(_mean(latest.values()))} · "
               f"positive {_fmt(m['share_positive'], False)} · negative {_fmt(m['share_negative'], False)} · "
               f"polarization (std) {_fmt(m['polarization_std'], False)}")
    by_type: Dict[str, List[int]] = defaultdict(list)
    for aid in latest:
        by_type[run.by_id[aid]["entity_type"]].append(aid)
    rows = []
    for etype, ids in sorted(by_type.items()):
        f_, l_ = _mean(first[i] for i in ids), _mean(latest[i] for i in ids)
        rows.append([etype, len(ids), _fmt(f_), _fmt(l_), _fmt(None if f_ is None or l_ is None else l_ - f_)])
    out.append(_table(["entity type", "agents", "first", "latest", "Δ"], rows))

    if run.config.get("probes"):
        out.append("\n## Probes (latest answer per agent)")
        pf, pl = _first_by_agent(turns, "probes"), _latest_by_agent(turns, "probes")
        rows = []
        for p in run.config["probes"]:
            k = p["key"]
            per_type = defaultdict(list)
            for aid, d in pl.items():
                if k in d:
                    per_type[run.by_id[aid]["entity_type"]].append(d[k])
            rows.append([f"`{k}`", "ALL", _fmt(_mean(d.get(k) for d in pf.values()), False),
                         _fmt(_mean(d.get(k) for d in pl.values()), False)])
            for etype, vals in sorted(per_type.items()):
                rows.append(["", etype, "", _fmt(_mean(vals), False)])
        out.append(_table(["probe", "segment", "first mean", "latest mean"], rows))

    # trajectory by 6h bucket
    bucket_rounds = max(1, int(6 * 60 / run.tc["minutes_per_round"]))
    traj = defaultdict(list)
    for t in turns:
        if t.get("sentiment") is not None:
            traj[t["round"] // bucket_rounds].append(t["sentiment"])
    rows = [[run.clock(b * bucket_rounds), len(v), _fmt(_mean(v))] for b, v in sorted(traj.items())]
    out.append("\n## Sentiment trajectory (6h buckets)\n" + _table(["from", "reports", "mean"], rows))

    rows = []
    for name, plat in run.plats.items():
        if platform and name != platform:
            continue
        for p in plat.posts.values():
            if p["kind"] != "repost":
                rows.append((plat.engagement(p), name, p))
    rows.sort(key=lambda x: -x[0])
    out.append("\n## Top posts by engagement\n" + _table(
        ["post", "author", "type", "♥/▲", "▼", "↻", "❝", "💬", "text"],
        [[f"{n} #{p['id']}", run.handle(n, p["author"]),
          run.by_id[p["author"]]["entity_type"] if p["author"] is not None else "News",
          len(p["likes"]), len(p["dislikes"]), p["shares"], p["quotes"], len(p["comments"]),
          truncate(p["content"], 110)] for _, n, p in rows[:10]]))

    received: Counter = Counter()
    for name, plat in run.plats.items():
        for p in plat.posts.values():
            if p["author"] is not None and p["kind"] != "repost":
                received[p["author"]] += plat.engagement(p)
    active = Counter(t["agent_id"] for t in turns
                     if any(a["type"] != "DO_NOTHING" and a.get("ok", True) for a in t["actions"]))
    out.append("\n## Most influential (engagement received)\n" + _table(
        ["agent", "type", "engagement", "active turns"],
        [[f"{run.by_id[a]['name']} (#{a})", run.by_id[a]["entity_type"], round(e, 1), active.get(a, 0)]
         for a, e in received.most_common(8)]))

    shifts = sorted(((latest[a] - first[a], a) for a in latest if a in first), key=lambda x: -abs(x[0]))
    out.append("\n## Biggest opinion shifts\n" + _table(
        ["agent", "type", "first", "latest", "Δ"],
        [[f"{run.by_id[a]['name']} (#{a})", run.by_id[a]["entity_type"], _fmt(first[a]), _fmt(latest[a]), _fmt(d)]
         for d, a in shifts[:8] if abs(d) >= 0.1]))
    return "\n".join(out)


def timeline(sim: Sim, bucket_hours: float = 6, platform: Optional[str] = None) -> str:
    run = Run(sim)
    bucket = max(1, int(bucket_hours * 60 / run.tc["minutes_per_round"]))
    by_bucket: Dict[int, List[dict]] = defaultdict(list)
    for t in run.turns:
        if not platform or t["platform"] == platform:
            by_bucket[t["round"] // bucket].append(t)
    out = [f"# Timeline — {run.config['title']} ({bucket_hours:g}h buckets)"]
    for b in sorted(by_bucket):
        ts = by_bucket[b]
        agent_ts = [t for t in ts if t["source"] == "agent"]
        c = Counter(a["type"] for t in agent_ts for a in t["actions"] if a.get("ok", True))
        out.append(f"\n## {run.clock(b * bucket)} → {run.clock((b + 1) * bucket)}")
        out.append(f"online turns {len(agent_ts)} · posts {c['CREATE_POST'] + c['QUOTE_POST']} · comments "
                   f"{c['CREATE_COMMENT']} · likes {c['LIKE_POST'] + c['LIKE_COMMENT']} · dislikes "
                   f"{c['DISLIKE_POST'] + c['DISLIKE_COMMENT']} · reposts {c['REPOST']} · follows {c['FOLLOW']} · "
                   f"mean sentiment {_fmt(_mean(t.get('sentiment') for t in agent_ts))}")
        for t in ts:
            if t["source"] in ("initial", "event"):
                label = "kick-off post" if t["source"] == "initial" else "INJECTED EVENT"
                a = t["actions"][0]
                out.append(f"- **{label}** [{t['platform']} #{a['context'].get('post_id')}] {t['agent_name']}: "
                           f"\"{truncate(a['args'].get('content', ''), 200)}\"")
        new_posts = []
        for t in agent_ts:
            for a in t["actions"]:
                if a.get("ok", True) and a["type"] in ("CREATE_POST", "QUOTE_POST", "CREATE_COMMENT"):
                    pid = a["context"].get("post_id") if a["type"] == "CREATE_POST" else a["context"].get("new_post_id")
                    plat = run.plats.get(t["platform"])
                    eng = plat.engagement(plat.posts[pid]) if plat and pid in plat.posts else 0
                    new_posts.append((eng, t, a))
        new_posts.sort(key=lambda x: -x[0])
        for eng, t, a in new_posts[:4]:
            ctx = a["context"]
            where = {"CREATE_POST": f"#{ctx.get('post_id')}",
                     "QUOTE_POST": f"#{ctx.get('new_post_id')} quoting #{ctx.get('post_id')}",
                     "CREATE_COMMENT": f"comment #{ctx.get('comment_id')} on #{ctx.get('post_id')}"}[a["type"]]
            verb = {"CREATE_POST": "posted", "QUOTE_POST": "quoted", "CREATE_COMMENT": "commented"}[a["type"]]
            out.append(f"- [{t['platform']} {where}] @{t['username']} ({t['entity_type']}) {verb}: "
                       f"\"{truncate(a['args'].get('content', ''), 180)}\" (engagement {eng:g})")
        thoughts = [t for t in agent_ts if t.get("thought") and t.get("sentiment") is not None]
        if thoughts:
            lo = min(thoughts, key=lambda t: t["sentiment"])
            hi = max(thoughts, key=lambda t: t["sentiment"])
            for t in {id(lo): lo, id(hi): hi}.values():
                out.append(f"- _private thought_ {t['agent_name']} ({t['entity_type']}, {t['sentiment']:+.2f}): "
                           f"\"{truncate(t['thought'], 160)}\"")
    return "\n".join(out)


def search(sim: Sim, query: str, limit: int = 15, thoughts: bool = False, platform: Optional[str] = None) -> str:
    run = Run(sim)
    q = keywords(query, 3)
    phrase = query.lower().strip()
    if not q and not phrase:
        raise CohorteError("Empty query.")
    docs: List[Tuple[float, str]] = []

    def score(text: str, bonus: float) -> float:
        low = text.lower()
        s = len(q & keywords(text, 3)) + (2.0 if phrase and phrase in low else 0.0)
        return s + bonus if s > 0 else 0.0

    for name, plat in run.plats.items():
        if platform and name != platform:
            continue
        for p in plat.posts.values():
            if p["kind"] == "repost":
                continue
            s = score(p["content"], 0.05 * plat.engagement(p))
            if s:
                author = run.by_id.get(p["author"], {}) if p["author"] is not None else {}
                docs.append((s, f"[{name} post #{p['id']}] {p['clock']} {plat.handle(p['author'])} "
                                f"({author.get('entity_type', 'News')}) eng {plat.engagement(p):g}: \"{truncate(p['content'], 260)}\""))
        for c in plat.comments.values():
            s = score(c["content"], 0.05 * len(c["likes"]))
            if s:
                docs.append((s, f"[{name} comment #{c['id']} on post #{c['post_id']}] {c['clock']} "
                                f"{plat.handle(c['author'])} ({run.by_id[c['author']]['entity_type']}) ▲{len(c['likes'])}: "
                                f"\"{truncate(c['content'], 260)}\""))
    if thoughts:
        for t in run.agent_turns(platform):
            if t.get("thought"):
                s = score(t["thought"], 0)
                if s:
                    docs.append((s - 0.01, f"[private thought · {t['platform']} {run.clock(t['round'])}] "
                                           f"{t['agent_name']} ({t['entity_type']}, sentiment {_fmt(t.get('sentiment'))}): "
                                           f"\"{truncate(t['thought'], 260)}\""))
    docs.sort(key=lambda x: -x[0])
    head = f"# Search \"{query}\" — {len(docs)} match(es){' (including private thoughts)' if thoughts else ''}"
    lines = [f"- {d}" for _, d in docs[:limit]] or ["_(no match)_"]
    return "\n".join([head] + lines)


def agent(sim: Sim, ref: str) -> str:
    run = Run(sim)
    p = resolve_agent(run.profiles, ref)
    aid = p["agent_id"]
    c = next(x for x in run.config["agent_configs"] if x["agent_id"] == aid)
    out = [f"# {p['name']} (@{p['username']}) — agent #{aid}",
           f"{p['entity_type']} ({p['kind']}) · {p.get('age')} · {p.get('gender')} · {p.get('mbti')} · "
           f"{p.get('profession')} · {p.get('country')}",
           f"stance {c['stance']} · bias {c['sentiment_bias']:+.1f} · activity {c['activity_level']} · "
           f"influence {c['influence_weight']} · active hours {c['active_hours']}",
           f"\n**Bio:** {p['bio']}\n\n**Persona:** {p['persona']}"]
    for name, plat in run.plats.items():
        following = sorted(plat.follows.get(aid, set()))
        followers = [a for a, f in plat.follows.items() if aid in f]
        out.append(f"\n**{name}:** follows {len(following)} · followed by {len(followers)} simulated agents "
                   f"(+{p.get('followers', 0)} audience)")
    out.append("\n## Activity log")
    for t in run.turns:
        if t.get("agent_id") != aid:
            continue
        acts = []
        for a in t["actions"]:
            if a["type"] == "DO_NOTHING":
                acts.append("scrolled")
                continue
            ctx = a.get("context", {})
            target = f" #{ctx['post_id']}" if ctx.get("post_id") and a["type"] != "CREATE_POST" else ""
            content = a.get("args", {}).get("content") or a.get("args", {}).get("query") or ctx.get("user") or ""
            acts.append(f"{a['type']}{target}{' ✗' if not a.get('ok', True) else ''}"
                        + (f" \"{truncate(content, 160)}\"" if content else ""))
        senti = f" · sentiment {_fmt(t.get('sentiment'))}" if t.get("sentiment") is not None else ""
        probes = f" · probes {t['probes']}" if t.get("probes") else ""
        out.append(f"- {run.clock(t['round'])} [{t['platform']}]{senti}{probes}: {'; '.join(acts)}")
        if t.get("thought"):
            out.append(f"  _thought:_ \"{truncate(t['thought'], 220)}\"")
    for iv in read_jsonl(sim.path("interviews.jsonl")):
        if iv.get("agent_id") == aid:
            out.append(f"\n**Interview** Q: {iv['question']}\nA: {iv['answer']}")
    return "\n".join(out)


def thread(sim: Sim, post_id: int, platform: Optional[str] = None) -> str:
    run = Run(sim)
    plat_name = platform or run.config["platforms"][0]
    plat = run.plats.get(plat_name)
    if plat is None or post_id not in plat.posts:
        raise CohorteError(f"No post #{post_id} on {plat_name}.")
    p = plat.posts[post_id]
    out = [plat.render_post(post_id, run.state["round"], run.tc["minutes_per_round"], -1, 0)]
    out.append(f"posted {p['clock']}")
    if p["likes"]:
        out.append("liked by: " + ", ".join(f"{run.by_id[a]['name']} ({run.by_id[a]['entity_type']})" for a in p["likes"]))
    if p["dislikes"]:
        out.append("downvoted by: " + ", ".join(run.by_id[a]["name"] for a in p["dislikes"]))
    if p["reposted_by"]:
        out.append("reposted by: " + ", ".join(run.by_id[a]["name"] for a in p["reposted_by"]))
    for q in plat.posts.values():
        if q["kind"] == "quote" and q["ref"] == post_id:
            out.append(f"- quote [post #{q['id']}] {plat.handle(q['author'])} {q['clock']}: \"{q['content']}\" "
                       f"(eng {plat.engagement(q):g})")
    for cid in p["comments"]:
        c = plat.comments[cid]
        out.append(f"- [comment #{cid}] {plat.handle(c['author'])} ({run.by_id[c['author']]['entity_type']}) "
                   f"{c['clock']} ▲{len(c['likes'])} ▼{len(c['dislikes'])}: \"{c['content']}\"")
    return "\n".join(out)


def export(sim: Sim, platform: Optional[str] = None) -> str:
    run = Run(sim)
    out = [f"# Transcript — {run.config['title']}"]
    for name, plat in run.plats.items():
        if platform and name != platform:
            continue
        out.append(f"\n## {name}")
        for p in sorted(plat.posts.values(), key=lambda x: x["id"]):
            if p["kind"] == "repost":
                continue
            quote = f" (quoting #{p['ref']})" if p["kind"] == "quote" else ""
            out.append(f"\n**#{p['id']}** {p['clock']} {plat.handle(p['author'])}{quote} · "
                       f"♥{len(p['likes'])} ▼{len(p['dislikes'])} ↻{p['shares']} ❝{p['quotes']}\n> {p['content']}")
            for cid in p["comments"]:
                c = plat.comments[cid]
                out.append(f"  - {plat.handle(c['author'])} ({c['clock']}, ▲{len(c['likes'])}): {c['content']}")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Interviews (stage 5: deep interaction)
# --------------------------------------------------------------------------- #

def interview(sim: Sim, llm: LLMClient, question: str, refs: Optional[List[str]] = None,
              entity_type: Optional[str] = None, sample: Optional[int] = None, everyone: bool = False,
              followup: bool = False) -> str:
    run = Run(sim)
    if refs:
        chosen = [resolve_agent(run.profiles, r) for r in refs]
    else:
        pool = [p for p in run.profiles if not entity_type or p["entity_type"].lower() == entity_type.lower()]
        if not pool:
            raise CohorteError(f"No agent of type '{entity_type}'.")
        if not everyone:
            n = sample or min(5, len(pool))
            rng = rng_for(run.config["seed"], "interview", question)
            pool = sorted(rng.sample(pool, min(n, len(pool))), key=lambda p: p["agent_id"])
        chosen = pool
    builder = TurnBuilder(run.config, run.profiles)
    history = list(read_jsonl(sim.path("interviews.jsonl"))) if followup else []
    lang = run.config["language"]
    reqs = []
    for p in chosen:
        aid = p["agent_id"]
        mem_lines = []
        for name, plat in run.plats.items():
            for m in plat.memory.get(aid, [])[-20:]:
                mem_lines.append(f"- [{name}] {m}")
        past = [h for h in history if h.get("agent_id") == aid][-4:]
        hist_txt = "".join(f"Interviewer: {h['question']}\nYou: {h['answer']}\n\n" for h in past)
        if hist_txt:
            hist_txt = "## Earlier in this interview\n" + hist_txt
        first_plat = next(iter(run.plats.values()))
        reqs.append(LLMRequest(
            id=f"interview:{aid}", kind="interview", expect_json=False, effort="medium", max_tokens=4000,
            system=[builder.persona_block(aid, first_plat),
                    P.INTERVIEW_SYSTEM_SUFFIX.format(name=p["name"], language=lang)],
            user=P.INTERVIEW_USER.format(memory="\n".join(mem_lines) or "- (you never posted or reacted)",
                                         history=hist_txt, question=question),
            meta={"name": p["name"], "language": lang, "subject": run.config["subject"]},
        ))
    answers = llm.run_batch(reqs, label="interviews")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = [f"# Interview: {question}"]
    records = []
    for p, req in zip(chosen, reqs):
        ans = (answers.get(req.id) or "(no answer — the call failed)").strip()
        out.append(f"\n## {p['name']} (@{p['username']}, {p['entity_type']}, agent #{p['agent_id']})\n{ans}")
        records.append({"at": stamp, "agent_id": p["agent_id"], "name": p["name"], "entity_type": p["entity_type"],
                        "question": question, "answer": ans})
    append_jsonl(sim.path("interviews.jsonl"), records)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Compare (adapt loop) and cost estimate
# --------------------------------------------------------------------------- #

def compare(sim_a: Sim, sim_b: Sim) -> str:
    ma, mb = metrics(Run(sim_a)), metrics(Run(sim_b))

    def delta(a, b):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return f"{b - a:+.2f}" if isinstance(a, float) or isinstance(b, float) else f"{b - a:+d}"
        return ""

    rows = []
    for key in ("rounds_done", "agent_turns", "idle_share", "posts", "comments", "sentiment_first_mean",
                "sentiment_latest_mean", "share_positive", "share_negative", "polarization_std", "parse_errors"):
        rows.append([key, ma.get(key), mb.get(key), delta(ma.get(key), mb.get(key))])
    for k in sorted(set(ma["actions"]) | set(mb["actions"])):
        rows.append([f"actions.{k}", ma["actions"].get(k, 0), mb["actions"].get(k, 0),
                     delta(ma["actions"].get(k, 0), mb["actions"].get(k, 0))])
    for k in sorted(set(ma["probes_latest_mean"]) | set(mb["probes_latest_mean"])):
        a, b = ma["probes_latest_mean"].get(k), mb["probes_latest_mean"].get(k)
        rows.append([f"probe.{k}", a, b, delta(a, b)])
    for k in sorted(set(ma["sentiment_by_type"]) | set(mb["sentiment_by_type"])):
        a, b = ma["sentiment_by_type"].get(k), mb["sentiment_by_type"].get(k)
        rows.append([f"sentiment.{k}", a, b, delta(a, b)])
    note = ""
    if ma["rounds_done"] != mb["rounds_done"]:
        note = "\n\n⚠️ The runs covered a different number of rounds; compare them at equal length for a fair read."
    return (f"# Compare {sim_a.name} → {sim_b.name}\n" + _table(["metric", sim_a.name, sim_b.name, "Δ"], rows)
            + note + "\n\nAgent behaviour is stochastic: treat small deltas (|Δ| < 0.1 sentiment) as noise; "
                     "re-run both variants or lengthen them before acting on them.")


def estimate(sim: Sim, model: Optional[str] = None) -> str:
    sim.require("agents.json", "config.json")
    cfg, profiles = sim.config(), sim.agents()
    model = resolve_model(model or sim.meta().get("llm", {}).get("model"))
    inp, out, cache_read = price_for(model)
    tc = cfg["time_config"]
    st = read_json(sim.path("state.json"), default={"round": 0})
    turns = 0
    for r in range(st["round"], tc["total_rounds"]):
        hour = clock_for(tc, r)[1]
        for plat in cfg["platforms"]:
            turns += len(select_active(cfg, hour, rng_for(cfg["seed"], plat, r, "active")))
    persona_tokens = _mean(len(p["persona"]) + len(p["bio"]) for p in profiles) or 1500
    sys_tokens = int(persona_tokens / 3.5) + 900
    feed = cfg["turn"]["feed_size"]
    feed_n = max(feed.values()) if isinstance(feed, dict) else int(feed)
    user_tokens = feed_n * 110 + cfg["turn"]["memory_size"] * 35 + 250
    out_tokens = 450  # JSON decision + low-effort thinking
    followups = int(turns * (0.08 if "reddit" in cfg["platforms"] else 0))
    calls = turns + followups
    no_cache = calls * ((sys_tokens + user_tokens) * inp + out_tokens * out) / 1e6
    cached = calls * (sys_tokens * cache_read + user_tokens * inp + out_tokens * out) / 1e6
    return (
        f"# Cost estimate for the remaining run — model {model}\n"
        f"- remaining rounds: {tc['total_rounds'] - st['round']} · agent turns: {turns} (exact, activation is "
        f"seeded) + ~{followups} search follow-ups\n"
        f"- tokens per call ≈ {sys_tokens} system + {user_tokens} user in, ~{out_tokens} out\n"
        f"- estimated cost: **${cached:.2f}** with prompt caching hits, up to **${no_cache:.2f}** without\n"
        f"- list prices used: ${inp}/M input, ${out}/M output, ${cache_read}/M cache read "
        f"(claude-cli on a subscription consumes plan usage instead)\n"
        f"- interviews and report-writing tools are extra but small (one call per interviewed agent)"
    )
