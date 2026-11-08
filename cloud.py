# -*- coding: utf-8 -*-
"""
社会热点雷达 · GitHub Actions 云端版（零第三方依赖）

    python cloud.py collect   采集一次，合并写入 data/YYYY-MM-DD.json
    python cloud.py report    聚合最近 7 天，生成 docs/index.html（供 GitHub Pages）

与本机版的区别：云端容器每次运行后销毁，所以不用 SQLite，
改为把每天的去重话题存成纯文本 JSON，由 workflow 提交回仓库。
不需要 S3、API Key 或任何第三方包。
"""
import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import hotspot as h

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DOCS = BASE / "docs"
BJ = timezone(timedelta(hours=8))
DAYS = 7


def now():
    return datetime.now(BJ)


def collect():
    cfg = h.load_config()
    DATA.mkdir(parents=True, exist_ok=True)
    day = now().strftime("%Y-%m-%d")
    ts = now().isoformat(timespec="seconds")
    path = DATA / (day + ".json")

    doc = {"date": day, "runs": 0, "topics": {}}
    if path.exists():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    topics = doc.setdefault("topics", {})

    total, ok = 0, 0
    for pl in cfg["platforms"]:
        if not pl.get("enabled", True):
            continue
        try:
            items = h.fetch(pl["id"], pl["domain"])
            ok += 1
            total += len(items)
            for rank, title, url in items:
                t = topics.get(title)
                if t is None:
                    t = {"count": 0, "platforms": [], "best": rank,
                         "first": ts, "last": ts, "url": url}
                    topics[title] = t
                t["count"] += 1
                if pl["name"] not in t["platforms"]:
                    t["platforms"].append(pl["name"])
                t["best"] = min(t["best"], rank)
                t["last"] = ts
                if url and not t["url"]:
                    t["url"] = url
        except Exception as e:
            print("  %s failed: %s" % (pl["name"], str(e)[:100]))

    doc["runs"] = doc.get("runs", 0) + 1
    doc["date"] = day
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8")

    cutoff = (now() - timedelta(days=int(cfg["retention_days"]))).strftime("%Y-%m-%d")
    for f in DATA.glob("20??-??-??.json"):
        if f.stem < cutoff:
            f.unlink()
    print("collect: %d items / %d platforms ok / %d topics / %d runs on %s" %
          (total, ok, len(topics), doc["runs"], day))


def merge_days(start):
    merged = {}
    runs = 0
    day_rows = []
    for p in sorted(DATA.glob("20??-??-??.json")):
        if p.stem < start:
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        topics = doc.get("topics", {})
        runs += doc.get("runs", 0)
        day_rows.append((p.stem, doc.get("runs", 0), len(topics)))
        for title, t in topics.items():
            m = merged.get(title)
            if m is None:
                m = {"count": 0, "platforms": [], "best": t["best"],
                     "first": t["first"], "last": t["last"], "url": t.get("url", "")}
                merged[title] = m
            m["count"] += t["count"]
            for pl in t["platforms"]:
                if pl not in m["platforms"]:
                    m["platforms"].append(pl)
            m["best"] = min(m["best"], t["best"])
            if t["first"] < m["first"]:
                m["first"] = t["first"]
            if t["last"] > m["last"]:
                m["last"] = t["last"]
            if t.get("url") and not m["url"]:
                m["url"] = t["url"]
    return merged, runs, day_rows


