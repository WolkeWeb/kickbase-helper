"""Background worker: collects Bundesliga data on a schedule (APScheduler, asyncio)."""

import asyncio
import logging
import signal
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.bot.mcp_server import serve_mcp
from app.bot.polling import poll_questions
from app.config import Settings, get_settings
from app.db.session import make_engine, make_sessionmaker
from app.db.stores import DbResponseCache, KeyValueStore
from app.kickbase.calendar import near_market_value_update
from app.kickbase.factory import build_kickbase_client
from app.kickbase.league import LeagueContext
from app.llm.claude_bridge import build_claude_bridge
from app.llm.config import load_ai_config
from app.llm.factory import build_llm
from app.log import configure_logging
from app.news.kicker import KickerFeed
from app.notify.events import load_notification_config
from app.notify.telegram import build_notifier
from app.openligadb.client import OpenLigaDbClient
from app.secret_files import log_secret_status
from app.worker.jobs import (
    JobContext,
    backfill_market_values,
    collect_injuries,
    collect_league_transfers,
    collect_lineup_predictions,
    collect_lineups,
    deadline_plan_reminder,
    deadline_warning,
    log_recommendation_snapshot,
    matchday_report,
    notify_own_offers,
    notify_squad,
    notify_watchlist,
    sync_players,
    sync_points,
    track_my_offers,
)
from app.worker.news_jobs import collect_news, daily_briefing, deadline_news
from app.worker.runner import JobRunner

logger = logging.getLogger("app.worker")


async def daily_players(runner: JobRunner) -> None:
    if await runner.run("sync_players", sync_players):
        await runner.run("notify_squad", notify_squad)
        await backfill(runner)


async def evening_players(runner: JobRunner) -> None:
    """Right after Kickbase's market value update (~22:00): new values and the notification
    the same evening instead of the next morning."""
    if await runner.run("sync_players", sync_players):
        await runner.run("notify_squad", notify_squad)


async def catch_up_players(runner: JobRunner) -> None:
    """Second chance later in the day if the morning run failed."""
    if not await runner.succeeded_today("sync_players"):
        await daily_players(runner)
        if not await runner.succeeded_today("log_recommendations"):
            await snapshot(runner)


async def players_synced_today(runner: JobRunner) -> bool:
    """For the start-up: sync now unless done today or market values are just being updated."""
    if await runner.succeeded_today("sync_players"):
        return True
    if near_market_value_update(runner.ctx.clock()):
        logger.info("Marktwerte werden gerade aktualisiert, Spieler-Abgleich folgt planmäßig")
        return False
    if not await runner.run("sync_players", sync_players):
        return False
    await runner.run("notify_squad", notify_squad)
    return True


async def points(runner: JobRunner) -> None:
    await runner.run("sync_points", sync_points)


async def lineups(runner: JobRunner) -> None:
    await runner.run("collect_lineups", collect_lineups)


async def lineup_predictions(runner: JobRunner) -> None:
    await runner.run("collect_lineup_predictions", collect_lineup_predictions)


async def injuries(runner: JobRunner) -> None:
    await runner.run("collect_injuries", collect_injuries)


async def warn_deadline(runner: JobRunner) -> None:
    await runner.run("deadline_warning", deadline_warning)


async def deadline_plan(runner: JobRunner) -> None:
    await runner.run("deadline_plan_reminder", deadline_plan_reminder)


async def check_watchlist(runner: JobRunner) -> None:
    await runner.run("notify_watchlist", notify_watchlist)


async def check_own_offers(runner: JobRunner) -> None:
    await runner.run("notify_own_offers", notify_own_offers)


async def send_matchday_report(runner: JobRunner) -> None:
    """Send (or just record) the per-matchday report after the points run."""
    await runner.run("matchday_report", matchday_report)


async def backfill(runner: JobRunner) -> None:
    """History for players with little of it: all players once, later only newcomers."""
    await runner.run("backfill_market_values", backfill_market_values)


async def transfers(runner: JobRunner) -> None:
    """Transfers first: they hold the winning prices for the evaluation of own bids."""
    if await runner.run("collect_league_transfers", collect_league_transfers):
        await runner.run("track_offers", track_my_offers)


async def snapshot(runner: JobRunner) -> None:
    """Only with today's market values: a snapshot of stale values would skew the track
    record. If the morning sync failed, the catch-up run takes the snapshot after its sync."""
    if not await runner.succeeded_today("sync_players"):
        logger.info("Tipp-Protokoll wartet auf den heutigen Spieler-Abgleich")
        return
    await runner.run("log_recommendations", log_recommendation_snapshot)


