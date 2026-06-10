# US/HK Signal Robot Rulebook (Discussion Draft)

Date: 2026-06-09  
Scope: Rule-based technical signal engine for discretionary trading support (no auto-order execution)

## 1) Objective and Operating Mode

- Purpose: generate structured trade-reference signals, then push reminders to Feishu/Telegram.
- Role boundary: decision support only; final execution is manual by trader.
- Runtime mode now:
  - Background monitor loop: `python run.py`
  - On-demand Q&A helper: `python ask.py price SYMBOL` / `python ask.py advice SYMBOL`

## 2) Current Live Universe and Timeframes

- Watchlist (active):
  - HK.07709
  - US.SPY, US.QQQ, US.MU, US.SNDK, US.GLW, US.IREN, US.TSLA, US.MSFT, US.NVDA
- Multi-timeframe stack:
  - Execution timeframe: 15m
  - Trend filter timeframe: 1h
  - Confirmation timeframes: 5m and 1m

## 3) Indicator Inputs (Per Symbol, Per Poll)

The engine computes:

- MA20, MA50
- RSI(14)
- Recent swing high / swing low (lookback 20 bars)
- Volume ratio = latest volume / 20-bar average volume
- ATR(14) and ATR% (ATR / close)

Minimum data safety requirement:

- At least 60 bars required for full indicator set.

## 4) Signal Taxonomy and Priority

Possible signal types:

- `exit_warning`
- `breakout_candidate`
- `long_setup`
- `observation_only`

Evaluation priority order (first match wins):

1. exit_warning
2. breakout_candidate
3. long_setup
4. observation_only

Quality scoring:

- Score mapped to evidence strength:
  - >=80: strong
  - >=60: medium
  - <60: weak
- Score mapped to priority tier:
  - A (>=80), B (>=60), C (<60)

## 5) Rule Definitions (Trigger Logic)

### 5.1 `long_setup` (bullish setup)

All conditions must pass:

- Price > MA20 and Price > MA50 on 15m
- MA20 > MA50 OR MA20 rising vs previous bar
- RSI recovers upward through threshold:
  - previous RSI < 48 <= current RSI
- 1h trend supportive:
  - trend price > trend MA20 and trend MA20 >= trend MA50
- Price not too close to swing resistance:
  - resistance distance > 0.8%
- Lower timeframe confirmation votes:
  - at least 1 of (5m, 1m) has price >= MA20 and RSI >= 50
- ATR filter pass (if enabled)
- Relative strength filter pass (if enabled)

Output fields include:

- Suggested entry zone around current price (+/-0.4%)
- Invalidation = min(MA20, swing low)
- Nearest resistance = swing high

### 5.2 `breakout_candidate` (breakout watch/entry candidate)

All conditions must pass:

- Price > recent swing high (breakout level)
- Volume ratio >= 1.2
- 1h trend not weak (trend price >= trend MA20)
- Not over-extended:
  - extension <= 3.0% from breakout level
- At least one lower timeframe keeps structure (price >= MA20)
- ATR filter pass (if enabled)
- Relative strength filter pass (if enabled)
- Breakout structure pass (if enabled):
  - fresh break (prev close <= breakout < now), OR
  - controlled retest-hold with tolerance 0.4%

Output fields include:

- Entry zone from breakout level to current price
- Invalidation near MA20
- Dynamic resistance = current price * (1 + 3.0%)

### 5.3 `exit_warning` (risk control warning)

Triggered when ANY condition is true:

- Price loses MA20 and RSI is still weakening
- RSI below exit threshold (42)
- Price breaks below recent swing low

Risk level is marked high by design.

### 5.4 `observation_only` (no actionable trigger, monitor only)

Triggered when:

- Market is improving (price > MA20 OR RSI rising), AND
- Higher timeframe not clearly broken (trend price >= trend MA50)

Interpretation: conditions are not fully confirmed; avoid rushing entries.

## 6) Filters and Suppression Layers

### 6.1 ATR filter

- Enabled: true
- Pass band: 0.3% <= ATR% <= 5.0%

### 6.2 Relative Strength filter

- Current setting: disabled (`ENABLE_RELATIVE_STRENGTH_FILTER=false`)
- If enabled, rule is:
  - symbol return over lookback >= benchmark return + excess threshold

### 6.3 Duplicate/cooldown suppression

- Cooldown window: 30 minutes for same symbol + same signal_type
- Price-change suppression:
  - if price change < 0.3% vs prior same-type signal, suppress repeat

### 6.4 Tier gating

- Minimum tier for sending: C (currently all tiers allowed)
- If raised to B/A, lower-tier signals are recorded but not pushed.

### 6.5 Symbol merge alerts

- Enabled: true
- Merge window: 20 minutes
- If a symbol flips signal type within window, system sends merged update
  instead of isolated duplicate-style messages.

## 7) Alert and Report Delivery Rules

Primary notifier:

- Channel: Feishu webhook
- Feishu keyword protection:
  - all outgoing text is prefixed with `[signal-assistant]` if missing
  - avoids keyword-check rejection errors

Message payload content:

- Symbol, timeframe, current price
- Entry zone, invalidation, resistance
- Risk level, ATR%
- Quality score + tier + evidence strength
- Rule explanation
- Optional LLM analysis block (when enabled)
- Disclaimer

Report reminders:

- Daily report: enabled, 16:10 (US market timezone), top 5 symbols
- Weekly report: enabled, weekday=4, 16:20, top 8 symbols

## 8) LLM Explanation Layer (Optional)

- Current state: disabled (`ENABLE_LLM_ANALYSIS=false`)
- When enabled:
  - only selected signal types use LLM (default long_setup, breakout_candidate)
  - LLM does not trigger trades; it explains existing rule-based signals.

## 9) Polling, Session Control, and Storage

- Poll interval: 60 seconds
- Trading-hours-only gate: false (engine runs cross-session currently)
- Database: `data/signals.db`
  - `signal_history`: all generated signals with payload JSON
  - `notification_logs`: send status/errors

## 10) Current Practical Interpretation (For Expert Review)

- The framework is currently conservative on new long entries and relatively sensitive to
  short-term weakness warnings (example: repeated SPY exit_warning under cooldown control).
- Practical knobs to discuss with your stock specialist:
  - RSI thresholds (entry recovery 48 / exit 42)
  - breakout volume threshold (1.2)
  - extension cap (3.0%)
  - cooldown and duplicate tolerance (30 min / 0.3%)
  - whether to lift minimum send tier from C to B
  - whether to enable relative strength filter in live usage

## 11) Compliance and Risk Statement

- This robot is not an execution system and does not place orders.
- All outputs are reference signals for discretionary decision support only.
- Not financial advice.

