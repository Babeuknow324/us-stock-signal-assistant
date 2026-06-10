# US Stock Signal Assistant (v1)

Python-based technical-analysis signal assistant for US equities, designed for discretionary trading support.

## v1 Scope

- Data source: **Longbridge OpenAPI SDK**
- Market: **US equities only**
- Timeframes: **1m, 5m, 15m, 1h**
  - Primary execution: `15m`
  - Trend filter: `1h`
  - Lower-timeframe confirmation: `5m`, `1m`
- Signal categories:
  - `long_setup`
  - `breakout_candidate`
  - `exit_warning`
  - `observation_only`
- Notification: Telegram alerts (human-readable, mobile-friendly)
- Optional AI explanation layer (LLM) for plain-language interpretation
- Storage: SQLite signal history + notification logs
- No live auto-trading in v1

## Project Structure

```text
us-stock-signal-assistant/
  src/signal_assistant/
    app.py
    config.py
    longbridge_client.py
    indicator_engine.py
    llm_analyzer.py
    signal_engine.py
    notifier.py
    storage.py
    __main__.py
  data/
  examples/
    alert_payloads.json
  .env.example
  requirements.txt
  run.py
  README.md
```

## Prerequisites

1. Python 3.10+ (recommended)
2. Longbridge OpenAPI credentials
3. Longbridge account quote permission for target markets
4. Telegram bot token + chat ID (optional but recommended)

## Longbridge Setup

Set these variables in `.env`:

- `LONGBRIDGE_APP_KEY`
- `LONGBRIDGE_APP_SECRET`
- `LONGBRIDGE_ACCESS_TOKEN`

## Quick Start

1. Open terminal in this project folder.
2. Create virtual environment:

```bash
python -m venv .venv
```

3. Activate environment:
   - PowerShell:

```bash
.venv\Scripts\Activate.ps1
```

4. Install dependencies:

```bash
pip install -r requirements.txt
```

5. Create env file:

```bash
copy .env.example .env
```

6. Edit `.env`:
   - Set Longbridge credentials (`LONGBRIDGE_APP_KEY`, `LONGBRIDGE_APP_SECRET`, `LONGBRIDGE_ACCESS_TOKEN`)
   - Set notifier channel (`NOTIFIER_CHANNEL=telegram|feishu`)
   - For Feishu: set `FEISHU_WEBHOOK_URL`
   - For Telegram: set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - Customize watchlist and thresholds

7. Run:

```bash
python run.py
```

## Q&A Mode (On-demand Ask)

You can keep `python run.py` running for automatic monitoring/alerts, and ask questions on demand in another terminal.

Examples:

```bash
python ask.py price US.NVDA
python ask.py advice US.NVDA
python ask.py advice HK.07709
```

What it returns:

- latest price
- current strategy signal (if any)
- action hint (`observe` / `long candidate` / `risk-control`)
- key levels (entry zone, invalidation, resistance)

## Interactive Feishu Q&A Bot (Second Bot)

You can run a second bot for chat-style Q&A while keeping signal push bot unchanged.

### What this second bot does

- receives Feishu text messages
- supports commands:
  - `价格 7709`
  - `建议 NVDA`
  - `解释 HK.07709`
  - `风控 TSLA`
- replies with live price/signal/risk/LLM explanation

### Environment variables

Add to `.env`:

- `FEISHU_QA_APP_ID`
- `FEISHU_QA_APP_SECRET`
- `FEISHU_QA_VERIFY_TOKEN` (optional but recommended)
- `FEISHU_QA_HOST` (default `0.0.0.0`)
- `FEISHU_QA_PORT` (default `8091`)

### Run

```bash
python feishu_qa_bot.py
```

### Feishu app callback

- create a Feishu self-built app (not webhook bot)
- enable event subscription for `im.message.receive_v1`
- set callback URL to:
  - `http://<your-host>:8091/feishu/events`
- set verify token in Feishu console and `.env` as `FEISHU_QA_VERIFY_TOKEN`

## Quick Profile Switch (US / MIX)

You can switch config profiles with one command:

```bash
powershell -ExecutionPolicy Bypass -File .\switch_env.ps1 -Profile us
```

or:

```bash
powershell -ExecutionPolicy Bypass -File .\switch_env.ps1 -Profile mix
```

Profiles:

- `.env.us.example`: US-only watchlist and US trading-hours mode
- `.env.mix.example`: HK + US watchlist for cross-session testing

## Configuration Notes

Key variables in `.env`:

