"""What the rows that carry a definitive identifier say about the rows that carry none.

`docs/design/2026-10-commitments/entities.md` section 4a is the reasoning. The rows of one
identifier U (a source's id for a party, or the party's account) teach that every one of them
OPENS the same way; what a description-only row needs is the reverse, that every row opening so
belongs to U, which nothing measures over the rows it will be applied to. So what is learned is a
RULE held as a hypothesis, and the record of it carries both numbers: how many of U's rows taught
it, and how many OTHER identified rows it was tested against (none of which opened so).

The strength of the evidence is that second number: with none of N tested rows matching, the
one-sided 95% bound on the share of such rows that would is about 3/N (the rule of three). A rule
is never hidden for being weak. Two settings decide its fate and both are the owner's to move
(`RuleSettings`): SUPPORT, how many of U's rows must share an opening before a rule is learned,
and CONFIDENCE, how many other identified rows a rule must have been tested against to be APPLIED
BY DEFAULT. Below it the rule is OFFERED: shown with its rows and not linked until ticked
(`keep_rule`). A rule the owner ticked or refused keeps that decision whatever the settings become.

Pure: it reads forms (a row's description as comparable words) and decides. It asks the store
nothing, except `rule_policy`, which reads the settings and the owner's decisions in one
statement.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .entity_tokens import Token, distinctive_words

if TYPE_CHECKING:  # pragma: no cover - imported for types alone
    from ..ingest.store import Store

#: A row's description as the comparable words of its reading (`entity_tokens.tokens_of`).
Form = tuple[str, ...]

#: How many of an identifier's rows must share an opening before a rule is learned. Two, since one
#: row is a description and teaches no pattern; raising it trades missed rules for fewer wrong ones.
SUPPORT_DEFAULT = 2

#: How many other identified rows a rule must have been tested against to be applied by default.
#: With none of N matching, the 95% bound on the share that would is about 3/N, so 300 is "fewer
#: than 1 in 100 would belong to someone else, if the unidentified rows are like them": the
#: strictest reading that a household's few thousand rows can still meet. A guess until the real
#: store has been read; `notes.md` records what each value decided on the invented stores.
CONFIDENCE_DEFAULT = 300

#: An opening of a single word must be at least this long to be distinctive; a shorter lone word
#: ("tesco") opens too many names. Two words or more are distinctive when one of them is.
LONE_OPENING_LETTERS = 8

SUPPORT_SETTING = "learned-rules.support"
CONFIDENCE_SETTING = "learned-rules.confidence"
KEPT_PREFIX = "learned-rules.kept:"
ORIGIN_PREFIX = "learned-rules.origin:"
AGREED_PREFIX = "learned-rules.agreed:"
DISAGREED_PREFIX = "learned-rules.disagreed:"

APPLIED = "applied"
OFFERED = "offered"
WITHDRAWN = "withdrawn"


@dataclass(frozen=True)
class RuleSettings:
    """The two numbers that decide a rule's fate (module docstring)."""

    support: int = SUPPORT_DEFAULT
    confidence: int = CONFIDENCE_DEFAULT


DEFAULT_SETTINGS = RuleSettings()


@dataclass(frozen=True)
class RulePolicy:
    """The settings and the owner's decisions: `kept` rules are applied and `refused` ones
    withdrawn whatever the settings say, by `rule_key`."""

    settings: RuleSettings = RuleSettings()
    kept: frozenset[str] = frozenset()
    #: For each entity rule the owner kept from a learned one (`rule_origin`), the rule's id to
    #: what was true when it was kept: how many rows taught it and the day.
    origins: Mapping[int, tuple[int, str]] = field(default_factory=dict)
    #: For each rule key, how many later-identified rows agreed with its inference and how many
    #: disagreed (`rule_confirmation`). One disagreement withdraws the rule, the same way one
    #: "Not this" does.
    agreed: Mapping[str, int] = field(default_factory=dict)
    disagreed: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    """An opening that every row of `party` shares: `taught` rows of the party carry it, and
    `tested` other identified rows were checked, none of which opens so."""

    party: str
    opening: Form
    taught: int
    tested: int

    @property
    def key(self) -> str:
        return rule_key(self.party, self.opening)

    @property
    def words(self) -> str:
        return " ".join(self.opening)


@dataclass(frozen=True)
class Learning:
    """The rules learned, and how many openings were shared by two parties and so taught nothing."""

    rules: tuple[Rule, ...] = ()
    shared: int = 0


