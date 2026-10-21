"""Daily briefing: a few lines on what to do today, written by the language model from facts the
dashboard has already worked out.

The facts are the "Heute" box (deadline, lineup, minus, squad gaps, open bids), the buy list as
the buy page shows it, the sell candidates, kicker news and the status of own players, and the
overnight market values of the squad. Each fact is a finished German sentence with an id; the
model only picks, merges and phrases them. Every line names the facts it is based on and may only
use names, numbers and actions (buy, sell, line up) from exactly those facts. A line that breaks
this is replaced by the plain sentences of its facts, and important facts the model left out are
added – so the text can't recommend selling a player just because his market value rises, or
move an offer's end to another day. Without the model the plain sentences are sent as they are.
"""

import html
import json
import logging
import re
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import Player
from app.kickbase import models as m
from app.kickbase.client import KickbaseClient
from app.kickbase.league import ResolvedLeague
from app.llm.client import LlmClient
from app.news.service import ReportView, reports_for_players
from app.openligadb.client import OpenLigaDbClient
from app.services.recommendations import Recommendation, SellView, load_basis, sell_view
from app.services.today import TodayView, today_view
from app.web.formatting import local_time, money_short, signed_money

logger = logging.getLogger(__name__)

TOP = 3
MAX_LINES = 8
NEWS_DAYS = 7
# The deadline counts as important for the briefing when it is this close.
DEADLINE_SOON = timedelta(hours=48)
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
# Small whole numbers ("2 Spieler", "Top 3") may appear without being in the data.
FREE_NUMBER_LIMIT = 11
# Sign included: "−20" in the text must not pass for "+20" in the data. Both the ASCII minus and
# U+2212 (used by our formatting) count as minus. A sign only counts at the start of a word, so
# the hyphen in a range like "3-4" is no minus.
NUMBER = re.compile(r"(?<![\w.,])([+\-\u2212]?)(\d+(?:[.,]\d+)*)")
# A line may only recommend what its facts recommend: a buy only from a buy fact, a sale only
# from a sell fact, lineup advice only from a lineup fact.
ACTIONS = {
    "verkaufen": re.compile(r"verkauf", re.I),
    "kaufen": re.compile(r"(?<!ver)kauf|gebot|biete", re.I),
    "aufstellen": re.compile(r"aufstell|\bstelle\b|startelf|\bbank\b|einsetz|\bsetze", re.I),
}
LINEUP = frozenset({"aufstellen"})
# The link of a "Heute" item says which action it is about.
LINK_ACTIONS = {
    "verkaufen": "verkaufen",
    "kaufen": "kaufen",
    "elf": "aufstellen",
    "spieler": "aufstellen",
}

SYSTEM = """Du schreibst ein kurzes Morgen-Briefing für einen Kickbase-Manager (Fantasy-Fußball).
Die Eingabe enthält "punkte": Fakten, die das Dashboard schon berechnet hat, jeder mit einer id.
Fasse sie in höchstens 6 kurzen Zeilen zusammen, das Wichtigste zuerst.
Regeln:
- Jede Zeile nennt in "punkte" die ids, auf denen sie beruht, und verwendet nur Namen, Zahlen,
  Zeiten und Empfehlungen aus genau diesen Punkten. Übernimm sie exakt (auch "heute", "morgen"
  und Wochentage); rechne nichts aus, runde nicht, erfinde nichts.
- Punkte mit "wichtig": true müssen vorkommen.
- Empfiehl nur, was in den Punkten steht: kaufen nur Kaufempfehlungen, verkaufen nur
  Verkaufskandidaten oder Verkaufsvorschläge, Aufstellungshinweise nur aus Punkten zur
  Aufstellung. Punkte mit "nur_info": true sind keine Empfehlung – nie deshalb kaufen oder
  verkaufen.
- Bündle Zusammengehöriges in einer Zeile (z. B. alle Kaufempfehlungen). Lieber weniger Zeilen
  als Füllsätze; ist wenig los, reichen zwei oder drei.
- Deutsch, knapp wie eine Notiz, keine Begrüßung, keine Formatierung (kein Markdown, keine
  Emojis).
Beispiel für den Stil (Platzhalter in <>):
"Aufstellung: 1 Platz leer – bis <Tag Uhrzeit> auffüllen."
"Kaufen: <Name> (Gebot <Betrag>, endet <Zeit>), <Name> (Gebot <Betrag>, endet <Zeit>)."
"<Name> fällt aus (<Grund>) – nicht aufstellen."
"""


class BriefingLine(BaseModel):
    punkte: list[str]
    text: str


class BriefingText(BaseModel):
    zeilen: list[BriefingLine]


