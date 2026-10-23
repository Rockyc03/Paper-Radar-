"""每日汇报渲染：单文件 HTML（可直接双击看/打印），外加结构化 JSON 供看板读取。"""
import html
import os
import re

from . import mathml
from .config import DEEPREAD_DIR, PORT, REPORTS_DIR, day_tag, write_json

SIXQ = [
    ("problem", "解决了什么问题", "①"),
    ("data_method", "用了什么数据与方法", "②"),
    ("key_result", "最关键的结果", "③"),
    ("innovation", "创新点落在哪个位置", "④"),
    ("open_problems", "遗留的问题", "⑤"),
    ("next_steps", "之后的研究方向与思路", "⑥"),
]

CSS = """
:root{--ink:#15181b;--sub:#666e75;--line:#e3e6e9;--bg:#f6f7f8;--card:#fff;
--accent:#1f5c4d;--accent-soft:#eef4f1;--warn:#8a5a00;--pre:#7a4b8f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.7}
.wrap{max-width:1080px;margin:0 auto;padding:34px 20px 90px}
header.top{display:flex;justify-content:space-between;align-items:flex-end;gap:18px;
border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:8px;flex-wrap:wrap}
.brand{font-size:13px;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);
font-family:ui-monospace,Menlo,monospace}
h1{font-size:30px;margin:6px 0 2px;letter-spacing:-.02em}
.field{color:var(--sub);font-size:14px}
.statrow{display:flex;gap:10px;flex-wrap:wrap;margin:18px 0 26px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:104px}
.stat b{display:block;font-size:20px;line-height:1.2}
.stat span{font-size:12px;color:var(--sub)}
.deepread{background:linear-gradient(135deg,#123f35,#1f5c4d);color:#fff;border-radius:12px;
padding:20px 22px;margin:0 0 26px}
.deepread .tag{font-size:12px;letter-spacing:.12em;opacity:.8;text-transform:uppercase}
.deepread h2{font-size:21px;margin:8px 0 6px;font-weight:650}
.deepread p{margin:4px 0;font-size:14px;opacity:.92}
.deepread a{display:inline-block;margin-top:12px;background:#fff;color:#123f35;text-decoration:none;
padding:8px 16px;border-radius:6px;font-size:14px;font-weight:600}
.deepread a.attach-hint{background:transparent;color:rgba(255,255,255,.82);font-weight:500;font-size:13px;
 padding:0;margin-top:10px;border-bottom:1px dashed var(--line);cursor:default}
.day-meta{font-size:12.5px;color:var(--sub);margin-bottom:22px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px 22px;margin-bottom:18px}
.card h3{margin:0 0 6px;font-size:19px;line-height:1.45}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 12px}
.chip{font-size:11.5px;padding:3px 9px;border-radius:99px;background:var(--accent-soft);color:#155043;
border:1px solid #d4e5dd}
.chip.venue{background:#eef1f4;color:#2f4350;border-color:#dbe2e8}
.chip.pre{background:#f6eef9;color:var(--pre);border-color:#e6d6ee}
.chip.date{background:#f4f4f2;color:#5a5a52;border-color:#e7e7e1}
.title-zh{font-size:14.5px;color:#3a444c;margin:0 0 10px}
.gist{font-size:14.5px;background:#fbfaf6;border-left:3px solid #d9b44a;padding:8px 12px;border-radius:0 4px 4px 0;margin:0 0 14px}
.qa{display:grid;gap:10px}
.q{display:grid;grid-template-columns:26px 1fr;gap:10px}
.q .n{font-family:ui-monospace,Menlo,monospace;color:var(--accent);font-size:13px;padding-top:2px}
.q .k{font-size:13px;font-weight:700;color:#213b33;display:block;margin-bottom:2px}
.q .v{font-size:14.5px;color:#2c343a;white-space:pre-wrap}
.q .v.empty{color:#9aa2a8;font-style:italic}
.foot{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-top:14px;
padding-top:10px;border-top:1px dashed var(--line);font-size:12.5px;color:var(--sub)}
.foot a{color:var(--accent);text-decoration:none;word-break:break-all}
.infl{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#8a5a00}
math{font-family:"Cambria Math","Latin Modern Math","STIX Two Math",Cambria,"Times New Roman",serif;
 font-size:1.06em}
.q .v math,.gist math,.title-zh math{font-size:1.08em;vertical-align:-0.05em}
.tags span{display:inline-block;font-size:11.5px;color:#5b6167;background:#f2f4f5;
padding:2px 8px;border-radius:4px;margin:2px 4px 0 0}
footer.end{margin-top:30px;padding-top:16px;border-top:1px solid var(--line);font-size:12.5px;color:var(--sub)}
#radarTip{position:fixed;left:0;right:0;bottom:0;z-index:99;background:#123f35;color:#fff;
font-size:12.5px;padding:9px 14px;text-align:center;box-shadow:0 -2px 10px rgba(0,0,0,.16)}
#radarTip b{color:#9fe3c8;font-family:ui-monospace,Menlo,monospace}
#radarTip button{background:transparent;border:1px solid rgba(255,255,255,.45);color:#fff;
border-radius:5px;font-size:12px;padding:2px 9px;margin-left:10px;cursor:pointer}
@media print{body{background:#fff}.card{break-inside:avoid}.deepread{-webkit-print-color-adjust:exact}#radarTip{display:none}}
@media(max-width:560px){
 body{-webkit-text-size-adjust:100%}
 .wrap{padding:20px 14px 60px;box-sizing:border-box;max-width:100%}
 h1{font-size:23px;letter-spacing:0}
 .deepread{padding:16px 16px;box-sizing:border-box;max-width:100%}
 .deepread h2{font-size:18px}
 .card{padding:16px 15px;box-sizing:border-box;max-width:100%}
 .card h3{font-size:17px}
 .deepread a.attach-hint{line-height:1.6}
}
"""


