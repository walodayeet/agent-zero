import os

import aiohttp

from helpers import runtime

# The Docker image ships a bundled SearXNG on 55510. Bare-metal installs use
# SEARXNG_URL when set, otherwise the first reachable instance from the defaults.
# DuckDuckGo is the last resort (it rate-limits aggressively on shared IPs).
DEFAULT_SEARXNG_URLS = (
    "http://localhost:55510/search",
    "http://127.0.0.1:8888/search",
)
SEARXNG_TIMEOUT = 8


def searxng_url() -> str:
    return os.environ.get("SEARXNG_URL", "").strip()


def _candidate_urls() -> list[str]:
    urls: list[str] = []
    configured = searxng_url()
    if configured:
        urls.append(configured)
    for url in DEFAULT_SEARXNG_URLS:
        if url not in urls:
            urls.append(url)
    return urls


async def search(query: str):
    return await runtime.call_development_function(_search, query=query)


async def _search(query: str):
    errors = []
    for url in _candidate_urls():
        try:
            result = await _searxng(url, query)
            if result.get("results"):
                result.setdefault("source", url)
                return result
        except Exception as exc:
            errors.append(f"{url}: {exc}")

    try:
        return await _duckduckgo(query)
    except Exception as exc:
        errors.append(f"duckduckgo: {exc}")

    return {"results": [], "error": "; ".join(errors)}


async def _searxng(url: str, query: str):
    timeout = aiohttp.ClientTimeout(total=SEARXNG_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, data={"q": query, "format": "json"}) as response:
            response.raise_for_status()
            payload = await response.json(content_type=None)
            if not isinstance(payload, dict):
                raise ValueError("unexpected response")
            return payload


async def _duckduckgo(query: str):
    import asyncio

    from duckduckgo_search import DDGS

    def run():
        with DDGS() as ddgs:
            return list(
                ddgs.text(query, safesearch="off", timelimit="y", max_results=10) or []
            )

    items = await asyncio.to_thread(run)
    results = []
    for item in items:
        results.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("href") or item.get("url") or ""),
                "content": str(item.get("body") or item.get("content") or ""),
            }
        )
    return {"results": results, "source": "duckduckgo"}
