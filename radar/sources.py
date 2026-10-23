"""取文层：OpenAlex（主）+ Crossref（配额兜底）+ arXiv（摘要回填与预印本）。

只负责拿真实元数据，不做相关性打分。
"""
import html as _html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

UA = {"User-Agent": "paper-radar/1.0 (academic workbench; mailto:setup@local)", "Accept": "*/*"}

OPENALEX_SELECT = (
    "id,doi,title,publication_date,authorships,primary_location,best_oa_location,"
    "cited_by_count,abstract_inverted_index,type"
)


def http_get(url, timeout=40, retries=4):
    """先直连，失败再走系统代理。429 按 Retry-After 退避（封顶 20s，避免睡到配额重置）。"""
    last = None
    for attempt in range(retries):
        broke_on_429 = False
        for handler in (urllib.request.ProxyHandler({}), None):
            try:
                opener = urllib.request.build_opener(*([] if handler is None else [handler]))
                req = urllib.request.Request(url, headers=UA)
                return opener.open(req, timeout=timeout).read()
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (404, 400, 403):
                    raise
                if e.code == 429:
                    wait = 0
                    try:
                        wait = int(e.headers.get("Retry-After") or 0)
                    except (TypeError, ValueError):
                        wait = 0
                    time.sleep(min(max(wait, 3 + attempt * 4), 20))
                    broke_on_429 = True
                    break
            except Exception as e:  # noqa: BLE001
                last = e
        if not broke_on_429:
            time.sleep(1.5 * (attempt + 1))
    raise last


def _get_json(url, timeout=40, retries=4):
    return json.loads(http_get(url, timeout=timeout, retries=retries).decode("utf-8"))


def reconstruct_abstract(inv):
    if not inv:
        return ""
    positions = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def _clean(text):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = _html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


VENUE_BY_ISSN = {}
VENUE_BY_OAID = {}

# OpenAlex 免费额度按 IP 共享，配额易耗尽；用户若在 config.json 填了 key 就用它
OA_KEY = [""]


def set_openalex_key(key):
    OA_KEY[0] = str(key or "").strip()


def _oa_params(extra=None):
    params = dict(extra or {})
    params["mailto"] = "setup@local"
    if OA_KEY[0]:
        params["api_key"] = OA_KEY[0]
    return params


def _venue_index(cfg):
    if not VENUE_BY_ISSN:
        for v in cfg["venues"]:
            if v.get("issn"):
                VENUE_BY_ISSN[v["issn"]] = v
            if v.get("openalex"):
                VENUE_BY_OAID[v["openalex"]] = v
    return VENUE_BY_ISSN, VENUE_BY_OAID


def _venue_filter(cfg, group):
    if group == "journals":
        ids = [v["issn"] for v in cfg["venues"] if v["kind"] == "journal" and v.get("issn")]
        if not ids:
            return ""
        return "primary_location.source.issn:" + "|".join(ids)
    ids = [v["openalex"] for v in cfg["venues"] if v["kind"] == "conference" and v.get("openalex")]
    if not ids:
        return ""
    return "primary_location.source.id:" + "|".join(ids)


def _probe_string(topic, limit=8):
    phrases = [k for k in topic["keywords"] if " " in k][:limit]
    singles = [k for k in topic["keywords"] if " " not in k and len(k) > 4][:2]
    return "|".join('"%s"' % t for t in (phrases + singles))


_OA_LOCK = threading.Lock()
_OA_LAST = [0.0]
OA_BLOCKED = [False]


def _oa_throttle(min_interval=0.5):
    with _OA_LOCK:
        gap = time.time() - _OA_LAST[0]
        if gap < min_interval:
            time.sleep(min_interval - gap)
        _OA_LAST[0] = time.time()


def openalex_available():
    """一次极轻的探测：配额耗尽时返回 False，整轮直接走兜底，不再挨个撞 429。"""
    if OA_BLOCKED[0]:
        return False
    try:
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(
            _oa_params({"per-page": "1", "select": "id"}))
        _get_json(url, timeout=20, retries=1)
        return True
    except urllib.error.HTTPError as e:
        if e.code == 429:
            OA_BLOCKED[0] = True
        return False
    except Exception:  # noqa: BLE001
        return False


def openalex_fetch(cfg, since, group, topic, per_page=200, errors=None):
    if OA_BLOCKED[0]:
        return []
    vf = _venue_filter(cfg, group)
    if not vf:
        return []
    flt = "%s,title_and_abstract.search:%s,from_publication_date:%s" % (vf, _probe_string(topic), since)
    params = _oa_params({
        "filter": flt, "per-page": str(per_page), "sort": "publication_date:desc",
        "select": OPENALEX_SELECT,
    })
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params, safe=':"|')
    try:
        _oa_throttle()
        return _get_json(url, timeout=60, retries=2).get("results") or []
    except urllib.error.HTTPError as e:
        if e.code == 429:
            OA_BLOCKED[0] = True  # 配额耗尽，本次运行全部改走兜底源
        if errors is not None:
            errors.append("openalex %s/%s: HTTP %s" % (group, topic["key"], e.code))
    except Exception as e:  # noqa: BLE001
        if errors is not None:
            errors.append("openalex %s/%s: %s" % (group, topic["key"], str(e)[:100]))
    return []