def esc(s):
    return html.escape(str(s or ""))


def radar_path(href):
    """规范站内链接：历史数据里的 ../deepread/x.html → /deepread/x.html；外链原样返回。"""
    h = str(href or "").strip()
    if not h:
        return ""
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", h) or h.startswith("//"):
        return h
    h = h.replace("\\", "/")
    h = re.sub(r"^(\.\./)+", "/", h)
    h = re.sub(r"^\./", "", h)
    return h if h.startswith("/") else "/" + h


# 报告可能被三种方式打开：看板（8780）、WorkBuddy 预览面板（其它端口）、双击本地文件。
# 后两者没有 /deepread/ 路由，所以站内链接统一交给这段脚本按当前环境改写。
BOOT_JS = r"""
<script>
(function(){
  var PORT = "%s";
  var http = /^https?:$/.test(location.protocol);
  var proto = http ? location.protocol : "http:";
  var host = location.hostname || "127.0.0.1";
  var here = location.port || (location.protocol === "https:" ? "443" : "80");
  var base = proto + "//" + host + ":" + PORT;
  var inDash = http && here === PORT;
  var isLocal = /^(localhost|127\.0\.0\.1|\[::1\]|::1)$/.test(host);
  var needTip = false;
  var links = Array.prototype.slice.call(document.querySelectorAll("a[data-radar]"));
  links.forEach(function(a){
    var p = a.getAttribute("data-radar") || "";
    if (p.charAt(0) !== "/") return;
    if (inDash) { a.setAttribute("href", p); return; }
    if (isLocal) {
      a.setAttribute("href", base + p);
      a.setAttribute("title", "在本地看板中打开：" + base + p);
      needTip = true;
      return;
    }
    // 手机 / 他人电脑打开附件时，127.0.0.1 必然打不开：改成附件指引
    a.removeAttribute("href");
    a.removeAttribute("target");
    a.textContent = "精读长文见随发附件《每日精读_{{DAY}}.html》";
    a.className = "attach-hint";
  });
  if (needTip) {
    var tip = document.createElement("div");
    tip.id = "radarTip";
    tip.innerHTML = "离线预览：站内链接指向本地看板 <b>" + base + "</b>，看板未启动请先运行 start.bat";
    var btn = document.createElement("button");
    btn.textContent = "知道了";
    btn.onclick = function(){ tip.parentNode.removeChild(tip); };
    tip.appendChild(btn);
    document.body.appendChild(tip);
  }
})();
</script>
""" % PORT


def _venue_chip(p):
    cls = "chip venue"
    if p.get("kind") == "preprint":
        return '<span class="chip pre">arXiv 预印本</span>'
    return '<span class="%s">%s</span>' % (cls, esc(p.get("venue")))


def card(p, idx):
    a = p.get("analysis") or {}
    qa = []
    for key, label, num in SIXQ:
        raw = a.get(key)
        if isinstance(raw, list):
            raw = "；".join(str(x) for x in raw if str(x).strip())
        val = str(raw or "").strip()
        qa.append(
            "<div class='q'><div class='n'>%s</div><div><span class='k'>%s</span>"
            "<div class='v%s'>%s</div></div></div>"
            % (num, esc(label), "" if val else " empty",
               mathml.render(val) if val else "（该项生成失败，请重跑）")
        )
    tags = "".join("<span>%s</span>" % esc(t) for t in (a.get("tags") or [])
                   if isinstance(t, (str, int, float)))
    link = p.get("url") or ""
    doi = p.get("doi") or ""
    return """
<article class="card">
  <div class="chips">
    {venue}<span class="chip date">{date}</span>
    <span class="chip">{topic}</span>
    <span class="chip">综合排序 #{idx}</span>
  </div>
  <h3>{title}</h3>
  <p class="title-zh">{title_zh}</p>
  <p class="gist">{gist}</p>
  <div class="qa">{qa}</div>
  <div class="foot">
    <div class="tags">{tags}</div>
    <div>{infl} &nbsp;|&nbsp; {link_html} {doi_html}</div>
  </div>
</article>""".format(
        venue=_venue_chip(p),
        date=esc(p.get("date")),
        topic=esc(p.get("topic_name") or ""),
        idx=idx,
        title=esc(p.get("title")),
        title_zh=mathml.render(a.get("title_zh") or ""),
        gist=mathml.render(a.get("gist") or ""),
        qa="".join(qa),
        tags=tags,
        infl='<span class="infl">影响力 %s/10</span>' % esc(a.get("influence")) if a.get("influence") else "",
        link_html='<a href="%s" target="_blank">原文</a>' % esc(link) if link else "",
        doi_html=("· " + esc(doi)) if doi else "",
    )


