"""Collection jobs of the worker.

All jobs are idempotent: running one twice only overwrites the rows it wrote before. They talk
to Kickbase through a client that never serves stale data, so a failed request fails the job
instead of storing old values as new.
"""

import asyncio
import html
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import (
    MarketValue,
    MatchdayPoints,
    MatchdayResult,
    MatchLineup,
    MyOffer,
    Player,
    PlayerStatsDaily,
    Team,
    Watchlist,
)
from app.db.stores import KeyValueStore
from app.db.upsert import upsert_many
from app.kickbase import models as m
from app.kickbase.calendar import market_day
from app.kickbase.client import KickbaseClient
from app.kickbase.errors import KickbaseHTTPError
from app.kickbase.league import LeagueContext
from app.lineups.ligainsider import FormatError, LigaInsiderClient
from app.lineups.service import (
    INJURIES_STATUS_KEY,
    LINEUPS_STATUS_KEY,
    injuries_for_players,
    ligainsider_hint,
    predicted_players,
    store_injuries,
    store_lineup,
)
from app.llm.claude_bridge import ClaudeBridge
from app.llm.client import LlmClient
from app.news.kicker import KickerFeed
from app.news.service import known_players
from app.notify.events import NotificationConfig, collect_events, commit, compose_message
from app.notify.telegram import TelegramError, TelegramNotifier
from app.openligadb.client import OpenLigaDbClient
from app.services.matchday_report import BenchEntry
from app.services.offers import track_offers
from app.services.players import start_quotes_all
from app.services.recommendations import buy_view, load_basis, sell_view, squad_gaps
from app.services.scoring import load_scoring_config
from app.services.today import LINEUP_SIZE, RISK_THRESHOLD, lineup_risks
from app.services.track_record import log_recommendations
from app.services.transfers import collect_transfers
from app.web.formatting import money_short

logger = logging.getLogger(__name__)

POINTS_STATE_KEY = "worker.points"
LINEUPS_STATE_KEY = "worker.lineups"
# Backfill: this many unknown players in a row without any success means the endpoint is broken.
BACKFILL_MAX_MISSES = 10
# Finished matches are fetched again for a few days in case Kickbase corrects points.
RECHECK_PERIOD = timedelta(days=3)
# kv key holding the last reported matchday and the backfill flag.
MATCHDAY_REPORT_KEY = "notify.matchday_report"
# Max earlier matchdays processed per backfill run (the worker client caps concurrency at
# `max_parallel=1`, so this is a per-run budget rather than a parallelism knob).
MATCHDAY_REPORT_BACKFILL_BATCH = 2


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class JobContext:
    client: KickbaseClient
    sessions: async_sessionmaker
    league: LeagueContext
    kv: KeyValueStore
    tz: ZoneInfo
    clock: Callable[[], datetime] = field(default=utcnow)
    notifier: TelegramNotifier | None = None
    notifications: NotificationConfig | None = None
    public_url: str = ""
    llm: LlmClient | None = None
    news_feed: KickerFeed | None = None
    oldb: OpenLigaDbClient | None = None
    claude: ClaudeBridge | None = None  # Claude im Telegram-Chat (Bridge auf der VM)

    def today(self) -> date:
        return self.clock().astimezone(self.tz).date()

    def market_day(self) -> date:
        return market_day(self.clock())


async def sync_players(ctx: JobContext) -> str:
    """Market value, status and club of every Bundesliga player (one request per club)."""
    league = await ctx.league.get()
    competition = league.competition_id
    table = await ctx.client.competition_table(competition, refresh=True)
    now, day = ctx.clock(), ctx.market_day()

    async with ctx.sessions.begin() as session:
        await upsert_many(
            session,
            Team,
            [
                {"id": t.team_id, "name": t.team_name, "image": t.image, "updated_at": now}
                for t in table.data.teams
            ],
            keys=["id"],
        )

    player_count = 0
    for team in table.data.teams:
        profile = (await ctx.client.team_profile(competition, team.team_id, refresh=True)).data
        players = profile.players
        async with ctx.sessions.begin() as session:
            await upsert_many(
                session,
                Player,
                [
                    {
                        "id": p.id,
                        "name": p.name,
                        "team_id": p.team_id,
                        "position": p.position.value,
                        "status": p.status,
                        "market_value": p.market_value,
                        "image": p.image,
                        "updated_at": now,
                    }
                    for p in players
                ],
                keys=["id"],
            )
            await upsert_many(
                session,
                MarketValue,
                [{"player_id": p.id, "day": day, "value": p.market_value} for p in players],
                keys=["player_id", "day"],
            )
            await upsert_many(
                session,
                PlayerStatsDaily,
                [
                    {
                        "player_id": p.id,
                        "day": day,
                        "status": p.status,
                        "team_id": p.team_id,
                        "market_value_change_7d": p.market_value_change_7d,
                    }
                    for p in players
                ],
                keys=["player_id", "day"],
            )
        player_count += len(players)
    return f"{len(table.data.teams)} Vereine, {player_count} Spieler (Marktwerte vom {day:%d.%m.})"


