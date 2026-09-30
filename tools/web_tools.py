"""Internet tools: web search and page fetch (hub-local)."""

from __future__ import annotations

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from core.registry import tool

_UA = "NexusNode/0.3 (+personal-assistant; https://github.com/Sell6ix/Nexus-Node)"
_MAX_FETCH_BYTES = 1_500_000


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 25.0,
) -> Any:
    req = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": _UA, **(headers or {})},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw.strip() else {}


def _http_text(url: str, *, timeout: float = 25.0, max_bytes: int = _MAX_FETCH_BYTES) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        data = data[:max_bytes]
    if "html" not in ctype and "text" not in ctype and "json" not in ctype and ctype:
        # still try decode for simple pages missing headers
        pass
    return data.decode("utf-8", errors="replace")


def _html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript|svg).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</p>", "\n\n", text)
    text = re.sub(r"(?is)</(div|h[1-6]|li|tr)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _search_brave(query: str, count: int) -> list[dict[str, str]] | None:
    key = (os.getenv("BRAVE_API_KEY") or os.getenv("BRAVE_SEARCH_API_KEY") or "").strip()
    if not key:
        return None
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(
        {"q": query, "count": str(count)}
    )
    data = _http_json(url, headers={"Accept": "application/json", "X-Subscription-Token": key})
    web = (data.get("web") or {}).get("results") or []
    out: list[dict[str, str]] = []
    for item in web[:count]:
        out.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "snippet": str(item.get("description") or ""),
            }
        )
    return out


def _search_tavily(query: str, count: int) -> list[dict[str, str]] | None:
    key = (os.getenv("TAVILY_API_KEY") or "").strip()
    if not key:
        return None
    payload = json.dumps(
        {"api_key": key, "query": query, "max_results": count, "include_answer": False}
    ).encode("utf-8")
    data = _http_json(
        "https://api.tavily.com/search",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=payload,
    )
    out: list[dict[str, str]] = []
    for item in (data.get("results") or [])[:count]:
        out.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "snippet": str(item.get("content") or item.get("snippet") or ""),
            }
        )
    return out


def _search_duckduckgo(query: str, count: int) -> list[dict[str, str]]:
    """Best-effort DuckDuckGo Lite POST fallback (no API key)."""
    body = urllib.parse.urlencode({"q": query, "kl": "us-en"}).encode("utf-8")
    req = urllib.request.Request(
        "https://lite.duckduckgo.com/lite/",
        data=body,
        headers={
            "User-Agent": _UA,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25.0) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    # Lite results: <a rel="nofollow" href="https://...">Title</a> then snippet in nearby <td>
    for match in re.finditer(
        r'<a[^>]+rel="nofollow"[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
        raw,
        flags=re.I | re.S,
    ):
        href = html.unescape(match.group(1))
        if "duckduckgo.com" in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        title = _html_to_text(match.group(2))[:200] or href
        snippet = ""
        tail = raw[match.end() : match.end() + 1200]
        sn = re.search(r"<td[^>]*class=\"result-snippet\"[^>]*>(.*?)</td>", tail, flags=re.I | re.S)
        if not sn:
            sn = re.search(r"<td[^>]*>(.*?)</td>", tail, flags=re.I | re.S)
        if sn:
            snippet = _html_to_text(sn.group(1))[:400]
        out.append({"title": title, "url": href, "snippet": snippet})
        if len(out) >= count:
            break
    return out


def _search_wikipedia(query: str, count: int) -> list[dict[str, str]]:
    """OpenSearch fallback (free, no key) when HTML search is empty."""
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {
            "action": "opensearch",
            "search": query,
            "limit": str(count),
            "namespace": "0",
            "format": "json",
        }
    )
    data = _http_json(url)
    if not isinstance(data, list) or len(data) < 4:
        return []
    titles, descs, links = data[1], data[2], data[3]
    out: list[dict[str, str]] = []
    for i, link in enumerate(links):
        out.append(
            {
                "title": str(titles[i] if i < len(titles) else link),
                "url": str(link),
                "snippet": str(descs[i] if i < len(descs) else ""),
            }
        )
    return out


@tool
def web_search(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the public web for current information.

    Uses Brave or Tavily when ``BRAVE_API_KEY`` / ``TAVILY_API_KEY`` is set;
    otherwise falls back to DuckDuckGo HTML (best-effort, no key).

    Args:
        query: What to search for.
        max_results: Number of results (1–10, default 5).

    Returns:
        Ranked results with title, url, snippet, and which provider was used.
    """
    try:
        q = (query or "").strip()
        if not q:
            return {"ok": False, "error": "query must not be empty"}
        count = max(1, min(int(max_results or 5), 10))
        provider = "none"
        results: list[dict[str, str]] | None = None
        errors: list[str] = []
        try:
            results = _search_brave(q, count)
            if results is not None:
                provider = "brave"
        except Exception as exc:  # noqa: BLE001
            errors.append(f"brave: {exc}")
        if results is None:
            try:
                results = _search_tavily(q, count)
                if results is not None:
                    provider = "tavily"
            except Exception as exc:  # noqa: BLE001
                errors.append(f"tavily: {exc}")
        if results is None:
            try:
                results = _search_duckduckgo(q, count)
                provider = "duckduckgo"
            except Exception as exc:  # noqa: BLE001
                errors.append(f"duckduckgo: {exc}")
                results = None
        if not results:
            try:
                results = _search_wikipedia(q, count)
                if results:
                    provider = "wikipedia"
            except Exception as exc:  # noqa: BLE001
                errors.append(f"wikipedia: {exc}")
                results = results or []
        if not results:
            return {
                "ok": False,
                "error": "No search results."
                + ((" Details: " + "; ".join(errors)) if errors else ""),
                "query": q,
                "provider": provider,
            }
        return {
            "ok": True,
            "query": q,
            "provider": provider,
            "count": len(results),
            "results": results,
            "hint": (
                None
                if provider in {"brave", "tavily"}
                else "Set BRAVE_API_KEY or TAVILY_API_KEY for more reliable search."
            ),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "query": query}


@tool
def fetch_url(url: str, max_chars: int = 8000) -> dict[str, Any]:
    """Download a public http(s) page and return readable text.

    Args:
        url: Absolute http or https URL.
        max_chars: Max characters of extracted text (500–20000).

    Returns:
        Title guess, final URL, and plain text excerpt.
    """
    try:
        parsed = urllib.parse.urlparse((url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return {"ok": False, "error": "Only absolute http(s) URLs are allowed.", "url": url}
        limit = max(500, min(int(max_chars or 8000), 20_000))
        raw = _http_text(parsed.geturl())
        title_m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
        title = _html_to_text(title_m.group(1))[:200] if title_m else ""
        text = _html_to_text(raw)
        truncated = len(text) > limit
        text = text[:limit]
        return {
            "ok": True,
            "url": parsed.geturl(),
            "title": title or None,
            "chars": len(text),
            "truncated": truncated,
            "text": text,
        }
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"HTTP {exc.code}", "url": url}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc), "url": url}
