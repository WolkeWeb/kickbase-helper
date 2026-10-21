import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import InterfaceError, OperationalError, SQLAlchemyError

from app.auth import require_csrf_header, require_login
from app.db.stores import KeyValueStore
from app.kickbase.errors import (
    KickbaseAuthError,
    KickbaseCredentialsMissing,
    KickbaseError,
    KickbaseHTTPError,
    KickbaseLoginFailed,
    KickbaseUnavailable,
    ReadOnlyViolation,
)
from app.kickbase.league import LeagueNotFound, ResolvedLeague
from app.llm.errors import LlmError
from app.news.service import (
    ReportView,
    feed_checked_at,
    headlines_for_players,
    news_problem,
    reports_for_players,
)
from app.services import holding_roles, watchlist
from app.services.daily_trends import load_trends
from app.services.deadline_plan import deadline_plan_view
from app.services.decision import player_decision
from app.services.live_matchday import load_live_matchday
from app.services.matchday_history import load_matchday_history
from app.services.offers import offer_stats
from app.services.player_ai import summarize
from app.services.players import load_market_listing, load_player, search_players
from app.services.recommendations import (
    BUY_SORTS,
    BuyFilters,
    buy_view,
    eleven_view,
    load_basis,
    select_buys,
    sell_view,
)
from app.services.team import load_team
from app.services.today import today_view
from app.services.track_record import track_record
from app.services.trading import trading_view
from app.services.transfer_profit import load_profit
from app.services.transfer_simulation import simulation_view
from app.services.transfers import load_bid_status
from app.web.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_login)])

POSITION_FILTERS = ((0, "Alle"), (1, "TW"), (2, "ABW"), (3, "MF"), (4, "ST"))
# The player list warns when the daily sync has not run for this long.
PLAYER_DATA_MAX_AGE = timedelta(hours=36)
# News shown on the team page (latest per player) and on a player's page.
TEAM_NEWS_PERIOD = timedelta(days=7)
PLAYER_NEWS_PERIOD = timedelta(days=30)


def _partial_request(request: Request) -> bool:
    """An htmx swap; a history restore (Back after a cache miss) needs the whole page."""
    headers = request.headers
    return bool(headers.get("HX-Request")) and not headers.get("HX-History-Restore-Request")


def describe_error(exc: KickbaseError) -> str:
    """User-facing explanation of what went wrong and what helps."""
    if isinstance(exc, KickbaseCredentialsMissing):
        return "Die Kickbase-Zugangsdaten fehlen auf dem Server (Docker Secrets)."
    if isinstance(exc, KickbaseLoginFailed):
        return "Kickbase hat den Login abgelehnt. Zugangsdaten in den Docker Secrets prüfen."
    if isinstance(exc, KickbaseAuthError):
        return f"Kickbase lehnt den Zugang ab. {exc}"
    if isinstance(exc, LeagueNotFound):
        return str(exc)
    if isinstance(exc, KickbaseUnavailable):
        return "Kickbase ist gerade nicht erreichbar. Versuch es in ein paar Minuten noch einmal."
    return "Die Daten von Kickbase konnten nicht geladen werden. Details stehen im Server-Log."


def describe_db_error(exc: SQLAlchemyError, what: str) -> str:
    # SQL parameters are hidden by the engine, so the driver message is safe to log.
    logger.error(
        "Datenbankfehler (%s): %s: %s", what, type(exc).__name__, getattr(exc, "orig", exc)
    )
    if isinstance(exc, (OperationalError, InterfaceError)):
        return "Die Datenbank ist gerade nicht erreichbar. Details stehen im Server-Log."
    return f"Datenbankfehler beim Laden {what}. Details stehen im Server-Log."


