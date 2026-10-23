"""补渲染：复用已生成的速读与精读，重写日报 HTML（不调用模型）。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from radar import config, report  # noqa: E402

DATE = sys.argv[1] if len(sys.argv) > 1 else config.today_str()
stem = DATE
rec = config.read_json(os.path.join(config.REPORTS_DIR, stem + ".json"))
if not rec:
    raise SystemExit("没有 %s 的日报 JSON" % stem)

dr = config.read_json(os.path.join(config.DEEPREAD_DIR, stem + ".json"))
if dr:
    p = dr.get("paper") or {}
    art = dr.get("article") or {}
    a = (p.get("analysis") or {})
    rec["deepread"] = {
        "ok": True,
        "paper_id": p.get("id"),
        "title": p.get("title"),
        "title_zh": art.get("title_zh") or a.get("title_zh"),
        "venue": p.get("venue"),
        "gist": a.get("gist"),
        "influence": a.get("influence"),
        "fulltext_note": dr.get("fulltext_note"),
        "href": "/deepread/%s.html" % stem,
    }

cfg = config.load_config()
rec.setdefault("stem", stem)
rec.setdefault("min_pub_date", cfg.get("min_pub_date"))
rec.setdefault("scope", cfg.get("field_name") or "本期方向")
rec.setdefault("scope_note", cfg.get("field_scope"))

from collections import Counter

origins = Counter((x.get("origin") or "unknown") for x in rec.get("papers") or [])
name = {"openalex": "OpenAlex", "crossref": "Crossref 题录", "arxiv": "arXiv"}
rec["origins_note"] = "数据源 " + " / ".join("%s %d 篇" % (name.get(k, k), v) for k, v in origins.items())
if origins.get("crossref"):
    rec["fallback_note"] = "OpenAlex 配额耗尽，本期改用 Crossref 取顶刊题录 + arXiv 回填摘要"

json_path, html_path = report.save(rec)

# 补写 seen，避免明天重复推同样的 10 篇
seen = config.read_json(config.SEEN_PATH, {"ids": {}}) or {"ids": {}}
ids = seen.setdefault("ids", {})
for x in rec.get("papers") or []:
    ids[x["id"]] = DATE
config.write_json(config.SEEN_PATH, seen)

print("已重写：", html_path)
print("篇数：", len(rec.get("papers") or []), "| 精读：", rec["deepread"]["ok"])
