#!/usr/bin/env python3
"""Fetch free global markets RSS feeds, filter, dedupe, print JSON to stdout.

Standard library only — no API key, no third-party packages, no file writes.
Stderr carries warnings; stdout is JSON only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = SKILL_DIR / "config.json"
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")

def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def strip_html(text: str) -> str:
    text = unescape(TAG_RE.sub(" ", text or ""))
    return WS_RE.sub(" ", text).strip()


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def child_text(el: ET.Element, names: tuple[str, ...]) -> str:
    for child in list(el):
        if local_name(child.tag) in names:
            return (child.text or "").strip()
    return ""


def child_link(el: ET.Element) -> str:
    # RSS <link>text</link>
    for child in list(el):
        if local_name(child.tag) == "link":
            href = child.attrib.get("href")
            if href:
                return href.strip()
            if (child.text or "").strip():
                return child.text.strip()
    # Atom <link rel="alternate" href="..."/>
    for child in list(el):
        if local_name(child.tag) == "link":
            rel = (child.attrib.get("rel") or "alternate").lower()
            href = child.attrib.get("href")
            if href and rel in {"alternate", ""}:
                return href.strip()
    for child in list(el):
        if local_name(child.tag) == "link" and child.attrib.get("href"):
            return child.attrib["href"].strip()
    return ""


def normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""
    query = parse_qs(parsed.query)
    keep = {
        k: v[0]
        for k, v in query.items()
        if k.lower()
        not in {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
        }
        and len(v) == 1
    }
    query_str = "&".join(f"{k}={keep[k]}" for k in sorted(keep))
    return urlunparse(
        (parsed.scheme.lower(), host, path.rstrip("/"), "", query_str, "")
    )


def normalize_title(title: str) -> str:
    t = strip_html(title).lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return WS_RE.sub(" ", t).strip()


def titles_similar(a: str, b: str) -> bool:
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    sa, sb = set(na.split()), set(nb.split())
    if len(sa) < 4 or len(sb) < 4:
        return False
    overlap = len(sa & sb) / max(len(sa), len(sb))
    return overlap >= 0.85


def parse_published_raw(raw: str) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    # ISO-8601 variants (Atom)
    cleaned = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


TICKER_PAGE_RE = re.compile(
    r"^[A-Z0-9][A-Z0-9.-]{0,12}\s*[-–—]\s*(?:\||reuters|stock price)",
    re.IGNORECASE,
)
# Single-name equity cues: (AAPL), TSX:ARG, NASDAQ:MSFT, NYSE:X
TICKER_MENTION_RE = re.compile(
    r"\(([A-Z]{1,5})\)"
    r"|\b(?:NASDAQ|NYSE|TSX|LSE|ASX|TSE):\s*[A-Z]{1,5}\b"
    r"|\b[A-Z]{1,5}:[A-Z]{1,5}\b"
)
STAKE_BUY_RE = re.compile(
    r"\b(buys?|bought|raises?|raising|adds?(?:\s+to)?|added)\b"
    r".{0,48}\b(shares|stake|holding)\b",
    re.IGNORECASE,
)
STOCK_PICK_RE = re.compile(
    r"\b(stocks?|shares)\b.{0,40}\b(to buy|to sell|now expensive|under.?valued)\b",
    re.IGNORECASE,
)


def is_noise_title(title: str) -> bool:
    t = title.strip()
    if not t:
        return True
    if TICKER_PAGE_RE.match(t):
        return True
    low = t.lower()
    if "stock price & latest news" in low:
        return True
    if "stock price, news, quote" in low:
        return True
    if "quote & history" in low:
        return True
    if "cryptocurrency prices" in low or "real time market data" in low:
        return True
    # Bare ticker stubs like "0HAC.L - Reuters"
    if re.match(r"^[A-Z0-9][A-Z0-9.-]{1,12}\s*[-–—]\s*reuters\s*$", t, re.I):
        return True
    return False


def is_single_stock_item(
    title: str, summary: str, keywords: list[str]
) -> bool:
    """Drop single-name equity hotspots; keep broad market / macro stories."""
    blob = f"{title} {summary}".lower()
    if any(k.lower() in blob for k in keywords):
        return True
    if TICKER_MENTION_RE.search(title):
        return True
    if STAKE_BUY_RE.search(blob) or STOCK_PICK_RE.search(blob):
        return True
    return False


def should_exclude(
    blob: str,
    hard_exclude: list[str],
    soft_exclude: list[str],
    keep: list[str],
) -> bool:
    if any(k.lower() in blob for k in hard_exclude):
        return True
    if any(k.lower() in blob for k in soft_exclude):
        if any(k.lower() in blob for k in keep):
            return False
        return True
    return False


BATCH_EXECUTE_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)


def is_google_news_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "news.google.com" or host.endswith(".news.google.com")


def _open_bytes(req: urllib.request.Request, timeout: int = 20) -> tuple[bytes, str]:
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with direct.open(req, timeout=timeout) as resp:
            return resp.read(), resp.geturl()
    except urllib.error.URLError:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.geturl()


def resolve_google_news_url(article_url: str) -> str | None:
    """Turn a news.google.com article wrapper into the publisher URL."""
    if not is_google_news_url(article_url):
        return article_url
    article_id = article_url.rstrip("/").split("/")[-1].split("?")[0]
    if not article_id:
        return None
    page_req = urllib.request.Request(
        article_url,
        headers={"User-Agent": BROWSER_UA, "Accept": "text/html"},
    )
    try:
        raw, final_url = _open_bytes(page_req)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None
    if final_url and not is_google_news_url(final_url):
        return final_url
    page = raw.decode("utf-8", errors="replace")
    sig_match = re.search(r'data-n-a-sg="([^"]+)"', page)
    ts_match = re.search(r'data-n-a-ts="([^"]+)"', page)
    if not sig_match or not ts_match:
        return None
    rpc_inner = json.dumps(
        [
            "garturlreq",
            [
                [
                    "X",
                    "X",
                    ["X", "X"],
                    None,
                    None,
                    1,
                    1,
                    "US:en",
                    None,
                    1,
                    None,
                    None,
                    None,
                    None,
                    None,
                    0,
                    1,
                ],
                "X",
                "X",
                1,
                [1, 1, 1],
                1,
                1,
                None,
                0,
                0,
                None,
                0,
            ],
            article_id,
            int(ts_match.group(1)),
            sig_match.group(1),
        ],
        separators=(",", ":"),
    )
    f_req = json.dumps(
        [[["Fbv4je", rpc_inner, None, "generic"]]],
        separators=(",", ":"),
    )
    post_req = urllib.request.Request(
        BATCH_EXECUTE_URL,
        data=urlencode({"f.req": f_req}).encode("ascii"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "Referer": "https://news.google.com/",
            "User-Agent": BROWSER_UA,
        },
        method="POST",
    )
    try:
        body_bytes, _ = _open_bytes(post_req)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None
    body = body_bytes.decode("utf-8", errors="replace")
    if body.startswith(")]}'"):
        body = body.split("\n", 1)[1].lstrip()
    head, _, tail = body.partition("\n")
    if head.strip().isdigit():
        body = tail
    try:
        envelopes = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(envelopes, list):
        return None
    for env in envelopes:
        if (
            isinstance(env, list)
            and len(env) >= 3
            and env[0] == "wrb.fr"
            and env[1] == "Fbv4je"
            and isinstance(env[2], str)
        ):
            try:
                payload = json.loads(env[2])
            except json.JSONDecodeError:
                continue
            if (
                isinstance(payload, list)
                and payload
                and payload[0] == "garturlres"
                and len(payload) > 1
                and isinstance(payload[1], str)
                and payload[1].startswith("http")
                and not is_google_news_url(payload[1])
            ):
                return payload[1]
    return None


def http_get(url: str, user_agent: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": (
                "application/rss+xml, application/atom+xml, "
                "application/xml, text/xml, */*"
            ),
        },
    )
    # Prefer direct socket: many Windows envs point HTTPS_PROXY at a dead local port.
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with direct.open(req, timeout=45) as resp:
            return resp.read()
    except urllib.error.URLError:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return resp.read()


def iter_entries(root: ET.Element) -> list[ET.Element]:
    items: list[ET.Element] = []
    # RSS 2.0
    for channel in root.iter():
        if local_name(channel.tag) == "channel":
            for child in list(channel):
                if local_name(child.tag) == "item":
                    items.append(child)
            if items:
                return items
    # Atom
    for child in root.iter():
        if local_name(child.tag) == "entry":
            items.append(child)
    if items:
        return items
    # Fallback: any item/entry
    for child in root.iter():
        if local_name(child.tag) in {"item", "entry"}:
            items.append(child)
    return items


def entry_fields(el: ET.Element) -> dict[str, str]:
    title = child_text(el, ("title",))
    link = child_link(el)
    summary = child_text(el, ("description", "summary", "content", "encoded"))
    published = child_text(
        el, ("pubDate", "published", "updated", "date", "created")
    )
    if not published:
        # dc:date
        for child in list(el):
            if local_name(child.tag) == "date" and (child.text or "").strip():
                published = child.text.strip()
                break
    return {
        "title": strip_html(title),
        "link": link.strip(),
        "summary": strip_html(summary),
        "published_raw": published,
    }


def parse_feed_bytes(raw: bytes) -> list[dict[str, str]]:
    # Strip BOM / leading junk before XML
    text = raw.decode("utf-8", errors="replace").lstrip()
    if text.startswith("\ufeff"):
        text = text[1:]
    # Some feeds prepend HTML comments or whitespace
    idx = text.find("<")
    if idx > 0:
        text = text[idx:]
    root = ET.fromstring(text)
    out: list[dict[str, str]] = []
    for el in iter_entries(root):
        fields = entry_fields(el)
        if fields["title"] and fields["link"]:
            out.append(fields)
    return out


def collect(config: dict[str, Any]) -> dict[str, Any]:
    window_hours = int(config.get("window_hours", 24))
    max_items = int(config.get("max_items", 15))
    min_per_region = int(config.get("min_per_region", 4))
    user_agent = config.get(
        "user_agent", "global-finance-radar-skill/1.0"
    )
    region_order = list(
        config.get("region_order")
        or ["americas", "europe", "asia", "global"]
    )
    # Backward-compatible keys if older config still uses exclude_keywords.
    hard_exclude = list(
        config.get("hard_exclude_keywords")
        or config.get("exclude_keywords")
        or []
    )
    soft_exclude = list(config.get("soft_exclude_keywords") or [])
    keep = list(
        config.get("keep_despite_soft_exclude")
        or config.get("keep_despite_exclude")
        or []
    )
    single_stock_kw = list(config.get("single_stock_exclude_keywords") or [])
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    warnings: list[str] = []
    source_stats: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    source_regions: dict[str, str] = {}

    for src in config.get("sources") or []:
        name = src.get("name") or "unknown"
        url = src.get("url") or ""
        region = (src.get("region") or "global").lower()
        source_regions[name] = region
        if not url:
            warnings.append(f"{name}: missing url")
            continue
        try:
            raw = http_get(url, user_agent)
            entries = parse_feed_bytes(raw)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ET.ParseError, OSError) as exc:
            msg = f"{name}: fetch/parse failed ({exc})"
            warnings.append(msg)
            print(f"WARNING: {msg}", file=sys.stderr)
            source_stats.append(
                {"name": name, "region": region, "ok": False, "count": 0}
            )
            continue

        if not entries:
            msg = f"{name}: no entries"
            warnings.append(msg)
            print(f"WARNING: {msg}", file=sys.stderr)
            source_stats.append(
                {"name": name, "region": region, "ok": False, "count": 0}
            )
            continue

        kept = 0
        for entry in entries:
            title = entry["title"]
            link = entry["link"]
            if is_noise_title(title):
                continue
            published = parse_published_raw(entry["published_raw"])
            if published is not None and published < cutoff:
                continue
            summary = entry["summary"]
            blob = f"{title} {summary}".lower()
            if should_exclude(blob, hard_exclude, soft_exclude, keep):
                continue
            if is_single_stock_item(title, summary, single_stock_kw):
                continue
            if len(summary) > 500:
                summary = summary[:497] + "..."
            candidates.append(
                {
                    "title": title,
                    "link": link,
                    "source": name,
                    "region": region,
                    "published": published.isoformat() if published else None,
                    "raw_summary": summary,
                    "_norm_url": normalize_url(link),
                }
            )
            kept += 1

        source_stats.append(
            {"name": name, "region": region, "ok": True, "count": kept}
        )

    def sort_key(item: dict[str, Any]) -> datetime:
        if item.get("published"):
            try:
                return datetime.fromisoformat(item["published"])
            except ValueError:
                pass
        return datetime.min.replace(tzinfo=timezone.utc)

    # Newest-first within each region; round-robin by region for coverage.
    by_region: dict[str, list[dict[str, Any]]] = {}
    for item in candidates:
        by_region.setdefault(item["region"], []).append(item)
    for rows in by_region.values():
        rows.sort(key=sort_key, reverse=True)

    for region in by_region:
        if region not in region_order:
            region_order.append(region)

    core_regions = [r for r in ("americas", "europe", "asia") if r in region_order]
    fill_regions = [r for r in region_order if r not in core_regions] + core_regions

    deduped: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    resolved_cache: dict[str, str | None] = {}
    unresolved = 0
    indexes = {r: 0 for r in region_order}
    region_counts = {r: 0 for r in region_order}

    def try_take(region: str) -> bool:
        nonlocal unresolved
        rows = by_region.get(region) or []
        i = indexes[region]
        while i < len(rows):
            item = rows[i]
            i += 1
            indexes[region] = i
            link = item["link"]
            if is_google_news_url(link):
                if link not in resolved_cache:
                    resolved_cache[link] = resolve_google_news_url(link)
                link = resolved_cache[link] or ""
                if not link:
                    unresolved += 1
                    continue
            nu = normalize_url(link)
            if nu and nu in seen_urls:
                continue
            if any(titles_similar(item["title"], prev["title"]) for prev in deduped):
                continue
            if nu:
                seen_urls.add(nu)
            deduped.append(
                {
                    "title": item["title"],
                    "link": link,
                    "source": item["source"],
                    "region": item["region"],
                    "published": item["published"],
                    "raw_summary": item["raw_summary"],
                }
            )
            region_counts[region] = region_counts.get(region, 0) + 1
            return True
        return False

    # Phase 1: ensure Americas / Europe / Asia each get a floor.
    progressed = True
    while progressed and len(deduped) < max_items:
        progressed = False
        for region in core_regions:
            if region_counts.get(region, 0) >= min_per_region:
                continue
            if len(deduped) >= max_items:
                break
            if try_take(region):
                progressed = True

    # Phase 2: fill remaining slots round-robin across all regions.
    progressed = True
    while progressed and len(deduped) < max_items:
        progressed = False
        for region in fill_regions:
            if len(deduped) >= max_items:
                break
            if try_take(region):
                progressed = True

    if unresolved:
        msg = (
            f"dropped {unresolved} Google News items whose publisher URL "
            "could not be resolved"
        )
        warnings.append(msg)
        print(f"WARNING: {msg}", file=sys.stderr)

    missing = [
        r for r in ("americas", "europe", "asia") if region_counts.get(r, 0) == 0
    ]
    if missing:
        msg = "no items kept for region(s): " + ", ".join(missing)
        warnings.append(msg)
        print(f"WARNING: {msg}", file=sys.stderr)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": window_hours,
        "count": len(deduped),
        "region_counts": {
            r: region_counts.get(r, 0) for r in ("americas", "europe", "asia", "global")
        },
        "sources": source_stats,
        "warnings": warnings,
        "items": deduped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch global markets RSS and print filtered JSON"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Path to config.json",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Override max_items from config",
    )
    args = parser.parse_args()

    if not args.config.exists():
        print(f"ERROR: config not found: {args.config}", file=sys.stderr)
        return 1

    config = load_config(args.config)
    if args.limit is not None:
        config["max_items"] = args.limit

    result = collect(config)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
