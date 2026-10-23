"""DeepSeek 客户端：带重试、JSON 兜底解析、token 预算控制。"""
import json
import re
import time
import urllib.error
import urllib.request

from .config import load_llm


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, conf=None):
        self.conf = conf or load_llm()
        self.base = self.conf["base_url"].rstrip("/")
        self.key = self.conf["api_key"]
        self.models = self.conf.get("models", {})
        self.temperature = self.conf.get("temperature", 0.3)

    # ---------- 底层调用 ----------
    def chat(self, system, user, model=None, json_mode=True, max_tokens=6000, temperature=None,
             retries=3, timeout=240):
        model = model or self.models.get("analysis", "deepseek-v4-pro")
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last = None
        budget = int(max_tokens)
        for attempt in range(retries):
            payload["max_tokens"] = budget
            truncated = False
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.base + "/chat/completions",
                data=body,
                headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
            )
            for handler in (urllib.request.ProxyHandler({}), None):
                try:
                    opener = urllib.request.build_opener(*([] if handler is None else [handler]))
                    resp = json.loads(opener.open(req, timeout=timeout).read().decode("utf-8"))
                    choice = resp["choices"][0]
                    content = (choice.get("message", {}).get("content") or "").strip()
                    finish = choice.get("finish_reason")
                    if content and finish != "length":
                        return content, resp.get("usage") or {}
                    if content and finish == "length":
                        # 半截 JSON 不能直接用，加大预算重来
                        truncated = True
                        last = LLMError("输出被截断（max_tokens=%d）" % budget)
                        break
                    # 推理模型：reasoning_content 可能吃满预算，正文为空
                    if finish == "length":
                        truncated = True
                        last = LLMError("输出被思考过程占满（max_tokens=%d）" % budget)
                    else:
                        last = LLMError("空回复（model=%s, finish=%s）" % (model, finish))
                    break
                except urllib.error.HTTPError as e:
                    detail = ""
                    try:
                        detail = e.read().decode("utf-8")[:300]
                    except Exception:  # noqa: BLE001
                        pass
                    last = LLMError("HTTP %s %s" % (e.code, detail))
                    if e.code in (401, 402, 403):
                        raise last
                except Exception as e:  # noqa: BLE001
                    last = LLMError("%s: %s" % (type(e).__name__, str(e)[:200]))
            if truncated:
                budget = min(16000, int(budget * 1.8) + 800)
                continue
            # 有的模型对 json_object 不友好，去掉再试一次
            if "response_format" in payload and attempt == 0:
                payload.pop("response_format", None)
                continue
            time.sleep(1.5 * (attempt + 1))
        raise last or LLMError("调用失败")

    # ---------- JSON ----------
    @staticmethod
    def parse_json(text):
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
            text = re.sub(r"```$", "", text).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            chunk = text[start:end + 1]
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                chunk2 = re.sub(r",\s*([}\]])", r"\1", chunk)
                return json.loads(chunk2)
        raise LLMError("返回不是合法 JSON：%s" % text[:180])

    def chat_json(self, system, user, **kw):
        text, usage = self.chat(system, user, json_mode=True, **kw)
        return self.parse_json(text), usage

    def health(self):
        text, usage = self.chat(
            "只输出 JSON。", '返回 {"ok": true, "msg": "pong"}', json_mode=True, max_tokens=30
        )
        return text, usage
