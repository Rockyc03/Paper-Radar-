"""打分与选题：主题相关度 + venue 权重 + 时效 + 引用，再按主题配额轮流取。"""
import math
import re
from datetime import date


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower())


def score_topics(paper, topics):
    title = _norm(paper.get("title"))
    abstract = _norm(paper.get("abstract"))
    scores = {}
    matched = {}
    for t in topics:
        s, hits = 0.0, []
        for kw in t["keywords"]:
            k = kw.lower()
            in_title = k in title
            in_abs = k in abstract
            if in_title:
                s += 3.0
                hits.append(kw)
            elif in_abs:
                s += 1.0
                hits.append(kw)
        if s > 0:
            scores[t["key"]] = s * t.get("weight", 1.0)
            matched[t["key"]] = hits
    if not scores:
        return None, 0.0, []
    best = max(scores, key=lambda k: scores[k])
    return best, scores[best], matched[best]


def composite(paper, topic_score, today=None, prefer_days=1095):
    """相关度 + venue + 时效 + 引用。时效按梯度给分，近五年内越新越靠前。"""
    today = today or date.today()
    try:
        d = date.fromisoformat(paper.get("date") or today.isoformat())
    except ValueError:
        d = today
    age = max(0, (today - d).days)
    if age <= 90:
        recency = 3.0
    elif age <= 365:
        recency = 2.4
    elif age <= 730:
        recency = 1.6
    elif age <= prefer_days:
        recency = 1.0
    else:
        recency = 0.2
    cites = math.log1p(paper.get("citations") or 0)
    return round(topic_score * 1.2 + paper.get("venue_weight", 0.5) * 2.0 + recency + cites, 3)


def annotate(papers, cfg, seen_ids=None, min_score=None):
    seen_ids = seen_ids or set()
    min_score = cfg.get("min_topic_score", 2.0) if min_score is None else min_score
    out = []
    for p in papers:
        if p["id"] in seen_ids:
            continue
        best, sc, hits = score_topics(p, cfg["topics"])
        if best is None or sc < min_score:
            continue
        p = dict(p)
        p["topic"] = best
        p["topic_name"] = next(t["name"] for t in cfg["topics"] if t["key"] == best)
        p["topic_score"] = round(sc, 2)
        p["matched"] = hits[:6]
        p["composite"] = composite(p, sc, prefer_days=cfg.get("prefer_recent_days", 1095))
        out.append(p)
    out.sort(key=lambda x: x["composite"], reverse=True)
    return out


def _take(pool, chosen, cfg, cap, preprint_cap, picked_ids):
    for p in pool:
        if len(chosen) >= cap:
            return
        if p["id"] in picked_ids:
            continue
        if sum(1 for c in chosen if c["venue_key"] == p["venue_key"]) >= cfg.get("paper_per_venue_cap", 3):
            continue
        if p["kind"] == "preprint" and sum(1 for c in chosen if c["kind"] == "preprint") >= preprint_cap:
            continue
        chosen.append(p)
        picked_ids.add(p["id"])


def select_daily(papers, cfg, target=None, seen_ids=None):
    """按主题配额轮流取，避免某一天全被一个主题占满；不够再全局补。"""
    target = target or cfg.get("daily_target", 10)
    pool = annotate(papers, cfg, seen_ids=seen_ids)
    if not pool:
        return [], {}

    by_topic = {}
    for p in pool:
        by_topic.setdefault(p["topic"], []).append(p)

    topic_keys = [t["key"] for t in cfg["topics"] if by_topic.get(t["key"])]
    base = target // max(1, len(topic_keys))
    remainder = target - base * len(topic_keys)
    order = sorted(topic_keys, key=lambda k: len(by_topic[k]), reverse=True)

    chosen, picked = [], set()
    preprint_cap = max(2, int(round(target * 0.4)))

    for i, k in enumerate(order):
        quota = target - len(chosen) if i == len(order) - 1 else base + (1 if i < remainder else 0)
        _take(by_topic[k][: quota * 4], chosen, cfg, min(target, len(chosen) + quota), preprint_cap, picked)

    if len(chosen) < target:
        _take(pool, chosen, cfg, target, max(preprint_cap, target - len(chosen)), picked)

    stats = {
        "pool": len(pool),
        "by_topic": {k: sum(1 for c in chosen if c["topic"] == k) for k in by_topic},
        "preprints": sum(1 for c in chosen if c["kind"] == "preprint"),
    }
    return chosen[:target], stats
