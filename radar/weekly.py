"""周报：把一周的六问速读聚合成脉络、Top3 与空缺。"""
import os
from datetime import date, timedelta

from .config import WEEKLY_DIR, audience as audience_of, load_prompt_pair, read_json, REPORTS_DIR, write_json


def collect(end_date, days=7):
    end = date.fromisoformat(end_date)
    items = []
    for i in range(days):
        d = (end - timedelta(days=i)).isoformat()
        rec = read_json(os.path.join(REPORTS_DIR, d + ".json"))
        if not rec:
            continue
        for p in rec.get("papers") or []:
            a = p.get("analysis") or {}
            items.append({
                "date": d,
                "title": p.get("title"),
                "venue": p.get("venue"),
                "topic": p.get("topic_name"),
                "problem": (a.get("problem") or "")[:180],
                "key_result": (a.get("key_result") or "")[:220],
                "innovation": (a.get("innovation") or "")[:200],
                "tags": a.get("tags") or [],
                "influence": a.get("influence"),
                "id": p.get("id"),
            })
    return items


def build(llm, cfg, end_date, days=7):
    import json

    items = collect(end_date, days)
    if not items:
        return None, "最近 %d 天没有可用的日报数据" % days
    period = "%s ~ %s" % ((date.fromisoformat(end_date) - timedelta(days=days - 1)).isoformat(), end_date)
    system, user = load_prompt_pair(
        "weekly.md",
        field=cfg["field_name"],
        audience=audience_of(cfg),
        period=period,
        count=len(items),
        week_json=json.dumps(items, ensure_ascii=False, indent=1)[:52000],
    )
    data, usage = llm.chat_json(system, user, model=llm.models.get("weekly"),
                                max_tokens=6000, temperature=0.35)
    data["_usage"] = usage
    data["period"] = period
    data["count"] = len(items)
    data["papers"] = items
    data["generated_at"] = date.today().isoformat()
    return data, None


def save(data, end_date):
    stem = "week-" + end_date
    write_json(os.path.join(WEEKLY_DIR, stem + ".json"), data)
    html_path = os.path.join(WEEKLY_DIR, stem + ".html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render(data))
    return html_path


def render(d):
    import html as H

    def e(s):
        return H.escape(str(s or ""))

    threads = "".join(
        "<section class='thread'><h3>%s</h3><p>%s</p><p class='ev'>涉及：%s</p></section>"
        % (e(t.get("name")), e(t.get("trend")), e("；".join(t.get("evidence") or [])))
        for t in (d.get("threads") or [])
    )
    top3 = "".join(
        "<li><strong>%s</strong> <span class='chip'>%s</span><p>%s</p></li>"
        % (e(t.get("title")), e(t.get("venue")), e(t.get("reason")))
        for t in (d.get("top3") or [])
    )
    gaps = "".join("<li>%s</li>" % e(x) for x in (d.get("gaps") or []))
    nxt = "".join("<li>%s</li>" % e(x) for x in (d.get("next_week") or []))
    css = """
body{margin:0;background:#f6f7f8;color:#15181b;font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.75}
.wrap{max-width:860px;margin:0 auto;padding:40px 22px 80px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.kicker{font-size:12px;letter-spacing:.18em;color:#1f5c4d;text-transform:uppercase;font-family:ui-monospace,Menlo,monospace}
h1{font-size:27px;margin:8px 0 6px;letter-spacing:-.01em}
.meta{color:#666e75;font-size:13px;border-bottom:1px solid #e3e6e9;padding-bottom:16px;margin-bottom:24px}
h2{font-size:19px;margin:34px 0 12px;padding-left:11px;border-left:4px solid #1f5c4d}
.thread{border:1px solid #e3e6e9;border-radius:8px;padding:14px 16px;margin:12px 0}
.thread h3{margin:0 0 6px;font-size:16px;color:#123f35}
.thread p{margin:6px 0;font-size:14.5px}
.ev{color:#666e75;font-size:13px}
ol.top3{padding-left:20px}ol.top3 li{margin:12px 0;font-size:15px}
ol.top3 p{margin:4px 0 0;font-size:14px;color:#3a444c}
.chip{font-size:11.5px;background:#eef4f1;color:#155043;padding:2px 8px;border-radius:99px;border:1px solid #d4e5dd}
ul{padding-left:20px}li{margin:7px 0;font-size:14.5px}
"""
    body = """
<div class="wrap">
  <div class="kicker">Paper Radar · 每周综述</div>
  <h1>{title}</h1>
  <div class="meta">周期 {period} · 覆盖 {count} 篇 · 生成于 {gen}</div>
  <h2>本周综述</h2><p>{overview}</p>
  <h2>三条主线</h2>{threads}
  <h2>本周最值得读的三篇</h2><ol class="top3">{top3}</ol>
  <h2>没人做但该做的空缺</h2><ul>{gaps}</ul>
  <h2>下周盯什么</h2><ul>{nxt}</ul>
</div>""".format(
        title=e(d.get("title") or "周报"), period=e(d.get("period")), count=e(d.get("count")),
        gen=e(d.get("generated_at")), overview=e(d.get("overview")), threads=threads,
        top3=top3, gaps=gaps, nxt=nxt,
    )
    return "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>" \
           "<meta name='viewport' content='width=device-width,initial-scale=1'>" \
           "<title>%s</title><style>%s</style></head><body>%s</body></html>" % (
               e(d.get("title")), css, body)