async def sync_points(ctx: JobContext) -> str:
    """Points per player for every finished match not stored yet (two requests per match:
    the team center of both clubs). Recently finished matches are fetched again."""
    league = await ctx.league.get()
    competition = league.competition_id
    schedule = (await ctx.client.competition_matchdays(competition, refresh=True)).data
    season = schedule.season_start_year
    if season is None:
        return "Kein Spielplan veröffentlicht"
    now = ctx.clock()

    state = await ctx.kv.get(POINTS_STATE_KEY) or {}
    synced: set[str] = set(state.get("matches", [])) if state.get("season") == season else set()

    todo = [
        (md.matchday, match)
        for md in schedule.matchdays
        for match in md.matches
        if match.is_finished and (match.id not in synced or now - match.kickoff < RECHECK_PERIOD)
    ]
    rows = 0
    for matchday, match in todo:
        sides = ((match.team1_id, match.team2_id, True), (match.team2_id, match.team1_id, False))
        batch = []
        for team_id, opponent_id, is_home in sides:
            center = await ctx.client.team_center(competition, team_id, matchday, refresh=True)
            if not center.data.players:
                logger.warning(
                    "Spiel %s, Verein %s: keine Punkte, erneut beim nächsten Lauf",
                    match.id,
                    team_id,
                )
                batch = []
                break
            batch += [
                {
                    "season": season,
                    "matchday": matchday,
                    "player_id": p.id,
                    "points": p.points,
                    "team_id": team_id,
                    "opponent_id": opponent_id,
                    "is_home": is_home,
                    "kickoff": match.kickoff,
                }
                for p in center.data.players
            ]
        if not batch:
            continue
        async with ctx.sessions.begin() as session:
            await upsert_many(
                session, MatchdayPoints, batch, keys=["season", "matchday", "player_id"]
            )
        synced.add(match.id)
        # Saved after every match, so an interrupted run resumes where it stopped.
        await ctx.kv.set(POINTS_STATE_KEY, {"season": season, "matches": sorted(synced)})
        rows += len(batch)
    return f"{len(todo)} Spiele, {rows} Punkteinträge (Saison {season}/{(season + 1) % 100:02d})"


async def collect_lineups(ctx: JobContext) -> str:
    """Starting eleven and bench of every finished match (one request per match). Players not
    in a matchday squad have no row; `started` is the basis for the start quote on the player
    page."""
    league = await ctx.league.get()
    competition = league.competition_id
    schedule = (await ctx.client.competition_matchdays(competition, refresh=True)).data
    season = schedule.season_start_year
    if season is None:
        return "Kein Spielplan veröffentlicht"

    state = await ctx.kv.get(LINEUPS_STATE_KEY) or {}
    synced: set[str] = set(state.get("matches", [])) if state.get("season") == season else set()

    todo = [
        (md.matchday, match)
        for md in schedule.matchdays
        for match in md.matches
        # The lineup of a finished match no longer changes: fetched once, never again.
        if match.is_finished and match.id not in synced
    ]
    rows = 0
    for matchday, match in todo:
        details = (await ctx.client.match_details(match.id, refresh=True)).data
        batch = _lineup_rows(details, match, season, matchday)
        if not details.team1_starting or not details.team2_starting:
            # A finished match without a lineup is an anomaly: keep retrying and don't mark it
            # as synced, so it is fetched again instead of silently leaving the start quote empty.
            logger.warning(
                "Spiel %s (Spieltag %d): unvollständige Aufstellung, erneut beim nächsten Lauf",
                match.id,
                matchday,
            )
            continue
        async with ctx.sessions.begin() as session:
            await upsert_many(session, MatchLineup, batch, keys=["match_id", "player_id"])
        synced.add(match.id)
        await ctx.kv.set(LINEUPS_STATE_KEY, {"season": season, "matches": sorted(synced)})
        rows += len(batch)
    return f"{len(todo)} Spiele, {rows} Einsatz-Einträge (Saison {season}/{(season + 1) % 100:02d})"


