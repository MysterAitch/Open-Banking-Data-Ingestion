"""The owner's decisions about what is still to fetch: setting a period aside, undoing it, and how
far back he keeps. The decisions are `fetch_marks`' and the words `web_marks`'; this is the routes.

Bring in lists the files wanted and links each to the form here (`/gaps-mark`), which says what
the store holds for the period beside each reason before anything is confirmed. Served on a GET,
so dates, account names, source names, counts, and the owner's own note only: no amount and no
description. Every answer leads back to Bring in.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from .callback import render_page
from .ingest.rebuild_hold import RebuildInProgress
from .ingest.store import Store
from .navigation import page_name
from .read.account_names import AccountsShown
from .read.fetch_marks import (
    CLAIM_KINDS,
    KINDS,
    Evidence,
    MarkKind,
    MarkRefused,
    MarkWorld,
    Standing,
    gather_evidence,
    judge,
    make_mark,
    read_mark,
    remove_mark,
    scopes_in,
    set_scope,
)
from .verify.statement_span import STATEMENT_SOURCES
from .web_marks import MARKS_STYLE_TAG, form_html, mark_query, span_words

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from .web import WebConfig

_esc = html.escape

_BACK_TO_BRING_IN = '<p><a class="button" href="/bring-in">Back to Bring in</a></p>'


class SetAsidePages:
    """The set-aside routes, composed into the request handler."""

    @property
    def bound_config(self) -> WebConfig:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _respond(self, status: int, body: bytes, *, no_store: bool = False) -> None:
        raise NotImplementedError

    def _account_names(self) -> AccountsShown:
        """Supplied by the handler this is composed into."""
        raise NotImplementedError

    def _marks_page(self, status: int, title: str, body: str) -> None:
        self._respond(
            status,
            render_page(title, MARKS_STYLE_TAG + body, body_class="gaps-page"),
            no_store=True,
        )

    def _marks_refused(self, why: str, values: Mapping[str, str]) -> None:
        again = mark_query(
            values.get("account", ""),
            values.get("source", ""),
            _try_date(values.get("first", "")),
            _try_date(values.get("last", "")),
        )
        self._marks_page(
            409,
            "Not set aside",
            f'<p class="bad"><strong>{_esc(why)}</strong></p>'
            "<p>Nothing was changed.</p>"
            f'<p><a class="button" href="{_esc(again)}">Change it</a></p>{_BACK_TO_BRING_IN}',
        )

    def _marks_rebuilding(self, paused: RebuildInProgress) -> None:
        self._marks_page(
            503, "Set a period aside", f'<p class="lede">{_esc(paused.hold.sentence())}</p>'
        )

    def _marks_world(self, today: date) -> tuple[MarkWorld, object] | None:
        read = self.bound_config.fetch_marks_read
        if read is None:
            self._respond(404, render_page("Not available", "<p>Not wired.</p>"))
            return None
        try:
            return read(today)
        except RebuildInProgress as paused:
            self._marks_rebuilding(paused)
            return None

    def _gaps_mark_form(self, params: Mapping[str, list[str]]) -> None:
        values = {
            key: (params.get(key, [""])[0] or "").strip()
            for key in ("account", "source", "first", "last", "kind", "note", "review_on")
        }
        self._render_mark_form(values, datetime.now(UTC).date())

    def _render_mark_form(self, values: Mapping[str, str], today: date, *, why: str = "") -> None:
        found = self._marks_world(today)
        if found is None:
            return
        world = found[0]
        names = self._account_names()
        account, source = values.get("account", ""), values.get("source", "")
        first, last = _try_date(values.get("first", "")), _try_date(values.get("last", ""))
        previews: dict[MarkKind, tuple[Standing, str, Evidence]] = {}
        if account and last is not None and (account in world.rows or account in world.declared):
            for kind in CLAIM_KINDS:
                evidence = gather_evidence(
                    world, account, source, first, last, statement_sources=STATEMENT_SOURCES
                )
                standing, reason = judge(
                    kind, source, first, last, evidence, statement_sources=STATEMENT_SOURCES
                )
                previews[kind] = (standing, reason, evidence)
        body = (f'<p class="bad"><strong>{_esc(why)}</strong></p>' if why else "") + form_html(
            account=account,
            source=source,
            first=values.get("first", ""),
            last=values.get("last", ""),
            kind=values.get("kind", ""),
            note=values.get("note", ""),
            review_on=values.get("review_on", ""),
            today=today,
            names=names,
            accounts=sorted((set(world.rows) | set(world.declared)) - world.spaces),
            sources=sorted({src for rows in world.rows.values() for _, src in rows}),
            previews=previews,
            parsed_last=last,
            parsed_first=first,
        )
        self._marks_page(200, page_name("/gaps-mark"), body + _BACK_TO_BRING_IN)

    def _gaps_mark_post(self, form: Mapping[str, list[str]]) -> None:
        values = {
            key: (form.get(key, [""])[0] or "").strip()
            for key in (
                "account", "source", "first", "last", "kind", "note", "review_on", "origin", "step"
            )
        }
        today = datetime.now(UTC).date()
        if values["step"] == "check" or not values["kind"]:
            self._render_mark_form(values, today)
            return
        write = self.bound_config.fetch_marks_write
        if write is None:
            self._respond(404, render_page("Not available", "<p>Not wired.</p>"))
            return
        try:
            first = _need_date(values["first"], optional=True)
            last = _need_date(values["last"])
            review_on = _need_date(values["review_on"], optional=True)
        except MarkRefused as bad:
            self._marks_refused(str(bad), values)
            return
        if last is None:
            self._marks_refused("A last day is needed.", values)
            return
        stamp = datetime.now(UTC).isoformat(timespec="seconds")

        def act(store: Store, world: MarkWorld) -> str:
            made = make_mark(
                store, world,
                account=values["account"], source=values["source"], kind=values["kind"],
                first_day=first, last_day=last, note=values["note"], review_on=review_on,
                origin=values["origin"] or "owner", now=stamp, today=today,
                statement_sources=STATEMENT_SOURCES,
            )
            reading = read_mark(made, world, today, statement_sources=STATEMENT_SOURCES)
            meaning = KINDS[made.kind]
            said = (
                f"Set aside as {meaning.label.lower()}: {made.account}, "
                f"{span_words(made.first_day, made.last_day)}."
            )
            if made.kind is MarkKind.KNOWN_GAP:
                return (
                    said + " The gap is no longer listed to fetch, and the data is still missing."
                )
            if reading.standing is Standing.CONTRADICTED:
                return (
                    said + " What is held disagrees with it, so the gap stays in the list until "
                    "you change or remove this mark."
                )
            return said

        try:
            sentence = write(act)
        except MarkRefused as refused:
            self._marks_refused(str(refused), values)
            return
        except RebuildInProgress as paused:
            self._marks_rebuilding(paused)
            return
        names = self._account_names()
        self._marks_page(200, "Period set aside", _ok_html(sentence, names))

    def _gaps_mark_undo_post(self, form: Mapping[str, list[str]]) -> None:
        write = self.bound_config.fetch_marks_write
        if write is None:
            self._respond(404, render_page("Not available", "<p>Not wired.</p>"))
            return
        text = (form.get("id", [""])[0] or "").strip()
        stamp = datetime.now(UTC).isoformat(timespec="seconds")

        def act(store: Store, world: MarkWorld) -> str:
            if not text.isdigit():
                raise MarkRefused("That is not a mark this page gave you.")
            removed = remove_mark(store, int(text), stamp)
            return (
                f"Removed the mark: {KINDS[removed.kind].label.lower()} for {removed.account}, "
                f"{span_words(removed.first_day, removed.last_day)}. Whatever it set aside is "
                "listed again."
            )

        try:
            sentence = write(act)
        except MarkRefused as refused:
            self._marks_refused(str(refused), {})
            return
        except RebuildInProgress as paused:
            self._marks_rebuilding(paused)
            return
        names = self._account_names()
        self._marks_page(200, "Mark removed", _ok_html(sentence, names))

    def _gaps_scope_post(self, form: Mapping[str, list[str]]) -> None:
        write = self.bound_config.fetch_marks_write
        if write is None:
            self._respond(404, render_page("Not available", "<p>Not wired.</p>"))
            return
        account = (form.get("account", [""])[0] or "").strip()
        mode = (form.get("mode", [""])[0] or "").strip()
        months_text = (form.get("months", [""])[0] or "").strip()
        first_text = (form.get("first", [""])[0] or "").strip()
        today = datetime.now(UTC).date()
        stamp = datetime.now(UTC).isoformat(timespec="seconds")

        def act(store: Store, world: MarkWorld) -> str:
            who = account or "every account"
            if mode == "clear":
                store.clear_record_scope(account)
                return f"You now keep all of the past for {who}."
            if mode not in ("rolling", "fixed"):
                raise MarkRefused("Choose how far back you keep: a number of months or a day.")
            months = None
            first = None
            if mode == "rolling":
                if not months_text.isdigit():
                    raise MarkRefused("Say how many months to keep, as a whole number.")
                months = int(months_text)
            else:
                first = _need_date(first_text, what="A first day")
            set_scope(store, world, account=account, first_day=first, months=months, now=stamp)
            kept = scopes_in(store)[account]
            return f"You now keep {who} {kept.describe(today)}; earlier days are not looked for."

        try:
            sentence = write(act)
        except MarkRefused as refused:
            self._marks_refused(str(refused), {})
            return
        except RebuildInProgress as paused:
            self._marks_rebuilding(paused)
            return
        names = self._account_names()
        self._marks_page(200, "How far back you keep", _ok_html(sentence, names))


def _ok_html(sentence: str, names: AccountsShown) -> str:
    """The one sentence that says what happened, with account labels as every page writes them,
    and the way back."""
    return f'<p class="ok"><strong>{_esc(names.in_text(sentence))}</strong></p>{_BACK_TO_BRING_IN}'


def _try_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _need_date(text: str, *, optional: bool = False, what: str = "A last day") -> date | None:
    """A day written like 2026-10-05, or None where it is optional and empty."""
    if not text and optional:
        return None
    found = _try_date(text)
    if found is None:
        raise MarkRefused(
            "A day must be written like 2026-10-05." if text else f"{what} is needed."
        )
    return found