async def _guarded[T](
    request: Request, what: str, load: Callable[[ResolvedLeague], Awaitable[T]]
) -> tuple[T | None, str | None, KickbaseError | None]:
    """Run a page load; Kickbase and database problems become a message for the page."""
    state = request.app.state
    try:
        league = await state.league.get()
        return await load(league), None, None
    except ReadOnlyViolation:
        raise  # a programming error against the read-only rule must never be hidden
    except KickbaseError as exc:
        # Outages are expected now and then; everything else needs attention.
        level = logging.WARNING if isinstance(exc, KickbaseUnavailable) else logging.ERROR
        logger.log(level, "Laden %s fehlgeschlagen: %s: %s", what, type(exc).__name__, exc)
        if (
            isinstance(exc, KickbaseHTTPError)
            and exc.status_code in (403, 404)
            and exc.path.startswith("/v4/leagues/")
        ):
            # The league id may have changed or access was revoked: look it up again next time.
            state.league.invalidate()
        return None, describe_error(exc), exc
    except SQLAlchemyError as exc:
        return None, describe_db_error(exc, what), None


async def _news(
    request: Request, player_ids: list[str], period: timedelta
) -> tuple[dict[str, list[ReportView]], str | None]:
    """News reports per player plus a hint if the feed is not current. A database problem
    here only hides the news, it doesn't break the page."""
    sessions = request.app.state.sessions
    now = datetime.now(UTC)
    try:
        reports = await reports_for_players(sessions, player_ids, now - period)
        return reports, await news_problem(KeyValueStore(sessions), now)
    except SQLAlchemyError as exc:
        return {}, describe_db_error(exc, "der Meldungen")


async def _original_news(request: Request, ids: list[str], period: timedelta, reports) -> dict:
    now = datetime.now(UTC)
    recent = [r for r in reports if now - r.current_at <= timedelta(hours=72)]
    older = [r for r in reports if now - r.current_at > timedelta(hours=72)]
    context = {"headlines": [], "news_checked_at": None, "recent_news": recent, "older_news": older}
    try:
        sessions = request.app.state.sessions
        headlines = await headlines_for_players(sessions, ids, now - period)
        links = {r.link for r in reports}
        context["headlines"] = [h for h in headlines if h.link not in links]
        context["news_checked_at"] = await feed_checked_at(KeyValueStore(sessions))
    except SQLAlchemyError as exc:
        context["original_news_problem"] = describe_db_error(exc, "der Originalartikel")
    return context


async def _team_page(request: Request, refresh: bool) -> dict:
    team, error, _ = await _guarded(
        request,
        "des Teams",
        lambda lg: load_team(request.app.state.kickbase, lg, refresh=refresh),
    )
    squad_news, news_hint = [], None
    if team is not None:
        ids = [p.id for group in team.squad for p in group.players]
        reports, news_hint = await _news(request, ids, TEAM_NEWS_PERIOD)
        squad_news = sorted(
            (items[0] for items in reports.values()), key=lambda r: r.current_at, reverse=True
        )
    original_news = await _original_news(
        request, ids if team is not None else [], TEAM_NEWS_PERIOD, squad_news
    )
    if original_news.get("original_news_problem"):
        news_hint = " ".join(filter(None, [news_hint, original_news["original_news_problem"]]))
    matchday_history = None
    matchday_error = None
    # Reuse the league `_guarded` already resolved (or invalidated) above. Calling
    # `state.league.get()` again would re-trigger a Kickbase round-trip on every error
    # path and undo the invalidation after 403/404.
    league = request.app.state.league._league
    if league is not None:
        try:
            matchday_history = await load_matchday_history(request.app.state.sessions, league.id)
        except SQLAlchemyError as exc:
            matchday_error = describe_db_error(exc, "der Spieltags-Bilanz")
    return {
        **original_news,
        "team": team,
        "error": error,
        "squad_news": squad_news,
        "news_problem": news_hint,
        "matchday_history": matchday_history,
        "matchday_error": matchday_error,
    }


@router.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    context = await _team_page(request, refresh=False)
    return templates.TemplateResponse(request, "home.html", {"active_nav": "team", **context})


async def _matchday_points(request: Request) -> dict:
    points, error, exc = await _guarded(
        request,
        "der Spieltagspunkte",
        lambda league: load_live_matchday(request.app.state.kickbase, league),
    )
    retryable = error is not None and (exc is None or isinstance(exc, KickbaseUnavailable))
    return {"points": points, "error": error, "retryable": retryable}


@router.get("/spieltag", response_class=HTMLResponse)
async def matchday_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "matchday.html",
        {"active_nav": "team", **await _matchday_points(request)},
    )