@dataclass(frozen=True)
class Fact:
    id: str
    text: str  # plain sentence: what the model rephrases, and the line without the model
    detail: str = ""  # more numbers for the model only (score components)
    important: bool = False
    # Actions a line may name besides those in the text itself (see ACTIONS): an injury report
    # may lead to "nicht aufstellen", a minus warning to "verkaufen".
    actions: frozenset[str] = frozenset()
    info_only: bool = False  # context like market values: never a reason to buy or sell

    @property
    def source(self) -> str:
        return f"{self.text}\n{self.detail}"

    def as_json(self) -> dict[str, Any]:
        item: dict[str, Any] = {"id": self.id, "text": self.text}
        if self.detail:
            item["details"] = self.detail
        if self.important:
            item["wichtig"] = True
        if self.info_only:
            item["nur_info"] = True
        return item


@dataclass(frozen=True)
class BriefingData:
    date: str
    facts: tuple[Fact, ...]
    # Player names (Bundesliga, own squad): one in an AI line must occur in that line's facts.
    names: frozenset[str] = frozenset()
    # Data-quality hints (e.g. "Programm zählt neutral"): always sent as fixed lines.
    notes: tuple[str, ...] = ()

    @property
    def payload(self) -> dict[str, Any]:
        return {"datum": self.date, "punkte": [f.as_json() for f in self.facts]}

    @property
    def payload_json(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False, separators=(",", ":"))

    @property
    def fallback_lines(self) -> tuple[str, ...]:
        return tuple(f.text for f in self.facts)


def _day(value: datetime, tz: str, now: datetime) -> str:
    """'heute 22:59', 'morgen 20:26', 'Fr. 23.10. 20:30' – the model must not guess the day."""
    zone = ZoneInfo(tz)
    local, today = value.astimezone(zone), now.astimezone(zone).date()
    if local.date() == today:
        return f"heute {local:%H:%M}"
    if local.date() == today + timedelta(days=1):
        return f"morgen {local:%H:%M}"
    return f"{WEEKDAYS[local.weekday()][:2]}. {local.day}.{local.month}. {local:%H:%M}"


def _news_text(report: ReportView, tz: str, now: datetime) -> str:
    details = ", ".join(x for x in (report.absence, report.expected_return) if x)
    when = local_time(report.published_at, tz, now)
    if report.updated_at:
        when += (
            f"; Änderung erkannt {local_time(report.updated_at, tz, now)}"
            f" · Revision {report.revision}"
        )
    text = f"{report.status_label} ({report.kind_label}"
    return text + (f": {details}" if details else "") + f"; kicker {when})"


def _components(rec: Recommendation) -> str:
    points = rec.score.shown_points
    return ", ".join(f"{c.label} {points[c.key]:+d}" for c in rec.score.components)


def _deadline_facts(today: TodayView, tz: str, now: datetime) -> list[Fact]:
    if today.deadline is None:
        return []
    matchday = f"Spieltag {today.matchday}" if today.matchday else "den nächsten Spieltag"
    when = _day(today.deadline, tz, now)
    text = f"Aufstellung für {matchday} bis {when} ({today.countdown})"
    important = today.deadline - now <= DEADLINE_SOON
    return [Fact("spieltag", text, important=important, actions=LINEUP)]


def _today_facts(today: TodayView) -> list[Fact]:
    """The "Heute" box as on the team page: problems are important, the rest is information."""
    return [
        Fact(
            f"heute_{n}",
            item.title + (f" – {item.detail}" if item.detail else ""),
            important=item.tone != "ok",
            actions=frozenset(
                LINK_ACTIONS[part]
                for part in (item.href or "").split("?")[0].split("/")
                if part in LINK_ACTIONS
            ),
        )
        for n, item in enumerate(today.items, 1)
    ]


def _budget_facts(sell: SellView) -> list[Fact]:
    text = f"Kontostand {money_short(sell.budget)}"
    if sell.budget_after_offers != sell.budget:
        text += f", nach allen offenen Geboten {money_short(sell.budget_after_offers)}"
    facts = [Fact("konto", text)]
    if sell.guide is not None:
        # The least-points-lost way when there is one, else the fewest sales.
        option = sell.guide.best or sell.guide.fewest
        players = ", ".join(f"{p.name} ({money_short(p.value)})" for p in option.players)
        facts.append(
            Fact(
                "verkaufsvorschlag",
                f"Verkaufsvorschlag gegen das Minus ({money_short(sell.guide.deficit)} fehlen): "
                f"{players}; kostet zusammen {option.lost_average} Punkte im Schnitt",
                important=True,
            )
        )
    return facts


