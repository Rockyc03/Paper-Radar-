"""精读：选影响力最高的一篇 → 抓全文 → 按推文版式生成十个板块 → 渲染成单页 HTML。"""
import html
import io
import json
import math
import re

from . import mathml, sources
from .config import audience as audience_of
from .config import day_tag, load_prompt_pair

MAX_FULLTEXT = 42000


# ---------------- 选篇 ----------------
def influence_score(paper, analysis):
    a = analysis or {}
    try:
        infl = float(a.get("influence") or 0)
    except (TypeError, ValueError):
        infl = 0.0
    cites = math.log1p(paper.get("citations") or 0)
    venue = paper.get("venue_weight", 0.5)
    oa_bonus = 0.8 if paper.get("pdf_url") else 0.0
    return round(infl * 2.0 + cites + venue * 1.5 + oa_bonus, 3)


def pick(papers, analyses):
    scored = [(influence_score(p, a), i) for i, (p, a) in enumerate(zip(papers, analyses))]
    scored.sort(reverse=True)
    return scored[0][1] if scored else None


# ---------------- 全文 ----------------
def fetch_fulltext(paper, max_chars=MAX_FULLTEXT):
    pdf_url = paper.get("pdf_url") or ""
    if not pdf_url:
        return "", "无开放全文（出版商未开放 PDF）"
    try:
        raw = sources.http_get(pdf_url, timeout=150, retries=2)
    except Exception as e:  # noqa: BLE001
        return "", "全文下载失败：%s" % str(e)[:80]
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        chunks = []
        for page in reader.pages[:36]:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                continue
        text = "\n".join(chunks)
    except Exception as e:  # noqa: BLE001
        return "", "PDF 解析失败：%s" % str(e)[:80]
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 1500:
        return "", "PDF 无可提取文本（可能为扫描件）"
    return text[:max_chars], "已获取全文节选 %d 字符" % min(len(text), max_chars)


# ---------------- 生成 ----------------
def generate(llm, cfg, paper, fulltext, topic_name):
    payload = json.dumps(
        {
            "标题": paper.get("title"),
            "venue": paper.get("venue_full"),
            "类型": paper.get("kind"),
            "发表日期": paper.get("date"),
            "作者": [{"name": a["name"], "affil": a.get("affil", "")} for a in (paper.get("authors") or [])],
            "DOI": paper.get("doi") or "无",
            "原文链接": paper.get("url"),
            "是否开放获取": paper.get("oa"),
            "摘要": paper.get("abstract") or "摘要未披露",
        },
        ensure_ascii=False,
        indent=2,
    )
    kwargs = dict(field=cfg["field_name"], audience=audience_of(cfg),
                  topic_name=topic_name or "", paper=payload,
                  fulltext=(fulltext or "（未获取到全文，仅可依据摘要）")[:MAX_FULLTEXT])
    model = llm.models.get("deepread")
    part_a, ua = llm.chat_json(*load_prompt_pair("deepread_a.md", **kwargs), model=model,
                               max_tokens=7000, temperature=0.35)
    part_b, ub = llm.chat_json(*load_prompt_pair("deepread_b.md", **kwargs), model=model,
                               max_tokens=7000, temperature=0.35)
    article = dict(part_a)
    article.update(part_b)
    article["_usage"] = {"a": ua, "b": ub}
    return article


# ---------------- 渲染 ----------------
def esc(s):
    return html.escape(str(s or ""))


def _rich(text):
    """正文渲染：转义 + **加粗** + LaTeX 公式转 MathML。"""
    return mathml.render(text)


def _para(text):
    """段落：保留 **加粗** 与 $公式$，其余转义。"""
    return "<p>%s</p>" % _rich(text).replace("\n", "</p><p>")


def _ol(items, cls="plain"):
    return "<ol class='%s'>%s</ol>" % (cls, "".join("<li>%s</li>" % _rich(x) for x in items or []))


