-- Cache LLM qualification/extraction work and add sentiment rationale metadata.

CREATE TABLE post_qualifications (
    post_id text NOT NULL REFERENCES posts (id),
    prompt_version text NOT NULL,
    qualified boolean NOT NULL,
    reason text NOT NULL DEFAULT '',
    model text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (post_id, prompt_version)
);

CREATE INDEX ix_post_qualifications_qualified
    ON post_qualifications (qualified);

CREATE TABLE post_ticker_extractions (
    post_id text NOT NULL REFERENCES posts (id),
    prompt_version text NOT NULL,
    model text NOT NULL,
    raw_tickers jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (post_id, prompt_version)
);

ALTER TABLE post_tickers
    ADD COLUMN IF NOT EXISTS qualification_prompt_version text,
    ADD COLUMN IF NOT EXISTS extraction_prompt_version text,
    ADD COLUMN IF NOT EXISTS qualification_reason text,
    ADD COLUMN IF NOT EXISTS extracted_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE sentiments
    ADD COLUMN IF NOT EXISTS rationale text;

ALTER TABLE sentiments
    DROP CONSTRAINT IF EXISTS uq_sentiment_post_ticker_prompt;

ALTER TABLE sentiments
    ADD CONSTRAINT uq_sentiment_post_ticker_model_prompt
    UNIQUE (post_id, ticker, model, prompt_version);
