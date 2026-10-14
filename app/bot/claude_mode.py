"""Claude im Telegram-Chat: `@claude …` oder `/claude …` öffnet ein Zeitfenster, in dem alle
Nachrichten an Claude (über die Bridge auf der VM) statt an das normale KI-Modell gehen.

Claude bekommt Websuche und die lesenden Kickbase-Werkzeuge des Workers (`app/bot/mcp_server.py`).
`/claude_model <name>` wechselt das Modell, `/claude_aus` beendet das Fenster vorzeitig.
"""

import asyncio
import logging
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.bot.context import build_situation
from app.bot.conversation import KICKBASE_RULES
from app.bot.tools import ToolContext
from app.llm.claude_bridge import ClaudeBridgeError
from app.llm.config import ClaudeConfig, load_ai_config

logger = logging.getLogger("app.bot.claude")

STATE_KEY = "bot.claude"
MODEL_KEY = "bot.claude_model"
QUESTIONS_KEY = "bot.claude_questions"
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
CHUNK = 3500  # below Telegram's 4096 incl. HTML tags and footer
TYPING_EVERY = 4.0
_clock = time.monotonic  # replaced in tests

MODEL_CMD = re.compile(r"^/claude[_-]model(?:@\w+)?(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
STOP_CMD = re.compile(r"^/claude[_-](?:aus|off|stop)(?:@\w+)?\s*$", re.IGNORECASE)
START = re.compile(
    r"^(?:/claude(?![\w-])(?:@\w+)?|@claude\b[,:]?)\s*(.*)$", re.IGNORECASE | re.DOTALL
)
CUSTOM_MODEL = re.compile(r"claude-[a-z0-9.\-\[\]]{1,60}")

SYSTEM = (
    "Du bist Claude und hilfst dem Nutzer im Telegram-Chat bei seinem Kickbase-Team. "
    + KICKBASE_RULES
    + """

Werkzeuge:
- Die Kickbase-Werkzeuge (mcp__kickbase__lagebild, spieler, markt, mein_kader, budget) liefern
  die Zahlen aus dem Dashboard des Nutzers (Kontostand, Kader, Scores, Markt). Für Fragen zu
  seinem Team zuerst lagebild aufrufen; Zahlen nie schätzen, sondern dort nachsehen.
- Mit WebSearch/WebFetch suchst du aktuelle Nachrichten, Verletzungen, voraussichtliche
  Aufstellungen und Quoten. Nenne die Quellen als URL am Ende.
- Du kannst in Kickbase nichts ändern (keine Gebote, Verkäufe, Aufstellung). Sag dem Nutzer
  stattdessen, was er in der App tun soll.

Format (Telegram): kurze Absätze, Aufzählungen mit "- ", **fett** nur für Spielernamen; keine
Überschriften, Tabellen oder Codeblöcke; Links als nackte URL. Antworte auf Deutsch und knapp
(meist unter 150 Wörtern), bei Recherchen darf es länger sein. Am Anfang jeder Nachricht steht
das aktuelle Datum mit Uhrzeit."""
)


@dataclass
class Handled:
    """What polling sends. `fallback_question` is set when Claude failed and the normal AI
    should answer the question instead."""

    replies: list[str]
    fallback_question: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


async def maybe_handle(ctx, text: str, now: datetime | None = None) -> Handled | None:
    """Handle `text` if it is for Claude (a command or inside an active window), else None."""
    now = now or _now()
    text = text.strip()
    if match := MODEL_CMD.match(text):
        return Handled([await _model_command(ctx, (match.group(1) or "").strip())])
    if STOP_CMD.match(text):
        state = await _state(ctx, now)
        await ctx.kv.set(STATE_KEY, None)
        return Handled(["Claude-Modus beendet." if state else "Claude-Modus war nicht aktiv."])
    start = START.match(text)
    state = await _state(ctx, now)
    if start is None and state is None:
        return None
    if ctx.claude is None:
        if start is None:
            # Leftover window from before the bridge was switched off: back to the normal AI.
            await ctx.kv.set(STATE_KEY, None)
            return None
        return Handled(
            [
                "Claude ist nicht eingerichtet "
                "(Bridge-Token fehlt, siehe docs/maintainer/deployment.md)."
            ]
        )
    cfg = load_ai_config().claude
    question = start.group(1).strip() if start else text
    if start and state is None:
        state = {"session_id": None}
    if not question:
        await _save_state(ctx, cfg, state, now)
        until = _until(cfg, now).astimezone(ctx.tz).strftime("%H:%M")
        model = await current_model(ctx, cfg)
        greeting = f"🤖 Claude ({model}) hört bis {until} mit. Was möchtest du wissen?"
        return Handled([f"{greeting} /claude_aus beendet."])
    return await _ask(ctx, cfg, state, question, now)


async def _ask(ctx, cfg: ClaudeConfig, state: dict, question: str, now: datetime) -> Handled:
    today = now.astimezone(ctx.tz).date().isoformat()
    key = f"{QUESTIONS_KEY}.{today}"
    used = await ctx.kv.get(key)
    used = used if isinstance(used, int) else 0
    if used >= cfg.questions_per_day:
        return Handled(
            ["Das Claude-Kontingent für heute ist aufgebraucht – ich frage die normale KI."],
            fallback_question=question,
        )
    model = await current_model(ctx, cfg)
    session_id = state.get("session_id")
    prompt = await _prompt(ctx, question, now, first=session_id is None)
    typing = asyncio.create_task(_keep_typing(ctx))
    started = _clock()
    try:
        reply = await ctx.claude.chat(
            prompt,
            model=model,
            session_id=session_id,
            system=SYSTEM,
            max_budget_usd=cfg.max_budget_usd_per_question,
        )
    except ClaudeBridgeError as exc:
        logger.warning("Claude nicht erreichbar: %s", exc)
        return Handled(
            [f"Claude gerade nicht verfügbar ({exc}) – die normale KI antwortet."],
            fallback_question=question,
        )
    finally:
        typing.cancel()
        with suppress(asyncio.CancelledError):
            await typing
    # The window counts from the answer, not the question (Claude may take minutes).
    now = now + timedelta(seconds=_clock() - started)
    await ctx.kv.set(key, used + 1)
    state = {**state, "session_id": reply.session_id or session_id}
    await _save_state(ctx, cfg, state, now)
    until = _until(cfg, now).astimezone(ctx.tz).strftime("%H:%M")
    footer = f"🤖 Claude · {reply.model} · aktiv bis {until} · /claude_aus beendet"
    logger.info(
        "Claude-Antwort (%s, %d Zeichen, %s USD)", reply.model, len(reply.text), reply.cost_usd
    )
    return Handled(split_message(f"{reply.text}\n\n{footer}"))


async def _prompt(ctx, question: str, now: datetime, *, first: bool) -> str:
    local = now.astimezone(ctx.tz)
    stamp = f"{WEEKDAYS[local.weekday()]}, {local:%d.%m.%Y %H:%M}"
    parts = [f"[Jetzt: {stamp} ({ctx.tz.key})]"]
    if first:
        situation = await _situation(ctx, now)
        if situation:
            parts.append(f"LAGEBILD (Stand jetzt, aus dem Dashboard):\n{situation}")
    parts.append(f"Frage: {question}")
    return "\n\n".join(parts)


async def _situation(ctx, now: datetime) -> str | None:
    """The Lagebild for the first question; Claude can still use the tools if it fails."""
    try:
        league = await ctx.league.get()
        tool_ctx = ToolContext(ctx.client, ctx.sessions, league, ctx.oldb, now)
        return (await build_situation(tool_ctx, ctx.tz)).text
    except Exception as exc:
        logger.warning("Lagebild für Claude fehlgeschlagen: %s", type(exc).__name__)
        return None


async def _keep_typing(ctx) -> None:
    notifier = ctx.notifier
    if notifier is None:
        return
    while True:
        await notifier.send_typing()
        await asyncio.sleep(TYPING_EVERY)


async def _model_command(ctx, arg: str) -> str:
    cfg = load_ai_config().claude
    current = await current_model(ctx, cfg)
    if not arg:
        options = ", ".join(cfg.models)
        return (
            f"Claude-Modell: **{current}**\nZur Wahl: {options} (oder eine volle Modell-ID "
            "wie claude-opus-5-5).\nWechseln: /claude_model opus"
        )
    choice = arg.split()[0].lower()
    if choice not in cfg.models and not CUSTOM_MODEL.fullmatch(choice):
        return f"Unbekanntes Modell '{choice[:40]}'. Zur Wahl: {', '.join(cfg.models)}."
    await ctx.kv.set(MODEL_KEY, choice)
    return f"Claude-Modell ist jetzt **{choice}**."


async def current_model(ctx, cfg: ClaudeConfig) -> str:
    stored = await ctx.kv.get(MODEL_KEY)
    if isinstance(stored, str) and (stored in cfg.models or CUSTOM_MODEL.fullmatch(stored)):
        return stored
    return cfg.default_model


async def _state(ctx, now: datetime) -> dict | None:
    """The active window, or None if there is none or it has expired."""
    stored = await ctx.kv.get(STATE_KEY)
    if not isinstance(stored, dict):
        return None
    try:
        until = datetime.fromisoformat(stored["until"])
    except (KeyError, TypeError, ValueError):
        return None
    if until <= now:
        return None
    session = stored.get("session_id")
    return {"session_id": session if isinstance(session, str) else None}


async def _save_state(ctx, cfg: ClaudeConfig, state: dict, now: datetime) -> None:
    until = _until(cfg, now)
    await ctx.kv.set(STATE_KEY, {"until": until.isoformat(), "session_id": state.get("session_id")})


def _until(cfg: ClaudeConfig, now: datetime) -> datetime:
    return now + timedelta(minutes=cfg.window_minutes)


def split_message(text: str, limit: int = CHUNK) -> list[str]:
    """Split at paragraph, then line, then hard boundaries into chunks of at most `limit`."""
    chunks: list[str] = []
    rest = text.strip()
    while len(rest) > limit:
        cut = rest.rfind("\n\n", 0, limit + 1)
        if cut < limit // 2:
            cut = rest.rfind("\n", 0, limit + 1)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit + 1)
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks
