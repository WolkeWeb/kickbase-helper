import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import create_engine

from app.db.models import AiUsage, Base, NewsItem, NewsReport
from app.db.session import make_engine, make_sessionmaker
from app.kickbase.league import LeagueContext, ResolvedLeague
from app.news.service import EXTRACT_STATUS_KEY, FEED_STATUS_KEY
from app.notify.telegram import TelegramNotifier
from app.services.briefing import (
    Briefing,
    BriefingData,
    Fact,
    _day,
    collect_briefing_data,
    compose_briefing,
    unknown_numbers,
    write_briefing,
)
from app.worker.news_jobs import AI_PAUSE_KEY, BRIEFING_KEY, daily_briefing
from tests.fakes import (
    TEAM_LEAGUE_ID,
    FakeClock,
    FakeLlm,
    FakeOpenLigaDb,
    FakeTelegram,
    MemoryKV,
    add_recommendation_endpoints,
    fake_with_team_data,
    make_client,
    make_llm,
    make_oldb_client,
)

NOW = datetime(2026, 10, 6, 5, 40, tzinfo=UTC)
TZ = ZoneInfo("Europe/Berlin")
LEAGUE = ResolvedLeague(TEAM_LEAGUE_ID, "Meine Liga", "1")
SQUAD_PLAYER = "8227"  # Vandevoordt in league_squad.json


@pytest.fixture
async def sessions(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'briefing.db'}"
    sync = create_engine(url)
    Base.metadata.create_all(sync)
    sync.dispose()
    engine = make_engine(url)
    yield make_sessionmaker(engine)
    await engine.dispose()


FACTS = (
    Fact("spieltag", "Aufstellung für Spieltag 7 bis Fr. 9.10. 20:30 (noch 3 Tage 14 Std.)"),
    Fact(
        "heute_1",
        "Musiala: Startchance 30 % – angeschlagen",
        important=True,
        actions=frozenset({"aufstellen"}),
    ),
    Fact("konto", "Kontostand 3,72 Mio."),
    Fact(
        "kauf_1",
        "Kaufempfehlung 1: Harry Kane (Bayern, ST), Preis 5,6 Mio., Score 71, "
        "Angebot endet morgen 20:26",
        "Score-Bestandteile: Form +20",
    ),
    Fact(
        "kauf_2",
        "Kaufempfehlung 2: Leon Goretzka (Bayern, MF), Preis 4,1 Mio., Score 50, "
        "Angebot endet heute 22:59",
    ),
    Fact(
        "marktwerte",
        "Teamwert seit gestern +1,49 Mio.; größter Anstieg Conté +270 Tsd.",
        info_only=True,
    ),
)
NAMES = frozenset({"Musiala", "Kane", "Harry Kane", "Goretzka", "Leon Goretzka", "Conté", "Wirtz"})
DATA = BriefingData("Dienstag, 6.10.", FACTS, NAMES, ("Programm zählt neutral.",))
SOURCE = "Kontostand 3,72 Mio.; Kane 5,6 Mio., Form +20; Musiala −8,3 %"


@pytest.mark.parametrize(
    ("text", "unknown"),
    [
        ("Kane für 5,6 Mio. kaufen, Budget 3,72 Mio.", []),
        ("2 Spieler fehlen, Top 3, 11 Punkte", []),  # small whole numbers
        ("12 Punkte", ["12"]),
        ("Kane für 5,9 Mio. kaufen", ["5,9"]),
        ("Form 20", []),  # unsigned stands for "+20"
        ("Form -20", ["-20"]),  # but a sign must match
        ("Musiala −8,3 %", []),
        ("Musiala +8,3 %", ["+8,3"]),
    ],
)
def test_unknown_numbers(text, unknown):
    assert unknown_numbers(text, SOURCE) == unknown


def line(text, *ids):
    return {"punkte": list(ids), "text": text}


async def ask(sessions, *lines):
    llm = make_llm(sessions, fake := FakeLlm({"zeilen": list(lines)}), FakeClock(NOW))
    briefing = await write_briefing(llm, DATA)
    await llm.aclose()
    return briefing, fake


