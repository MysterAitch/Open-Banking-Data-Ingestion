"""The pages that used to be sections of the home page, and the System strip that replaced them.

Each page opens with what it is for, then leads with state, then offers its
actions, then folds reference material and rarely used controls behind
`<details>`. The section renderers themselves still live in `web.py` beside the
rest of the markup; this module composes them into pages and owns the routes.

THE PAGES ARE BUILT FROM THE SAME HOOKS THE HOME PAGE USED, with the same
tolerances: an unwired hook hides its part, and a hook that raises is handled
inside the renderer that calls it, exactly as before. Every hook is timed, and
a slow render names its slowest hooks in the log (see `HookTimer`), because a
page nobody can see into is diagnosed by guesswork.

THE WAY BACK. A page an action came from is where a person wants to return to,
so a result page offers it first and the Overview second (`way_back`).
"""

from __future__ import annotations

import html
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ParamSpec, TypeVar
from urllib.parse import urlparse

from .alerts import consent_rung
from .buildinfo import describe
from .callback import render_page
from .connections import ConnectionStore
from .web_gaps import FETCH_NEXT_LINE
from .web_scheduler import scheduler_section

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .accounts import AccountRecord
    from .coverage import SourceCoverage
    from .spaces import ArchiveNote
    from .web import ExtendableAccount, WebConfig

_esc = html.escape

_HookParams = ParamSpec("_HookParams")
_HookReturn = TypeVar("_HookReturn")

#: The Overview, as the second button on every result page.
HOME_LINK = '<p><a class="button" href="/">Back to overview</a></p>'

#: The pages a result page can send a person back to, and what the button says.
#: The wording avoids "Back to connections", which the navigation tests hold out
#: of every page as a stale label from before the strip existed.
WAY_BACK_PAGES: dict[str, str] = {
    "/connections": "Back to bank connections",
    "/actual": "Back to Actual sync",
    "/coverage": "Back to coverage by source",
    "/diagnostics": "Back to Diagnostics",
    "/import": "Back to import",
}


def back_link(href: str) -> str:
    """One button back to a known page.

    An address that is not a known page raises, so a typo in a handler fails
    in its own test instead of leaving a person on a dead-end page.
    """
    return f'<p><a class="button" href="{_esc(href)}">{_esc(WAY_BACK_PAGES[href])}</a></p>'


def way_back(href: str) -> str:
    """The way back to the page an action came from, then to the Overview."""
    return back_link(href) + HOME_LINK


def referring_page(referer: str | None, default: str) -> str:
    """Which of the known pages an action was pressed on, else `default`.

    Read from the Referer header, and only its path is used, matched against
    the known pages: a forged or foreign value can therefore choose among our
    own relative links and nothing else.
    """
    if referer:
        path = urlparse(referer).path.rstrip("/")
        if path in WAY_BACK_PAGES:
            return path
    return default


class HookTimer:
    """Times every hook a page calls and reports a slow render by hook.

    Three refuted theories (locks, raw-JSON parsing, provider calls) proved
    that nobody can guess where forty seconds of a render lives, so a render
    over the threshold prints its own cost breakdown.
    """

    def __init__(self) -> None:
        self.seconds: dict[str, float] = {}
        self._began = time.perf_counter()

    def wrap(
        self, name: str, hook: Callable[_HookParams, _HookReturn] | None
    ) -> Callable[_HookParams, _HookReturn] | None:
        if hook is None:
            return None
        bound = hook

        def call(*args: _HookParams.args, **kwargs: _HookParams.kwargs) -> _HookReturn:
            began = time.perf_counter()
            try:
                return bound(*args, **kwargs)
            finally:
                self.seconds[name] = self.seconds.get(name, 0.0) + time.perf_counter() - began

        return call

    def report(self, route: str) -> None:
        render_seconds = time.perf_counter() - self._began
        threshold = float(os.environ.get("OBDI_WEB_SLOW_RENDER_SECS", "2.0"))
        if render_seconds < threshold:
            return
        slowest = sorted(self.seconds.items(), key=lambda kv: kv[1], reverse=True)[:8]
        accounted = sum(self.seconds.values())
        print(
            f"web timing: {route} rendered in {render_seconds:.2f}s - "
            + ", ".join(f"{name} {secs:.2f}s" for name, secs in slowest)
            + f" (hooks total {accounted:.2f}s; the remainder is "
            "templating and store-free work)",
            flush=True,
        )


def _lede(text: str) -> str:
    return f'<p class="lede">{_esc(text)}</p>'