def normalize_openalex(w, cfg):
    by_issn, by_oaid = _venue_index(cfg)
    src = ((w.get("primary_location") or {}).get("source") or {})
    venue = None
    issns = src.get("issn") or []
    if isinstance(issns, str):
        issns = [issns]
    for i in issns:
        if i in by_issn:
            venue = by_issn[i]
            break
    if venue is None and src.get("id"):
        venue = by_oaid.get(src["id"].rsplit("/", 1)[-1])
    if venue is None:
        return None
    oa = w.get("best_oa_location") or {}
    doi = (w.get("doi") or "").replace("https://doi.org/", "")
    landing = (w.get("primary_location") or {}).get("landing_page_url") or (w.get("doi") or "")
    authors = []
    for a in (w.get("authorships") or [])[:12]:
        name = (a.get("author") or {}).get("display_name") or ""
        insts = a.get("institutions") or []
        if name:
            authors.append({"name": name, "affil": (insts[0].get("display_name") if insts else "") or ""})
    return {
        "id": "doi:" + doi.lower() if doi else "oa:" + w["id"].rsplit("/", 1)[-1],
        "title": _clean(w.get("title") or ""),
        "abstract": _clean(reconstruct_abstract(w.get("abstract_inverted_index"))),
        "authors": authors,
        "venue_key": venue["key"], "venue": venue["short"], "venue_full": venue["name"],
        "venue_weight": venue["weight"], "kind": venue["kind"], "publisher_kind": venue["kind"],
        "date": w.get("publication_date") or "", "citations": w.get("cited_by_count") or 0,
        "doi": doi, "url": landing, "pdf_url": oa.get("pdf_url") or "",
        "oa": bool(oa.get("is_oa")), "type": w.get("type") or "", "origin": "openalex",
    }


# ---------------- Crossref（OpenAlex 配额耗尽时的顶刊兜底） ----------------
def crossref_fetch_venue(cfg, since, venue, query, rows=80, errors=None):
    params = {
        "filter": "issn:%s,from-pub-date:%s" % (venue["issn"], since),
        "rows": rows, "sort": "published", "order": "desc",
        "query.bibliographic": query, "mailto": "setup@local",
        "select": "DOI,title,container-title,issued,published,author,is-referenced-by-count,abstract,type,URL,link",
    }
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    try:
        items = (_get_json(url, timeout=60).get("message") or {}).get("items") or []
    except Exception as e:  # noqa: BLE001
        if errors is not None:
            errors.append("crossref %s: %s" % (venue["key"], str(e)[:100]))
        return []
    out = []
    for it in items:
        if (it.get("type") or "") not in ("journal-article", "proceedings-article", "article"):
            continue
        doi = (it.get("DOI") or "").lower()
        title = _clean((it.get("title") or [""])[0])
        if not title:
            continue
        date = ""
        for key in ("published", "issued"):
            parts = ((it.get(key) or {}).get("date-parts") or [[None]])[0]
            if parts and parts[0]:
                y = str(parts[0])
                m = str(parts[1]).zfill(2) if len(parts) > 1 and parts[1] else "01"
                d = str(parts[2]).zfill(2) if len(parts) > 2 and parts[2] else "01"
                date = "%s-%s-%s" % (y, m, d)
                break
        authors = []
        for a in (it.get("author") or [])[:12]:
            nm = ("%s %s" % (a.get("given") or "", a.get("family") or "")).strip()
            aff = ""
            for af in (a.get("affiliation") or []):
                if af.get("name"):
                    aff = af["name"]
                    break
            if nm:
                authors.append({"name": nm, "affil": aff})
        out.append({
            "id": "doi:" + doi if doi else "cr:" + title.lower()[:60],
            "title": title, "abstract": _clean(it.get("abstract") or ""),
            "authors": authors,
            "venue_key": venue["key"], "venue": venue["short"], "venue_full": venue["name"],
            "venue_weight": venue["weight"], "kind": venue["kind"], "publisher_kind": venue["kind"],
            "date": date, "citations": it.get("is-referenced-by-count") or 0, "doi": doi,
            "url": it.get("URL") or ("https://doi.org/" + doi if doi else ""),
            "pdf_url": "", "oa": False, "type": it.get("type") or "",
            "origin": "crossref",
        })
    return out