async def test_write_briefing_uses_checked_ai_text(sessions):
    briefing, fake = await ask(
        sessions,
        line("Musiala wackelt (Startchance 30 %) – Aufstellung prüfen.", "heute_1", "spieltag"),
        line("Kaufen: Kane (5,6 Mio., endet morgen 20:26).", "kauf_1", "konto"),
        line(" ", "konto"),
    )
    assert briefing.lines == (
        "Musiala wackelt (Startchance 30 %) – Aufstellung prüfen.",
        "Kaufen: Kane (5,6 Mio., endet morgen 20:26).",
    )
    assert briefing.by_ai
    assert briefing.note == "KI-Text von small"
    prompt = fake.requests[0]["messages"][1]["content"]
    assert json.loads(prompt) == DATA.payload  # the model sees exactly our facts
    assert DATA.payload["punkte"][1] == {
        "id": "heute_1",
        "text": "Musiala: Startchance 30 % – angeschlagen",
        "wichtig": True,
    }
    assert DATA.payload["punkte"][-1]["nur_info"] is True


@pytest.mark.parametrize(
    ("bad", "problem"),
    [
        # A rising market value is no reason to sell: "verkaufen" isn't in the fact.
        (line("Verkaufe Conté, Marktwert +270 Tsd.", "marktwerte"), "„verkaufen“ ohne Grundlage"),
        # The day of one offer moved to another offer.
        (line("Kaufe Goretzka bis morgen 20:26.", "kauf_2"), "Zahlen nicht im Bezug ['20', '26']"),
        # A player from outside the referenced facts.
        (line("Setze Wirtz statt Goretzka ein.", "kauf_2"), "Spieler nicht im Bezug ['Wirtz']"),
        (line("Kauf Goretzka (Kaufempfehlung 2).", "kauf_7"), "ohne gültigen Bezug"),
        # Lineup advice from a buy fact.
        (line("Goretzka in die Startelf.", "kauf_2"), "„aufstellen“ ohne Grundlage"),
    ],
)
async def test_write_briefing_replaces_lines_that_do_not_match_their_facts(sessions, bad, problem):
    good = line("Musiala: Startchance 30 % – nicht aufstellen.", "heute_1")
    briefing, _ = await ask(sessions, good, bad)
    assert briefing.by_ai
    assert briefing.lines[0] == good["text"]
    # The plain sentences of the referenced facts instead of the bad line.
    referenced = [f.text for f in FACTS if f.id in bad["punkte"]]
    assert list(briefing.lines[1:]) == referenced
    assert problem in briefing.note


async def test_write_briefing_puts_missing_important_facts_first(sessions):
    # The line cites the lineup risk but doesn't name the player: that doesn't count.
    briefing, _ = await ask(
        sessions,
        line("Kaufen: Kane für 5,6 Mio.", "kauf_1", "heute_1"),
    )
    assert briefing.lines == (
        "Musiala: Startchance 30 % – angeschlagen",
        "Kaufen: Kane für 5,6 Mio.",
    )
    assert "1 wichtige(r) Punkt(e) ergänzt" in briefing.note


@pytest.mark.parametrize(
    ("answer", "note"),
    [
        ([line("Kane für 7,1 Mio. kaufen.", "kauf_1")], "KI-Text verworfen"),
        ([line("Alles ruhig.")], "ohne gültigen Bezug"),
        ([], "keinen Text"),
    ],
)
async def test_write_briefing_falls_back(sessions, answer, note):
    briefing, _ = await ask(sessions, *answer)
    assert briefing.lines == DATA.fallback_lines
    assert not briefing.by_ai
    assert note in briefing.note


@pytest.mark.parametrize(
    ("at", "text"),
    [
        (datetime(2026, 10, 6, 20, 59, tzinfo=UTC), "heute 22:59"),
        (datetime(2026, 10, 6, 22, 30, tzinfo=UTC), "morgen 00:30"),
        (datetime(2026, 10, 9, 18, 30, tzinfo=UTC), "Fr. 9.10. 20:30"),
    ],
)
def test_day_names_today_and_tomorrow(at, text):
    assert _day(at, "Europe/Berlin", NOW) == text


