from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine

from app.db.models import Base
from app.db.session import make_engine, make_sessionmaker
from app.kickbase.league import ResolvedLeague
from app.lineups.service import InjuryNote
from app.services.today import TodayView, lineup_risks, today_view
from tests.fakes import (
    NOW,
    TEAM_LEAGUE_ID,
    FakeOpenLigaDb,
    add_recommendation_endpoints,
    fake_with_team_data,
    fixture,
    make_client,
    make_oldb_client,
)

LEAGUE = ResolvedLeague(TEAM_LEAGUE_ID, "Testliga", "1")

MARKET_PATH = f"/v4/leagues/{TEAM_LEAGUE_ID}/market"


def market(deadline_in: timedelta = timedelta(days=2)) -> dict:
    """The market fixture with the deadline relative to now; the fixed fixture deadline
    made `test_today_partial` fail once the recorded moment had passed."""
    data = fixture("league_market")
    data["dt"] = (datetime.now(UTC) + deadline_in).isoformat().replace("+00:00", "Z")
    return data


@pytest.fixture
async def sessions(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'today.db'}"
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


async def load(fake, sessions):
    async with make_client(fake) as client, make_oldb_client(FakeOpenLigaDb()) as oldb:
        return await today_view(client, oldb, sessions, LEAGUE, NOW)


def titles(view, tone=None):
    return [i.title for i in view.items if tone is None or i.tone == tone]


async def test_today_lists_empty_slots_offers_and_deadline(fake, sessions):
    view = await load(fake, sessions)
    # The fixture eleven has 9 players: 2 empty slots are the most urgent item.
    assert view.items[0].tone == "danger"
    assert view.items[0].title == "2 Plätze in der Aufstellung leer"
    assert view.items[0].href == "/empfehlungen/elf"
    assert any(t.startswith("2 offene Gebote über") for t in titles(view, "ok"))
    assert view.deadline == datetime(2026, 10, 9, 18, 30, tzinfo=UTC)
    assert view.matchday == 5
    assert view.countdown == "noch 3 Tage 6 Std."
    assert not view.deadline_close and not view.all_clear
    # No item for the budget: 17 Mio. and 8 Mio. after the bids are both fine.
    assert not [t for t in titles(view) if "Minus" in t or "Gebote:" in t]


async def test_today_warns_about_minus(fake, sessions):
    budget = fixture("league_budget")
    budget["b"], budget["pbaa"] = -2_000_000.0, -3_000_000.0
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/me/budget", budget)
    view = await load(fake, sessions)
    minus = [i for i in view.items if i.title.startswith("Im Minus")]
    assert minus and minus[0].tone == "danger" and minus[0].href == "/empfehlungen/verkaufen"


async def test_today_warns_if_winning_all_bids_means_minus(fake, sessions):
    budget = fixture("league_budget")
    budget["b"], budget["pbaa"] = 2_000_000.0, -3_000_000.0
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/me/budget", budget)
    view = await load(fake, sessions)
    warn = [i for i in view.items if i.title.startswith("Gewinnen alle Gebote")]
    assert warn and warn[0].tone == "warn"
    assert not [t for t in titles(view) if t.startswith("Im Minus")]


async def test_full_lineup_without_risks_is_all_clear_for_the_lineup(fake, sessions):
    eleven = fixture("league_myeleven")
    eleven["lp"] = (eleven["lp"] * 2)[:11]
    for i, player in enumerate(eleven["lp"]):
        player.update(i=f"x{i}", st=0)
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/teamcenter/myeleven", eleven)
    view = await load(fake, sessions)
    assert "Aufstellung vollständig, keine Ausfallrisiken" in titles(view, "ok")
    assert not [t for t in titles(view) if "leer" in t]


def test_lineup_risks_flags_players_out():
    class P:
        def __init__(self, pid, status):
            self.id, self.name, self.team_id, self.status = pid, f"N{pid}", "2", status

    injuries = {"1": InjuryNote("verletzung", "Kreuzband", None)}
    risks = lineup_risks([P("1", 0), P("2", 0)], {}, {}, injuries, {})
    assert [(r.player_id, r.percent) for r in risks] == [("1", 0)]
    assert risks[0].reasons


@pytest.mark.parametrize(
    ("left", "text", "close"),
    [
        (timedelta(days=1, hours=3, minutes=5), "noch 1 Tag 3 Std.", False),
        (timedelta(hours=2, minutes=14), "noch 2 Std. 14 Min.", False),
        (timedelta(minutes=9), "noch 9 Min.", True),
        (timedelta(minutes=-5), "noch 0 Min.", True),
    ],
)
def test_countdown(left, text, close):
    view = TodayView(now=NOW, deadline=NOW + left, matchday=5)
    assert view.countdown == text
    assert view.deadline_close is close


def test_no_deadline():
    view = TodayView(now=NOW, deadline=None, matchday=None)
    assert view.countdown is None and not view.deadline_close and view.all_clear


