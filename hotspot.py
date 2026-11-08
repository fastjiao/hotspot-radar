# -*- coding: utf-8 -*-
"""
每周社会热点雷达 - 零第三方依赖版

只使用 Python 标准库（urllib / sqlite3 / html），无需 pip 安装任何东西。
用法：
    python hotspot.py collect          抓取一次热榜并写入本地数据库
    python hotspot.py report           生成最近 7 天的 HTML 周报
    python hotspot.py report --days 3  生成最近 3 天的报告
    python hotspot.py status           查看数据量与磁盘占用

数据、日志、报告全部保存在本脚本所在目录，不会写入 C 盘。
兼容 Python 3.8+。
"""
import argparse
import html
import json
import shutil
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
REPORT_DIR = BASE / "reports"
DB_PATH = DATA_DIR / "hotspot.db"
LOG_PATH = DATA_DIR / "run.log"
CONFIG_PATH = BASE / "config.json"

# 数据源：TrendRadar 默认的 newsnow 公共热榜接口
API_BASE = "https://newsnow.busiyi.world/api/s"
RETENTION_DAYS = 120          # 自动清理 120 天前的数据，控制磁盘占用
REQUEST_INTERVAL = 0.6        # 每个平台之间的间隔（秒），礼貌抓取
REQUEST_TIMEOUT = 15
TOP_N = 60                    # 周报展示的热度前 N 个话题
DAILY_NEW_N = 8               # 每天展示的新上榜话题数

PLATFORMS = [
    ("toutiao", "今日头条", "toutiao.com"),
    ("baidu", "百度热搜", "baidu.com"),
    ("wallstreetcn-hot", "华尔街见闻", "wallstreetcn.com"),
    ("thepaper", "澎湃新闻", "thepaper.cn"),
    ("bilibili-hot-search", "B站热搜", "bilibili.com"),
    ("cls-hot", "财联社", "cls.cn"),
    ("ifeng", "凤凰网", "ifeng.com"),
    ("tieba", "贴吧", "baidu.com"),
    ("weibo", "微博", "weibo.com"),
    ("douyin", "抖音", "douyin.com"),
    ("zhihu", "知乎", "zhihu.com"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
}


def default_config():
    return {
        "collect_times": ["09:00", "13:00", "20:00"],
        "report_time": "20:30",
        "top_n": TOP_N,
        "retention_days": RETENTION_DAYS,
        "request_interval": REQUEST_INTERVAL,
        "platforms": [{"id": p[0], "name": p[1], "domain": p[2], "enabled": True}
                      for p in PLATFORMS],
    }


def load_config():
    """配置来自 config.json（控制台可编辑）；缺失或损坏时回退到内置默认值。"""
    cfg = default_config()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return cfg
        for k in ("collect_times", "report_time", "top_n",
                  "retention_days", "request_interval"):
            if k in raw:
                cfg[k] = raw[k]
        if isinstance(raw.get("platforms"), list) and raw["platforms"]:
            cfg["platforms"] = raw["platforms"]
    except Exception:
        pass
    return cfg


SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    day TEXT NOT NULL,
    platform TEXT NOT NULL,
    rank INTEGER NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);
