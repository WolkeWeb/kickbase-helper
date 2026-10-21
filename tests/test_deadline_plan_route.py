from datetime import UTC, datetime, timedelta

import httpx

from app.services.holding_roles import Role, set_role
from tests.fakes import TEAM_LEAGUE_ID, fixture

LEAGUE_ID = TEAM_LEAGUE_ID


def _market(deadline_in: timedelta = timedelta(days=2)):
    data = fixture("league_market")
    data["dt"] = (datetime.now(UTC) + deadline_in).isoformat().replace("+00:00", "Z")
    return data


def test_team_page_loads_deadline_plan_lazily(logged_in_client):
    html = logged_in_client.get("/").text
    assert 'hx-get="/deadline_plan"' in html
    assert 'hx-trigger="load"' in html


def test_deadline_plan_without_htmx_redirects_to_team_page(logged_in_client):
    response = logged_in_client.get("/deadline_plan", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"


async def test_deadline_plan_partial_renders_plan_with_fixtures(logged_in_client, fake_kickbase):
    fake_kickbase.on(
        "GET", f"/v4/leagues/{LEAGUE_ID}/market", _market(deadline_in=timedelta(days=3))
    )
    html = logged_in_client.get("/deadline_plan", headers={"HX-Request": "true"}).text
    assert "<html" not in html
    assert 'id="deadline-plan"' in html
    assert "Verkäufe bis" in html
    assert "Anpfiff" in html
    # No deficit with the recorded squad, so no sales plan is drawn.
    assert "Finanzierung" not in html
    assert "Elf stellen und Gebote setzen" in html


async def test_deadline_plan_partial_shows_sales_plan_when_minus(logged_in_client, fake_kickbase):
    budget = fixture("league_budget")
    budget["b"], budget["pbaa"] = -8_000_000.0, -8_000_000.0
    fake_kickbase.on("GET", f"/v4/leagues/{LEAGUE_ID}/me/budget", budget)
    fake_kickbase.on(
        "GET", f"/v4/leagues/{LEAGUE_ID}/market", _market(deadline_in=timedelta(days=3))
    )
    html = logged_in_client.get("/deadline_plan", headers={"HX-Request": "true"}).text
    assert "Finanzierung" in html
    assert "Bester Plan" in html


async def test_deadline_plan_partial_marks_a_late_window(logged_in_client, fake_kickbase):
    fake_kickbase.on(
        "GET", f"/v4/leagues/{LEAGUE_ID}/market", _market(deadline_in=timedelta(hours=1))
    )
    html = logged_in_client.get("/deadline_plan", headers={"HX-Request": "true"}).text
    assert "nicht mehr sicher" in html


async def test_deadline_plan_partial_lists_protected_players(logged_in_client, fake_kickbase):
    protected_pid = str(next(iter(fixture("league_squad")["it"]))["i"])
    await set_role(
        logged_in_client.app.state.sessions,
        LEAGUE_ID,
        protected_pid,
        Role.CORE,
        datetime.now(UTC),
    )
    fake_kickbase.on(
        "GET", f"/v4/leagues/{LEAGUE_ID}/market", _market(deadline_in=timedelta(days=3))
    )
    html = logged_in_client.get("/deadline_plan", headers={"HX-Request": "true"}).text
    assert "geschützte Stammspieler" in html


def test_deadline_plan_handles_kickbase_outage(logged_in_client, fake_kickbase):
    fake_kickbase.on("GET", f"/v4/leagues/{LEAGUE_ID}/market", httpx.Response(503))
    html = logged_in_client.get("/deadline_plan", headers={"HX-Request": "true"}).text
    assert "Plan konnte nicht geladen werden" in html


def test_deadline_plan_partial_without_a_deadline_is_unknown(logged_in_client, fake_kickbase):
    market = fixture("league_market")
    market["dt"] = None
    fake_kickbase.on("GET", f"/v4/leagues/{LEAGUE_ID}/market", market)
    html = logged_in_client.get("/deadline_plan", headers={"HX-Request": "true"}).text
    assert "Nächste Deadline unbekannt" in html
