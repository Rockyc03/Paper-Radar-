"""论文雷达 · 本地看板服务（纯标准库，端口 8780）。

启动：  .venv\\Scripts\\python.exe server.py
访问：  http://127.0.0.1:8780
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from radar import config  # noqa: E402

PORT = config.PORT
PY = sys.executable

RUN = {"proc": None, "log": [], "started": None, "exit": None, "kind": None}
LOCK = threading.Lock()


def _index_reports():
    items = []
    for fn in sorted(os.listdir(config.REPORTS_DIR), reverse=True):
        if not fn.endswith(".json"):
            continue
        rec = config.read_json(os.path.join(config.REPORTS_DIR, fn), {}) or {}
        papers = rec.get("papers") or []
        by_topic = {}
        for p in papers:
            name = p.get("topic_name") or "其他"
            by_topic[name] = by_topic.get(name, 0) + 1
        items.append({
            "stem": rec.get("stem") or fn[:-5],
            "date": rec.get("date"),
            "scope": rec.get("scope") or "",
            "count": len(papers),
            "by_topic": by_topic,
            "deepread": bool((rec.get("deepread") or {}).get("ok")),
            "generated_at": rec.get("generated_at"),
            "window_days": (rec.get("stats") or {}).get("window_days"),
            "preprints": sum(1 for p in papers if p.get("kind") == "preprint"),
        })
    return items


def _strip(obj):
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip(x) for x in obj]
    return obj


def _list_deepreads():
    out = []
    for fn in sorted(os.listdir(config.DEEPREAD_DIR), reverse=True):
        if not fn.endswith(".json"):
            continue
        d = config.read_json(os.path.join(config.DEEPREAD_DIR, fn), {}) or {}
        p = d.get("paper") or {}
        out.append({
            "stem": fn[:-5],
            "title": p.get("title"),
            "title_zh": (d.get("article") or {}).get("title_zh"),
            "venue": p.get("venue"),
            "date": p.get("date"),
            "topic": p.get("topic_name"),
            "note": d.get("fulltext_note"),
        })
    return out


def _list_weekly():
    out = []
    for fn in sorted(os.listdir(config.WEEKLY_DIR), reverse=True):
        if not fn.endswith(".json"):
            continue
        d = config.read_json(os.path.join(config.WEEKLY_DIR, fn), {}) or {}
        out.append({
            "stem": fn[:-5],
            "title": d.get("title"),
            "period": d.get("period"),
            "count": d.get("count"),
            "overview": (d.get("overview") or "")[:160],
        })
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "PaperRadar/1.0"

    def log_message(self, fmt, *args):  # 安静一点
        pass

    # ---------- helpers ----------
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(_strip(obj), ensure_ascii=False))

    def _file(self, path):
        if not os.path.isfile(path):
            return self._send(404, "<h1>404</h1>", "text/html; charset=utf-8")
        ext = os.path.splitext(path)[1].lower()
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".woff2": "font/woff2",
        }.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def _safe(self, root, rel):
        rel = re.sub(r"^[/\\]+", "", rel)
        path = os.path.normpath(os.path.join(root, rel))
        return path if path.startswith(os.path.normpath(root)) else None

    # ---------- routing ----------
    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)

        if p in ("/", "/index.html"):
            return self._file(os.path.join(config.WEB_DIR, "index.html"))
        if p.startswith("/web/"):
            return self._file(self._safe(config.WEB_DIR, p[5:]) or "")

        if p == "/api/summary":
            try:
                cfg = config.load_config()
                configured = config.is_configured(cfg)
            except config.ConfigError:
                cfg, configured = {}, False
            return self._json({
                "brand": cfg.get("brand") or "论文雷达",
                "field": cfg.get("field_name") or "",
                "audience": config.audience(cfg) if cfg else "",
                "configured": configured,
                "setup_hint": "" if configured else "python setup.py",
                "scope_note": cfg.get("field_scope"),
                "topics": [{"key": t["key"], "name": t["name"]} for t in cfg.get("topics") or []],
                "venues": [{"short": v["short"], "name": v["name"], "kind": v["kind"]} for v in cfg.get("venues") or []],
                "reports": _index_reports(),
                "deepreads": _list_deepreads(),
                "weekly": _list_weekly(),
                "today": config.today_str(),
                "run": {"running": RUN["proc"] is not None and RUN["proc"].poll() is None,
                        "exit": RUN["exit"], "kind": RUN["kind"]},
            })

        if p == "/api/report":
            stem = (q.get("stem") or [""])[0]
            path = self._safe(config.REPORTS_DIR, stem + ".json")
            rec = config.read_json(path, None) if path else None
            if not rec:
                return self._json({"ok": False, "error": "报告不存在"}, 404)
            return self._json(rec)

        if p == "/api/deepread":
            stem = (q.get("stem") or [""])[0]
            path = self._safe(config.DEEPREAD_DIR, stem + ".json")
            rec = config.read_json(path, None) if path else None
            if not rec:
                return self._json({"ok": False, "error": "精读不存在"}, 404)
            return self._json(rec)

        if p == "/api/weekly":
            stem = (q.get("stem") or [""])[0]
            path = self._safe(config.WEEKLY_DIR, stem + ".json")
            rec = config.read_json(path, None) if path else None
            return self._json(rec or {"ok": False, "error": "周报不存在"}, 200 if rec else 404)

        if p == "/api/library":
            papers = {}
            for item in _index_reports():
                rec = config.read_json(os.path.join(config.REPORTS_DIR, item["stem"] + ".json"), {}) or {}
                for x in rec.get("papers") or []:
                    a = x.get("analysis") or {}
                    papers[x.get("id")] = {
                        "id": x.get("id"), "title": x.get("title"),
                        "title_zh": a.get("title_zh"), "venue": x.get("venue"),
                        "date": x.get("date"), "topic": x.get("topic_name"),
                        "kind": x.get("kind"), "influence": a.get("influence"),
                        "gist": a.get("gist"), "citations": x.get("citations"),
                        "url": x.get("url"), "report": item["stem"],
                        "tags": a.get("tags") or [],
                    }
            return self._json({"items": sorted(papers.values(), key=lambda x: x.get("date") or "", reverse=True)})

        if p == "/api/run/status":
            return self._json({"running": RUN["proc"] is not None and RUN["proc"].poll() is None,
                               "exit": RUN["exit"], "log": RUN["log"][-120:], "kind": RUN["kind"],
                               "started": RUN["started"]})

        if p == "/api/llm":
            conf = config.load_llm()
            return self._json({"base_url": conf["base_url"], "models": conf["models"],
                               "key_masked": conf["api_key"][:6] + "..." + conf["api_key"][-4:]})

        if p == "/api/wecom":
            from radar import wecom

            return self._json(wecom.status())

        if p.startswith("/reports/"):
            return self._file(self._safe(config.REPORTS_DIR, p[9:]) or "")
        if p.startswith("/deepread/"):
            return self._file(self._safe(config.DEEPREAD_DIR, p[10:]) or "")
        if p.startswith("/weekly/"):
            return self._file(self._safe(config.WEEKLY_DIR, p[8:]) or "")

        return self._send(404, "<h1>404</h1>", "text/html; charset=utf-8")

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            body = {}

        if u.path == "/api/run":
            with LOCK:
                if RUN["proc"] is not None and RUN["proc"].poll() is None:
                    return self._json({"ok": False, "error": "已有任务在跑"}, 409)
                kind = body.get("kind") or "daily"
                args = [PY, os.path.join(BASE_DIR, "pipeline.py")]
                if kind == "weekly":
                    args.append("--weekly")
                else:
                    if body.get("force", True):
                        args.append("--force")
                    if body.get("topic"):
                        args += ["--topic", body["topic"]]
                    if body.get("deepread") is False:
                        args.append("--no-deepread")
                env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
                proc = subprocess.Popen(args, cwd=BASE_DIR, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, env=env, text=True,
                                        encoding="utf-8", errors="replace")
                RUN.update({"proc": proc, "log": [], "started": time.time(), "exit": None, "kind": kind})

                def pump():
                    for line in proc.stdout:
                        RUN["log"].append(line.rstrip())
                    proc.wait()
                    RUN["exit"] = proc.returncode
                    RUN["log"].append("[exit] code=%s" % proc.returncode)

                threading.Thread(target=pump, daemon=True).start()
            return self._json({"ok": True, "kind": kind})

        if u.path == "/api/llm/test":
            from radar.llm import LLM

            try:
                text, usage = LLM().health()
                return self._json({"ok": True, "reply": text[:120], "usage": usage})
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "error": str(e)[:300]})

        if u.path == "/api/wecom/test":
            from radar import wecom

            try:
                return self._json(wecom.test())
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "error": str(e)[:300]})

        if u.path == "/api/wecom/config":
            from radar import wecom

            patch = {}
            for k in ("enabled", "send_daily", "send_weekly", "attach_deepread", "attach_report",
                      "chat_type"):
                if k in body:
                    patch[k] = body[k]
            for k in ("bot_id", "secret", "chatid", "chat_label"):
                if body.get(k):
                    patch[k] = body[k]
            if patch:
                wecom.save_conf(patch)
            return self._json({"ok": True, "saved": sorted(patch), "status": wecom.status()})

        if u.path == "/api/wecom/send":
            from radar import wecom

            stem = body.get("date") or config.today_str()
            rec = config.read_json(os.path.join(config.REPORTS_DIR, stem + ".json"), None)
            if not rec:
                return self._json({"ok": False, "error": "没有 %s 的日报" % stem}, 404)
            dr = dict(rec.get("deepread") or {})
            html = os.path.join(config.DEEPREAD_DIR, stem + ".html")
            rep = os.path.join(config.REPORTS_DIR, stem + ".html")
            dr["attach"] = bool(dr.get("ok")) and os.path.exists(html)
            dr["attach_report"] = os.path.exists(rep)
            try:
                res = wecom.notify_daily(rec, dr, html if dr["attach"] else None,
                                         rep if dr["attach_report"] else None)
                res["status"] = wecom.status()
                return self._json(res)
            except Exception as e:  # noqa: BLE001
                return self._json({"ok": False, "error": str(e)[:300]})

        return self._json({"ok": False, "error": "未知接口"}, 404)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("论文雷达看板已启动： http://127.0.0.1:%d" % PORT, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("已停止")


if __name__ == "__main__":
    main()