def _lineup_rows(details: m.MatchDetails, match: m.Match, season: int, matchday: int) -> list[dict]:
    # One row per player: a player listed in both starting and bench would otherwise collide on
    # the primary key and abort the whole job.
    rows: dict[str, dict] = {}
    for team_id, starting, bench in (
        (details.team1_id, details.team1_starting, details.team1_bench),
        (details.team2_id, details.team2_starting, details.team2_bench),
    ):
        for player in starting:
            rows[player.id] = {
                "match_id": match.id,
                "player_id": player.id,
                "season": season,
                "matchday": matchday,
                "team_id": team_id,
                "started": True,
                "kickoff": match.kickoff,
            }
        for player in bench:
            rows.setdefault(
                player.id,
                {
                    "match_id": match.id,
                    "player_id": player.id,
                    "season": season,
                    "matchday": matchday,
                    "team_id": team_id,
                    "started": False,
                    "kickoff": match.kickoff,
                },
            )
    return list(rows.values())


async def backfill_market_values(ctx: JobContext, min_days: int = 30) -> str:
    """92 days of market value history for players with little history: everyone on the first
    run (~460 requests), later only players new to the Bundesliga."""
    league = await ctx.league.get()
    competition = league.competition_id
    async with ctx.sessions() as session:
        counts = (
            select(MarketValue.player_id, func.count().label("days"))
            .group_by(MarketValue.player_id)
            .subquery()
        )
        result = await session.execute(
            select(Player.id)
            .outerjoin(counts, counts.c.player_id == Player.id)
            .where(func.coalesce(counts.c.days, 0) < min_days)
            .order_by(Player.market_value.desc())
        )
        player_ids = list(result.scalars())

    filled, skipped = 0, 0
    for player_id in player_ids:
        if skipped >= BACKFILL_MAX_MISSES and not filled:
            raise RuntimeError(f"Backfill: {skipped} Spieler in Folge unbekannt, Endpunkt prüfen")
        try:
            history = await ctx.client.competition_player_market_value(
                competition, player_id, 92, refresh=True
            )
        except KickbaseHTTPError as exc:
            if exc.status_code != 404:
                raise  # anything but "unknown player" means the endpoint itself is broken
            # e.g. a player who left the league since the last sync; the others still count
            logger.warning("Marktwerte für Spieler %s nicht verfügbar: %s", player_id, exc)
            skipped += 1
            continue
        async with ctx.sessions.begin() as session:
            await upsert_many(
                session,
                MarketValue,
                [
                    {"player_id": player_id, "day": point.day, "value": point.value}
                    for point in history.data.points
                ],
                keys=["player_id", "day"],
            )
        filled += 1
    return f"{filled} Spieler ergänzt, {skipped} übersprungen"


async def notify_squad(ctx: JobContext) -> str:
    """Telegram message about big market value moves and status changes of own players."""
    if ctx.notifier is None or ctx.notifications is None:
        return "Benachrichtigungen aus (Telegram nicht konfiguriert)"
    league = await ctx.league.get()
    squad = (await ctx.client.squad(league.id, refresh=True)).data.players
    collected = await collect_events(squad, ctx.kv, ctx.notifications, ctx.today(), ctx.public_url)
    if collected.events:
        # Raises on failure: nothing is recorded, the next run tries the same events again.
        await ctx.notifier.send(compose_message(league.name, collected.events))
    await commit(ctx.kv, collected, ctx.today())
    if not collected.events:
        return "Keine neuen Ereignisse"
    return f"{len(collected.events)} Ereignisse gemeldet"


async def notify_watchlist(ctx: JobContext) -> str:
    """Telegram message about watched players (Block C, C1.4).

    Checks four trigger types (listed, price, status, dropped from LigaInsider XI). Without
    Telegram or without any watchlist rows it bails out *before* the API call – the job is cheap
    to skip and runs every 30 min, so an empty watchlist must not spam the API."""
    from app.notify.watchlist import (
        collect_watchlist_events,
        commit_watchlist,
        load_watchlist_config,
    )
    from app.services.watchlist import list_entries

    if ctx.notifier is None or ctx.notifications is None:
        return "Benachrichtigungen aus (Telegram nicht konfiguriert)"
    # Cheap pre-check: skip the league lookup (and every Kickbase request) when no league has any
    # watched players. The user has a single configured league, so a single row anywhere means
    # the job is worth running.
    async with ctx.sessions() as session:
        has_entries = await session.scalar(select(Watchlist.league_id).limit(1)) is not None
    if not has_entries:
        return "nichts zu tun"
    league = await ctx.league.get()
    items = await list_entries(ctx.sessions, league.id)
    if not items:
        return "nichts zu tun"
    now = ctx.clock()
    market_data, squad_data = await asyncio.gather(
        ctx.client.market(league.id, refresh=True), ctx.client.squad(league.id, refresh=True)
    )
    market = market_data.data.players
    squad = squad_data.data.players
    my_offer_listing_keys = {p.listing_key for p in market if p.my_offer}
    squad_ids = {p.id for p in squad}
    team_by_player = {p.id: p.team_id for p in market}
    for p in squad:
        team_by_player.setdefault(p.id, p.team_id)
    predicted = await predicted_players(ctx.sessions, now)
    collected = await collect_watchlist_events(
        items,
        market,
        predicted,
        squad_ids,
        my_offer_listing_keys,
        team_by_player,
        load_watchlist_config(),
        ctx.kv,
        ctx.public_url,
    )
    if collected.events:
        await ctx.notifier.send(compose_message(league.name, collected.events))
    await commit_watchlist(ctx.kv, collected, ctx.today())
    if not collected.events:
        return "Keine neuen Ereignisse"
    return f"{len(collected.events)} Merklisten-Ereignisse gemeldet"