@dataclass(frozen=True)
class RuleView:
    """A rule as the Entities page lists it: its state, the description-shapes of the rows it
    links (applied) or would link (offered), and how many rows those are."""

    rule: Rule
    state: str
    shapes: tuple[str, ...] = ()
    rows: int = 0
    #: Whether the owner ticked it (so it is applied whatever the confidence setting says).
    ticked: bool = False
    #: Later-identified rows that agreed with the rule's inference, and that disagreed (which
    #: withdrew it): the one measurement of the reverse direction the rows ever give.
    confirmed: int = 0
    disagreed: int = 0


def rule_key(party: str, opening: Form) -> str:
    """What the owner's decision on a rule is kept under: the party and the opening."""
    return f"{party}|{' '.join(opening)}"


def opens(form: Form, opening: Form) -> bool:
    return len(form) >= len(opening) and form[: len(opening)] == opening


def is_distinctive(opening: Form) -> bool:
    """Whether an opening can tell one party from another: it holds a word that does
    (`distinctive_words`: not a payment method, a function word, or a code), and is two words or
    more, or one word of `LONE_OPENING_LETTERS` letters or more."""
    if not opening:
        return False
    tokens = [Token(word, word) for word in opening]
    if not distinctive_words(tokens):
        return False
    return len(opening) >= 2 or len(opening[0]) >= LONE_OPENING_LETTERS


def _common_opening(forms: Sequence[Form]) -> Form:
    first = forms[0]
    length = len(first)
    for form in forms[1:]:
        length = min(length, len(form))
        for index in range(length):
            if form[index] != first[index]:
                length = index
                break
    return first[:length]


def learn_rules(
    forms: Mapping[str, Sequence[Form]],
    support: int,
) -> Learning:
    """The rules the identified rows teach. `forms` holds, for each identifier, the form of each
    of its rows that has a description.

    A rule needs `support` rows (two at the least) that share a distinctive opening and that no
    other identifier's row opens. An opening that another identifier's row shares is counted in
    `Learning.shared` and teaches nothing.

    The population a rule is TESTED against is the other identified rows that have a description
    to open: a row with none cannot open any way, so counting it would make the bound stronger
    than the evidence (the page's count of rows that carry an identifier is larger for that
    reason, and says so once)."""
    described = {party: sum(1 for form in fs if form) for party, fs in forms.items()}
    total = sum(described.values())
    by_first: dict[str, list[tuple[str, Form]]] = defaultdict(list)
    for party, party_forms in forms.items():
        for form in party_forms:
            if form:
                by_first[form[0]].append((party, form))
    rules: list[Rule] = []
    shared: set[Form] = set()
    for party, party_forms in forms.items():
        usable = [form for form in party_forms if form]
        if len(usable) < max(support, 2):
            continue
        opening = _common_opening(usable)
        if not is_distinctive(opening):
            continue
        if any(
            other != party and opens(form, opening) for other, form in by_first[opening[0]]
        ):
            shared.add(opening)
            continue
        rules.append(Rule(party, opening, len(usable), total - described[party]))
    rules.sort(key=lambda r: (-r.tested, r.party))
    return Learning(tuple(rules), len(shared))


def decide(rule: Rule, policy: RulePolicy, withdrawn: Collection[str] = ()) -> str:
    """Whether a rule is `APPLIED` (its description-only rows linked), `OFFERED` (shown, unticked,
    not linked), or `WITHDRAWN`. A refusal (`withdrawn`, by `rule_key`) beats a tick, and a tick
    beats the confidence setting."""
    if rule.key in withdrawn:
        return WITHDRAWN
    if rule.key in policy.kept or rule.tested >= policy.settings.confidence:
        return APPLIED
    return OFFERED


