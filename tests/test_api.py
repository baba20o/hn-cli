"""Tests for hn.api — HN Algolia client."""

import json
import time
from unittest.mock import MagicMock, patch

import pytest

from hn.api import HNClient, _format_hit, _ts_from_datestr, _retry_wait_seconds


# ── Fixtures ──────────────────────────────────────────────

SAMPLE_SEARCH_RESPONSE = {
    "hits": [
        {
            "objectID": "12345",
            "title": "Show HN: My New Project",
            "url": "https://example.com",
            "author": "testuser",
            "points": 150,
            "num_comments": 42,
            "created_at": "2026-03-15T10:00:00.000Z",
            "created_at_i": 1773756000,
            "story_id": None,
            "story_title": None,
            "story_url": None,
            "comment_text": None,
            "_tags": ["story", "author_testuser", "story_12345"],
        }
    ],
    "nbHits": 1,
    "page": 0,
    "nbPages": 1,
    "hitsPerPage": 20,
}

SAMPLE_COMMENT_RESPONSE = {
    "hits": [
        {
            "objectID": "67890",
            "title": None,
            "url": None,
            "author": "commenter",
            "points": None,
            "num_comments": None,
            "created_at": "2026-03-15T11:00:00.000Z",
            "created_at_i": 1773759600,
            "story_id": 12345,
            "story_title": "Show HN: My New Project",
            "story_url": "https://example.com",
            "comment_text": "This is a great project!",
            "_tags": ["comment", "author_commenter", "story_12345"],
        }
    ],
    "nbHits": 1,
    "page": 0,
    "nbPages": 1,
    "hitsPerPage": 20,
}

SAMPLE_ITEM_RESPONSE = {
    "id": 12345,
    "title": "Show HN: My New Project",
    "author": "testuser",
    "url": "https://example.com",
    "points": 150,
    "created_at": "2026-03-15T10:00:00.000Z",
    "children": [
        {
            "id": 67890,
            "author": "commenter",
            "text": "This is a great project!",
            "created_at": "2026-03-15T11:00:00.000Z",
            "children": [],
        }
    ],
}

SAMPLE_USER_RESPONSE = {
    "username": "testuser",
    "karma": 5000,
    "about": "I build things.",
    "created_at": "2020-01-01T00:00:00.000Z",
}