async def notify_own_offers(ctx: JobContext) -> str:
    """Telegram message about bids on the manager's own listed players (Block C, C2.3).

    Reads the configured rule from `NotificationConfig.own_offers`. Bails out before any API call
    when Telegram is off or the alarm is disabled – the job runs every 30 min, so the cheap
    skips matter."""
    from app.notify.own_offers import collect_own_offer_events, commit_own_offers

    if ctx.notifier is None or ctx.notifications is None:
        return "Benachrichtigungen aus (Telegram nicht konfiguriert)"
    rule = ctx.notifications.own_offers
    if not rule.enabled:
        return "Benachrichtigungen aus (Eigene-Angebote-Alarm aus)"
    league = await ctx.league.get()
    user_id = (await ctx.client.user_me()).data.user.id
    market_data = await ctx.client.market(league.id, refresh=True)
    events = await collect_own_offer_events(
        market_data.data.players,
        user_id,
        rule,
        market_data.fetched_at,
        ctx.tz,
        ctx.public_url,
        ctx.kv,
    )
    if events:
        await ctx.notifier.send(compose_message(league.name, events))
    await commit_own_offers(ctx.kv, events, ctx.today())
    if not events:
        return "Keine neuen Ereignisse"
    n = len(events)
    return f"{n} {'Gebot' if n == 1 else 'Gebote'} auf eigene Spieler gemeldet"


async def matchday_report(ctx: JobContext) -> str:
    """Per-matchday Telegram report after the last match finishes (Block C, C3.3).

    Skips on early exit reasons (no OpenLigaDB client, latest matchday unknown, matchday
    still in progress, already reported) – the job is cheap to call, but it touches
    multiple Kickbase endpoints on the happy path and that costs the user's rate limit.

    On success: writes one row to `matchday_results` (always – even without Telegram,
    so the dashboard's chart in C3.4 has data) and updates `notify.matchday_report` so
    the next run knows which matchday was already handled. Telegram sends *before* the
    write/commit – a failed send leaves the row unwritten, so the next run retries.

    On the first run after C3.3 ships, any earlier matchdays missing from
    `matchday_results` are backfilled here (max 2 per run, newest first, then never
    again once everything is filled) so the chart has a complete history instead of
    starting at "matchday N today"."""

    if ctx.oldb is None:
        return "OpenLigaDB-Client fehlt"
    league = await ctx.league.get()
    schedule = (await ctx.client.competition_matchdays(league.competition_id, refresh=True)).data
    season = schedule.season_start_year
    if season is None:
        return "Kein Spielplan veröffentlicht"

    # The latest matchday Kickbase already scored (matches are finished, points exist).
    # This is the live ranking – it catches up before the teamcenter / matchday-points
    # collector (sync_points) finishes, but we only act when it's strictly newer than what
    # we already reported.
    latest_ranking = (await ctx.client.ranking(league.id, refresh=True)).data
    if latest_ranking.latest_matchday is None:
        return "Kein Spieltag in der Wertung"

    state = await ctx.kv.get(MATCHDAY_REPORT_KEY) or {}
    last_reported = int(state.get("matchday", 0))

    # Backfill earlier matchdays until everything is filled. This runs before the
    # "schon gemeldet" shortcut so a partial first run keeps progressing on later
    # runs even when the latest matchday was already reported. The flag sticks once
    # the table is complete, so the per-run DB lookup is a one-time affair.
    backfill_msg = ""
    if not state.get("backfill_done"):
        backfill_msg, still_missing = await _backfill_earlier_matchdays(
            ctx, league, season, latest_ranking.latest_matchday
        )
        if not still_missing:
            state["backfill_done"] = True
            await ctx.kv.set(MATCHDAY_REPORT_KEY, state)

    if latest_ranking.latest_matchday <= last_reported:
        # Nothing new to report. The backfill (if any) is its own message.
        return backfill_msg or "schon gemeldet"

    # OpenLigaDB confirms the matchday is over (Kickbase's `latest_matchday` sometimes
    # advances before the very last late-game correction is in).
    matches = (await ctx.oldb.matchday(season, latest_ranking.latest_matchday)).data
    if not matches or not all(m.finished for m in matches):
        return f"Spieltag {latest_ranking.latest_matchday} läuft noch"

    user_id = (await ctx.client.user_me()).data.user.id
    summary = await _send_matchday_report(
        ctx, league, season, latest_ranking.latest_matchday, user_id, state
    )
    if backfill_msg:
        return f"{summary} ({backfill_msg})"
    return summary


