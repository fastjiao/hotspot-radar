# -*- coding: utf-8 -*-
"""
热点信息差 · GitHub Actions 周报（零第三方依赖，纯标准库）

    python digest.py           生成 docs/digest.html + digest-YYYYMMDD.html 快照
    python digest.py --dry     只抓取并打印统计，不写文件

设计原则：
  1. 每个数据源独立 try/except，单个站点挂掉只标注、不影响整期产出；
  2. 不依赖 AI 也能出完整一期；配了 LLM_API_KEY 才追加「AI 解读」一节；
  3. digest.html 恒为最新一期，digest-YYYYMMDD.* 是往期快照；
     往期索引与归档页由 archive.py 统一维护。

可选环境变量（不配不报错）：
  LLM_API_KEY   OpenAI 兼容接口的密钥，放仓库 Secrets（不是 GITHUB_TOKEN）
  LLM_BASE_URL  默认 https://open.bigmodel.cn/api/paas/v4（智谱，国内直连）
  LLM_MODEL     默认 glm-4-flash（智谱永久免费模型）
                 换 Groq：base https://api.groq.com/openai/v1 / model openai/gpt-oss-120b
                 换 OpenRouter：base https://openrouter.ai/api/v1 / model <xxx>:free
  GITHUB_TOKEN  Actions 自动注入，用于调用 api.github.com（本地没有也能跑）

注意：Groq / Gemini 对中国大陆出口 IP 返回 403，本地连控制台都打不开，
所以默认改用国内可直连的智谱。GitHub Actions 的 runner 在美国，
若想换回 Groq 反而更快，只需设上面两个 Variables，不用改代码。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import archive
import site_nav

BASE = Path(__file__).resolve().parent
DOCS = BASE / "docs"
DATA = BASE / "data"
BJ = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

SOURCES = [
    ("江苏省教育考试院", "https://www.jseea.cn/",
     "https://www.jseea.cn/webfile/index/index_zkxx/"),
    ("江苏招生考试·职教高考", "https://www.jszs.com/zhijiaogaokao/",
     "https://www.jszs.com/"),
    ("中国江苏网教育频道", "https://edu.jschina.com.cn/",
     "https://edu.jschina.com.cn/"),
    ("江苏省沛县中等专业学校", "http://www.pxzj.com/",
     "http://www.pxzj.com/"),
    ("南京信息职业技术学院", "https://www.njcit.cn/",
     "https://zs.njcit.cn/"),
    ("徐州市中考信息发布平台", "https://www.jsxzeea.cn/",
     "http://www.jsxzeea.cn/"),
]

# 必须至少命中一个强相关词，避免把「教师资格考试」「普通高考」等混进来
STRONG = ("职教高考", "中职", "分段培养", "3+3", "转段", "学业水平", "学考",
          "中专", "五年制", "对口单招", "高职", "征求志愿", "专业技能", "文化统考")
WEAK = ("报名", "考试", "志愿", "录取", "公示", "简章", "计划", "成绩",
        "分数线", "确认", "截止", "名单", "招生")
EXCLUDE = ("教师资格", "普通话", "职称", "工会", "党建", "党史",
           "运动会", "教职工", "招聘教师", "校园安全")
NODE_KEYS = ("报名", "考试", "志愿", "录取", "公示", "简章", "计划",
             "成绩", "分数线", "转段", "确认", "截止")

CSS = """
:root{--paper:#faf8f3;--ink:#1b1a17;--red:#b23a24;--muted:#6b675f;--line:#d8d3c8}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);
font-family:"Songti SC","Noto Serif CJK SC",Georgia,serif;line-height:1.75}
.wrap{max-width:900px;margin:0 auto;padding:36px 22px 70px}
header{border-top:4px double var(--ink);border-bottom:1px solid var(--ink);padding:14px 0 18px}
h1{font-size:30px;margin:0;letter-spacing:2px}h2{font-size:19px;margin:38px 0 14px;
border-bottom:1px solid var(--line);padding-bottom:7px}h3{font-size:15px;margin:22px 0 8px}
.meta,.note{color:var(--muted);font-size:13px}.dateline{margin-top:8px;font-size:13px;color:var(--muted)}
.status{background:#f1ede3;border-left:3px solid var(--red);padding:10px 14px;margin:20px 0;font-size:13px}
.tag{display:inline-block;background:#eee7d9;color:#6a4b20;font-size:12px;padding:1px 7px;
border-radius:2px;margin-left:6px;white-space:nowrap}
.tag.none{background:#e9e6df;color:var(--muted)}
ul{list-style:none;padding:0;margin:0}li{padding:8px 0;border-bottom:1px dotted var(--line);font-size:14px}
a{color:#2b4d73;text-decoration:none}a:hover{text-decoration:underline}
.date{color:var(--muted);font-size:12px;margin-left:8px}
.empty{color:var(--muted);font-size:13px;padding:6px 0}
table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:6px 8px;
border-bottom:1px solid var(--line)}th{color:var(--muted);font-weight:normal}
.ai{background:#fff;border:1px solid var(--line);padding:16px 20px;white-space:pre-wrap;font-size:14px}
footer{margin-top:46px;border-top:1px solid var(--ink);padding-top:12px;color:var(--muted);font-size:12px}
@media(max-width:600px){h1{font-size:23px}.wrap{padding:22px 15px 50px}}
"""


def now():
    return datetime.now(BJ)


def get(url, timeout=12, retry=1):
    """抓一个 URL，自动处理编码，失败重试一次。"""
    last = None
    for _ in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/json,*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(500000)
                ctype = (resp.headers.get("Content-Type") or "").lower()
            if "charset=gb" in ctype or "charset=gbk" in ctype:
                return raw.decode("gb18030", "replace")
            for enc in ("utf-8", "gb18030"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", "replace")
        except Exception as e:
            last = e
    raise last


class LinkParser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.links = []
        self._href = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._buf = []

    def handle_data(self, data):
        if self._href is not None:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            if len(text) >= 6:
                self.links.append((text, self._href))
            self._href = None
            self._buf = []


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def find_date(text):
    m = re.search(r"(20\d{2})[-/年.]?(\d{1,2})[-/月.]?(\d{1,2})", text)
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(20\d{2})[-/年.]?(\d{1,2})月?", text)
    if m:
        return "%s-%02d" % (m.group(1), int(m.group(2)))
    return ""


def audience(title):
    """按「职教高考年份 = 中职毕业年份」推断届别；推断结果一律标注为预计。

    标题里常同时出现学考年份与职教高考年份
    （如「2026年学考和2027年中职职教高考报名」），
    此时优先取紧跟「职教高考」的年份，其次取最大年份。
    """
    years = [int(y) for y in re.findall(r"20\d{2}", title) if 2025 <= int(y) <= 2035]
    if not years:
        return "适用于：各届备考参考（以官方原文为准）"
    m = re.search(r"(20\d{2})年(?:中职)?职教高考", title)
    y = int(m.group(1)) if m else max(years)
    return "适用于：%d年6月毕业的中职生（%d届，预计）" % (y, y % 100)


def collect_sites():
    out = []
    for name, primary, fallback in SOURCES:
        item = {"name": name, "url": primary, "items": [], "error": ""}
        page, used, last_err = None, primary, ""
        for candidate in (primary, fallback):
            try:
                page = get(candidate)
                used = candidate
                break
            except Exception as e:
                last_err = "%s: %s" % (type(e).__name__, str(e)[:80])
        if page is None:
            item["error"] = last_err or "未知错误"
            out.append(item)
            continue
        try:
            parser = LinkParser()
            parser.feed(page)
            seen = set()
            scored = []
            for text, href in parser.links:
                strong = sum(1 for k in STRONG if k in text)
                if strong == 0 or len(text) > 90 or any(x in text for x in EXCLUDE):
                    continue
                hits = strong * 3 + sum(1 for k in WEAK if k in text)
                link = urljoin(used, href or "")
                if not link.startswith("http") or link in seen:
                    continue
                seen.add(link)
                scored.append((hits, text, link))
            scored.sort(key=lambda x: -x[0])
            for _, text, link in scored[:12]:
                item["items"].append({
                    "title": text, "url": link,
                    "date": find_date(text + " " + link),
                    "tag": audience(text),
                })
            if not item["items"]:
                item["error"] = "页面可访问，但未匹配到职教高考相关条目"
        except Exception as e:
            item["error"] = "%s: %s" % (type(e).__name__, str(e)[:90])
        out.append(item)
    return out


def collect_xwlb(days=7):
    """央视《新闻联播》每日分条目标题，页面里的 title 属性就是条目名。"""
    out = []
    today = now().date()
    for i in range(days, 0, -1):
        d = today - timedelta(days=i)
        stamp = d.strftime("%Y%m%d")
        label = d.strftime("%Y-%m-%d")
        url = "https://tv.cctv.com/lm/xwlb/day/%s.shtml" % stamp
        row = {"date": label, "url": url, "items": [], "error": ""}
        try:
            page = get(url, timeout=7)
            titles = re.findall(r'title="(\[视频\][^"]{4,})"', page)
            uniq = []
            for t in titles:
                t = re.sub(r"^\[视频\]", "", t).strip()
                if t and t not in uniq:
                    uniq.append(t)
            row["items"] = uniq[:25]
            if not row["items"]:
                row["error"] = "当日页面暂无条目"
        except Exception as e:
            row["error"] = "%s（可能尚未更新或被网络拦截）" % type(e).__name__
        out.append(row)
    return out


def collect_github(days=7):
    since = (now().date() - timedelta(days=days)).isoformat()
    url = ("https://api.github.com/search/repositories?q=created:>%s"
           "&sort=stars&order=desc&per_page=10" % since)
    headers = {"Accept": "application/vnd.github+json", "User-Agent": UA}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [{"title": x["full_name"], "url": x["html_url"],
             "stars": x["stargazers_count"],
             "desc": (x.get("description") or "")[:110]}
            for x in data.get("items", [])]


def collect_radar(days=7):
    """复用本仓库 collect workflow 产出的 data/*.json，不额外联网。"""
    if not DATA.exists():
        return []
    cutoff = (now().date() - timedelta(days=days)).isoformat()
    merged = {}
    for path in sorted(DATA.glob("*.json")):
        if path.stem < cutoff:
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for title, t in (doc.get("topics") or {}).items():
            cur = merged.setdefault(title, {"count": 0, "url": t.get("url", ""),
                                            "platforms": []})
            cur["count"] += t.get("count", 0)
            for p in t.get("platforms", []):
                if p not in cur["platforms"]:
                    cur["platforms"].append(p)
    rows = [{"title": k, "count": v["count"], "url": v["url"],
             "platforms": len(v["platforms"])} for k, v in merged.items()]
    rows.sort(key=lambda x: -x["count"])
    return rows[:20]


def ai_digest(text):
    key = os.environ.get("LLM_API_KEY", "").strip()
    if not key:
        return None, "未配置 LLM_API_KEY（仓库 Settings → Secrets and variables → Actions）"
    # 默认智谱：控制台与 API 国内可直连、glm-4-flash 永久免费、中文摘要够用。
    # Groq / Gemini 对大陆出口 IP 返回 403，本地拿不到 key，故不作默认。
    # 如需换回，设 LLM_BASE_URL=https://api.groq.com/openai/v1
    # 与 LLM_MODEL=openai/gpt-oss-120b 即可（Actions runner 在美国，更快）。
    # workflow 里 ${{ vars.X }} 未配置时会展开成空字符串（变量存在但值为空），
    # os.environ.get 的默认值在这种情况下不生效，必须用 or 显式兜底。
    base = ((os.environ.get("LLM_BASE_URL") or "").strip().rstrip("/")
            or "https://open.bigmodel.cn/api/paas/v4")
    model = (os.environ.get("LLM_MODEL") or "").strip() or "glm-4-flash"
    prompt = (
        "你是中职职教高考与就业趋势分析助手。下面是本周采集到的原始标题，"
        "请输出中文周报解读：①本周最重要的 3-5 个时间节点，"
        "每条标注适用的具体人群（如 2028 年 6 月毕业的中职生 / 28 届）；"
        "②从新闻联播里找出与人工智能、计算机网络、就业相关的信号并解读行业趋势；"
        "③给 28 届中职生列一条本周待办。没有依据的不写，不要编造日期。\n\n"
        + text[:6000]
    )
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0.3, "max_tokens": 1600}).encode("utf-8")
    last = ""
    for attempt in range(2):
        req = urllib.request.Request(base + "/chat/completions", data=body, headers={
            "Authorization": "Bearer " + key, "Content-Type": "application/json",
            "User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip(), None
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            last = "HTTP %s %s" % (e.code, detail)
            if e.code not in (429, 500, 502, 503, 504):
                break
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, str(e)[:150])
        if attempt == 0:
            time.sleep(3)
    return None, last


def render_html(ctx):
    p = []
    p.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    p.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    p.append('<title>热点信息差 · %s</title><style>%s</style>%s%s</head><body>%s<div class="wrap">'
             % (esc(ctx["date"]), CSS, site_nav.head_css(), archive.head_css(),
                site_nav.nav_html("digest")))
    p.append('<header><h1>热点信息差</h1><div class="dateline">第 %s 期 · %s — %s · 每周五更新</div></header>'
             % (esc(ctx["issue"]), esc(ctx["start"]), esc(ctx["date"])))

    ok = sum(1 for s in ctx["sites"] if not s["error"])
    p.append('<div class="status"><strong>本期采集状态</strong>：政务/招生站点 %d/%d 可用；'
             '新闻联播 %d/%d 天取到；AI 解读：%s。失败源只标注、不影响其余内容。</div>'
             % (ok, len(ctx["sites"]),
                sum(1 for d in ctx["xwlb"] if d["items"]), len(ctx["xwlb"]),
                esc(ctx["ai_status"])))

    p.append('<h2>本周时间节点提醒</h2>')
    if ctx["nodes"]:
        p.append('<ul>')
        for n in ctx["nodes"]:
            p.append('<li><a href="%s" target="_blank" rel="noopener">%s</a>'
                     '<span class="tag">%s</span></li>'
                     % (esc(n["url"]), esc(n["title"]), esc(n["tag"])))
        p.append('</ul>')
    else:
        p.append('<div class="empty">本期各站点未检出带报名 / 考试 / 录取等节点词的条目。</div>')

    p.append('<h2>一、中职 · 职教高考信息</h2>')
    for s in ctx["sites"]:
        p.append('<h3><a href="%s" target="_blank" rel="noopener">%s</a></h3>'
                 % (esc(s["url"]), esc(s["name"])))
        if s["error"]:
            p.append('<div class="empty">未取到：%s</div>' % esc(s["error"]))
        elif not s["items"]:
            p.append('<div class="empty">本周无中职相关新信息。</div>')
        else:
            p.append('<ul>')
            for it in s["items"]:
                p.append('<li><a href="%s" target="_blank" rel="noopener">%s</a>'
                         '<span class="date">%s</span><span class="tag">%s</span></li>'
                         % (esc(it["url"]), esc(it["title"]),
                            esc(it["date"] or "日期未标注"), esc(it["tag"])))
            p.append('</ul>')

    p.append('<h2>二、新闻联播（本周逐日）</h2>')
    for d in ctx["xwlb"]:
        p.append('<h3><a href="%s" target="_blank" rel="noopener">%s</a></h3>'
                 % (esc(d["url"]), esc(d["date"])))
        if d["items"]:
            p.append('<ul>' + ''.join('<li>%s</li>' % esc(t) for t in d["items"]) + '</ul>')
        else:
            p.append('<div class="empty">%s</div>' % esc(d["error"] or "无条目"))

    p.append('<h2>三、GitHub 本周新建高星项目</h2>')
    if ctx["github"]:
        p.append('<table><tr><th>项目</th><th>星标</th><th>简介</th></tr>')
        for g in ctx["github"]:
            p.append('<tr><td><a href="%s" target="_blank" rel="noopener">%s</a></td>'
                     '<td>%s</td><td>%s</td></tr>'
                     % (esc(g["url"]), esc(g["title"]), g["stars"], esc(g["desc"])))
        p.append('</table>')
    else:
        p.append('<div class="empty">GitHub API 本次未取到（网络或限流），不影响其余内容。</div>')

    p.append('<h2>四、社会热点（来自热点雷达）</h2>')
    if ctx["radar"]:
        p.append('<table><tr><th>话题</th><th>上榜次数</th><th>平台数</th></tr>')
        for r in ctx["radar"]:
            title = ('<a href="%s" target="_blank" rel="noopener">%s</a>'
                     % (esc(r["url"]), esc(r["title"]))) if r["url"] else esc(r["title"])
            p.append('<tr><td>%s</td><td>%d</td><td>%d</td></tr>'
                     % (title, r["count"], r["platforms"]))
        p.append('</table>')
    else:
        p.append('<div class="empty">暂无近 7 天采集数据（需先运行 Hotspot collect workflow）。</div>')

    p.append('<h2>五、AI 解读</h2>')
    if ctx["ai_text"]:
        p.append('<div class="ai">%s</div>' % esc(ctx["ai_text"]))
    else:
        p.append('<div class="empty">%s。<br>启用方式：仓库 Settings → Secrets and variables '
                 '→ Actions → New repository secret，添加 <code>LLM_API_KEY</code>'
                 '（默认用智谱 open.bigmodel.cn 的免费 glm-4-flash，国内可直连）；'
                 '若要换 Groq / OpenRouter，再在 Variables 里设 LLM_BASE_URL / LLM_MODEL。'
                 '配置后本会自动追加趋势解读与 28 届待办，无需改代码。</div>'
                 % esc(ctx["ai_status"]))

    p.append('<footer>适用对象标签按「职教高考年份 = 中职毕业年份」自动推断，'
             '凡推断结果均标注「预计」，请以官方原文为准。数据源见各节链接。'
             '由 GitHub Actions 于 %s 生成。</footer>' % esc(ctx["generated"]))
    p.append(archive.widget("digest", ctx["date"]))
    p.append('</div></body></html>')
    return "\n".join(p)


def render_md(ctx):
    p = ["# 热点信息差 · %s" % ctx["date"], "",
         "> 周期 %s — %s ｜ 采集状态：站点 %d/%d 可用 ｜ AI 解读：%s"
         % (ctx["start"], ctx["date"],
            sum(1 for s in ctx["sites"] if not s["error"]),
            len(ctx["sites"]), ctx["ai_status"]), "",
         "## 本周时间节点提醒", ""]
    if ctx["nodes"]:
        for n in ctx["nodes"]:
            p.append("- [%s](%s) ｜ %s" % (n["title"], n["url"], n["tag"]))
    else:
        p.append("本期未检出带节点词的条目。")
    p += ["", "## 一、中职 · 职教高考信息", ""]
    for s in ctx["sites"]:
        p += ["### [%s](%s)" % (s["name"], s["url"]), ""]
        if s["error"]:
            p.append("未取到：%s" % s["error"])
        elif not s["items"]:
            p.append("本周无中职相关新信息。")
        else:
            for it in s["items"]:
                p.append("- [%s](%s) ｜ %s ｜ %s"
                         % (it["title"], it["url"], it["date"] or "日期未标注", it["tag"]))
        p.append("")
    p += ["## 二、新闻联播（本周逐日）", ""]
    for d in ctx["xwlb"]:
        p += ["### [%s](%s)" % (d["date"], d["url"]), ""]
        p += ["- %s" % t for t in d["items"]] or ["未取到：%s" % (d["error"] or "无条目")]
        p.append("")
    p += ["## 三、GitHub 本周新建高星项目", ""]
    p += ["- [%s](%s) ｜ %s ★ ｜ %s" % (g["title"], g["url"], g["stars"], g["desc"])
          for g in ctx["github"]] or ["GitHub API 本次未取到。"]
    p += ["", "## 四、社会热点", ""]
    p += ["- %s ｜ 上榜 %d 次 ｜ %d 平台" % (r["title"], r["count"], r["platforms"])
          for r in ctx["radar"]] or ["暂无近 7 天采集数据。"]
    p += ["", "## 五、AI 解读", "",
          ctx["ai_text"] or ("%s。配置 LLM_API_KEY 后自动生成。" % ctx["ai_status"]), "",
          "---", "", "往期归档：archive.html", "",
          "适用对象为按年份自动推断，标注「预计」者请以官方原文为准。", ""]
    return "\n".join(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只打印统计，不写文件")
    args = ap.parse_args()

    today = now()
    start = (today.date() - timedelta(days=7)).isoformat()
    ctx = {
        "date": today.date().isoformat(),
        "start": start,
        "issue": today.strftime("%Y%U"),
        "generated": today.strftime("%Y-%m-%d %H:%M") + " (UTC+8)",
    }

    print("[1/5] 抓取政务 / 招生站点 ...")
    ctx["sites"] = collect_sites()
    for s in ctx["sites"]:
        print("      %-22s %2d 条 %s" % (s["name"], len(s["items"]), s["error"] or "OK"))

    print("[2/5] 抓取新闻联播 ...")
    ctx["xwlb"] = collect_xwlb()
    print("      %d/%d 天有条目" % (sum(1 for d in ctx["xwlb"] if d["items"]), len(ctx["xwlb"])))

    print("[3/5] GitHub 本周高星项目 ...")
    try:
        ctx["github"] = collect_github()
    except Exception as e:
        print("      跳过：%s" % type(e).__name__)
        ctx["github"] = []
    print("      %d 条" % len(ctx["github"]))

    print("[4/5] 读取热点雷达数据 ...")
    ctx["radar"] = collect_radar()
    print("      %d 条" % len(ctx["radar"]))

    nodes = []
    seen = set()
    for s in ctx["sites"]:
        for it in s["items"]:
            if any(k in it["title"] for k in NODE_KEYS) and it["title"] not in seen:
                seen.add(it["title"])
                nodes.append(it)
    ctx["nodes"] = nodes[:12]

    print("[5/5] AI 解读 ...")
    payload = []
    for s in ctx["sites"]:
        payload.append("[%s] %s" % (s["name"], "；".join(i["title"] for i in s["items"])))
    for d in ctx["xwlb"]:
        payload.append("[新闻联播 %s] %s" % (d["date"], "；".join(d["items"])))
    payload.append("[GitHub] " + "；".join(g["title"] for g in ctx["github"]))
    ctx["ai_text"], ai_err = ai_digest("\n".join(payload))
    model_label = (os.environ.get("LLM_MODEL") or "").strip() or "glm-4-flash"
    ctx["ai_status"] = ("已启用（%s）" % model_label) if ctx["ai_text"] else (ai_err or "未启用")
    print("      %s" % ctx["ai_status"])

    if args.dry:
        print("\n--dry：未写文件。")
        return 0

    DOCS.mkdir(parents=True, exist_ok=True)
    stamp = today.strftime("%Y%m%d")
    snap_html = "digest-%s.html" % stamp
    snap_md = "digest-%s.md" % stamp
    # 把本期快照提前登记进索引，页面内嵌的往期列表才不会缺当期
    archive.refresh(extra=[("digest", snap_html, stamp)])
    html, md = render_html(ctx), render_md(ctx)
    (DOCS / "digest.html").write_text(html, encoding="utf-8")
    (DOCS / "digest.md").write_text(md, encoding="utf-8")
    (DOCS / snap_html).write_text(html, encoding="utf-8")
    (DOCS / snap_md).write_text(md, encoding="utf-8")
    print("已生成 docs/digest.html + %s (%.1f KB)"
          % (snap_html, len(html.encode("utf-8")) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
