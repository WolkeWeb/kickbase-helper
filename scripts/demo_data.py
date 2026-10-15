"""Frei erfundene Spielerdaten für den Demo-Server (`scripts/demo_server.py`).

Marktwert-Verläufe, Punkte und Aufstellungsprognosen kommen aus einem festen Zufallsgenerator;
die Spieler stammen aus den anonymisierten Fixtures. Nichts davon stammt aus einem echten
Konto oder einer echten Liga.
"""

import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.db.models import (
    LineupPrediction,
    MarketValue,
    MatchdayPoints,
    Player,
    PlayerStatsDaily,
    Team,
)
from app.kickbase.calendar import market_day
from tests.fakes import fixture

SEASON = 2026
HISTORY_DAYS = 21


def seed_demo_data(engine: Engine, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    rng = random.Random(42)
    today = market_day(now)
    table = fixture("competition_table")["it"]
    squad = fixture("league_squad")["it"]
    market = fixture("league_market")["it"]
    matchdays = fixture("competition_matchdays")["it"]

    with Session(engine) as session, session.begin():
        for t in table:
            session.add(Team(id=t["tid"], name=t["tn"], image=t.get("tim"), updated_at=now))

        for index, raw in enumerate(squad + market):
            on_market = index >= len(squad)
            value = raw["mv"]
            # Trend je Spieler: ein paar klare Aufsteiger und Absteiger, der Rest schwankt.
            drift = rng.choice([-0.012, -0.004, 0.0, 0.004, 0.012, 0.02])
            status = raw.get("st", 0)
            session.add(
                Player(
                    id=raw["i"],
                    name=raw["n"],
                    team_id=raw["tid"],
                    position=raw["pos"],
                    status=status,
                    market_value=value,
                    image=raw.get("pim"),
                    updated_at=now,
                )
            )
            level = value / (1 + drift) ** HISTORY_DAYS
            history = []
            for back in range(HISTORY_DAYS, -1, -1):
                level *= 1 + drift + rng.uniform(-0.006, 0.006)
                history.append((today - timedelta(days=back), level))
            scale = value / history[-1][1]
            for day, raw_value in history:
                session.add(MarketValue(player_id=raw["i"], day=day, value=int(raw_value * scale)))
            week_ago = int(history[-8][1] * scale)
            session.add(
                PlayerStatsDaily(
                    player_id=raw["i"],
                    day=today,
                    status=status,
                    team_id=raw["tid"],
                    market_value_change_7d=value - week_ago,
                )
            )
            base = rng.choice([40, 70, 100, 130, 160]) if not on_market else rng.randint(30, 140)
            for day in matchdays[:5]:
                match = next(
                    (m for m in day["it"] if raw["tid"] in (m["t1"], m["t2"])),
                    None,
                )
                if match is None or rng.random() < 0.12:
                    continue
                home = match["t1"] == raw["tid"]
                session.add(
                    MatchdayPoints(
                        season=SEASON,
                        matchday=day["day"],
                        player_id=raw["i"],
                        points=max(-20, int(rng.gauss(base, 55))),
                        team_id=raw["tid"],
                        opponent_id=match["t2"] if home else match["t1"],
                        is_home=home,
                        kickoff=datetime.fromisoformat(match["dt"].replace("Z", "+00:00")),
                    )
                )
            if (not on_market and rng.random() < 0.8) or (on_market and rng.random() < 0.5):
                session.add(
                    LineupPrediction(
                        source="ligainsider",
                        team_id=raw["tid"],
                        player_id=raw["i"],
                        player_name=raw["n"],
                        fetched_at=now,
                        status="startelf",
                    )
                )