def _nothing_wired(what: str) -> str:
    return (
        f'<p class="muted">{_esc(what)} is not wired on this instance, or has '
        "nothing to show yet.</p>"
    )


# ---------------------------------------------------------------- System strip


def _fact(label: str, href: str, body: str) -> str:
    return (
        f'<li class="fact"><a class="tap" href="{_esc(href)}"><strong>{_esc(label)}</strong></a>'
        f"{body}</li>"
    )


def _actual_fact(actual_status: Callable[[], list[dict[str, object]]] | None) -> str:
    from . import web

    if actual_status is None:
        return '<p class="muted">not wired on this instance</p>'
    try:
        results = actual_status()
    except Exception:
        return '<p class="bad">results could not be read</p>'
    push = web._newest_of_kind(results, "push")
    if push is None:
        return '<p class="muted">no push recorded</p>'
    stamp = web._stamp_z(push)
    if push.get("ok"):
        return f"<p>last push applied {stamp}</p>"
    return f'<p class="bad">last push FAILED {stamp}</p>'


def _connections_fact(store: ConnectionStore, now: datetime) -> str:
    connections = list(store)
    if not connections:
        return '<p class="muted">no banks connected</p>'
    dated = [
        (expiry, connection.consent_days_remaining(now=now))
        for connection in connections
        if (expiry := connection.consent_expires_on()) is not None
    ]
    count = f"{len(connections)} bank{'' if len(connections) == 1 else 's'} connected"
    if not dated:
        return f"<p>{count}</p>"
    soonest, days = min(dated, key=lambda pair: pair[0])
    css = "warn" if consent_rung(days) is not None else "muted"
    return (
        f"<p>{count}</p>"
        f'<p class="{css}">soonest consent expires {soonest.isoformat()} ({days} days)</p>'
    )


def _rebuild_fact(
    rebuild_status: Callable[[], dict[str, object]] | None,
    recent_rebuilds: Callable[[], list[dict[str, object]]] | None,
) -> str:
    status: dict[str, object] = {}
    if rebuild_status is not None:
        try:
            status = rebuild_status() or {}
        except Exception:
            status = {}
    state = str(status.get("state", ""))
    if state == "running":
        return '<p class="warn">rebuild running</p>'
    if state == "done":
        ok, finished = bool(status.get("ok")), str(status.get("finished_at", ""))
    else:
        runs: list[dict[str, object]] = []
        if recent_rebuilds is not None:
            try:
                runs = recent_rebuilds()
            except Exception:
                runs = []
        if not runs:
            return '<p class="muted">no rebuild recorded</p>'
        newest = max(runs, key=lambda run: str(run.get("finished_at", "")))
        ok, finished = bool(newest.get("ok")), str(newest.get("finished_at", ""))
    stamp = _esc(finished[:16].replace("T", " ")) + "Z" if finished else "at an unrecorded time"
    if ok:
        return f"<p>last rebuild ok, {stamp}</p>"
    return f'<p class="bad">last rebuild FAILED, {stamp}</p>'


def system_strip_html(
    store: ConnectionStore,
    *,
    scheduler_heartbeat: Callable[[], dict[str, object]] | None,
    actual_status: Callable[[], list[dict[str, object]]] | None,
    rebuild_status: Callable[[], dict[str, object]] | None,
    recent_rebuilds: Callable[[], list[dict[str, object]]] | None,
    now: datetime | None = None,
    with_actual: bool = True,
) -> str:
    """Facts about the machinery, each a link to the page that owns it.

    Links only: the home page carries no controls, so nothing here can be
    pressed by accident while scrolling past. `with_actual` is False where the page
    already carries the push's state as a status line of its own.
    """
    from . import web

    moment = now or datetime.now(UTC)
    scheduler = web._scheduler_row(scheduler_heartbeat, moment) or (
        '<p class="muted">no scheduler cycle recorded</p>'
    )
    facts = (
        _fact("Scheduler", "/connections", scheduler)
        + (_fact("Actual", "/actual", _actual_fact(actual_status)) if with_actual else "")
        + _fact("Connections", "/connections", _connections_fact(store, moment))
        + _fact("Rebuild", "/admin", _rebuild_fact(rebuild_status, recent_rebuilds))
        + _fact("Build", "/admin", f'<p class="mono">{_esc(describe())}</p>')
    )
    return f'<div class="overview"><h2 id="system">System</h2><ul class="system">{facts}</ul></div>'


# ---------------------------------------------------------------------- Pages


