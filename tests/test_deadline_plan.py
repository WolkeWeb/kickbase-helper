from datetime import UTC, datetime, timedelta

from app.kickbase.models import Position
from app.services.deadline_plan import build_deadline_plan
from app.services.scoring import TradingConfig
from app.services.sell_guide import SalePlayer

TW, ABW, MF, ST = Position.GOALKEEPER, Position.DEFENDER, Position.MIDFIELDER, Position.FORWARD


def _player(pid, name, value, position, average=80, start=90, out=False):
    return SalePlayer(name, value, average, start, position, out, pid)


def _default_squad():
    return (
        [_player("1", "Keeper", 5_000_000, TW, average=80, start=90)]
        + [_player(str(i), f"D{i}", 4_000_000, ABW, average=80, start=90) for i in range(2, 6)]
        + [_player(str(i), f"M{i}", 6_000_000, MF, average=80, start=90) for i in range(6, 11)]
        + [_player(str(i), f"S{i}", 5_000_000, ST, average=80, start=90) for i in range(11, 14)]
    )


def _lineup():
    return {"1", *{str(i) for i in range(2, 12)}}


def test_no_deficit_means_no_sales_plan():
    plan = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        deadline=datetime(2026, 10, 23, 18, 30, tzinfo=UTC),
        budget=12_000_000,
        pending_offers_budget=12_000_000,
        own_bids={},
        squad=_default_squad(),
        lineup_ids=_lineup(),
    )
    assert plan.sales is None
    assert plan.deficit_stays_visible is False
    assert plan.late_window is False
    assert any(step.label == "Elf stellen und Gebote setzen" for step in plan.steps)
    assert all(step.label != "Verkäufe abschließen" for step in plan.steps)


def test_deficit_protected_players_excluded_from_the_guide():
    plan = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        deadline=datetime(2026, 10, 23, 18, 30, tzinfo=UTC),
        budget=-9_000_000,
        own_bids={},
        squad=[
            _player("1", "Maza", 30_000_000, MF),
            _player("2", "Prass", 6_000_000, MF, average=10),
            _player("3", "Hansen", 3_000_000, ABW, average=80),
        ],
        protected_ids={"1"},
    )
    assert plan.sales is not None
    assert plan.sales.best is not None
    assert "Maza" not in [p.name for p in plan.sales.best.players]
    sold_step = next(step for step in plan.steps if step.label == "Verkäufe abschließen")
    assert sold_step.at == datetime(2026, 10, 23, 16, 30, tzinfo=UTC)
    assert {"Prass", "Hansen"} == set(sold_step.players)
    assert plan.deficit_stays_visible is False


def test_deficit_only_protected_players_can_cover():
    plan = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        deadline=datetime(2026, 10, 23, 18, 30, tzinfo=UTC),
        budget=-12_000_000,
        own_bids={},
        squad=[
            _player("1", "Maza", 30_000_000, MF),
            _player("2", "Prass", 5_000_000, MF, average=10),
        ],
        protected_ids={"1", "2"},
    )
    assert plan.sales is not None
    assert plan.sales.best is None
    assert plan.deficit_stays_visible is True
    assert any("geschützte Stammspieler" in note for note in plan.notes)


def test_duplicate_bids_on_the_same_listing_are_counted_only_once():
    plan = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        deadline=datetime(2026, 10, 23, 18, 30, tzinfo=UTC),
        budget=5_000_000,
        own_bids=[("99", 2_000_000), ("99", 2_500_000)],
        squad=_default_squad(),
    )
    assert plan.reserved_bids == 2_500_000

    plan_dict = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        deadline=datetime(2026, 10, 23, 18, 30, tzinfo=UTC),
        budget=5_000_000,
        own_bids={"99": 2_500_000},
        squad=_default_squad(),
    )
    assert plan_dict.reserved_bids == 2_500_000
    plan_sequences_agree = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        deadline=datetime(2026, 10, 23, 18, 30, tzinfo=UTC),
        budget=5_000_000,
        own_bids=[("99", 2_000_000), ("99", 2_500_000)],
        squad=_default_squad(),
    )
    assert plan_sequences_agree.reserved_bids == plan_dict.reserved_bids


