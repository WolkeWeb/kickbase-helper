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
import unicodedata
from collections.abc import Iterable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from itertools import pairwise
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
from app.web.formatting import money_short, signed_money

logger = logging.getLogger(__name__)

TOP = 3
MAX_LINES = 8
MESSAGE_LIMIT = 3500  # Telegram allows 4096; cut by whole lines well before
MORE = "… und {} weitere Punkte im Dashboard"
# A line citing more facts than this is no summary but a way around the per-line check.
MAX_FACTS_PER_LINE = 5
NEWS_DAYS = 7
# The deadline counts as important for the briefing when it is this close.
DEADLINE_SOON = timedelta(hours=48)
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
# Small whole numbers ("2 Spieler", "Top 3") may appear without being in the data.
FREE_NUMBER_LIMIT = 11
# Sign included: "−20" in the text must not pass for "+20" in the data. The ASCII minus, U+2212
# (used by our formatting) and the en dash (often written by models) count as minus. A sign only
# counts at the start of a word, so the dash in a range like "3-4" or "3–4" is no minus.
NUMBER = re.compile(r"(?<![\w.,])([+\-\u2212\u2013]?)(\d+(?:[.,]\d+)*)")
# Amounts, percentages and scores must match even when small: "6 Mio." is not "5,6 Mio.".
STRICT_NUMBER = re.compile(
    r"(?<![\w.,])[+\-\u2212\u2013]?\d+(?:[.,]\d+)*(?=\s*(?:Mio|Tsd|%|€|Punkt|Pkt))"
    r"|(?<=Score )\d+"
)
TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
# "heute Morgen" and "Morgen-…" are no day; abbreviations only before a date ("Fr. 9.10.").
DAY = re.compile(
    r"(?i:\b(?:heute|übermorgen|gestern|montag|dienstag|mittwoch|donnerstag|freitag|samstag"
    r"|sonntag)\b|(?<!heute )(?<!am )\bmorgen\b(?!-))"
    r"|\b(?:Mo|Di|Mi|Do|Fr|Sa|So)\.(?=\s*\d)"
)
DAY_TIME = re.compile(
    "(" + DAY.pattern + r")\s+(?:um\s+)?(?:\d{1,2}\.\d{1,2}\.\s+)?(?:um\s+)?(\d{1,2}):(\d{2})"
)
# Clauses of a line; after a dash the clause is still about the players before it.
CLAUSE = re.compile(r"(;|\n|\.\s+(?=[A-ZÄÖÜ])|\s[–—-]\s)")
DASHES = frozenset("–—-")
# A line may only recommend what its facts recommend: a buy only from a buy fact, a sale only
# from a sell fact, lineup advice only from a lineup fact.
ACTIONS = {
    "verkaufen": re.compile(
        r"verk[aä]uf|abstoß|abgeben|\bloswerd|\bloszuwerd|gewinn\w*\s+mitnehm|zu geld machen",
        re.I,
    ),
    "kaufen": re.compile(r"(?<!ver)k[aä]uf|gebot|biete|\bhol(?:en|e)\b|verpflicht", re.I),
    "aufstellen": re.compile(
        r"aufstell|aufgestellt|\bstell\w*\s+(?:\S+\s+){0,3}auf\b|startelf|\bin die elf\b"
        r"|\bbank\b|einsetz|eingesetzt|\bsetz\w*\s+(?:\S+\s+){0,3}ein\b|nominier|ersetz"
        r"|spielen lassen",
        re.I,
    ),
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
- Schreib Zahlen und Zeiten direkt hinter den Spieler, zu dem sie gehören; verschiedene
  Handlungen (kaufen, verkaufen, aufstellen) trennst du mit Semikolon.
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
    for offset, word in ((0, "heute"), (1, "morgen"), (-1, "gestern")):
        if local.date() == today + timedelta(days=offset):
            return f"{word} {local:%H:%M}"
    return f"{WEEKDAYS[local.weekday()][:2]}. {local.day}.{local.month}. {local:%H:%M}"


def _news_text(report: ReportView, tz: str, now: datetime) -> str:
    details = ", ".join(x for x in (report.absence, report.expected_return) if x)
    when = _day(report.published_at, tz, now)
    if report.updated_at:
        when += (
            f"; Änderung erkannt {_day(report.updated_at, tz, now)} · Revision {report.revision}"
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
        and not _mentions(shown, {p.name})
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
    # Last names too: the model writes "Kane", not always "Harry Kane".
    names = await _player_names(sessions)
    for name in (
        *(p.name for p in squad),
        *(r.player.name for r in (*today.buys[:TOP], *sell.recommendations[:TOP])),
    ):
        names |= {name, *name.split()[-1:]}
    return BriefingData(_date(now, tz), tuple(facts), frozenset(names), tuple(basis.all_notes))


def _negative(sign: str) -> bool:
    return sign in ("-", "\u2212", "\u2013")


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


def unknown_numbers(text: str, source: str, *, free: int = FREE_NUMBER_LIMIT) -> list[str]:
    """Numbers in `text` that don't occur in `source` (apart from whole numbers up to `free`),
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
        if not sign and token.isdigit() and int(token) <= free:
            continue
        values = _values(token)
        if sign:
            ok = any((_negative(sign), v) in known for v in values)
        else:
            ok = any(v in any_sign for v in values)
        if not ok:
            unknown.append(sign + token)
    return unknown


def _fold(text: str) -> str:
    """Without accents and of the same length: "Conté" → "Conte"."""
    return "".join(unicodedata.normalize("NFKD", c)[0] for c in text)


@lru_cache(maxsize=4096)
def _name_pattern(name: str) -> re.Pattern[str]:
    # Genitive and apostrophe forms count as the name: "Musialas Startchance", "Kane's".
    return re.compile(rf"(?<!\w){re.escape(_fold(name))}(?:s|'s|’s|'|’)?(?!\w)")


def _mentions(text: str, names: Iterable[str]) -> set[str]:
    folded = _fold(text)
    return {n for n in names if _name_pattern(n).search(folded)}


def _spans(text: str, names: Iterable[str]) -> list[tuple[int, str]]:
    """Start and name of each player named in `text`, in order; a name inside a longer one
    ("Kane" in "Harry Kane") is the longer one."""
    folded = _fold(text)
    found = sorted(
        (m.start(), -m.end(), n) for n in names for m in _name_pattern(n).finditer(folded)
    )
    spans: list[tuple[int, str]] = []
    end = -1
    for start, negative_end, name in found:
        if start >= end:
            spans.append((start, name))
            end = -negative_end
    return spans


def _day_key(word: str) -> str:
    """ "Fr.", "Freitag" and "freitags" are the same day."""
    word = word.lower().rstrip(".")
    if word in ("heute", "morgen", "übermorgen", "gestern"):
        return word
    return next(day for day in WEEKDAYS if word[:2] == day[:2].lower())


def _times(text: str) -> set[tuple[int, int]]:
    return {(int(h), int(minute)) for h, minute in TIME.findall(text)}


def _day_times(text: str) -> set[tuple[str, int, int]]:
    return {(_day_key(d), int(h), int(minute)) for d, h, minute in DAY_TIME.findall(text)}


def _wrong_values(text: str, source: str) -> list[str]:
    """Numbers, times and days in `text` that `source` doesn't have."""
    wrong = [f"{h}:{minute:02d}" for h, minute in sorted(_times(text) - _times(source))]
    wrong += [f"{d} {h}:{m:02d}" for d, h, m in sorted(_day_times(text) - _day_times(source))]
    # "heute" alone is a common word; with a time it is checked above.
    days = {_day_key(d) for d in DAY.findall(text)} - {"heute"}
    wrong += sorted(days - {_day_key(d) for d in DAY.findall(source)})
    numbers = TIME.sub(" ", text)
    wrong += unknown_numbers(numbers, source)
    wrong += unknown_numbers(" ".join(STRICT_NUMBER.findall(numbers)), source, free=-1)
    return list(dict.fromkeys(wrong))


def _actions(facts: Iterable[Fact]) -> set[str]:
    """Actions these facts allow; context facts like market values allow none."""
    allowed: set[str] = set()
    for f in facts:
        if not f.info_only:
            allowed |= f.actions | {a for a, pattern in ACTIONS.items() if pattern.search(f.source)}
    return allowed


def line_problem(text: str, facts: Sequence[Fact], data: BriefingData) -> str | None:
    """Why an AI line doesn't match its facts (None: it does).

    Clause by clause: values next to a player only from the facts about him (and those about no
    player), so bids or times of two players can't swap; an action only if the facts about every
    player named with it allow it, so "Verkaufe X" needs a sell fact about X."""
    if not facts:
        return f"ohne gültigen Bezug: {text!r}"
    if len(facts) > MAX_FACTS_PER_LINE:
        return f"zu viele Bezüge ({len(facts)}): {text!r}"
    sources = "\n".join(f.source for f in facts)
    if foreign := sorted(_mentions(text, data.names) - _mentions(sources, data.names)):
        return f"Spieler nicht im Bezug {foreign[:3]}: {text!r}"
    about = {f.id: _mentions(f.text, data.names) for f in facts}

    def source_for(who: set[str]) -> str:
        relevant = [f for f in facts if not who or not about[f.id] or about[f.id] & who]
        return "\n".join([data.date, *(f.source for f in relevant)])

    def allowed_for(names: set[str]) -> set[str]:
        return set.intersection(*(_actions(f for f in facts if n in about[f.id]) for n in names))

    in_line = {n for _, n in _spans(text, data.names)}
    parts = CLAUSE.split(text)
    named: set[str] = set()
    for clause, separator in zip(parts[::2], ["", *parts[1::2]], strict=True):
        spans = _spans(clause, data.names)
        named = {n for _, n in spans} or (named if separator.strip() in DASHES else set())
        cuts = [0, *(start for start, _ in spans), len(clause)]
        for (a, b), who in zip(pairwise(cuts), [named, *({n} for _, n in spans)], strict=True):
            if wrong := _wrong_values(clause[a:b], source_for(who)):
                return f"Werte nicht im Bezug {wrong[:3]}: {text!r}"
        if named:
            allowed = allowed_for(named)
        elif in_line:
            allowed = _actions(f for f in facts if not about[f.id]) | allowed_for(in_line)
        else:
            allowed = _actions(facts)
        for action, pattern in ACTIONS.items():
            if action not in allowed and pattern.search(clause):
                return f"„{action}“ ohne Grundlage: {text!r}"
    return None


def _covers(text: str, fact: Fact, names: frozenset[str]) -> bool:
    """Whether a line that cites `fact` states it: one of its players is named; without players
    its times, else one of its numbers occur – "Aufstellung vollständig" doesn't state
    "2 Plätze leer"."""
    if about := _mentions(fact.text, names):
        return bool(_mentions(text, about))
    if times := _times(fact.text):
        return times <= _times(text)
    numbers = {v for _, token in NUMBER.findall(fact.text) for v in _values(token)}
    return not numbers or bool(numbers & {v for _, t in NUMBER.findall(text) for v in _values(t)})


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
    """Telegram HTML; lines beyond the length limit are cut as a whole so notes and the footer
    with its link stay intact (Telegram cuts at 4096 characters, tags and all)."""
    origin = "KI-Text aus den Dashboard-Daten" if briefing.by_ai else "ohne KI"
    head = [f"<b>Briefing {html.escape(league_name)}</b>"]
    tail = [f"⚠️ {html.escape(note)}" for note in notes]
    tail.append(f'<i>{origin}</i> – <a href="{html.escape(link)}">Empfehlungen öffnen</a>')
    more = len(MORE.format(len(briefing.lines))) + 1
    room = MESSAGE_LIMIT - more - sum(len(x) + 1 for x in (*head, *tail))
    body: list[str] = []
    for n, line in enumerate(briefing.lines):
        text = html.escape(line)
        if len(text) + 1 > room:
            body.append(MORE.format(len(briefing.lines) - n))
            break
        body.append(text)
        room -= len(text) + 1
    return "\n".join([*head, *body, *tail])
