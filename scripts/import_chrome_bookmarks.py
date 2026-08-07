#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ipaddress
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import yaml

LOCAL_SUFFIXES = (".local", ".lan", ".home", ".internal", ".test")
TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "ref_src",
    "spm",
}


@dataclass(frozen=True)
class Bookmark:
    title: str
    url: str
    folders: tuple[str, ...]


class NetscapeBookmarkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.bookmarks: list[Bookmark] = []
        self.folder_stack: list[str] = []
        self.dl_pushes: list[bool] = []
        self.pending_folder: str | None = None
        self.capture_folder = False
        self.capture_link = False
        self.text_parts: list[str] = []
        self.current_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "h3":
            self.capture_folder = True
            self.text_parts = []
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.capture_link = True
                self.current_url = href
                self.text_parts = []
        elif tag == "dl":
            pushed = self.pending_folder is not None
            if pushed:
                self.folder_stack.append(self.pending_folder or "")
                self.pending_folder = None
            self.dl_pushes.append(pushed)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "h3" and self.capture_folder:
            self.pending_folder = clean_text("".join(self.text_parts))
            self.capture_folder = False
            self.text_parts = []
        elif tag == "a" and self.capture_link:
            title = clean_text("".join(self.text_parts))
            self.bookmarks.append(
                Bookmark(title=title, url=self.current_url.strip(), folders=tuple(self.folder_stack))
            )
            self.capture_link = False
            self.current_url = ""
            self.text_parts = []
        elif tag == "dl" and self.dl_pushes:
            if self.dl_pushes.pop() and self.folder_stack:
                self.folder_stack.pop()

    def handle_data(self, data: str) -> None:
        if self.capture_folder or self.capture_link:
            self.text_parts.append(data)


def clean_text(value: str) -> str:
    return " ".join(value.split())


def is_local_host(hostname: str) -> bool:
    hostname = hostname.strip("[]").lower()
    if hostname == "localhost" or hostname.endswith(LOCAL_SUFFIXES) or "." not in hostname:
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_unspecified
    )


def canonical_url(value: str) -> str | None:
    try:
        parts = urllib.parse.urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    if is_local_host(parts.hostname):
        return None

    scheme = parts.scheme.lower()
    hostname = parts.hostname.lower()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    query = []
    for key, item in urllib.parse.parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in TRACKING_KEYS:
            continue
        query.append((key, item))

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return urllib.parse.urlunsplit((scheme, netloc, path, urllib.parse.urlencode(query), ""))


def collect_existing_urls(path: Path) -> set[str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    urls: set[str] = set()
    for category in data:
        if not isinstance(category, dict):
            continue
        groups = [category.get("links") or []]
        groups.extend(group.get("links") or [] for group in category.get("list") or [])
        for links in groups:
            for item in links:
                normalized = canonical_url(item.get("url", ""))
                if normalized:
                    urls.add(normalized)
    return urls


def parse_bookmarks(path: Path) -> list[Bookmark]:
    parser = NetscapeBookmarkParser()
    parser.feed(path.read_text(encoding="utf-8", errors="replace"))
    return parser.bookmarks


def build_candidates(bookmarks: list[Bookmark], existing_urls: set[str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    candidates: list[dict[str, Any]] = []
    seen = set(existing_urls)
    skipped_local = 0
    skipped_duplicate = 0
    skipped_protocol = 0

    for bookmark in bookmarks:
        normalized = canonical_url(bookmark.url)
        if not normalized:
            parts = urllib.parse.urlsplit(bookmark.url)
            if parts.scheme.lower() in {"http", "https"}:
                skipped_local += 1
            else:
                skipped_protocol += 1
            continue
        if normalized in seen:
            skipped_duplicate += 1
            continue
        seen.add(normalized)
        hostname = urllib.parse.urlsplit(normalized).hostname or ""
        candidates.append(
            {
                "title": bookmark.title or hostname,
                "url": normalized,
                "folder": " / ".join(bookmark.folders) or "未分类",
                "logo": f"https://www.google.com/s2/favicons?sz=64&domain={hostname}",
            }
        )

    summary = {
        "bookmarks": len(bookmarks),
        "candidates": len(candidates),
        "skipped_duplicate": skipped_duplicate,
        "skipped_local": skipped_local,
        "skipped_protocol": skipped_protocol,
    }
    return candidates, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract public, unique URLs from a Chrome bookmark export.")
    parser.add_argument("bookmarks", type=Path, help="Chrome-exported bookmarks HTML file")
    parser.add_argument(
        "--webstack",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "data" / "webstack.yml",
        help="Existing WebStack YAML used for duplicate detection",
    )
    parser.add_argument("-o", "--output", type=Path, help="Write YAML output to this file instead of stdout")
    args = parser.parse_args()

    bookmarks = parse_bookmarks(args.bookmarks)
    candidates, summary = build_candidates(bookmarks, collect_existing_urls(args.webstack))
    payload = {"summary": summary, "links": candidates}
    rendered = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120)

    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {len(candidates)} candidates -> {args.output}")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