async def _backfill_earlier_matchdays(
    ctx: JobContext, league: LeagueContext, season: int, latest_matchday: int
) -> tuple[str, bool]:
    """One-shot backfill: any earlier matchday (1..latest-1) missing from
    `matchday_results` is fetched and stored, newest first and max 2 per run so a
    fresh install doesn't burst Kickbase with one request per past matchday. Returns
    the human-readable summary plus a `still_missing` flag so the caller only sticks
    `backfill_done=True` once the table is actually complete."""
    async with ctx.sessions() as session:
        stored = set(
            (
                await session.execute(
                    select(MatchdayResult.matchday).where(
                        MatchdayResult.league_id == league.id,
                        MatchdayResult.season == season,
                    )
                )
            ).scalars()
        )
    missing = [d for d in range(1, latest_matchday) if d not in stored]
    if not missing:
        return "Backfill: nichts zu tun", False
    # Newest first: the chart immediately shows a recent delta, and a partial run still
    # leaves a continuous tail so `latest_placement_delta` can compare adjacent rows.
    user_id = (await ctx.client.user_me()).data.user.id
    filled = 0
    for matchday in sorted(missing, reverse=True)[:MATCHDAY_REPORT_BACKFILL_BATCH]:
        try:
            if await _store_matchday(ctx, league, season, matchday, user_id, send=False):
                filled += 1
        except Exception as exc:
            # A single past matchday failing should not poison the rest of the run.
            logger.warning(
                "Backfill Spieltag %d fehlgeschlagen: %s: %s",
                matchday,
                type(exc).__name__,
                exc,
            )
    still_missing = filled < len(missing)
    return f"Backfill: {filled}/{len(missing)} nachgetragen", still_missing


async def _send_matchday_report(
    ctx: JobContext,
    league: LeagueContext,
    season: int,
    matchday: int,
    user_id: str,
    state: dict,
) -> str:
    """Build the report, send the Telegram message (when enabled), then store the row and
    update `notify.matchday_report`. The send happens before the write so a Telegram error
    leaves the next run free to retry."""
    success = await _store_matchday(
        ctx,
        league,
        season,
        matchday,
        user_id,
        send=True,
        notifier=ctx.notifier,
        notifications=ctx.notifications,
        public_url=ctx.public_url,
        league_name=league.name,
    )
    if not success:
        return f"Spieltag {matchday}: Telegram-Versand fehlgeschlagen, nächster Lauf wiederholt"
    state["matchday"] = matchday
    await ctx.kv.set(MATCHDAY_REPORT_KEY, state)
    return f"Spieltag {matchday} gemeldet"


async def _store_matchday(
    ctx: JobContext,
    league: LeagueContext,
    season: int,
    matchday: int,
    user_id: str,
    *,
    send: bool,
    notifier: TelegramNotifier | None = None,
    notifications: NotificationConfig | None = None,
    public_url: str = "",
    league_name: str = "",
) -> bool:
    """Build + (optionally send) + persist one matchday's report. Returns False when
    the Telegram send failed (the row was not written, so the next run retries)."""
    from app.notify.matchday_report import compose_message as compose_matchday
    from app.services.matchday_report import (
        build_report,
    )

    teamcenter_data, ranking_data = await asyncio.gather(
        ctx.client.user_teamcenter(league.id, user_id, matchday),
        ctx.client.ranking(league.id, matchday=matchday, refresh=True),
    )
    teamcenter = teamcenter_data.data
    ranking = ranking_data.data
    bench, bank_is_approximation = await _bench_for_teamcenter(
        ctx, teamcenter, league.id, season, matchday
    )
    # The bench may come from the squad fallback (when `teamcenter.bench` is empty) – those
    # players are not in the teamcenter at all, so include them in the position lookup.
    positions = await _positions_for_teamcenter(ctx, teamcenter, [b.id for b in bench])
    previous_placement = await _previous_placement(ctx, league.id, season, matchday)
    report = build_report(
        teamcenter=teamcenter,
        ranking=ranking,
        user_id=user_id,
        season=season,
        matchday=matchday,
        positions=positions,
        bench=bench,
        previous_placement=previous_placement,
        bank_is_approximation=bank_is_approximation,
    )

    # Match the gated pattern of `notify_own_offers`: an unconfigured notifier or a
    # disabled rule means *no* message – the row is still written so the chart has
    # data. With Telegram on, a failed send returns False here so the row stays
    # unwritten and the next run retries (the worker surfaces "Telegram-Versand
    # fehlgeschlagen" and the kv state never advances past the previous matchday).
    if (
        send
        and notifier is not None
        and notifications is not None
        and notifications.matchday_report.enabled
    ):
        try:
            await notifier.send(compose_matchday(league_name, report, public_url))
        except TelegramError:
            return False

    async with ctx.sessions.begin() as session:
        await upsert_many(
            session,
            MatchdayResult,
            [
                {
                    "league_id": league.id,
                    "season": season,
                    "matchday": matchday,
                    "points": report.points,
                    "placement": report.placement,
                    "matchday_placement": report.matchday_placement,
                    "team_value": report.team_value,
                    "missed_points": report.missed_points,
                    "created_at": ctx.clock(),
                }
            ],
            keys=["league_id", "season", "matchday"],
        )
    return True