def test_compose_briefing_escapes_and_adds_notes():
    text = compose_briefing(
        "A&B",
        Briefing(("Kaufe <Kane>",), True, ""),
        ["Programm <neutral>"],
        "https://kbh.test/empfehlungen",
    )
    lines = text.split("\n")
    assert lines[0] == "<b>Briefing A&amp;B</b>"
    assert lines[1] == "Kaufe &lt;Kane&gt;"
    assert lines[2] == "⚠️ Programm &lt;neutral&gt;"
    assert "KI-Text aus den Dashboard-Daten" in lines[3]
    assert '<a href="https://kbh.test/empfehlungen">Empfehlungen öffnen</a>' in lines[3]


async def add_squad_news(sessions, published=NOW, *, updated=None, revision=1):
    async with sessions.begin() as session:
        session.add(
            NewsItem(
                guid="g1",
                source="kicker",
                title="Torwart fehlt",
                teaser="",
                link="https://kicker.test/1",
                categories=[],
                published_at=published,
                fetched_at=NOW,
                processed_at=NOW,
                updated_at=updated,
                revision=revision,
            )
        )
        session.add(
            NewsReport(
                news_guid="g1",
                published_at=published,
                player_name="Maarten Vandevoordt",
                team_id="43",
                player_id=SQUAD_PLAYER,
                kind="verletzung",
                status="faellt_aus",
                absence="rund drei Wochen",
                expected_return=None,
                revision=revision,
            )
        )


def facts_by_id(data):
    return {f.id: f for f in data.facts}


async def test_collect_briefing_data(sessions):
    await add_squad_news(sessions)
    fake = fake_with_team_data()
    add_recommendation_endpoints(fake)
    async with make_client(fake) as client, make_oldb_client(FakeOpenLigaDb()) as oldb:
        data = await collect_briefing_data(client, oldb, sessions, LEAGUE, "Europe/Berlin", NOW)
    assert data.payload["datum"] == "Dienstag, 6.10."
    facts = facts_by_id(data)
    # Deadline and the "Heute" box as on the team page; problems are important.
    assert facts["spieltag"].text.startswith(
        "Aufstellung für Spieltag 5 bis Fr. 9.10. 20:30 (noch "
    )
    assert facts["heute_1"].text.startswith("2 Plätze in der Aufstellung leer")
    assert facts["heute_1"].important and facts["heute_1"].actions == {"aufstellen"}
    assert any(
        f.text.startswith("Kader-Lücke: 2× ST") and f.actions == {"kaufen"} for f in facts.values()
    )
    assert (
        facts["konto"].text
        == "Kontostand 17,01\u00a0Mio., nach allen offenen Geboten 8,01\u00a0Mio."
    )
    assert facts["meldung_1"].text.startswith(
        "Meldung zu Vandevoordt (Leipzig, eigener Kader): fällt aus (Verletzung: rund drei Wochen; "
    )
    assert facts["meldung_1"].important
    # The buy list as the buy page shows it: with bid advice and the exact day of the end.
    buys = [f for f in data.facts if f.id.startswith("kauf_")]
    assert 1 <= len(buys) <= 3
    assert buys[0].text.startswith(
        "Kaufempfehlung 1: Reigan Heskey (Köln, MF), Preis 500\u00a0Tsd."
    )
    assert "Angebot endet morgen 09:03" in buys[0].text
    assert buys[0].detail.startswith("Score-Bestandteile: Form +")
    assert facts["verkauf_1"].text.startswith("Verkaufskandidat 1: Hansen (Paderborn)")
    assert facts["marktwerte"].info_only and not facts["marktwerte"].important
    # The whole squad is no longer a fact: nothing to invent a sale from.
    assert "Regeer" not in data.payload_json
    assert {"Regeer", "Heskey", "Reigan Heskey"} <= data.names
    # Other managers never go to the model: not the sellers, not the league name
    # (it contains a person's name).
    assert "Manager" not in data.payload_json
    assert "Meine Liga" not in data.payload_json
    assert data.fallback_lines == tuple(f.text for f in data.facts)