async def news(runner: JobRunner) -> None:
    await runner.run("collect_news", collect_news)


async def news_before_deadline(runner: JobRunner) -> None:
    await runner.run("deadline_news", deadline_news)


async def morning_news(runner: JobRunner) -> None:
    """News first, so the briefing already knows this morning's reports."""
    await news(runner)
    await runner.run("daily_briefing", daily_briefing)


async def purge_cache(runner: JobRunner) -> None:
    cache = DbResponseCache(runner.ctx.sessions)
    # Keep a week of expired entries: they are served as stale data during outages.
    removed = await cache.purge_expired(datetime.now(UTC) - timedelta(days=7))
    logger.info("Cache bereinigt: %d alte Einträge entfernt", removed)


async def startup(runner: JobRunner) -> None:
    """Fill gaps right after a (re)start instead of waiting for the next scheduled run.
    The quick jobs go first; the backfill can take several minutes on the first start."""
    interrupted = await runner.mark_interrupted_runs()
    if interrupted:
        logger.warning("%d unterbrochene Job-Läufe als abgebrochen markiert", interrupted)
    players_ok = await players_synced_today(runner)
    await points(runner)
    await lineups(runner)
    if not await runner.succeeded_today("collect_lineup_predictions"):
        await lineup_predictions(runner)
    if not await runner.succeeded_today("collect_injuries"):
        await injuries(runner)
    if not await runner.succeeded_today("collect_news"):
        await news(runner)
    if not await runner.succeeded_today("collect_league_transfers"):
        await transfers(runner)
    if players_ok and not await runner.succeeded_today("log_recommendations"):
        await snapshot(runner)
    if players_ok:
        await backfill(runner)


def _log_polling_end(task: asyncio.Task) -> None:
    """The question bot should run until shutdown; if it ends early, say why."""
    if task.cancelled():
        return
    if (exc := task.exception()) is not None:
        logger.error("Telegram-Fragen beendet", exc_info=exc)


def _log_startup_result(task: asyncio.Task) -> None:
    if not task.cancelled() and (exc := task.exception()) is not None:
        logger.error("Nachholen beim Start fehlgeschlagen", exc_info=exc)


def build_scheduler(runner: JobRunner, tz: str) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=tz)
    defaults = {"args": [runner], "coalesce": True, "max_instances": 1, "misfire_grace_time": 3600}
    # Market values change around 22:00; the morning run stores the value of the new day.
    scheduler.add_job(daily_players, "cron", hour=7, minute=0, id="sync_players", **defaults)
    scheduler.add_job(catch_up_players, "cron", hour=13, minute=0, id="catch_up", **defaults)
    scheduler.add_job(evening_players, "cron", hour=22, minute=45, id="evening", **defaults)
    # Points: in the evening after the last kick-off and again in the morning for corrections.
    scheduler.add_job(points, "cron", hour=7, minute=20, id="sync_points", **defaults)
    scheduler.add_job(points, "cron", hour=23, minute=45, id="sync_points_evening", **defaults)
    # Lineups: after the morning points run, and again after the evening one so the start quote
    # does not lag behind evening matches.
    scheduler.add_job(lineups, "cron", hour=7, minute=30, id="collect_lineups", **defaults)
    scheduler.add_job(lineups, "cron", hour=23, minute=50, id="collect_lineups_evening", **defaults)
    # Predicted elevens (LigaInsider): morning and afternoon.
    scheduler.add_job(
        lineup_predictions, "cron", hour=8, minute=45, id="collect_predictions", **defaults
    )
    scheduler.add_job(
        lineup_predictions,
        "cron",
        hour=17,
        minute=0,
        id="collect_predictions_afternoon",
        **defaults,
    )
    # Injuries/suspensions (LigaInsider): once a day is enough, after the prediction run.
    scheduler.add_job(injuries, "cron", hour=9, minute=0, id="collect_injuries", **defaults)
    # Deadline warning: every 30 min it checks whether the lineup deadline is within 2 h.
    scheduler.add_job(warn_deadline, "cron", minute="*/30", id="deadline_warning", **defaults)
    # Deadline-plan reminder: hourly during the day; the job bails out fast when the deadline
    # is more than `trading.close_hours` away or there is no open deficit.
    scheduler.add_job(
        deadline_plan, "cron", hour="7-23", minute=0, id="deadline_plan_reminder", **defaults
    )
    # Watchlist: same cadence; active hours only (the worker bails out fast when the watchlist is
    # empty, so the night runs are essentially free).
    scheduler.add_job(
        check_watchlist,
        "cron",
        hour="7-23",
        minute="*/30",
        id="notify_watchlist",
        **defaults,
    )
    # Own-offers: same cadence; the job is a single market fetch, so the night runs are cheap and
    # the active-hour window is mainly to keep the Telegram channel quiet overnight.
    scheduler.add_job(
        check_own_offers,
        "cron",
        hour="7-23",
        minute="*/30",
        id="notify_own_offers",
        **defaults,
    )
    # Per-matchday report: once after the evening points run (23:55) and once as a Nachholer
    # the next morning (07:35), so a brief Kickbase outage doesn't lose the message. The job
    # bails out fast when there is nothing new.
    scheduler.add_job(
        send_matchday_report, "cron", hour=23, minute=55, id="matchday_report", **defaults
    )
    scheduler.add_job(
        send_matchday_report,
        "cron",
        hour=7,
        minute=35,
        id="matchday_report_morning",
        **defaults,
    )
    # kicker news three times a day (gentle: one request each); the briefing after the first.
    scheduler.add_job(morning_news, "cron", hour=7, minute=40, id="morning_news", **defaults)
    scheduler.add_job(news, "cron", hour=12, minute=40, id="news_midday", **defaults)
    scheduler.add_job(news, "cron", hour=18, minute=40, id="news_evening", **defaults)
    # League transfers (bid recommendation): one request every two hours.
    scheduler.add_job(transfers, "cron", hour="*/2", minute=10, id="transfers", **defaults)
    # Reuse the cached market after the :30 deadline check; at most hourly feed near kickoff.
    scheduler.add_job(
        news_before_deadline, "cron", hour="7-23", minute=35, id="deadline_news", **defaults
    )

    # Track record: one snapshot a day after the morning sync, news and LigaInsider runs.
    scheduler.add_job(snapshot, "cron", hour=9, minute=15, id="log_recommendations", **defaults)
    scheduler.add_job(purge_cache, "cron", day_of_week="sun", hour=4, id="purge_cache", **defaults)
    return scheduler