@router.get("/spieltag/punkte", response_class=HTMLResponse)
async def matchday_points(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "partials/live_points.html", await _matchday_points(request)
    )


@router.get("/heute", response_class=HTMLResponse, response_model=None)
async def today(request: Request) -> HTMLResponse | RedirectResponse:
    """The "Heute" box, loaded by the team page after it is shown (it needs the slower
    recommendation data)."""
    if not _partial_request(request):
        return RedirectResponse("/", status_code=303)
    state = request.app.state
    view, error, _ = await _guarded(
        request,
        "von Heute",
        lambda lg: today_view(state.kickbase, state.openligadb, state.sessions, lg),
    )
    return templates.TemplateResponse(
        request, "partials/today.html", {"today": view, "error": error}
    )


@router.get("/deadline_plan", response_class=HTMLResponse, response_model=None)
async def deadline_plan(request: Request) -> HTMLResponse | RedirectResponse:
    """The Deadline-Plan card, lazy-loaded like /heute. Loads data from the cached kickbase
    endpoints and the local holding-role table only — no new Kickbase calls."""
    if not _partial_request(request):
        return RedirectResponse("/", status_code=303)
    state = request.app.state
    view, error, _ = await _guarded(
        request,
        "des Deadline-Plans",
        lambda lg: deadline_plan_view(state.kickbase, state.sessions, lg),
    )
    return templates.TemplateResponse(
        request, "partials/deadline_plan.html", {"view": view, "error": error}
    )


@router.get("/spieler/{player_id}/einschaetzung", response_class=HTMLResponse, response_model=None)
async def player_decision_box(
    request: Request, player_id: Annotated[str, Path(pattern=r"^\d{1,10}$")]
) -> HTMLResponse | RedirectResponse:
    """Score and verdict for the player page, loaded after the page (needs the slower
    recommendation data)."""
    if not _partial_request(request):
        return RedirectResponse(f"/spieler/{player_id}", status_code=303)
    state = request.app.state

    async def load(league):
        page = await load_player(state.kickbase, state.sessions, league, player_id)
        return await player_decision(state.kickbase, state.openligadb, state.sessions, league, page)

    decision, error, _ = await _guarded(request, "der Einschätzung", load)
    return templates.TemplateResponse(
        request, "partials/decision.html", {"decision": decision, "error": error}
    )


@router.post(
    "/team/refresh", response_class=HTMLResponse, dependencies=[Depends(require_csrf_header)]
)
async def refresh_team(request: Request) -> HTMLResponse:
    context = await _team_page(request, refresh=True)
    return templates.TemplateResponse(request, "partials/team.html", context)


@router.get("/spieler", response_class=HTMLResponse)
async def players(
    request: Request,
    q: Annotated[str, Query(max_length=60)] = "",
    # "" (all positions) comes from the radio buttons, so no int type here.
    pos: Annotated[str, Query(max_length=1, pattern=r"^[0-4]?$")] = "",
) -> HTMLResponse:
    position = int(pos or 0)
    error, result = None, None
    try:
        result = await search_players(request.app.state.sessions, q, position)
    except SQLAlchemyError as exc:
        error = describe_db_error(exc, "der Spieler")
    as_of = result.as_of if result else None
    watched: set[str] = set()
    if error is None:
        try:
            league = await request.app.state.league.get()
            watched = await watchlist.watched_ids(request.app.state.sessions, league.id)
        except (KickbaseError, SQLAlchemyError) as exc:
            # Watched flags are a small UX touch – failing them must not hide the search results.
            logger.warning("Merkliste-IDs nicht ladbar: %s: %s", type(exc).__name__, exc)
    context = {
        "active_nav": "spieler",
        "players": result.players if result else [],
        "as_of": as_of,
        "outdated": as_of is not None and datetime.now(UTC) - as_of > PLAYER_DATA_MAX_AGE,
        "query": q,
        "position": position,
        "position_filters": POSITION_FILTERS,
        "error": error,
        "watched": watched,
    }
    template = "partials/player_list.html" if _partial_request(request) else "players.html"
    return templates.TemplateResponse(request, template, context)


