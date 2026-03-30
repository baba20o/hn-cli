# hn-cli

A CLI for searching Hacker News via the [Algolia HN Search API](https://hn.algolia.com/api). Full-text search, comments, authors, discussions, and date filtering.

## Install

```bash
pip install -e .
```

## Quick Start

```bash
# Search by relevance
hn search "autonomous agents"

# Search by date (newest first)
hn recent "Claude Code" -n 10

# Stories only, with point filter
hn stories "vibe coding" --points 50

# Comment search
hn comments "cursor vs claude"

# Markdown output (for piping to other tools)
hn search "LLM" -m
```

## Commands

### Search

| Command | Description |
|---------|-------------|
| `hn search <query>` | Search by relevance |
| `hn recent <query>` | Search by date (newest first) |
| `hn stories <query>` | Stories only |
| `hn comments <query>` | Comments only |
| `hn show-hn [query]` | Show HN posts |
| `hn ask-hn [query]` | Ask HN posts |
| `hn jobs [query]` | Job posts |
| `hn front-page [query]` | Items that reached the front page |

### Filters

| Command | Description |
|---------|-------------|
| `hn popular [query]` | Stories above a point threshold (default: 100) |
| `hn hot [query]` | Stories with heavy discussion (default: 100+ comments) |
| `hn author <username>` | Posts by a specific author |
| `hn date-range <query> --from YYYY-MM-DD --to YYYY-MM-DD` | Search within a date range |
| `hn thread <story_id>` | Search comments within a story thread |

### Lookup

| Command | Description |
|---------|-------------|
| `hn item <id>` | Full item details with comment tree |
| `hn user <username>` | User profile (karma, about) |

### Utility

| Command | Description |
|---------|-------------|
| `hn clear-cache` | Clear local response cache |

## Common Options

All search commands support:

| Flag | Description |
|------|-------------|
| `-n, --limit` | Results per page (default: 20, max: 1000) |
| `-p, --page` | Page number (0-indexed) |
| `--points` | Minimum points filter |
| `--comments` | Minimum comments filter |
| `-t, --tags` | Tag filters: `story`, `comment`, `show_hn`, `ask_hn`, `job`, `front_page` |
| `-j, --json-output` | Raw JSON output |
| `-m, --markdown` | Markdown table output |
| `--no-cache` | Disable response caching |
| `--debug` | Enable debug logging |

## Query Tips

Multi-word queries are **auto-quoted** for AND-like matching. Algolia's default is loose OR matching, which returns irrelevant results for multi-term queries. The CLI automatically quotes up to 3 key terms (skipping stop words) so all terms must appear in results.

| You type | CLI sends to Algolia | Why |
|----------|---------------------|-----|
| `MCP function calling` | `"MCP" "function" "calling"` | All 3 terms required |
| `Codex free tier ChatGPT Plus` | `"Codex" "free" "tier"` | Capped at 3 key terms |
| `"Claude Code" vs Codex` | `"Claude Code" "Codex"` | Pre-quoted phrase preserved, stop word "vs" skipped |
| `MCP` | `MCP` | Single word unchanged |

**For best results:**
- Use 2-3 specific terms, not full sentences
- Pre-quote multi-word phrases: `"Claude Code" "rate limiting"`
- Use `--points` or `--comments` filters to find high-signal results
- Prefer `hn comments` for developer sentiment and `hn stories` for news

## Examples

```bash
# Popular AI stories with 500+ points
hn popular "AI" --min-points 500

# Hot discussions about Rust with 200+ comments
hn hot "rust" --min-comments 200

# pg's stories
hn author pg --type story

# Show HN posts about MCP from last month
hn date-range "MCP" --from 2026-03-01 --to 2026-03-29 -t show_hn

# Search within a specific thread
hn thread 44567857 -q "inevitable"

# User profile
hn user dang

# JSON output for piping
hn search "startup" -n 100 -j | jq '.hits[].title'

# Markdown for reports
hn popular "LLM" --min-points 200 -m >> research-notes.md

# Developer sentiment on pricing (good query patterns)
hn comments "Codex pricing" --limit 10
hn comments '"Claude Code" "rate limit"' --limit 10
hn comments "MCP security" --limit 5 -m
```

## API

No API key required. Rate limit: 10,000 requests/hour. The CLI rate-limits to 1 request/second and caches responses for 1 hour.

- Base URL: `https://hn.algolia.com/api/v1`
- Endpoints: `/search` (relevance), `/search_by_date` (date), `/items/:id`, `/users/:username`
- Tags: `story`, `comment`, `show_hn`, `ask_hn`, `poll`, `job`, `front_page`, `author_USERNAME`, `story_ID`
- Numeric filters: `points`, `num_comments`, `created_at_i` (unix timestamp)

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## License

MIT