async def _positions_for_teamcenter(
    ctx: JobContext,
    teamcenter: m.UserTeamCenter,
    extra_ids: list[str] | None = None,
) -> dict[str, int]:
    """The teamcenter omits positions; look them up from the local `players` table so the
    cross-position swap check has what it needs. `extra_ids` covers bench players from the
    squad fallback that the teamcenter itself does not know about."""
    ids = {p.id for p in teamcenter.lineup} | {p.id for p in teamcenter.bench}
    if extra_ids:
        ids.update(extra_ids)
    if not ids:
        return {}
    async with ctx.sessions() as session:
        rows = (
            await session.execute(select(Player.id, Player.position).where(Player.id.in_(ids)))
        ).all()
    return {pid: pos for pid, pos in rows}


async def _previous_placement(
    ctx: JobContext, league_id: str, season: int, matchday: int
) -> int | None:
    """Return the placement recorded after the previous matchday, or None on the first one."""
    if matchday <= 1:
        return None
    async with ctx.sessions() as session:
        return await session.scalar(
            select(MatchdayResult.placement).where(
                MatchdayResult.league_id == league_id,
                MatchdayResult.season == season,
                MatchdayResult.matchday == matchday - 1,
            )
        )


async def _bench_for_teamcenter(
    ctx: JobContext,
    teamcenter: m.UserTeamCenter,
    league_id: str,
    season: int,
    matchday: int,
) -> tuple[list[BenchEntry], bool]:
    """When the teamcenter returns its `nlp` (Kickbase's own bench), use it as-is and
    return an empty list with `bank_is_approximation=False`. Otherwise fall back to
    "current squad minus current lineup" with points from `matchday_points` and mark
    the result as an approximation."""
    if teamcenter.bench:
        return [], False
    lineup_ids = {p.id for p in teamcenter.lineup}
    squad_data = (await ctx.client.squad(league_id, refresh=True)).data
    bench_players = [p for p in squad_data.players if p.id not in lineup_ids]
    if not bench_players:
        return [], True
    bench_ids = {p.id for p in bench_players}
    async with ctx.sessions() as session:
        rows = (
            await session.execute(
                select(MatchdayPoints.player_id, MatchdayPoints.points).where(
                    MatchdayPoints.season == season,
                    MatchdayPoints.matchday == matchday,
                    MatchdayPoints.player_id.in_(bench_ids),
                )
            )
        ).all()
    points_by_id = {pid: pts for pid, pts in rows}
    return (
        [
            BenchEntry(
                id=p.id,
                name=p.name,
                points=points_by_id.get(p.id, 0),
                position=p.position.value,
            )
            for p in bench_players
        ],
        True,
    )


DEADLINE_WARN_WINDOW = timedelta(hours=2)
DEADLINE_RISK_THRESHOLD = RISK_THRESHOLD


