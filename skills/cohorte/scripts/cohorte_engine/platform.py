"""Simulated social platforms (stand-in for OASIS' Twitter and Reddit environments).

State is plain JSON-able data so a run can be saved after every round and resumed.

Built to scale to tens of thousands of agents:
  * post keywords, authors' posts and follower counts are indexed once, not recomputed per feed;
  * feeds are ranked from a per-round candidate pool (globally hot/recent posts + posts by the
    people the agent follows) instead of scoring every post for every agent;
  * likes/dislikes/reposters are sets in memory (lists on disk);
  * crowd-tier agents keep a short memory, and their reactions reach authors as one aggregated
    notification per post ("37 people liked your post") instead of thousands of lines.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .util import keywords, truncate

TWITTER_ACTIONS = ["CREATE_POST", "LIKE_POST", "REPOST", "FOLLOW", "DO_NOTHING", "QUOTE_POST"]
REDDIT_ACTIONS = [
    "LIKE_POST", "DISLIKE_POST", "CREATE_POST", "CREATE_COMMENT", "LIKE_COMMENT", "DISLIKE_COMMENT",
    "SEARCH_POSTS", "SEARCH_USER", "TREND", "REFRESH", "DO_NOTHING", "FOLLOW", "MUTE",
]
PLATFORM_ACTIONS = {"twitter": TWITTER_ACTIONS, "reddit": REDDIT_ACTIONS}
INFO_ACTIONS = {"SEARCH_POSTS", "SEARCH_USER", "TREND", "REFRESH"}
CONTENT_LIMIT = {"twitter": 600, "reddit": 3000}
NEWS_BOOST_ROUNDS = 6
MEMORY_KEEP = 40
CROWD_MEMORY_KEEP = 6
NOTIF_KEEP = 15
SEEN_KEEP = 80
POOL_SIZE = 150            # globally ranked candidates per round
FOLLOWED_RECENT = 4        # latest posts per followed author added to an agent's candidates
FOLLOWED_WINDOW = 48       # rounds
SET_FIELDS = ("likes", "dislikes", "reposted_by")


class Platform:
    def __init__(self, name: str, profiles: List[Dict[str, Any]], cfg: Dict[str, Any], data: Optional[dict] = None):
        self.name = name
        self.cfg = cfg                                  # platform_configs[name]
        self.profiles = {p["agent_id"]: p for p in profiles}
        self.by_username = {p["username"].lower(): p["agent_id"] for p in profiles}
        self.crowd_ids = {p["agent_id"] for p in profiles if p.get("tier") == "crowd"}
        data = data or {}
        self.posts: Dict[int, dict] = {}
        for p in data.get("posts", []):
            for f in SET_FIELDS:
                p[f] = set(p.get(f, []))
            self.posts[p["id"]] = p
        self.comments: Dict[int, dict] = {}
        for c in data.get("comments", []):
            c["likes"], c["dislikes"] = set(c.get("likes", [])), set(c.get("dislikes", []))
            self.comments[c["id"]] = c
        self.follows: Dict[int, set] = {
            int(k): set(v) for k, v in data.get("follows", {}).items()
        } or {p["agent_id"]: set(p.get("following", [])) for p in profiles}
        self.mutes: Dict[int, set] = {int(k): set(v) for k, v in data.get("mutes", {}).items()}
        self.seen: Dict[int, Dict[int, int]] = {
            int(k): {int(pk): c for pk, c in v.items()} for k, v in data.get("seen", {}).items()
        }
        self.memory: Dict[int, List[str]] = {int(k): v for k, v in data.get("memory", {}).items()}
        self.notifications: Dict[int, List[str]] = {int(k): v for k, v in data.get("notifications", {}).items()}
        self.sentiment: Dict[int, float] = {int(k): v for k, v in data.get("sentiment", {}).items()}
        self.next_post = max(self.posts, default=0) + 1
        self.next_comment = max(self.comments, default=0) + 1
        # indexes (rebuilt on load, maintained incrementally)
        self._kw: Dict[int, frozenset] = {}
        self.author_posts: Dict[Optional[int], List[int]] = defaultdict(list)
        for pid in sorted(self.posts):
            if self.posts[pid]["kind"] != "repost":
                self.author_posts[self.posts[pid]["author"]].append(pid)
        self.follower_counts: Dict[int, int] = defaultdict(int)
        for followed in self.follows.values():
            for t in followed:
                self.follower_counts[t] += 1
        self._profile_kw: Optional[Dict[int, set]] = None
        self._pool: Tuple[int, List[int]] = (-1, [])
        self._crowd_agg: Dict[int, Dict[str, int]] = {}

    # ------------------------------------------------------------------ io --
    def to_dict(self) -> dict:
        def post_out(p):
            return {**p, **{f: sorted(p[f]) for f in SET_FIELDS}}

        def comment_out(c):
            return {**c, "likes": sorted(c["likes"]), "dislikes": sorted(c["dislikes"])}

        return {
            "posts": [post_out(self.posts[k]) for k in sorted(self.posts)],
            "comments": [comment_out(self.comments[k]) for k in sorted(self.comments)],
            "follows": {str(k): sorted(v) for k, v in self.follows.items()},
            "mutes": {str(k): sorted(v) for k, v in self.mutes.items() if v},
            "seen": {str(k): {str(pk): c for pk, c in v.items()} for k, v in self.seen.items()},
            "memory": {str(k): v for k, v in self.memory.items()},
            "notifications": {str(k): v for k, v in self.notifications.items() if v},
            "sentiment": {str(k): v for k, v in self.sentiment.items()},
        }

    # ------------------------------------------------------------- helpers --
    def handle(self, agent_id: Optional[int]) -> str:
        if agent_id is None:
            return "NEWS"
        p = self.profiles.get(agent_id)
        prefix = "u/" if self.name == "reddit" else "@"
        return f"{prefix}{p['username']}" if p else f"{prefix}unknown"

    def followers_of(self, agent_id: int) -> int:
        return self.profiles.get(agent_id, {}).get("followers", 0) + self.follower_counts.get(agent_id, 0)

    def engagement(self, post: dict) -> float:
        return (
            len(post["likes"]) + 2 * post["shares"] + 2 * post["quotes"]
            + 1.5 * len(post["comments"]) - 0.5 * len(post["dislikes"])
        )

    def post_keywords(self, pid: int) -> frozenset:
        kw = self._kw.get(pid)
        if kw is None:
            kw = self._kw[pid] = frozenset(keywords(self.posts[pid]["content"]))
        return kw

    def _add_memory(self, agent_id: int, text: str) -> None:
        mem = self.memory.setdefault(agent_id, [])
        mem.append(text)
        keep = CROWD_MEMORY_KEEP if agent_id in self.crowd_ids else MEMORY_KEEP
        if len(mem) > keep:
            del mem[:-keep]

    def _notify(self, agent_id: Optional[int], text: str, actor: int) -> None:
        if agent_id is None or agent_id == actor or agent_id not in self.profiles or agent_id in self.crowd_ids:
            return
        notes = self.notifications.setdefault(agent_id, [])
        notes.append(text)
        del notes[:-NOTIF_KEEP]

    def _crowd_reaction(self, pid: int, kind: str) -> None:
        agg = self._crowd_agg.setdefault(pid, {})
        agg[kind] = agg.get(kind, 0) + 1

    def flush_crowd_notifications(self, clock: str) -> None:
        """One aggregated line per post for the crowd's reactions this round."""
        labels = {"like": "liked", "dislike": "downvoted", "repost": "reposted", "quote": "quoted",
                  "comment": "commented on"}
        for pid, agg in self._crowd_agg.items():
            post = self.posts.get(pid)
            if not post or post["author"] is None or post["author"] in self.crowd_ids:
                continue
            parts = [f"{n} {'person' if n == 1 else 'people'} {labels[k]}" for k, n in agg.items() if n]
            if parts:
                self._notify(post["author"], f"{clock} " + ", ".join(parts) + f" your [post {pid}]", -1)
        self._crowd_agg = {}

    def _mentions(self, content: str, actor: int, clock: str, post_id: int) -> None:
        if actor in self.crowd_ids or "@" not in content and "u/" not in content:
            return
        for token in content.split():
            if token.startswith(("@", "u/")):
                uname = token.lstrip("@").replace("u/", "", 1).strip(".,:;!?)(\"'").lower()
                target = self.by_username.get(uname)
                if target is not None:
                    self._notify(target, f"{clock} {self.handle(actor)} mentioned you in [post {post_id}]: "
                                         f"\"{truncate(content, 160)}\"", actor)

    # -------------------------------------------------------------- create --
    def create_post(self, author: Optional[int], content: str, round_no: int, clock: str,
                    kind: str = "post", ref: Optional[int] = None) -> int:
        pid = self.next_post
        self.next_post += 1
        self.posts[pid] = {
            "id": pid, "author": author, "content": content[: CONTENT_LIMIT[self.name]], "round": round_no,
            "clock": clock, "kind": kind, "ref": ref, "likes": set(), "dislikes": set(), "shares": 0, "quotes": 0,
            "comments": [], "reposted_by": set(),
        }
        if kind != "repost":
            self.author_posts[author].append(pid)
        if author is not None and kind in ("post", "quote"):
            self._mentions(content, author, clock, pid)
        return pid

    # ---------------------------------------------------------------- feed --
    def _base_score(self, post: dict, round_no: int) -> float:
        c = self.cfg
        age = max(0, round_no - post["round"])
        eng = self.engagement(post)
        viral = c.get("viral_threshold", 10)
        popularity = min(1.0, math.log1p(max(0.0, eng)) / math.log1p(viral * 3)) + (0.5 if eng >= viral else 0.0)
        score = c.get("recency_weight", 0.4) * math.exp(-age / 12.0) + c.get("popularity_weight", 0.3) * popularity
        if post["author"] is None and age < NEWS_BOOST_ROUNDS:
            score += 1.0
        return score

    def prepare_round(self, round_no: int) -> None:
        """Rank the global candidate pool once per round (cheap feeds for thousands of agents)."""
        if self._pool[0] == round_no:
            return
        scored = [(self._base_score(p, round_no), pid) for pid, p in self.posts.items() if p["kind"] != "repost"]
        scored.sort(reverse=True)
        self._pool = (round_no, [pid for _, pid in scored[:POOL_SIZE]])

    def _score(self, agent_id: int, post: dict, round_no: int, interest: set, rng, follows: set) -> float:
        c = self.cfg
        score = self._base_score(post, round_no)
        words = self.post_keywords(post["id"])
        if words and interest:
            relevance = len(words & interest) / max(4, min(len(words), 12))
            score += c.get("relevance_weight", 0.3) * min(1.0, relevance * 2)
        author = post["author"]
        if author in follows:
            score += 0.6 if self.name == "twitter" else 0.2
        if post["reposted_by"] and not follows.isdisjoint(post["reposted_by"]):
            score += 0.4
        if author is not None:
            mine, theirs = self.sentiment.get(agent_id), self.sentiment.get(author)
            if mine is not None and theirs is not None:
                score += c.get("echo_chamber_strength", 0.5) * (0.5 - abs(mine - theirs) / 2)
            score += 0.1 * math.log1p((self.profiles.get(author) or {}).get("influence_weight", 1.0))
        score -= min(1.0, 0.4 * self.seen.get(agent_id, {}).get(post["id"], 0))
        return score + rng.random() * 0.15

    def feed(self, agent_id: int, round_no: int, size: int, interest: set, rng) -> List[int]:
        self.prepare_round(round_no)
        muted = self.mutes.get(agent_id, set())
        follows = self.follows.get(agent_id, set())
        candidates = set(self._pool[1])
        for author in follows:
            for pid in reversed(self.author_posts.get(author, [])[-FOLLOWED_RECENT:]):
                if round_no - self.posts[pid]["round"] <= FOLLOWED_WINDOW:
                    candidates.add(pid)
        ranked = sorted(
            (self.posts[pid] for pid in candidates
             if self.posts[pid]["author"] != agent_id and self.posts[pid]["author"] not in muted),
            key=lambda p: -self._score(agent_id, p, round_no, interest, rng, follows),
        )
        chosen = [p["id"] for p in ranked[:size]]
        rest = [p["id"] for p in ranked[size:]]
        if rest and size >= 4:
            chosen[-1] = rng.choice(rest)          # exploration slot
        return chosen

    def mark_seen(self, agent_id: int, post_ids: List[int]) -> None:
        seen = self.seen.setdefault(agent_id, {})
        for pid in post_ids:
            seen[pid] = seen.get(pid, 0) + 1
        if len(seen) > SEEN_KEEP:
            for pid in list(seen)[: len(seen) - SEEN_KEEP]:
                del seen[pid]

    def render_post(self, pid: int, round_no: int, minutes_per_round: int, viewer: int,
                    with_comments: int = 0, extra: str = "") -> str:
        p = self.posts[pid]
        age_h = (round_no - p["round"]) * minutes_per_round / 60
        age = "just now" if age_h < 1 else (f"{int(age_h)}h ago" if age_h < 48 else f"{int(age_h // 24)}d ago")
        if p["author"] is None:
            head = f"[post {pid}] 📰 NEWS · {age}"
        else:
            prof = self.profiles.get(p["author"], {})
            who = f"{self.handle(p['author'])} ({prof.get('name', '?')}, {prof.get('entity_type', '?')})"
            if p["author"] in self.follows.get(viewer, set()):
                who += " · you follow"
            head = f"[post {pid}] {who} · {age}"
        if self.name == "twitter":
            stats = f"♥ {len(p['likes'])} · ↻ {p['shares']} · ❝ {p['quotes']}"
        else:
            stats = f"▲ {len(p['likes'])} ▼ {len(p['dislikes'])} · {len(p['comments'])} comments"
        lines = [f"{head} · {stats}{extra}"]
        viewer_follows = self.follows.get(viewer, set())
        reposters = [r for r in p["reposted_by"] if r in viewer_follows][:3]
        if reposters:
            lines.append("  ↻ reposted by " + ", ".join(self.handle(r) for r in reposters))
        if p["kind"] == "quote" and p["ref"] in self.posts:
            q = self.posts[p["ref"]]
            lines.append(f"  quoting [post {q['id']}] {self.handle(q['author'])}: \"{truncate(q['content'], 140)}\"")
        lines.append(f"  \"{p['content']}\"")
        if with_comments and p["comments"]:
            top = sorted(p["comments"], key=lambda c: -(len(self.comments[c]["likes"]) - len(self.comments[c]["dislikes"])))
            for cid in top[:with_comments]:
                c = self.comments[cid]
                lines.append(f"    [comment {cid}] {self.handle(c['author'])} (▲{len(c['likes'])}): "
                             f"\"{truncate(c['content'], 220)}\"")
            if len(p["comments"]) > with_comments:
                lines.append(f"    … {len(p['comments']) - with_comments} more comments")
        return "\n".join(lines)

    # --------------------------------------------------------------- search --
    def search_posts(self, query: str, limit: int = 5) -> List[int]:
        q = keywords(query, 3)
        scored = []
        for p in self.posts.values():
            if p["kind"] == "repost":
                continue
            overlap = len(q & self.post_keywords(p["id"]))
            if overlap:
                scored.append((overlap + 0.05 * self.engagement(p), p["id"]))
        return [pid for _, pid in sorted(scored, reverse=True)[:limit]]

    def search_users(self, query: str, limit: int = 5) -> List[int]:
        if self._profile_kw is None:
            self._profile_kw = {
                aid: keywords(f"{p['name']} {p['username']} {p.get('bio', '')} {p.get('entity_type', '')}", 3)
                | {p["username"].lower()}
                for aid, p in self.profiles.items()
            }
        q = keywords(query, 3) | {query.lower().lstrip("@")}
        scored = [(len(q & kw), self.follower_counts.get(aid, 0), aid) for aid, kw in self._profile_kw.items() if q & kw]
        return [aid for _, _, aid in sorted(scored, reverse=True)[:limit]]

    def trending(self, round_no: int, window: int = 24, limit: int = 5) -> List[int]:
        recent = [p for p in self.posts.values() if p["kind"] != "repost" and round_no - p["round"] <= window]
        return [p["id"] for p in sorted(recent, key=lambda p: -self.engagement(p))[:limit]]

    # ---------------------------------------------------------------- apply --
    def apply(self, agent_id: int, action: Dict[str, Any], round_no: int, clock: str) -> Dict[str, Any]:
        """Apply one action. Returns a log record {"type", "args", "ok", "context"}."""
        t = action["type"]
        args = {k: v for k, v in action.items() if k != "type"}
        rec: Dict[str, Any] = {"type": t, "args": args, "ok": True, "context": {}}
        me = self.handle(agent_id)
        crowd = agent_id in self.crowd_ids

        def fail(reason: str) -> Dict[str, Any]:
            rec["ok"] = False
            rec["error"] = reason
            return rec

        def post_arg() -> Optional[dict]:
            pid = args.get("post_id")
            try:
                pid = int(pid)
            except (TypeError, ValueError):
                return None
            return self.posts.get(pid)

        def describe(p: dict) -> Dict[str, Any]:
            if crowd:
                return {"post_id": p["id"]}
            return {"post_id": p["id"], "post_author": self.handle(p["author"]),
                    "post_content": truncate(p["content"], 200)}

        if t == "DO_NOTHING" or t in INFO_ACTIONS:
            return rec
        if t == "CREATE_POST":
            content = str(args.get("content") or "").strip()
            if not content:
                return fail("empty content")
            pid = self.create_post(agent_id, content, round_no, clock)
            rec["context"] = {"post_id": pid}
            self._add_memory(agent_id, f"{clock} You posted [post {pid}]: \"{truncate(content, 220)}\"")
            return rec
        if t in ("LIKE_POST", "DISLIKE_POST", "REPOST", "QUOTE_POST", "CREATE_COMMENT"):
            p = post_arg()
            if p is None:
                return fail("unknown post_id")
            rec["context"] = describe(p)
            snippet = f"{self.handle(p['author'])}'s [post {p['id']}] \"{truncate(p['content'], 120)}\""
            if t == "LIKE_POST":
                if agent_id in p["likes"] or p["author"] == agent_id:
                    return fail("already liked or own post")
                p["likes"].add(agent_id)
                p["dislikes"].discard(agent_id)
                if crowd:
                    self._crowd_reaction(p["id"], "like")
                else:
                    self._notify(p["author"], f"{clock} {me} liked your [post {p['id']}]", agent_id)
                self._add_memory(agent_id, f"{clock} You liked {snippet}")
            elif t == "DISLIKE_POST":
                if agent_id in p["dislikes"] or p["author"] == agent_id:
                    return fail("already disliked or own post")
                p["dislikes"].add(agent_id)
                p["likes"].discard(agent_id)
                if crowd:
                    self._crowd_reaction(p["id"], "dislike")
                self._add_memory(agent_id, f"{clock} You downvoted {snippet}")
            elif t == "REPOST":
                if agent_id in p["reposted_by"] or p["author"] == agent_id:
                    return fail("already reposted or own post")
                p["shares"] += 1
                p["reposted_by"].add(agent_id)
                self.create_post(agent_id, p["content"], round_no, clock, kind="repost", ref=p["id"])
                if crowd:
                    self._crowd_reaction(p["id"], "repost")
                else:
                    self._notify(p["author"], f"{clock} {me} reposted your [post {p['id']}]", agent_id)
                self._add_memory(agent_id, f"{clock} You reposted {snippet}")
            elif t == "QUOTE_POST":
                content = str(args.get("content") or "").strip()
                if not content:
                    return fail("empty content")
                p["quotes"] += 1
                pid = self.create_post(agent_id, content, round_no, clock, kind="quote", ref=p["id"])
                rec["context"]["new_post_id"] = pid
                if crowd:
                    self._crowd_reaction(p["id"], "quote")
                else:
                    self._notify(p["author"], f"{clock} {me} quoted your [post {p['id']}] in [post {pid}]: "
                                              f"\"{truncate(content, 160)}\"", agent_id)
                self._add_memory(agent_id, f"{clock} You quoted {snippet} saying: \"{truncate(content, 200)}\"")
            else:  # CREATE_COMMENT
                content = str(args.get("content") or "").strip()
                if not content:
                    return fail("empty content")
                cid = self.next_comment
                self.next_comment += 1
                self.comments[cid] = {"id": cid, "post_id": p["id"], "author": agent_id,
                                      "content": content[: CONTENT_LIMIT[self.name]], "round": round_no,
                                      "clock": clock, "likes": set(), "dislikes": set()}
                p["comments"].append(cid)
                rec["context"]["comment_id"] = cid
                if crowd:
                    self._crowd_reaction(p["id"], "comment")
                else:
                    self._notify(p["author"], f"{clock} {me} commented on your [post {p['id']}] "
                                              f"([comment {cid}]): \"{truncate(content, 160)}\"", agent_id)
                    others = {self.comments[c]["author"] for c in p["comments"][-30:-1]} - {p["author"]}
                    for other in others:
                        self._notify(other, f"{clock} {me} also commented on [post {p['id']}] "
                                            f"([comment {cid}]): \"{truncate(content, 120)}\"", agent_id)
                    self._mentions(content, agent_id, clock, p["id"])
                self._add_memory(agent_id, f"{clock} You commented on {snippet}: \"{truncate(content, 200)}\"")
            return rec
        if t in ("LIKE_COMMENT", "DISLIKE_COMMENT"):
            try:
                c = self.comments.get(int(args.get("comment_id")))
            except (TypeError, ValueError):
                c = None
            if c is None:
                return fail("unknown comment_id")
            rec["context"] = {"comment_id": c["id"], "post_id": c["post_id"]} if crowd else {
                "comment_id": c["id"], "comment_author": self.handle(c["author"]),
                "comment_content": truncate(c["content"], 200), "post_id": c["post_id"]}
            field, other = ("likes", "dislikes") if t == "LIKE_COMMENT" else ("dislikes", "likes")
            if agent_id in c[field] or c["author"] == agent_id:
                return fail("duplicate or own comment")
            c[field].add(agent_id)
            c[other].discard(agent_id)
            verb = "upvoted" if t == "LIKE_COMMENT" else "downvoted"
            if t == "LIKE_COMMENT" and not crowd:
                self._notify(c["author"], f"{clock} {me} upvoted your [comment {c['id']}]", agent_id)
            self._add_memory(agent_id, f"{clock} You {verb} {self.handle(c['author'])}'s comment "
                                       f"\"{truncate(c['content'], 100)}\"")
            return rec
        if t in ("FOLLOW", "MUTE"):
            target = self._resolve_user(args)
            if target is None or target == agent_id:
                return fail("unknown user")
            rec["context"] = {"user_id": target, "user": self.handle(target)}
            if t == "FOLLOW":
                if target in self.follows.setdefault(agent_id, set()):
                    return fail("already following")
                self.follows[agent_id].add(target)
                self.follower_counts[target] += 1
                if not crowd:
                    self._notify(target, f"{clock} {me} started following you", agent_id)
                self._add_memory(agent_id, f"{clock} You followed {self.handle(target)}")
            else:
                self.mutes.setdefault(agent_id, set()).add(target)
                self._add_memory(agent_id, f"{clock} You muted {self.handle(target)}")
            return rec
        return fail(f"action {t} not available on {self.name}")

    def _resolve_user(self, args: Dict[str, Any]) -> Optional[int]:
        uid = args.get("user_id")
        try:
            uid = int(uid)
            return uid if uid in self.profiles else None
        except (TypeError, ValueError):
            pass
        name = str(args.get("username") or args.get("user") or uid or "").lstrip("@").replace("u/", "").lower()
        return self.by_username.get(name)

    def consume_notifications(self, agent_id: int) -> List[str]:
        return self.notifications.pop(agent_id, [])

    def stats_counts(self) -> Tuple[int, int]:
        return sum(1 for p in self.posts.values() if p["kind"] != "repost"), len(self.comments)
