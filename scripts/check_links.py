#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures as futures
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)
RESTRICTED_STATUS = {401, 403, 429, 451}
ONLINE_MAX_STATUS = 399
DEFAULT_TIMEOUT = 12
DEFAULT_WORKERS = 24


def normalize_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip())
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


def quoted_url(url: str) -> str:
    parts = urllib.parse.urlsplit(normalize_url(url))
    path = urllib.parse.quote(urllib.parse.unquote(parts.path or "/"), safe="/%:@")
    query = urllib.parse.quote(urllib.parse.unquote(parts.query), safe="=&?/:+,%@")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def add_url(urls: dict[str, set[str]], url: Any, source: str) -> None:
    if not isinstance(url, str) or not re.match(r"^https?://", url, re.I):
        return
    urls.setdefault(url.strip(), set()).add(source)


def collect_from_webstack(path: Path, urls: dict[str, set[str]]) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    for category in data:
        taxonomy = category.get("taxonomy", "") if isinstance(category, dict) else ""
        if not isinstance(category, dict):
            continue
        for item in category.get("links") or []:
            title = item.get("title", "")
            add_url(urls, item.get("url"), f"webstack/{taxonomy}/{title}")
            add_url(urls, item.get("usrl"), f"webstack/{taxonomy}/{title}")
        for group in category.get("list") or []:
            term = group.get("term", "") if isinstance(group, dict) else ""
            for item in group.get("links") or []:
                title = item.get("title", "")
                add_url(urls, item.get("url"), f"webstack/{taxonomy}/{term}/{title}")
                add_url(urls, item.get("usrl"), f"webstack/{taxonomy}/{term}/{title}")


def collect_from_friendlinks(path: Path, urls: dict[str, set[str]]) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    for item in data:
        add_url(urls, item.get("url"), f"friendlinks/{item.get('title', '')}")


def collect_from_headers(path: Path, urls: dict[str, set[str]]) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []

    def walk(value: Any, title: str = "") -> None:
        if isinstance(value, dict):
            current_title = value.get("title") or value.get("name") or title
            add_url(urls, value.get("link"), f"headers/{current_title}")
            for child in value.values():
                walk(child, current_title)
        elif isinstance(value, list):
            for child in value:
                walk(child, title)

    walk(data)


def request_once(url: str, method: str, timeout: int) -> tuple[int, str]:
    request = urllib.request.Request(
        quoted_url(url),
        method=method,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Connection": "close",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if method != "HEAD":
            response.read(512)
        return response.status, normalize_url(response.geturl())


def check_url(url: str, timeout: int) -> dict[str, Any]:
    started_at = time.perf_counter()
    last_error = ""
    for method in ("HEAD", "GET"):
        try:
            code, final_url = request_once(url, method, timeout)
            status = status_from_code(code)
            return {
                "status": status,
                "code": code,
                "method": method,
                "final_url": final_url,
                "elapsed": round(time.perf_counter() - started_at, 2),
            }
        except urllib.error.HTTPError as error:
            if method == "HEAD":
                last_error = f"HTTP {error.code}"
                continue
            code = error.code
            status = status_from_code(code)
            return {
                "status": status,
                "code": code,
                "method": method,
                "final_url": normalize_url(getattr(error, "url", url)),
                "elapsed": round(time.perf_counter() - started_at, 2),
            }
        except Exception as error:
            last_error = f"{type(error).__name__}: {error}"
            if method == "HEAD":
                continue
    return {
        "status": "offline",
        "error": last_error,
        "elapsed": round(time.perf_counter() - started_at, 2),
    }


def status_from_code(code: int) -> str:
    if code in RESTRICTED_STATUS:
        return "restricted"
    if code <= ONLINE_MAX_STATUS:
        return "online"
    return "offline"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check WebStack links and write Hugo data/link_status.yml.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    args = parser.parse_args()

    root = args.root
    urls: dict[str, set[str]] = {}
    collect_from_webstack(root / "data/webstack.yml", urls)
    collect_from_friendlinks(root / "data/friendlinks.yml", urls)
    collect_from_headers(root / "data/headers.yml", urls)

    results: dict[str, dict[str, Any]] = {}
    with futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        checks = {executor.submit(check_url, url, args.timeout): url for url in urls}
        for check in futures.as_completed(checks):
            url = checks[check]
            result = check.result()
            result["sources"] = sorted(urls[url])
            results[url] = result

    output = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "summary": {
            "total": len(results),
            "online": sum(1 for item in results.values() if item["status"] == "online"),
            "restricted": sum(1 for item in results.values() if item["status"] == "restricted"),
            "offline": sum(1 for item in results.values() if item["status"] == "offline"),
        },
        "links": dict(sorted(results.items())),
    }
    output_path = root / "data/link_status.yml"
    output_path.write_text(yaml.safe_dump(output, allow_unicode=True, sort_keys=False), encoding="utf-8")

    print(
        "checked {total} links: {online} online, {restricted} restricted, {offline} offline -> {path}".format(
            path=output_path,
            **output["summary"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
