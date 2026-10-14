"""Simulated social platforms (stand-in for OASIS' Twitter and Reddit environments).

State is plain JSON-able data so a run can be saved after every round and resumed.
"""

from __future__ import annotations

import math
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
NOTIF_KEEP = 15


class Platform:
    def __init__(self, name: str, profiles: List[Dict[str, Any]], cfg: Dict[str, Any], data: Optional[dict] = None):
        self.name = name
        self.cfg = cfg                                  # platform_configs[name]
        self.profiles = {p["agent_id"]: p for p in profiles}
        self.by_username = {p["username"].lower(): p["agent_id"] for p in profiles}
        data = data or {}
        self.posts: Dict[int, dict] = {p["id"]: p for p in data.get("posts", [])}
        self.comments: Dict[int, dict] = {c["id"]: c for c in data.get("comments", [])}
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

    # ------------------------------------------------------------------ io --
    def to_dict(self) -> dict:
        return {
            "posts": [self.posts[k] for k in sorted(self.posts)],
            "comments": [self.comments[k] for k in sorted(self.comments)],
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
        base = self.profiles.get(agent_id, {}).get("followers", 0)
        return base + sum(1 for f in self.follows.values() if agent_id in f)

    def engagement(self, post: dict) -> float:
        return (
            len(post["likes"]) + 2 * post["shares"] + 2 * post["quotes"]
            + 1.5 * len(post["comments"]) - 0.5 * len(post["dislikes"])
        )

    def _add_memory(self, agent_id: int, text: str) -> None:
        mem = self.memory.setdefault(agent_id, [])
        mem.append(text)
        del mem[:-MEMORY_KEEP]

    def _notify(self, agent_id: Optional[int], text: str, actor: int) -> None:
        if agent_id is None or agent_id == actor or agent_id not in self.profiles:
            return
        notes = self.notifications.setdefault(agent_id, [])
        notes.append(text)
        del notes[:-NOTIF_KEEP]

    def _mentions(self, content: str, actor: int, clock: str, post_id: int) -> None:
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
            "clock": clock, "kind": kind, "ref": ref, "likes": [], "dislikes": [], "shares": 0, "quotes": 0,
            "comments": [], "reposted_by": [],
        }
        if author is not None and kind in ("post", "quote"):
            self._mentions(content, author, clock, pid)
        return pid

    # ---------------------------------------------------------------- feed --
    def _score(self, agent_id: int, post: dict, round_no: int, interest: set, rng) -> float:
        c = self.cfg
        age = max(0, round_no - post["round"])
        recency = math.exp(-age / 12.0)
        eng = self.engagement(post)
        viral = c.get("viral_threshold", 10)
        popularity = min(1.0, math.log1p(max(0.0, eng)) / math.log1p(viral * 3))
        if eng >= viral:
            popularity += 0.5
        words = keywords(post["content"])
        relevance = len(words & interest) / max(4, min(len(words), 12)) if words else 0.0
        score = (c.get("recency_weight", 0.4) * recency + c.get("popularity_weight", 0.3) * popularity
                 + c.get("relevance_weight", 0.3) * min(1.0, relevance * 2))
        author = post["author"]
        follows = self.follows.get(agent_id, set())
        if author in follows:
            score += 0.6 if self.name == "twitter" else 0.2
        if any(r in follows for r in post["reposted_by"]):
            score += 0.4
        if author is None and age < NEWS_BOOST_ROUNDS:
            score += 1.0
        if author is not None:
            mine, theirs = self.sentiment.get(agent_id), self.sentiment.get(author)
            if mine is not None and theirs is not None:
                similarity = 1 - abs(mine - theirs) / 2
                score += c.get("echo_chamber_strength", 0.5) * (similarity - 0.5)
            influence = (self.profiles.get(author) or {}).get("influence_weight", 1.0)
            score += 0.1 * math.log1p(influence)
        score -= min(1.0, 0.4 * self.seen.get(agent_id, {}).get(post["id"], 0))
        return score + rng.random() * 0.15

    def feed(self, agent_id: int, round_no: int, size: int, interest: set, rng) -> List[int]:
        muted = self.mutes.get(agent_id, set())
        candidates = [
            p for p in self.posts.values()
            if p["author"] != agent_id and p["author"] not in muted and p["kind"] != "repost"
        ]
        ranked = sorted(candidates, key=lambda p: -self._score(agent_id, p, round_no, interest, rng))
        chosen = [p["id"] for p in ranked[:size]]
        rest = [p["id"] for p in ranked[size:]]
        if rest and size >= 4:
            chosen[-1] = rng.choice(rest)          # exploration slot
        return chosen

    def mark_seen(self, agent_id: int, post_ids: List[int]) -> None:
        seen = self.seen.setdefault(agent_id, {})
        for pid in post_ids:
            seen[pid] = seen.get(pid, 0) + 1

    def render_post(self, pid: int, round_no: int, minutes_per_round: int, viewer: int,
                    with_comments: int = 0) -> str:
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
        lines = [f"{head} · {stats}"]
        reposters = [r for r in p["reposted_by"] if r in self.follows.get(viewer, set())]
        if reposters:
            lines.append("  ↻ reposted by " + ", ".join(self.handle(r) for r in reposters[:3]))
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
            text = p["content"] + " " + " ".join(self.comments[c]["content"] for c in p["comments"])
            overlap = len(q & keywords(text, 3))
            if overlap:
                scored.append((overlap + 0.05 * self.engagement(p), p["id"]))
        return [pid for _, pid in sorted(scored, reverse=True)[:limit]]

    def search_users(self, query: str, limit: int = 5) -> List[int]:
        q = keywords(query, 3) | {query.lower().lstrip("@")}
        scored = []
        for aid, prof in self.profiles.items():
            text = f"{prof['name']} {prof['username']} {prof.get('bio', '')} {prof.get('entity_type', '')}"
            overlap = len(q & (keywords(text, 3) | {prof["username"].lower()}))
            if overlap:
                scored.append((overlap, aid))
        return [aid for _, aid in sorted(scored, reverse=True)[:limit]]

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
                p["likes"].append(agent_id)
                if agent_id in p["dislikes"]:
                    p["dislikes"].remove(agent_id)
                self._notify(p["author"], f"{clock} {me} liked your [post {p['id']}]", agent_id)
                self._add_memory(agent_id, f"{clock} You liked {snippet}")
            elif t == "DISLIKE_POST":
                if agent_id in p["dislikes"] or p["author"] == agent_id:
                    return fail("already disliked or own post")
                p["dislikes"].append(agent_id)
                if agent_id in p["likes"]:
                    p["likes"].remove(agent_id)
                self._add_memory(agent_id, f"{clock} You downvoted {snippet}")
            elif t == "REPOST":
                if agent_id in p["reposted_by"] or p["author"] == agent_id:
                    return fail("already reposted or own post")
                p["shares"] += 1
                p["reposted_by"].append(agent_id)
                self.create_post(agent_id, p["content"], round_no, clock, kind="repost", ref=p["id"])
                self._notify(p["author"], f"{clock} {me} reposted your [post {p['id']}]", agent_id)
                self._add_memory(agent_id, f"{clock} You reposted {snippet}")
            elif t == "QUOTE_POST":
                content = str(args.get("content") or "").strip()
                if not content:
                    return fail("empty content")
                p["quotes"] += 1
                pid = self.create_post(agent_id, content, round_no, clock, kind="quote", ref=p["id"])
                rec["context"]["new_post_id"] = pid
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
                                      "clock": clock, "likes": [], "dislikes": []}
                p["comments"].append(cid)
                rec["context"]["comment_id"] = cid
                self._notify(p["author"], f"{clock} {me} commented on your [post {p['id']}] "
                                          f"([comment {cid}]): \"{truncate(content, 160)}\"", agent_id)
                for other in {self.comments[c]["author"] for c in p["comments"][:-1]}:
                    if other != p["author"]:
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
            rec["context"] = {"comment_id": c["id"], "comment_author": self.handle(c["author"]),
                              "comment_content": truncate(c["content"], 200), "post_id": c["post_id"]}
            field, other = ("likes", "dislikes") if t == "LIKE_COMMENT" else ("dislikes", "likes")
            if agent_id in c[field] or c["author"] == agent_id:
                return fail("duplicate or own comment")
            c[field].append(agent_id)
            if agent_id in c[other]:
                c[other].remove(agent_id)
            verb = "upvoted" if t == "LIKE_COMMENT" else "downvoted"
            if t == "LIKE_COMMENT":
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
