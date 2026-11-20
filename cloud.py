# -*- coding: utf-8 -*-
"""
社会热点雷达 · GitHub Actions 云端版（零第三方依赖）

    python cloud.py collect   采集一次，合并写入 data/YYYY-MM-DD.json
    python cloud.py report    聚合当天，生成 docs/index.html 与 docs/day-YYYYMMDD.html

日报每天生成一份，同一份 HTML 写两处：index.html 是最新一期，
day-YYYYMMDD.html 是历史快照。往期索引由 archive.py 统一维护。
与本机版的区别：云端容器每次运行后销毁，所以不用 SQLite，
改为把每天的去重话题存成纯文本 JSON，由 workflow 提交回仓库。
不需要 S3、API Key 或任何第三方包。
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import archive
import hotspot as h
import site_nav

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
DOCS = BASE / "docs"
BJ = timezone(timedelta(hours=8))
# 聚合窗口（天）。1 = 日报；改成 3 就是三日滚动榜。
DAYS = 1


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


def classify(title):
    """按关键词把话题归入主题分类；未命中归「其他」。

    分类名本身是固定词表，但页面只展示当天实际出现过的分类
    （动态显隐），所以用户看到的分类是随内容每天变化的。
    """
    t = title.lower()
    rules = (
        ("国际·外交", ("apec", "g20", "关税", "特朗普", "中美", "中美关系",
                       "普京", "乌克兰", "俄乌", "联合国", "外交", "访华", "巴以", "以色列",
                       "金砖", "上合", "欧盟", "北约", "制裁")),
        ("科技·AI", ("ai", "人工智能", "大模型", "gpt", "openai", "deepseek",
                     "芯片", "半导体", "算力", "机器人", "马斯克", "苹果", "华为",
                     "自动驾驶", "量子", "卫星", "航天", "火箭", "5g", "6g", "模型")),
        ("财经", ("股市", "股票", "a股", "港股", "美股", "基金", "央行", "降息",
                  "加息", "汇率", "人民币", "黄金", "楼市", "房价", "地产", "财报",
                  "上市公司", "ipo", "债券", "物价", "cpi", "通胀")),
        ("社会民生", ("教育", "医疗", "医保", "养老", "房价", "就业", "工资",
                      "食品安全", "交通", "事故", "灾害", "地震", "天气", "台风",
                      "疫情", "疫苗", "结婚", "离婚", "生育", "高考", "考研")),
        ("体育·娱乐", ("世界杯", "足球", "篮球", "nba", "cba", "奥运会", "亚运会",
                       "电竞", "电影", "电视剧", "综艺", "明星", "歌手", "演唱会",
                       "票房", "游戏", "动漫")),
    )
    for cat, kws in rules:
        for kw in kws:
            if kw in t:
                return cat
    return "其他"


def render(top, merged, runs, start, top_n):
    end = now()
    period = "%s 00:00 ~ 23:59" % start
    raw = sum(t["count"] for t in merged.values())
    platforms = set()
    for t in merged.values():
        for p in t["platforms"]:
            platforms.add(p)
    issue = "%s 周%s" % (end.strftime("%Y-%m-%d"), "一二三四五六日"[end.weekday()])
    # 给每个话题打上主题分类，供筛选使用
    for title, t in merged.items():
        t["cat"] = classify(title)

    parts = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>社会热点日报 · %s</title>' % h.esc(issue), h.STYLE,
        site_nav.head_css(), archive.head_css(),
        '</head><body>', site_nav.nav_html("radar"), '<div class="wrap">',
        '<header class="masthead">',
        '<div><p class="kicker">DAILY PUBLIC ATTENTION DIGEST</p>',
        '<h1>社会热点日报</h1></div>',
        '<div class="issue"><b>%s</b>统计周期 %s<br>每日更新 · 云端生成</div>' %
        (h.esc(issue), h.esc(period)),
        '</header>',
        '<div class="dateline">',
        '<span>数据源 <b>%d 个公开热榜平台</b></span>' % len(platforms),
        '<span>生成于 <b>%s</b></span>' % h.esc(end.strftime("%Y-%m-%d %H:%M")),
        '<span>今日收录 <b>%d</b> 个话题</span>' % len(merged),
        '</div>',
        '<div class="stats">',
    ]
    for label, val in (("采集批次", runs), ("原始条目", raw),
                       ("去重话题", len(merged)), ("覆盖平台", len(platforms))):
        parts.append('<div class="stat"><b>%s</b><span>%s</span></div>' % (val, label))
    parts.append('</div>')

    if top:
        # 当天实际出现过的分类（按规则定义顺序 + 其他兜底）
        cat_order = ("国际·外交", "科技·AI", "财经", "社会民生", "体育·娱乐")
        cats_present = [c for c in cat_order
                        if any(t["cat"] == c for _, t in merged.items())]
        if any(t["cat"] == "其他" for _, t in merged.items()):
            cats_present.append("其他")
        parts.append('<div class="focus-head">')
        parts.append('<h2>今日焦点<span class="en">Editor\'s pick</span></h2>')
        parts.append('<label class="filter"><span>筛选主题</span><select id="cat-filter">'
                     '<option value="all">全部话题</option>')
        for c in cats_present:
            parts.append('<option value="%s">%s</option>' % (h.esc(c), h.esc(c)))
        parts.append('</select></label>')
        parts.append('</div>')
        parts.append('<p class="lede">按出现次数、跨平台覆盖与最佳排名综合排序，取前三条。'
                     '可用右上角下拉按主题筛选，焦点与下方榜单联动。</p>')
        parts.append('<div class="focus">')
        for i, (title, t) in enumerate(top[:3], 1):
            parts.append('<article data-cat="%s"><div class="no">焦点 %02d</div>' %
                         (h.esc(t["cat"]), i))
            parts.append('<h3>%s</h3>' % h.make_link(title, t["url"]))
            parts.append('<div class="meta">%s · 出现 %d 次 · %d 个平台 · 最佳第 %d 名</div>' %
                         (h.esc(t["cat"]), t["count"], len(t["platforms"]), t["best"]))
            parts.append('</article>')
        parts.append('</div>')

    parts.append('<section class="rank-section">')
    parts.append('<h2 id="rank-head">热度榜单<span class="en">Ranking</span></h2>')
    rest = top[3:]
    if not top:
        parts.append('<div class="empty">暂无数据。采集 workflow 运行几次后这里就会有内容。</div>')
    elif not rest:
        parts.append('<div class="empty">今日话题较少，已全部列于上方焦点区。</div>')
    else:
        parts.append('<p class="lede">点击话题所在整行即可跳转原榜单；点击列表头可排序，'
                     '再点一次反转方向。向下滚动时标题与表头会吸附在页面顶部。</p>')
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
            url = t["url"] if t["url"] and t["url"].startswith(("http://", "https://")) else ""
            parts.append(
                "<tr data-cat='%s' data-href='%s'>"
                "<td class='rank' data-v='%d'>%d</td>"
                "<td class='topic' data-v='%s'>%s</td>"
                "<td class='num' data-v='%d'>%d</td>"
                "<td data-v='%d'>%s</td>"
                "<td class='num' data-v='%d'>%d</td>"
                "<td class='num hide' data-v='%s'>%s</td></tr>" %
                (h.esc(t["cat"]), h.esc(url),
                 i, i, h.esc(title), h.make_link(title, t["url"]),
                 t["count"], t["count"], len(t["platforms"]), tags,
                 t["best"], t["best"], h.esc(first), h.esc(first)))
        parts.append('</tbody></table>')
    parts.append('</section>')

    parts.append('<div class="foot"><span>社会热点日报 · 由 GitHub Actions 每日生成 · '
                 '数据源 newsnow 公共热榜接口</span>'
                 '<span>仅汇总公开标题与链接，不含任何账号或密钥 · %s</span></div>' %
                 h.esc(end.strftime("%Y")))
    parts.append(archive.widget("daily", start))
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

  // 整行点击跳转：点击榜单任意一行（含序号格）打开话题链接
  document.querySelectorAll('tr[data-href]').forEach(function(row){
    var href=row.getAttribute('data-href');
    if(!href){return;}
    row.style.cursor='pointer';
    row.setAttribute('tabindex','0');
    row.setAttribute('role','link');
    row.addEventListener('click',function(e){
      // 若点击落在行内真实链接上，交给链接自身处理，避免重复打开
      if(e.target && e.target.closest && e.target.closest('a')){return;}
      window.open(href,'_blank','noopener');
    });
    row.addEventListener('keydown',function(e){
      if(e.key==='Enter'){window.open(href,'_blank','noopener');}
    });
  });

  // 主题筛选：焦点卡片与热度榜单联动
  var sel=document.getElementById('cat-filter');
  if(sel){
    function applyFilter(){
      var v=sel.value;
      document.querySelectorAll('.focus article').forEach(function(card){
        var show=(v==='all')||(card.getAttribute('data-cat')===v);
        card.style.display=show?'':'none';
      });
      document.querySelectorAll('#ranking tbody tr').forEach(function(row){
        var show=(v==='all')||(row.getAttribute('data-cat')===v);
        row.style.display=show?'':'none';
      });
    }
    sel.addEventListener('change',applyFilter);
  }
})();
</script>""")
    parts.append('</body></html>')
    return "".join(parts)


def report():
    cfg = h.load_config()
    DATA.mkdir(parents=True, exist_ok=True)
    DOCS.mkdir(parents=True, exist_ok=True)
    start = (now() - timedelta(days=DAYS - 1)).strftime("%Y-%m-%d")
    merged, runs, _ = merge_days(start)
    ranked = sorted(merged.items(),
                    key=lambda kv: (-h.topic_score(kv[1]), -kv[1]["count"], kv[0]))
    top_n = max(1, min(200, int(cfg["top_n"])))
    stamp = now().strftime("%Y%m%d")
    day_file = "day-%s.html" % stamp
    # 把本期快照提前登记进索引，页面内嵌的往期列表才不会缺当期
    archive.refresh(extra=[("daily", day_file, stamp)])
    html = render(ranked[:top_n], merged, runs, start, top_n)
    (DOCS / "index.html").write_text(html, encoding="utf-8")
    (DOCS / day_file).write_text(html, encoding="utf-8")
    print("report: %s / %d topics / %d runs / day-%s.html %.1f KB" %
          (start, len(merged), runs, stamp, len(html.encode("utf-8")) / 1024.0))


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
