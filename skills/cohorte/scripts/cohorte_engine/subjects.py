"""Subject profiles: what is being tested and how a person experiences and rates it.

Each kind defines
  experience   how an agent encounters the subject before judging it (prompt text)
  dimensions   rating questions (key, question, min, max, weight) -> the potential score
  outcomes     the real-world numbers this kind is usually judged on (for calibration)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _d(key: str, question: str, weight: float = 1.0, lo: int = 0, hi: int = 10) -> Dict[str, Any]:
    return {"key": key, "question": question, "min": lo, "max": hi, "weight": weight}


GENERIC_DIMS = [
    _d("appeal", "How much does it appeal to you overall?", 1.5),
    _d("relevance", "How relevant is it to your needs or interests?"),
    _d("clarity", "How clear and understandable is it?", 0.5),
    _d("act_intent", "How likely are you to act on it (use, buy, watch, share...) soon?", 2.0),
    _d("recommend", "How likely are you to recommend it to someone like you?", 1.5),
]

SUBJECTS: Dict[str, Dict[str, Any]] = {
    "app": {
        "noun": "app",
        "experience": "You discover the app (store listing, screenshots, description), imagine installing it and "
                      "going through its first minutes of use as described.",
        "dimensions": [
            _d("usefulness", "How useful would this app be in your life?", 1.5),
            _d("ease", "How easy does it look to use?", 0.5),
            _d("install_intent", "How likely are you to install it this week?", 2.0),
            _d("pay_intent", "How likely are you to pay for it at the stated price?", 1.5),
            _d("retention", "How likely are you to still use it after a month?", 1.5),
            _d("recommend", "How likely are you to recommend it?", 1.0),
        ],
        "outcomes": ["store rating", "install conversion", "D30 retention", "paid conversion"],
    },
    "website": {
        "noun": "web page",
        "experience": "You land on the page from a search or a link, skim it like a real visitor (headline, "
                      "first screen, proof, price, call to action) and decide whether to stay.",
        "dimensions": [
            _d("first_impression", "How good is your first impression (first 5 seconds)?", 1.0),
            _d("clarity", "How clearly do you understand what is offered and for whom?", 1.0),
            _d("trust", "How much do you trust it?", 1.0),
            _d("click_intent", "How likely are you to click the main call to action?", 2.0),
            _d("convert_intent", "How likely are you to sign up or buy?", 2.0),
        ],
        "outcomes": ["conversion rate", "bounce rate", "click-through rate"],
    },
    "video": {
        "noun": "video",
        "experience": "The video appears in your feed. You decide from the thumbnail and title whether to click, "
                      "then watch as long as it holds you (transcript and described frames are what you see).",
        "dimensions": [
            _d("click_intent", "How likely are you to click on it from the thumbnail and title?", 1.5),
            _d("retention", "How much of it would you watch (0 = seconds, 10 = all of it)?", 2.0),
            _d("enjoyment", "How much do you enjoy it?", 1.0),
            _d("share_intent", "How likely are you to share, like or comment?", 1.5),
            _d("follow_intent", "How likely are you to follow the creator after this?", 1.0),
        ],
        "outcomes": ["views", "average view duration", "like ratio", "shares"],
    },
    "movie": {
        "noun": "film",
        "experience": "You learn about the film (synopsis, trailer, cast, reviews you would see) and, if you would "
                      "see it, imagine watching it in full as described.",
        "dimensions": [
            _d("watch_intent", "How likely are you to see it (cinema or streaming)?", 2.0),
            _d("pay_cinema", "How likely are you to pay for a cinema ticket for it?", 1.0),
            _d("enjoyment", "How much would you enjoy it?", 1.5),
            _d("recommend", "How likely are you to recommend it?", 1.5),
            _d("rating", "What rating would you give it out of 10?", 1.0),
        ],
        "outcomes": ["audience rating (IMDb, Letterboxd)", "opening box office", "streaming completion"],
    },
    "series": {
        "noun": "series",
        "experience": "You hear about the series and watch the first episode as described.",
        "dimensions": [
            _d("start_intent", "How likely are you to start watching?", 1.5),
            _d("continue_intent", "After episode one, how likely are you to keep watching?", 2.0),
            _d("enjoyment", "How much do you enjoy it?", 1.0),
            _d("recommend", "How likely are you to recommend it?", 1.5),
        ],
        "outcomes": ["completion rate", "audience rating", "renewal"],
    },
    "article": {
        "noun": "article",
        "experience": "You see the headline in a feed or newsletter, decide whether to open it, and read it as far "
                      "as it keeps your attention.",
        "dimensions": [
            _d("click_intent", "How likely are you to open it from the headline?", 1.5),
            _d("read_depth", "How much of it would you read (0 = nothing, 10 = to the end)?", 1.5),
            _d("credibility", "How credible is it?", 1.0),
            _d("value", "How valuable or interesting is it to you?", 1.0),
            _d("share_intent", "How likely are you to share it?", 1.5),
        ],
        "outcomes": ["click-through rate", "read time", "shares"],
    },
    "book": {
        "noun": "book",
        "experience": "You see the cover, blurb and opening pages and decide whether to read on.",
        "dimensions": [
            _d("buy_intent", "How likely are you to buy or borrow it?", 2.0),
            _d("finish_intent", "How likely are you to finish it?", 1.0),
            _d("enjoyment", "How much do you enjoy what you read?", 1.5),
            _d("recommend", "How likely are you to recommend it?", 1.5),
        ],
        "outcomes": ["sales", "reader rating"],
    },
    "music": {
        "noun": "track",
        "experience": "The track comes up in a playlist; you listen based on the lyrics, description and style.",
        "dimensions": [
            _d("skip_risk_inverse", "How likely are you to keep listening past 30 seconds?", 1.5),
            _d("replay_intent", "How likely are you to play it again?", 2.0),
            _d("enjoyment", "How much do you enjoy it?", 1.0),
            _d("share_intent", "How likely are you to share or add it to a playlist?", 1.5),
        ],
        "outcomes": ["skip rate", "saves", "streams"],
    },
    "game": {
        "noun": "game",
        "experience": "You see the store page and trailer, then imagine the first hour of play as described.",
        "dimensions": [
            _d("try_intent", "How likely are you to try it?", 1.5),
            _d("buy_intent", "How likely are you to buy it at the stated price?", 1.5),
            _d("fun", "How fun does it look?", 1.5),
            _d("retention", "How likely are you to still play after two weeks?", 1.5),
            _d("recommend", "How likely are you to recommend it?", 1.0),
        ],
        "outcomes": ["wishlists", "review score", "D14 retention"],
    },
    "product": {
        "noun": "product",
        "experience": "You discover the product (page, packaging, price, reviews) as you normally would.",
        "dimensions": [
            _d("need_fit", "How well does it fit a real need of yours?", 1.5),
            _d("price_ok", "How acceptable is the price?", 1.0),
            _d("buy_intent", "How likely are you to buy it in the next month?", 2.0),
            _d("trust", "How much do you trust the brand and claims?", 1.0),
            _d("recommend", "How likely are you to recommend it?", 1.0),
        ],
        "outcomes": ["conversion", "sales", "return rate", "review score"],
    },
    "pricing": {
        "noun": "pricing change",
        "experience": "You learn about the new price or plan structure as a current or potential customer.",
        "dimensions": [
            _d("fairness", "How fair does the new pricing feel?", 1.5),
            _d("stay_intent", "How likely are you to stay or buy at this price?", 2.0),
            _d("upgrade_intent", "How likely are you to choose a higher plan?", 1.0),
            _d("complain_intent_inverse", "How unlikely are you to complain publicly? (10 = will not complain)", 1.0),
        ],
        "outcomes": ["churn", "conversion", "ARPU"],
    },
    "ad": {
        "noun": "ad",
        "experience": "The ad interrupts your feed or video; you react as you really would.",
        "dimensions": [
            _d("attention", "How much does it grab your attention?", 1.5),
            _d("brand_recall", "How likely are you to remember the brand tomorrow?", 1.0),
            _d("liking", "How much do you like the ad?", 1.0),
            _d("click_intent", "How likely are you to click or look up the product?", 2.0),
        ],
        "outcomes": ["CTR", "view-through rate", "brand lift"],
    },
    "pitch": {
        "noun": "pitch",
        "experience": "You review the pitch deck or memo as the intended reader (investor, buyer, jury).",
        "dimensions": [
            _d("clarity", "How clear is the opportunity?", 1.0),
            _d("credibility", "How credible are the team and claims?", 1.5),
            _d("excitement", "How excited are you?", 1.0),
            _d("next_step_intent", "How likely are you to take the next step (meeting, purchase, vote)?", 2.0),
        ],
        "outcomes": ["meetings booked", "deal closed", "funding raised"],
    },
    "job_offer": {
        "noun": "job posting",
        "experience": "You read the job posting as a candidate who could fit the role.",
        "dimensions": [
            _d("attractiveness", "How attractive is the job?", 1.5),
            _d("clarity", "How clear is the role?", 0.5),
            _d("apply_intent", "How likely are you to apply?", 2.0),
            _d("employer_trust", "How much do you trust this employer?", 1.0),
        ],
        "outcomes": ["applications", "qualified applicant rate", "acceptance rate"],
    },
    "policy": {
        "noun": "policy or announcement",
        "experience": "You learn about the decision as someone it affects.",
        "dimensions": [
            _d("support", "How much do you support it?", 2.0),
            _d("fairness", "How fair is it?", 1.0),
            _d("trust", "How much do you trust the people behind it?", 1.0),
            _d("comply_intent", "How likely are you to go along with it rather than resist?", 1.5),
        ],
        "outcomes": ["approval rate", "compliance", "protest volume"],
    },
    "event": {
        "noun": "event or scenario",
        "experience": "You consider the event as described and how it would affect you.",
        "dimensions": [
            _d("belief", "How likely do you think it is to happen as described?", 1.0),
            _d("approval", "How much would you welcome it?", 1.0),
            _d("engagement", "How likely are you to act because of it (attend, buy, vote, talk about it)?", 2.0),
        ],
        "outcomes": ["attendance", "turnout", "outcome happened (yes/no)"],
    },
    "generic": {
        "noun": "subject",
        "experience": "You encounter the subject as described, in the context where you would normally meet it.",
        "dimensions": GENERIC_DIMS,
        "outcomes": ["any metric you can measure later"],
    },
}


def get_subject(kind: str) -> Dict[str, Any]:
    kind = (kind or "generic").lower().replace("-", "_")
    aliases = {"film": "movie", "site": "website", "landing": "website", "landing_page": "website",
               "blog": "article", "post": "article", "newsletter": "article", "show": "series", "tv": "series",
               "song": "music", "album": "music", "podcast": "video", "trailer": "movie", "deck": "pitch",
               "job": "job_offer", "price": "pricing", "announcement": "policy", "campaign": "ad"}
    kind = aliases.get(kind, kind)
    profile = dict(SUBJECTS.get(kind, SUBJECTS["generic"]))
    profile["kind"] = kind if kind in SUBJECTS else "generic"
    return profile


# For outcomes rated by the people who chose the subject (CinemaScore, Steam or app-store reviews, Goodreads),
# appeal to everyone is the wrong target: predict how satisfied the choosers are. (selection dims, satisfaction dims)
SATISFACTION = {
    "movie": (["watch_intent"], ["enjoyment", "recommend", "rating"]),
    "series": (["start_intent"], ["enjoyment", "continue_intent", "recommend"]),
    "game": (["buy_intent", "try_intent"], ["fun", "retention", "recommend"]),
    "book": (["buy_intent"], ["enjoyment", "finish_intent", "recommend"]),
    "video": (["click_intent"], ["retention", "enjoyment"]),
    "app": (["install_intent"], ["usefulness", "ease", "retention", "recommend"]),
    "product": (["buy_intent"], ["need_fit", "trust", "recommend"]),
}


def _norm01(value: float, d: Dict[str, Any]) -> float:
    return (value - d["min"]) / (d["max"] - d["min"]) if d["max"] > d["min"] else 0.0


def satisfaction(rows: List[Dict[str, Any]], dims: List[Dict[str, Any]], kind: str) -> Optional[float]:
    """Satisfaction of those who would choose it (0-100): each respondent's satisfaction dimensions, weighted by the
    people it stands for times its selection intent. rows: [{"weight": w, "scores": {key: value}}]."""
    if kind not in SATISFACTION:
        return None
    sel_keys, sat_keys = SATISFACTION[kind]
    by_key = {d["key"]: d for d in dims}
    num = den = 0.0
    for r in rows:
        sc = r["scores"]
        sats = [_norm01(sc[k], by_key[k]) for k in sat_keys if k in sc and k in by_key]
        sels = [_norm01(sc[k], by_key[k]) for k in sel_keys if k in sc and k in by_key]
        if not sats or r["weight"] <= 0:
            continue
        w = r["weight"] * max(0.05, sum(sels) / len(sels) if sels else 0.5)
        num += w * sum(sats) / len(sats)
        den += w
    return round(100 * num / den, 1) if den else None


def kinds() -> List[str]:
    return sorted(SUBJECTS)


def default_probes(kind: str, n: int = 2) -> List[Dict[str, Any]]:
    """Highest-weight dimensions as private probes for the social simulation."""
    dims = sorted(get_subject(kind)["dimensions"], key=lambda d: -d["weight"])[:n]
    return [{"key": d["key"], "question": d["question"], "min": d["min"], "max": d["max"]} for d in dims]


def potential(scores: Dict[str, float], dims: List[Dict[str, Any]]) -> float:
    """Weighted mean of dimensions, normalised to 0-100."""
    total = weight = 0.0
    for d in dims:
        if scores.get(d["key"]) is None:
            continue
        span = (d["max"] - d["min"]) or 1
        total += d["weight"] * (scores[d["key"]] - d["min"]) / span * 100
        weight += d["weight"]
    return round(total / weight, 2) if weight else 0.0
