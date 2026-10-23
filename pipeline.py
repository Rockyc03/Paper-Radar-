"""论文雷达 · 主流水线

用法：
  python pipeline.py                 # 生成今天的日报（默认 10 篇 + 1 篇精读）
  python pipeline.py --target 6      # 只取 6 篇
  python pipeline.py --no-deepread   # 跳过精读
  python pipeline.py --weekly        # 生成周日周报（覆盖最近 7 天）
  python pipeline.py --force         # 覆盖已有产物
  python pipeline.py --dry-run       # 只取文不调模型，看看能捞到什么
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from radar import analyze, config, deepread, rank, report, sources, weekly, wecom  # noqa: E402
from radar.llm import LLM  # noqa: E402


def log(msg):
    print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


def load_seen():
    data = config.read_json(config.SEEN_PATH, {"ids": {}}) or {"ids": {}}
    return set(data.get("ids", {}).keys()), data


def save_seen(data, new_ids, date_str):
    ids = data.setdefault("ids", {})
    for i in new_ids:
        ids[i] = date_str
    config.write_json(config.SEEN_PATH, data)


def gather(cfg, target, seen_ids):
    """逐级放宽时间窗并累积候选池，直到篇数够、且每个方向都不空。"""
    errors = []
    merged, merged_ids = [], set()
    selected, stats = [], {}
    n_topics = len(cfg["topics"])
    floor = max(2, target // (n_topics + 1))

    for window in cfg.get("window_days_ladder", [120, 400, 900]):
        log("取文：窗口 %d 天" % window)
        papers, fetch_stats = sources.fetch_all(cfg, window, errors)
        added = 0
        for p in papers:
            if p["id"] not in merged_ids:
                merged_ids.add(p["id"])
                merged.append(p)
                added += 1
        selected, stats = rank.select_daily(merged, cfg, target, seen_ids)
        stats["window_days"] = window
        stats["merged"] = len(merged)
        stats["fetch"] = fetch_stats
        dist = stats.get("by_topic") or {}
        log("  新增 %d 篇（池 %d）→ 可排序 %d → 选出 %d 篇 %s"
            % (added, len(merged), stats.get("pool", 0), len(selected),
               " ".join("%s:%d" % (k[:4], v) for k, v in dist.items())))

        enough = len(selected) >= target and all(dist.get(t["key"], 0) >= floor for t in cfg["topics"])
        if enough:
            break
        if len(selected) < target:
            log("  篇数不足（%d/%d），放宽时间窗" % (len(selected), target))
        else:
            log("  有方向偏空（下限 %d 篇/方向），继续放宽时间窗补" % floor)
    return selected, stats, errors


def resolve_topic(cfg, needle):
    """按 key / 中文名片段定位单个方向；多个方向是并列的，可以单独出报。"""
    if not needle:
        return None
    n = needle.strip().lower()
    for t in cfg["topics"]:
        if n == t["key"].lower() or n in t["name"].lower() or n in t["short"].lower():
            return t
    return None


def run_daily(args):
    cfg = config.require_configured(config.load_config())
    date_str = args.date or config.today_str()
    target = args.target or cfg.get("daily_target", 10)
    scope_topic = resolve_topic(cfg, args.topic)
    cfg_use = json.loads(json.dumps(cfg))
    if scope_topic:
        cfg_use["topics"] = [scope_topic]
        cfg_use["field_name"] = scope_topic["name"] + "（单方向）"
        target = args.target or min(target, 8)
    out_json = os.path.join(config.REPORTS_DIR, date_str + ("" if not scope_topic else "-" + scope_topic["key"]) + ".json")
    if os.path.exists(out_json) and not args.force:
        log("%s 的日报已存在，跳过（要重跑加 --force）" % date_str)
        return 0

    stem = date_str + ("" if not scope_topic else "-" + scope_topic["key"])
    llm = LLM()
    seen_ids, seen_data = load_seen()
    started = time.time()
    resumed = None

    if args.resume:
        rec = config.read_json(out_json)
        if not rec or not rec.get("papers"):
            log("没有可恢复的 %s 日报；先正常跑一次再谈恢复" % date_str)
            return 2
        resumed = rec
        selected = rec["papers"]
        analyses = [p.get("analysis") or {} for p in selected]
        stats = rec.get("stats") or {}
        errors = []
        log("恢复模式：复用已生成的 %d 篇速读，只重做精读与渲染" % len(selected))
    else:
        selected, stats, errors = gather(cfg_use, target, set() if args.force else seen_ids)
        if not selected:
            log("没取到任何论文，检查网络或放宽 config.json 的关键词")
            return 2

    if args.dry_run and not resumed:
        for i, p in enumerate(selected, 1):
            log("%2d. [%s] %s (%s, 相关度 %.1f, 方向 %s)"
                % (i, p["venue"], p["title"][:78], p["date"], p["topic_score"], p["topic_name"]))
        log("dry-run 结束，未调用模型")
        return 0

    if not resumed:
        log("模型 %s，开始六问速读（%d 篇）" % (llm.models.get("analysis"), len(selected)))

        def progress(done, total, title):
            log("  速读 %d/%d  %s" % (done, total, title))

        analyses, failures = analyze.analyze_many(llm, cfg_use, selected, on_progress=progress)
        for p, a in zip(selected, analyses):
            p["analysis"] = a
        if failures:
            log("速读失败 %d 篇：%s" % (len(failures), failures[:2]))

    # ---- 精读 ----
    dr_record = {"ok": False}
    if not args.no_deepread:
        idx = deepread.pick(selected, analyses)
        if idx is not None:
            p, a = selected[idx], analyses[idx]
            log("今日精读：%s" % p["title"][:80])
            text, note = deepread.fetch_fulltext(p)
            log("  全文：%s" % note)
            try:
                article = deepread.generate(llm, cfg_use, p, text, p.get("topic_name"))
                html_doc = deepread.render_html(article, p, date_str, cfg_use["brand"], cfg_use["field_name"], note)
                dr_file = report.deepread_path(stem)
                with open(dr_file, "w", encoding="utf-8") as f:
                    f.write(html_doc)
                config.write_json(os.path.join(config.DEEPREAD_DIR, stem + ".json"),
                                  {"paper": p, "article": article, "fulltext_note": note})
                dr_record = {
                    "ok": True,
                    "paper_id": p["id"],
                    "title": p["title"],
                    "title_zh": article.get("title_zh") or a.get("title_zh"),
                    "venue": p["venue"],
                    "gist": a.get("gist"),
                    "influence": a.get("influence"),
                    "fulltext_note": note,
                    "href": "/deepread/%s.html" % stem,
                }
                log("  精读已生成：%s" % dr_file)
            except Exception as e:  # noqa: BLE001
                dr_record = {"ok": False, "error": "精读生成失败：%s" % str(e)[:200]}
                log("  " + dr_record["error"])

    # ---- 统计 ----
    usage_total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    for a in analyses:
        u = (a or {}).get("_usage") or {}
        for k in usage_total:
            usage_total[k] += int(u.get(k) or 0)

    record = {
        "date": date_str,
        "stem": stem,
        "scope": scope_topic["name"] if scope_topic else cfg.get("field_name") or "本期方向",
        "scope_note": cfg.get("field_scope"),
        "field": cfg_use["field_name"],
        "brand": cfg["brand"],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "model": llm.models.get("analysis"),
        "target": target,
        "min_pub_date": cfg.get("min_pub_date"),
        "stats": {**stats, "fetch_errors": errors[:6]},
        "usage": usage_total,
        "elapsed_sec": round(time.time() - started, 1),
        "papers": selected,
        "deepread": dr_record,
    }
    json_path, html_path = report.save(record)

    if not args.force:
        save_seen(seen_data, [p["id"] for p in selected], date_str)

    # ---- 自检 ----
    empty = sum(1 for p in selected if not all((p["analysis"] or {}).get(k) for k in analyze.REQUIRED[:6]))
    log("完成：%s 篇，六个问题完整 %d 篇，空项 %d 篇，用时 %.0fs，token %d"
        % (len(selected), len(selected) - empty, empty, record["elapsed_sec"], usage_total["total_tokens"]))
    log("日报：%s" % html_path)

    # ---- 推送企业微信（失败只记日志，不影响产物）----
    if args.no_wecom:
        log("已跳过企业微信推送（--no-wecom）")
    else:
        push_daily(record, stem)

    if empty:
        log("警告：有 %d 篇存在空项，建议 --force 重跑" % empty)
        return 1
    return 0


def push_daily(record, stem):
    """把日报推到企业微信：markdown 摘要（自动分条）+ 两份 HTML 附件。"""
    try:
        dr = dict(record.get("deepread") or {})
        html = os.path.join(config.DEEPREAD_DIR, stem + ".html")
        rep = os.path.join(config.REPORTS_DIR, stem + ".html")
        attach = bool(dr.get("ok")) and os.path.exists(html)
        dr["attach"] = attach
        dr["attach_report"] = os.path.exists(rep)
        res = wecom.notify_daily(record, dr, html if attach else None,
                                 rep if dr["attach_report"] else None)
        if res.get("ok"):
            names = [wecom.attach_name("每日十篇泛读", record.get("date")),
                     wecom.attach_name("每日精读", record.get("date"))]
            log("企业微信：已推送 %d 条 + 附件 %s" % (res.get("sent", 0), "、".join(names)))
        elif res.get("skipped"):
            log("企业微信：%s，已跳过" % res.get("reason"))
        else:
            log("企业微信：未推送（%s）" % res.get("error"))
    except Exception as e:  # noqa: BLE001
        log("企业微信推送异常：%s" % str(e)[:200])


def run_weekly(args):
    cfg = config.require_configured(config.load_config())
    llm = LLM()
    end_date = args.date or config.today_str()
    log("生成周报：%s 往前 7 天" % end_date)
    data, err = weekly.build(llm, cfg, end_date)
    if err:
        log("周报失败：%s" % err)
        return 2
    path = weekly.save(data, end_date)
    log("周报已生成：%s（覆盖 %d 篇）" % (path, data["count"]))
    if not getattr(args, "no_wecom", False):
        try:
            res = wecom.notify_weekly(data)
            if res.get("ok"):
                log("企业微信：周报已推送 %d 条" % res.get("sent", 0))
            elif res.get("skipped"):
                log("企业微信：%s，已跳过" % res.get("reason"))
            else:
                log("企业微信：周报未推送（%s）" % res.get("error"))
        except Exception as e:  # noqa: BLE001
            log("企业微信推送异常：%s" % str(e)[:200])
    return 0


def main():
    ap = argparse.ArgumentParser(description="论文雷达")
    ap.add_argument("--date", help="指定日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--target", type=int, default=None, help="本期论文数")
    ap.add_argument("--no-deepread", action="store_true", help="跳过精读")
    ap.add_argument("--force", action="store_true", help="覆盖已存在的当日产物")
    ap.add_argument("--dry-run", action="store_true", help="只取文，不调模型")
    ap.add_argument("--weekly", action="store_true", help="生成周报")
    ap.add_argument("--topic", help="只做一个方向，写 config.json 里 topics 的 key 或中文名片段")
    ap.add_argument("--resume", action="store_true", help="复用当天已生成的速读，只重做精读与渲染")
    ap.add_argument("--no-wecom", action="store_true", help="本次不推送企业微信")
    args = ap.parse_args()
    try:
        return run_weekly(args) if args.weekly else run_daily(args)
    except config.ConfigError as e:
        print("\n[配置未完成] %s\n" % e, file=sys.stderr, flush=True)
        return 3


if __name__ == "__main__":
    sys.exit(main())