def bound_sentence(tested: int) -> str:
    """What N other identified rows, none opening so, support - always with its caveat."""
    if tested <= 0:
        return (
            "tested against no other identified rows, so nothing is known about whether "
            "others open so"
        )
    share = max(1, tested // 3)
    return (
        f"tested against {tested:,} other identified rows, none opened so - if the unidentified "
        f"rows are like them, fewer than 1 in {share:,} would belong to someone else, though the "
        "unidentified rows need not be like them"
    )


def method_sentence() -> str:
    """How a rule is made and what its numbers mean, said once at the head of the section and
    never per rule: a rule is a guess from rows a source identified, tested only against other
    identified rows that have a description, and its bound is only as good as the likeness of the
    rows it is applied to."""
    return (
        "A rule is a guess made from rows a source identified. When every one of a party's "
        "described rows begins its description the same way, and none of the other identified "
        "rows that have a description does, a row with no identifier that begins so is taken to "
        "be that party's. \"Tested against M\" counts those other rows. With none of M opening "
        "so, fewer than 1 in M/3 of such rows would belong to someone else - if the rows with no "
        "identifier are like the ones tested, which they need not be."
    )


def rule_sentence(rule: Rule, state: str) -> str:
    """The rule as the page says it: the opening, the two numbers, and the bound; an offered rule
    says it is unable to be confirmed from the rows held."""
    evidence = (
        f"opens as every one of this party's {rule.taught:,} identified rows does and none of "
        f"the other {rule.tested:,} identified rows does"
    )
    if state == OFFERED:
        return (
            f"{evidence}; unable to confirm from the rows held: tested against only "
            f"{rule.tested:,} other identified rows - {bound_sentence(rule.tested)}"
        )
    return f"{evidence}; {bound_sentence(rule.tested)}"


def summary_sentence(states: Sequence[str], shared: int) -> str:
    """What the current settings decide, counted: the line beside the form that sets them."""
    applied = sum(1 for s in states if s == APPLIED)
    offered = sum(1 for s in states if s == OFFERED)
    withdrawn = sum(1 for s in states if s == WITHDRAWN)
    text = (
        f"At these settings {applied:,} {'rule applies' if applied == 1 else 'rules apply'} by "
        f"default and {offered:,} {'is' if offered == 1 else 'are'} offered unticked; "
        f"{shared:,} {'opening is' if shared == 1 else 'openings are'} shared by two parties and "
        "teach nothing"
    )
    return text + (f"; {withdrawn:,} withdrawn by you." if withdrawn else ".")


def rule_policy(store: Store) -> RulePolicy:
    """The settings and the rules the owner ticked, read in one statement."""
    held = store.preferences_with_prefix("learned-rules.")
    return RulePolicy(
        RuleSettings(
            _whole(held.get(SUPPORT_SETTING), SUPPORT_DEFAULT),
            _whole(held.get(CONFIDENCE_SETTING), CONFIDENCE_DEFAULT),
        ),
        frozenset(name[len(KEPT_PREFIX) :] for name in held if name.startswith(KEPT_PREFIX)),
        _origins(held),
        _tallies(held, AGREED_PREFIX),
        _tallies(held, DISAGREED_PREFIX),
    )


def _tallies(held: Mapping[str, str], prefix: str) -> dict[str, int]:
    return {
        name[len(prefix) :]: int(value)
        for name, value in held.items()
        if name.startswith(prefix) and value.isdigit()
    }


def _origins(held: Mapping[str, str]) -> dict[int, tuple[int, str]]:
    found: dict[int, tuple[int, str]] = {}
    for name, value in held.items():
        if not name.startswith(ORIGIN_PREFIX):
            continue
        rule = name[len(ORIGIN_PREFIX) :]
        taught, _, day = value.partition("|")
        if rule.isdigit() and taught.isdigit():
            found[int(rule)] = (int(taught), day)
    return found


def record_origin(store: Store, rule_id: int, taught: int, day: str) -> None:
    """Remember what a kept entity rule was learned from: the rows that taught it and the day the
    owner kept it. The rule itself is the owner's claim from then on; this is its provenance."""
    store.set_preference(f"{ORIGIN_PREFIX}{rule_id}", f"{taught}|{day}")


def origin_sentence(taught: int, day: str) -> str:
    return f"learned from {taught:,} identified rows, kept by you on {day}"


def _whole(value: str | None, default: int) -> int:
    try:
        found = int(value) if value is not None else default
    except ValueError:
        return default
    return found if found >= 1 else default


def set_settings(store: Store, support: int, confidence: int) -> None:
    """Keep the two settings; refused unless both are whole numbers of one or more, and support
    at least two (one row teaches no pattern)."""
    if support < 2 or confidence < 1:
        raise ValueError("support is two rows or more and confidence is one row or more")
    store.set_preference(SUPPORT_SETTING, str(support))
    store.set_preference(CONFIDENCE_SETTING, str(confidence))


def keep_rule(store: Store, rule: Rule) -> None:
    """Tick a rule: it is applied whatever the confidence setting becomes."""
    store.set_preference(KEPT_PREFIX + rule.key, "1")