async def test_briefing_keeps_original_date_and_identifies_recent_revision(sessions):
    await add_squad_news(
        sessions, NOW - timedelta(days=20), updated=NOW - timedelta(hours=1), revision=2
    )
    fake = fake_with_team_data()
    add_recommendation_endpoints(fake)
    async with make_client(fake) as client, make_oldb_client(FakeOpenLigaDb()) as oldb:
        data = await collect_briefing_data(client, oldb, sessions, LEAGUE, "Europe/Berlin", NOW)
    news = facts_by_id(data)["meldung_1"].text
    assert "16.9." in news
    assert "Änderung erkannt 06:40 · Revision 2" in news


@pytest.fixture
async def briefing_ctx(sessions):
    from app.worker.jobs import JobContext

    fake = fake_with_team_data()
    add_recommendation_endpoints(fake)
    clock = FakeClock(NOW)
    client = make_client(fake, clock=clock)
    oldb = make_oldb_client(FakeOpenLigaDb())
    telegram = FakeTelegram()
    kv = MemoryKV()
    notifier = TelegramNotifier("t", "42", transport=httpx.MockTransport(telegram.handler))
    ctx = JobContext(
        client=client,
        sessions=sessions,
        league=LeagueContext(client, kv, "Testliga"),
        kv=kv,
        tz=TZ,
        clock=clock,
        notifier=notifier,
        public_url="https://kbh.test",
        oldb=oldb,
    )
    ctx.telegram = telegram
    fresh = NOW.isoformat()
    await kv.set(FEED_STATUS_KEY, {"ok": True, "at": fresh})
    await kv.set(EXTRACT_STATUS_KEY, {"at": fresh, "state": "ok", "pending": 0, "skipped": 0})
    yield ctx
    await client.aclose()
    await oldb.aclose()
    await notifier.aclose()


async def test_daily_briefing_once_a_day(briefing_ctx):
    ctx = briefing_ctx
    answer = {"zeilen": [{"punkte": ["konto"], "text": "Kontostand 17,01 Mio."}]}
    llm = ctx.llm = make_llm(ctx.sessions, FakeLlm(answer), ctx.clock)
    result = await daily_briefing(ctx)
    assert result.startswith("Briefing gesendet (KI-Text von small, ")
    assert "wichtige(r) Punkt(e) ergänzt" in result
    assert await daily_briefing(ctx) == "Briefing heute schon gesendet"
    [text] = ctx.telegram.texts
    lines = text.split("\n")
    assert lines[0] == "<b>Briefing Testliga</b>"
    # The important points the model left out come first (here the empty lineup slots).
    assert lines[1].startswith("2 Plätze in der Aufstellung leer")
    assert "Kontostand 17,01 Mio." in lines
    assert "<i>KI-Text aus den Dashboard-Daten</i>" in text
    assert await ctx.kv.get(BRIEFING_KEY) == "2026-10-06"
    await llm.aclose()


async def test_daily_briefing_mentions_incomplete_news(briefing_ctx):
    ctx = briefing_ctx
    await ctx.kv.set(
        EXTRACT_STATUS_KEY, {"at": NOW.isoformat(), "state": "failed", "detail": "HTTP 503"}
    )
    answer = {"zeilen": [{"punkte": ["konto"], "text": "Kontostand 17,01 Mio."}]}
    llm = ctx.llm = make_llm(ctx.sessions, FakeLlm(answer), ctx.clock)
    await daily_briefing(ctx)
    [text] = ctx.telegram.texts
    assert "⚠️ Meldungen: Auswertung der Meldungen zuletzt fehlgeschlagen (HTTP 503)." in text
    await llm.aclose()


async def test_daily_briefing_falls_back_without_answer(briefing_ctx):
    ctx = briefing_ctx
    fake = FakeLlm(httpx.Response(503, json={}), httpx.Response(503, json={}))
    llm = ctx.llm = make_llm(ctx.sessions, fake, ctx.clock)
    result = await daily_briefing(ctx)
    assert result.startswith("Briefing gesendet (KI-Fehler: KI-Anbieter antwortet mit HTTP 503")
    [text] = ctx.telegram.texts
    assert "\nKontostand 17,01\u00a0Mio." in text
    assert "<i>ohne KI</i>" in text
    await llm.aclose()


