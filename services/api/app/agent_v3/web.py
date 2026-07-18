from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import httpx


MAX_WEB_BYTES = 2 * 1024 * 1024
ALLOWED_CONTENT_TYPES = {"text/html", "text/plain", "application/xhtml+xml", "application/json"}


class WebSearchError(RuntimeError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, _attrs) -> None:
        if tag in {"script", "style", "noscript", "svg"}:
            self._ignored += 1
        elif tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1
        elif tag in {"p", "div", "section", "article", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts).replace("\x00", "")
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n\s*\n\s*\n+", "\n\n", value)
        return value.strip()


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not any((address.is_private, address.is_loopback, address.is_link_local, address.is_multicast,
                    address.is_reserved, address.is_unspecified))


def validate_public_url(url: str, resolver: Callable[..., Any] = socket.getaddrinfo) -> str:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise WebSearchError("web URL must be credential-free HTTPS")
    try:
        addresses = {item[4][0] for item in resolver(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise WebSearchError("web host could not be resolved") from exc
    if not addresses or not all(_is_public_address(value) for value in addresses):
        raise WebSearchError("web URL resolves to a restricted network")
    return parsed.geturl()


@dataclass(frozen=True)
class WebSearchSettings:
    provider: str = ""
    base_url: str = ""
    api_key: str = ""

    @classmethod
    def from_environment(cls) -> "WebSearchSettings":
        provider = os.environ.get("EAI_WEB_SEARCH_PROVIDER", "").strip().lower()
        defaults = {"brave": "https://api.search.brave.com/res/v1/web/search", "tavily": "https://api.tavily.com/search"}
        return cls(
            provider=provider,
            base_url=os.environ.get("EAI_WEB_SEARCH_BASE_URL", defaults.get(provider, "")).strip().rstrip("/"),
            api_key=os.environ.get("EAI_WEB_SEARCH_API_KEY", "").strip(),
        )


class WebSearchProvider:
    def __init__(self, settings: WebSearchSettings | None = None, *,
                 client_factory: Callable[[], httpx.Client] | None = None,
                 resolver: Callable[..., Any] = socket.getaddrinfo) -> None:
        self.settings = settings or WebSearchSettings.from_environment()
        self.client_factory = client_factory or (
            lambda: httpx.Client(timeout=httpx.Timeout(10, connect=4), follow_redirects=False,
                                 headers={"User-Agent": "EAI-Desktop/0.6"})
        )
        self.resolver = resolver

    @property
    def available(self) -> bool:
        if self.settings.provider == "searxng":
            return bool(self.settings.base_url)
        return self.settings.provider in {"brave", "tavily"} and bool(self.settings.base_url and self.settings.api_key)

    def status(self) -> dict[str, Any]:
        return {
            "id": "web", "provider": self.settings.provider or "unconfigured", "available": self.available,
            "base_url": self.settings.base_url if self.settings.provider == "searxng" else "",
            "requires_key": self.settings.provider in {"brave", "tavily"},
            "health": "unchecked" if self.available else "unavailable",
            "reason": "" if self.available else "通用网页搜索未配置",
        }

    def check(self) -> dict[str, Any]:
        checked_at = datetime.now(timezone.utc).isoformat()
        if not self.available:
            return {**self.status(), "health": "unavailable", "checked_at": checked_at}
        try:
            self.search("EAI connectivity check", 1)
        except (WebSearchError, httpx.HTTPError, ValueError):
            return {
                **self.status(),
                "health": "unreachable",
                "available": False,
                "reason": "网页搜索连接失败，请检查地址、密钥和网络。",
                "checked_at": checked_at,
            }
        return {**self.status(), "health": "healthy", "checked_at": checked_at}

    def search(self, query: str, limit: int = 8) -> list[dict[str, str]]:
        if not self.available:
            raise WebSearchError("通用网页搜索未配置")
        query = " ".join(str(query or "").split())[:1200]
        if not query:
            raise WebSearchError("search query is empty")
        limit = max(1, min(int(limit), 12))
        provider = self.settings.provider
        with self.client_factory() as client:
            if provider == "searxng":
                response = client.get(f"{self.settings.base_url}/search", params={"q": query, "format": "json", "language": "auto"})
            elif provider == "brave":
                response = client.get(self.settings.base_url, params={"q": query, "count": limit, "safesearch": "moderate"},
                                      headers={"X-Subscription-Token": self.settings.api_key, "Accept": "application/json"})
            else:
                response = client.post(self.settings.base_url, json={"api_key": self.settings.api_key, "query": query,
                                                                     "max_results": limit, "search_depth": "basic"})
            response.raise_for_status()
            if len(response.content) > MAX_WEB_BYTES:
                raise WebSearchError("web search response exceeds 2 MB")
            payload = response.json()
        if provider == "brave":
            raw = ((payload.get("web") or {}).get("results") or [])
            values = [{"title": item.get("title"), "url": item.get("url"), "excerpt": item.get("description")} for item in raw]
        else:
            raw = payload.get("results") or []
            values = [{"title": item.get("title"), "url": item.get("url"),
                       "excerpt": item.get("content") or item.get("snippet")} for item in raw]
        results: list[dict[str, str]] = []
        for item in values:
            try:
                url = validate_public_url(str(item.get("url") or ""), self.resolver)
            except WebSearchError:
                continue
            results.append({
                "title": " ".join(str(item.get("title") or url).split())[:500],
                "url": url, "excerpt": " ".join(str(item.get("excerpt") or "").split())[:1800],
            })
            if len(results) >= limit:
                break
        return results

    def read(self, url: str) -> dict[str, str]:
        current = validate_public_url(url, self.resolver)
        try:
            with self.client_factory() as client:
                for _ in range(4):
                    with client.stream("GET", current, headers={"Accept": "text/html,text/plain,application/xhtml+xml"}) as response:
                        if response.status_code in {301, 302, 303, 307, 308}:
                            location = response.headers.get("location")
                            if not location:
                                raise WebSearchError("web redirect has no target")
                            current = validate_public_url(urljoin(current, location), self.resolver)
                            continue
                        response.raise_for_status()
                        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                        if content_type not in ALLOWED_CONTENT_TYPES:
                            raise WebSearchError("web content type is not supported")
                        try:
                            declared_size = int(response.headers.get("content-length", "0") or 0)
                        except ValueError:
                            declared_size = 0
                        if declared_size > MAX_WEB_BYTES:
                            raise WebSearchError("web content exceeds 2 MB")
                        content = bytearray()
                        for chunk in response.iter_bytes():
                            content.extend(chunk)
                            if len(content) > MAX_WEB_BYTES:
                                raise WebSearchError("web content exceeds 2 MB")
                        encoding = response.encoding or "utf-8"
                        try:
                            text = bytes(content).decode(encoding, errors="replace")
                        except LookupError:
                            text = bytes(content).decode("utf-8", errors="replace")
                        if content_type in {"text/html", "application/xhtml+xml"}:
                            parser = _TextExtractor()
                            parser.feed(text)
                            text = parser.text()
                        else:
                            text = " ".join(text.replace("\x00", "").split())
                        text = text[:12000]
                        return {"url": current, "text": text,
                                "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                                "content_type": content_type}
        except WebSearchError:
            raise
        except httpx.HTTPError as exc:
            raise WebSearchError("web read request failed") from exc
        raise WebSearchError("too many web redirects")
