# Strategy Methodology

This project is a research tool for testing whether X chatter about public companies has measurable
forward-return value. It is not financial advice.

## Signal Inputs

Only posts that pass LLM qualification enter ticker extraction and sentiment scoring. A qualified post is
one that discusses a public company, ticker, market-moving event, trading setup, or company-specific
information that could plausibly be trading intelligence.

Current built-in signals:

- `positive_high`: sentiment score `>= 0.6` and ticker confidence `>= 0.6`
- `negative_high`: sentiment score `<= -0.6` and ticker confidence `>= 0.6`

## Return Calculation

The backtest uses Schwab daily closes from the `prices` table.

- Entry: next available trading-day close strictly after the post date.
- Exit: close `N` trading days after entry.
- Raw return: `(exit_close - entry_close) / entry_close`
- Directional return: raw return for bullish signals; negated raw return for bearish signals.

Multiple matching posts for the same ticker and entry date are deduped to the earliest matching post.
This prevents a single viral topic from dominating the sample.

## Interpreting Results

Backtest runs are saved to `backtest_runs` with aggregate metrics:

- sample count
- missing price candidates
- duplicate candidates
- average and median raw return
- average and median directional return
- win rate
- volatility
- simple Sharpe-style score
- tiny-sample flag

Treat tiny samples as directional diagnostics only. They are useful for debugging the pipeline, not for
making trading decisions.

## Bias And Limitations

- Daily OHLCV data cannot model intraday entry timing.
- Backtests do not include transaction costs, spreads, slippage, or borrow costs.
- X posts can be noisy, duplicated, sarcastic, promotional, or wrong.
- LLM qualification and sentiment are cached, auditable approximations, not ground truth.
- Backtested results do not guarantee future performance.
