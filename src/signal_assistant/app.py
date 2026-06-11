from __future__ import annotations

import logging
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Settings, load_settings
from .labels import label_signal_type
from .longbridge_client import LongbridgeMarketDataClient
from .llm_analyzer import LLMAnalyzer
from .notifier import SignalNotifier
from .signal_engine import SignalEngine
from .storage import SignalStorage


def _in_us_market_hours(settings: Settings) -> bool:
    now_local = datetime.now(ZoneInfo(settings.us_market_tz))
    if now_local.weekday() >= 5:
        return False
    current_t = now_local.time()
    return settings.market_open_time <= current_t <= settings.market_close_time


def _tier_value(tier: str) -> int:
    return {"A": 3, "B": 2, "C": 1}.get(tier, 0)


def _build_daily_report_message(report_day: datetime, summary: dict) -> str:
    total = int(summary["total_count"])
    avg_quality = summary["avg_quality"]
    by_type = summary["by_type"]
    top_symbols = summary["top_symbols"]

    lines = [
        f"每日信号报告 | {report_day.strftime('%Y-%m-%d')} ({report_day.strftime('%Z')})",
        f"信号总数: {total}",
        f"平均质量: {avg_quality}/100",
    ]

    if by_type:
        lines.append("按类型:")
        for item in by_type:
            type_label = label_signal_type(item["signal_type"])
            lines.append(f"- {type_label}: {item['count']}")

    if top_symbols:
        lines.append("活跃标的:")
        for item in top_symbols:
            lines.append(
                f"- {item['symbol']}: {item['count']} 次信号, 平均质量 {item['avg_quality']}"
            )

    lines.append("仅供参考，不构成投资建议。")
    return "\n".join(lines)


def _build_weekly_report_message(report_day: datetime, summary: dict) -> str:
    lines = [
        f"每周信号报告 | 截至 {report_day.strftime('%Y-%m-%d')} ({report_day.strftime('%Z')})",
        f"信号总数: {summary['total_count']}",
        f"平均质量: {summary['avg_quality']}/100",
    ]

    by_tier = summary.get("by_tier", [])
    if by_tier:
        lines.append("优先级分布:")
        for item in by_tier:
            lines.append(f"- {item['tier']}级: {item['count']}")

    top_symbols = summary.get("top_symbols", [])
    if top_symbols:
        lines.append("活跃标的:")
        for item in top_symbols:
            lines.append(
                f"- {item['symbol']}: {item['count']} 次信号, 平均质量 {item['avg_quality']}"
            )

    lines.append("仅供参考，不构成投资建议。")
    return "\n".join(lines)


