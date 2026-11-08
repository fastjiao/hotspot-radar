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

    parts = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>一周社会热点周报</title>', h.STYLE,
        '</head><body><div class="wrap">',
        '<h1>一周社会热点周报</h1>',
        '<div class="sub">统计周期：%s ｜ 云端生成 ｜ 数据来自公开热榜</div>' % h.esc(period),
        '<div class="cards">',
    ]
    for label, val in (("采集批次", runs), ("原始条目", raw),
                       ("去重话题", len(merged)), ("覆盖平台", len(platforms))):
        parts.append('<div class="card"><b>%s</b><span>%s</span></div>' % (val, label))
    parts.append('</div>')

    parts.append('<h2>热度 Top %d</h2>' % min(top_n, len(top)))
    if not top:
        parts.append('<div class="empty">暂无数据。数据采集 workflow 运行几次后这里就会有内容。</div>')
    else:
        parts.append('<table><thead><tr><th>#</th><th>话题</th><th>出现次数</th>'
                     '<th>上榜平台</th><th>最佳排名</th><th class="hide">首次出现</th>'
                     '</tr></thead><tbody>')
        for i, (title, t) in enumerate(top, 1):
            tags = "".join('<span class="tag">%s</span>' % h.esc(p)
                           for p in sorted(t["platforms"]))
            parts.append(
                "<tr><td class='num'>%d</td><td>%s</td><td class='num'>%d</td>"
                "<td>%s</td><td class='num'>%d</td><td class='num hide'>%s</td></tr>" %
                (i, h.make_link(title, t["url"]), t["count"], tags, t["best"],
                 h.esc(t["first"][:16].replace("T", " "))))
        parts.append('</tbody></table>')

    if day_rows:
        parts.append('<h2>每日采集概览</h2><table><thead><tr><th>日期</th>'
                     '<th>采集次数</th><th>当日话题</th></tr></thead><tbody>')
        for d, r, c in day_rows:
            parts.append("<tr><td>%s</td><td class='num'>%d</td>"
                         "<td class='num'>%d</td></tr>" % (h.esc(d), r, c))
        parts.append('</tbody></table>')

    if plat_count:
        parts.append('<h2>平台覆盖</h2><table><thead><tr><th>平台</th>'
                     '<th>累计条目</th><th>占比</th></tr></thead><tbody>')
        total = sum(plat_count.values())
        for name, cnt in plat_count.most_common():
            parts.append("<tr><td>%s</td><td class='num'>%d</td>"
                         "<td class='num'>%.1f%%</td></tr>" %
                         (h.esc(name), cnt, 100.0 * cnt / total))
        parts.append('</tbody></table>')

    parts.append('<div class="foot">由 GitHub Actions 定时生成 · 数据源 newsnow 公共热榜接口 · '
                 '仅汇总公开新闻标题与链接，不含任何账号或密钥信息。</div>')
    parts.append('</div></body></html>')
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
