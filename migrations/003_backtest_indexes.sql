-- Indexes for Step 6 backtest filtering as enrichment volume grows.

CREATE INDEX IF NOT EXISTS ix_sentiments_model_prompt_score
    ON sentiments (model, prompt_version, score);

CREATE INDEX IF NOT EXISTS ix_post_tickers_confidence_post_ticker
    ON post_tickers (confidence, post_id, ticker);
