# -*- coding: utf-8 -*-
"""企业微信「智能机器人」推送。

走官方长连接协议（wss://openws.work.weixin.qq.com），不依赖任何第三方 SDK：

    aibot_subscribe            订阅鉴权（bot_id + secret）
    aibot_send_msg             主动推送 markdown / file
    aibot_upload_media_init    分片上传第一步 → upload_id
    aibot_upload_media_chunk   分片（base64，单片 ≤ 512KB）
    aibot_upload_media_finish  合并 → media_id
    ping                       心跳（本项目每次只连几秒，仍保留保活任务）

两条硬约束（官方文档 path/101463、101837）：
1. 机器人必须先在该会话里被用户发过消息，之后才允许主动推送 —— 所以第一次要跑
   `wecom_bind.py`，在群里 @ 它一句，把 chatid 抓下来存进 data/wecom.json。
2. 同一个机器人同一时间只允许一个长连接，新连接会踢掉旧连接。

发送策略：临时建连 → 订阅 → 逐条发送（等 ack）→ 断开。不做常驻进程，
每天只发一两次，建连成本可以忽略，也避免和别处长连接互踢。
"""
import asyncio
import base64
import hashlib
import json
import os
import time
import uuid
from datetime import datetime

from . import config

try:
    import websockets
except ImportError:  # 没装依赖时给出可读的失败信息，而不是 ImportError 栈
    websockets = None

WS_URL = "wss://openws.work.weixin.qq.com"
CONF_PATH = os.path.join(config.DATA_DIR, "wecom.json")
LOG_PATH = os.path.join(config.DATA_DIR, "wecom_log.jsonl")
CHUNK_SIZE = 512 * 1024
MARKDOWN_LIMIT = 3800  # 官方上限 4096 字节，留出余量

DEFAULTS = {
    "enabled": False,
    "bot_id": "",
    "secret": "",
    "chatid": "",
    "chat_type": 2,          # 1 单聊（chatid 填 userid）2 群聊
    "chat_label": "",
    "send_daily": True,
    "send_weekly": True,
    "attach_deepread": True,
    "attach_report": True,
    "bound_at": "",
}


# ---------------- 配置与日志 ----------------
def load_conf():
    conf = dict(DEFAULTS)
    got = config.read_json(CONF_PATH, {}) or {}
    if isinstance(got, dict):
        conf.update({k: v for k, v in got.items() if v is not None})
    return conf


def save_conf(patch):
    conf = load_conf()
    conf.update({k: v for k, v in (patch or {}).items() if v is not None})
    config.write_json(CONF_PATH, conf)
    return conf


def ready(conf=None):
    """能不能发：开关打开 + 凭证齐全 + 已绑定会话。"""
    c = conf or load_conf()
    if not c.get("enabled"):
        return False, "未启用（可选功能，data/wecom.json 里 enabled=false）"
    if not c.get("bot_id") or not c.get("secret"):
        return False, "缺少 Bot ID / Secret"
    if not c.get("chatid"):
        return False, "还没绑定会话，先运行 wecom_bind.py"
    return True, ""


def log(event, **fields):
    rec = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "event": event}
    rec.update(fields)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def recent_logs(n=20):
    if not os.path.exists(LOG_PATH):
        return []
    with open(LOG_PATH, encoding="utf-8") as f:
        lines = f.readlines()[-n:]
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


# ---------------- 协议 ----------------
def _rid(prefix="req"):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:16])


def _find(obj, key):
    """在返回帧里递归找字段（不同接口把结果放在 body 或顶层，位置不统一）。"""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            got = _find(v, key)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for v in obj:
            got = _find(v, key)
            if got is not None:
                return got
    return None


class WeComError(RuntimeError):
    pass