def _buy_facts(buys: Sequence[Recommendation], tz: str, now: datetime) -> list[Fact]:
    facts = []
    for n, r in enumerate(buys[:TOP], 1):
        club = f"{r.player.team_name}, " if r.player.team_name else ""
        text = (
            f"Kaufempfehlung {n}: {r.player.name} ({club}{r.player.position.short_label}), "
            f"Preis {money_short(r.price or 0)}"
        )
        if r.bid is not None:
            text += f", Gebot {money_short(r.bid.amount)} empfohlen"
        text += f", Score {r.score.shown_total}"
        if r.expires_at is not None:
            text += f", Angebot endet {_day(r.expires_at, tz, now)}"
        facts.append(Fact(f"kauf_{n}", text, f"Score-Bestandteile: {_components(r)}"))
    return facts


def _sell_facts(sell: SellView) -> list[Fact]:
    facts = []
    for n, r in enumerate(sell.recommendations[:TOP], 1):
        club = f" ({r.player.team_name})" if r.player.team_name else ""
        reasons = f": {'; '.join(r.reasons)}" if r.reasons else ""
        facts.append(Fact(f"verkauf_{n}", f"Verkaufskandidat {n}: {r.player.name}{club}{reasons}"))
    return facts


def _squad_facts(
    squad: Sequence[m.SquadPlayer],
    news: dict[str, list[ReportView]],
    today: TodayView,
    teams: dict[str, str],
    tz: str,
    now: datetime,
) -> list[Fact]:
    facts = []
    for p in squad:
        if p.id in news:
            club = teams.get(p.team_id, p.team_id)
            text = (
                f"Meldung zu {p.name} ({club}, eigener Kader): {_news_text(news[p.id][0], tz, now)}"
            )
            facts.append(Fact(f"meldung_{len(facts) + 1}", text, important=True, actions=LINEUP))
    # Non-fit players without news; those in the lineup are already in the "Heute" box.
    shown = " ".join(item.title for item in today.items)
    unfit = [
        f"{p.name} ({p.status_label})"
        for p in squad
        if m.availability(p.status) is not m.Availability.FIT
        and p.id not in news
        and p.name not in shown
    ]
    if unfit:
        facts.append(
            Fact("kader_status", "Nicht fit im Kader: " + ", ".join(unfit), actions=LINEUP)
        )
    return facts


def _market_value_facts(squad: Sequence[m.SquadPlayer]) -> list[Fact]:
    """Overnight change of the squad's market values: information only, never an action."""
    if not squad or not any(p.market_value_change_24h for p in squad):
        return []
    total = sum(p.market_value_change_24h for p in squad)
    up = max(squad, key=lambda p: p.market_value_change_24h)
    down = min(squad, key=lambda p: p.market_value_change_24h)
    text = f"Teamwert seit gestern {signed_money(total)}"
    if up.market_value_change_24h > 0:
        text += f"; größter Anstieg {up.name} {signed_money(up.market_value_change_24h)}"
    if down.market_value_change_24h < 0:
        text += f"; größter Rückgang {down.name} {signed_money(down.market_value_change_24h)}"
    return [Fact("marktwerte", text, info_only=True)]


async def _player_names(sessions: async_sessionmaker) -> set[str]:
    async with sessions() as session:
        return set((await session.scalars(select(Player.name))).all())


def _date(now: datetime, tz: str) -> str:
    local = now.astimezone(ZoneInfo(tz))
    return f"{WEEKDAYS[local.weekday()]}, {local.day}.{local.month}."


async def collect_briefing_data(
    client: KickbaseClient,
    oldb: OpenLigaDbClient,
    sessions: async_sessionmaker,
    league: ResolvedLeague,
    tz: str,
    now: datetime,
) -> BriefingData:
    basis = await load_basis(client, oldb, sessions, league, now=now)
    today = await today_view(client, oldb, sessions, league, now, basis=basis)
    sell = await sell_view(client, league, basis, now=now)
    squad = sorted((await client.squad(league.id)).data.players, key=lambda p: (p.position, p.name))
    news = await reports_for_players(
        sessions, [p.id for p in squad], now - timedelta(days=NEWS_DAYS)
    )
    facts = [
        *_deadline_facts(today, tz, now),
        *_today_facts(today),
        *_budget_facts(sell),
        *_squad_facts(squad, news, today, basis.team_names, tz, now),
        *_buy_facts(today.buys, tz, now),
        *_sell_facts(sell),
        *_market_value_facts(squad),
    ]
    names = await _player_names(sessions)
    names |= {p.name for p in squad}
    for r in (*today.buys[:TOP], *sell.recommendations[:TOP]):
        names |= {r.player.name, *r.player.name.split()[-1:]}
    return BriefingData(_date(now, tz), tuple(facts), frozenset(names), tuple(basis.all_notes))


def _negative(sign: str) -> bool:
    return sign in ("-", "\u2212")


def _values(token: str) -> set[Decimal]:
    """The values a number may mean: German ("1.234,5", "17,00") and, with a single dot and no
    comma, English ("8.3"). Compared by value, so "17" matches "17,00"."""
    values = set()
    with suppress(InvalidOperation):
        values.add(Decimal(token.replace(".", "").replace(",", ".")))
    if token.count(".") == 1 and "," not in token:
        with suppress(InvalidOperation):
            values.add(Decimal(token))
    return values


