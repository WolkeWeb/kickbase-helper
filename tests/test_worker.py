import asyncio
import signal

from app.worker import main as worker


class DummyClient:
    async def aclose(self):
        pass


class DummyRunner:
    def __init__(self):
        self.ctx = type(
            "Ctx",
            (),
            {
                "client": DummyClient(),
                "notifier": None,
                "llm": None,
                "news_feed": None,
                "oldb": None,
                "claude": None,
            },
        )()


def test_schedule():
    scheduler = worker.build_scheduler(DummyRunner(), "Europe/Berlin")
    jobs = {job.id: str(job.trigger) for job in scheduler.get_jobs()}
    assert set(jobs) == {
        "sync_players",
        "catch_up",
        "evening",
        "sync_points",
        "sync_points_evening",
        "collect_lineups",
        "collect_lineups_evening",
        "collect_predictions",
        "collect_predictions_afternoon",
        "collect_injuries",
        "deadline_warning",
        "deadline_plan_reminder",
        "notify_watchlist",
        "notify_own_offers",
        "matchday_report",
        "matchday_report_morning",
        "purge_cache",
        "morning_news",
        "news_midday",
        "news_evening",
        "deadline_news",
        "transfers",
        "log_recommendations",
    }
    assert "hour='*/2', minute='10'" in jobs["transfers"]
    assert "hour='7', minute='0'" in jobs["sync_players"]
    assert "hour='7', minute='30'" in jobs["collect_lineups"]
    assert "hour='23', minute='50'" in jobs["collect_lineups_evening"]
    # The briefing runs after the morning news, which run after players and points.
    assert "hour='7', minute='40'" in jobs["morning_news"]
    assert "hour='22', minute='45'" in jobs["evening"]
    assert "hour='23', minute='45'" in jobs["sync_points_evening"]
    assert "hour='7-23', minute='*/30'" in jobs["notify_watchlist"]
    assert "hour='7-23', minute='*/30'" in jobs["notify_own_offers"]
    assert "hour='7-23', minute='0'" in jobs["deadline_plan_reminder"]
    assert "hour='23', minute='55'" in jobs["matchday_report"]
    assert "hour='7', minute='35'" in jobs["matchday_report_morning"]


async def test_worker_stops_on_sigterm(monkeypatch, settings):
    started = asyncio.Event()

    async def fake_startup(runner):
        started.set()

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "build_runner", lambda s: DummyRunner())
    monkeypatch.setattr(worker, "startup", fake_startup)
    task = asyncio.create_task(worker.run())
    await asyncio.wait_for(started.wait(), timeout=5)
    signal.raise_signal(signal.SIGTERM)
    await asyncio.wait_for(task, timeout=5)


class RecordingRunner:
    """Fake runner: records which jobs ran and returns configured results."""

    def __init__(self, *, results=None, synced_today=False, now=None):
        from datetime import UTC, datetime

        self.calls: list[str] = []
        self.results = results or {}
        self.synced_today = synced_today
        moment = now or datetime(2026, 10, 7, 5, 0, tzinfo=UTC)
        self.ctx = type("Ctx", (), {"clock": staticmethod(lambda: moment)})()

    async def run(self, name, job):
        self.calls.append(name)
        return self.results.get(name, True)

    async def succeeded_today(self, name):
        # Like the real runner: true once the job ran successfully today (or was set up so).
        ran_ok = name in self.calls and self.results.get(name, True)
        return self.synced_today or ran_ok

    async def mark_interrupted_runs(self):
        self.calls.append("mark_interrupted")
        return 0


async def test_startup_runs_quick_jobs_first_then_backfill():
    runner = RecordingRunner()
    await worker.startup(runner)
    assert runner.calls == [
        "mark_interrupted",
        "sync_players",
        "notify_squad",
        "sync_points",
        "collect_lineups",
        "collect_lineup_predictions",
        "collect_injuries",
        "collect_news",
        "collect_league_transfers",
        "track_offers",
        "log_recommendations",
        "backfill_market_values",
    ]


async def test_startup_skips_player_sync_done_today():
    runner = RecordingRunner(synced_today=True)
    await worker.startup(runner)
    assert runner.calls == [
        "mark_interrupted",
        "sync_points",
        "collect_lineups",
        "backfill_market_values",
    ]


async def test_no_backfill_after_failed_player_sync():
    runner = RecordingRunner(results={"sync_players": False})
    await worker.startup(runner)
    assert "backfill_market_values" not in runner.calls
    runner = RecordingRunner(results={"sync_players": False})
    await worker.daily_players(runner)
    assert runner.calls == ["sync_players"]  # no notification and no backfill after a failure


async def test_startup_waits_during_market_value_update():
    from datetime import UTC, datetime

    runner = RecordingRunner(now=datetime(2026, 10, 6, 20, 5, tzinfo=UTC))
    await worker.startup(runner)
    assert runner.calls == [
        "mark_interrupted",
        "sync_points",
        "collect_lineups",
        "collect_lineup_predictions",
        "collect_injuries",
        "collect_news",
        "collect_league_transfers",
        "track_offers",
    ]


async def test_morning_news_runs_before_briefing():
    runner = RecordingRunner()
    await worker.morning_news(runner)
    assert runner.calls == ["collect_news", "daily_briefing"]


async def test_catch_up_only_when_morning_run_failed():
    runner = RecordingRunner(synced_today=True)
    await worker.catch_up_players(runner)
    assert runner.calls == []
    runner = RecordingRunner(synced_today=False)
    await worker.catch_up_players(runner)
    assert runner.calls == [
        "sync_players",
        "notify_squad",
        "backfill_market_values",
        "log_recommendations",  # the snapshot the morning missed
    ]


async def test_evening_run_notifies_after_sync():
    runner = RecordingRunner()
    await worker.evening_players(runner)
    assert runner.calls == ["sync_players", "notify_squad"]
    runner = RecordingRunner(results={"sync_players": False})
    await worker.evening_players(runner)
    assert runner.calls == ["sync_players"]


async def test_startup_failure_is_logged(caplog):
    async def boom():
        raise RuntimeError("DB weg")

    task = asyncio.create_task(boom())
    task.add_done_callback(worker._log_startup_result)
    await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert "Nachholen beim Start fehlgeschlagen" in caplog.text


async def test_offers_only_after_a_successful_transfer_run():
    runner = RecordingRunner(results={"collect_league_transfers": False})
    await worker.transfers(runner)
    assert runner.calls == ["collect_league_transfers"]


async def test_snapshot_waits_for_todays_player_sync():
    runner = RecordingRunner()
    await worker.snapshot(runner)
    assert runner.calls == []  # no sync today: no snapshot of stale values
    runner = RecordingRunner(synced_today=True)
    await worker.snapshot(runner)
    assert runner.calls == ["log_recommendations"]


async def test_catch_up_takes_the_missed_snapshot():
    runner = RecordingRunner()
    await worker.catch_up_players(runner)
    assert runner.calls[0] == "sync_players"
    assert "log_recommendations" in runner.calls