async def deadline_plan_reminder(ctx: JobContext) -> str:
    """Telegram reminder while the deadline plan shows an open deficit (Block C, C21.3).

    Reuses the C21.2 `deadline_plan_view` loader (single Kickbase round trip, no fresh
    endpoints). Fires at most once per (deadline, deficit bucket, recommended sales)
    fingerprint; a solved deficit clears the fingerprint and a future deficit with the same
    numbers still triggers a fresh alarm. Outside the close-hours window or with no Telegram
    it bails out early – the job is cheap to skip and runs hourly, so a stale Telegram
    config must not burst the API."""
    from app.notify.deadline_plan import commit_reminder, compose_reminder, should_remind
    from app.services.deadline_plan import deadline_plan_view

    if ctx.notifier is None or ctx.notifications is None:
        return "Benachrichtigungen aus (Telegram nicht konfiguriert)"
    rule = ctx.notifications.deadline_plan
    if not rule.enabled:
        return "Benachrichtigungen aus (Deadline-Plan-Alarm aus)"

    now = ctx.clock()
    view = await deadline_plan_view(ctx.client, ctx.sessions, await ctx.league.get(), now=now)
    if view is None:
        return "Deadline-Plan: Kader nicht ladbar"
    plan = view.plan
    if plan.deadline is None:
        # Mirror the "keine Deadline"-silence of the team-card; commit a baseline so the next
        # run can return immediately without recomputing.
        await commit_reminder(ctx.kv, "none")
        return "keine Deadline – kein Reminder"
    close_window = timedelta(hours=load_scoring_config().trading.close_hours)
    if now < plan.deadline - close_window:
        return "außerhalb des Deadline-Fensters"
    # Only an *open* deficit is worth a reminder – budget healthy → no message, clear the
    # fingerprint so a future deficit with the same numbers still fires.
    from app.notify.deadline_plan import situation_key

    key = situation_key(plan)
    if not key.startswith("deficit:"):
        await commit_reminder(ctx.kv, key)
        return "kein offenes Defizit"

    if not await should_remind(ctx.kv, key):
        return "für diese Situation schon gemeldet"

    reminder = compose_reminder(view, (await ctx.league.get()).name, ctx.tz, ctx.public_url)
    if reminder is None:  # defensive: situation_key said deficit but compose had nothing to say
        return "kein Reminder-Text"
    await ctx.notifier.send(reminder.text)
    await commit_reminder(ctx.kv, reminder.key)
    return "Deadline-Plan erinnert"


async def deadline_warning(ctx: JobContext) -> str:
    """Warn ~2 h before the lineup deadline about risky starters, gaps and expiring bids."""
    if ctx.notifier is None:
        return "Benachrichtigungen aus (Telegram nicht konfiguriert)"
    league = await ctx.league.get()
    now = ctx.clock()
    deadline = (await ctx.client.market(league.id, refresh=True)).data.deadline
    if deadline is None or not (now <= deadline <= now + DEADLINE_WARN_WINDOW):
        return "keine Deadline in den nächsten 2 Stunden"
    key = f"deadline.warned.{deadline.isoformat()}"
    if await ctx.kv.get(key):
        return "für diese Deadline schon gewarnt"

    schedule = (await ctx.client.competition_matchdays(league.competition_id, refresh=True)).data
    season = schedule.season_start_year
    lineup = (await ctx.client.my_eleven(league.id, refresh=True)).data
    squad = (await ctx.client.squad(league.id, refresh=True)).data

    predicted = await predicted_players(ctx.sessions, now)
    injuries = await injuries_for_players(ctx.sessions, now)
    data_hint = await ligainsider_hint(ctx.sessions, ctx.kv, now)
    quotes = await start_quotes_all(ctx.sessions, season) if season is not None else {}
    status_by_id = {p.id: p for p in squad.players}
    risks = _deadline_risks(lineup.players, status_by_id, predicted, injuries, quotes)
    gaps = [
        f"{count}× {position.short_label}" for position, count in squad_gaps(squad.players).items()
    ]
    bid_lines = [
        f"🔔 {html.escape(b.player_name)} ({money_short(b.amount)}), "
        f"endet {b.expires_at.astimezone(ctx.tz):%H:%M}"
        for b in await _bids_expiring(ctx.sessions, league.id, now, deadline)
    ]
    empty_slots = max(0, LINEUP_SIZE - len(lineup.players))
    if not (risks or gaps or bid_lines or data_hint or empty_slots):
        return "nichts zu warnen"

    deadline_local = deadline.astimezone(ctx.tz)
    lines = [
        f"<b>Aufstellung {html.escape(league.name)}</b>",
        f"Deadline {deadline_local:%d.%m. %H:%M}",
    ]
    if empty_slots:
        lines.append(
            f"❗ Nur {len(lineup.players)} von {LINEUP_SIZE} Plätzen aufgestellt – "
            "ein leerer Platz bringt keine Punkte."
        )
    if risks:
        lines.append("Ausfallrisiko:")
        lines += risks
    if data_hint:
        lines.append(f"⚠️ {html.escape(data_hint)}")
    if gaps:
        lines.append("Freie Plätze: " + ", ".join(gaps))
    if bid_lines:
        lines.append("Gebote vor der Deadline:")
        lines += bid_lines
    await ctx.notifier.send("\n".join(lines))
    await ctx.kv.set(key, True)
    return "gewarnt"


def _deadline_risks(lineup_players, status_by_id, predicted, injuries, quotes) -> list[str]:
    return [
        f"⚠️ {html.escape(r.headline)} ({html.escape('; '.join(r.reasons))})"
        for r in lineup_risks(
            lineup_players, status_by_id, predicted, injuries, quotes, DEADLINE_RISK_THRESHOLD
        )
    ]