# ---------------- arXiv ----------------
ARXIV_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def arxiv_fetch(topic, max_results=40, errors=None):
    query = topic.get("arxiv") or ""
    if not query:
        return []
    params = {"search_query": query, "start": 0, "max_results": max_results,
              "sortBy": "submittedDate", "sortOrder": "descending"}
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    try:
        root = ET.fromstring(http_get(url, timeout=60))
    except Exception as e:  # noqa: BLE001
        if errors is not None:
            errors.append("arxiv/%s: %s" % (topic["key"], str(e)[:100]))
        return []
    out = []
    for e in root.findall("a:entry", ARXIV_NS):
        aid = (e.findtext("a:id", default="", namespaces=ARXIV_NS) or "").strip()
        m = re.search(r"abs/([^v]+)v?\d*$", aid)
        arxid = m.group(1) if m else aid.rsplit("/", 1)[-1]
        cats = [c.get("term") for c in e.findall("a:category", ARXIV_NS)]
        authors = [
            {"name": (a.findtext("a:name", default="", namespaces=ARXIV_NS) or "").strip(), "affil": ""}
            for a in e.findall("a:author", ARXIV_NS)
        ]
        out.append({
            "id": "arxiv:" + arxid,
            "title": _clean(e.findtext("a:title", default="", namespaces=ARXIV_NS)),
            "abstract": _clean(e.findtext("a:summary", default="", namespaces=ARXIV_NS)),
            "authors": [a for a in authors if a["name"]],
            "venue_key": "arXiv", "venue": "arXiv",
            "venue_full": "arXiv preprint (%s)" % (cats[0] if cats else "cs"),
            "venue_weight": 0.55, "kind": "preprint", "publisher_kind": "preprint",
            "date": (e.findtext("a:published", default="", namespaces=ARXIV_NS) or "")[:10],
            "citations": 0,
            "doi": (e.findtext("arxiv:doi", default="", namespaces=ARXIV_NS) or "").strip(),
            "url": "https://arxiv.org/abs/" + arxid,
            "pdf_url": "https://arxiv.org/pdf/" + arxid,
            "oa": True, "type": "preprint", "origin": "arxiv", "categories": cats,
        })
    return out


def _norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def arxiv_abstracts_by_titles(titles, timeout=80):
    """一次请求里 OR 多个标题，拿回 {规范化标题: {abstract, pdf_url}}。"""
    titles = [t for t in titles if t][:6]
    if not titles:
        return {}
    q = " OR ".join('ti:"%s"' % re.sub(r'["\\]', " ", t)[:150] for t in titles)
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": q, "max_results": 30})
    try:
        root = ET.fromstring(http_get(url, timeout=timeout))
    except Exception:  # noqa: BLE001
        return {}
    got = {}
    for e in root.findall("a:entry", ARXIV_NS):
        t = _norm_title(e.findtext("a:title", default="", namespaces=ARXIV_NS))
        if not t:
            continue
        aid = (e.findtext("a:id", default="", namespaces=ARXIV_NS) or "").strip()
        m = re.search(r"abs/([^v]+)v?\d*$", aid)
        arxid = m.group(1) if m else ""
        got[t] = {
            "abstract": _clean(e.findtext("a:summary", default="", namespaces=ARXIV_NS)),
            "pdf_url": "https://arxiv.org/pdf/" + arxid if arxid else "",
            "arxiv_id": arxid,
        }
    return got


def arxiv_abstract_by_title(title, timeout=40):
    """Crossref 题录没有摘要时，用标题去 arXiv 找同一篇（预印本版本）并取摘要。"""
    q = 'ti:"%s"' % re.sub(r'["\\]', " ", title)[:180]
    url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
        {"search_query": q, "max_results": 3})
    try:
        root = ET.fromstring(http_get(url, timeout=timeout))
    except Exception:  # noqa: BLE001
        return None
    want = _norm_title(title)
    for e in root.findall("a:entry", ARXIV_NS):
        got = _norm_title(e.findtext("a:title", default="", namespaces=ARXIV_NS))
        if not got:
            continue
        # 标题要高度相似，避免张冠李戴
        if got == want or (len(want) > 20 and (want in got or got in want)):
            aid = (e.findtext("a:id", default="", namespaces=ARXIV_NS) or "").strip()
            m = re.search(r"abs/([^v]+)v?\d*$", aid)
            arxid = m.group(1) if m else ""
            return {"abstract": _clean(e.findtext("a:summary", default="", namespaces=ARXIV_NS)),
                    "pdf_url": "https://arxiv.org/pdf/" + arxid if arxid else "",
                    "arxiv_id": arxid}
    return None


