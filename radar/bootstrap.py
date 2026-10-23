"""首次配置用的解析器：把用户口语化的回答变成 config.json 需要的结构。

三件事：
  1. 方向名 → 英文关键词 + arXiv 查询串（调模型展开）
  2. 期刊名 → ISSN（查 Crossref）
  3. 会议名 → OpenAlex source id（查 OpenAlex）

全部带回退：网络/模型不可用时给出可手动填写的提示，而不是直接崩。
"""
import json
import re
import urllib.parse

from .sources import _get_json

TOPIC_SYSTEM = """你是学术文献检索专家。用户会给你一个研究方向的名字（可能是中文，可能口语化）。
请把它变成一组用于检索英文论文的配置，只输出 JSON。

字段要求：
- key：3-12 个字符的英文小写标识，单词用下划线连接，例如 multi_sensor_fusion
- name：方向中文名，保留用户原意，20 字以内
- short：2-4 个汉字，用于图表里做标签
- keywords：18-30 个英文检索词，用于匹配论文标题与摘要。要求：
    * 从宽泛词到具体词排序，前几个要宽（保证召回），后面补细节（保证精度）
    * 优先多词短语（"control barrier function"），少量单词（仅当它本身就是术语，如 stackelberg）
    * 覆盖同义写法与常见缩写，例如 control barrier function / CBF
    * 全部小写，不带标点
- arxiv：一段 arXiv API 的 search_query 表达式，形如 (abs:"control barrier function" OR abs:"safe control")，
    6-10 个 abs 条件，只用 OR 连接，全部用英文
- why：一句话说明你理解的这个方向（中文，30 字以内）

只输出 JSON 对象，不要解释，不要 markdown 代码块。"""

TOPIC_USER = """研究方向：{name}
{extra}
请输出 JSON。"""


def _slug(text):
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return s[:24] or "topic"


def _clean_kw(k):
    k = str(k or "").strip().strip('"').strip("'").lower()
    k = re.sub(r"\s+", " ", k)
    return k


def _clean_arxiv(q):
    q = str(q or "").strip()
    if not q:
        return ""
    if not q.startswith("("):
        q = "(" + q + ")"
    return q


def arxiv_from_keywords(keywords, limit=8):
    """没有模型时，用关键词自己拼一条 arXiv 查询串。"""
    parts = []
    for k in keywords or []:
        k = re.sub(r'["\\]', " ", str(k or "").strip())
        if not k:
            continue
        parts.append('abs:"%s"' % k)
        if len(parts) >= limit:
            break
    return "(" + " OR ".join(parts) + ")" if parts else ""


def expand_topic(llm, name, extra="", index=0):
    """方向名 → {key, name, short, keywords, arxiv, why}。llm 为空则返回最小可用结构。"""
    name = str(name or "").strip()
    if not name:
        raise ValueError("方向名不能为空")

    if llm is None:
        return {"key": "topic%d" % (index + 1), "name": name, "short": name[:4],
                "keywords": [], "arxiv": "", "why": "",
                "manual": True}

    system = TOPIC_SYSTEM
    user = TOPIC_USER.format(name=name, extra=("补充说明：%s\n" % extra) if extra else "")
    data, _usage = llm.chat_json(system, user, max_tokens=2000, temperature=0.2)
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        raise ValueError("模型返回的不是 JSON 对象")

    keywords = []
    for k in (data.get("keywords") or []):
        k = _clean_kw(k)
        if k and k not in keywords:
            keywords.append(k)
    if not keywords:
        raise ValueError("模型没有给出关键词")

    key = _slug(data.get("key") or name)
    if re.match(r"^topic\d+$", key):
        key = "topic%d" % (index + 1)
    short = str(data.get("short") or name)[:6]
    return {
        "key": key,
        "name": str(data.get("name") or name).strip()[:40],
        "short": short,
        "keywords": keywords[:30],
        "arxiv": _clean_arxiv(data.get("arxiv")),
        "why": str(data.get("why") or "").strip()[:80],
        "manual": False,
    }


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def resolve_journal(name):
    """期刊名 → {key, name, short, kind, issn, weight}。查不到返回 None。"""
    q = str(name or "").strip()
    if not q:
        return None
    url = "https://api.crossref.org/journals?" + urllib.parse.urlencode({"query": q, "rows": 5})
    try:
        items = (_get_json(url, timeout=30, retries=2).get("message") or {}).get("items") or []
    except Exception:  # noqa: BLE001
        return None

    want = _norm(q)
    best, best_score = None, 0.0
    for it in items:
        title = (it.get("title") or "").strip()
        issns = it.get("ISSN") or []
        if isinstance(issns, str):
            issns = [issns]
        if not title or not issns:
            continue
        got = _norm(title)
        score = 0.0
        if got == want:
            score = 3.0
        elif want and (want in got or got in want):
            score = 2.0
        else:
            # 词重叠度
            a, b = set(want.split()), set(got.split())
            if not a:
                continue
            score = len(a & b) / float(len(a))
        if score > best_score:
            best, best_score = (title, issns), score
    if not best or best_score < 0.5:
        return None

    title, issns = best
    short = _short_title(title)
    return {"key": _slug(short) or _slug(title), "name": title, "short": short,
            "kind": "journal", "issn": issns[0], "weight": 1.0}


