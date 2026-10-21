"""Tests for the deadline-plan reminder (Block C, C21.3).

Two layers:

* the *pure* rule layer (`app.notify.deadline_plan`) – fingerprint, message body and the kv
  dedupe comparison, exercised with plain dataclasses and an in-memory kv stub
* the *worker job* (`app.worker.jobs.deadline_plan_reminder`) – early bail-outs, Telegram
  send and failed-send handling, exercised through the same `FakeKickbase`/`JobContext`
  harness the other notification jobs use
"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.kickbase.league import ResolvedLeague
from app.notify.deadline_plan import (
    DEFICIT_PREFIX,
    NO_FIRE,
    SOLVED_PREFIX,
    Reminder,
    compose_reminder,
    read_fingerprint,
    should_remind,
    situation_key,
)
from app.notify.deadline_plan import (
    KEY as FINGERPRINT_KEY,
)
from app.notify.events import (
    DeadlinePlanRule,
    MarketValueRule,
    NotificationConfig,
)
from app.services.deadline_plan import (
    DeadlinePlan,
    DeadlinePlanView,
)
from app.services.sell_guide import (
    GuideWeights,
    SalePlayer,
    SellGuide,
    SellOption,
)
from tests.fakes import TEAM_LEAGUE_ID, fixture

LEAGUE_NAME = "Testliga"
LEAGUE = ResolvedLeague(TEAM_LEAGUE_ID, LEAGUE_NAME, "1")
TZ = ZoneInfo("Europe/Berlin")
BASE_URL = "https://kickbase.example.com"
TOKEN = "123456:SECRET-token"

NOTIFY_CONFIG = NotificationConfig(
    market_value=MarketValueRule(min_percent=10, min_amount=1_500_000),
    status_changes=True,
    deadline_plan=DeadlinePlanRule(enabled=True),
)
DISABLED = NOTIFY_CONFIG.model_copy(update={"deadline_plan": DeadlinePlanRule(enabled=False)})


# ---------------------------------------------------------------- helpers (pure layer)


def _sale_player(pid: str, name: str, value: int = 5_000_000, position=None) -> SalePlayer:
    return SalePlayer(
        id=pid, name=name, value=value, average_points=80, start_percent=90, position=position
    )


def _build_plan(
    *,
    budget: int = 1_000_000,
    pending_offers_budget: int | None = None,
    sales: SellGuide | None = None,
    deficit_stays_visible: bool = False,
    late_window: bool = False,
    deadline: bool | datetime | None = False,
) -> DeadlinePlan:
    # `False` (the default) keeps the helper opinionated about a sane deadline; `None` explicitly
    # means "no deadline at all" and must survive.
    if deadline is False:
        final_deadline: datetime | None = datetime(2026, 10, 9, 18, 30, tzinfo=UTC)
    else:
        final_deadline = deadline
    sell_by = final_deadline - timedelta(hours=2) if final_deadline is not None else None
    return DeadlinePlan(
        deadline=final_deadline,
        sell_by=sell_by,
        budget=budget,
        reserved_bids=0,
        free_slots={},
        protected_players=(),
        sales=sales,
        steps=(),
        late_window=late_window,
        deficit_stays_visible=deficit_stays_visible,
        notes=(),
    )


def _sell_guide(
    *, deficit: int, player_ids: tuple[str, ...], names: tuple[str, ...] | None = None
) -> SellGuide:
    if names is None:
        names = tuple(f"P{pid}" for pid in player_ids)
    players = tuple(
        _sale_player(pid, name, value=4_000_000)
        for pid, name in zip(player_ids, names, strict=True)
    )
    return SellGuide(
        budget=-deficit,
        deficit=deficit,
        weights=GuideWeights(),
        best=SellOption(players=players, total=deficit, covers=True),
        fewest=SellOption(players=players, total=deficit, covers=True),
        candidates=SellOption(players=(), total=0, covers=False),
    )


def _view(plan: DeadlinePlan) -> DeadlinePlanView:
    return DeadlinePlanView(
        plan=plan,
        now=datetime(2026, 10, 9, 8, 0, tzinfo=UTC),
        matchday=5,
    )


class _StubKv:
    """Minimal async key/value store for the pure-layer tests."""

    def __init__(self, initial: dict | None = None) -> None:
        self.data = dict(initial or {})

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value):
        self.data[key] = value


# ---------------------------------------------------------------- fingerprint


def test_situation_key_is_none_when_no_deadline():
    plan = _build_plan(deadline=None, budget=-1_000_000)
    assert situation_key(plan) == NO_FIRE


def test_situation_key_solved_when_budget_is_healthy():
    plan = _build_plan(budget=10_000_000, sales=None)
    assert situation_key(plan).startswith(SOLVED_PREFIX)


def test_situation_key_deficit_buckets_amounts_and_lists_player_ids():
    plan_a = _build_plan(budget=-2_000_000, sales=_sell_guide(deficit=2_000_000, player_ids=("1",)))
    plan_b = _build_plan(budget=-2_200_000, sales=_sell_guide(deficit=2_200_000, player_ids=("1",)))
    plan_c = _build_plan(budget=-2_000_000, sales=_sell_guide(deficit=2_000_000, player_ids=("2",)))
    key_a = situation_key(plan_a)
    key_b = situation_key(plan_b)
    key_c = situation_key(plan_c)
    assert key_a.startswith(DEFICIT_PREFIX)
    # ±200 k€ sits in the same bucket → same key (uses stable player ids, not names)
    assert key_a == key_b
    # Different sales list → new key
    assert key_c != key_a
    # Sales list uses SalePlayer.id when present (stable across renames), not the human name
    assert key_a.endswith(":1")
    assert key_c.endswith(":2")
    assert ":P1:" not in key_a
    assert ":P2:" not in key_c


def test_situation_key_keeps_deficit_visible_marker_separate():
    """When the deficit can only be covered by selling protected players, we still owe a
    reminder – the fingerprint reflects that so an alarm fires."""
    plan = _build_plan(
        budget=-5_000_000,
        sales=_sell_guide(deficit=5_000_000, player_ids=()),
        deficit_stays_visible=True,
    )
    assert situation_key(plan).startswith(DEFICIT_PREFIX)


# ---------------------------------------------------------------- message body


def test_compose_reminder_returns_none_when_no_deadline():
    assert compose_reminder(_view(_build_plan(deadline=None)), LEAGUE_NAME, TZ, BASE_URL) is None


def test_compose_reminder_for_solved_deficit_still_carries_anchor():
    plan = _build_plan(budget=5_000_000)
    reminder = compose_reminder(_view(plan), LEAGUE_NAME, TZ, BASE_URL)
    # Even a "no deficit" run goes through compose (the worker's skip happens *after*),
    # so callers can quote any time the dashboard itself is shown.
    assert reminder is None or reminder.key.startswith(SOLVED_PREFIX)


def test_compose_reminder_lists_sales_and_deadline():
    plan = _build_plan(
        budget=-2_000_000,
        sales=_sell_guide(deficit=2_000_000, player_ids=("101",), names=("Hansen",)),
    )
    reminder = compose_reminder(_view(plan), LEAGUE_NAME, TZ, BASE_URL)
    assert isinstance(reminder, Reminder)
    assert "Hansen" in reminder.text
    assert "Deadline-Plan Testliga" in reminder.text
    assert "/deadline_plan" in reminder.text
    assert reminder.key.startswith(DEFICIT_PREFIX)


def test_compose_reminder_warns_when_late_window():
    plan = _build_plan(late_window=True)
    reminder = compose_reminder(_view(plan), LEAGUE_NAME, TZ, BASE_URL)
    assert reminder is not None and "Verkauf vor Deadline nicht mehr sicher" in reminder.text


# ---------------------------------------------------------------- kv dedupe


async def test_should_remind_fires_on_first_run_and_silences_repeat():
    kv = _StubKv()
    assert await should_remind(kv, "deficit:abc:1:1,2") is True
    await kv.set(FINGERPRINT_KEY, "deficit:abc:1:1,2")
    assert await should_remind(kv, "deficit:abc:1:1,2") is False
    assert await should_remind(kv, "deficit:abc:1:1,3") is True


async def test_should_remind_recognises_solved_baseline():
    """Solved → open with the same numbers is a *new* alarm. The dedupe is per fingerprint
    string, and the solved marker is a different string from any deficit key."""
    kv = _StubKv({FINGERPRINT_KEY: "solved:deadline-iso"})
    assert await should_remind(kv, "deficit:deadline-iso:2000000:1") is True


async def test_commit_round_trips_through_kv():
    kv = _StubKv()
    from app.notify.deadline_plan import commit_reminder

    await commit_reminder(kv, "solved:now")
    assert await read_fingerprint(kv) == "solved:now"


# ---------------------------------------------------------------- worker job


@pytest.fixture
async def ctx(tmp_path):
    """Lightweight context that mimics `tests.fakes.make_client` + `JobContext`. The
    `deadline_plan_reminder` only needs the kickbase client, sessions and a kv store."""
    from sqlalchemy import create_engine

    from app.db.models import Base
    from app.db.session import make_engine, make_sessionmaker
    from app.db.stores import KeyValueStore
    from app.kickbase.league import LeagueContext
    from app.notify.telegram import TelegramNotifier
    from app.worker.jobs import JobContext
    from tests.fakes import FakeClock, FakeKickbase, FakeTelegram, make_client

    url = f"sqlite+pysqlite:///{tmp_path / 'reminder.db'}"
    sync = create_engine(url)
    Base.metadata.create_all(sync)
    sync.dispose()
    engine = make_engine(url)
    sessions = make_sessionmaker(engine)

    fake = FakeKickbase()
    fake.on("GET", "/v4/leagues/selection", fixture("leagues_selection"))
    fake.on("GET", "/v4/user/me", fixture("user_me"))
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/market", fixture("league_market"))
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/squad", fixture("league_squad"))
    fake.on(
        "GET",
        f"/v4/leagues/{TEAM_LEAGUE_ID}/teamcenter/myeleven",
        fixture("league_myeleven"),
    )
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/me/budget", fixture("league_budget"))
    clock = FakeClock(now=datetime(2026, 10, 9, 12, 0, tzinfo=UTC))
    client = make_client(fake, clock=clock, serve_stale=False)
    kv = KeyValueStore(sessions)
    context = JobContext(
        client=client,
        sessions=sessions,
        league=LeagueContext(client, kv, LEAGUE_NAME),
        kv=kv,
        tz=TZ,
        clock=clock,
        public_url=BASE_URL,
    )
    telegram = FakeTelegram()
    context.fake = fake
    context.notifier = TelegramNotifier(
        TOKEN, "42", transport=httpx.MockTransport(telegram.handler)
    )
    context.notifications = NOTIFY_CONFIG
    context.telegram = telegram
    yield context
    await client.aclose()
    await engine.dispose()


def _set_market(ctx, *, deadline_in: timedelta, budget: int):
    market = deepcopy(fixture("league_market"))
    # Anchor the deadline to the first call so a budget-only change does not accidentally
    # shift the deadline (and thereby the situation fingerprint).
    if getattr(ctx, "_market_anchor", None) is None:
        ctx._market_anchor = ctx.clock()
    market["dt"] = (ctx._market_anchor + deadline_in).isoformat().replace("+00:00", "Z")
    ctx.fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/market", market)
    budget_data = deepcopy(fixture("league_budget"))
    budget_data["b"] = float(budget)
    budget_data["pbaa"] = float(budget)
    ctx.fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/me/budget", budget_data)
    # The client has a 10-min live cache; advance the clock so the next call re-fetches.
    ctx.clock.advance(minutes=11)


def _clear_market_deadline(ctx):
    market = deepcopy(fixture("league_market"))
    market["dt"] = None
    ctx.fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/market", market)


async def test_reminder_without_telegram_bails_out_before_any_call(ctx):
    ctx.notifier = None
    ctx.notifications = None
    requests_before = len(ctx.fake.requests)

    from app.worker.jobs import deadline_plan_reminder

    summary = await deadline_plan_reminder(ctx)
    assert summary == "Benachrichtigungen aus (Telegram nicht konfiguriert)"
    assert len(ctx.fake.requests) == requests_before


async def test_reminder_disabled_bails_out(ctx):
    ctx.notifications = DISABLED
    requests_before = len(ctx.fake.requests)
    from app.worker.jobs import deadline_plan_reminder

    summary = await deadline_plan_reminder(ctx)
    assert summary == "Benachrichtigungen aus (Deadline-Plan-Alarm aus)"
    assert len(ctx.fake.requests) == requests_before
    assert ctx.telegram.texts == []


async def test_reminder_skips_when_no_deadline(ctx):
    _clear_market_deadline(ctx)
    from app.worker.jobs import deadline_plan_reminder

    summary = await deadline_plan_reminder(ctx)
    assert summary == "keine Deadline – kein Reminder"
    assert ctx.telegram.texts == []
    assert await ctx.kv.get(FINGERPRINT_KEY) == NO_FIRE


async def test_reminder_skips_when_deadline_outside_close_window(ctx):
    """A deadline three days out is *before* the `trading.close_hours` window (24 h)."""
    _set_market(ctx, deadline_in=timedelta(days=3), budget=20_000_000)
    from app.worker.jobs import deadline_plan_reminder

    summary = await deadline_plan_reminder(ctx)
    assert summary == "außerhalb des Deadline-Fensters"
    assert ctx.telegram.texts == []
    # No fingerprint committed – the previous state stays untouched.
    assert await ctx.kv.get(FINGERPRINT_KEY) is None


async def test_reminder_skips_when_budget_healthy(ctx):
    # Inside close window (deadline in 12 h), plenty of money → solved state.
    _set_market(ctx, deadline_in=timedelta(hours=12), budget=20_000_000)
    from app.worker.jobs import deadline_plan_reminder

    summary = await deadline_plan_reminder(ctx)
    assert summary == "kein offenes Defizit"
    assert ctx.telegram.texts == []
    stored = await ctx.kv.get(FINGERPRINT_KEY)
    assert stored is not None and stored.startswith(SOLVED_PREFIX)


async def test_reminder_sends_when_open_deficit(ctx):
    # Squad fixture sells for ~3.7 + 16 + 14 + 8 + ... Mio. so a 10 Mio. deficit has a clear guide.
    _set_market(ctx, deadline_in=timedelta(hours=12), budget=-10_000_000)
    from app.worker.jobs import deadline_plan_reminder

    summary = await deadline_plan_reminder(ctx)
    assert summary == "Deadline-Plan erinnert"
    assert len(ctx.telegram.texts) == 1
    text = ctx.telegram.texts[0]
    assert "Deadline-Plan Testliga" in text
    assert "/deadline_plan" in text
    stored = await ctx.kv.get(FINGERPRINT_KEY)
    assert stored is not None and stored.startswith(DEFICIT_PREFIX)


async def test_reminder_does_not_send_twice_for_same_fingerprint(ctx):
    _set_market(ctx, deadline_in=timedelta(hours=12), budget=-10_000_000)
    from app.worker.jobs import deadline_plan_reminder

    assert await deadline_plan_reminder(ctx) == "Deadline-Plan erinnert"
    # Second run with the same fixture → same fingerprint → silent.
    assert await deadline_plan_reminder(ctx) == "für diese Situation schon gemeldet"
    assert len(ctx.telegram.texts) == 1


async def test_reminder_fires_again_when_sales_list_changes(ctx):
    _set_market(ctx, deadline_in=timedelta(hours=12), budget=-10_000_000)
    from app.worker.jobs import deadline_plan_reminder

    await deadline_plan_reminder(ctx)

    # Small bucket change (±200 k€) still lands in the same bucket → silent.
    _set_market(ctx, deadline_in=timedelta(hours=12), budget=-10_200_000)
    assert await deadline_plan_reminder(ctx) == "für diese Situation schon gemeldet"
    # Big jump enough to push into a new deficit bucket → fresh alarm.
    _set_market(ctx, deadline_in=timedelta(hours=12), budget=-15_000_000)
    summary = await deadline_plan_reminder(ctx)
    assert summary == "Deadline-Plan erinnert"
    assert len(ctx.telegram.texts) == 2


async def test_reminder_fires_again_when_deficit_reappears(ctx):
    from app.worker.jobs import deadline_plan_reminder

    # 1) open deficit → alarm
    _set_market(ctx, deadline_in=timedelta(hours=12), budget=-10_000_000)
    await deadline_plan_reminder(ctx)
    stored = await ctx.kv.get(FINGERPRINT_KEY)
    assert stored is not None and stored.startswith(DEFICIT_PREFIX)

    # 2) solved → state changes to solved prefix, no alarm
    _set_market(ctx, deadline_in=timedelta(hours=12), budget=20_000_000)
    assert await deadline_plan_reminder(ctx) == "kein offenes Defizit"
    stored = await ctx.kv.get(FINGERPRINT_KEY)
    assert stored is not None and stored.startswith(SOLVED_PREFIX)
    assert len(ctx.telegram.texts) == 1

    # 3) deficit returns with *the same* numbers → fresh alarm (different fingerprint)
    _set_market(ctx, deadline_in=timedelta(hours=12), budget=-10_000_000)
    summary = await deadline_plan_reminder(ctx)
    assert summary == "Deadline-Plan erinnert"
    assert len(ctx.telegram.texts) == 2


async def test_reminder_failed_send_keeps_no_state(ctx):
    _set_market(ctx, deadline_in=timedelta(hours=12), budget=-10_000_000)
    ctx.telegram.fail = True
    from app.notify.telegram import TelegramError
    from app.worker.jobs import deadline_plan_reminder

    with pytest.raises(TelegramError):
        await deadline_plan_reminder(ctx)
    # No state advance → next run retries with the same fingerprint.
    stored = await ctx.kv.get(FINGERPRINT_KEY)
    assert stored is None or stored == NO_FIRE or not stored.startswith(DEFICIT_PREFIX)
    ctx.telegram.fail = False
    assert await deadline_plan_reminder(ctx) == "Deadline-Plan erinnert"
    assert len(ctx.telegram.texts) == 1
