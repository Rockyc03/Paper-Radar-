# -*- coding: utf-8 -*-
"""论文雷达 · 首次安装向导。

做四件事，不改代码、不装系统包：

  1. 体检环境（Python 版本 + 依赖）
  2. 配好模型（生成 data/llm.json，并跑一次连通测试）
  3. 问你研究方向和取文范围（生成 config.json），仓库里不带任何预置方向
  4. 告诉你企业微信是可选的，以及下一步跑什么

用法：
  python setup.py                      # 交互式配置
  python setup.py --check              # 只体检，不写任何文件
  python setup.py --base-url URL --api-key KEY --model M          # 只配模型
  python setup.py --topic "方向A" --topic "方向B" \
                  --venue "Nature Machine Intelligence" --venue "NeurIPS"   # 非交互配方向
  python setup.py --reconfigure        # 重新回答方向与取文范围
  python setup.py --skip-test          # 写完不测连通
"""
import argparse
import importlib.util
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from radar import bootstrap, config  # noqa: E402

LLM_PATH = os.path.join(config.DATA_DIR, "llm.json")
EXAMPLE_PATH = os.path.join(config.DATA_DIR, "llm.example.json")

REQUIRED = [
    ("pypdf", "解析开放 PDF 全文（精读用）"),
    ("latex2mathml", "把 LaTeX 公式转成浏览器可渲染的 MathML"),
]
OPTIONAL = [
    ("websockets", "推送消息（可选功能，不装就不推送）"),
]

MIN_PY = (3, 9)
MAX_TOPICS = 5


def hr(title):
    print("\n" + title)
    print("-" * 52)


def check_python():
    v = sys.version_info
    if v < MIN_PY:
        print("  ✗ Python %d.%d，需要 %d.%d 以上" % (v[0], v[1], MIN_PY[0], MIN_PY[1]))
        return False
    print("  ✓ Python %d.%d.%d" % (v[0], v[1], v[2]))
    return True


def check_deps():
    ok = True
    for mod, why in REQUIRED:
        if importlib.util.find_spec(mod):
            print("  ✓ %-14s %s" % (mod, why))
        else:
            print("  ✗ %-14s 未安装 —— %s" % (mod, why))
            print("    pip install %s" % mod)
            ok = False
    for mod, why in OPTIONAL:
        if importlib.util.find_spec(mod):
            print("  ✓ %-14s %s" % (mod, why))
        else:
            print("  ○ %-14s 未安装 —— %s" % (mod, why))
    return ok


def tty():
    try:
        return sys.stdin.isatty()
    except Exception:  # noqa: BLE001
        return False


def ask_if_tty(prompt, default=""):
    """非交互环境（agent / 管道）直接返回默认值，不要卡住。"""
    if not tty():
        return default
    return ask(prompt, default)


def ask(prompt, default=""):
    tail = (" [%s]" % default) if default else ""
    try:
        s = input("%s%s：" % (prompt, tail)).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        sys.exit(1)
    return s or default