JUNK_TITLE = re.compile(
    r"^\s*(correction|corrigendum|erratum|retraction|retracted|comment on|reply to|"
    r"response to|editorial|guest editorial|expression of concern|book review|"
    r"table of contents|front cover|call for papers|corrections to)\b", re.I)
JUNK_TYPE = {"paratext", "editorial", "erratum", "retraction", "peer-review", "letter", "other"}


def is_research_item(p, min_abstract=120):
    if not p or not p.get("title"):
        return False
    if JUNK_TITLE.search(p["title"]):
        return False
    if (p.get("type") or "") in JUNK_TYPE:
        return False
    if len(p.get("abstract") or "") < min_abstract:
        return False
    return True


def fetch_all(cfg, window_days, errors=None):
    """返回 (papers, stats)。OpenAlex 优先；配额耗尽时自动切换 Crossref + arXiv 回填。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .config import daterange_back

    since = daterange_back(window_days)
    set_openalex_key(cfg.get("openalex_api_key"))
    floor_date = cfg.get("min_pub_date") or ""
    if floor_date and since < floor_date:
        since = floor_date
    errors = errors if errors is not None else []
    seen, papers, stats = set(), [], {}

    def add(items, tag, need_abstract=True):
        n = 0
        for p in items:
            if not is_research_item(p, 120 if need_abstract else 10):
                continue
            if floor_date and (p.get("date") or "") < floor_date:
                continue
            key = (p.get("doi") or p["title"]).lower()
            norm = re.sub(r"[^a-z0-9]+", "", p["title"].lower())[:80]
            if key in seen or norm in seen:
                continue
            seen.add(key)
            seen.add(norm)
            papers.append(p)
            n += 1
        stats[tag] = stats.get(tag, 0) + n
        return n

    oa_ok = openalex_available()
    if not oa_ok:
        errors.append("OpenAlex 配额不可用（429），本轮直接使用 Crossref + arXiv")

    jobs = []
    for topic in cfg["topics"]:
        if not oa_ok:
            break
        for group in ("journals", "conferences"):
            jobs.append((
                "openalex_%s_%s" % (group, topic["key"]),
                lambda g=group, t=topic: [normalize_openalex(w, cfg)
                                          for w in openalex_fetch(cfg, since, g, t, errors=errors)],
            ))
    for topic in cfg["topics"]:
        jobs.append(("arxiv_%s" % topic["key"], lambda t=topic: arxiv_fetch(t, errors=errors)))

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fn): tag for tag, fn in jobs}
        for fut in as_completed(futures):
            try:
                add(fut.result(), futures[fut])
            except Exception as e:  # noqa: BLE001
                errors.append("fetch/%s: %s" % (futures[fut], str(e)[:100]))

    oa_total = sum(v for k, v in stats.items() if k.startswith("openalex"))
    if oa_total == 0:
        # ---- 兜底：Crossref 取顶刊题录 ----
        stats["fallback"] = "crossref"
        errors.append("OpenAlex 不可用（配额耗尽/限流），本次改用 Crossref + arXiv 回填")
        journals = [v for v in cfg["venues"] if v["kind"] == "journal" and v.get("issn")]
        raw = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(crossref_fetch_venue, cfg, since, v,
                            " ".join([k for k in t["keywords"] if " " in k][:5]), 60, errors)
                for t in cfg["topics"] for v in journals
            ]
            for fut in as_completed(futures):
                try:
                    raw.extend(fut.result())
                except Exception as e:  # noqa: BLE001
                    errors.append("crossref: %s" % str(e)[:100])
        stats["crossref_raw"] = len(raw)

        # 摘要回填：先按主题相关度挑前 40 条，再去 arXiv 找同篇摘要
        from .rank import score_topics

        scored = []
        for p in raw:
            best, sc, _ = score_topics(p, cfg["topics"])
            if best and sc >= cfg.get("min_topic_score", 3.0):
                p["_topic"] = best
                scored.append((sc, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        todo = [p for _, p in scored[:30] if len(p.get("abstract") or "") < 120]
        filled = 0
        batches = [todo[i:i + 6] for i in range(0, len(todo), 6)]
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(arxiv_abstracts_by_titles, [p["title"] for p in b]): b for b in batches}
            for fut in as_completed(futures):
                found = fut.result() or {}
                if not found:
                    continue
                for p in futures[fut]:
                    want = _norm_title(p["title"])
                    hit = found.get(want)
                    if not hit:
                        for k, v in found.items():
                            if len(want) > 20 and (want in k or k in want):
                                hit = v
                                break
                    if hit and len(hit.get("abstract") or "") > 120:
                        p["abstract"] = hit["abstract"]
                        p["pdf_url"] = hit.get("pdf_url") or ""
                        filled += 1
        stats["abstract_backfilled"] = filled
        add([p for _, p in scored], "crossref_journals")

    return papers, stats