class Link:
    """一次性的长连接会话：订阅 → 发送若干条 → 关闭。"""

    def __init__(self, bot_id, secret, timeout=25):
        self.bot_id = bot_id
        self.secret = secret
        self.timeout = timeout
        self.ws = None
        self._hb = None
        self._pending = {}

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        await self.close()

    async def connect(self):
        if websockets is None:
            raise WeComError("缺少依赖 websockets，请先 pip install websockets")
        self.ws = await websockets.connect(WS_URL, open_timeout=self.timeout,
                                           ping_interval=None, max_size=16 * 1024 * 1024)
        self._reader = asyncio.create_task(self._read_loop())
        await self._request("aibot_subscribe", {"bot_id": self.bot_id, "secret": self.secret},
                            timeout=self.timeout)
        self._hb = asyncio.create_task(self._heartbeat())

    async def _read_loop(self):
        try:
            async for raw in self.ws:
                try:
                    frame = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                rid = (frame.get("headers") or {}).get("req_id")
                # 只有「自己发出、正在等 ack」的 req_id 才算响应；
                # 服务端推送的回调帧同样带 req_id（但不在 pending 里），必须当事件处理。
                fut = self._pending.pop(rid, None) if rid else None
                if fut is not None and not fut.done():
                    fut.set_result(frame)
                else:
                    await self._on_event(frame)
        except Exception:  # noqa: BLE001 连接关闭是正常路径
            pass

    async def _on_event(self, frame):
        cb = getattr(self, "on_event", None)
        if cb:
            await cb(frame)

    async def _request(self, cmd, body, timeout=None):
        rid = _rid(cmd)
        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self.ws.send(json.dumps({"cmd": cmd, "headers": {"req_id": rid}, "body": body},
                                      ensure_ascii=False))
        try:
            frame = await asyncio.wait_for(fut, timeout or self.timeout)
        finally:
            self._pending.pop(rid, None)
        code = frame.get("errcode", 0)
        if code not in (0, None):
            raise WeComError("%s 失败：errcode=%s %s" % (cmd, code, frame.get("errmsg") or ""))
        return frame

    async def _heartbeat(self):
        try:
            while True:
                await asyncio.sleep(25)
                await self.ws.send(json.dumps({"cmd": "ping", "headers": {"req_id": _rid("ping")}}))
        except Exception:  # noqa: BLE001
            return

    async def send_markdown(self, chatid, chat_type, content):
        await self._request("aibot_send_msg", {
            "chatid": chatid, "chat_type": int(chat_type),
            "msgtype": "markdown", "markdown": {"content": content},
        })

    async def send_file(self, chatid, chat_type, path, filename=None, timeout=60):
        media_id = await self.upload(path, filename=filename, timeout=timeout)
        await self._request("aibot_send_msg", {
            "chatid": chatid, "chat_type": int(chat_type),
            "msgtype": "file", "file": {"media_id": media_id},
        }, timeout=timeout)

    async def upload(self, path, filename=None, timeout=60):
        with open(path, "rb") as f:
            data = f.read()
        name = filename or os.path.basename(path)
        chunks = [data[i:i + CHUNK_SIZE] for i in range(0, len(data), CHUNK_SIZE)] or [b""]
        init = await self._request("aibot_upload_media_init", {
            "type": "file", "filename": name, "total_size": len(data),
            "total_chunks": len(chunks), "md5": hashlib.md5(data).hexdigest(),
        }, timeout=timeout)
        upload_id = _find(init, "upload_id")
        if not upload_id:
            raise WeComError("上传初始化没有返回 upload_id：%s" % json.dumps(init, ensure_ascii=False)[:200])
        for idx, chunk in enumerate(chunks):
            await self._request("aibot_upload_media_chunk", {
                "upload_id": upload_id, "chunk_index": idx,
                "base64_data": base64.b64encode(chunk).decode("ascii"),
            }, timeout=timeout)
        fin = await self._request("aibot_upload_media_finish", {"upload_id": upload_id},
                                  timeout=timeout)
        media_id = _find(fin, "media_id")
        if not media_id:
            raise WeComError("上传完成却没有 media_id：%s" % json.dumps(fin, ensure_ascii=False)[:200])
        return media_id

    async def close(self):
        if self._hb:
            self._hb.cancel()
        if getattr(self, "_reader", None):
            self._reader.cancel()
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
            self.ws = None


# ---------------- 组稿 ----------------
def _bytes(s):
    return len(s.encode("utf-8"))


def attach_name(prefix, date_str=None):
    """附件展示名：每日精读_10.23.html（磁盘文件名保持不变，只在发送时改名）。"""
    return "%s_%s.html" % (prefix, config.day_tag(date_str))


def split_blocks(blocks, limit=MARKDOWN_LIMIT):
    """按字节累计切分，尽量不把一篇论文拆到两条消息里。"""
    out, cur = [], []
    for b in blocks:
        b = b.strip("\n")
        if not b:
            continue
        if cur and sum(_bytes(x) + 2 for x in cur) + _bytes(b) > limit:
            out.append("\n\n".join(cur))
            cur = [b]
        elif _bytes(b) > limit:  # 单块超长（极少见），硬切
            if cur:
                out.append("\n\n".join(cur))
                cur = []
            out.append(b[:limit])
        else:
            cur.append(b)
    if cur:
        out.append("\n\n".join(cur))
    return out


