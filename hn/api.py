"""HN Algolia API client.

Base URL: https://hn.algolia.com/api/v1
Auth: None required
Rate limit: 10,000 requests/hour
Response: JSON
Docs: https://hn.algolia.com/api
"""

import logging
import random
import time
from datetime import datetime, timezone
from typing import List, Optional

import requests

from hn.cache import HNCache
from hn.rate_limiter import get_rate_limiter

logger = logging.getLogger(__name__)

BASE_URL = "https://hn.algolia.com/api/v1"

MAX_RETRIES = 3
DEFAULT_RETRY_WAIT = 2
MAX_RETRY_WAIT = 60
REQUEST_TIMEOUT = 30


def _retry_wait_seconds(attempt: int, response: requests.Response = None) -> float:
    """Calculate retry wait with exponential backoff + jitter."""
    if response is not None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(1.0, float(retry_after))
            except ValueError:
                pass

    base = 10 if response is not None and response.status_code == 429 else DEFAULT_RETRY_WAIT
    wait = base * (2 ** attempt) + random.uniform(0, 1.0)
    return min(wait, MAX_RETRY_WAIT)


def _ts_from_datestr(date_str: str) -> int:
    """Convert YYYY-MM-DD to unix timestamp."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        raise ValueError(f"Invalid date format: {date_str} (expected YYYY-MM-DD)")


def _format_hit(hit: dict) -> dict:
    """Normalize an Algolia hit into a clean dict."""
    return {
        "id": hit.get("objectID", ""),
        "title": hit.get("title") or hit.get("story_title") or "",
        "url": hit.get("url") or "",
        "author": hit.get("author", ""),
        "points": hit.get("points") or 0,
        "num_comments": hit.get("num_comments") or 0,
        "created_at": hit.get("created_at", ""),
        "created_at_i": hit.get("created_at_i", 0),
        "story_id": hit.get("story_id"),
        "story_title": hit.get("story_title") or "",
        "story_url": hit.get("story_url") or "",
        "comment_text": hit.get("comment_text") or "",
        "type": hit.get("_tags", [""])[0] if hit.get("_tags") else "",
        "hn_url": f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}",
    }


_STOP_WORDS = frozenset({
    "a", "an", "the", "in", "on", "at", "to", "for", "of", "with", "by",
    "from", "is", "are", "was", "were", "be", "been", "being", "has", "have",
    "had", "do", "does", "did", "will", "would", "could", "should", "may",
    "might", "shall", "can", "and", "or", "but", "not", "no", "if", "then",
    "than", "that", "this", "these", "those", "it", "its", "vs", "about",
})


def _normalize_query(query: str) -> str:
    """Auto-quote unquoted multi-word queries for AND-like matching.

    Algolia treats unquoted multi-word queries as loose OR matching, which
    returns irrelevant results (e.g. "function calling MCP comparison" matches
    comments about Prolog function calling with zero mention of MCP).

    Strategy: extract up to 3 key terms (skipping stop words), quote each one.
    This gives AND-like behavior without being so strict that results vanish.
    Already-quoted phrases are preserved and count toward the 3-term limit.

    Examples:
        "MCP function calling"           → '"MCP" "function" "calling"'
        "Codex free tier ChatGPT Plus"   → '"Codex" "free" "tier"'
        '"Claude Code" vs Codex'         → '"Claude Code" "Codex"'
        "Codex pricing"                  → '"Codex" "pricing"'
        "MCP"                            → 'MCP'  (single word, no change)
    """
    stripped = query.strip()
    if not stripped:
        return stripped

    # Single word — no quoting needed
    if '"' not in stripped and ' ' not in stripped:
        return stripped

    # Extract already-quoted phrases and unquoted words
    quoted_parts: List[str] = []
    unquoted_words: List[str] = []
    i = 0
    while i < len(stripped):
        if stripped[i] == '"':
            end = stripped.find('"', i + 1)
            if end == -1:
                end = len(stripped)
            quoted_parts.append(stripped[i:end + 1])
            i = end + 1
        elif stripped[i] == ' ':
            i += 1
        else:
            end = i
            while end < len(stripped) and stripped[end] not in (' ', '"'):
                end += 1
            unquoted_words.append(stripped[i:end])
            i = end

    # If everything is already quoted, return as-is
    if not unquoted_words:
        return ' '.join(quoted_parts)

    # Filter stop words from unquoted terms
    key_words = [w for w in unquoted_words if w.lower() not in _STOP_WORDS]
    if not key_words:
        key_words = unquoted_words  # fallback: use all if everything is a stop word

    # Budget: up to 3 total quoted terms (including pre-quoted phrases)
    budget = max(3 - len(quoted_parts), 1)
    selected = key_words[:budget]

    # Build result: pre-quoted phrases first, then selected keywords quoted
    result = list(quoted_parts)
    for w in selected:
        result.append(f'"{w}"')
    return ' '.join(result)


class HNClient:
    """Client for the HN Algolia Search API."""

    def __init__(self, use_cache: bool = True):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "hn-cli/0.1.0",
        })
        self.rate_limiter = get_rate_limiter()
        self.use_cache = use_cache
        self.cache = HNCache() if use_cache else None

    # ── Search ────────────────────────────────────────────

    def search(
        self,
        query: str,
        tags: Optional[str] = None,
        numeric_filters: Optional[str] = None,
        page: int = 0,
        hits_per_page: int = 20,
    ) -> dict:
        """Search HN by relevance.

        Args:
            query: Full-text search query. Multi-word queries are auto-quoted
                   for AND-like matching. Use explicit quotes for exact phrases.
            tags: Filter tags — story, comment, show_hn, ask_hn, poll, job,
                  front_page, author_USERNAME, story_ID.
                  ANDed by default, use parens for OR: (story,poll)
            numeric_filters: e.g. "points>100,num_comments>50"
            page: Zero-indexed page number.
            hits_per_page: Results per page (max 1000).
        """
        query = _normalize_query(query)
        params = {"query": query, "page": page, "hitsPerPage": hits_per_page}
        if tags:
            params["tags"] = tags
        if numeric_filters:
            params["numericFilters"] = numeric_filters
        return self._get("/search", params)

    def search_by_date(
        self,
        query: str,
        tags: Optional[str] = None,
        numeric_filters: Optional[str] = None,
        page: int = 0,
        hits_per_page: int = 20,
    ) -> dict:
        """Search HN sorted by date (newest first).

        Same parameters as search().
        """
        query = _normalize_query(query)
        params = {"query": query, "page": page, "hitsPerPage": hits_per_page}
        if tags:
            params["tags"] = tags
        if numeric_filters:
            params["numericFilters"] = numeric_filters
        return self._get("/search_by_date", params)

    # ── Filtered Searches ─────────────────────────────────

    def search_stories(
        self,
        query: str,
        page: int = 0,
        hits_per_page: int = 20,
        numeric_filters: Optional[str] = None,
    ) -> dict:
        """Search stories only."""
        return self.search(query, tags="story", numeric_filters=numeric_filters,
                          page=page, hits_per_page=hits_per_page)

    def search_comments(
        self,
        query: str,
        page: int = 0,
        hits_per_page: int = 20,
        numeric_filters: Optional[str] = None,
    ) -> dict:
        """Search comments only."""
        return self.search(query, tags="comment", numeric_filters=numeric_filters,
                          page=page, hits_per_page=hits_per_page)

    def search_show_hn(
        self,
        query: str = "",
        page: int = 0,
        hits_per_page: int = 20,
        numeric_filters: Optional[str] = None,
    ) -> dict:
        """Search Show HN posts."""
        return self.search(query, tags="show_hn", numeric_filters=numeric_filters,
                          page=page, hits_per_page=hits_per_page)

    def search_ask_hn(
        self,
        query: str = "",
        page: int = 0,
        hits_per_page: int = 20,
        numeric_filters: Optional[str] = None,
    ) -> dict:
        """Search Ask HN posts."""
        return self.search(query, tags="ask_hn", numeric_filters=numeric_filters,
                          page=page, hits_per_page=hits_per_page)

    def search_jobs(
        self,
        query: str = "",
        page: int = 0,
        hits_per_page: int = 20,
    ) -> dict:
        """Search job posts."""
        return self.search(query, tags="job", page=page, hits_per_page=hits_per_page)

    def search_by_author(
        self,
        author: str,
        query: str = "",
        tags: Optional[str] = None,
        page: int = 0,
        hits_per_page: int = 20,
    ) -> dict:
        """Search posts by a specific author."""
        author_tag = f"author_{author}"
        if tags:
            combined_tags = f"{author_tag},{tags}"
        else:
            combined_tags = author_tag
        return self.search(query, tags=combined_tags, page=page, hits_per_page=hits_per_page)

    def search_front_page(
        self,
        query: str = "",
        page: int = 0,
        hits_per_page: int = 20,
    ) -> dict:
        """Search items that reached the front page."""
        return self.search(query, tags="front_page", page=page, hits_per_page=hits_per_page)

    def search_story_comments(
        self,
        story_id: str,
        query: str = "",
        page: int = 0,
        hits_per_page: int = 20,
    ) -> dict:
        """Search comments within a specific story."""
        return self.search(query, tags=f"comment,story_{story_id}",
                          page=page, hits_per_page=hits_per_page)

    # ── Date Range Searches ───────────────────────────────

    def search_date_range(
        self,
        query: str,
        date_from: str,
        date_to: str,
        tags: Optional[str] = None,
        page: int = 0,
        hits_per_page: int = 20,
    ) -> dict:
        """Search within a date range.

        Dates in YYYY-MM-DD format.
        """
        ts_from = _ts_from_datestr(date_from)
        ts_to = _ts_from_datestr(date_to) + 86399  # end of day
        numeric = f"created_at_i>{ts_from},created_at_i<{ts_to}"
        return self.search_by_date(query, tags=tags, numeric_filters=numeric,
                                   page=page, hits_per_page=hits_per_page)

    def popular_stories(
        self,
        query: str = "",
        min_points: int = 100,
        page: int = 0,
        hits_per_page: int = 20,
    ) -> dict:
        """Find popular stories above a point threshold."""
        return self.search(query, tags="story", numeric_filters=f"points>{min_points}",
                          page=page, hits_per_page=hits_per_page)

    def hot_discussions(
        self,
        query: str = "",
        min_comments: int = 100,
        page: int = 0,
        hits_per_page: int = 20,
    ) -> dict:
        """Find stories with heavy discussion."""
        return self.search(query, tags="story", numeric_filters=f"num_comments>{min_comments}",
                          page=page, hits_per_page=hits_per_page)

    # ── Item / User Lookup ────────────────────────────────

    def get_item(self, item_id: str) -> dict:
        """Get a single item with full comment tree.

        Returns the item with nested children (comments).
        """
        return self._get(f"/items/{item_id}", {})

    def get_user(self, username: str) -> dict:
        """Get user profile."""
        return self._get(f"/users/{username}", {})

    # ── Internal ──────────────────────────────────────────

    def _get(self, path: str, params: dict) -> dict:
        """Execute an API GET with caching and rate limiting."""
        url = f"{BASE_URL}{path}"
        cache_params = {k: str(v) for k, v in params.items()}

        if self.use_cache and self.cache:
            cached = self.cache.get(url, cache_params)
            if cached is not None:
                logger.debug("Cache hit: %s %s", path, params)
                return cached

        last_error = None
        for attempt in range(MAX_RETRIES + 1):
            self.rate_limiter.acquire()
            try:
                response = self.session.get(url, params=params, timeout=(10, REQUEST_TIMEOUT))

                if response.status_code == 429:
                    wait = _retry_wait_seconds(attempt, response)
                    if attempt < MAX_RETRIES:
                        logger.warning(
                            "HN Algolia 429 — waiting %.1fs (retry %d/%d)",
                            wait, attempt + 1, MAX_RETRIES,
                        )
                        time.sleep(wait)
                        continue
                    return {"error": "Rate limited (HTTP 429) after retries", "hits": [], "nbHits": 0}

                if response.status_code == 404:
                    return {"error": f"Not found: {path}", "hits": [], "nbHits": 0}

                response.raise_for_status()
                result = response.json()

                # Normalize search results
                if "hits" in result:
                    result["hits"] = [_format_hit(h) for h in result["hits"]]

                if self.use_cache and self.cache and "error" not in result:
                    self.cache.set(url, cache_params, result)

                return result

            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < MAX_RETRIES:
                    wait = _retry_wait_seconds(attempt)
                    logger.warning("Request error — waiting %.1fs (retry %d/%d)", wait, attempt + 1, MAX_RETRIES)
                    time.sleep(wait)
                    continue
                return {"error": str(e), "hits": [], "nbHits": 0}

        return {"error": str(last_error) if last_error else "max retries exceeded", "hits": [], "nbHits": 0}
