# -*- coding: utf-8 -*-
"""LaTeX → MathML（服务端渲染，离线可用）。

为什么用 MathML 而不是 KaTeX/MathJax：
1. 长文是单文件 HTML，会被双击打开、被预览面板托管、也会被打印/转发；
   引入前端 JS 要么依赖 CDN（离线即失效），要么把上千 KB 的字体塞进单文件。
2. MathML 是浏览器原生能力（Chrome 109+ / Edge / Firefox / Safari 均已支持），
   零外部资源，打印矢量清晰，正文里的行内公式还能跟着字号一起缩放。
3. 转换在生成时做一次，页面打开时没有任何 JS 计算，滚动不卡。

对外只暴露两个函数：
    to_mathml(latex, block=False) -> str | None   # 单个表达式
    render(text) -> str                            # 一段含 $...$ / $$...$$ 的文本 → HTML
"""
import html
import re

try:
    import latex2mathml.converter as _latex2mathml
except ImportError:  # 没装就退化成纯文本，绝不让整条流水线挂掉
    _latex2mathml = None

_CACHE = {}
_MAX_LEN = 2000  # 超长/畸形输入不转换，直接降级

# 模型偶尔会写出 latex2mathml 不认识或语义重复的命令，先做等价替换
_FIXES = [
    (r"\\bm\s*\{", r"\\mathbf{"),
    (r"\\boldsymbol\s*\{", r"\\mathbf{"),
    (r"\\operatorname\*?\s*\{([^{}]*)\}", r"\\mathrm{\1}"),
    (r"\\tag\*?\s*\{[^{}]*\}", ""),
    (r"\\label\s*\{[^{}]*\}", ""),
    (r"\\nonumber|\\notag", ""),
    (r"\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle)", ""),
    (r"\\hspace\*?\s*\{[^{}]*\}", r"\\;"),
    (r"\\vspace\*?\s*\{[^{}]*\}", ""),
    (r"\\begin\{(?:equation|gather|multline|displaymath)\*?\}", ""),
    (r"\\end\{(?:equation|gather|multline|displaymath)\*?\}", ""),
    (r"\\(?:allowbreak|nobreak|phantom)\b", ""),
]


def _clean(latex):
    s = str(latex or "").strip()
    if s.startswith("$$") and s.endswith("$$"):
        s = s[2:-2]
    elif s.startswith("$") and s.endswith("$"):
        s = s[1:-1]
    s = s.replace(r"\[", "").replace(r"\]", "").replace(r"\(", "").replace(r"\)", "")
    for pat, rep in _FIXES:
        s = re.sub(pat, rep, s)
    return s.strip()


def to_mathml(latex, block=False):
    """LaTeX → <math> 片段；转不了返回 None（由调用方降级为纯文本）。"""
    if _latex2mathml is None:
        return None
    src = _clean(latex)
    if not src or len(src) > _MAX_LEN:
        return None
    key = ("B" if block else "I") + src
    if key in _CACHE:
        return _CACHE[key]
    try:
        # display="inline" 一律使用 inline：块级居中交给外层 div 的 text-align，
        # 比 MathML 原生 display="block" 在各浏览器下更一致。
        out = _latex2mathml.convert(src)
    except Exception:  # noqa: BLE001
        _CACHE[key] = None
        return None
    if not out or "<merror" in out:
        _CACHE[key] = None
        return None
    _CACHE[key] = out
    return out


def looks_like_tex(s):
    """判断一段裸文本是公式还是人话：有 TeX 记号、且不是一长串中文。"""
    s = str(s or "")
    if not re.search(r"[\\^_{}=]", s):
        return False
    return not re.search(r"[\u4e00-\u9fff]{5,}", s)


def _fallback_tex(latex):
    """转换失败时的兜底：等宽显示去掉了 $ 的 LaTeX 源码，至少不是一串美元符号。"""
    return "<code class='tex'>%s</code>" % html.escape(_clean(latex))


def render(text):
    """把一段文本渲染成 HTML：转义 + **加粗** + $行内公式$ / $$块级公式$$。"""
    raw = str(text or "")
    out = []
    pos = 0
    token = re.compile(r"\$\$(.+?)\$\$|\$(.+?)\$", re.S)
    for m in token.finditer(raw):
        out.append(_plain(raw[pos:m.start()]))
        body = m.group(1) if m.group(1) is not None else m.group(2)
        block = m.group(1) is not None
        ml = to_mathml(body, block=block)
        if ml:
            out.append(ml)
        else:
            out.append(_fallback_tex(body))
        pos = m.end()
    out.append(_plain(raw[pos:]))
    return "".join(out)


def _plain(text):
    text = html.escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text, flags=re.S)


def render_formula(expr):
    """公式块的 expr：可能是带 $ 的混排、裸 LaTeX，也可能只是文字描述。"""
    s = str(expr or "").strip()
    if not s:
        return ""
    if "$" in s:
        return render(s)
    if looks_like_tex(s):
        ml = to_mathml(s, block=True)
        return ml or _fallback_tex(s)
    return _plain(s)
