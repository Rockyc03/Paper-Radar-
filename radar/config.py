"""路径与配置加载。"""
import json
import os
from datetime import date, datetime, timedelta

PORT = 8780  # 看板服务端口；报告里的站内链接按它拼接

RADAR_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(RADAR_DIR)
DATA_DIR = os.path.join(BASE_DIR, "data")
PAPERS_DIR = os.path.join(DATA_DIR, "papers")
REPORTS_DIR = os.path.join(DATA_DIR, "reports")
DEEPREAD_DIR = os.path.join(DATA_DIR, "deepread")
WEEKLY_DIR = os.path.join(DATA_DIR, "weekly")
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")
WEB_DIR = os.path.join(BASE_DIR, "web")
SEEN_PATH = os.path.join(DATA_DIR, "seen.json")

for _d in (DATA_DIR, PAPERS_DIR, REPORTS_DIR, DEEPREAD_DIR, WEEKLY_DIR):
    os.makedirs(_d, exist_ok=True)


class ConfigError(RuntimeError):
    """配置缺失或不可用，消息本身就是给用户看的操作指引。"""


CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
CONFIG_EXAMPLE = os.path.join(BASE_DIR, "config.example.json")

DEFAULT_AUDIENCE = "该方向的研究生与研究者"

# 与研究方向无关的默认值；setup.py 生成 config.json 时以此打底
DEFAULTS = {
    "brand": "论文雷达",
    "field_name": "",
    "field_scope": "多个并列的独立方向：命中任一方向即可入选，不要求交叉",
    "audience": DEFAULT_AUDIENCE,
    "daily_target": 10,
    "deepread_per_day": 1,
    "weekly_enabled": True,
    "language": "zh",
    "timezone": "Asia/Shanghai",
    "min_pub_date": "2020-01-01",
    "prefer_recent_days": 1095,
    "window_days_ladder": [90, 150, 240, 365, 550, 800],
    "min_topic_score": 3.0,
    "paper_per_venue_cap": 3,
    "openalex_api_key": "",
    "topics": [],
    "venues": [],
}


def build_config(topics, venues, **overrides):
    """把用户回答组装成完整配置；未提供的键用 DEFAULTS 补齐。"""
    cfg = json.loads(json.dumps(DEFAULTS))
    cfg["topics"] = [t for t in (topics or []) if t.get("keywords")]
    cfg["venues"] = [v for v in (venues or []) if v.get("issn") or v.get("openalex")]
    cfg["field_name"] = " / ".join(t["name"] for t in cfg["topics"])
    for k, v in (overrides or {}).items():
        if v not in (None, ""):
            cfg[k] = v
    return cfg



def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise ConfigError(
            "还没有 config.json —— 研究方向与取文范围需要你自己填。\n"
            "  运行：python setup.py\n"
            "  它会依次问你：研究方向、要盯的期刊/会议、每天几篇，然后自动生成。")
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def is_configured(cfg):
    """研究方向和取文范围都填了才算配置完成。"""
    topics = [t for t in (cfg.get("topics") or []) if t.get("keywords")]
    venues = [v for v in (cfg.get("venues") or []) if v.get("issn") or v.get("openalex")]
    return bool(topics and venues)


def require_configured(cfg):
    if is_configured(cfg):
        return cfg
    raise ConfigError(
        "config.json 里还没有<研究方向>或<取文范围>，跑起来也取不到论文。\n"
        "  运行：python setup.py\n"
        "  它会问你两个问题：盯哪些方向、盯哪些期刊/会议。")



def load_llm():
    path = os.path.join(DATA_DIR, "llm.json")
    if not os.path.exists(path):
        raise ConfigError(
            "还没有 data/llm.json（模型配置），这是唯一必填的凭据。\n"
            "  · 交互式创建：python setup.py\n"
            "  · 或手动复制：cp data/llm.example.json data/llm.json，再填入 api_key\n"
            "  支持任何 OpenAI 兼容接口（DeepSeek / OpenAI / 通义 / 本地 vLLM 等）。")
    with open(path, encoding="utf-8") as f:
        conf = json.load(f)
    key = str(conf.get("api_key") or "").strip()
    if not key or key.startswith("在这里") or key == "sk-xxx":
        raise ConfigError(
            "data/llm.json 里的 api_key 还是占位值。\n"
            "  填上真实 Key，或运行 python setup.py 重新配置。")
    return conf


def today_str():
    return date.today().isoformat()


def audience(cfg):
    return (cfg.get("audience") or DEFAULT_AUDIENCE).strip()


def day_tag(date_str=None):
    """2026-10-23 → 10.23（用于附件名与页面标题）。"""
    s = str(date_str or today_str())
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return "%s.%s" % (s[5:7], s[8:10])
    return s


def daterange_back(days):
    return (date.today() - timedelta(days=days)).isoformat()


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_prompt(name, **kwargs):
    path = os.path.join(PROMPTS_DIR, name)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    for k, v in kwargs.items():
        text = text.replace("{{%s}}" % k, str(v))
    return text


def load_prompt_pair(name, **kwargs):
    """提示词文件用 ===SYSTEM=== / ===USER=== 分段，返回 (system, user)。"""
    text = load_prompt(name, **kwargs)
    system, _, user = text.partition("===USER===")
    system = system.replace("===SYSTEM===", "").strip()
    return system, user.strip()