def render(top, merged, runs, day_rows, start, top_n):
    end = now()
    period = "%s ~ %s" % (start, end.strftime("%Y-%m-%d %H:%M"))
    raw = sum(t["count"] for t in merged.values())
    platforms = set()
    plat_count = Counter()
    for t in merged.values():
        for p in t["platforms"]:
            platforms.add(p)
            plat_count[p] += t["count"]
    issue = "%s 年第 %d 期" % (end.strftime("%Y"), int(end.strftime("%W")))

    parts = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>社会热点周报 · %s</title>' % h.esc(issue), h.STYLE,
        '</head><body><div class="wrap">',
        '<header class="masthead">',
        '<div><p class="kicker">WEEKLY PUBLIC ATTENTION DIGEST</p>',
        '<h1>社会热点周报</h1></div>',
        '<div class="issue"><b>%s</b>统计周期 %s<br>每周五更新 · 云端生成</div>' %
        (h.esc(issue), h.esc(period)),
        '</header>',
        '<div class="dateline">',
        '<span>数据源 <b>%d 个公开热榜平台</b></span>' % len(platforms),
        '<span>生成于 <b>%s</b></span>' % h.esc(end.strftime("%Y-%m-%d %H:%M")),
        '<span>本期收录 <b>%d</b> 个话题</span>' % len(merged),
        '</div>',
        '<div class="stats">',
    ]
    for label, val in (("采集批次", runs), ("原始条目", raw),
                       ("去重话题", len(merged)), ("覆盖平台", len(platforms))):
        parts.append('<div class="stat"><b>%s</b><span>%s</span></div>' % (val, label))
    parts.append('</div>')

    if top:
        parts.append('<h2>本期焦点<span class="en">Editor\'s pick</span></h2>')
        parts.append('<p class="lede">按出现次数、跨平台覆盖与最佳排名综合排序，取前三条。</p>')
        parts.append('<div class="focus">')
        for i, (title, t) in enumerate(top[:3], 1):
            parts.append('<article><div class="no">焦点 %02d</div>' % i)
            parts.append('<h3>%s</h3>' % h.make_link(title, t["url"]))
            parts.append('<div class="meta">出现 %d 次 · %d 个平台 · 最佳第 %d 名</div>' %
                         (t["count"], len(t["platforms"]), t["best"]))
            parts.append('</article>')
        parts.append('</div>')

    parts.append('<section class="rank-section">')
    parts.append('<h2 id="rank-head">热度榜单<span class="en">Ranking</span></h2>')
    rest = top[3:]
    if not top:
        parts.append('<div class="empty">暂无数据。采集 workflow 运行几次后这里就会有内容。</div>')
    elif not rest:
        parts.append('<div class="empty">本期话题较少，已全部列于上方焦点区。</div>')
    else:
        parts.append('<p class="lede">点击标题跳转原榜单；点击任意列表头可排序，再点一次反转方向。'
                     '向下滚动时标题与表头会吸附在页面顶部。</p>')
        parts.append('<table class="sortable" id="ranking"><thead><tr>'
                     '<th class="sortable" data-col="0" data-type="num" data-first="asc" title="按序号排序">序</th>'
                     '<th class="sortable" data-col="1" data-type="text" data-first="asc" title="按话题名称排序">话题</th>'
                     '<th class="sortable" data-col="2" data-type="num" data-first="desc" title="按出现次数排序">出现</th>'
                     '<th class="sortable" data-col="3" data-type="num" data-first="desc" title="按上榜平台数排序">上榜平台</th>'
                     '<th class="sortable" data-col="4" data-type="num" data-first="asc" title="按最佳排名排序">最佳</th>'
                     '<th class="sortable hide" data-col="5" data-type="text" data-first="asc" title="按首次出现时间排序">首次出现</th>'
                     '</tr></thead><tbody>')
        for i, (title, t) in enumerate(rest, 4):
            tags = "".join('<span class="tag">%s</span>' % h.esc(p)
                           for p in sorted(t["platforms"]))
            first = t["first"][:16].replace("T", " ")
            parts.append(
                "<tr><td class='rank' data-v='%d'>%d</td>"
                "<td class='topic' data-v='%s'>%s</td>"
                "<td class='num' data-v='%d'>%d</td>"
                "<td data-v='%d'>%s</td>"
                "<td class='num' data-v='%d'>%d</td>"
                "<td class='num hide' data-v='%s'>%s</td></tr>" %
                (i, i, h.esc(title), h.make_link(title, t["url"]),
                 t["count"], t["count"], len(t["platforms"]), tags,
                 t["best"], t["best"], h.esc(first), h.esc(first)))
        parts.append('</tbody></table>')
    parts.append('</section>')

    if plat_count:
        parts.append('<h2>平台覆盖<span class="en">Sources</span></h2>')
        parts.append('<p class="lede">各平台累计被采集到的条目占比。</p>')
        total = sum(plat_count.values())
        parts.append('<div class="bars">')
        for name, cnt in plat_count.most_common():
            pct = 100.0 * cnt / total
            parts.append('<div class="bar"><span class="nm">%s</span>'
                         '<span class="track"><span class="fill" style="width:%.1f%%"></span></span>'
                         '<span class="v">%d · %.1f%%</span></div>' %
                         (h.esc(name), pct, cnt, pct))
        parts.append('</div>')

    if day_rows:
        parts.append('<h2>每日采集概览<span class="en">Log</span></h2>')
        parts.append('<table class="sortable"><thead><tr>'
                     '<th class="sortable" data-col="0" data-type="text" data-first="desc" title="按日期排序">日期</th>'
                     '<th class="sortable" data-col="1" data-type="num" data-first="desc" title="按采集次数排序">采集次数</th>'
                     '<th class="sortable" data-col="2" data-type="num" data-first="desc" title="按当日话题数排序">当日话题</th>'
                     '</tr></thead><tbody>')
        for d, r, c in day_rows:
            parts.append("<tr><td data-v='%s'>%s</td><td class='num' data-v='%d'>%d</td>"
                         "<td class='num' data-v='%d'>%d</td></tr>" %
                         (h.esc(d), h.esc(d), r, r, c, c))
        parts.append('</tbody></table>')

    parts.append('<div class="foot"><span>社会热点周报 · 由 GitHub Actions 定时生成 · '
                 '数据源 newsnow 公共热榜接口</span>'
                 '<span>仅汇总公开标题与链接，不含任何账号或密钥 · %s</span></div>' %
                 h.esc(end.strftime("%Y")))
    parts.append('</div>')
    parts.append("""<script>
(function(){
  var head=document.getElementById('rank-head');
  if(head){document.documentElement.style.setProperty('--head-h',head.offsetHeight+'px');}
  document.querySelectorAll('table.sortable').forEach(function(table){
    var tbody=table.tBodies[0];
    var ths=Array.prototype.slice.call(table.querySelectorAll('thead th.sortable'));
    ths.forEach(function(th){
      th.setAttribute('tabindex','0');
      th.setAttribute('role','button');
      function sortIt(){
        var col=parseInt(th.getAttribute('data-col'),10);
        var type=th.getAttribute('data-type');
        var dir=th.getAttribute('data-dir');
        dir=dir?(dir==='asc'?'desc':'asc'):(th.getAttribute('data-first')||'asc');
        ths.forEach(function(o){o.removeAttribute('data-dir');o.removeAttribute('aria-sort');});
        th.setAttribute('data-dir',dir);
        th.setAttribute('aria-sort',dir==='asc'?'ascending':'descending');
        var rows=Array.prototype.slice.call(tbody.rows);
        rows.sort(function(a,b){
          var av=String(a.cells[col].getAttribute('data-v')||a.cells[col].textContent).trim();
          var bv=String(b.cells[col].getAttribute('data-v')||b.cells[col].textContent).trim();
          var r=0;
          if(type==='num'){r=(parseFloat(av)||0)-(parseFloat(bv)||0);}
          else{r=av.localeCompare(bv,'zh-CN');}
          if(r===0){
            var ai=parseInt(a.cells[0].getAttribute('data-v'),10)||0;
            var bi=parseInt(b.cells[0].getAttribute('data-v'),10)||0;
            return ai-bi;
          }
          return dir==='asc'?r:-r;
        });
        rows.forEach(function(row){tbody.appendChild(row);});
      }
      th.addEventListener('click',sortIt);
      th.addEventListener('keydown',function(e){
        if(e.key==='Enter'||e.key===' '){e.preventDefault();sortIt();}
      });
    });
  });
})();
</script>""")
    parts.append('</body></html>')
    return "".join(parts)


def report():
    cfg = h.load_config()
    DATA.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    start = (now() - timedelta(days=DAYS)).strftime("%Y-%m-%d")
    merged, runs, day_rows = merge_days(start)
    ranked = sorted(merged.items(),
                    key=lambda kv: (-h.topic_score(kv[1]), -kv[1]["count"], kv[0]))
    top_n = max(1, min(200, int(cfg["top_n"])))
    html = render(ranked[:top_n], merged, runs, day_rows, start, top_n)
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    (DOCS / ("week-%s.html" % now().strftime("%Y%m%d"))).write_text(html, encoding="utf-8")
    print("report: %d topics / %d runs / docs/index.html %.1f KB" %
          (len(merged), runs, len(html.encode("utf-8")) / 1024.0))


def main():
    ap = argparse.ArgumentParser(description="社会热点雷达云端版")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("collect")
    sub.add_parser("report")
    a = ap.parse_args()
    if a.cmd == "collect":
        collect()
    elif a.cmd == "report":
        report()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
