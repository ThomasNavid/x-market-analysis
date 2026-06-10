"""LLM sentiment scoring for qualified post/ticker pairs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from findb.config import settings
from findb.core.db.connection import connect
from findb.core.llm import create_json_completion

SENTIMENT_SYSTEM = """You score stock-specific sentiment for a trading research pipeline.
Return JSON only.

Score the sentiment of the post toward the named ticker's future stock performance,
not general emotional tone. Positive means the post implies potentially bullish
stock impact. Negative means potentially bearish stock impact. Neutral means mixed,
unclear, stale, factual without directional implication, or not actually about the
ticker's stock prospects.

Use a score from -1.0 to 1.0:
-1.0 is strongly bearish, 0.0 is neutral/unclear, 1.0 is strongly bullish.

Schema:
{"label": "positive|negative|neutral", "score": -1.0-1.0, "rationale": "short reason"}
"""

VALID_LABELS = {"positive", "negative", "neutral"}


@dataclass(frozen=True)
class PostTickerForSentiment:
    """One qualified post/ticker pair needing sentiment."""

    post_id: str
    ticker: str
    text: str
    created_at: datetime


@dataclass(frozen=True)
class SentimentScore:
    """Structured sentiment result for one post/ticker pair."""

    post_id: str
    ticker: str
    label: str
    score: float
    rationale: str
    model: str
    prompt_version: str


@dataclass(frozen=True)
class SentimentEnrichmentResult:
    """Summary of sentiment enrichment work."""

    checked: int
    scored: int


def _score(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(-1.0, min(1.0, numeric))


def _label(value: Any, score: float) -> str:
    label = str(value or "").strip().lower()
    if label in VALID_LABELS:
        return label
    if score > 0.15:
        return "positive"
    if score < -0.15:
        return "negative"
    return "neutral"


def fetch_post_tickers_needing_sentiment(*, limit: int) -> list[PostTickerForSentiment]:
    """Return qualified post/ticker rows without cached sentiment for this prompt/model."""
    sql = """
        SELECT p.id, pt.ticker, p.text, p.created_at
        FROM post_tickers pt
        JOIN posts p
            ON p.id = pt.post_id
        LEFT JOIN sentiments s
            ON s.post_id = pt.post_id
            AND s.ticker = pt.ticker
            AND s.model = %s
            AND s.prompt_version = %s
        WHERE s.id IS NULL
        ORDER BY p.created_at DESC
        LIMIT %s
    """
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (settings.sentiment_model, settings.sentiment_prompt_version, limit))
            return [
                PostTickerForSentiment(
                    post_id=row[0],
                    ticker=row[1],
                    text=row[2],
                    created_at=row[3],
                )
                for row in cur.fetchall()
            ]


def _sentiment_prompt(item: PostTickerForSentiment) -> str:
    return (
        f"Post id: {item.post_id}\n"
        f"Ticker: {item.ticker}\n"
        f"Created at: {item.created_at.isoformat()}\n"
        f"Text:\n{item.text}"
    )


def score_sentiment_for_post_ticker(item: PostTickerForSentiment) -> SentimentScore:
    """Ask the configured LLM for stock-directional sentiment for one post/ticker pair."""
    payload = create_json_completion(
        settings.sentiment_model,
        system=SENTIMENT_SYSTEM,
        user=_sentiment_prompt(item),
        max_tokens=400,
    )
    score = _score(payload.get("score"))
    return SentimentScore(
        post_id=item.post_id,
        ticker=item.ticker,
        label=_label(payload.get("label"), score),
        score=score,
        rationale=str(payload.get("rationale", ""))[:1000],
        model=settings.sentiment_model,
        prompt_version=settings.sentiment_prompt_version,
    )


def upsert_sentiments(sentiments: list[SentimentScore]) -> int:
    """Persist sentiment rows, keyed by post/ticker/model/prompt."""
    if not sentiments:
        return 0

    sql = """
        INSERT INTO sentiments (
            post_id, ticker, label, score, model, prompt_version, rationale
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (post_id, ticker, model, prompt_version)
        DO UPDATE SET
            label = EXCLUDED.label,
            score = EXCLUDED.score,
            rationale = EXCLUDED.rationale,
            created_at = now()
    """
    rows = [
        (
            item.post_id,
            item.ticker,
            item.label,
            item.score,
            item.model,
            item.prompt_version,
            item.rationale,
        )
        for item in sentiments
    ]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
    return len(sentiments)


def score_missing_sentiments(*, limit: int) -> SentimentEnrichmentResult:
    """Run Step 5 sentiment scoring for qualified post/ticker pairs."""
    if limit < 1:
        raise ValueError("limit must be at least 1")

    items = fetch_post_tickers_needing_sentiment(limit=limit)
    if not items:
        return SentimentEnrichmentResult(checked=0, scored=0)

    sentiments = [score_sentiment_for_post_ticker(item) for item in items]
    return SentimentEnrichmentResult(
        checked=len(items),
        scored=upsert_sentiments(sentiments),
    )