def _log_mcp_end(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("Kickbase-Werkzeuge für Claude gestoppt: %r", task.exception())


def build_runner(settings: Settings) -> JobRunner:
    engine = make_engine(settings.database_url)
    sessions = make_sessionmaker(engine)
    client = build_kickbase_client(
        settings, sessions, serve_stale=False, min_interval=settings.worker_min_interval
    )
    kv = KeyValueStore(sessions)
    ctx = JobContext(
        client=client,
        sessions=sessions,
        league=LeagueContext(client, kv, settings.kickbase_league_name),
        kv=kv,
        tz=ZoneInfo(settings.tz),
        notifier=build_notifier(settings),
        notifications=load_notification_config(),
        public_url=settings.public_url,
        llm=build_llm(settings, sessions),
        news_feed=KickerFeed(),
        oldb=OpenLigaDbClient(DbResponseCache(sessions)),
        claude=build_claude_bridge(settings, timeout=load_ai_config().claude.timeout_seconds),
    )
    return JobRunner(ctx)


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    log_secret_status(settings.secrets_dir)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    runner = build_runner(settings)
    scheduler = build_scheduler(runner, settings.tz)
    scheduler.start()
    logger.info("Worker gestartet")
    startup_task = asyncio.create_task(startup(runner))
    startup_task.add_done_callback(_log_startup_result)
    polling_task = asyncio.create_task(poll_questions(runner.ctx, stop))
    polling_task.add_done_callback(_log_polling_end)
    background = [startup_task, polling_task]
    if runner.ctx.claude is not None:
        # Read-only Kickbase tools for Claude; guarded by the same token as the bridge.
        mcp = serve_mcp(runner.ctx, runner.ctx.claude.token, settings.claude_mcp_port, stop)
        mcp_task = asyncio.create_task(mcp)
        mcp_task.add_done_callback(_log_mcp_end)
        background.append(mcp_task)
    await stop.wait()
    logger.info("Worker wird beendet")
    scheduler.shutdown(wait=False)
    startup_task.cancel()
    polling_task.cancel()
    # Let cancelled jobs record their status before the client and database go away
    # (the MCP server ends by itself once `stop` is set).
    await asyncio.gather(*background, return_exceptions=True)
    ctx = runner.ctx
    await ctx.client.aclose()
    for resource in (ctx.notifier, ctx.llm, ctx.news_feed, ctx.oldb, ctx.claude):
        if resource is not None:
            await resource.aclose()


if __name__ == "__main__":
    asyncio.run(run())
