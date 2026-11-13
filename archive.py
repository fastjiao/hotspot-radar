# -*- coding: utf-8 -*-
"""
往期归档（社会热点日报 + 热点信息差）· 单一来源

    import archive
    archive.refresh()                      # 重扫 docs/，写 archive.json 与 archive.html
    archive.head_css()                     # 放进 </head> 前
    archive.widget("daily", "2026-11-13")  # 放进内容容器末尾
    python archive.py patch                # 给旧页面幂等注入归档组件

设计约束：
  1. 索引单独存 docs/archive.json，页面用相对路径 fetch 它；旧页面无需
     重写就能自动显示最新归档。生成器同时内嵌一份静态列表作为兜底，
     所以用 file:// 直接打开也能看到往期。
  2. 链接一律相对路径（day-*.html / archive.html），主域与 github.io
     项目页都能用，自定义域名到期后一行都不用改。
  3. 样式与脚本统一 wb-arch 前缀，不与页面原有全局 CSS / JS 串味。
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import site_nav

BASE = Path(__file__).resolve().parent
DOCS = BASE / "docs"
BJ = timezone(timedelta(hours=8))
WEEK = "一二三四五六日"

CSS = """
.wb-arch{max-width:900px;margin:48px auto 0;padding:18px 0 0;
border-top:1px solid #d8d3c8;
font:13px/1.6 "Inter","PingFang SC","Microsoft YaHei",system-ui,sans-serif}
@media(max-width:940px){.wb-arch{margin-left:20px;margin-right:20px}}
.wb-arch-head{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;margin-bottom:12px}
.wb-arch h2{margin:0;padding:0;border:0;font-size:16px;font-weight:500;color:inherit;
letter-spacing:0;font-family:inherit}
.wb-arch a.wb-arch-all{font-size:12px;color:#6366F1;text-decoration:none;white-space:nowrap}
.wb-arch a.wb-arch-all:hover{text-decoration:underline}
.wb-arch-list{display:flex;flex-wrap:wrap;gap:8px}
.wb-arch-list a{display:inline-block;padding:5px 11px;border:1px solid #d8d3c8;border-radius:999px;
background:#fff;color:#4a4d58;text-decoration:none;font-size:12px;white-space:nowrap}
.wb-arch-list a:hover{border-color:#6366F1;color:#6366F1;text-decoration:none}
.wb-arch-list a.is-current{background:#6366F1;border-color:#6366F1;color:#fff}
.wb-arch-empty{color:#6b675f;font-size:12px}
@media(max-width:600px){.wb-arch h2{font-size:14px}}
"""

PAGE_CSS = """
*{box-sizing:border-box}body{margin:0;background:#faf8f3;color:#1b1a17;
font:15px/1.75 "Inter","PingFang SC","Microsoft YaHei",system-ui,sans-serif}
.wb-arch-page{max-width:900px;margin:0 auto;padding:40px 22px 80px}
h1{font-size:28px;margin:0 0 6px;letter-spacing:1px}
.lead{color:#6b675f;font-size:13px;margin:0 0 36px}
.wb-arch-page section{margin-bottom:36px}
.wb-arch-page h2{font-size:19px;border-bottom:1px solid #d8d3c8;padding-bottom:8px;margin:0 0 16px}
.wb-arch-page .wb-arch{margin:0;border:0;padding:0}
@media(max-width:600px){.wb-arch-page{padding:26px 15px 60px}h1{font-size:23px}}
"""


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def head_css():
    """返回可放在 </head> 前的独立样式块。"""
    return '<style data-wb-arch>' + CSS + '</style>'


def _date_label(stem):
    """20261113 -> 2026-11-13 周五"""
    try:
        d = datetime.strptime(stem, "%Y%m%d")
        return "%s 周%s" % (d.strftime("%Y-%m-%d"), WEEK[d.weekday()])
    except ValueError:
        return stem


def _date_iso(stem):
    try:
        return datetime.strptime(stem, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return stem


def scan(extra=None):
    """扫描 docs/，返回 (日报列表, 信息差列表)，均按日期倒序。

    extra 是 [(kind, filename, stem), ...]，把本次即将生成、但还没落盘的
    文件提前写进索引，避免页面内嵌的往期列表缺少当期。
    """
    daily = []
    for kind, pattern in (("日报", "day-20??????.html"),
                          ("周报", "week-20??????.html")):
        for p in DOCS.glob(pattern):
            stem = p.name.split("-", 1)[1][:8]
            daily.append({"date": _date_iso(stem), "label": _date_label(stem),
                          "url": p.name, "type": kind})
    digest = []
    for p in DOCS.glob("digest-20??????.html"):
        stem = p.name.split("-", 1)[1][:8]
        digest.append({"date": _date_iso(stem), "label": _date_label(stem),
                       "url": p.name, "type": "信息差"})
    if not digest and (DOCS / "digest.html").exists():
        # 还没有快照时，把当前一期也算进归档，date 用文件修改时间
        ts = datetime.fromtimestamp((DOCS / "digest.html").stat().st_mtime, BJ)
        stem = ts.strftime("%Y%m%d")
        digest.append({"date": _date_iso(stem), "label": _date_label(stem),
                       "url": "digest.html", "type": "信息差"})
    for kind, name, stem in extra or ():
        item = {"date": _date_iso(stem), "label": _date_label(stem),
                "url": name, "type": "日报" if kind == "daily" else "信息差"}
        target = daily if kind == "daily" else digest
        if not any(x["url"] == name for x in target):
            target.append(item)
    # 同一天若同时存在旧周报与新日报，日报排在前面
    daily.sort(key=lambda x: (x["date"], 1 if x["type"] == "日报" else 0,
                              x["url"]), reverse=True)
    digest.sort(key=lambda x: (x["date"], x["url"]), reverse=True)
    return daily, digest


def read_index():
    """生成器在渲染页面前会先 refresh()，这里读到的就是最新索引。"""
    p = DOCS / "archive.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    daily, digest = scan()
    return {"daily": daily, "digest": digest}


def _links_html(items, current):
    if not items:
        return '<span class="wb-arch-empty">暂无历史存档，下一次生成后会出现在这里。</span>'
    out = []
    for it in items:
        cls = ' class="is-current"' if current and it["date"] == current else ""
        out.append('<a href="%s"%s>%s · %s</a>'
                   % (esc(it["url"]), cls, esc(it["type"]), esc(it["label"])))
    return "".join(out)


def widget(kind, current=None):
    """页面内嵌的「往期」区块。静态列表兜底 + fetch 最新索引自动刷新。"""
    if kind not in ("daily", "digest"):
        raise ValueError("unknown archive kind: %s" % kind)
    title = "往期日报" if kind == "daily" else "往期信息差"
    items = read_index().get(kind, [])
    shown = items[:12]
    html = [
        '<section class="wb-arch" data-wb-arch="%s">' % kind,
        '<div class="wb-arch-head"><h2>%s</h2>'
        '<a class="wb-arch-all" href="archive.html">全部归档 →</a></div>' % title,
        '<div class="wb-arch-list" data-wb-arch-list>%s</div>'
        % _links_html(shown, current),
        '</section>',
    ]
    js = """<script data-wb-arch>
(function(){
  var box=document.querySelector('.wb-arch[data-wb-arch="%s"]');
  if(!box||!window.fetch)return;
  var list=box.querySelector('[data-wb-arch-list]'),cur=%s;
  fetch('archive.json',{cache:'no-store'}).then(function(r){
    if(!r.ok)throw new Error('http '+r.status);return r.json();
  }).then(function(d){
    var items=d['%s']||[];if(!items.length)return;
    list.textContent='';
    items.slice(0,12).forEach(function(it){
      var a=document.createElement('a');a.href=it.url;
      a.textContent=(it.type?it.type+' · ':'')+it.label;
      if(cur&&it.date===cur)a.className='is-current';
      list.appendChild(a);
    });
    if(items.length>12){
      var m=document.createElement('a');m.href='archive.html';
      m.textContent='更多 '+(items.length-12)+' 期 →';list.appendChild(m);
    }
  }).catch(function(){});
})();
</script>""" % (kind, json.dumps(current or "", ensure_ascii=False), kind)
    html.append(js)
    return "".join(html)


def _archive_page(doc):
    daily, digest = doc.get("daily", []), doc.get("digest", [])
    p = ['<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>往期归档 · 社会热点日报与热点信息差</title>',
         '<style>%s</style>%s%s</head><body>' % (PAGE_CSS, site_nav.head_css(), head_css()),
         site_nav.nav_html("archive"), '<div class="wb-arch-page">',
         '<h1>往期归档</h1>',
         '<p class="lead">社会热点日报与热点信息差的完整历史 · 共 %d 期日报 / %d 期信息差 · '
         '索引更新于 %s</p>' % (len(daily), len(digest), esc(doc.get("updated", "")))]
    for title, kind, items in (("社会热点日报", "daily", daily),
                               ("热点信息差", "digest", digest)):
        p.append('<section><h2>%s</h2>' % title)
        p.append('<div class="wb-arch"><div class="wb-arch-list">%s</div></div>'
                 % _links_html(items, None))
        p.append('</section>')
    p.append('<p class="lead">索引文件 <a href="archive.json">archive.json</a> · '
             '由 GitHub Actions 每次生成时自动更新。</p>')
    p.append('</div></body></html>')
    return "\n".join(p)


def refresh(extra=None):
    """重扫 docs/，写 archive.json 与静态归档页。两个生成器都调用它。"""
    daily, digest = scan(extra)
    doc = {"updated": datetime.now(BJ).strftime("%Y-%m-%d %H:%M"),
           "daily": daily, "digest": digest}
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "archive.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (DOCS / "archive.html").write_text(_archive_page(doc), encoding="utf-8")
    return doc


def patch_file(path, kind, current):
    """给已有页面幂等注入 / 刷新归档组件。

    样式总是刷新（改了 CSS 后跑一次就能同步所有历史页）；
    组件只在缺失时注入，已内嵌的页面保持原位，线上由 fetch 更新列表。
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if "</head>" not in text or "</body>" not in text:
        return 0
    style = head_css()
    if re.search(r'<style data-wb-arch>.*?</style>', text, re.S):
        text = re.sub(r'<style data-wb-arch>.*?</style>', lambda m: style,
                      text, count=1, flags=re.S)
    else:
        text = text.replace('</head>', style + '\n</head>', 1)
    if 'data-wb-arch="' not in text:
        text = text.replace('</body>', widget(kind, current) + '\n</body>', 1)
    p.write_text(text, encoding="utf-8")
    return len(text)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 0
    cmd = sys.argv[1]
    if cmd == "refresh":
        doc = refresh()
        print("archive.json: %d daily / %d digest" %
              (len(doc["daily"]), len(doc["digest"])))
        print("archive.html written")
    elif cmd == "patch":
        refresh()
        targets = []
        for p in sorted(DOCS.glob("*.html")):
            if p.name == "archive.html":
                continue
            kind = "digest" if p.name.startswith("digest") else "daily"
            m = re.search(r"(20\d{6})", p.name)
            targets.append((p, kind, _date_iso(m.group(1)) if m else None))
        for path, kind, cur in targets:
            n = patch_file(path, kind, cur)
            print("%-28s %s" % (path.name, "patched" if n else "skipped"))
    else:
        print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
