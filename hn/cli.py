"""CLI entry point for Hacker News search via Algolia API."""

import json
import logging
import textwrap
from datetime import datetime, timezone

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from hn.api import HNClient

console = Console()


def _error_exit(result: dict) -> bool:
    if "error" in result:
        console.print(f"[red]Error:[/red] {result['error']}")
        raise SystemExit(1)
    return False


def _truncate(text: str, width: int = 60) -> str:
    if not text:
        return ""
    return text if len(text) <= width else f"{text[:width - 3]}..."


def _format_date(value: str) -> str:
    """Format ISO date string to YYYY-MM-DD HH:MM."""
    if not value:
        return ""
    return value[:16].replace("T", " ")


def _format_age(ts: int) -> str:
    """Format unix timestamp as relative age."""
    if not ts:
        return ""
    now = datetime.now(timezone.utc).timestamp()
    diff = now - ts
    if diff < 3600:
        return f"{int(diff / 60)}m ago"
    if diff < 86400:
        return f"{int(diff / 3600)}h ago"
    if diff < 2592000:
        return f"{int(diff / 86400)}d ago"
    return f"{int(diff / 2592000)}mo ago"


def _escape_md(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def _strip_html(text: str) -> str:
    """Strip basic HTML tags from comment text."""
    import re
    text = re.sub(r"<p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


# ── Renderers ─────────────────────────────────────────────


def _render_stories(result: dict, title: str) -> None:
    """Render search results as a Rich table."""
    hits = result.get("hits", [])
    total = result.get("nbHits", len(hits))

    if not hits:
        console.print(f"[yellow]No results for {title}[/yellow]")
        return

    table = Table(title=title)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Title", style="white")
    table.add_column("Author", style="green")
    table.add_column("Pts", style="yellow", justify="right")
    table.add_column("Comments", style="magenta", justify="right")
    table.add_column("Age", style="dim")

    for hit in hits:
        table.add_row(
            str(hit.get("id", "")),
            _truncate(hit.get("title", ""), 55),
            hit.get("author", ""),
            str(hit.get("points", 0)),
            str(hit.get("num_comments", 0)),
            _format_age(hit.get("created_at_i", 0)),
        )

    console.print(table)
    page = result.get("page", 0)
    nb_pages = result.get("nbPages", 1)
    console.print(f"[dim]{total:,} total results — page {page + 1}/{nb_pages}[/dim]")


def _render_stories_markdown(result: dict, title: str) -> None:
    """Render search results as a markdown table."""
    hits = result.get("hits", [])
    total = result.get("nbHits", len(hits))

    click.echo(f"## {title}")
    click.echo("")
    click.echo("| ID | Title | Author | Pts | Comments | Date |")
    click.echo("|---|---|---|---|---|---|")
    for hit in hits:
        click.echo(
            f"| {hit.get('id', '')} "
            f"| {_escape_md(_truncate(hit.get('title', ''), 55))} "
            f"| {_escape_md(hit.get('author', ''))} "
            f"| {hit.get('points', 0)} "
            f"| {hit.get('num_comments', 0)} "
            f"| {_format_date(hit.get('created_at', ''))} |"
        )
    click.echo("")
    click.echo(f"{total:,} total results")


def _render_comments(result: dict, title: str) -> None:
    """Render comment search results."""
    hits = result.get("hits", [])
    total = result.get("nbHits", len(hits))

    if not hits:
        console.print(f"[yellow]No comments found for {title}[/yellow]")
        return

    console.print(f"[bold]{title}[/bold] — {total:,} total matches\n")

    for hit in hits:
        text = _strip_html(hit.get("comment_text", ""))
        story = hit.get("story_title") or f"Story #{hit.get('story_id', '?')}"
        header = f"[green]{hit.get('author', '?')}[/green] on [cyan]{story}[/cyan] — {_format_age(hit.get('created_at_i', 0))}"
        console.print(header)
        console.print(textwrap.fill(text, width=100) if text else "[dim]<empty>[/dim]")
        console.print(f"[dim]{hit.get('hn_url', '')}[/dim]\n")


def _render_comments_markdown(result: dict, title: str) -> None:
    hits = result.get("hits", [])
    total = result.get("nbHits", len(hits))

    click.echo(f"## {title}")
    click.echo(f"{total:,} total matches\n")

    for hit in hits:
        text = _strip_html(hit.get("comment_text", ""))
        story = hit.get("story_title") or f"Story #{hit.get('story_id', '?')}"
        click.echo(f"**{hit.get('author', '?')}** on *{_escape_md(story)}* — {_format_date(hit.get('created_at', ''))}")
        click.echo(f"> {_escape_md(text[:300])}")
        click.echo(f"[Link]({hit.get('hn_url', '')})\n")


def _render_item_detail(item: dict) -> None:
    """Render a single item with comment tree."""
    lines = [
        f"[bold]ID:[/bold] {item.get('id', 'N/A')}",
        f"[bold]Title:[/bold] {item.get('title', 'N/A')}",
        f"[bold]Author:[/bold] {item.get('author', 'N/A')}",
        f"[bold]URL:[/bold] {item.get('url') or 'N/A'}",
        f"[bold]Points:[/bold] {item.get('points', 0)}",
        f"[bold]Created:[/bold] {item.get('created_at', 'N/A')}",
    ]

    text = item.get("text")
    if text:
        lines.append(f"\n[bold]Text:[/bold]\n{_strip_html(text)}")

    console.print(Panel("\n".join(lines), title="Item Details", expand=False))

    children = item.get("children", [])
    if children:
        console.print(f"\n[bold]{len(children)} top-level comments:[/bold]\n")
        for i, child in enumerate(children[:10]):
            author = child.get("author") or "[deleted]"
            text = _strip_html(child.get("text") or "")
            snippet = textwrap.shorten(text, width=120, placeholder="...")
            console.print(f"  [green]{author}[/green]: {snippet}")
        if len(children) > 10:
            console.print(f"  [dim]... and {len(children) - 10} more[/dim]")


def _render_item_detail_markdown(item: dict) -> None:
    click.echo(f"## {item.get('title', 'Item')}")
    click.echo("")
    click.echo(f"- **ID:** {item.get('id', 'N/A')}")
    click.echo(f"- **Author:** {item.get('author', 'N/A')}")
    click.echo(f"- **URL:** {item.get('url') or 'N/A'}")
    click.echo(f"- **Points:** {item.get('points', 0)}")
    click.echo(f"- **Created:** {item.get('created_at', 'N/A')}")

    text = item.get("text")
    if text:
        click.echo(f"\n### Text\n{_strip_html(text)}")

    children = item.get("children", [])
    if children:
        click.echo(f"\n### Comments ({len(children)} top-level)\n")
        for child in children[:10]:
            author = child.get("author") or "[deleted]"
            text = _strip_html(child.get("text") or "")
            snippet = _escape_md(textwrap.shorten(text, width=120, placeholder="..."))
            click.echo(f"- **{author}**: {snippet}")


def _render_user(user: dict) -> None:
    lines = [
        f"[bold]Username:[/bold] {user.get('username', 'N/A')}",
        f"[bold]Karma:[/bold] {user.get('karma', 0):,}",
        f"[bold]Created:[/bold] {user.get('created_at', 'N/A')}",
        f"[bold]About:[/bold] {_strip_html(user.get('about') or 'N/A')}",
    ]
    console.print(Panel("\n".join(lines), title="User Profile", expand=False))


def _render_user_markdown(user: dict) -> None:
    click.echo(f"## {user.get('username', 'User')}")
    click.echo(f"- **Karma:** {user.get('karma', 0):,}")
    click.echo(f"- **Created:** {user.get('created_at', 'N/A')}")
    click.echo(f"- **About:** {_strip_html(user.get('about') or 'N/A')}")


# ── CLI Commands ──────────────────────────────────────────


@click.group()
@click.option("--debug", is_flag=True, help="Enable debug logging")
@click.option("--no-cache", is_flag=True, help="Disable response caching")
@click.pass_context
def main(ctx, debug, no_cache):
    """hn — Hacker News search and discussion intelligence tool (Algolia API)."""
    logging.basicConfig(level=logging.DEBUG if debug else logging.WARNING)
    ctx.ensure_object(dict)
    ctx.obj["client"] = HNClient(use_cache=not no_cache)


@main.command()
@click.argument("query")
@click.option("--limit", "-n", default=20, show_default=True, help="Results per page (max 1000)")
@click.option("--page", "-p", default=0, show_default=True, help="Page number (0-indexed)")
@click.option("--tags", "-t", default=None, help="Tag filters (story,comment,show_hn,ask_hn,job,front_page)")
@click.option("--points", type=int, default=None, help="Minimum points filter")
@click.option("--comments", type=int, default=None, help="Minimum comments filter")
@click.option("--json-output", "-j", is_flag=True, help="Output raw JSON")
@click.option("--markdown", "-m", is_flag=True, help="Output as markdown")
@click.pass_context
def search(ctx, query, limit, page, tags, points, comments, json_output, markdown):
    """Search Hacker News by relevance."""
    client = ctx.obj["client"]
    numeric = _build_numeric_filters(points, comments)
    result = client.search(query, tags=tags, numeric_filters=numeric,
                          page=page, hits_per_page=limit)
    if _error_exit(result):
        return
    _output(result, f"Search: {query}", json_output, markdown)


@main.command()
@click.argument("query")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--page", "-p", default=0, show_default=True)
@click.option("--tags", "-t", default=None)
@click.option("--points", type=int, default=None, help="Minimum points filter")
@click.option("--comments", type=int, default=None, help="Minimum comments filter")
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def recent(ctx, query, limit, page, tags, points, comments, json_output, markdown):
    """Search Hacker News sorted by date (newest first)."""
    client = ctx.obj["client"]
    numeric = _build_numeric_filters(points, comments)
    result = client.search_by_date(query, tags=tags, numeric_filters=numeric,
                                   page=page, hits_per_page=limit)
    if _error_exit(result):
        return
    _output(result, f"Recent: {query}", json_output, markdown)


@main.command()
@click.argument("query")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--page", "-p", default=0, show_default=True)
@click.option("--points", type=int, default=None)
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def stories(ctx, query, limit, page, points, json_output, markdown):
    """Search stories only."""
    client = ctx.obj["client"]
    numeric = _build_numeric_filters(points, None)
    result = client.search_stories(query, page=page, hits_per_page=limit, numeric_filters=numeric)
    if _error_exit(result):
        return
    _output(result, f"Stories: {query}", json_output, markdown)


@main.command(name="comments")
@click.argument("query")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--page", "-p", default=0, show_default=True)
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def comments_cmd(ctx, query, limit, page, json_output, markdown):
    """Search comments only."""
    client = ctx.obj["client"]
    result = client.search_comments(query, page=page, hits_per_page=limit)
    if _error_exit(result):
        return
    if json_output:
        click.echo(json.dumps(result, indent=2))
    elif markdown:
        _render_comments_markdown(result, f"Comments: {query}")
    else:
        _render_comments(result, f"Comments: {query}")


@main.command(name="show-hn")
@click.argument("query", default="")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--page", "-p", default=0, show_default=True)
@click.option("--points", type=int, default=None)
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def show_hn(ctx, query, limit, page, points, json_output, markdown):
    """Search Show HN posts."""
    client = ctx.obj["client"]
    numeric = _build_numeric_filters(points, None)
    result = client.search_show_hn(query, page=page, hits_per_page=limit, numeric_filters=numeric)
    if _error_exit(result):
        return
    _output(result, f"Show HN: {query or 'all'}", json_output, markdown)


@main.command(name="ask-hn")
@click.argument("query", default="")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--page", "-p", default=0, show_default=True)
@click.option("--points", type=int, default=None)
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def ask_hn(ctx, query, limit, page, points, json_output, markdown):
    """Search Ask HN posts."""
    client = ctx.obj["client"]
    numeric = _build_numeric_filters(points, None)
    result = client.search_ask_hn(query, page=page, hits_per_page=limit, numeric_filters=numeric)
    if _error_exit(result):
        return
    _output(result, f"Ask HN: {query or 'all'}", json_output, markdown)


@main.command()
@click.argument("query", default="")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--page", "-p", default=0, show_default=True)
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def jobs(ctx, query, limit, page, json_output, markdown):
    """Search job posts."""
    client = ctx.obj["client"]
    result = client.search_jobs(query, page=page, hits_per_page=limit)
    if _error_exit(result):
        return
    _output(result, f"Jobs: {query or 'all'}", json_output, markdown)


@main.command()
@click.argument("username")
@click.option("--query", "-q", default="", help="Optional search within author's posts")
@click.option("--type", "post_type", type=click.Choice(["story", "comment"]), default=None,
              help="Filter by post type")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--page", "-p", default=0, show_default=True)
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def author(ctx, username, query, post_type, limit, page, json_output, markdown):
    """Search posts by a specific author."""
    client = ctx.obj["client"]
    result = client.search_by_author(username, query=query, tags=post_type,
                                     page=page, hits_per_page=limit)
    if _error_exit(result):
        return
    _output(result, f"Author: {username}", json_output, markdown)


@main.command(name="date-range")
@click.argument("query")
@click.option("--from", "date_from", required=True, help="Start date YYYY-MM-DD")
@click.option("--to", "date_to", required=True, help="End date YYYY-MM-DD")
@click.option("--tags", "-t", default=None, help="Tag filters")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--page", "-p", default=0, show_default=True)
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def date_range(ctx, query, date_from, date_to, tags, limit, page, json_output, markdown):
    """Search within a date range (YYYY-MM-DD)."""
    client = ctx.obj["client"]
    result = client.search_date_range(query, date_from, date_to, tags=tags,
                                      page=page, hits_per_page=limit)
    if _error_exit(result):
        return
    _output(result, f"Date range: {query} ({date_from} to {date_to})", json_output, markdown)


@main.command()
@click.argument("query", default="")
@click.option("--min-points", type=int, default=100, show_default=True)
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--page", "-p", default=0, show_default=True)
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def popular(ctx, query, min_points, limit, page, json_output, markdown):
    """Find popular stories above a point threshold."""
    client = ctx.obj["client"]
    result = client.popular_stories(query, min_points=min_points, page=page, hits_per_page=limit)
    if _error_exit(result):
        return
    _output(result, f"Popular (>{min_points} pts): {query or 'all'}", json_output, markdown)


@main.command(name="hot")
@click.argument("query", default="")
@click.option("--min-comments", type=int, default=100, show_default=True)
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--page", "-p", default=0, show_default=True)
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def hot(ctx, query, min_comments, limit, page, json_output, markdown):
    """Find stories with heavy discussion (100+ comments by default)."""
    client = ctx.obj["client"]
    result = client.hot_discussions(query, min_comments=min_comments, page=page, hits_per_page=limit)
    if _error_exit(result):
        return
    _output(result, f"Hot discussions (>{min_comments} comments): {query or 'all'}", json_output, markdown)


@main.command(name="thread")
@click.argument("story_id")
@click.option("--query", "-q", default="", help="Search within thread comments")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--page", "-p", default=0, show_default=True)
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def thread(ctx, story_id, query, limit, page, json_output, markdown):
    """Search comments within a specific story thread."""
    client = ctx.obj["client"]
    result = client.search_story_comments(story_id, query=query, page=page, hits_per_page=limit)
    if _error_exit(result):
        return
    if json_output:
        click.echo(json.dumps(result, indent=2))
    elif markdown:
        _render_comments_markdown(result, f"Thread #{story_id}: {query or 'all comments'}")
    else:
        _render_comments(result, f"Thread #{story_id}: {query or 'all comments'}")


@main.command()
@click.argument("item_id")
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def item(ctx, item_id, json_output, markdown):
    """Get full item details with comment tree."""
    client = ctx.obj["client"]
    result = client.get_item(item_id)
    if _error_exit(result):
        return
    if json_output:
        click.echo(json.dumps(result, indent=2))
    elif markdown:
        _render_item_detail_markdown(result)
    else:
        _render_item_detail(result)


@main.command()
@click.argument("username")
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def user(ctx, username, json_output, markdown):
    """Get user profile."""
    client = ctx.obj["client"]
    result = client.get_user(username)
    if _error_exit(result):
        return
    if json_output:
        click.echo(json.dumps(result, indent=2))
    elif markdown:
        _render_user_markdown(result)
    else:
        _render_user(result)


@main.command(name="front-page")
@click.argument("query", default="")
@click.option("--limit", "-n", default=20, show_default=True)
@click.option("--page", "-p", default=0, show_default=True)
@click.option("--json-output", "-j", is_flag=True)
@click.option("--markdown", "-m", is_flag=True)
@click.pass_context
def front_page(ctx, query, limit, page, json_output, markdown):
    """Search items that reached the HN front page."""
    client = ctx.obj["client"]
    result = client.search_front_page(query, page=page, hits_per_page=limit)
    if _error_exit(result):
        return
    _output(result, f"Front Page: {query or 'all'}", json_output, markdown)


@main.command(name="clear-cache")
@click.pass_context
def clear_cache(ctx):
    """Clear local response cache."""
    client = ctx.obj["client"]
    if not client.cache:
        console.print("[yellow]Cache is disabled for this run (--no-cache).[/yellow]")
        return
    removed = client.cache.clear()
    console.print(f"[green]Cleared {removed} cached response files[/green]")


# ── Helpers ───────────────────────────────────────────────


def _build_numeric_filters(points: int = None, comments: int = None) -> str:
    """Build numericFilters string from point/comment thresholds."""
    parts = []
    if points is not None:
        parts.append(f"points>{points}")
    if comments is not None:
        parts.append(f"num_comments>{comments}")
    return ",".join(parts) if parts else None


def _output(result: dict, title: str, json_output: bool, markdown: bool) -> None:
    """Route output to the correct renderer."""
    if json_output:
        click.echo(json.dumps(result, indent=2))
    elif markdown:
        _render_stories_markdown(result, title)
    else:
        _render_stories(result, title)


if __name__ == "__main__":
    main()
