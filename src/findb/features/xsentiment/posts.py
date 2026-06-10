"""Ingest recent X posts and authors into PostgreSQL."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from findb.core.db.connection import connect
from findb.features.xsentiment.x_client import (
    build_cashtag_query,
    create_x_bearer_client,
    create_x_user_client,
)


@dataclass(frozen=True)
class AuthorRecord:
    """One X author row."""

    id: str
    handle: str
    followers: int
    verified: bool
    account_tier: str | None


@dataclass(frozen=True)
class PostRecord:
    """One X post row."""

    id: str
    author_id: str
    text: str
    created_at: datetime
    like_count: int
    repost_count: int
    reply_count: int
    lang: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class PostIngestResult:
    """Summary of one ingest run."""

    query: str
    authors_seen: int
    posts_seen: int
    pages_fetched: int


def _parse_x_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _author_from_user(user: dict[str, Any]) -> AuthorRecord:
    metrics = user.get("public_metrics", {})
    return AuthorRecord(
        id=user["id"],
        handle=user.get("username", user["id"]),
        followers=int(metrics.get("followers_count", 0)),
        verified=bool(user.get("verified", False)),
        account_tier=None,
    )


def _post_from_tweet(tweet: dict[str, Any]) -> PostRecord:
    metrics = tweet.get("public_metrics", {})
    return PostRecord(
        id=tweet["id"],
        author_id=tweet["author_id"],
        text=tweet["text"],
        created_at=_parse_x_datetime(tweet["created_at"]),
        like_count=int(metrics.get("like_count", 0)),
        repost_count=int(metrics.get("retweet_count", metrics.get("repost_count", 0))),
        reply_count=int(metrics.get("reply_count", 0)),
        lang=tweet.get("lang"),
        raw=tweet,
    )


def records_from_search_payload(
    payload: dict[str, Any],
) -> tuple[list[AuthorRecord], list[PostRecord]]:
    """Convert one X API search page into database records."""
    users = payload.get("includes", {}).get("users", [])
    authors_by_id = {_author_from_user(user).id: _author_from_user(user) for user in users}

    posts = [_post_from_tweet(tweet) for tweet in payload.get("data", [])]
    for post in posts:
        if post.author_id not in authors_by_id:
            authors_by_id[post.author_id] = AuthorRecord(
                id=post.author_id,
                handle=post.author_id,
                followers=0,
                verified=False,
                account_tier=None,
            )

    return list(authors_by_id.values()), posts


def upsert_authors(authors: list[AuthorRecord]) -> int:
    """Insert or update X authors by id."""
    if not authors:
        return 0

    sql = """
        INSERT INTO authors (
            id, handle, followers, verified, account_tier
        )
        VALUES (
            %s, %s, %s, %s, %s
        )
        ON CONFLICT (id)
        DO UPDATE SET
            handle = EXCLUDED.handle,
            followers = EXCLUDED.followers,
            verified = EXCLUDED.verified,
            account_tier = EXCLUDED.account_tier
    """
    rows = [(a.id, a.handle, a.followers, a.verified, a.account_tier) for a in authors]

    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()

    return len(authors)


def upsert_posts(posts: list[PostRecord]) -> int:
    """Insert or update X posts by id."""
    if not posts:
        return 0

    sql = """
        INSERT INTO posts (
            id, author_id, text, created_at, like_count, repost_count,
            reply_count, lang, raw
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (id)
        DO UPDATE SET
            author_id = EXCLUDED.author_id,
            text = EXCLUDED.text,
            created_at = EXCLUDED.created_at,
            like_count = EXCLUDED.like_count,
            repost_count = EXCLUDED.repost_count,
            reply_count = EXCLUDED.reply_count,
            lang = EXCLUDED.lang,
            raw = EXCLUDED.raw
    """
    rows = [
        (
            p.id,
            p.author_id,
            p.text,
            p.created_at,
            p.like_count,
            p.repost_count,
            p.reply_count,
            p.lang,
            Jsonb(p.raw),
        )
        for p in posts
    ]

    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()

    return len(posts)


def ingest_recent_posts(
    *,
    tickers: list[str],
    query: str | None,
    max_posts: int,
    page_size: int,
) -> PostIngestResult:
    """Search recent X posts and persist authors/posts."""
    if max_posts < 1:
        raise ValueError("max_posts must be at least 1")

    effective_query = query or build_cashtag_query(tickers)
    effective_page_size = max(10, min(page_size, 100))
    client = create_x_bearer_client()

    authors_seen = 0
    posts_seen = 0
    pages_fetched = 0
    next_token: str | None = None

    try:
        while posts_seen < max_posts:
            remaining = max_posts - posts_seen
            payload = client.search_recent_posts(
                effective_query,
                max_results=max(10, min(effective_page_size, remaining)),
                next_token=next_token,
            )
            pages_fetched += 1

            authors, posts = records_from_search_payload(payload)
            posts = posts[: max_posts - posts_seen]
            upsert_authors(authors)
            upsert_posts(posts)

            authors_seen += len(authors)
            posts_seen += len(posts)

            next_token = payload.get("meta", {}).get("next_token")
            if not next_token or not posts:
                break
    finally:
        client.close()

    return PostIngestResult(
        query=effective_query,
        authors_seen=authors_seen,
        posts_seen=posts_seen,
        pages_fetched=pages_fetched,
    )


def ingest_home_timeline_posts(
    *,
    max_posts: int,
    page_size: int,
    exclude_replies: bool,
    exclude_retweets: bool,
) -> PostIngestResult:
    """Fetch the authenticated user's following feed and persist authors/posts.

    X's API names this the reverse-chronological home timeline; it returns posts
    from the authenticated user and accounts they follow, without algorithmic ranking.
    """
    if max_posts < 1:
        raise ValueError("max_posts must be at least 1")

    effective_page_size = max(1, min(page_size, 100))
    client = create_x_user_client()

    authors_seen = 0
    posts_seen = 0
    pages_fetched = 0
    pagination_token: str | None = None
    exclude = []
    if exclude_replies:
        exclude.append("replies")
    if exclude_retweets:
        exclude.append("retweets")

    try:
        me = client.get_me()
        user = me.get("data", {})
        user_id = user["id"]

        while posts_seen < max_posts:
            remaining = max_posts - posts_seen
            payload = client.get_home_timeline(
                user_id,
                max_results=min(effective_page_size, remaining),
                pagination_token=pagination_token,
                exclude=exclude or None,
            )
            pages_fetched += 1

            authors, posts = records_from_search_payload(payload)
            posts = posts[: max_posts - posts_seen]
            upsert_authors(authors)
            upsert_posts(posts)

            authors_seen += len(authors)
            posts_seen += len(posts)

            pagination_token = payload.get("meta", {}).get("next_token")
            if not pagination_token or not posts:
                break
    finally:
        client.close()

    return PostIngestResult(
        query="following_feed",
        authors_seen=authors_seen,
        posts_seen=posts_seen,
        pages_fetched=pages_fetched,
    )