def unknown_numbers(text: str, source: str) -> list[str]:
    """Numbers in `text` that don't occur in `source` (apart from small whole numbers),
    compared by value. An unsigned number may stand for a signed one ("8,3 %" for "+8,3 %"),
    but a sign must match."""
    known = {
        (_negative(sign), value)
        for sign, token in NUMBER.findall(source)
        for value in _values(token)
    }
    any_sign = {value for _, value in known}
    unknown = []
    for sign, token in NUMBER.findall(text):
        if not sign and token.isdigit() and int(token) <= FREE_NUMBER_LIMIT:
            continue
        values = _values(token)
        if sign:
            ok = any((_negative(sign), v) in known for v in values)
        else:
            ok = any(v in any_sign for v in values)
        if not ok:
            unknown.append(sign + token)
    return unknown


def _mentions(text: str, names: Iterable[str]) -> set[str]:
    return {n for n in names if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", text)}


def line_problem(text: str, facts: Sequence[Fact], data: BriefingData) -> str | None:
    """Why an AI line doesn't match its facts (None: it does)."""
    if not facts:
        return f"ohne gültigen Bezug: {text!r}"
    source = "\n".join([data.date, *(f.source for f in facts)])
    if unknown := unknown_numbers(text, source):
        return f"Zahlen nicht im Bezug {unknown[:3]}: {text!r}"
    if foreign := sorted(_mentions(text, data.names) - _mentions(source, data.names)):
        return f"Spieler nicht im Bezug {foreign[:3]}: {text!r}"
    allowed = {a for a, pattern in ACTIONS.items() if pattern.search(source)}
    allowed |= {a for f in facts for a in f.actions}
    for action, pattern in ACTIONS.items():
        if action not in allowed and pattern.search(text):
            return f"„{action}“ ohne Grundlage: {text!r}"
    return None


def _covers(text: str, fact: Fact, names: frozenset[str]) -> bool:
    """A referenced fact about players only counts as mentioned if one of them is named."""
    about = _mentions(fact.text, names)
    return not about or bool(_mentions(text, about))


@dataclass(frozen=True)
class Briefing:
    lines: tuple[str, ...]
    by_ai: bool
    note: str


async def write_briefing(llm: LlmClient, data: BriefingData) -> Briefing:
    """Checked lines by the model; a line that doesn't match its facts becomes their plain
    sentences, and important facts it left out are put first. Without a single usable line the
    plain briefing. Raises LlmError when the model can't be asked at all (the caller then sends
    the plain briefing)."""
    completion = await llm.complete("briefing", SYSTEM, data.payload_json, "briefing", BriefingText)
    by_id = {f.id: f for f in data.facts}
    lines: list[str] = []
    covered: set[str] = set()
    problems: list[str] = []
    accepted = 0
    for line in completion.data.zeilen[:MAX_LINES]:
        text = line.text.strip()
        if not text:
            continue
        facts = [by_id[i] for i in dict.fromkeys(line.punkte) if i in by_id]
        if problem := line_problem(text, facts, data):
            problems.append(problem)
            replacement = [f for f in facts if f.id not in covered]
            lines += [f.text for f in replacement]
            covered |= {f.id for f in replacement}
        else:
            accepted += 1
            lines.append(text)
            covered |= {f.id for f in facts if _covers(text, f, data.names)}
    if problems:
        logger.info("Briefing: %d KI-Zeile(n) ersetzt: %s", len(problems), problems)
    if not accepted:
        reason = f"KI-Text verworfen: {problems[:2]}" if problems else "KI lieferte keinen Text"
        return Briefing(data.fallback_lines, False, reason)
    missing = [f.text for f in data.facts if f.important and f.id not in covered]
    note = f"KI-Text von {completion.model}"
    if problems:
        note += f", {len(problems)} Zeile(n) ersetzt: {problems[:2]}"
    if missing:
        note += f", {len(missing)} wichtige(r) Punkt(e) ergänzt"
    return Briefing((*missing, *lines), True, note)


def compose_briefing(
    league_name: str, briefing: Briefing, notes: list[str] | tuple[str, ...], link: str
) -> str:
    lines = [f"<b>Briefing {html.escape(league_name)}</b>"]
    lines += [html.escape(line) for line in briefing.lines]
    lines += [f"⚠️ {html.escape(note)}" for note in notes]
    origin = "KI-Text aus den Dashboard-Daten" if briefing.by_ai else "ohne KI"
    lines.append(f'<i>{origin}</i> – <a href="{html.escape(link)}">Empfehlungen öffnen</a>')
    return "\n".join(lines)