def ask_yes(prompt, default=True):
    if not tty():
        return default
    d = "Y/n" if default else "y/N"
    try:
        s = input("%s（%s）：" % (prompt, d)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        sys.exit(1)
    if not s:
        return default
    return s in ("y", "yes", "是", "1")


# ---------------- 模型 ----------------
def write_llm(args):
    conf = {}
    if os.path.exists(EXAMPLE_PATH):
        with open(EXAMPLE_PATH, encoding="utf-8") as f:
            conf = json.load(f)

    base_url = args.base_url or ask("接口地址 base_url", conf.get("base_url") or "https://api.deepseek.com/v1")
    api_key = args.api_key or ask("API Key（输入时不回显请用 --api-key 传入）")
    if not api_key:
        print("  ! 没有 API Key，跳过写入。")
        return None

    models = conf.get("models") or {}
    default_main = models.get("analysis") or "deepseek-chat"
    default_flash = models.get("translate") or default_main
    interactive = not (args.base_url or args.api_key)
    main_model = args.model or (ask("主力模型（六问/精读/周报）", default_main)
                                if interactive else default_main)
    flash_model = args.flash_model or (ask("轻量模型（标题翻译）", default_flash)
                                       if interactive else default_flash)

    conf.update({
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "models": {"analysis": main_model, "deepread": main_model,
                   "weekly": main_model, "translate": flash_model},
    })
    config.write_json(LLM_PATH, conf)
    print("  ✓ 已写入 %s" % os.path.relpath(LLM_PATH, BASE_DIR))
    return conf


def test_llm():
    print("  正在测试连通…")
    try:
        from radar.llm import LLM

        text, usage = LLM().health()
        print("  ✓ 连通正常，模型回复：%s" % str(text)[:60].replace("\n", " "))
        return True
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        print("  ✗ 失败：%s" % msg[:300])
        low = msg.lower()
        if "401" in low or "invalid" in low or "unauthorized" in low:
            print("    → Key 不对，或与 base_url 不匹配")
        elif "402" in low or "insufficient" in low or "balance" in low:
            print("    → 账户余额不足")
        elif "404" in low or "model" in low:
            print("    → 模型名不存在，检查 models 里的名字")
        elif "timeout" in low or "connect" in low:
            print("    → 网络不通，或 base_url 写错（结尾一般要带 /v1）")
        return False


def make_llm():
    """尽力返回一个可用 LLM；不可用返回 None（后续走手工填关键词）。"""
    try:
        from radar.llm import LLM

        return LLM()
    except Exception:  # noqa: BLE001
        return None


# ---------------- 研究方向与取文范围 ----------------
def show_domain():
    try:
        cfg = config.load_config()
    except config.ConfigError:
        print("  状态：未配置")
        print("  下一步会问你：盯哪些研究方向、盯哪些期刊/会议。")
        return None
    topics = cfg.get("topics") or []
    venues = cfg.get("venues") or []
    if not config.is_configured(cfg):
        print("  状态：未完成（缺研究方向或缺期刊/会议）")
    print("  领域：%s" % (cfg.get("field_name") or "-"))
    print("  方向：%s" % (" / ".join(t.get("name", "") for t in topics) or "-"))
    print("  读者：%s" % config.audience(cfg))
    print("  每天：%s 篇 + 精读 %s 篇，时间下限 %s"
          % (cfg.get("daily_target", 10), cfg.get("deepread_per_day", 1),
             cfg.get("min_pub_date", "2020-01-01")))
    print("  出处：%d 个期刊/会议 + arXiv" % len(venues))
    return cfg


def ask_directions(cli_topics):
    if cli_topics:
        return list(cli_topics)
    print("  你的研究方向是什么？（可以有多个并列方向，命中任一即入报）")
    print("  例：图神经网络 / 因果推断 / 强化学习 —— 用中文或英文都行，回车结束。")
    out = []
    for i in range(MAX_TOPICS):
        s = ask("    方向 %d" % (i + 1), "" if i else "")
        if not s:
            if not out:
                print("    至少给一个方向。")
                continue
            break
        out.append(s)
    return out


def ask_keywords_manually(name, base):
    if not tty():
        print("    模型不可用，且当前不是交互终端，无法手工填关键词 —— 跳过该方向")
        print("    可在配好模型后重跑：python setup.py --reconfigure")
        return None
    print("    模型不可用，手动填关键词（英文，逗号分隔，越多越好）")
    raw = ask("    关键词")
    kws = [k.strip().lower() for k in raw.replace("，", ",").split(",") if k.strip()]
    base = dict(base or {})
    base["keywords"] = kws
    base["arxiv"] = bootstrap.arxiv_from_keywords(kws)
    return base


def expand_all(names, llm):
    topics = []
    if not llm:
        print("  ! 模型还没配好，方向关键词只能手工填。")
    for i, name in enumerate(names):
        print("\n  [%d/%d] %s" % (i + 1, len(names), name))
        data = None
        if llm:
            print("    正在让模型展开关键词…")
            try:
                data = bootstrap.expand_topic(llm, name, index=i)
            except Exception as e:  # noqa: BLE001
                print("    展开失败：%s" % str(e)[:160])
        if not data or not data.get("keywords"):
            data = ask_keywords_manually(name, data or {"key": "topic%d" % (i + 1),
                                                        "name": name, "short": name[:4]})
            if not data:
                continue
        else:
            print("    关键词 %d 个：%s" % (len(data["keywords"]), "、".join(data["keywords"][:10])
                                          + ("…" if len(data["keywords"]) > 10 else "")))
            if data.get("arxiv"):
                print("    arXiv：%s" % data["arxiv"][:120])
            if not ask_yes("    就这样？", True):
                data = ask_keywords_manually(name, data)
        data.pop("manual", None)
        topics.append(data)
    return topics


def ask_venues(cli_venues, cfg, api_key=""):
    if cli_venues:
        raws = list(cli_venues)
    else:
        print("\n  要盯哪些期刊和会议？（逗号分隔，可混写；留空则只用 arXiv）")
        print("  例：Nature Machine Intelligence, IEEE TPAMI, NeurIPS —— 也可以写 issn:2522-5839 或 S4210221977")
        prev = "、".join(v.get("short") or v.get("name") for v in (cfg or {}).get("venues") or [])
        raw = ask("  期刊/会议", "")
        if not raw:
            if prev:
                print("  沿用原有 %d 个出处。" % len(prev.split("、")))
                return (cfg or {}).get("venues") or []
            return []
        raws = [s.strip() for s in raw.replace("，", ",").replace("、", ",").split(",") if s.strip()]

    venues, failed = [], []
    print()
    for s in raws:
        hit = bootstrap.parse_venue_arg(s, api_key=api_key)
        if not hit:
            failed.append(s)
            continue
        venues.append(hit)
        tag = hit["issn"] if hit["kind"] == "journal" else hit["openalex"]
        print("  ✓ %-18s %s" % (hit["short"], (hit["name"] or "")[:52]))
        print("    %s = %s" % ("ISSN" if hit["kind"] == "journal" else "OpenAlex", tag))
    for s in failed:
        print("  ✗ %s —— 没查到，已跳过" % s)
    if failed:
        print("    （会议查询依赖 OpenAlex，配额用尽时会失败；稍后重跑 "
              "python setup.py --reconfigure 可补上，或直接给 source id）")
    return venues


def configure_domain(args, llm):
    cfg = None
    try:
        cfg = config.load_config()
    except config.ConfigError:
        cfg = None

    ready = bool(cfg) and config.is_configured(cfg)
    cli = bool(args.topic or args.venue)
    if ready and not args.reconfigure and not cli:
        print("  已是配置好的状态，跳过。要重答请加 --reconfigure")
        return cfg
    if not tty() and not cli:
        print("  当前不是交互终端，跳过方向配置。请用 --topic / --venue 传入。")
        return cfg

    print("  仓库不自带任何研究方向，这两个问题只有你能回答。")
    names = ask_directions(args.topic)
    if not names:
        print("  没有方向，跳过。")
        return cfg
    topics = expand_all(names, llm)

    api_key = str((cfg or {}).get("openalex_api_key") or "")
    venues = ask_venues(args.venue, cfg, api_key=api_key)

    audience = args.audience or ask_if_tty(
        "\n  这份日报写给谁看（读者定位，会注入提示词）",
        config.audience(cfg) if cfg else config.DEFAULT_AUDIENCE)
    daily = args.daily or ask_if_tty("  每天几篇", str((cfg or {}).get("daily_target", 10)))
    min_date = args.min_date or ((cfg or {}).get("min_pub_date") or "2020-01-01")

    try:
        daily = int(str(daily).strip())
    except (TypeError, ValueError):
        daily = 10

    overrides = {"audience": audience, "daily_target": daily, "min_pub_date": min_date}
    if cfg:
        for k in ("brand", "language", "timezone", "prefer_recent_days",
                  "window_days_ladder", "min_topic_score", "paper_per_venue_cap",
                  "deepread_per_day", "weekly_enabled"):
            if k in cfg:
                overrides[k] = cfg[k]
    new_cfg = config.build_config(topics, venues, **overrides)
    config.write_json(config.CONFIG_PATH, new_cfg)
    print("\n  ✓ 已写入 config.json")
    print("    领域：%s" % new_cfg["field_name"])
    print("    方向：%s" % " / ".join("%s（%d 词）" % (t["name"], len(t["keywords"]))
                                    for t in new_cfg["topics"]))
    print("    出处：%d 个" % len(new_cfg["venues"]))
    return new_cfg


# ---------------- 可选功能 ----------------
def show_wecom():
    conf = config.read_json(os.path.join(config.DATA_DIR, "wecom.json"), {}) or {}
    if conf.get("enabled") and conf.get("chatid"):
        print("  ✓ 已启用（%s）" % (conf.get("chat_label") or conf.get("chatid")))
    else:
        print("  ○ 未启用 —— 这是可选功能，不影响日报生成")
        print("    想推到企业微信：见 README「可选：推送到企业微信」")


def main():
    ap = argparse.ArgumentParser(description="论文雷达安装向导")
    ap.add_argument("--check", action="store_true", help="只体检，不修改任何文件")
    ap.add_argument("--base-url", help="模型接口地址")
    ap.add_argument("--api-key", help="API Key")
    ap.add_argument("--model", help="主力模型名")
    ap.add_argument("--flash-model", help="轻量模型名")
    ap.add_argument("--skip-test", action="store_true", help="不测试连通")
    ap.add_argument("--topic", action="append", help="研究方向，可重复；给中文或英文都行")
    ap.add_argument("--venue", action="append", help="期刊或会议，可重复；也可写 issn:xxx / Sxxxxxx")
    ap.add_argument("--audience", help="读者定位（注入提示词）")
    ap.add_argument("--daily", help="每天几篇")
    ap.add_argument("--min-date", help="发表时间下限，如 2020-01-01")
    ap.add_argument("--reconfigure", action="store_true", help="重新回答方向与取文范围")
    args = ap.parse_args()

    print("\n论文雷达 · 安装体检")
    print("=" * 52)

    hr("1. 运行环境")
    py_ok = check_python()
    dep_ok = check_deps()

    hr("2. 模型配置")
    have = os.path.exists(LLM_PATH)
    if have and not (args.base_url or args.api_key):
        try:
            cur = config.load_llm()
            print("  ✓ 已有配置：%s / %s" % (cur.get("base_url"),
                                            (cur.get("models") or {}).get("analysis")))
            print("    要改就删掉 data/llm.json 再跑一次，或用 --base-url / --api-key 覆盖。")
        except config.ConfigError as e:
            print("  ! %s" % str(e).split("\n")[0])
            have = False
    if not have:
        if args.check:
            print("  ○ 还没有 data/llm.json（--check 模式不写入）")
        else:
            write_llm(args)
            have = os.path.exists(LLM_PATH)

    if have and not args.skip_test:
        test_llm()

    hr("3. 研究方向与取文范围")
    if args.check:
        show_domain()
    else:
        configure_domain(args, make_llm() if have else None)

    hr("4. 推送（可选）")
    show_wecom()

    hr("5. 下一步")
    if py_ok and dep_ok:
        print("  python pipeline.py --dry-run     # 只取文不花钱，先看能不能捞到论文")
        print("  python pipeline.py               # 正式生成今天的日报 + 精读")
        print("  python server.py                 # 打开看板 http://127.0.0.1:%d" % config.PORT)
        print("  （Windows 也可以直接双击 start.bat）")
        return 0
    print("  先把上面标 ✗ 的依赖装上，再回来跑 python setup.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
