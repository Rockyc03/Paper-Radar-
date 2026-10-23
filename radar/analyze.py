"""六问速读：每篇论文一次 LLM 调用，产出「解决的问题/数据方法/关键结果/创新位置/遗留问题/后续方向」。"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import audience as audience_of
from .config import load_prompt_pair
from .llm import LLM

REQUIRED = [
    "problem", "data_method", "key_result", "innovation",
    "open_problems", "next_steps", "influence", "influence_reason", "tags",
]


def paper_payload(p, max_authors=6):
    authors = p.get("authors") or []
    names = ", ".join(a["name"] for a in authors[:max_authors])
    if len(authors) > max_authors:
        names += " et al."
    return {
        "标题": p.get("title"),
        "中文题名参考": "（请自行准确翻译）",
        "venue": p.get("venue_full"),
        "类型": {"journal": "期刊论文", "conference": "会议论文", "preprint": "预印本（未经同行评议）"}.get(p.get("kind"), p.get("kind")),
        "发表日期": p.get("date"),
        "作者": names,
        "引用数": p.get("citations"),
        "DOI": p.get("doi") or "无",
        "摘要": (p.get("abstract") or "摘要未披露")[:4000],
        "命中主题": p.get("topic_name"),
    }


TEXT_KEYS = {"title_zh", "gist", "problem", "data_method", "key_result", "innovation",
             "open_problems", "next_steps", "influence_reason", "relevance_note"}


def _clean_answer(d):
    out = {}
    for k, v in d.items():
        if isinstance(v, list) and k in TEXT_KEYS:
            out[k] = "；".join(str(x).strip() for x in v if str(x).strip())
        elif isinstance(v, list):
            out[k] = [str(x).strip() for x in v if str(x).strip()]
        elif isinstance(v, (int, float)):
            out[k] = v
        else:
            out[k] = str(v or "").strip()
    return out


def analyze_one(llm, cfg, paper, retries=2):
    system, user = load_prompt_pair(
        "sixq.md",
        field=cfg["field_name"],
        audience=audience_of(cfg),
        paper=json.dumps(paper_payload(paper), ensure_ascii=False, indent=2),
    )
    last_err = None
    for attempt in range(retries):
        try:
            data, usage = llm.chat_json(system, user, model=llm.models.get("analysis"),
                                        max_tokens=6000, temperature=0.25)
            data = _clean_answer(data)
            missing = [k for k in REQUIRED if not data.get(k)]
            if missing:
                raise ValueError("缺字段: %s" % ",".join(missing))
            try:
                data["influence"] = max(1, min(10, int(float(data["influence"]))))
            except (TypeError, ValueError):
                data["influence"] = 5
            if not isinstance(data.get("tags"), list):
                data["tags"] = [str(data.get("tags"))] if data.get("tags") else []
            data["_usage"] = usage
            return data, None
        except Exception as e:  # noqa: BLE001
            last_err = str(e)[:200]
            system = system  # 保持提示词不变，仅重试
    return {
        "problem": "生成失败：%s" % last_err,
        "data_method": "", "key_result": "", "innovation": "", "open_problems": "",
        "next_steps": "", "influence": 0, "influence_reason": "", "tags": [],
        "title_zh": "", "gist": "", "_error": last_err,
    }, last_err


def analyze_many(llm, cfg, papers, on_progress=None):
    results = [None] * len(papers)
    workers = max(1, int(llm.conf.get("max_concurrency", 3)))
    failures = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(analyze_one, llm, cfg, p): i for i, p in enumerate(papers)}
        done = 0
        for fut in as_completed(futures):
            i = futures[fut]
            data, err = fut.result()
            results[i] = data
            if err:
                failures.append((papers[i]["title"][:60], err))
            done += 1
            if on_progress:
                on_progress(done, len(papers), papers[i]["title"][:50])
    return results, failures
