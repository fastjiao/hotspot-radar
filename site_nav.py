# -*- coding: utf-8 -*-
"""
站点统一导航（root-relative，域名无关）

设计约束：
  1. 链接一律用根相对路径，不写死任何域名。主域 www.ljiao.me 与
     fastjiao.github.io 下都能工作；自定义域名到期后一行都不用改。
  2. 样式全部加 .wb-nav 前缀，不引外部 CSS/JS，避免与根站 index_style.css
     以及两份周报里的全局标签选择器（body / header / a / li）串味。
  3. 两个生成器共用本文件，改导航只需改这一处。

生成器用法：
    import site_nav
    # <head> 里、主样式之后
    site_nav.head_css()
    # <body> 之后、内容容器之前
    site_nav.nav_html("radar")   # home | radar | digest

给已生成的 HTML 补导航（幂等，可重复运行）：
    python site_nav.py patch
"""
import re
import sys
from pathlib import Path

NAV_CSS = """
.wb-nav{display:flex;align-items:center;justify-content:space-between;gap:12px;
flex-wrap:wrap;padding:9px 20px;background:#fffdf8;border-bottom:1px solid #ddd6c8;
font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
position:relative;z-index:50}
.wb-nav a.wb-nav-brand{font-weight:500;color:#1b1a17;text-decoration:none;
letter-spacing:.08em;white-space:nowrap}
.wb-nav .wb-nav-links{display:flex;gap:4px;flex-wrap:wrap;align-items:center}
.wb-nav a.wb-nav-link{color:#5b564d;text-decoration:none;padding:5px 11px;
border:1px solid transparent;border-radius:4px;white-space:nowrap}
.wb-nav a.wb-nav-link:hover{color:#b23a24;background:#f5efe3;text-decoration:none}
.wb-nav a.wb-nav-link.is-active{color:#b23a24;border-color:#e0c9b8;background:#faf1e8}
@media(max-width:600px){.wb-nav{padding:8px 12px}
.wb-nav a.wb-nav-link{padding:4px 8px;font-size:12px}}
"""

_LINKS = (
    ("home", "/", "首页"),
    ("radar", "/hotspot-radar/", "社会热点周报"),
    ("digest", "/hotspot-radar/digest.html", "热点信息差"),
)


def head_css():
    """返回可放在 </head> 前的独立样式块。"""
    return '<style data-wb-nav>' + NAV_CSS + '</style>'


def nav_html(current):
    """返回导航条 HTML；current 为 home / radar / digest。"""
    parts = ['<nav class="wb-nav" aria-label="站点导航">',
             '<a class="wb-nav-brand" href="/">Lovejiao</a>',
             '<div class="wb-nav-links">']
    for key, href, label in _LINKS:
        cls = "wb-nav-link is-active" if key == current else "wb-nav-link"
        extra = ' aria-current="page"' if key == current else ""
        parts.append('<a class="%s" href="%s"%s>%s</a>'
                     % (cls, href, extra, label))
    parts.append('</div></nav>')
    return "".join(parts)


def patch_file(path, current):
    """给已有 HTML 注入导航，重复运行不会叠加。"""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    text = re.sub(r'<style data-wb-nav>.*?</style>\s*', '', text, flags=re.S)
    text = re.sub(r'<nav class="wb-nav".*?</nav>\s*', '', text, flags=re.S)
    text = text.replace('</head>', head_css() + '\n</head>', 1)
    m = re.search(r'(<body[^>]*>)', text)
    if not m:
        raise ValueError("no <body> tag: %s" % p)
    text = text[:m.end()] + nav_html(current) + text[m.end():]
    p.write_text(text, encoding="utf-8")
    return len(text)


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "patch":
        print(__doc__)
        return 0
    docs = Path(__file__).resolve().parent / "docs"
    targets = [(docs / "index.html", "radar"), (docs / "digest.html", "digest")]
    targets += [(p, "radar") for p in sorted(docs.glob("week-*.html"))]
    for path, current in targets:
        if path.exists():
            size = patch_file(path, current)
            print("patched %-24s %d chars" % (path.name, size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