def _short_title(title):
    """IEEE Transactions on Automatic Control → TAC 之类；其余取首字母缩写。"""
    t = title.strip()
    m = re.search(r"transactions? on (.+)", t, re.I)
    if m:
        words = [w for w in re.findall(r"[A-Za-z]+", m.group(1)) if w.lower() not in
                 ("of", "and", "the", "on", "for", "in")]
        abbr = "".join(w[0].upper() for w in words)[:4]
        if len(abbr) == 1:
            return "T-" + abbr
        return ("T" + abbr) if len(abbr) <= 3 else abbr
    words = [w for w in re.findall(r"[A-Za-z]+", t) if w.lower() not in
             ("of", "and", "the", "on", "for", "in", "a")]
    if len(words) >= 2 and all(w[0].isupper() for w in words[:3]):
        return "".join(w[0] for w in words)[:4]
    return " ".join(words[:2])[:12] or t[:12]


def resolve_conference(name, api_key=""):
    """会议名 → {key, name, short, kind, openalex, weight}。OpenAlex 不可用时返回 None。"""
    q = str(name or "").strip()
    if not q:
        return None
    params = {"search": q, "per-page": 5, "select": "id,display_name,type,works_count"}
    if api_key:
        params["api_key"] = api_key
    url = "https://api.openalex.org/sources?" + urllib.parse.urlencode(params)
    try:
        items = _get_json(url, timeout=30, retries=1).get("results") or []
    except Exception:  # noqa: BLE001
        return None

    want = _norm(q)
    best, best_score = None, 0.0
    for it in items:
        title = it.get("display_name") or ""
        oid = (it.get("id") or "").rsplit("/", 1)[-1]
        if not title or not oid:
            continue
        got = _norm(title)
        a, b = set(want.split()), set(got.split())
        score = len(a & b) / float(len(a)) if a else 0.0
        if want and (want in got or got in want):
            score = max(score, 0.8)
        if score > best_score:
            best, best_score = (title, oid), score
    if not best or best_score < 0.5:
        return None

    title, oid = best
    words = [w for w in re.findall(r"[A-Za-z]+", title)
             if w.lower() not in ("on", "of", "and", "the", "for", "in", "a", "ieee", "acm")]
    short = "".join(w[0].upper() for w in words[:5])[:6] or _slug(title)[:6].upper()
    return {"key": short, "name": title, "short": short, "kind": "conference",
            "openalex": oid, "weight": 0.9}


def parse_venue_arg(raw, api_key="", errors=None):
    """支持三种写法：期刊/会议名、"issn:0018-9286"、"S4210221977"。"""
    s = str(raw or "").strip()
    if not s:
        return None
    low = s.lower()
    if re.match(r"^\d{4}-?\d{3}[\dXx]$", s.strip()):
        s = "issn:" + s.strip()
        low = s.lower()
    if low.startswith("issn:"):
        issn = s.split(":", 1)[1].strip()
        return {"key": issn.replace("-", "")[-4:], "name": "ISSN " + issn, "short": issn,
                "kind": "journal", "issn": issn, "weight": 1.0}
    if re.match(r"^s\d{6,}$", low):
        return {"key": s.upper()[:6], "name": "OpenAlex " + s.upper(), "short": s.upper()[:6],
                "kind": "conference", "openalex": s.upper(), "weight": 0.9}

    hit = resolve_journal(s)
    if hit:
        return hit
    hit = resolve_conference(s, api_key=api_key)
    if hit:
        return hit
    if errors is not None:
        errors.append(s)
    return None


def infer_kind(name):
    low = str(name or "").lower()
    return "conference" if any(w in low for w in ("conference", "conf.", "workshop", "symposium",
                                                  "proceedings", "meeting")) else "journal"


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)