def test_team_page_loads_today_lazily(logged_in_client):
    html = logged_in_client.get("/").text
    assert 'hx-get="/heute"' in html and 'hx-trigger="load"' in html


def test_today_partial(logged_in_client, fake_kickbase):
    # Deadline relative to now: the fixed fixture deadline made this fail shortly before it.
    fake_kickbase.on("GET", MARKET_PATH, market())
    html = logged_in_client.get("/heute", headers={"HX-Request": "true"}).text
    assert 'id="today"' in html and "<html" not in html
    assert "2 Plätze in der Aufstellung leer" in html
    assert "bis zur Deadline (5. Spieltag)" in html
    assert 'href="/empfehlungen/elf"' in html
    assert "Dringend:" in html  # the tone is not color alone


def test_today_without_htmx_goes_to_the_team_page(logged_in_client):
    response = logged_in_client.get("/heute", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"


def test_today_kickbase_outage_is_a_note(logged_in_client, fake_kickbase):
    import httpx

    fake_kickbase.on("GET", "/v4/leagues/1000002/market", httpx.Response(503))
    html = logged_in_client.get("/heute", headers={"HX-Request": "true"}).text
    assert "„Heute“ konnte nicht geladen werden" in html


async def test_best_buy_skips_players_already_bid_on(fake, sessions):
    top = (await load(fake, sessions)).best_buy.player.id
    market = fixture("league_market")
    next(p for p in market["it"] if p["i"] == top)["uop"] = 1_000_000
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/market", market)
    view = await load(fake, sessions)
    assert view.best_buy is not None and view.best_buy.player.id != top
    # The briefing's buy list follows the same rule.
    assert view.buys[0] is view.best_buy
    assert top not in {r.player.id for r in view.buys}


async def test_ended_bids_are_not_open(fake, sessions):
    market = fixture("league_market")
    for p in market["it"]:
        if p.get("uop"):
            p["exs"] = 0  # ended the moment the market was fetched
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/market", market)
    view = await load(fake, sessions)
    assert not [t for t in titles(view) if "offene" in t]


async def test_stale_lineup_marks_the_box_stale(fake, sessions):
    import httpx

    from tests.fakes import FakeClock

    clock = FakeClock()
    async with make_client(fake, clock=clock) as client, make_oldb_client(FakeOpenLigaDb()) as oldb:
        await today_view(client, oldb, sessions, LEAGUE, NOW)
        clock.advance(minutes=20)
        fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/teamcenter/myeleven", httpx.Response(503))
        view = await today_view(client, oldb, sessions, LEAGUE, clock())
    assert view.stale


def test_lineup_risks_flag_a_coin_flip_and_any_status_but_fit():
    from app.lineups.service import TeamPrediction

    class P:
        def __init__(self, pid, status):
            self.id, self.name, self.team_id, self.status = pid, f"N{pid}", "2", status

    predicted = {"2": TeamPrediction(starting=frozenset({"knock", "fit"}))}
    players = [P("knock", 2), P("fit", 0), P("unknown", 2)]
    risks = {r.player_id: r for r in lineup_risks(players, {}, predicted, {}, {})}
    # Like Maza on 09.10.2026: predicted starter, "angeschlagen" → capped at 50 %: flagged.
    assert risks["knock"].percent == 50
    assert "fit" not in risks  # a fit predicted starter is no risk
    # Not in the prediction and doubtful: flagged as well (start chance below the cap).
    assert "unknown" in risks and risks["unknown"].reasons
    assert risks["knock"].headline == "Nknock: Startchance 50 %"


def test_unknown_status_code_is_flagged_with_the_status_once():
    from app.kickbase import models as m
    from app.lineups.service import TeamPrediction

    class P:
        def __init__(self, pid, status):
            self.id, self.name, self.team_id, self.status = pid, f"N{pid}", "2", status

    code = 99
    assert m.availability(code) is m.Availability.UNKNOWN
    predicted = {"2": TeamPrediction(starting=frozenset({"starter"}))}
    risks = {r.player_id: r for r in lineup_risks([P("starter", code)], {}, predicted, {}, {})}
    # A predicted starter with an unknown status: flagged, and the status says why.
    starter = risks["starter"]
    assert starter.reasons[0].startswith("Kickbase-Status")
    assert sum("Kickbase-Status" in r for r in starter.reasons) == 1
    assert "Kickbase-Status" not in starter.headline  # no doubled text in "X: … (…)"


async def test_doubtful_starter_is_not_all_clear(fake, sessions):
    eleven = fixture("league_myeleven")
    eleven["lp"] = (eleven["lp"] * 2)[:11]
    for i, player in enumerate(eleven["lp"]):
        player.update(i=f"x{i}", st=0)
    eleven["lp"][0]["st"] = 2  # angeschlagen
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/teamcenter/myeleven", eleven)
    view = await load(fake, sessions)
    assert "Aufstellung vollständig, keine Ausfallrisiken" not in titles(view)
    assert any(t.startswith(eleven["lp"][0]["n"]) for t in titles(view, "warn"))
