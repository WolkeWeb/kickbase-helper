"""Rückwärtsplanung vom jetzigen Zeitpunkt bis zur echten Kickbase-Aufstellungs-Deadline.

Der Plan beachtet das Sicherheitsfenster aus `trading.safety_hours` (Verkäufe müssen vor der
eigentlichen Deadline verbucht sein) und nimmt den eigenen Stammkader (C17, „core") vom Verkauf
aus. Reicht die Zeit nicht mehr für sichere Verkäufe (englische Wochen oder sehr knappe Fristen),
fällt der Zeitplan weg – der Hinweis ersetzt ihn. Bleibt das Defizit nur deckbar, wenn geschützte
Stammspieler verkauft würden, wird das sichtbar gemacht statt still eines der Stammspieler
eingeplant.

Die Funktion ist rein: alle benötigten Daten werden übergeben, kein API-Aufruf, keine Datenbank.
Der asynchrone Loader in C21.2 sammelt sie aus den bereits vorhandenen Quellen
(`today_view`, `transfers.load_bid_status`, `holding_roles.roles_for`).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.kickbase import models as m
from app.kickbase.client import Fetched, KickbaseClient
from app.kickbase.league import ResolvedLeague
from app.services import holding_roles
from app.services.lineup import FORMATIONS, Formation
from app.services.scoring import TradingConfig, load_scoring_config
from app.services.sell_guide import (
    GuideWeights,
    SalePlayer,
    SellGuide,
    SellOption,
    build_sell_guide,
)

LINEUP_SIZE = 11
POSITIONS = (m.Position.GOALKEEPER, m.Position.DEFENDER, m.Position.MIDFIELDER, m.Position.FORWARD)


@dataclass(frozen=True)
class PlanStep:
    at: datetime
    label: str
    detail: str
    players: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeadlinePlan:
    deadline: datetime | None
    sell_by: datetime | None
    budget: int
    reserved_bids: int
    free_slots: dict[m.Position, int] = field(default_factory=dict)
    protected_players: tuple[SalePlayer, ...] = ()
    sales: SellGuide | None = None
    steps: tuple[PlanStep, ...] = ()
    late_window: bool = False
    deficit_stays_visible: bool = False
    notes: tuple[str, ...] = ()


def _coalesce_bids(
    own_bids: Mapping[str, int] | Iterable[tuple[str, int]] | None,
) -> int:
    """Summe der eigenen offenen Gebote, pro Spieler nur das höchste gezählt.

    Dasselbe Listing kann mehrfach im Feed erscheinen oder zwei Angebote für denselben Spieler
    parallel laufen – `my_offer` darf dann nicht doppelt eingehen, sonst übersteigt die Reserve
    das Budget.
    """
    if not own_bids:
        return 0
    if isinstance(own_bids, Mapping):
        return sum(max(0, int(v)) for v in own_bids.values())
    best: dict[str, int] = {}
    for player_id, amount in own_bids:
        if not player_id or amount is None:
            continue
        amount = int(amount)
        if amount <= 0:
            continue
        if player_id not in best or amount > best[player_id]:
            best[player_id] = amount
    return sum(best.values())


def _conservative_reserve(
    budget: int,
    pending_offers_budget: int | None,
    known_bids: int,
) -> tuple[int, str | None]:
    """Reservierter Betrag für offene Gebote; bei Widerspruch zwischen Budget und Geboten wird
    der höhere Wert genommen (gleiche Logik wie `transfer_simulation.reserve_from`)."""
    if pending_offers_budget is None:
        reserve_from_budget = 0
        offered = budget
    else:
        offered = pending_offers_budget
        reserve_from_budget = max(0, budget - offered)
    warning: str | None = None
    if known_bids > reserve_from_budget or (pending_offers_budget is not None and offered > budget):
        warning = (
            "Budget und bekannte Gebote passen nicht zusammen. "
            "Der höhere bekannte Betrag wird vorsichtshalber reserviert."
        )
    return max(reserve_from_budget, known_bids), warning


def _free_slots(squad: Sequence[SalePlayer]) -> dict[m.Position, int]:
    """Leere Plätze der besten Formation, die der Kader überhaupt decken kann (verletzte Spieler
    zählen nicht)."""
    counts = {pos: 0 for pos in POSITIONS}
    for player in squad:
        if player.out or player.position is None:
            continue
        counts[player.position] += 1
    best_missing: dict[m.Position, int] | None = None
    for formation in (Formation(*f) for f in FORMATIONS):
        missing = {
            pos: max(0, formation.slots(pos) - counts[pos])
            for pos in POSITIONS
            if formation.slots(pos) > counts[pos]
        }
        if best_missing is None or sum(missing.values()) < sum(best_missing.values()):
            best_missing = missing
    missing = best_missing or {}
    return {pos: count for pos, count in missing.items() if count > 0}


def _sell_choice(guide: SellGuide) -> SellOption:
    """Variante, die das Defizit deckt (geringster Punkteverlust) oder die wenigsten Spieler
    (wenn nur die geschützten Stammspieler es decken könnten)."""
    return guide.best if guide.best is not None else guide.fewest


def _lineup_step(deadline: datetime, lineup_count: int) -> PlanStep:
    if lineup_count:
        detail = f"Anpfiff – {lineup_count} von {LINEUP_SIZE} Plätzen aufgestellt."
    else:
        detail = "Anpfiff – Aufstellung fehlt, Punkte fallen aus."
    return PlanStep(at=deadline, label="Elf stellen und Gebote setzen", detail=detail)


def build_deadline_plan(
    *,
    now: datetime,
    deadline: datetime | None,
    budget: int,
    pending_offers_budget: int | None = None,
    own_bids: Mapping[str, int] | Iterable[tuple[str, int]] | None = None,
    squad: Sequence[SalePlayer],
    lineup_ids: Iterable[str] | None = None,
    protected_ids: set[str] | None = None,
    sell_weights: GuideWeights | None = None,
    trading: TradingConfig | None = None,
) -> DeadlinePlan:
    """Rückwärtsplan zum nächsten Anpfiff.

    Sämtliche Werte werden vorberechnet übergeben; die Funktion liest weder aus dem Netz noch
    aus der Datenbank. Sie nimmt das `TradingConfig` aus `config/scoring.yaml` als Voreinstellung,
    sodass Aufrufer die Fenster nur dann überschreiben müssen, wenn sie experimentieren.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    cfg = trading if trading is not None else load_scoring_config().trading
    bids_total = _coalesce_bids(own_bids)
    reserved, reserve_warning = _conservative_reserve(budget, pending_offers_budget, bids_total)
    sell_by = deadline - timedelta(hours=cfg.safety_hours) if deadline is not None else None
    late_window = sell_by is not None and sell_by <= now

    protected_set = set(protected_ids or ())
    protected = tuple(
        sorted(
            (player for player in squad if player.id in protected_set),
            key=lambda player: player.name,
        )
    )
    free_slots = _free_slots(squad)
    lineup_count = len(set(lineup_ids or ()))

    notes: list[str] = []
    if reserve_warning:
        notes.append(reserve_warning)

    sales: SellGuide | None = None
    deficit_stays = False
    budget_after_offers = (
        pending_offers_budget if pending_offers_budget is not None else budget - reserved
    )
    if budget_after_offers < 0:
        guide = build_sell_guide(
            budget_after_offers,
            squad,
            candidates=squad,
            weights=sell_weights,
            protected_ids=protected_set,
        )
        sales = guide
        if (
            guide is not None
            and guide.best is None
            and any(player.id in protected_set for player in squad)
        ):
            deficit_stays = True
            notes.append(
                "Defizit ist nur durch geschützte Stammspieler deckbar – "
                "die Verkäufe bleiben offen."
            )

    steps: list[PlanStep] = []
    if late_window:
        if sales is not None:
            notes.append(
                "Verkauf vor Deadline nicht mehr sicher: "
                "das Sicherheitsfenster ist bereits abgelaufen."
            )
    elif sales is not None:
        chosen = _sell_choice(sales)
        names = tuple(sorted(player.name for player in chosen.players))
        step_names = tuple(player.name for player in chosen.players)
        if deadline is not None and sell_by is not None:
            steps.append(
                PlanStep(
                    at=sell_by,
                    label="Verkäufe abschließen",
                    detail=(
                        f"{', '.join(names)} sind bis dahin verbucht "
                        f"(Deckung {chosen.total / 1_000_000:.2f} Mio. €)."
                    ),
                    players=step_names,
                )
            )
            steps.append(_lineup_step(deadline, lineup_count))
        elif sell_by is not None:
            steps.append(
                PlanStep(
                    at=sell_by,
                    label="Verkäufe abschließen",
                    detail="Kein exakter Anpfiff – so früh wie möglich verkaufen.",
                    players=step_names,
                )
            )
    elif deadline is not None:
        steps.append(_lineup_step(deadline, lineup_count))

    return DeadlinePlan(
        deadline=deadline,
        sell_by=sell_by,
        budget=budget,
        reserved_bids=reserved,
        free_slots=free_slots,
        protected_players=protected,
        sales=sales,
        steps=tuple(steps),
        late_window=late_window,
        deficit_stays_visible=deficit_stays,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------- view loader (C21.2)


@dataclass(frozen=True)
class DeadlinePlanView:
    """The schedule plus enough context for the card (freshness, matchday, lineup size)."""

    plan: DeadlinePlan
    now: datetime
    matchday: int | None
    stale: bool = False  # at least one endpoint returned cached/stale data
    fetched_at: datetime | None = None

    @property
    def lineup_count(self) -> int:
        return sum(1 for step in self.plan.steps if step.label == "Elf stellen und Gebote setzen")


def _to_sale_player(player: m.SquadPlayer) -> SalePlayer:
    """Squad player → what `build_sell_guide` expects. `out` follows Kickbase availability, not
    just the status label – an OUT availability never counts for the eleven (lineup nor sale)."""
    return SalePlayer(
        name=player.name,
        value=int(player.market_value),
        average_points=int(player.average_points),
        start_percent=None,
        position=m.Position(player.position),
        out=m.availability(player.status) is m.Availability.OUT,
        id=player.id,
    )


def _pick_deadline(market: m.Market, now: datetime) -> datetime | None:
    """Kickbase sometimes returns a past deadline (the deadline of the last matchday). Treat
    those as unknown so the plan doesn't insist on a time already gone."""
    deadline = market.deadline
    if deadline is None:
        return None
    if deadline <= now:
        return None
    return deadline


async def deadline_plan_view(
    client: KickbaseClient,
    sessions: async_sessionmaker,
    league: ResolvedLeague,
    now: datetime | None = None,
) -> DeadlinePlanView | None:
    """Collect the data the plan needs from the existing kickbase endpoints (with their caches)
    and the local holding-role table, then run the pure builder. Returns None when the squad
    could not be loaded – the card should show an unknown state, not invent a schedule."""
    now = now or datetime.now(UTC)
    market: Fetched[m.Market] = await client.market(league.id)
    squad: Fetched[m.Squad] = await client.squad(league.id)
    eleven: Fetched[m.MyEleven] = await client.my_eleven(league.id)
    budget: Fetched[m.Budget] = await client.budget(league.id)
    if not squad.data.players:
        return None
    roles = await holding_roles.roles_for(sessions, league.id)
    protected_ids = {pid for pid, role in roles.items() if role == "core"}
    own_bids = {
        p.id: int(p.my_offer)
        for p in market.data.players
        if p.my_offer is not None and int(p.my_offer) > 0
    }
    lineup_ids = [p.id for p in eleven.data.players]
    plan = build_deadline_plan(
        now=now,
        deadline=_pick_deadline(market.data, now),
        budget=int(budget.data.budget),
        pending_offers_budget=(
            int(budget.data.pending_offers_budget)
            if budget.data.pending_offers_budget is not None
            else None
        ),
        own_bids=own_bids,
        squad=[_to_sale_player(p) for p in squad.data.players],
        lineup_ids=lineup_ids,
        protected_ids=protected_ids,
    )
    stale = any(f.stale for f in (market, squad, eleven, budget))
    return DeadlinePlanView(
        plan=plan,
        now=now,
        matchday=market.data.next_matchday,
        stale=stale,
        fetched_at=max(
            (f.fetched_at for f in (market, squad, eleven, budget)),
            default=None,
        ),
    )