def render_html(article, paper, date_str, brand, field_name, fulltext_note):
    a = article
    survey = a.get("survey") or {}
    obj = a.get("objectives") or {}
    innov = a.get("innovation") or {}
    exp = a.get("experiments") or {}
    authors = a.get("authors") or [{"name": x["name"], "affil": x.get("affil", "")} for x in paper.get("authors") or []]
    kw_en = a.get("keywords_en") or []
    kw_zh = a.get("keywords_zh") or []

    author_li = "".join(
        "<li>%s（%s）</li>" % (esc(x.get("name")), esc(x.get("affil") or "单位未披露"))
        for x in authors if x.get("name")
    )

    cats = "".join(
        "<li><strong>%s</strong>：%s</li>" % (_rich(c.get("label")), _rich(c.get("desc")))
        for c in (survey.get("categories") or [])
    )

    challenges = "".join(
        "<li><strong>%s</strong>：%s</li>" % (_rich(c.get("title")), _rich(c.get("desc")))
        for c in (a.get("challenges") or [])
    )

    formulas = "".join(
        "<div class='formula'><div class='f-label'>%s</div><div class='f-expr'>%s</div><div class='f-note'>%s</div></div>"
        % (_rich(f.get("label")), mathml.render_formula(f.get("expr")), _rich(f.get("note")))
        for f in (innov.get("formulas") or [])
    )

    roadmap = (innov.get("roadmap") or [])
    roadmap_html = " <span class='arrow'>→</span> ".join("<span class='step'>%s</span>" % _rich(s) for s in roadmap)

    figures = "".join(
        "<figure><div class='fig-body'>📊</div><figcaption><strong>%s</strong> %s</figcaption><p>%s</p></figure>"
        % (_rich(r.get("figure")), _rich(r.get("title")), _rich(r.get("analysis")))
        for r in (a.get("results") or [])
    )

    link = paper.get("url") or ""
    link_html = "<a href='%s' target='_blank'>%s</a>" % (esc(link), esc(link)) if link else "无"

    body = f"""
<article class="wx">
  <div class="kicker">{esc(brand)} · 精读 · {esc(paper.get('venue'))}</div>
  <h1>{esc(paper.get('venue'))} | {_rich(a.get('title_zh') or paper.get('title'))}</h1>
  <div class="meta-line">{esc(date_str)} · {esc(paper.get('topic_name') or '')} · {esc('预印本' if paper.get('kind')=='preprint' else ('期刊' if paper.get('kind')=='journal' else '会议'))}</div>

  <h2>📝 论文概述</h2>
  <h3>1. 文章题目</h3>
  <ul class="plain">
    <li>英文题目：{_rich(a.get('title_en') or paper.get('title'))}</li>
    <li>中文题目：{_rich(a.get('title_zh') or '')}</li>
  </ul>
  <h3>2. 作者及单位</h3>
  <ul class="plain">{author_li}</ul>
  <h3>3. 关键词</h3>
  <ul class="plain">
    <li>Index Terms：{esc('; '.join(kw_en))}</li>
    <li>中文关键词：{esc('，'.join(kw_zh))}</li>
  </ul>
  <h3>4. 发表信息</h3>
  <blockquote>{_rich(a.get('pubinfo') or '')}<br>原文：{link_html}<br>全文状态：{esc(fulltext_note)}</blockquote>

  <h2>🔍 研究背景和意义</h2>
  {_para(a.get('background'))}

  <h2>📚 当前研究综述</h2>
  <h3>1. 已有方法类别</h3>
  <ul class="plain">{cats}</ul>
  <h3>2. 代表性研究结论</h3>
  {_para(survey.get('findings'))}
  <h3>3. 与本文工作的关系</h3>
  {_para(survey.get('relation'))}

  <h2>⚠️ 研究现存挑战</h2>
  <ol class="plain">{challenges}</ol>

  <h2>🎯 研究对象和研究目标</h2>
  <h3>1. 研究对象</h3>
  {_para(obj.get('object'))}
  {_para(obj.get('formulation'))}
  <h3>2. 研究目标</h3>
  {_ol(obj.get('goals'))}

  <h2>🌟 文章的主要贡献</h2>
  {_ol(a.get('contributions'))}

  <h2>🧠 关键创新和技术路线</h2>
  <h3>1. 核心思想</h3>
  {_para(innov.get('core'))}
  <h3>2. 关键理论工具</h3>
  <ul class="plain">{''.join("<li>%s</li>" % _rich(t) for t in (innov.get('tools') or []))}</ul>
  <h3>3. 关键公式、定理或算法</h3>
  {formulas or "<p class='muted'>摘要未披露具体公式。</p>"}
  <h3>4. 技术路线总结</h3>
  <div class="roadmap">{roadmap_html or "<span class='muted'>未披露</span>"}</div>

  <h2>🧪 实验程序</h2>
  <h3>1. 实验目的</h3>
  {_para(exp.get('purpose'))}
  <h3>2. 实验设置</h3>
  <ul class="plain">{''.join("<li>%s</li>" % _rich(t) for t in (exp.get('setup') or []))}</ul>
  <h3>3. 实验流程</h3>
  {_ol(exp.get('flow'))}

  <h2>📊 实验结果图与讨论</h2>
  {figures or "<p class='muted'>摘要未披露实验图，需查阅原文。</p>"}

  <h2>✅ 文章结论</h2>
  {_para(a.get('conclusion'))}

  <blockquote class="about">
    <strong>关于本稿</strong><br>
    {esc(a.get('source_note') or '')}<br>
    由「{esc(brand)}」自动生成于 {esc(date_str)}，覆盖方向：{esc(field_name)}。<br>
    所有结论以论文原文为准；标「摘要未披露」处请回查全文。
  </blockquote>
</article>
"""

    css = """
:root{--ink:#1a1a1a;--sub:#6b6b6b;--line:#e6e2da;--bg:#faf8f4;--accent:#1f5c4d;--hi:#fff8e6}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
 font-family:-apple-system,"PingFang SC","Microsoft YaHei",Georgia,serif;
 line-height:1.85;font-size:16.5px}
.wx{max-width:760px;margin:0 auto;padding:44px 22px 80px;background:#fff;
 box-shadow:0 1px 3px rgba(0,0,0,.05)}
.kicker{font-size:12.5px;letter-spacing:.14em;color:var(--accent);text-transform:uppercase;
 font-family:ui-monospace,Menlo,monospace;margin-bottom:10px}
h1{font-size:27px;line-height:1.45;margin:6px 0 10px;font-weight:700;letter-spacing:-.01em}
.meta-line{font-size:13px;color:var(--sub);border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:26px}
h2{font-size:20px;margin:38px 0 14px;padding-left:12px;border-left:4px solid var(--accent);font-weight:700}
h3{font-size:16px;margin:22px 0 8px;color:var(--accent);font-weight:600}
p{margin:10px 0}
ul.plain,ol.plain{margin:8px 0 8px 0;padding-left:22px}
ul.plain li,ol.plain li{margin:7px 0}
strong{color:#0f3d33}
blockquote{margin:16px 0;padding:14px 16px;background:var(--hi);border-left:3px solid #d9b44a;
 font-size:14.5px;color:#4a4132;border-radius:0 4px 4px 0}
.formula{margin:14px 0;padding:12px 14px;background:#f5f7f6;border-radius:6px;font-size:14.5px}
.f-label{font-weight:600;color:var(--accent);margin-bottom:4px}
.f-expr{background:#fff;padding:16px 12px 12px;border-radius:4px;border:1px solid var(--line);
 text-align:center;overflow-x:auto;overflow-y:hidden;color:#243;line-height:2.3}
.f-expr math{font-size:1.24em}
.f-note{color:var(--sub);font-size:13.5px;margin-top:6px}
code.tex{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;background:#fff;color:#a0562a;
 padding:1px 5px;border-radius:3px;border:1px solid #ecdfd4;word-break:break-all}
.roadmap{margin:12px 0;line-height:2.4}
.step{display:inline-block;padding:2px 10px;background:#eef4f1;border:1px solid #cfe0d8;border-radius:4px;font-size:14px}
.arrow{color:var(--accent);font-weight:700;margin:0 2px}
figure{margin:18px 0;padding:14px;border:1px solid var(--line);border-radius:6px;background:#fcfcfa}
.fig-body{height:74px;display:flex;align-items:center;justify-content:center;background:repeating-linear-gradient(
 45deg,#f2efe9,#f2efe9 8px,#f7f5f1 8px,#f7f5f1 16px);border-radius:4px;font-size:26px;margin-bottom:8px}
figcaption{font-size:14px;color:var(--accent);margin-bottom:6px}
figure p{font-size:14.5px;color:#333;margin:0}
.about{background:#f4f2ed;border-left-color:#b9b2a2;color:#5a5348}
.muted{color:var(--sub);font-size:14px}
a{color:var(--accent);word-break:break-all}
math{font-family:"Cambria Math","Latin Modern Math","STIX Two Math",Cambria,"Times New Roman",serif;
 font-size:1.06em}
p math,li math,.f-note math,figcaption math{font-size:1.08em;vertical-align:-0.05em}
@media print{body{background:#fff}.wx{box-shadow:none;max-width:100%}}
"""
    return "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>" \
           "<meta name='viewport' content='width=device-width,initial-scale=1'>" \
           "<title>每日精读 · %s | %s</title><style>%s</style></head><body>%s</body></html>" % (
               day_tag(date_str), esc(paper.get("venue")), css, body)
