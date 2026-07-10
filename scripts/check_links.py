#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures as futures
import re
import subprocess
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
DEFAULT_OFFLINE_RETRIES = 2


def is_parked_page(content: bytes) -> bool:
    lowered = content.lower()
    return (
        b"<title>loading...</title>" in lowered
        and b"window.location.replace" in lowered
    ) or any(
        marker in lowered
        for marker in (
            b"sarai-tid.com/zokredirect",
            b"domain is for sale",
            b"is for sale</h1>",
            "正在出售中".encode(),
        )
    )


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


def request_once(url: str, method: str, timeout: int) -> tuple[int, str, bytes]:
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
        content = b""
        if method != "HEAD":
            content = response.read(4096)
        return response.status, normalize_url(response.geturl()), content


def request_with_curl(url: str, timeout: int) -> tuple[int, str]:
    result = subprocess.run(
        [
            "curl",
            "--location",
            "--silent",
            "--show-error",
            "--output",
            "/dev/null",
            "--connect-timeout",
            str(min(timeout, 8)),
            "--max-time",
            str(timeout),
            "--user-agent",
            USER_AGENT,
            "--write-out",
            "%{http_code}\t%{url_effective}",
            quoted_url(url),
        ],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        detail = " ".join(result.stderr.split()) or f"exit code {result.returncode}"
        raise RuntimeError(detail)

    code_text, final_url = result.stdout.strip().split("\t", maxsplit=1)
    return int(code_text), normalize_url(final_url)


def check_url(url: str, timeout: int, inspect_content: bool = False) -> dict[str, Any]:
    started_at = time.perf_counter()
    last_error = ""
    for method in ("HEAD", "GET"):
        try:
            code, final_url, content = request_once(url, method, timeout)
            if method == "GET" and is_parked_page(content):
                return {
                    "status": "offline",
                    "method": method,
                    "final_url": final_url,
                    "error": "域名已停放、出售或跳转至无关页面",
                    "elapsed": round(time.perf_counter() - started_at, 2),
                }
            status = status_from_code(code)
            if method == "HEAD" and status == "online" and inspect_content:
                continue
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

    try:
        code, final_url = request_with_curl(url, timeout)
        return {
            "status": status_from_code(code),
            "code": code,
            "method": "CURL",
            "final_url": final_url,
            "elapsed": round(time.perf_counter() - started_at, 2),
        }
    except Exception as error:
        curl_error = f"{type(error).__name__}: {error}"

    return {
        "status": "offline",
        "error": f"{last_error}; {curl_error}",
        "elapsed": round(time.perf_counter() - started_at, 2),
    }


def status_from_code(code: int) -> str:
    if code in RESTRICTED_STATUS:
        return "restricted"
    if code <= ONLINE_MAX_STATUS:
        return "online"
    return "offline"


def status_from_error(error: str) -> str:
    permanent_markers = (
        "certificate_verify_failed",
        "hostname mismatch",
        "name or service not known",
        "nodename nor servname provided",
        "connection refused",
        "no route to host",
    )
    lowered = error.lower()
    return "offline" if any(marker in lowered for marker in permanent_markers) else "restricted"


def main() -> int:
    parser = argparse.ArgumentParser(description="Check WebStack links and write Hugo data/link_status.yml.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--offline-retries", type=int, default=DEFAULT_OFFLINE_RETRIES)
    args = parser.parse_args()

    root = args.root
    urls: dict[str, set[str]] = {}
    collect_from_webstack(root / "data/webstack.yml", urls)
    collect_from_friendlinks(root / "data/friendlinks.yml", urls)
    collect_from_headers(root / "data/headers.yml", urls)

    results: dict[str, dict[str, Any]] = {}
    with futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        checks = {
            executor.submit(
                check_url,
                url,
                args.timeout,
                any(source.startswith("webstack/无法访问/") for source in urls[url]),
            ): url
            for url in urls
        }
        for check in futures.as_completed(checks):
            url = checks[check]
            result = check.result()
            result["sources"] = sorted(urls[url])
            results[url] = result

    for _ in range(max(0, args.offline_retries)):
        offline_urls = [
            url
            for url, result in results.items()
            if result["status"] == "offline"
            and not result.get("error", "").startswith("域名已停放")
        ]
        if not offline_urls:
            break
        with futures.ThreadPoolExecutor(max_workers=min(args.workers, len(offline_urls))) as executor:
            retries = {
                executor.submit(
                    check_url,
                    url,
                    args.timeout,
                    any(source.startswith("webstack/无法访问/") for source in urls[url]),
                ): url
                for url in offline_urls
            }
            for retry in futures.as_completed(retries):
                url = retries[retry]
                result = retry.result()
                result["sources"] = sorted(urls[url])
                results[url] = result

    for result in results.values():
        if result["status"] == "offline" and "code" not in result:
            error = result.get("error", "")
            if not error.startswith("域名已停放"):
                result["status"] = status_from_error(error)

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
