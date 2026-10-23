# -*- coding: utf-8 -*-
"""重渲染精读长文 HTML（不调用模型，公式会重新转成 MathML）。

用法：
    .venv\\Scripts\\python.exe repair_deepread.py            # 全部历史精读
    .venv\\Scripts\\python.exe repair_deepread.py 2026-10-23  # 指定日期
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from radar import config, deepread  # noqa: E402


def rebuild(date):
    stem = date[:-5] if date.endswith(".json") else date
    src = os.path.join(config.DEEPREAD_DIR, stem + ".json")
    rec = config.read_json(src)
    if not rec:
        print("跳过（无 JSON）：", stem)
        return None
    cfg = config.load_config()
    html_doc = deepread.render_html(
        rec.get("article") or {},
        rec.get("paper") or {},
        stem,
        cfg.get("brand") or "论文雷达",
        cfg.get("field_name") or "",
        rec.get("fulltext_note") or "",
    )
    out = os.path.join(config.DEEPREAD_DIR, stem + ".html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_doc)
    return out, len(html_doc), html_doc.count("<math")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        targets = args
    else:
        targets = [fn[:-5] for fn in sorted(os.listdir(config.DEEPREAD_DIR)) if fn.endswith(".json")]
    for t in targets:
        r = rebuild(t)
        if r:
            print("已重写：%s（%d 字节，%d 处公式）" % r)


if __name__ == "__main__":
    main()