def render_connections(
    store: ConnectionStore,
    *,
    bank_authorisation: bool = True,
    rename_connection: Callable[[str, str], str] | None = None,
    starling_status: Callable[[], dict[str, object] | None] | None = None,
    provider_knowledge: Callable[[], list[dict[str, object]]] | None = None,
    extendables: Callable[[], list[ExtendableAccount]] | None = None,
    backfill_status: Callable[[], dict[str, object]] | None = None,
    fetch_now_available: bool = False,
    scheduler_heartbeat: Callable[[], dict[str, object]] | None = None,
) -> bytes:
    from . import web

    body = (
        web._credential_banner(bank_authorisation)
        + web._backfill_running_banner(backfill_status)
        + _lede(
            "The banks that feed this store, and how long each consent has left. A bank "
            "makes you reconfirm every ninety days, only you can do that at the bank, and "
            "the page below says when."
        )
        + "<h2>Banks and their consent</h2>"
        + web._connection_rows(store, rename_available=rename_connection is not None)
        + web._starling_row(starling_status)
        + scheduler_section(scheduler_heartbeat)
        + web._add_a_bank_section(bank_authorisation)
        + (
            web._fetch_now_rows(store, starling_status, backfill_status)
            if fetch_now_available
            else ""
        )
        + web._extend_rows(extendables, fetch_now=fetch_now_available)
        + web._knowledge_rows(provider_knowledge)
    )
    return render_page("Bank connections", body)


def render_actual(
    *,
    push_actual: Callable[[], str] | None = None,
    actual_status: Callable[[], list[dict[str, object]]] | None = None,
    actual_roster: Callable[[], list[dict[str, object]]] | None = None,
    actual_queue: Callable[[], list[dict[str, object]]] | None = None,
    audit_actual: Callable[[], str] | None = None,
    prune_actual: Callable[..., str] | None = None,
    actual_heartbeat: Callable[[], str] | None = None,
    rebuild_status: Callable[[], dict[str, object]] | None = None,
    rebuild_busy_note: Callable[[], str | None] | None = None,
    empty_actual: Callable[..., str] | None = None,
    marker_actual: Callable[[], str] | None = None,
    align_actual: Callable[..., str] | None = None,
    actual_configured: Callable[[], bool] | None = None,
    now: datetime | None = None,
) -> bytes:
    from . import web

    configured = True
    if actual_configured is not None:
        try:
            configured = actual_configured()
        except Exception:
            # An unreadable setting must not hide the page: the buttons then answer for themselves.
            configured = True
    section = web._actual_rows(
        actual_status,
        push_actual is not None,
        actual_roster,
        actual_queue,
        audit_available=audit_actual is not None,
        actual_heartbeat=actual_heartbeat,
        prune_available=prune_actual is not None,
        empty_available=empty_actual is not None,
        marker_available=marker_actual is not None,
        align_available=align_actual is not None,
        configured=configured,
        now=now,
    )
    body = (
        web._rebuild_running_banner(rebuild_status, rebuild_busy_note)
        + (section or _nothing_wired("The Actual sync"))
        + '<p class="muted">No amounts are shown on this page.</p>'
    )
    return render_page("Actual sync", body, wide=True)


def render_coverage(
    *,
    holdings: Callable[[], list[SourceCoverage]] | None = None,
    display_labels: Callable[[], dict[str, str]] | None = None,
    account_timelines: Callable[[], dict[str, dict[str, str]]] | None = None,
    account_feeders: Callable[[], dict[str, list[str]]] | None = None,
    source_connections: dict[tuple[str, str], list[str]] | None = None,
    feed_warnings: Callable[[], list[str]] | None = None,
    archive_notes: Callable[[], dict[str, ArchiveNote]] | None = None,
) -> bytes:
    from . import web

    section = web._holdings_rows(
        holdings,
        display_labels,
        account_timelines,
        account_feeders,
        source_connections,
        feed_warnings,
        archive_notes,
    )
    body = _lede(
        "What is held, one row for each source feeding an account, with the stretch of "
        "history each source covers. The Accounts cards on the Overview are one card "
        "per account, however many sources feed it; this page is where a source is "
        "named, bound to an account, or archived."
    ) + FETCH_NEXT_LINE + (section or _nothing_wired("Coverage"))
    body += (
        '<p class="muted"><a class="tap" href="/coverage-timeline">'
        "The same history, by day, as a chart</a></p>"
    )
    return render_page("Coverage by source", body)