@router.get("/spieler/{player_id}", response_class=HTMLResponse)
async def player(
    request: Request, player_id: Annotated[str, Path(pattern=r"^\d{1,10}$")]
) -> HTMLResponse:
    state = request.app.state
    page, error, exc = await _guarded(
        request,
        "des Spielers",
        lambda lg: load_player(state.kickbase, state.sessions, lg, player_id),
    )
    status_code = 200
    if (
        isinstance(exc, KickbaseHTTPError)
        and exc.status_code == 404
        and exc.path.endswith(f"/players/{player_id}")
    ):
        error, status_code = "Diesen Spieler gibt es bei Kickbase nicht (mehr).", 404
    news, news_hint = [], None
    market = None
    watched = False
    if page is not None:
        reports, news_hint = await _news(request, [player_id], PLAYER_NEWS_PERIOD)
        news = reports.get(player_id, [])
        try:
            market = await load_market_listing(
                state.kickbase,
                state.sessions,
                await state.league.get(),
                player_id,
                datetime.now(UTC),
            )
        except (KickbaseError, SQLAlchemyError):
            market = None  # the listing is optional, the page works without it
        try:
            league = await state.league.get()
            watched = player_id in await watchlist.watched_ids(state.sessions, league.id)
        except (KickbaseError, SQLAlchemyError) as exc:
            logger.warning(
                "Merkliste-Status für %s nicht ladbar: %s: %s",
                player_id,
                type(exc).__name__,
                exc,
            )
    original_news = await _original_news(request, [player_id], PLAYER_NEWS_PERIOD, news)
    if original_news.get("original_news_problem"):
        news_hint = " ".join(filter(None, [news_hint, original_news["original_news_problem"]]))
    return templates.TemplateResponse(
        request,
        "player.html",
        {
            **original_news,
            "active_nav": "spieler",
            "player": page,
            "error": error,
            "news": news,
            "news_problem": news_hint,
            "market": market,
            "watched": watched,
            "player_id": player_id,
        },
        status_code=status_code,
    )


@router.post(
    "/spieler/{player_id}/ki",
    response_class=HTMLResponse,
    dependencies=[Depends(require_csrf_header)],
)
async def player_ai(
    request: Request, player_id: Annotated[str, Path(pattern=r"^\d{1,10}$")]
) -> HTMLResponse:
    """On-demand AI summary of a player, cached per market day."""
    state = request.app.state
    llm = getattr(state, "llm", None)
    if llm is None:
        return templates.TemplateResponse(
            request,
            "partials/player_ai.html",
            {
                "ai_text": "KI-Zusammenfassung nicht verfügbar (kein API-Key konfiguriert).",
                "ai_error": True,
            },
        )
    page, error, _ = await _guarded(
        request,
        "des Spielers",
        lambda lg: load_player(state.kickbase, state.sessions, lg, player_id),
    )
    if page is None:
        return templates.TemplateResponse(
            request,
            "partials/player_ai.html",
            {"ai_text": error or "Spieler nicht gefunden.", "ai_error": True},
        )
    reports, _ = await _news(request, [player_id], PLAYER_NEWS_PERIOD)
    news = reports.get(player_id, [])
    decision = None
    try:
        league = await state.league.get()
        decision = await player_decision(
            state.kickbase, state.openligadb, state.sessions, league, page
        )
    except (KickbaseError, SQLAlchemyError) as exc:
        # Without the verdict the assessment is thinner, but still possible.
        logger.warning("Einschätzung für die KI fehlt (%s): %s", player_id, exc)
    try:
        text = await summarize(
            llm, KeyValueStore(state.sessions), page, news, datetime.now(UTC), decision
        )
    except (LlmError, SQLAlchemyError) as exc:
        logger.warning("KI-Zusammenfassung für %s fehlgeschlagen: %s", player_id, exc)
        return templates.TemplateResponse(
            request,
            "partials/player_ai.html",
            {"ai_text": "KI-Zusammenfassung gerade nicht verfügbar.", "ai_error": True},
        )
    if text is None:
        return templates.TemplateResponse(
            request,
            "partials/player_ai.html",
            {"ai_text": "KI lieferte keine brauchbare Zusammenfassung.", "ai_error": True},
        )
    return templates.TemplateResponse(
        request, "partials/player_ai.html", {"ai_text": text, "ai_error": False}
    )