CREATE INDEX IF NOT EXISTS idx_snapshots_title ON snapshots(title);
CREATE TABLE IF NOT EXISTS runs (
    ts TEXT PRIMARY KEY,
    items INTEGER NOT NULL,
    ok_platforms INTEGER NOT NULL,
    failed_platforms INTEGER NOT NULL
);
"""

STYLE = """
<style>
:root{--bg:#f7f8fa;--card:#ffffff;--txt:#1f2329;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb}
*{box-sizing:border-box}
body{margin:0;padding:32px 16px;background:var(--bg);color:var(--txt);
 font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1080px;margin:0 auto}
h1{font-size:24px;font-weight:600;margin:0 0 6px}
h2{font-size:17px;font-weight:600;margin:28px 0 12px}
.sub{color:var(--muted);font-size:13px;margin-bottom:22px}
.cards{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.card{flex:1;min-width:140px;background:var(--card);border:1px solid var(--line);
 border-radius:10px;padding:14px 16px}
.card b{display:block;font-size:24px;font-weight:600;color:var(--accent)}
.card span{color:var(--muted);font-size:12px}
table{width:100%;border-collapse:collapse;background:var(--card);
 border:1px solid var(--line);border-radius:10px;overflow:hidden;font-size:13px}
th,td{padding:9px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#f0f2f5;color:var(--muted);font-weight:500;white-space:nowrap}
tr:last-child td{border-bottom:none}
tr:nth-child(even) td{background:#fafbfc}
td.num{color:var(--muted);white-space:nowrap}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.tag{display:inline-block;background:#eef2ff;color:#4338ca;border-radius:4px;
 padding:1px 6px;margin:1px 3px 1px 0;font-size:11px;white-space:nowrap}
.day{color:var(--muted);font-size:13px;margin:18px 0 8px}
.empty{background:var(--card);border:1px solid var(--line);border-radius:10px;
 padding:24px;color:var(--muted)}
.foot{margin-top:30px;color:var(--muted);font-size:12px;
 border-top:1px solid var(--line);padding-top:14px}
@media(max-width:640px){.hide{display:none}body{padding:16px 8px}}
</style>
"""


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 512 * 1024:
            lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-1500:]
            LOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_PATH))
    con.executescript(SCHEMA)
    con.commit()
    return con


def safe_url(url, domain):
    """只信任期望域名及其子域的链接，防止第三方接口返回被篡改的地址。"""
    if not url or not domain:
        return False
    try:
        p = urllib.parse.urlparse(url)
        if p.scheme not in ("http", "https"):
            return False
        host = (p.hostname or "").lower()
        return host == domain or host.endswith("." + domain)
    except Exception:
        return False


def fetch(platform_id, domain):
    url = "%s?id=%s&latest" % (API_BASE, urllib.parse.quote(platform_id))
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        raw = resp.read(2 * 1024 * 1024)
    data = json.loads(raw.decode("utf-8", "replace"))
    if data.get("status") not in ("success", "cache"):
        raise ValueError("status=%s" % data.get("status"))
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("items 字段不是列表")
    out = []
    for idx, it in enumerate(items[:50], 1):
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or it.get("id") or "").strip()
        if not title:
            continue
        link = str(it.get("url") or it.get("mobileUrl") or "").strip()
        if not safe_url(link, domain):
            link = ""
        out.append((idx, title[:300], link[:1000]))
    return out


def collect():
    cfg = load_config()
    con = connect()
    now = datetime.now().astimezone()
    ts = now.isoformat(timespec="seconds")
    day = now.strftime("%Y-%m-%d")
    rows = []
    ok = 0
    failed = []
    active = [p for p in cfg["platforms"] if p.get("enabled", True)]
    log("开始采集 %s（启用 %d 个平台）" % (ts, len(active)))
    for p in active:
        pid, name, domain = p["id"], p["name"], p["domain"]
        try:
            items = fetch(pid, domain)
            rows.extend((ts, day, name, rank, title, url)
                        for rank, title, url in items)
            ok += 1
            log("  %s: %d 条" % (name, len(items)))
        except Exception as e:
            failed.append(name)
            log("  %s: 失败 %s" % (name, str(e)[:120]))
        time.sleep(float(cfg["request_interval"]))

    if rows:
        con.executemany(
            "INSERT INTO snapshots(ts,day,platform,rank,title,url) VALUES(?,?,?,?,?,?)",
            rows,
        )
    con.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?)",
                (ts, len(rows), ok, len(failed)))
    con.commit()

    cutoff = (now - timedelta(days=int(cfg["retention_days"]))).strftime("%Y-%m-%d")
    cur = con.execute("DELETE FROM snapshots WHERE day < ?", (cutoff,))
    con.execute("DELETE FROM runs WHERE ts < ?", (cutoff,))
    con.commit()
    log("采集完成: %d 条 / 成功 %d 平台 / 失败 %d / 清理旧数据 %d 行" %
        (len(rows), ok, len(failed), cur.rowcount))
    con.close()
    return 0 if ok else 1


def esc(s):
    return html.escape(str(s), quote=True)


def make_link(title, url):
    label = esc(title)
    if url and url.startswith(("http://", "https://")):
        return '<a href="%s" target="_blank" rel="noopener noreferrer">%s</a>' % (esc(url), label)
    return label


def topic_score(t):
    return t["count"] + 3 * (len(t["platforms"]) - 1) + max(0, 11 - t["best"]) * 0.3


def build_report(days=7):
    con = connect()
    now = datetime.now().astimezone()
    start = now - timedelta(days=days)
    since = start.isoformat(timespec="seconds")
    rows = con.execute(
        "SELECT ts,day,platform,rank,title,url FROM snapshots WHERE ts>=? ORDER BY ts,id",
        (since,),
    ).fetchall()
    runs = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(items),0) FROM runs WHERE ts>=?", (since,)
    ).fetchone()
    con.close()

    topics = {}
    for ts, day, platform, rank, title, url in rows:
        t = topics.get(title)
        if t is None:
            t = {"count": 0, "platforms": set(), "best": rank,
                 "first": ts, "last": ts, "url": url, "first_day": day}
            topics[title] = t
        t["count"] += 1
        t["platforms"].add(platform)
        if rank < t["best"]:
            t["best"] = rank
        if ts < t["first"]:
            t["first"] = ts
            t["first_day"] = day
        if ts > t["last"]:
            t["last"] = ts
        if url and not t["url"]:
            t["url"] = url

    ranked = sorted(topics.items(),
                    key=lambda kv: (-topic_score(kv[1]), -kv[1]["count"], kv[0]))
    top_n = max(1, min(200, int(load_config()["top_n"])))
    top = ranked[:top_n]
    plat_counter = Counter(r[2] for r in rows)
    period = "%s ~ %s" % (start.strftime("%Y-%m-%d %H:%M"),
                          now.strftime("%Y-%m-%d %H:%M"))

    parts = [
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>一周社会热点周报</title>', STYLE,
        '</head><body><div class="wrap">',
        '<h1>一周社会热点周报</h1>',
        '<div class="sub">统计周期：%s ｜ 覆盖 %d 个平台 ｜ 生成本地文件，不上传</div>' %
        (esc(period), len(PLATFORMS)),
    ]

    stats = [("采集次数", runs[0]), ("原始条目", len(rows)),
             ("去重话题", len(topics)), ("覆盖平台", len(plat_counter))]
    parts.append('<div class="cards">' + "".join(
        '<div class="card"><b>%s</b><span>%s</span></div>' % (v, k)
        for k, v in stats) + '</div>')

    parts.append('<h2>热度 Top %d</h2>' % min(top_n, len(top)))
    if not top:
        parts.append('<div class="empty">暂无数据。请先运行一次 '
                     '<code>python hotspot.py collect</code>，或等定时任务积累几天数据。</div>')
    else:
        parts.append('<table><thead><tr><th>#</th><th>话题</th><th>出现次数</th>'
                     '<th>上榜平台</th><th>最佳排名</th><th class="hide">首次出现</th>'
                     '</tr></thead><tbody>')
        for i, (title, t) in enumerate(top, 1):
            tags = "".join('<span class="tag">%s</span>' % esc(p)
                           for p in sorted(t["platforms"]))
            parts.append(
                "<tr><td class='num'>%d</td><td>%s</td><td class='num'>%d</td>"
                "<td>%s</td><td class='num'>%d</td><td class='num hide'>%s</td></tr>" %
                (i, make_link(title, t["url"]), t["count"], tags, t["best"],
                 esc(t["first"][:16].replace("T", " "))))
        parts.append('</tbody></table>')

    by_day = defaultdict(list)
    for title, t in topics.items():
        by_day[t["first_day"]].append((title, t))
    if by_day:
        parts.append('<h2>每日新上榜话题</h2>')
        for day in sorted(by_day.keys(), reverse=True):
            parts.append('<div class="day">%s</div><table><tbody>' % esc(day))
            daily = sorted(by_day[day], key=lambda kv: -topic_score(kv[1]))[:DAILY_NEW_N]
            for title, t in daily:
                parts.append(
                    "<tr><td>%s</td><td class='num'>%d 次 / %d 个平台</td></tr>" %
                    (make_link(title, t["url"]), t["count"], len(t["platforms"])))
            parts.append('</tbody></table>')

    if plat_counter:
        parts.append('<h2>平台覆盖</h2><table><thead><tr><th>平台</th>'
                     '<th>采集条目</th><th>占比</th></tr></thead><tbody>')
        total = sum(plat_counter.values())
        for name, cnt in plat_counter.most_common():
            parts.append("<tr><td>%s</td><td class='num'>%d</td>"
                         "<td class='num'>%.1f%%</td></tr>" %
                         (esc(name), cnt, 100.0 * cnt / total))
        parts.append('</tbody></table>')

    parts.append('<div class="foot">数据来源：newsnow 公共热榜接口（与 TrendRadar 同源）'
                 '｜ 全部数据仅保存在本机 D:\\hotspot-radar，不会上传或外发'
                 '｜ 链接已做域名校验，不合规的链接会被隐藏。</div>')
    parts.append('</div></body></html>')

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORT_DIR / ("week-%s.html" % now.strftime("%Y%m%d"))
    out.write_text("".join(parts), encoding="utf-8")
    shutil.copyfile(str(out), str(REPORT_DIR / "latest.html"))
    log("周报已生成: %s（%d 个话题 / %d 条原始记录）" % (out.name, len(topics), len(rows)))
    print("报告路径: %s" % out)
    return out


def status():
    if not DB_PATH.exists():
        print("数据库尚未创建，请先运行: python hotspot.py collect")
        return 0
    con = connect()
    n = con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    rng = con.execute("SELECT MIN(ts),MAX(ts) FROM snapshots").fetchone()
    runs = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    con.close()
    print("数据库: %s (%.2f MB)" % (DB_PATH, DB_PATH.stat().st_size / 1048576.0))
    print("快照记录: %d 条 / 采集批次: %d" % (n, runs))
    print("时间范围: %s ~ %s" % (rng[0] or "-", rng[1] or "-"))
    reports = sorted(REPORT_DIR.glob("week-*.html")) if REPORT_DIR.exists() else []
    print("周报文件: %d 份" % len(reports))
    for p in reports[-5:]:
        print("   %s  %.0f KB" % (p.name, p.stat().st_size / 1024.0))
    total = sum(f.stat().st_size for f in BASE.rglob("*") if f.is_file())
    print("程序 + 数据总占用: %.2f MB" % (total / 1048576.0))
    return 0


def main():
    ap = argparse.ArgumentParser(description="每周社会热点雷达（零第三方依赖）")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("collect", help="抓取一次热榜并入库")
    pr = sub.add_parser("report", help="生成周报")
    pr.add_argument("--days", type=int, default=7, help="统计最近 N 天（默认 7）")
    sub.add_parser("status", help="查看数据量与磁盘占用")
    a = ap.parse_args()
    if a.cmd == "collect":
        return collect()
    if a.cmd == "report":
        build_report(a.days)
        return 0
    if a.cmd == "status":
        return status()
    ap.print_help()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("运行异常: %s: %s" % (type(e).__name__, e))
        sys.exit(1)
