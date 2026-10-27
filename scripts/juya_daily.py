#!/usr/bin/env python3
# Display name: Juya AI Daily
# 显示名称：橘鸦AI早报
# Description: Read one issue of the Juya AI daily report (Chinese AI news digest). Use it for what happened in AI on a given day or what is new today; the newest issue's overview by default.
# 描述：读橘鸦AI早报：某一天 AI 圈发生了什么，默认给最新一期的概览，可以按小节取。
# Timeout: 60
# Clicall: yes
# Parameters:
# {"type": "object", "properties": {
#   "date": {"type": "string", "description": "YYYY-MM-DD, or today / yesterday (Beijing time). Default: the newest issue."},
#   "section": {"type": "string", "description": "A section heading as listed in the output, such as 模型发布 or 开发生态, or all for the whole issue. Default 概览 (overview)."},
#   "max_chars": {"type": "integer", "description": "Cut the text to this many characters, 200-20000. Default 6000."},
#   "format": {"type": "string", "enum": ["md", "json"], "description": "md (default) for reading; json for code, e.g. piping miyu call into jq or python."}
# }}
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

SITE = "https://daily.juya.uk"
ARCHIVE_URL = SITE + "/archive/"
MARKDOWN_URL = SITE + "/markdown/{date}.md"
ISSUE_URL = SITE + "/issues/{date}/"

USER_AGENT = "miyu-scripts-juya-daily/0.1"
HTTP_TIMEOUT = 15
ARCHIVE_TTL = 30 * 60
FRESH_ISSUE_TTL = 6 * 3600  # 最近两天的期可能还在改，旧的期一直留着

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ARCHIVE_DATE_RE = re.compile(r"/issues/(\d{4}-\d{2}-\d{2})/")
SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

DEFAULT_SECTION = "概览"
DEFAULT_MAX_CHARS = 6000
ALL_NAMES = ("all", "全部", "全文", "*")
BEIJING = datetime.timezone(datetime.timedelta(hours=8))


class Failed(Exception):
    pass


def cache_path(name):
    root = os.environ.get("MIYU_SCRIPT_CACHE_DIR")
    return os.path.join(root, name) if root else None


def cache_read(name, ttl):
    path = cache_path(name)
    if not path:
        return None
    try:
        if ttl is None or time.time() - os.path.getmtime(path) < ttl:
            with open(path, encoding="utf-8") as f:
                return f.read()
    except OSError:
        pass
    return None


def cache_write(name, text):
    path = cache_path(name)
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def http_text(url, allow_404=False):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and allow_404:
            return None
        raise Failed(f"{url} 返回 HTTP {exc.code}，稍后再试")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise Failed(f"连不上 {SITE}：{getattr(exc, 'reason', exc)}，检查网络后再试")


def issue_dates():
    html = cache_read("archive.html", ARCHIVE_TTL)
    if html is None:
        html = http_text(ARCHIVE_URL)
        cache_write("archive.html", html)
    dates = sorted(set(ARCHIVE_DATE_RE.findall(html)))
    if not dates:
        raise Failed(f"期号索引里一个日期都没解析到，站点可能改版了：{ARCHIVE_URL}")
    return dates


def issue_markdown(date):
    name = f"issues/{date}.md"
    recent = datetime.date.fromisoformat(date) >= datetime.datetime.now(BEIJING).date() - datetime.timedelta(days=1)
    text = cache_read(name, FRESH_ISSUE_TTL if recent else None)
    if text is not None:
        return text
    text = http_text(MARKDOWN_URL.format(date=date), allow_404=True)
    if text is not None:
        cache_write(name, text)
    return text


def split_sections(md):
    parts = SECTION_RE.split(md)
    return [(parts[i].strip(), parts[i + 1] if i + 1 < len(parts) else "") for i in range(1, len(parts), 2)]


def pick_section(sections, want):
    names = [name for name, _ in sections]
    if want in ALL_NAMES:
        return "全文", "\n".join(f"## {name}\n{body}" for name, body in sections)
    for name, body in sections:
        if name == want:
            return name, body
    hits = [(name, body) for name, body in sections if want in name or name in want]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise Failed(f"这一期没有「{want}」这一节；可选：{' / '.join(names)}，或 all")
    raise Failed(f"「{want}」对上了好几节：{' / '.join(n for n, _ in hits)}；写完整的小节名")


def tidy(text):
    text = IMAGE_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n+(?:-{3,}|\*{3,}|_{3,})[ \t]*(?=\n## |\Z)", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def resolve_date(raw):
    text = (raw or "").strip().lower().replace("/", "-")
    today = datetime.datetime.now(BEIJING).date()
    if text in ("today", "今天"):
        return today.isoformat()
    if text in ("yesterday", "昨天"):
        return (today - datetime.timedelta(days=1)).isoformat()
    if not DATE_RE.match(text):
        raise Failed(f"日期「{raw}」看不懂：写 YYYY-MM-DD（例：2026-10-01），或 today / yesterday")
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        raise Failed(f"没有 {text} 这一天")
    return text


def run(args):
    if not isinstance(args, dict):
        raise Failed("参数要是一个 JSON 对象")
    fmt = args.get("format") or "md"
    if fmt not in ("md", "json"):
        raise Failed("format 只能是 md 或 json")
    max_chars = args.get("max_chars", DEFAULT_MAX_CHARS)
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 200 <= max_chars <= 20000:
        raise Failed("max_chars 要是 200 到 20000 的整数")
    want = str(args.get("section") or "").strip()

    if args.get("date"):
        date = resolve_date(str(args["date"]))
    else:
        date = issue_dates()[-1]
    md = issue_markdown(date)
    if md is None:
        dates = issue_dates()
        raise Failed(f"{date} 没有早报。能查的是 {dates[0]} 到 {dates[-1]}；"
                     f"当天的一般北京时间 9 点到 11 点之间出")

    sections = split_sections(md)
    if not sections:
        raise Failed(f"{date} 的正文里没解析出小节，站点可能改版了：{ISSUE_URL.format(date=date)}")
    if want:
        name, body = pick_section(sections, want)
    else:
        try:
            name, body = pick_section(sections, DEFAULT_SECTION)
        except Failed:
            name, body = pick_section(sections, "all")

    body = tidy(body)
    total = len(body)
    text = body
    if total > max_chars:
        text = body[:max_chars]
        cut = text.rfind("\n")
        if cut > max_chars * 0.6:
            text = text[:cut]
        text = text.rstrip()
    payload = {
        "date": date, "section": name, "sections": [n for n, _ in sections],
        "url": ISSUE_URL.format(date=date), "text": text,
        "chars": len(text), "total_chars": total, "truncated": len(text) < total,
    }
    if fmt == "json":
        return json.dumps(payload, ensure_ascii=False)
    out = [f"# 橘鸦AI早报 {date} · {name}", "", text, "", "---"]
    if payload["truncated"]:
        out.append(f"已截断：这一节共 {total} 字，只给了前 {len(text)} 字；调大 max_chars 或按小节取")
    out.append("本期小节：" + " / ".join(payload["sections"]))
    out.append(f"原文：{payload['url']}")
    return "\n".join(out)


def main():
    try:
        print(run(json.load(sys.stdin)))
    except json.JSONDecodeError as exc:
        print(f"错误：参数不是 JSON：{exc}")
        sys.exit(1)
    except Failed as exc:
        print(f"错误：{exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