RECOMMENDATION_VIEWS = {
    "kaufen": "Kaufen",
    "trading": "Trading",
    "verkaufen": "Verkaufen",
    "elf": "Beste Elf",
    "bilanz": "Bilanz",
    "gewinne": "Transfer-Gewinne",
    "trends": "Tagestrends",
}


@router.get("/empfehlungen", response_class=HTMLResponse)
async def recommendations_index(request: Request) -> HTMLResponse:
    """Task overview; renders without loading any external data."""
    return templates.TemplateResponse(request, "plan.html", {"active_nav": "empfehlungen"})


def _price_in_euro(raw: str) -> int | None:
    """'5' or '5,5' (millions) → euro; empty → no limit."""
    if not raw:
        return None
    return round(float(raw.replace(",", ".")) * 1_000_000)


@router.get("/empfehlungen/bilanz", response_class=HTMLResponse)
async def track_record_page(request: Request) -> HTMLResponse:
    """How did the recommendations do? Database only, no Kickbase request."""
    state = request.app.state
    record, offers, error = None, None, None
    try:
        league = await state.league.get()
        now = datetime.now(UTC)
        record = await track_record(state.sessions, league.id, now)
        offers = await offer_stats(state.sessions, league.id, now)
    except KickbaseError as exc:
        error = describe_error(exc)
    except SQLAlchemyError as exc:
        error = describe_db_error(exc, "der Bilanz")
    return templates.TemplateResponse(
        request,
        "track_record.html",
        {
            "active_nav": "empfehlungen",
            "view": "bilanz",
            "views": RECOMMENDATION_VIEWS,
            "record": record,
            "offers": offers,
            "error": error,
        },
    )


@router.get("/empfehlungen/gewinne", response_class=HTMLResponse)
async def transfer_profit_page(request: Request) -> HTMLResponse:
    state = request.app.state

    async def load(league):
        squad = await state.kickbase.squad(league.id)
        data = await load_profit(state.sessions, league.id, squad.data.players, datetime.now(UTC))
        return replace(data, stale=squad.stale, fetched_at=squad.fetched_at)

    data, error, _ = await _guarded(request, "der Transfer-Gewinne", load)
    return templates.TemplateResponse(
        request,
        "transfer_profit.html",
        {
            "active_nav": "empfehlungen",
            "view": "gewinne",
            "views": RECOMMENDATION_VIEWS,
            "data": data,
            "error": error,
        },
    )


@router.get("/empfehlungen/trends", response_class=HTMLResponse)
async def daily_trends_page(request: Request) -> HTMLResponse:
    state = request.app.state

    async def load(league):
        squad = await state.kickbase.squad(league.id)
        return await load_trends(state.sessions, squad, datetime.now(UTC))

    data, error, _ = await _guarded(request, "der Tagestrends", load)
    return templates.TemplateResponse(
        request,
        "daily_trends.html",
        {
            "active_nav": "empfehlungen",
            "view": "trends",
            "views": RECOMMENDATION_VIEWS,
            "data": data,
            "error": error,
        },
    )


@router.get("/kader/rollen", response_class=HTMLResponse)
async def holding_roles_page(request: Request) -> HTMLResponse:
    state = request.app.state

    async def load(league):
        squad = await state.kickbase.squad(league.id)
        roles = await holding_roles.roles_for(state.sessions, league.id)
        return {"players": squad.data.players, "roles": roles}

    data, error, _ = await _guarded(request, "der Kaderrollen", load)
    return templates.TemplateResponse(
        request,
        "holding_roles.html",
        {
            "active_nav": "empfehlungen",
            "data": data,
            "error": error,
        },
    )


