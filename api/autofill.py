"""Decide what goes in each field of an application form.

The extension enumerates the fields it can see and sends them here; this
module answers with a value per field. Keeping the judgement in Python rather
than in the content script means every rule is testable without a browser, and
a new ATS needs new *selectors*, not new logic.

Three rules run through everything:

* Free text about motivation is never generated here. "Why this company"
  is returned as a skip, because a generated answer to it is visibly generated.
* Voluntary self-identification is filled only when `self_id.mode` is
  `disclose`, and only by matching against the options the page actually
  offers — Canadian employment-equity wording and US EEO-1 wording are not
  interchangeable, so the page decides the words, not us.
* Anything unmatched is reported as unmatched. A wrong guess costs more than
  a blank field, because it teaches you to stop trusting the fill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from resume.schema import Disclosure, Profile

YES = ("yes", "y", "true")
NO = ("no", "n", "false")


@dataclass(frozen=True)
class FieldSpec:
    """One fillable control, as the content script sees it."""

    id: str
    label: str = ""
    type: str = "text"  # text, email, tel, textarea, select, radio, checkbox, file, password
    options: tuple[str, ...] = ()
    required: bool = False
    automation_id: str = ""

    @property
    def haystack(self) -> str:
        """Label and automation id, flattened into plain words.

        Workday often puts the human label out of reach and carries the meaning
        in `data-automation-id` instead, as `legalNameSection_firstName`. Word
        patterns cannot see inside camelCase or underscores, so both are split
        apart before matching.
        """
        raw = f"{self.label} {self.automation_id}"
        spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", raw)
        spaced = re.sub(r"[_\-.]+", " ", spaced)
        # Both forms, because splitting helps `firstName` and hurts `LinkedIn`.
        return f"{raw} {spaced}".casefold()

    @property
    def is_choice(self) -> bool:
        return self.type in {"select", "radio", "checkbox"}


@dataclass
class Fill:
    field_id: str
    value: str | None = None
    action: str = "fill"  # fill | select | skip | unmatched
    source: str = ""
    confidence: str = "high"  # high | medium | low
    reason: str = ""


def choose_option(options: tuple[str, ...], *candidates: str) -> str | None:
    """Pick the page's own wording for an intended answer.

    Forms phrase the same answer as "Yes", "Yes, I am legally authorized" or
    "I am legally entitled to work in Canada", so the option list decides the
    string and we only decide the meaning.
    """
    if not options:
        return None
    folded = [(o, o.casefold().strip()) for o in options]
    for candidate in candidates:
        want = candidate.casefold().strip()
        if not want:
            continue
        for original, low in folded:
            if low == want:
                return original
        # Whole words only, in both directions. Bare substring matching is a
        # trap here: "no" sits inside "visible minority", so a loose check
        # answers "No" to "are you a member of a visible minority?". A wrong
        # answer to an equity question is far worse than a blank one.
        for original, low in folded:
            if re.search(rf"\b{re.escape(want)}\b", low) or re.search(
                rf"\b{re.escape(low)}\b", want
            ):
                return original
    return None


def _yes(spec: FieldSpec) -> str:
    return choose_option(spec.options, *YES) or "Yes" if spec.is_choice else "Yes"


def _no(spec: FieldSpec) -> str:
    return choose_option(spec.options, *NO) or "No" if spec.is_choice else "No"


Resolver = Callable[[Profile, FieldSpec], Fill | None]


@dataclass
class Rule:
    pattern: re.Pattern
    resolve: Resolver
    source: str
    confidence: str = "high"


def _rule(pattern: str, source: str, confidence: str = "high"):
    def wrap(fn: Resolver) -> Rule:
        return Rule(re.compile(pattern, re.I), fn, source, confidence)

    return wrap


def _text(value: str | None, spec: FieldSpec) -> Fill | None:
    if not value:
        return None
    if spec.is_choice:
        chosen = choose_option(spec.options, value)
        return Fill(spec.id, chosen, "select") if chosen else None
    return Fill(spec.id, value)


# Order matters: the first rule whose pattern hits the label wins, so narrow
# patterns come before broad ones ("first name" before a bare "name").
RULES: list[Rule] = [
    # --- never generated, never guessed ---------------------------------
    _rule(r"cover\s*letter|why (do|are|would) you|what (interests|excites)|tell us about|"
          r"describe (your|a time)|in your own words",
          "human")(
        lambda p, s: Fill(s.id, None, "skip", "human", "low",
                          "motivation text — write this one yourself")
    ),
    _rule(r"salary\s*history|current\s*(salary|compensation|ctc)", "policy")(
        lambda p, s: Fill(s.id, None, "skip", "policy", "low",
                          "salary history — decline; several provinces bar the question")
    ),

    # --- identity --------------------------------------------------------
    _rule(r"\b(first|given|fore)\s*name\b|\bfname\b", "identity")(
        lambda p, s: _text(p.identity.first_name, s)
    ),
    _rule(r"\b(last|family|sur)\s*name\b|\blname\b", "identity")(
        lambda p, s: _text(p.identity.last_name, s)
    ),
    _rule(r"\bpreferred\s*name\b", "identity", "medium")(
        lambda p, s: _text(p.identity.first_name, s)
    ),
    _rule(r"\b(full|legal|your)?\s*name\b", "identity", "medium")(
        lambda p, s: _text(p.identity.name, s)
    ),
    _rule(r"e-?mail", "identity")(lambda p, s: _text(p.identity.email, s)),
    _rule(r"\b(phone|mobile|telephone|cell)\b", "identity")(
        lambda p, s: _text(p.identity.phone, s)
    ),
    _rule(r"address\s*line\s*1|\bstreet\b|\baddress\b", "identity", "medium")(
        lambda p, s: _text(p.identity.street, s)
    ),
    _rule(r"\b(city|town|municipal)\b", "identity")(lambda p, s: _text(p.identity.city, s)),
    _rule(r"\b(province|state|region)\b", "identity")(
        lambda p, s: _text("Ontario" if p.identity.province == "ON" else p.identity.province, s)
        or _text(p.identity.province, s)
    ),
    _rule(r"\b(postal|zip)\b", "identity")(lambda p, s: _text(p.identity.postal_code, s)),
    _rule(r"\bcountry\b", "identity")(lambda p, s: _text(p.identity.country, s)),
    _rule(r"linkedin", "identity")(lambda p, s: _text(p.identity.link("linkedin"), s)),
    _rule(r"github", "identity")(lambda p, s: _text(p.identity.link("github"), s)),
    _rule(r"website|portfolio|personal\s*site|blog", "identity", "medium")(
        lambda p, s: _text(
            next(
                (u for u in p.identity.links
                 if "linkedin" not in u.casefold() and "github" not in u.casefold()),
                None,
            ),
            s,
        )
    ),

    # --- work authorisation ----------------------------------------------
    _rule(r"(require|need|request).{0,25}sponsor|sponsor.{0,25}(require|need|now|future)",
          "constraints")(
        lambda p, s: Fill(s.id, _no(s) if not p.constraints.requires_sponsorship else _yes(s),
                          "select" if s.is_choice else "fill", "constraints")
    ),
    _rule(r"legally (entitled|authoriz|permitted)|(authoriz|entitled|eligible).{0,20}to work",
          "constraints")(
        lambda p, s: Fill(s.id, _yes(s), "select" if s.is_choice else "fill", "constraints")
    ),
    _rule(r"work (authoriz|permit|status|eligibility)|immigration status", "constraints",
          "medium")(
        lambda p, s: _text(
            f"{(p.constraints.permit_subtype or 'open work permit').upper()} "
            f"({'no sponsorship required' if not p.constraints.requires_sponsorship else 'sponsorship required'})",
            s,
        )
    ),
    _rule(r"citizenship|citizen of|nationality", "constraints", "medium")(
        lambda p, s: _text(p.constraints.citizenship, s)
    ),

    # --- logistics --------------------------------------------------------
    _rule(r"notice period|when can you (start|begin)|availability|start date|"
          r"earliest.{0,15}start", "constraints")(
        lambda p, s: _text(
            "Immediately" if not p.constraints.notice_period_days
            else f"{p.constraints.notice_period_days} days",
            s,
        )
    ),
    _rule(r"(expected|desired|target).{0,20}(salary|compensation|pay|rate)|"
          r"(salary|compensation).{0,20}(expect|requirement|range)", "constraints")(
        lambda p, s: _text(p.constraints.expected_base_cad, s)
    ),
    _rule(r"relocat", "constraints")(
        lambda p, s: Fill(
            s.id,
            _yes(s) if p.constraints.relocate_within_canada else _no(s),
            "select" if s.is_choice else "fill",
            "constraints",
        )
    ),
    _rule(r"french|bilingual|language proficiency|langue", "constraints", "medium")(
        lambda p, s: _text(p.constraints.french_level.title(), s)
    ),

    # --- voluntary self-identification -----------------------------------
    _rule(r"\bgender\b|\bsex\b|gender identity", "self_id", "medium")(
        lambda p, s: _self_id(p, s, p.self_id.gender, "man", "male")
    ),
    _rule(r"indigenous|aboriginal|first nations|m[eé]tis|inuit", "self_id", "medium")(
        lambda p, s: _self_id_yesno(p, s, p.self_id.indigenous)
    ),
    _rule(r"visible minority|racializ|\brace\b|ethnic", "self_id", "medium")(
        lambda p, s: _racialized(p, s)
    ),
    _rule(r"disab", "self_id", "medium")(
        lambda p, s: _self_id_yesno(p, s, p.self_id.disability,
                                    no_extra=("i do not have", "no, i do not"))
    ),
    _rule(r"veteran|military service|armed forces", "self_id", "medium")(
        lambda p, s: _self_id_yesno(p, s, p.self_id.veteran,
                                    no_extra=("i am not", "not a protected veteran"))
    ),
]


def _disclosing(profile: Profile) -> bool:
    return profile.self_id.mode == "disclose"


def _self_id(profile: Profile, spec: FieldSpec, value: str, *aliases: str) -> Fill | None:
    if not _disclosing(profile) or value == "prefer_not_to_say":
        return Fill(spec.id, None, "skip", "self_id", "low", "voluntary — left for you")
    chosen = choose_option(spec.options, value, *aliases) if spec.is_choice else value
    if chosen is None:
        return Fill(spec.id, None, "skip", "self_id", "low",
                    "no option matches this profile's wording")
    return Fill(spec.id, chosen, "select" if spec.is_choice else "fill", "self_id", "medium")


def _self_id_yesno(profile: Profile, spec: FieldSpec, value: Disclosure,
                   *, no_extra: tuple[str, ...] = ()) -> Fill | None:
    if not _disclosing(profile) or value is Disclosure.prefer_not_to_say:
        return Fill(spec.id, None, "skip", "self_id", "low", "voluntary — left for you")
    wanted = YES if value is Disclosure.yes else NO + no_extra
    chosen = choose_option(spec.options, *wanted) if spec.is_choice else value.value.title()
    if chosen is None:
        return Fill(spec.id, None, "skip", "self_id", "low", "no matching option")
    return Fill(spec.id, chosen, "select" if spec.is_choice else "fill", "self_id", "medium")


def _racialized(profile: Profile, spec: FieldSpec) -> Fill | None:
    """Canadian forms list designated groups; US forms ship EEO-1 wording."""
    if not _disclosing(profile) or profile.self_id.racialized is Disclosure.prefer_not_to_say:
        return Fill(spec.id, None, "skip", "self_id", "low", "voluntary — left for you")
    group = (profile.self_id.racialized_group or "").replace("_", " ")
    chosen = choose_option(
        spec.options, group, "asian", "visible minority", "racialized", *YES
    ) if spec.is_choice else group.title()
    if chosen is None:
        return Fill(spec.id, None, "skip", "self_id", "low", "no matching option")
    return Fill(spec.id, chosen, "select" if spec.is_choice else "fill", "self_id", "medium")


def _from_answer_bank(profile: Profile, spec: FieldSpec) -> Fill | None:
    label = spec.haystack
    for question, answer in profile.answers.items():
        if question.casefold() in label:
            if not answer:
                return Fill(spec.id, None, "skip", "answers", "low", "blank on purpose")
            return _text(answer, spec) or Fill(spec.id, answer, "fill", "answers")
    return None


def resolve_field(profile: Profile, spec: FieldSpec) -> Fill:
    if spec.type == "password":
        return Fill(spec.id, None, "skip", "policy", "low", "credentials are never autofilled")
    if spec.type == "file":
        return Fill(spec.id, None, "skip", "resume", "low", "attach the tailored resume here")

    from_bank = _from_answer_bank(profile, spec)
    if from_bank is not None:
        return from_bank

    for rule in RULES:
        if not rule.pattern.search(spec.haystack):
            continue
        fill = rule.resolve(profile, spec)
        if fill is None:
            continue
        if not fill.source:
            fill.source = rule.source
        if fill.action == "fill" and fill.confidence == "high":
            fill.confidence = rule.confidence
        return fill

    return Fill(spec.id, None, "unmatched", "", "low", "no rule matched this label")


def resolve(profile: Profile, fields: list[FieldSpec]) -> list[Fill]:
    return [resolve_field(profile, spec) for spec in fields]