def _paper_block(idx, p):
    a = p.get("analysis") or {}
    title = (p.get("title") or "").strip()
    url = p.get("url") or ""
    head = "**%s [%s](%s)**" % (idx, title.replace("[", "(").replace("]", ")"), url) if url \
        else "**%s %s**" % (idx, title)
    meta = " · ".join(x for x in [
        p.get("topic_name"), p.get("venue"),
        (p.get("date") or "")[:7],
        "预印本" if p.get("kind") == "preprint" else "",
    ] if x)
    lines = [head, '<font color="comment">%s</font>' % meta]
    gist = (a.get("gist") or "").strip()
    if gist:
        lines.append(gist)
    return "\n".join(lines)


def compose_daily(record, deepread=None, note=""):
    """日报 → 若干条 markdown（自动按字节切分）。"""
    papers = record.get("papers") or []
    stats = record.get("stats") or {}
    date = record.get("date") or config.today_str()
    marks = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮"
    journals = sum(1 for p in papers if p.get("kind") == "journal")
    pres = sum(1 for p in papers if p.get("kind") == "preprint")
    try:
        names = " / ".join(t["name"] for t in config.load_config().get("topics") or [])
    except Exception:  # noqa: BLE001
        names = record.get("scope") or ""

    head = ["# 📄 论文雷达 · %s" % date[5:]]
    quote = ["方向：%s" % (names or record.get("scope") or "")]
    quote.append("本期 %d 篇 · 期刊 %d · 预印本 %d · 时间窗 %s 天" % (
        len(papers), journals, pres, stats.get("window_days", "-")))
    head.append("> " + "\n> ".join(quote))

    blocks = ["\n".join(head)]
    for i, p in enumerate(papers):
        blocks.append(_paper_block(marks[i] if i < len(marks) else "%d." % (i + 1), p))

    if deepread and deepread.get("ok"):
        dr = deepread
        md = config.day_tag(date)
        parts = [
            "**🌟 今日精读｜%s**" % (dr.get("venue") or ""),
            "**%s**" % (dr.get("title_zh") or dr.get("title") or ""),
            '<font color="comment">影响力 %s/10 · %s</font>' % (dr.get("influence") or "-",
                                                                dr.get("fulltext_note") or ""),
            (dr.get("gist") or "").strip(),
        ]
        attaches = []
        if dr.get("attach_report"):
            attaches.append("《每日十篇泛读_%s.html》" % md)
        if dr.get("attach"):
            attaches.append("《每日精读_%s.html》" % md)
        if attaches:
            parts.append('<font color="info">附件：%s</font>' % "、".join(attaches))
        blocks.append("\n".join(x for x in parts if x))
    if note:
        blocks.append('<font color="comment">%s</font>' % note)
    return split_blocks(blocks)


def compose_weekly(rec):
    blocks = ["# 🗓 论文雷达 · 周综述\n> %s\n> 覆盖 %s 篇" % (rec.get("period") or "",
                                                            rec.get("count") or "-")]
    if rec.get("overview"):
        blocks.append(rec["overview"].strip())
    for i, t in enumerate((rec.get("threads") or [])[:3]):
        if isinstance(t, dict):
            lines = ["**主线 %d｜%s**" % (i + 1, t.get("name") or t.get("title") or "")]
            if t.get("desc"):
                lines.append(t["desc"])
            for ev in (t.get("evidence") or [])[:2]:
                lines.append('<font color="comment">· %s</font>' % str(ev)[:46])
            blocks.append("\n".join(lines))
        else:
            blocks.append("**主线 %d**\n%s" % (i + 1, t))
    top3 = [x.get("title") for x in (rec.get("top3") or []) if isinstance(x, dict) and x.get("title")]
    if top3:
        blocks.append("**本周 Top3**\n" + "\n".join("· %s" % x[:60] for x in top3[:3]))
    if rec.get("next_week"):
        blocks.append("**下周关注**\n" + "\n".join("· %s" % str(x)[:60] for x in rec["next_week"][:4]))
    return split_blocks(blocks)


# ---------------- 发送 ----------------
async def _deliver(conf, texts, files):
    async with Link(conf["bot_id"], conf["secret"]) as link:
        for t in texts:
            await link.send_markdown(conf["chatid"], conf.get("chat_type", 2), t)
            await asyncio.sleep(0.4)
        for item in files:
            if not item:
                continue
            # 支持 (磁盘路径, 展示文件名) 或纯路径
            path, name = item if isinstance(item, (tuple, list)) else (item, None)
            if path and os.path.exists(path):
                await link.send_file(conf["chatid"], conf.get("chat_type", 2), path, filename=name)