@router.post(
    "/kader/rolle", response_class=HTMLResponse, dependencies=[Depends(require_csrf_header)]
)
async def holding_role_save(
    request: Request,
    player_id: Annotated[str, Form(max_length=10, pattern=r"^[1-9]\d{0,9}$")],
    role: Annotated[holding_roles.Role, Form()],
) -> HTMLResponse:
    state = request.app.state

    async def load(league):
        squad = await state.kickbase.squad(league.id)
        return league, squad

    result, error, _ = await _guarded(request, "des Kaders", load)
    if result is None:
        return HTMLResponse(error or "Kader nicht verfügbar.", status_code=503)
    league, squad = result
    if squad.stale:
        return HTMLResponse(
            "Kader nicht aktuell erreichbar. Rolle wurde nicht geändert.", status_code=503
        )
    own = next((p for p in squad.data.players if p.id == player_id), None)
    if own is None:
        return HTMLResponse("Spieler ist nicht im eigenen Kader.", status_code=409)
    try:
        await holding_roles.set_role(state.sessions, league.id, player_id, role, datetime.now(UTC))
    except SQLAlchemyError as exc:
        return HTMLResponse(describe_db_error(exc, "der Kaderrolle"), status_code=503)
    return templates.TemplateResponse(
        request,
        "partials/holding_role.html",
        {
            "player_id": player_id,
            "player_name": own.name,
            "role": role.value,
            "saved": True,
        },
    )


@router.get("/empfehlungen/trading", response_class=HTMLResponse)
async def trading_page(request: Request) -> HTMLResponse:
    state = request.app.state

    async def load(league):
        basis = await load_basis(state.kickbase, state.openligadb, state.sessions, league)
        now = datetime.now(UTC)
        bids = await load_bid_status(state.sessions, league.id, now, basis.config.bids)
        data = await trading_view(
            state.kickbase, state.sessions, league, basis, bids.model, bids.hint, now
        )
        return basis, data

    result, error, _ = await _guarded(request, "des Trading-Plans", load)
    basis, data = result if result else (None, None)
    return templates.TemplateResponse(
        request,
        "trading.html",
        {
            "active_nav": "empfehlungen",
            "view": "trading",
            "views": RECOMMENDATION_VIEWS,
            "basis": basis,
            "data": data,
            "error": error,
        },
    )


PlanPlayerId = Annotated[str, Query(pattern=r"^[1-9]\d{0,9}$")]


@router.get("/empfehlungen/simulation", response_class=HTMLResponse)
async def transfer_simulation_page(
    request: Request,
    sales: Annotated[list[PlanPlayerId] | None, Query(alias="verkauf", max_length=25)] = None,
    purchases: Annotated[list[PlanPlayerId] | None, Query(alias="kauf", max_length=25)] = None,
) -> HTMLResponse:
    state = request.app.state
    sales, purchases = sales or [], purchases or []
    costs, input_error = {}, None
    for pid in purchases:
        amounts = request.query_params.getlist(f"preis_{pid}")
        if len(amounts) > 1 or (
            amounts and amounts[0] and not re.fullmatch(r"[1-9]\d{0,8}", amounts[0])
        ):
            input_error = (
                "Kaufpreise bitte einmal je Spieler als ganze Euro eingeben (1 bis 999.999.999)."
            )
            break
        if amounts and amounts[0]:
            costs[pid] = int(amounts[0])

    async def load(league):
        now = datetime.now(UTC)
        basis = await load_basis(state.kickbase, state.openligadb, state.sessions, league, now)
        bids = await load_bid_status(state.sessions, league.id, now, basis.config.bids)
        data = await simulation_view(
            state.kickbase,
            state.sessions,
            league,
            basis,
            sales,
            purchases,
            costs,
            bids.model,
            bids.hint,
            now,
        )
        return basis, data

    result, error, _ = await _guarded(request, "der Transfer-Simulation", load)
    basis, data = result if result else (None, None)
    context = {
        "active_nav": "empfehlungen",
        "basis": basis,
        "data": data,
        "error": input_error or error or (data.error if data else None),
        "sales": set(sales),
        "purchases": set(purchases),
        "cost_inputs": {
            pid: request.query_params.get(f"preis_{pid}", "")[:16] for pid in purchases
        },
    }
    if request.headers.get("HX-Request") and not request.headers.get("HX-History-Restore-Request"):
        return templates.TemplateResponse(request, "partials/simulation.html", context)
    return templates.TemplateResponse(request, "simulation.html", context)


