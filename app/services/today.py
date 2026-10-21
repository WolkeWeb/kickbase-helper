"""The "Heute" box on the team page: what needs doing before the next deadline, at a glance."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.kickbase import models as m
from app.kickbase.client import KickbaseClient
from app.kickbase.league import ResolvedLeague
from app.lineups.service import InjuryNote, TeamPrediction, prediction_for
from app.openligadb.client import OpenLigaDbClient
from app.services.players import StartQuote, compute_start_probability
from app.services.recommendations import (
    Basis,
    BuyFilters,
    Recommendation,
    buy_view,
    load_basis,
    select_buys,
    squad_gaps,
    still_listed,
)
from app.services.transfers import load_bid_status
from app.web.formatting import money_short

LINEUP_SIZE = 11
# Starters with a start chance up to this (inclusive) are flagged – 50 % is a coin flip, e.g.
# every "angeschlagen" player (capped at 50 %). Any Kickbase status other than fit is flagged too.
RISK_THRESHOLD = 50
# The deadline counts as close (red) within this window – like the Telegram warning.
DEADLINE_CLOSE = timedelta(hours=2)


@dataclass(frozen=True)
class LineupRisk:
    player_id: str
    name: str
    percent: int | None  # None: no start data, flagged for the Kickbase status alone
    reasons: tuple[str, ...]

    @property
    def headline(self) -> str:
        if self.percent is None:
            return f"{self.name}: Startchance unbekannt"
        return f"{self.name}: Startchance {self.percent} %"


def lineup_risks(
    players: Iterable,
    squad_by_id: Mapping[str, m.SquadPlayer],
    predicted: Mapping[str, TeamPrediction],
    injuries: Mapping[str, InjuryNote],
    quotes: Mapping[str, StartQuote],
    threshold: int = RISK_THRESHOLD,
) -> list[LineupRisk]:
    """Lined-up players whose start chance is at most `threshold` or whose Kickbase status is
    not fit (shared with the deadline warning, so the page and Telegram agree)."""
    risks = []
    for p in players:
        sq = squad_by_id.get(p.id)
        status = sq.status if sq else p.status
        prob = compute_start_probability(
            prediction_for(predicted, p.team_id, p.id),
            injuries.get(p.id),
            m.availability(status),
            quotes.get(p.id),
            m.status_label(status),
        )
        # Known non-fit statuses are capped at ≤ 50 % already; an unknown Kickbase status code
        # isn't, so it is flagged on its own – with the status as the first reason.
        unknown_status = m.availability(status) is m.Availability.UNKNOWN
        low = prob.percent is not None and prob.percent <= threshold
        if low or unknown_status:
            reasons = prob.reasons
            if unknown_status:
                reasons = (f"Kickbase-Status {m.status_label(status)}", *prob.reasons)
            risks.append(LineupRisk(p.id, p.name, prob.percent, reasons))
    return risks


@dataclass(frozen=True)
class TodayItem:
    tone: str  # "danger" | "warn" | "ok"
    title: str
    detail: str | None = None
    href: str | None = None
    link: str | None = None


@dataclass(frozen=True)
class TodayView:
    now: datetime
    deadline: datetime | None
    matchday: int | None
    items: list[TodayItem] = field(default_factory=list)
    best_buy: Recommendation | None = None
    # The buy list as the buy page shows it by default, without offers the user already bid on.
    buys: tuple[Recommendation, ...] = ()
    bid_hint: str | None = None  # why the bid advice may be missing or not current
    notes: list[str] = field(default_factory=list)  # informational data hints
    warnings: list[str] = field(default_factory=list)  # failed/outdated sources: always shown
    stale: bool = False

    @property
    def deadline_close(self) -> bool:
        return self.deadline is not None and self.deadline - self.now <= DEADLINE_CLOSE

    @property
    def countdown(self) -> str | None:
        """'noch 1 Tag 3 Std.', 'noch 2 Std. 14 Min.', 'noch 9 Min.'"""
        if self.deadline is None:
            return None
        left = max(timedelta(0), self.deadline - self.now)
        days, rest = divmod(int(left.total_seconds()), 86400)
        hours, minutes = rest // 3600, rest % 3600 // 60
        if days:
            parts = [f"{days} {'Tag' if days == 1 else 'Tage'}", f"{hours} Std."]
        elif hours:
            parts = [f"{hours} Std.", f"{minutes} Min."]
        else:
            parts = [f"{minutes} Min."]
        return "noch " + " ".join(parts)

    @property
    def all_clear(self) -> bool:
        return not any(item.tone != "ok" for item in self.items)


async def today_view(
    client: KickbaseClient,
    oldb: OpenLigaDbClient,
    sessions: async_sessionmaker,
    league: ResolvedLeague,
    now: datetime | None = None,
    basis: Basis | None = None,
) -> TodayView:
    now = now or datetime.now(UTC)
    basis = basis or await load_basis(client, oldb, sessions, league, now)
    bids = await load_bid_status(sessions, league.id, now, basis.config.bids)
    buys = await buy_view(client, sessions, league, basis, bids.model, bids.hint, now)
    market_fetched = await client.market(league.id)
    eleven_fetched = await client.my_eleven(league.id)
    # buy_view added budget, market and squad; the lineup drives most items, so it counts too.
    basis.fetched.append(eleven_fetched)
    market, eleven = market_fetched.data, eleven_fetched.data
    squad = (await client.squad(league.id)).data
    budget = (await client.budget(league.id)).data

    items: list[TodayItem] = []
    lineup_link = {"href": "/empfehlungen/elf", "link": "Beste Elf"}
    empty = max(0, LINEUP_SIZE - len(eleven.players))
    if empty:
        items.append(
            TodayItem(
                "danger",
                f"{empty} {'Platz' if empty == 1 else 'Plätze'} in der Aufstellung leer",
                "Ein leerer Platz bringt keine Punkte.",
                **lineup_link,
            )
        )
    squad_by_id = {p.id: p for p in squad.players}
    risks = lineup_risks(eleven.players, squad_by_id, basis.predicted, basis.injuries, basis.quotes)
    for risk in risks:
        items.append(
            TodayItem(
                "danger" if risk.percent == 0 else "warn",
                risk.headline,
                ", ".join(risk.reasons) or None,
                href=f"/spieler/{risk.player_id}",
                link="Spieler",
            )
        )
    if not empty and not risks and eleven.players:
        items.append(TodayItem("ok", "Aufstellung vollständig, keine Ausfallrisiken"))

    if budget.budget < 0:
        items.append(
            TodayItem(
                "danger",
                f"Im Minus: {money_short(budget.budget)}",
                "Im Minus gibt es am Spieltag keine Punkte – vor der Deadline verkaufen.",
                href="/empfehlungen/verkaufen",
                link="Verkaufen",
            )
        )
    elif budget.budget_after_offers < 0:
        items.append(
            TodayItem(
                "warn",
                f"Gewinnen alle Gebote: {money_short(budget.budget_after_offers)}",
                "Dann bist du im Minus und bekommst keine Punkte.",
                href="/empfehlungen/verkaufen",
                link="Verkaufen",
            )
        )

    gaps = squad_gaps(squad.players)
    if gaps:
        missing = ", ".join(f"{n}× {pos.short_label}" for pos, n in gaps.items())
        items.append(
            TodayItem(
                "warn",
                f"Kader-Lücke: {missing}",
                "Es fehlen einsatzfähige Spieler für eine volle Elf.",
                href="/empfehlungen/kaufen?luecke=1",
                link="Passende Käufe",
            )
        )

    mine = [p for p in still_listed(market_fetched, now) if p.my_offer is not None]
    if mine:
        total = sum(p.my_offer or 0 for p in mine)
        names = ", ".join(f"{p.last_name} ({money_short(p.my_offer)})" for p in mine[:3])
        more = f" und {len(mine) - 3} weitere" if len(mine) > 3 else ""
        items.append(
            TodayItem(
                "ok",
                f"{len(mine)} offene {'Gebot' if len(mine) == 1 else 'Gebote'} über "
                f"{money_short(total)}",
                names + more,
                href="/empfehlungen/kaufen",
                link="Markt",
            )
        )

    # The best offer the user hasn't bid on yet (a bid is already "done").
    bid_on = {p.id for p in mine}
    shown, _ = select_buys(buys, BuyFilters())
    open_buys = tuple(r for r in shown if r.player.id not in bid_on)
    return TodayView(
        now=now,
        deadline=buys.deadline,
        matchday=market.next_matchday,
        items=items,
        best_buy=open_buys[0] if open_buys else None,
        buys=open_buys,
        bid_hint=buys.bid_hint,
        notes=list(basis.notes),
        warnings=list(basis.warnings),
        stale=basis.stale,
    )