def send(texts, files=(), timeout=120):
    """同步入口：建连 → 发送 → 断开。返回 {ok, error, sent}。"""
    conf = load_conf()
    ok, why = ready(conf)
    if not ok:
        log("skip", reason=why)
        return {"ok": False, "error": why, "sent": 0}
    try:
        asyncio.run(asyncio.wait_for(_deliver(conf, texts, list(files)), timeout=timeout))
    except Exception as e:  # noqa: BLE001
        log("fail", error=str(e)[:300], texts=len(texts))
        return {"ok": False, "error": str(e)[:300], "sent": 0}
    log("ok", texts=len(texts), files=len([f for f in files if f]))
    return {"ok": True, "error": "", "sent": len(texts)}


def notify_daily(record, deepread=None, attach_html=None, attach_report=None, dry_run=False):
    """推日报：markdown 摘要 + 两份附件（十篇泛读 / 当日精读）。

    企业微信是可选功能：没配置就直接返回 skipped，不报错、不打扰。
    """
    conf = load_conf()
    if not conf.get("enabled") or not conf.get("chatid"):
        return {"ok": False, "skipped": True, "reason": "企业微信未配置（可选功能）", "sent": 0}
    if not conf.get("send_daily", True):
        return {"ok": False, "skipped": True, "reason": "日报推送已关闭", "sent": 0}
    texts = compose_daily(record, deepread)
    date = record.get("date") or config.today_str()
    files = []
    if attach_report and conf.get("attach_report", True):
        files.append((attach_report, attach_name("每日十篇泛读", date)))
    if attach_html and conf.get("attach_deepread", True):
        files.append((attach_html, attach_name("每日精读", date)))
    if dry_run:
        return {"ok": True, "dry_run": True, "texts": texts,
                "files": [os.path.basename(f[1]) for f in files]}
    return send(texts, files)


def notify_weekly(rec):
    conf = load_conf()
    if not conf.get("enabled") or not conf.get("chatid"):
        return {"ok": False, "skipped": True, "reason": "企业微信未配置（可选功能）", "sent": 0}
    if not conf.get("send_weekly", True):
        return {"ok": False, "skipped": True, "reason": "周报推送已关闭", "sent": 0}
    return send(compose_weekly(rec))


def test(text=None):
    text = text or ("**论文雷达 · 连通测试**\n> %s\n> 收到这条说明 Bot ID / Secret / chatid 都通了。"
                    % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return send([text])


def bind(timeout=180, echo=True):
    """连上长连接，等一条用户消息，把 chatid 记下来。"""
    conf = load_conf()
    if not conf.get("bot_id") or not conf.get("secret"):
        raise WeComError("先填 Bot ID 和 Secret（data/wecom.json 或看板设置）")

    got = {}

    async def run():
        link = Link(conf["bot_id"], conf["secret"])
        await link.connect()

        async def on_event(frame):
            cmd = frame.get("cmd") or "?"
            if cmd != "aibot_msg_callback":
                print("  · 收到非消息帧：%s" % cmd)
                return
            body = frame.get("body") or {}
            sender = body.get("from") or {}
            is_group = (body.get("chattype") or "").lower() == "group"
            # 单聊回调不带 chatid，官方要求单聊直接把用户 userid 当 chatid 用
            chatid = body.get("chatid") or (None if is_group else sender.get("userid"))
            print("  · 收到消息：chattype=%s chatid=%s" % (body.get("chattype"), chatid))
            if not chatid:
                return
            got.update({
                "chatid": chatid,
                "chat_type": 2 if is_group else 1,
                "chat_label": body.get("chatname") or sender.get("name") or sender.get("userid") or "",
            })
            if echo:
                try:
                    await link.send_markdown(chatid, got["chat_type"],
                                             "**绑定成功** ✅\n> 这个会话以后会收到论文雷达的每日汇报。")
                except Exception:  # noqa: BLE001
                    pass

        link.on_event = on_event
        try:
            for _ in range(int(timeout / 0.5)):
                if got:
                    break
                await asyncio.sleep(0.5)
        finally:
            await link.close()

    asyncio.run(asyncio.wait_for(run(), timeout=timeout + 30))
    if not got:
        raise WeComError("等超时了，没有收到任何消息；确认机器人已进群并在群里 @ 它发一句话")
    got["bound_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_conf(got)
    return got


def status():
    conf = load_conf()
    ok, why = ready(conf)
    return {
        "enabled": bool(conf.get("enabled")),
        "bound": bool(conf.get("chatid")),
        "chat_label": conf.get("chat_label") or "",
        "chat_type": conf.get("chat_type"),
        "ready": ok, "reason": why,
        "bot_id_masked": (conf.get("bot_id") or "")[:6] + "***" if conf.get("bot_id") else "",
        "send_daily": conf.get("send_daily"), "send_weekly": conf.get("send_weekly"),
        "attach_deepread": conf.get("attach_deepread"),
        "attach_report": conf.get("attach_report"),
        "logs": recent_logs(8),
    }
