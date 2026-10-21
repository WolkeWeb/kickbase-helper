"""Deadline-Plan reminders (Block C, C21.3).

Pure rule layer for the Telegram alarm that fires while a deadline plan shows an open
deficit. Mirrors the pattern of ``app.notify.own_offers`` and ``app.notify.events``:

* ``compose_reminder`` builds the message body for the current run (or returns ``None``
  when there is nothing to alarm about)
* the worker compares the situation fingerprint against ``notify.deadline_plan.fingerprint``
  in kv and only sends when it changed
* ``commit_reminder`` records the new fingerprint after a successful send

No Kickbase or DB I/O lives here – callers pass in the ``DeadlinePlanView`` the worker
already loaded. The kv fingerprint is one of:

* ``"none"`` – neither deadline nor deficit (baseline written by the first quiet run)
* ``"solved:<deadline_iso>"`` – deadline upcoming but no deficit right now
* ``"deficit:<deadline_iso>:<bucket>:<sales>"`` – open deficit the user should act on

A solved marker persists across quiet runs and only changes when the deficit reappears
*with a different fingerprint* – a fresh deficit triggers a new alarm even when the
numbers look identical to a previous solved one.
"""

import html
from dataclasses import dataclass
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from app.services.deadline_plan import DeadlinePlan, DeadlinePlanView
from app.web.formatting import money_short

KEY = "notify.deadline_plan.fingerprint"  # last fired (or solved) situation
NO_FIRE = "none"
SOLVED_PREFIX = "solved:"
DEFICIT_PREFIX = "deficit:"
# Coarse bucket so a small day-to-day budget swing does not fire a fresh alarm for
# essentially the same situation (~0.25 Mio. – below Kickbase's smallest listable sale).
# Integer floor division (`//`) so a deficit just below the bucket boundary stays in the
# lower bucket instead of leaking into the next one and triggering a fresh alarm.
_DEFICIT_BUCKET_EUR = 250_000


class KeyValue(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any) -> None: ...


@dataclass(frozen=True)
class Reminder:
    """One Telegram message plus the fingerprint it represents."""

    key: str
    text: str


def _has_open_deficit(plan: DeadlinePlan) -> bool:
    """True while the plan recommends *covering* sales. `deficit_stays_visible` means the only
    way out would touch a protected core player – it is still a deficit we owe the user a
    reminder about."""
    return plan.sales is not None and (plan.sales.best is not None or plan.deficit_stays_visible)


def _deficit_amount(plan: DeadlinePlan) -> int:
    if plan.sales is None:
        return 0
    return plan.sales.deficit


def _sales_fingerprint(plan: DeadlinePlan) -> tuple[str, ...]:
    if plan.sales is None or plan.sales.best is None:
        return ()
    return tuple(sorted(player.id or player.name for player in plan.sales.best.players))


def situation_key(plan: DeadlinePlan) -> str:
    """Stable representation of (deadline, deficit bucket, recommended sales). Returned for
    both open-deficit and solved cases so the worker uses one comparison path."""
    if plan.deadline is None:
        return NO_FIRE
    if not _has_open_deficit(plan):
        return f"{SOLVED_PREFIX}{plan.deadline.isoformat()}"
    bucket = (_deficit_amount(plan) // _DEFICIT_BUCKET_EUR) * _DEFICIT_BUCKET_EUR
    sales_ids = ",".join(_sales_fingerprint(plan))
    return f"{DEFICIT_PREFIX}{plan.deadline.isoformat()}:{bucket}:{sales_ids}"


def compose_reminder(
    view: DeadlinePlanView, league_name: str, tz: ZoneInfo, base_url: str
) -> Reminder | None:
    """Build the message body for one deadline-plan alarm. Returns ``None`` when the plan has
    no deadline at all – callers should bail out, nothing to remind about."""
    plan = view.plan
    if plan.deadline is None:
        return None
    deadline_local = plan.deadline.astimezone(tz)
    deadline_label = deadline_local.strftime("%d.%m. %H:%M")
    deficit = _deficit_amount(plan)
    sales_names = (
        [player.name for player in plan.sales.best.players]
        if plan.sales is not None and plan.sales.best is not None
        else []
    )
    lines: list[str] = [
        f"<b>Deadline-Plan {html.escape(league_name)}</b>",
        f"Anpfiff {deadline_label} – Defizit {money_short(deficit)}.",
    ]
    if sales_names:
        lines.append(
            "Vorschlag: " + ", ".join(html.escape(name) for name in sales_names) + " verkaufen."
        )
    if plan.sell_by is not None:
        sell_label = plan.sell_by.astimezone(tz).strftime("%d.%m. %H:%M")
        lines.append(f"Verkauf bis {sell_label} verbucht.")
    if plan.late_window:
        lines.append("⚠️ Verkauf vor Deadline nicht mehr sicher.")
    if plan.deficit_stays_visible:
        lines.append("⚠️ Defizit nur durch geschützte Stammspieler deckbar – Verkauf bleibt offen.")
    lines.append(f'<a href="{base_url}/deadline_plan">Plan öffnen</a>')
    return Reminder(key=situation_key(plan), text="\n".join(lines))


async def should_remind(kv: KeyValue, current_key: str) -> bool:
    """Return ``True`` when the current situation differs from the previously recorded one."""
    previous = await kv.get(KEY)
    return previous != current_key


async def commit_reminder(kv: KeyValue, key: str) -> None:
    """Persist the new fingerprint after the message went out. Call only on success – a failed
    send keeps the old fingerprint so the next run retries the same alarm."""
    await kv.set(KEY, key)


async def read_fingerprint(kv: KeyValue) -> str | None:
    """Read the last recorded fingerprint, for tests and diagnostics."""
    return await kv.get(KEY)