def run() -> None:
    settings = load_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logger = logging.getLogger("signal_assistant")

    storage = SignalStorage(settings.sqlite_path)
    market_data_client = LongbridgeMarketDataClient(settings)
    notifier = SignalNotifier(settings)
    llm_analyzer = LLMAnalyzer(settings)
    signal_engine = SignalEngine(settings)
    subscribed_symbols = sorted(set(settings.watchlist + [settings.relative_strength_benchmark]))
    last_daily_report_date = None
    last_weekly_report_anchor = None

    try:
        market_data_client.subscribe_watchlist(subscribed_symbols, settings.all_timeframes)
        logger.info("Service started for watchlist: %s", settings.watchlist)

        while True:
            if settings.trading_hours_only and not _in_us_market_hours(settings):
                logger.info("Outside configured US market hours. Sleeping...")
                time.sleep(settings.poll_interval_seconds)
                continue

            benchmark_bars_by_timeframe = {}
            if settings.enable_relative_strength_filter:
                try:
                    benchmark_bars_by_timeframe = {
                        timeframe: market_data_client.get_kline(
                            settings.relative_strength_benchmark, timeframe, max_count=300
                        )
                        for timeframe in settings.all_timeframes
                    }
                except Exception as exc:
                    logger.exception(
                        "Failed benchmark data %s: %s",
                        settings.relative_strength_benchmark,
                        exc,
                    )
                    time.sleep(settings.poll_interval_seconds)
                    continue

            for symbol in settings.watchlist:
                try:
                    bars_by_timeframe = {
                        timeframe: market_data_client.get_kline(symbol, timeframe, max_count=300)
                        for timeframe in settings.all_timeframes
                    }
                    signal = signal_engine.evaluate(
                        symbol,
                        bars_by_timeframe,
                        benchmark_bars_by_timeframe if settings.enable_relative_strength_filter else None,
                    )
                    if signal is None:
                        continue

                    should_suppress = storage.should_suppress(
                        symbol=symbol,
                        signal_type=signal.signal_type,
                        latest_price=signal.price,
                        cooldown_minutes=settings.alert_cooldown_minutes,
                        duplicate_price_tolerance_pct=settings.duplicate_price_tolerance_pct,
                    )
                    if should_suppress:
                        logger.info("Suppressed duplicate/cooldown signal: %s %s", symbol, signal.signal_type)
                        continue

                    if llm_analyzer.enabled and signal.signal_type in settings.llm_signal_types:
                        llm_result, llm_error = llm_analyzer.analyze(signal)
                        if llm_result:
                            signal = replace(signal, llm_analysis=llm_result)
                        elif llm_error:
                            logger.warning("LLM analysis unavailable for %s: %s", symbol, llm_error)

                    merged_recent = None
                    if settings.enable_symbol_merge_alerts:
                        merged_recent = storage.get_recent_symbol_signal(
                            symbol=symbol,
                            lookback_minutes=settings.symbol_merge_window_minutes,
                        )

                    storage.record_signal(signal)
                    if _tier_value(signal.priority_tier) < _tier_value(settings.alert_min_priority_tier):
                        logger.info(
                            "Filtered by tier: %s %s | tier=%s < min=%s",
                            symbol,
                            signal.signal_type,
                            signal.priority_tier,
                            settings.alert_min_priority_tier,
                        )
                        storage.record_notification(
                            signal,
                            status="filtered_by_tier",
                            error_message=(
                                f"tier {signal.priority_tier} below min {settings.alert_min_priority_tier}"
                            ),
                        )
                        continue

                    if settings.dry_run:
                        logger.info(
                            "DRY_RUN signal generated: %s %s | quality=%s",
                            symbol,
                            signal.signal_type,
                            signal.quality_score,
                        )
                        storage.record_notification(signal, status="dry_run", error_message=None)
                    else:
                        if (
                            merged_recent is not None
                            and merged_recent["signal_type"] != signal.signal_type
                        ):
                            merged_message = (
                                f"{signal.symbol} | 信号更新 | {signal.timeframe}\n"
                                f"最新: {label_signal_type(signal.signal_type)} "
                                f"({signal.priority_tier}级, 质量 {signal.quality_score})\n"
                                f"此前: {label_signal_type(merged_recent['signal_type'])} "
                                f"({merged_recent['priority_tier']}级, 质量 {merged_recent['quality_score']})\n"
                                f"当前价格: {signal.price}\n"
                                f"说明: {signal.explanation}\n"
                                f"仅供参考，不构成投资建议。"
                            )
                            ok, err = notifier.send_text(merged_message)
                            status = "merged_sent" if ok else "merged_failed"
                        else:
                            ok, err = notifier.send(signal)
                            status = "sent" if ok else "failed"

                        storage.record_notification(signal, status=status, error_message=err)
                        if ok:
                            logger.info("Signal sent: %s %s", symbol, signal.signal_type)
                        else:
                            logger.warning("Signal send failed: %s %s | %s", symbol, signal.signal_type, err)
                except Exception as exc:
                    logger.exception("Failed processing %s: %s", symbol, exc)

            if settings.enable_daily_report:
                now_local = datetime.now(ZoneInfo(settings.us_market_tz))
                today_local = now_local.date()
                if (
                    now_local.weekday() < 5
                    and now_local.time() >= settings.daily_report_send_time
                    and last_daily_report_date != today_local
                ):
                    day_start_local = datetime.combine(
                        today_local,
                        settings.market_open_time,
                        tzinfo=ZoneInfo(settings.us_market_tz),
                    )
                    day_end_local = datetime.combine(
                        today_local,
                        settings.market_close_time,
                        tzinfo=ZoneInfo(settings.us_market_tz),
                    )
                    start_utc_iso = day_start_local.astimezone(timezone.utc).isoformat()
                    end_utc_iso = (day_end_local + timedelta(minutes=90)).astimezone(
                        timezone.utc
                    ).isoformat()
                    summary = storage.get_daily_signal_summary(
                        start_utc_iso=start_utc_iso,
                        end_utc_iso=end_utc_iso,
                        top_n=settings.daily_report_top_n,
                    )
                    message = _build_daily_report_message(now_local, summary)
                    if settings.dry_run:
                        logger.info("DRY_RUN daily report:\n%s", message)
                    else:
                        ok, err = notifier.send_text(message)
                        if ok:
                            logger.info("Daily report sent for %s", today_local)
                        else:
                            logger.warning("Daily report send failed: %s", err)
                    last_daily_report_date = today_local

            if settings.enable_weekly_report:
                now_local = datetime.now(ZoneInfo(settings.us_market_tz))
                if (
                    now_local.weekday() == settings.weekly_report_send_weekday
                    and now_local.time() >= settings.weekly_report_send_time
                ):
                    week_anchor = now_local.date() - timedelta(days=now_local.weekday())
                    if last_weekly_report_anchor != week_anchor:
                        week_start_local = datetime.combine(
                            week_anchor,
                            settings.market_open_time,
                            tzinfo=ZoneInfo(settings.us_market_tz),
                        )
                        week_end_local = datetime.combine(
                            now_local.date(),
                            settings.market_close_time,
                            tzinfo=ZoneInfo(settings.us_market_tz),
                        )
                        weekly_summary = storage.get_weekly_signal_summary(
                            start_utc_iso=week_start_local.astimezone(timezone.utc).isoformat(),
                            end_utc_iso=(week_end_local + timedelta(minutes=120))
                            .astimezone(timezone.utc)
                            .isoformat(),
                            top_n=settings.weekly_report_top_n,
                        )
                        weekly_message = _build_weekly_report_message(now_local, weekly_summary)
                        if settings.dry_run:
                            logger.info("DRY_RUN weekly report:\n%s", weekly_message)
                        else:
                            ok, err = notifier.send_text(weekly_message)
                            if ok:
                                logger.info("Weekly report sent for week %s", week_anchor)
                            else:
                                logger.warning("Weekly report send failed: %s", err)
                        last_weekly_report_anchor = week_anchor

            time.sleep(settings.poll_interval_seconds)
    except KeyboardInterrupt:
        logger.info("Stopped by user.")
    finally:
        market_data_client.close()
        storage.close()


if __name__ == "__main__":
    run()