def _mock_response(data, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.headers = {}
    resp.raise_for_status = MagicMock()
    return resp


# ── Unit Tests: Helpers ───────────────────────────────────


def test_format_hit_story():
    raw = SAMPLE_SEARCH_RESPONSE["hits"][0]
    hit = _format_hit(raw)
    assert hit["id"] == "12345"
    assert hit["title"] == "Show HN: My New Project"
    assert hit["author"] == "testuser"
    assert hit["points"] == 150
    assert hit["num_comments"] == 42
    assert hit["type"] == "story"
    assert "12345" in hit["hn_url"]


def test_format_hit_comment():
    raw = SAMPLE_COMMENT_RESPONSE["hits"][0]
    hit = _format_hit(raw)
    assert hit["id"] == "67890"
    assert hit["author"] == "commenter"
    assert hit["comment_text"] == "This is a great project!"
    assert hit["story_id"] == 12345
    assert hit["type"] == "comment"


def test_ts_from_datestr():
    ts = _ts_from_datestr("2026-01-01")
    assert ts == 1767225600


def test_ts_from_datestr_invalid():
    with pytest.raises(ValueError, match="Invalid date format"):
        _ts_from_datestr("not-a-date")


def test_retry_wait_seconds_base():
    wait = _retry_wait_seconds(0)
    assert 2.0 <= wait <= 3.0  # base=2, jitter 0-1


def test_retry_wait_seconds_429():
    resp = MagicMock()
    resp.status_code = 429
    resp.headers = {}
    wait = _retry_wait_seconds(0, resp)
    assert 10.0 <= wait <= 11.0  # base=10, jitter 0-1


def test_retry_wait_respects_retry_after():
    resp = MagicMock()
    resp.status_code = 429
    resp.headers = {"Retry-After": "25"}
    wait = _retry_wait_seconds(0, resp)
    assert wait == 25.0


def test_retry_wait_capped():
    wait = _retry_wait_seconds(10)  # attempt 10 → huge backoff
    assert wait <= 60.0  # MAX_RETRY_WAIT


# ── Client Tests (mocked HTTP) ───────────────────────────


@patch("hn.api.get_rate_limiter")
def test_search(mock_rl):
    mock_rl.return_value = MagicMock()
    client = HNClient(use_cache=False)

    with patch.object(client.session, "get", return_value=_mock_response(SAMPLE_SEARCH_RESPONSE)):
        result = client.search("test query")
        assert result["nbHits"] == 1
        assert len(result["hits"]) == 1
        assert result["hits"][0]["id"] == "12345"


@patch("hn.api.get_rate_limiter")
def test_search_stories(mock_rl):
    mock_rl.return_value = MagicMock()
    client = HNClient(use_cache=False)

    with patch.object(client.session, "get", return_value=_mock_response(SAMPLE_SEARCH_RESPONSE)) as mock_get:
        result = client.search_stories("test")
        assert result["nbHits"] == 1
        # Verify tags=story was passed
        call_args = mock_get.call_args
        assert "story" in call_args[1]["params"]["tags"]


@patch("hn.api.get_rate_limiter")
def test_search_comments(mock_rl):
    mock_rl.return_value = MagicMock()
    client = HNClient(use_cache=False)

    with patch.object(client.session, "get", return_value=_mock_response(SAMPLE_COMMENT_RESPONSE)):
        result = client.search_comments("great project")
        assert result["hits"][0]["comment_text"] == "This is a great project!"


@patch("hn.api.get_rate_limiter")
def test_search_by_author(mock_rl):
    mock_rl.return_value = MagicMock()
    client = HNClient(use_cache=False)

    with patch.object(client.session, "get", return_value=_mock_response(SAMPLE_SEARCH_RESPONSE)) as mock_get:
        result = client.search_by_author("testuser")
        call_args = mock_get.call_args
        assert "author_testuser" in call_args[1]["params"]["tags"]


@patch("hn.api.get_rate_limiter")
def test_search_by_date(mock_rl):
    mock_rl.return_value = MagicMock()
    client = HNClient(use_cache=False)

    with patch.object(client.session, "get", return_value=_mock_response(SAMPLE_SEARCH_RESPONSE)) as mock_get:
        result = client.search_by_date("test")
        url = mock_get.call_args[0][0]
        assert "/search_by_date" in url


@patch("hn.api.get_rate_limiter")
def test_search_date_range(mock_rl):
    mock_rl.return_value = MagicMock()
    client = HNClient(use_cache=False)

    with patch.object(client.session, "get", return_value=_mock_response(SAMPLE_SEARCH_RESPONSE)) as mock_get:
        result = client.search_date_range("test", "2026-01-01", "2026-03-01")
        call_args = mock_get.call_args
        numeric = call_args[1]["params"]["numericFilters"]
        assert "created_at_i>" in numeric
        assert "created_at_i<" in numeric


@patch("hn.api.get_rate_limiter")
def test_popular_stories(mock_rl):
    mock_rl.return_value = MagicMock()
    client = HNClient(use_cache=False)

    with patch.object(client.session, "get", return_value=_mock_response(SAMPLE_SEARCH_RESPONSE)) as mock_get:
        result = client.popular_stories("", min_points=200)
        call_args = mock_get.call_args
        assert "points>200" in call_args[1]["params"]["numericFilters"]


@patch("hn.api.get_rate_limiter")
def test_hot_discussions(mock_rl):
    mock_rl.return_value = MagicMock()
    client = HNClient(use_cache=False)

    with patch.object(client.session, "get", return_value=_mock_response(SAMPLE_SEARCH_RESPONSE)) as mock_get:
        result = client.hot_discussions("", min_comments=50)
        call_args = mock_get.call_args
        assert "num_comments>50" in call_args[1]["params"]["numericFilters"]


@patch("hn.api.get_rate_limiter")
def test_get_item(mock_rl):
    mock_rl.return_value = MagicMock()
    client = HNClient(use_cache=False)

    with patch.object(client.session, "get", return_value=_mock_response(SAMPLE_ITEM_RESPONSE)) as mock_get:
        result = client.get_item("12345")
        url = mock_get.call_args[0][0]
        assert "/items/12345" in url
        assert result["title"] == "Show HN: My New Project"


@patch("hn.api.get_rate_limiter")
def test_get_user(mock_rl):
    mock_rl.return_value = MagicMock()
    client = HNClient(use_cache=False)

    with patch.object(client.session, "get", return_value=_mock_response(SAMPLE_USER_RESPONSE)) as mock_get:
        result = client.get_user("testuser")
        url = mock_get.call_args[0][0]
        assert "/users/testuser" in url
        assert result["username"] == "testuser"
        assert result["karma"] == 5000


@patch("hn.api.get_rate_limiter")
def test_429_retry(mock_rl):
    mock_rl.return_value = MagicMock()
    client = HNClient(use_cache=False)

    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {}

    resp_ok = _mock_response(SAMPLE_SEARCH_RESPONSE)

    with patch.object(client.session, "get", side_effect=[resp_429, resp_ok]):
        with patch("hn.api.time.sleep"):
            result = client.search("test")
            assert result["nbHits"] == 1


@patch("hn.api.get_rate_limiter")
def test_404_returns_error(mock_rl):
    mock_rl.return_value = MagicMock()
    client = HNClient(use_cache=False)

    resp_404 = MagicMock()
    resp_404.status_code = 404
    resp_404.headers = {}

    with patch.object(client.session, "get", return_value=resp_404):
        result = client.get_item("99999999999")
        assert "error" in result
        assert "Not found" in result["error"]


@patch("hn.api.get_rate_limiter")
def test_search_story_comments(mock_rl):
    mock_rl.return_value = MagicMock()
    client = HNClient(use_cache=False)

    with patch.object(client.session, "get", return_value=_mock_response(SAMPLE_COMMENT_RESPONSE)) as mock_get:
        result = client.search_story_comments("12345", query="great")
        call_args = mock_get.call_args
        assert "story_12345" in call_args[1]["params"]["tags"]
        assert "comment" in call_args[1]["params"]["tags"]