def render_import(
    *,
    display_labels: Callable[[], dict[str, str]] | None = None,
    declared_accounts: Callable[[], list[AccountRecord]] | None = None,
    held_accounts: Callable[[], list[str]] | None = None,
) -> bytes:
    import contextlib

    from . import web
    from .web_accounts import picker_labels

    # The preview verifies the file against what the chosen account already
    # holds, so the destination is picked before anything is read.
    labels: dict[str, str] = {}
    if display_labels is not None:
        with contextlib.suppress(Exception):
            labels = display_labels()
    declared: list[AccountRecord] = []
    held: list[str] = []
    if declared_accounts is not None:
        with contextlib.suppress(Exception):
            declared = declared_accounts()
    if held_accounts is not None:
        with contextlib.suppress(Exception):
            held = held_accounts()
    labels = picker_labels(labels, declared, held)
    lede = _lede(
        "Two ways to bring history in from outside a bank connection: a bank's CSV "
        "or QIF export, or a PDF statement."
    )
    body = f"""{lede}
<h2>Import a file</h2>
<p>Bank CSV or QIF exports. Choose the destination first - the preview can
then verify the file against what that account already holds, before
anything is stored.</p>
<form action="/upload" method="post" enctype="multipart/form-data">
  {web.account_picker(labels)}
  <p><input type="file" name="statement" aria-label="Statement file" required></p>
  <p><button class="button" type="submit"
     style="border:0;width:100%;font-size:inherit;cursor:pointer">Preview import</button></p>
</form>
<h2>Upload a statement (PDF)</h2>
<p>A PDF statement is kept as evidence and its layout is shown with every value
masked, so a parser can be taught the shape before any rows are read.</p>
<p><a class="button secondary" href="/statement-shape">Upload a statement</a></p>"""
    return render_page("Import", body)


class SectionPages:
    """The section routes, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _connections_page(self) -> None:
        config, timer = self.bound_config, HookTimer()
        page = render_connections(
            config.connection_store,
            bank_authorisation=config.bank_authorisation,
            rename_connection=config.rename_connection,
            starling_status=timer.wrap("starling_status", config.starling_status),
            provider_knowledge=timer.wrap("provider_knowledge", config.provider_knowledge),
            extendables=timer.wrap("extendables", config.extendables),
            backfill_status=timer.wrap("backfill_status", config.backfill_status),
            fetch_now_available=config.fetch_now is not None,
            scheduler_heartbeat=timer.wrap("scheduler_heartbeat", config.scheduler_heartbeat),
        )
        timer.report("/connections")
        self._respond(200, page)

    def _actual_page(self) -> None:
        config, timer = self.bound_config, HookTimer()
        page = render_actual(
            push_actual=config.push_actual,
            actual_status=timer.wrap("actual_status", config.actual_status),
            actual_roster=timer.wrap("actual_roster", config.actual_roster),
            actual_queue=timer.wrap("actual_queue", config.actual_queue),
            audit_actual=config.audit_actual,
            prune_actual=config.prune_actual,
            empty_actual=config.empty_actual,
            marker_actual=config.marker_actual,
            align_actual=config.align_actual,
            actual_configured=config.actual_configured,
            actual_heartbeat=timer.wrap("actual_heartbeat", config.actual_heartbeat),
            rebuild_status=timer.wrap("rebuild_status", config.rebuild_status),
            rebuild_busy_note=timer.wrap("rebuild_busy_note", config.rebuild_busy_note),
        )
        timer.report("/actual")
        self._respond(200, page)

    def _coverage_page(self) -> None:
        config, timer = self.bound_config, HookTimer()
        connections = timer.wrap("source_connections", config.source_connections)
        page = render_coverage(
            holdings=timer.wrap("holdings", config.holdings),
            display_labels=timer.wrap("display_labels", config.display_labels),
            account_timelines=timer.wrap("account_timelines", config.account_timelines),
            account_feeders=timer.wrap("account_feeders", config.account_feeders),
            source_connections=connections() if connections is not None else None,
            feed_warnings=timer.wrap("feed_warnings", config.feed_warnings),
            archive_notes=timer.wrap("archive_notes", config.archive_notes),
        )
        timer.report("/coverage")
        self._respond(200, page)

    def _import_page(self) -> None:
        config, timer = self.bound_config, HookTimer()
        page = render_import(
            display_labels=timer.wrap("display_labels", config.display_labels),
            declared_accounts=timer.wrap("declared_accounts", config.declared_accounts),
            held_accounts=timer.wrap("held_accounts", config.held_accounts),
        )
        timer.report("/import")
        self._respond(200, page)