async def _bids_expiring(
    sessions: async_sessionmaker, league_id: str, now: datetime, deadline: datetime
) -> list[MyOffer]:
    async with sessions() as session:
        return list(
            (
                await session.execute(
                    select(MyOffer).where(
                        MyOffer.league_id == league_id,
                        MyOffer.outcome == "open",
                        MyOffer.expires_at.is_not(None),
                        MyOffer.expires_at >= now,
                        MyOffer.expires_at < deadline,
                    )
                )
            ).scalars()
        )


async def collect_lineup_predictions(
    ctx: JobContext, client: LigaInsiderClient | None = None
) -> str:
    """Predicted starting elevens from LigaInsider (18 club pages, one request per club)."""
    known = await known_players(ctx.sessions)
    if not known:
        return "Keine Spieler in der Datenbank (erst der Spieler-Abgleich)"
    owns = client is None
    client = client or LigaInsiderClient()
    stored = teams = unmatched = 0
    unreadable: list[str] = []
    try:
        for team_id in client.config.teams:
            try:
                lineup = await client.fetch_lineup(team_id)
            except FormatError as exc:
                logger.warning("LigaInsider (%s): %s", team_id, exc)
                unreadable.append(team_id)
                continue
            n, miss = await store_lineup(ctx.sessions, team_id, lineup.players, known, ctx.clock())
            stored += n
            unmatched += miss
            teams += 1
    finally:
        if owns:
            await client.aclose()
    await ctx.kv.set(LINEUPS_STATUS_KEY, {"at": ctx.clock().isoformat(), "unreadable": unreadable})
    if unreadable and not teams:
        raise FormatError(f"Keine Aufstellung lesbar ({len(unreadable)} Vereine), Seite geändert?")
    summary = f"{teams} Vereine, {stored} Einträge"
    if unmatched:
        summary += f", {unmatched} nicht zugeordnet"
    if unreadable:
        summary += f", {len(unreadable)} nicht lesbar"
    return summary


async def collect_injuries(ctx: JobContext, client: LigaInsiderClient | None = None) -> str:
    """Injured/suspended players from LigaInsider's overview page (one request)."""
    known = await known_players(ctx.sessions)
    if not known:
        return "Keine Spieler in der Datenbank (erst der Spieler-Abgleich)"
    owns = client is None
    client = client or LigaInsiderClient()
    team_by_slug = client.config.team_by_slug
    try:
        parsed = await client.fetch_injuries()
    finally:
        if owns:
            await client.aclose()
    stored, unmatched, skipped = await store_injuries(
        ctx.sessions, parsed.teams, team_by_slug, known, ctx.clock()
    )
    await ctx.kv.set(INJURIES_STATUS_KEY, {"at": ctx.clock().isoformat(), "skipped": skipped})
    summary = f"{stored} Ausfälle"
    if unmatched:
        summary += f", {unmatched} nicht zugeordnet"
    if skipped:
        summary += f", {len(skipped)} Vereine unbekannt ({', '.join(skipped)})"
    return summary


async def collect_league_transfers(ctx: JobContext) -> str:
    """Transfers of the league from the activity feed (one request, more on the first run)."""
    league = await ctx.league.get()
    me = (await ctx.client.user_me()).data.user.name
    period = load_scoring_config().bids.period_days
    result = await collect_transfers(ctx.client, ctx.sessions, league, me, ctx.clock(), period)
    summary = f"{result.new} neue Transfers ({result.pages} Seiten), {result.valued} mit Marktwert"
    if result.unreadable:
        summary += f", {result.unreadable} nicht lesbar"
    return summary


async def track_my_offers(ctx: JobContext) -> str:
    """Own bids on the market; evaluates ended ones (after the transfer run, which has the
    winning prices)."""
    league = await ctx.league.get()
    result = await track_offers(ctx.client, ctx.sessions, league, ctx.clock())
    return f"{result.new} neue Gebote, {result.evaluated} ausgewertet"


async def log_recommendation_snapshot(ctx: JobContext) -> str:
    """Daily snapshot of all offers and squad players with score (track record)."""
    if ctx.oldb is None:
        return "Protokoll aus (OpenLigaDB-Client fehlt)"
    league = await ctx.league.get()
    now = ctx.clock()
    basis = await load_basis(ctx.client, ctx.oldb, ctx.sessions, league, now=now)
    buys = await buy_view(ctx.client, ctx.sessions, league, basis, now=now)
    sells = await sell_view(ctx.client, league, basis, now=now)
    count = await log_recommendations(ctx.sessions, league.id, now, buys, sells)
    return f"{count} Einträge protokolliert"