@router.get("/empfehlungen/{view}", response_class=HTMLResponse)
async def recommendations(
    request: Request,
    view: Annotated[str, Path(pattern=r"^(kaufen|verkaufen|elf)$")],
    sort: Annotated[str, Query(pattern=r"^(pro-mio|score|preis|ende|xp-pro-mio)$")] = "pro-mio",
    pos: Annotated[str, Query(max_length=1, pattern=r"^[0-4]?$")] = "",
    vor: Annotated[str, Query(pattern=r"^[01]$")] = "1",
    luecke: Annotated[str, Query(pattern=r"^[01]?$")] = "",
    ueber: Annotated[str, Query(pattern=r"^[01]?$")] = "",
    max_mio: Annotated[
        str, Query(alias="max", max_length=6, pattern=r"^(\d{1,3}([.,]\d{1,2})?)?$")
    ] = "",
) -> HTMLResponse:
    state = request.app.state
    filters = BuyFilters(
        sort=sort,
        position=int(pos or 0),
        before_deadline=vor == "1",
        gaps_only=luecke == "1",
        max_price=_price_in_euro(max_mio),
        over_budget=ueber == "1",
    )

    async def load(league):
        basis = await load_basis(state.kickbase, state.openligadb, state.sessions, league)
        if view == "kaufen":
            bids = await load_bid_status(
                state.sessions, league.id, datetime.now(UTC), basis.config.bids
            )
            data = await buy_view(
                state.kickbase, state.sessions, league, basis, bids.model, bids.hint
            )
        elif view == "verkaufen":
            data = await sell_view(state.kickbase, league, basis)
        else:
            data = await eleven_view(state.kickbase, league, basis)
        return basis, data

    result, error, _ = await _guarded(request, "der Empfehlungen", load)
    basis, data = result if result else (None, None)
    context = {
        "active_nav": "empfehlungen",
        "view": view,
        "views": RECOMMENDATION_VIEWS,
        "basis": basis,
        "data": data,
        "error": error,
    }
    if view == "kaufen":
        shown, matching = select_buys(data, filters) if data else ([], 0)
        context |= {
            "filters": filters,
            "max_input": max_mio,
            "sorts": {key: label for key, (label, _) in BUY_SORTS.items()},
            "position_filters": POSITION_FILTERS,
            "shown": shown,
            "matching": matching,
        }
        if _partial_request(request):
            # Also on errors: htmx swaps the answer into #buy-results, never a whole page.
            return templates.TemplateResponse(request, "partials/buy_results.html", context)
    return templates.TemplateResponse(request, "recommendations.html", context)


# ---------------------------------------------------------------------------- watchlist (C1)


async def _watchlist_page_data(
    request: Request,
) -> tuple[list[watchlist.WatchlistItem], dict[str, int], str | None]:
    """Watchlist rows + a `{player_id: price}` map of which entries are listed right now.

    Errors from Kickbase are reduced to "no market info"; the page itself works without it.
    Database errors bubble up so the caller can show the standard page error."""
    state = request.app.state
    sessions = state.sessions
    league = await state.league.get()
    rows = await watchlist.list_entries(sessions, league.id)
    market: dict[str, int] = {}
    error: str | None = None
    try:
        fetched = await state.kickbase.market(league.id)
        me = (await state.kickbase.user_me()).data.user.id
        now = datetime.now(UTC)
        deadline = fetched.data.deadline
        if deadline is not None and deadline <= now:
            deadline = None
        for p in fetched.data.players:
            if p.seller is not None and p.seller.id == me:
                continue
            expires_at = p.expires_at(fetched.fetched_at)
            if expires_at is not None and expires_at <= now:
                continue
            market[p.id] = p.price
    except (KickbaseError, SQLAlchemyError) as exc:
        logger.warning("Markt für Merkliste nicht ladbar: %s: %s", type(exc).__name__, exc)
        error = "Markt-Info gerade nicht verfügbar."
    return rows, market, error


