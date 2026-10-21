"""Changes worth a message: big daily market value moves and status changes of own players."""

import html
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.kickbase import models as m
from app.web.formatting import money_short, percent

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "notifications.yaml"
STATUS_KEY = "notify.status"  # last known status per own player
SENT_KEY = "notify.sent"  # event keys already sent -> ISO date (kept for two weeks)
KEEP_SENT = timedelta(days=14)


class MarketValueRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    min_percent: float = Field(gt=0, le=100)
    min_amount: int = Field(gt=0)


class WatchlistRule(BaseModel):
    """One on/off switch for a watchlist alarm type (Block C, C1.3)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True


class WatchlistConfig(BaseModel):
    """Settings for `config/notifications.yaml` → `watchlist:`."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    listing: WatchlistRule = Field(default_factory=WatchlistRule)
    price_below: WatchlistRule = Field(default_factory=WatchlistRule)
    status_changes: WatchlistRule = Field(default_factory=WatchlistRule)
    lineup_dropped: WatchlistRule = Field(default_factory=WatchlistRule)

    @field_validator("listing", "price_below", "status_changes", "lineup_dropped", mode="before")
    @classmethod
    def _short_bool(cls, value: Any) -> Any:
        # YAML shortcut: bare `true` / `false` instead of `{enabled: ...}`.
        if isinstance(value, bool):
            return {"enabled": value}
        return value


class OwnOffersRule(BaseModel):
    """Settings for `config/notifications.yaml` → `own_offers:` (Block C, C2).

    The minimum premium is the percentage above the market value a bid must clear to fire.
    `0` (the default) means *any* positive premium is worth a Telegram message; raise it to
    filter out lowball bids the manager does not need to react to."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True
    min_premium_pct: float = Field(default=0, ge=0)

    @field_validator("min_premium_pct", mode="before")
    @classmethod
    def _coerce_pct(cls, value: Any) -> Any:
        # YAML may send "10" as a string (no `%`); the field is always numeric in the API.
        return float(value) if value not in (None, "") else value


class MatchdayReportRule(BaseModel):
    """Settings for `config/notifications.yaml` → `matchday_report:` (Block C, C3.3).

    The Telegram alarm fires after the matchday finishes (Kickbase ranking has a new
    latest_matchday *and* OpenLigaDB confirms every match is over). The row in
    `matchday_results` is always written – only the message itself is gated by `enabled`."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True


class DeadlinePlanRule(BaseModel):
    """Settings for `config/notifications.yaml` → `deadline_plan:` (Block C, C21.3).

    Telegram reminder for an open deficit before the lineup deadline (hours ahead defined by
    `trading.close_hours`). The job is fully driven by the existing `deadline_plan` service:
    nothing fires while the budget is healthy, the alarm re-arms only when the (deadline,
    deficit bucket, recommended sales) fingerprint changes – a solved deficit clears the
    fingerprint so a fresh deficit triggers a new message."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = True


class NotificationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    market_value: MarketValueRule
    status_changes: bool = True
    watchlist: WatchlistConfig = Field(default_factory=WatchlistConfig)
    own_offers: OwnOffersRule = Field(default_factory=OwnOffersRule)
    matchday_report: MatchdayReportRule = Field(default_factory=MatchdayReportRule)
    deadline_plan: DeadlinePlanRule = Field(default_factory=DeadlinePlanRule)


@lru_cache
def load_notification_config(path: Path = CONFIG_PATH) -> NotificationConfig:
    return NotificationConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class KeyValue(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any) -> None: ...


@dataclass(frozen=True)
class Event:
    key: str  # identifies the event, so it is sent only once
    text: str  # one HTML line for Telegram


def _player_link(player: m.SquadPlayer, base_url: str) -> str:
    return f'<a href="{base_url}/spieler/{player.id}">{html.escape(player.name)}</a>'


def market_value_events(
    squad: list[m.SquadPlayer], rule: MarketValueRule, base_url: str
) -> list[Event]:
    events = []
    for player in squad:
        change = player.market_value_change_24h
        before = player.market_value - change
        pct = change / before * 100 if before > 0 else None
        big_relative = pct is not None and abs(pct) >= rule.min_percent
        if not change or not (big_relative or abs(change) >= rule.min_amount):
            continue
        arrow = "📈" if change > 0 else "📉"
        relative = f"{percent(pct)} " if pct is not None else ""
        events.append(
            Event(
                # Built from the values, not the clock: the same jump is the same event whether
                # the evening or the next morning's run sees it, even if Kickbase updates late.
                key=f"mv:{player.id}:{player.market_value}:{change}",
                text=(
                    f"{arrow} {_player_link(player, base_url)}: Marktwert {relative}"
                    f"({money_short(change, signed=True)}) auf {money_short(player.market_value)}"
                ),
            )
        )
    return events


def status_events(
    squad: list[m.SquadPlayer], previous: dict[str, int], today: date, base_url: str
) -> list[Event]:
    events = []
    for player in squad:
        old = previous.get(player.id)
        if old is None or old == player.status:
            continue  # new in the squad or unchanged
        icon = "✅" if player.availability is m.Availability.FIT else "🚑"
        events.append(
            Event(
                key=f"status:{player.id}:{old}>{player.status}:{today.isoformat()}",
                text=(
                    f"{icon} {_player_link(player, base_url)}: "
                    f"{m.status_label(old)} → {player.status_label}"
                ),
            )
        )
    return events


@dataclass(frozen=True)
class Collected:
    events: list[Event]
    # Status of every own player now; saved with `commit` once the message went out, so a
    # failed send does not lose a status change.
    statuses: dict[str, int]


async def collect_events(
    squad: list[m.SquadPlayer],
    kv: KeyValue,
    config: NotificationConfig,
    today: date,
    base_url: str,
) -> Collected:
    events = market_value_events(squad, config.market_value, base_url)
    previous = await kv.get(STATUS_KEY)
    if config.status_changes and previous is not None:
        events += status_events(squad, previous, today, base_url)
    sent = await kv.get(SENT_KEY) or {}
    return Collected(
        events=[event for event in events if event.key not in sent],
        statuses={p.id: p.status for p in squad},
    )


async def commit(kv: KeyValue, collected: Collected, today: date) -> None:
    """Record sent events and the new status baseline (call after a successful send, or when
    there was nothing to send)."""
    sent = await kv.get(SENT_KEY) or {}
    sent.update({event.key: today.isoformat() for event in collected.events})
    cutoff = (today - KEEP_SENT).isoformat()
    await kv.set(SENT_KEY, {key: day for key, day in sent.items() if day >= cutoff})
    await kv.set(STATUS_KEY, collected.statuses)


def compose_message(league_name: str, events: list[Event], limit: int = 3500) -> str:
    """One line per event. Too many events are cut by whole lines (never inside a tag)."""
    lines = [f"<b>{html.escape(league_name)}</b>"]
    for index, event in enumerate(events):
        if sum(len(line) + 1 for line in lines) + len(event.text) > limit:
            lines.append(f"… und {len(events) - index} weitere")
            break
        lines.append(event.text)
    return "\n".join(lines)