def render_daily(record):
    papers = record.get("papers") or []
    stats = record.get("stats") or {}
    dr = record.get("deepread") or {}
    dist = " · ".join(
        "%s %d" % (k, v) for k, v in (stats.get("by_topic") or {}).items() if v
    )
    deepread_block = ""
    if dr.get("ok"):
        deepread_block = """
<section class="deepread">
  <div class="tag">今日精读 · 影响力最高</div>
  <h2>{venue} | {title}</h2>
  <p>{gist}</p>
  <p>全文状态：{note} · 影响力 {infl}/10</p>
  <a href="{href}" data-radar="{href}" target="_blank" rel="noopener">打开精读长文 →</a>
</section>""".format(
            venue=esc(dr.get("venue")), title=esc(dr.get("title_zh") or dr.get("title")),
            gist=esc(dr.get("gist")), note=esc(dr.get("fulltext_note")),
            infl=esc(dr.get("influence")), href=esc(radar_path(dr.get("href"))),
        )
    elif dr.get("error"):
        deepread_block = (
            '<section class="deepread" style="background:#5a3a12">'
            "<div class='tag'>今日精读 · 生成失败</div><h2>精读未生成</h2>"
            "<p>%s</p></section>" % esc(dr.get("error"))
        )

    body = """
<div class="wrap">
  <header class="top">
    <div>
      <div class="brand">Paper Radar · 每日汇报</div>
      <h1>{date} 论文调研</h1>
      <div class="field">方向：{field}</div>
      <div class="field" style="font-size:12.5px;margin-top:4px">本期范围：{scope}{scope_note}</div>
    </div>
    <div class="field">共 {n} 篇 · {dist}<br>预印本 {pre} 篇 · 时间窗 {window} 天</div>
  </header>
  <div class="statrow">
    <div class="stat"><b>{n}</b><span>本期论文</span></div>
    <div class="stat"><b>{journals}</b><span>期刊</span></div>
    <div class="stat"><b>{confs}</b><span>会议</span></div>
    <div class="stat"><b>{pre}</b><span>预印本</span></div>
    <div class="stat"><b>{pool}</b><span>候选池</span></div>
  </div>
  <div class="day-meta">生成于 {generated} · 模型 {model} · {origins} · 仅收录 {floor} 之后的文献（优先近五年）
    {fallback}</div>
  {deepread}
  {cards}
  <footer class="end">
    六问模板：①解决的问题 ②数据与方法 ③关键结果 ④创新位置 ⑤遗留问题 ⑥后续方向。<br>
    本报告由脚本抓取真实元数据 + 模型归纳生成，标「摘要未披露」处需回查原文；预印本未经同行评议，引用前请核对正式版本。
  </footer>
</div>"""
    counts = {"journal": 0, "conference": 0, "preprint": 0}
    for p in papers:
        counts[p.get("kind", "journal")] = counts.get(p.get("kind", "journal"), 0) + 1
    return "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>" \
           "<meta name='viewport' content='width=device-width,initial-scale=1'>" \
           "<title>每日十篇泛读 · %s | 论文雷达</title><style>%s</style></head><body>%s%s</body></html>" % (
               day_tag(record.get("date")), CSS,
               body.format(
                   date=esc(record.get("date")), field=esc(record.get("field")),
                   scope=esc(record.get("scope") or ""),
                   scope_note=("（%s）" % esc(record.get("scope_note"))) if record.get("scope_note") else "",
                   n=len(papers), dist=esc(dist), pre=counts.get("preprint", 0),
                   window=esc(stats.get("window_days")),
                   journals=counts.get("journal", 0), confs=counts.get("conference", 0),
                   pool=esc(stats.get("pool", "")),
                   generated=esc(record.get("generated_at", "")),
                   model=esc(record.get("model", "")),
                   floor=esc(record.get("min_pub_date") or "2020-01-01"),
                   origins=esc(record.get("origins_note") or "数据源 OpenAlex + arXiv"),
                   fallback=('· <b style="color:#8a5a00">%s</b>' % esc(record.get("fallback_note"))) if record.get("fallback_note") else "",
                   deepread=deepread_block,
                   cards="\n".join(card(p, i + 1) for i, p in enumerate(papers)),
               ),
               BOOT_JS.replace("{{DAY}}", day_tag(record.get("date"))),
           )


def save(record):
    stem = record.get("stem") or record["date"]
    json_path = os.path.join(REPORTS_DIR, stem + ".json")
    html_path = os.path.join(REPORTS_DIR, stem + ".html")
    write_json(json_path, record)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(render_daily(record))
    return json_path, html_path


def deepread_path(date):
    return os.path.join(DEEPREAD_DIR, date + ".html")