def test_conservative_reserve_uses_higher_value_on_inconsistent_budget():
    plan = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        deadline=datetime(2026, 10, 23, 18, 30, tzinfo=UTC),
        budget=10_000_000,
        pending_offers_budget=8_000_000,
        own_bids={"42": 3_000_000},
        squad=_default_squad(),
    )
    assert plan.reserved_bids == 3_000_000
    assert any("höhere bekannte Betrag" in note for note in plan.notes)


def test_deadline_in_less_than_two_hours_means_late_window():
    now = datetime(2026, 10, 23, 17, 0, tzinfo=UTC)
    deadline = datetime(2026, 10, 23, 18, 30, tzinfo=UTC)
    plan = build_deadline_plan(
        now=now,
        deadline=deadline,
        budget=-8_000_000,
        own_bids={},
        squad=[
            _player("1", "Keeper", 5_000_000, TW, average=80, start=90),
            _player("2", "Verteidiger", 4_000_000, ABW, average=80, start=90),
        ],
    )
    assert plan.late_window is True
    assert plan.sell_by is not None and plan.sell_by <= now
    assert not any(step.label == "Verkäufe abschließen" for step in plan.steps)
    assert any("Verkauf vor Deadline nicht mehr sicher" in note for note in plan.notes)


def test_no_deadline_keeps_the_plan_without_schedule():
    plan = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        deadline=None,
        budget=-4_000_000,
        own_bids={},
        squad=[
            _player("1", "Keeper", 5_000_000, TW, average=80, start=90),
            _player("2", "Verteidiger", 4_000_000, ABW, average=80, start=90),
        ],
    )
    assert plan.deadline is None and plan.sell_by is None
    assert plan.sales is not None
    assert plan.steps == ()
    assert plan.late_window is False


def test_trading_config_overrides_safety_hours_and_sell_by():
    cfg = TradingConfig(
        close_hours=24,
        safety_hours=4,
        min_daily_gain=20_000,
        daily_fall_percent=-0.25,
    )
    deadline = datetime(2026, 10, 25, 20, 30, tzinfo=UTC)
    plan = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        deadline=deadline,
        budget=1_000_000,
        own_bids={},
        squad=_default_squad(),
        trading=cfg,
    )
    assert plan.sell_by == deadline - timedelta(hours=4)


def test_free_slots_reports_missing_positions_for_the_best_formation():
    plan = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        deadline=datetime(2026, 10, 23, 18, 30, tzinfo=UTC),
        budget=1_000_000,
        own_bids={},
        squad=[_player("1", "Einziger", 1_000_000, TW, average=80, start=90)],
    )
    assert plan.free_slots.get(Position.DEFENDER, 0) >= 1
    assert plan.free_slots.get(Position.FORWARD, 0) >= 1


def test_bids_already_counted_by_kickbase_remove_them_once():
    plan = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        deadline=datetime(2026, 10, 23, 18, 30, tzinfo=UTC),
        budget=4_000_000,
        pending_offers_budget=2_000_000,
        own_bids={},
        squad=_default_squad(),
    )
    assert plan.reserved_bids == 2_000_000
    assert plan.sales is None


def test_explicit_bids_enlarge_the_sales_target():
    plan = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        deadline=datetime(2026, 10, 23, 18, 30, tzinfo=UTC),
        budget=-2_000_000,
        own_bids={"99": 4_000_000},
        squad=[
            _player("1", "Keeper", 5_000_000, TW, average=80, start=90),
            _player("2", "Verteidiger", 4_000_000, ABW, average=80, start=90),
        ],
    )
    assert plan.reserved_bids == 4_000_000
    assert plan.sales is not None
    assert plan.sales.deficit == 6_000_000


def test_protected_players_are_listed_even_without_deficit():
    plan = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        deadline=datetime(2026, 10, 23, 18, 30, tzinfo=UTC),
        budget=1_000_000,
        own_bids={},
        squad=[
            _player("1", "Stammspieler", 1_000_000, MF, average=80, start=90),
        ],
        protected_ids={"1"},
    )
    assert [p.id for p in plan.protected_players] == ["1"]


def test_naive_now_is_treated_as_utc():
    plan = build_deadline_plan(
        now=datetime(2026, 10, 21, 12, 0),
        deadline=datetime(2026, 10, 23, 18, 30, tzinfo=UTC),
        budget=1_000_000,
        own_bids={},
        squad=_default_squad(),
    )
    assert plan.deadline is not None
    assert plan.deadline.tzinfo is not None
