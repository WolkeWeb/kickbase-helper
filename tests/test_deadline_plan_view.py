from datetime import timedelta

import pytest
from sqlalchemy import create_engine

from app.db.models import Base
from app.db.session import make_engine, make_sessionmaker
from app.kickbase.league import ResolvedLeague
from app.services.deadline_plan import deadline_plan_view
from app.services.holding_roles import Role, set_role
from tests.fakes import (
    NOW,
    TEAM_LEAGUE_ID,
    add_recommendation_endpoints,
    fake_with_team_data,
    fixture,
    make_client,
)

LEAGUE = ResolvedLeague(TEAM_LEAGUE_ID, "Testliga", "1")
MARKET_PATH = f"/v4/leagues/{TEAM_LEAGUE_ID}/market"


def _market(
    deadline_in: timedelta | None = timedelta(days=2),
    my_offers: list[tuple[str, int]] | None = None,
):
    """The market fixture with a deadline relative to NOW and optional own bids. The raw
    fixture must keep its real keys ("uop") so pydantic parses `my_offer`."""
    data = fixture("league_market")
    if deadline_in is not None:
        data["dt"] = (NOW + deadline_in).isoformat().replace("+00:00", "Z")
    else:
        data["dt"] = None
    # The fixture keeps a couple of `uop` entries from a different test scenario; strip them so
    # the bids shown here match exactly what the test sets up.
    for entry in data["it"]:
        entry.pop("uop", None)
    if my_offers:
        by_id = {pid: amount for pid, amount in my_offers}
        for entry in data["it"]:
            pid = str(entry["i"])
            if pid in by_id:
                entry["uop"] = by_id[pid]
    return data


@pytest.fixture
async def sessions(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'plan.db'}"
    sync = create_engine(url)
    Base.metadata.create_all(sync)
    sync.dispose()
    engine = make_engine(url)
    yield make_sessionmaker(engine)
    await engine.dispose()


@pytest.fixture
def fake():
    fake = fake_with_team_data()
    add_recommendation_endpoints(fake)
    return fake


async def load(fake, sessions, *, deadline_in=timedelta(days=2), my_offers=None):
    fake.on("GET", MARKET_PATH, _market(deadline_in=deadline_in, my_offers=my_offers))
    async with make_client(fake) as client:
        return await deadline_plan_view(client, sessions, LEAGUE, NOW)


async def test_view_loads_squad_budget_eleven_and_protected_ids(fake, sessions):
    player_id = next(iter(fixture("league_squad")["it"]))["i"]
    await set_role(sessions, LEAGUE.id, str(player_id), Role.CORE, NOW)
    view = await load(fake, sessions, deadline_in=timedelta(hours=42))
    assert view is not None
    plan = view.plan
    assert plan.deadline == NOW + timedelta(hours=42)
    assert plan.sell_by == plan.deadline - timedelta(hours=2)
    assert view.matchday == 5
    protected_ids = {p.id for p in plan.protected_players}
    assert str(player_id) in protected_ids


async def test_view_returns_none_when_squad_is_empty(fake, sessions):
    squad = fixture("league_squad")
    squad["it"] = []
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/squad", squad)
    view = await load(fake, sessions)
    assert view is None


async def test_view_omits_past_deadlines(fake, sessions):
    view = await load(fake, sessions, deadline_in=timedelta(minutes=-30))
    assert view is not None
    assert view.plan.deadline is None
    assert view.plan.sell_by is None


async def test_view_treats_existing_my_offers_as_open_bids(fake, sessions):
    bidding_id = next(iter(fixture("league_market")["it"]))["i"]
    # Set budget such that the only known reservation comes from the explicit bid.
    budget = fixture("league_budget")
    budget["b"], budget["pbaa"] = 10_000_000.0, 10_000_000.0
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/me/budget", budget)
    view = await load(fake, sessions, my_offers=[(bidding_id, 2_500_000)])
    assert view.plan.reserved_bids == 2_500_000


async def test_view_uses_pending_offers_budget_when_set(fake, sessions):
    # pbaa = b − 4 Mio. means Kickbase expects 4 Mio. worth of bids to clear; the plan must
    # keep that amount reserved even when no per-player bid is known from the feed yet.
    budget = fixture("league_budget")
    budget["b"], budget["pbaa"] = 10_000_000.0, 6_000_000.0
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/me/budget", budget)
    view = await load(fake, sessions)
    assert view.plan.reserved_bids == 4_000_000


async def test_view_returns_fresh_data_when_all_sources_ok(fake, sessions):
    view = await load(fake, sessions)
    assert view is not None
    assert view.stale is False
    assert view.fetched_at is not None


async def test_view_builds_a_schedule_with_steps(fake, sessions):
    view = await load(fake, sessions)
    steps = view.plan.steps
    labels = [s.label for s in steps]
    assert "Elf stellen und Gebote setzen" in labels