@router.get("/merkliste", response_class=HTMLResponse)
async def watchlist_page(request: Request) -> HTMLResponse:
    """Spieler, die ich im Auge behalten will: Name, Marktwert, Status, Markt + Schwelle."""
    rows: list[watchlist.WatchlistItem] = []
    market: dict[str, int] = {}
    market_error: str | None = None
    error: str | None = None
    try:
        rows, market, market_error = await _watchlist_page_data(request)
    except SQLAlchemyError as exc:
        error = describe_db_error(exc, "der Merkliste")
    context = {
        "active_nav": "merkliste",
        "entries": rows,
        "market": market,
        "market_error": market_error,
        "error": error,
    }
    template = "watchlist.html" if not _partial_request(request) else "partials/watchlist_rows.html"
    return templates.TemplateResponse(request, template, context)


@router.post(
    "/merkliste/add",
    response_class=HTMLResponse,
    dependencies=[Depends(require_csrf_header)],
)
async def watchlist_add(
    request: Request,
    player_id: Annotated[str, Form(max_length=10, pattern=r"^[1-9]\d{0,9}$")],
) -> HTMLResponse:
    state = request.app.state
    league = await state.league.get()
    error: str | None = None
    try:
        await watchlist.add(state.sessions, league.id, player_id, datetime.now(UTC))
    except watchlist.PlayerNotFound:
        error = "Spieler nicht im Datenbestand."
    except SQLAlchemyError as exc:
        error = describe_db_error(exc, "des Hinzufügens")
    return templates.TemplateResponse(
        request,
        "partials/watchlist_star.html",
        {"player_id": player_id, "watched": error is None, "error": error},
    )


@router.post(
    "/merkliste/remove",
    response_class=HTMLResponse,
    dependencies=[Depends(require_csrf_header)],
)
async def watchlist_remove(
    request: Request,
    player_id: Annotated[str, Form(max_length=10, pattern=r"^[1-9]\d{0,9}$")],
) -> HTMLResponse:
    state = request.app.state
    league = await state.league.get()
    error = None
    try:
        await watchlist.remove(state.sessions, league.id, player_id)
    except SQLAlchemyError as exc:
        error = describe_db_error(exc, "des Entfernens")
    if request.headers.get("HX-Target") == "watchlist-list":
        entries, market, market_error = [], {}, None
        try:
            entries, market, market_error = await _watchlist_page_data(request)
        except SQLAlchemyError as exc:
            error = error or describe_db_error(exc, "der Merkliste")
        if error:
            # Keep the existing DOM if deletion or reloading failed, and show a visible toast.
            return HTMLResponse(error, status_code=503)
        return templates.TemplateResponse(
            request,
            "partials/watchlist_rows.html",
            {"entries": entries, "market": market, "market_error": market_error},
        )
    return templates.TemplateResponse(
        request,
        "partials/watchlist_star.html",
        {"player_id": player_id, "watched": error is not None, "error": error},
    )


@router.post(
    "/merkliste/threshold",
    response_class=HTMLResponse,
    dependencies=[Depends(require_csrf_header)],
)
async def watchlist_threshold(
    request: Request,
    player_id: Annotated[str, Form(max_length=10, pattern=r"^[1-9]\d{0,9}$")],
    price_below: Annotated[str, Form(max_length=10, pattern=r"^\d{0,10}$")] = "",
) -> HTMLResponse:
    """Set or clear the price threshold (empty value → clear)."""
    state = request.app.state
    league = await state.league.get()
    value: int | None = int(price_below) if price_below else None
    error: str | None = None
    entry: watchlist.WatchlistItem | None = None
    market: dict[str, int] = {}
    try:
        await watchlist.set_threshold(state.sessions, league.id, player_id, value)
        entries, market, _ = await _watchlist_page_data(request)
        for item in entries:
            if item.entry.player_id == player_id:
                entry = item
                break
    except watchlist.WatchlistEntryMissing:
        error = "Spieler steht nicht mehr auf der Merkliste."
    except SQLAlchemyError as exc:
        error = describe_db_error(exc, "der Schwelle")
        # Retain the editable row on a failed write or a failed reload after the write.
        return HTMLResponse(error, status_code=503)
    return templates.TemplateResponse(
        request,
        "partials/watchlist_row.html",
        {
            "entry": entry,
            "market": market,
            "threshold_error": error,
            "threshold_input": price_below,
            "player_id": player_id,
        },
    )