async def test_daily_briefing_at_the_limit_sends_plain_and_pause_notice(briefing_ctx):
    ctx = briefing_ctx
    fake = FakeLlm()
    llm = ctx.llm = make_llm(ctx.sessions, fake, ctx.clock, calls_per_day=1)
    async with ctx.sessions.begin() as session:
        session.add(AiUsage(at=NOW, task="news", model="small", status="ok"))
    result = await daily_briefing(ctx)
    assert result.startswith("Briefing gesendet (KI pausiert: Tageslimit")
    briefing, pause = ctx.telegram.texts
    assert "<i>ohne KI</i>" in briefing
    assert pause.startswith("<b>KI pausiert bis morgen</b>")
    assert await ctx.kv.get(BRIEFING_KEY) == "2026-10-06"
    assert await ctx.kv.get(AI_PAUSE_KEY) == "day:2026-10-06"
    assert fake.requests == []
    await llm.aclose()


async def test_daily_briefing_needs_ai_and_telegram(briefing_ctx):
    ctx = briefing_ctx
    assert await daily_briefing(ctx) == "Briefing aus (KI nicht konfiguriert)"
    ctx.notifier = None
    assert await daily_briefing(ctx) == "Briefing aus (Telegram nicht konfiguriert)"


async def test_collect_briefing_data_mentions_the_sell_guide_when_in_minus(sessions):
    from tests.fakes import fixture

    fake = fake_with_team_data()
    add_recommendation_endpoints(fake)
    budget = fixture("league_budget")
    budget["pbaa"] = -5_000_000.0
    budget["b"] = -5_000_000.0
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/me/budget", budget)
    async with make_client(fake) as client, make_oldb_client(FakeOpenLigaDb()) as oldb:
        data = await collect_briefing_data(client, oldb, sessions, LEAGUE, "Europe/Berlin", NOW)
    facts = facts_by_id(data)
    proposal = facts["verkaufsvorschlag"]
    assert proposal.important
    assert proposal.text.startswith("Verkaufsvorschlag gegen das Minus (")
    assert any(line.startswith("Im Minus: ") for line in data.fallback_lines)


async def test_briefing_mentions_minus_only_if_bids_win(sessions):
    from tests.fakes import fixture

    fake = fake_with_team_data()
    add_recommendation_endpoints(fake)
    budget = fixture("league_budget")
    budget["b"], budget["pbaa"] = 2_000_000.0, -3_000_000.0
    fake.on("GET", f"/v4/leagues/{TEAM_LEAGUE_ID}/me/budget", budget)
    async with make_client(fake) as client, make_oldb_client(FakeOpenLigaDb()) as oldb:
        data = await collect_briefing_data(client, oldb, sessions, LEAGUE, "Europe/Berlin", NOW)
    facts = facts_by_id(data)
    assert "verkaufsvorschlag" not in facts
    warning = next(f for f in facts.values() if f.text.startswith("Gewinnen alle Gebote: "))
    assert warning.important and warning.actions == {"verkaufen"}
    assert "−3,00\u00a0Mio." in warning.text


@pytest.mark.parametrize(
    ("text", "source", "unknown"),
    [
        ("Du hast 17 Mio.", "Kontostand: 17,00 Mio.", []),  # by value, not by spelling
        ("um 8,3 % gestiegen", "Marktwert 7 Tage: +8.3 %", []),  # English decimal point
        ("für 25,0 Mio.", "Frage: Reicht es für 25 Mio?", []),
        ("fällt 3-4 Wochen aus", "Ausfall: 3-4 Wochen", []),  # a range, no minus
        ("Marktwert 1.234.567 €", "1.234.567", []),
        ("3,7 Mio.", "3,72 Mio.", ["3,7"]),  # rounding is still not allowed
        ("Form -20", "Form +20", ["-20"]),
    ],
)
def test_unknown_numbers_by_value(text, source, unknown):
    assert unknown_numbers(text, source) == unknown