- `WATCHLIST` (supports `US.SPY`/`HK.07709` or `SPY.US`/`7709.HK`; auto-normalized)
- `EXECUTION_TIMEFRAME` (default `15m`)
- `TREND_TIMEFRAME` (default `1h`)
- `CONFIRM_TIMEFRAMES` (default `5m,1m`)
- `ALERT_COOLDOWN_MINUTES`
- `DUPLICATE_PRICE_TOLERANCE_PCT`
- `TRADING_HOURS_ONLY` + market window configs
- `ENABLE_ATR_FILTER`, `ATR_PERIOD`, `ATR_MIN_PCT`, `ATR_MAX_PCT`
- `ENABLE_RELATIVE_STRENGTH_FILTER`, `RELATIVE_STRENGTH_BENCHMARK`, `RELATIVE_STRENGTH_LOOKBACK_BARS`
- `ENABLE_BREAKOUT_RETEST_FILTER`, `BREAKOUT_RETEST_TOLERANCE_PCT`
- `ENABLE_LLM_ANALYSIS` (default `false`)
- `LLM_PROVIDER` (currently `openai`)
- `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`
- `LLM_SIGNAL_TYPES` (default `long_setup,breakout_candidate`)
- `DRY_RUN` (default `true` in sample; generates/logs signals without Telegram push)
- `NOTIFIER_CHANNEL` (`telegram` or `feishu`)
- `FEISHU_WEBHOOK_URL` (required when using Feishu)
- `ENABLE_DAILY_REPORT`, `DAILY_REPORT_SEND_HHMM`, `DAILY_REPORT_TOP_N`
- `ALERT_MIN_PRIORITY_TIER` (`A`/`B`/`C`, default sample `B` to suppress noisy `C` alerts)
- `ENABLE_SYMBOL_MERGE_ALERTS`, `SYMBOL_MERGE_WINDOW_MINUTES`
- `ENABLE_WEEKLY_REPORT`, `WEEKLY_REPORT_SEND_WEEKDAY`, `WEEKLY_REPORT_SEND_HHMM`, `WEEKLY_REPORT_TOP_N`

## Signal Output Design

Every alert includes:

- symbol
- timeframe
- current price
- entry zone
- invalidation
- nearest resistance
- ATR%
- priority tier (`A` / `B` / `C`)
- risk level (`low` / `medium` / `high`)
- rule quality score and rule evidence strength
- plain-language explanation
- optional AI interpretation block (summary, confidence, evidence strength, invalid-if condition, next check)
- final disclaimer: `Reference only, not financial advice.`

## Optional LLM Layer

LLM is an **explanation layer**, not the trade trigger.

- Rule-based signal engine decides whether a signal is triggered.
- LLM turns structured signal data into easier language.
- LLM adds decision hygiene fields: evidence strength, failure condition, and next check.
- You can restrict LLM calls to selected signal categories via `LLM_SIGNAL_TYPES`.
- If LLM fails or is disabled, core signal flow still works.

## Quality Filters (v2)

- ATR filter: suppresses signals when volatility is too low (noise) or too high (unstable).
- Relative strength filter: compares symbol return vs benchmark (default `US.SPY`) over configurable bars.
- Breakout retest filter: breakout signals require fresh break or controlled retest-hold structure.

## Storage

SQLite path is configurable (`SQLITE_PATH`, default `data/signals.db`).

Tables:

- `signal_history`: stores structured signals and JSON payload
- `notification_logs`: stores send status / errors

Daily report summary includes:

- total signals of the day
- average rule quality score
- signal count by signal type
- top symbols by signal frequency and average quality

Weekly report summary includes:

- total weekly signal count
- average weekly quality score
- tier mix (`A/B/C`)
- top symbols by weekly activity and quality

## Portability (Move to Another Computer)

This project is intentionally self-contained.

To migrate:

1. Copy the **entire folder** `us-stock-signal-assistant` to the new machine.
2. Install Python on the new machine.
3. Recreate venv and run `pip install -r requirements.txt`.
4. Copy/update `.env`.
5. Fill Longbridge credentials in `.env`, then run `python run.py`.

No hardcoded absolute paths are required for runtime.

Optional one-click export on Windows PowerShell:

```bash
powershell -ExecutionPolicy Bypass -File .\export_portable.ps1
```

This creates a zip package next to your project folder, excluding local virtual env and local DB.

## Safety

- This tool is for reference only.
- It does not execute live orders in v1.
- It is not financial advice.

## Future Extensions

- Simulated trading support via Longbridge trade APIs (manual control first)
- Optional TradingView integration as alternate signal source
- Optional dashboard for signal history review
