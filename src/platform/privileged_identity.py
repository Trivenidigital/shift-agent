"""Cross-store privileged-identity integrity.

A roster row must not stitch together identifiers belonging to DIFFERENT
principals and thereby acquire owner capability through identifier widening.

Why this cannot live in the ``Roster`` schema
---------------------------------------------
It is not decidable from the roster alone. Owner authority lives in
``config.owner``, so the check needs BOTH stores. ``Roster`` validation runs
without any config in scope, which is exactly why the invariant is a separate
cross-store function rather than a model validator.

The mechanism being guarded
---------------------------
``identify-sender._resolve_principal`` matches an employee row FIRST, then
fills ``eff_phone``/``eff_lid`` FROM THAT ROW, and only then calls
``_match_owner_identity`` against config. So a roster row can supply the
identifier that reaches the config owner anchor. Measured against the real
resolver with schema-valid fixtures (2026-08-31):

    row{phone: attacker,   lid: owner.lid}       queried by phone -> owner
    row{phone: owner.phone, lid: attacker}       queried by LID   -> owner
    row{phone: attacker,   lid: authorized.lid}  queried by phone -> owner
    row{phone: AUTHORIZED, lid: stranger}        queried by LID   -> owner

The last shape is reachable WITHOUT any operator action:
``shift-agent-lid-learn`` matches ``phone_history`` entries with no
effective-window check (unlike ``Roster.find_by_phone``), so a stranger who
now holds a number formerly held by an owner-authorized employee gets their
LID written onto that row.

What stays legal
----------------
Overlap itself is intentional configuration, not a defect. The reference
customer's ``e008`` holds the phone listed in
``config.owner.authorized_identities`` and its own real LID: ONE human with
two capabilities. Banning owner-as-employee would break a working deployment.

The rule is about PRINCIPALS, not about overlap:

    A row may carry owner-side identifiers only when EVERY identifier on that
    row belongs to the SAME owner-side principal.

``e008`` passes because its phone and its LID both belong to the one
authorized identity. A stitched row fails because its two identifiers resolve
to two different principals.

Known limitation -- report, do not paper over
---------------------------------------------
When an authorized identity records a phone and NO ``lid`` (which is exactly
the deployed configuration today), stored state CANNOT distinguish:

    e008 { phone: <authorized>, lid: <e008's own LID> }        legitimate
    e008 { phone: <authorized>, lid: <a stranger's LID> }      the N5 stitch

Both are "a row holding the authorized phone plus some LID". Config asserts
only that the phone is authorized; nothing in either store binds a LID to that
principal, so there is no evidence to compare against. This function therefore
does NOT flag that shape -- flagging it would reject the working deployment,
and inventing a heuristic to guess would be worse than the gap.

What DOES close it is data, not logic: record ``lid`` on the authorized
identity. Then the principal has both identifiers on file, the mismatch is
decidable, and the ``unrelated_identifier_on_privileged_row`` branch below
catches the stitch. That is an operator configuration decision, not something
this module may do on its own.

Everything else is detectable: any row touching TWO owner-side principals, and
any row whose identifiers contradict a principal that has both on file.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path as _Path
from datetime import datetime, timezone
from typing import Any, Optional

__all__ = [
    "Violation",
    "check_privileged_identity_integrity",
    "canonical_phone",
]


def canonical_phone(value: Any) -> Optional[str]:
    """Canonicalise via the deployed ``E164Phone``, never a local reimplementation.

    Returns None when the value cannot be canonicalised. The resolver compares
    canonical phones, so comparing raw strings here would let a differently
    formatted duplicate slip past a check the resolver would still match.
    """
    if value is None:
        return None
    E164Phone = _e164()
    try:
        return str(E164Phone.from_any(str(value)))
    except Exception:
        return None


def _e164():
    """Locate the deployed ``E164Phone``.

    Deliberately raises when it cannot be found, rather than degrading to raw
    string comparison. The resolver compares CANONICAL phones, so a silent
    fallback would let `+91-85220-41562` miss a principal holding
    `+918522041562` -- a check that quietly stops canonicalising is worse than
    one that fails loudly, because the caller would bank a clean result.

    Two layouts are supported: the repo (`src/platform` on the path) and the
    deployed box, where the modules sit directly under `/opt/shift-agent`.
    """
    try:
        from schemas import E164Phone
        return E164Phone
    except ImportError:
        pass
    import sys
    for candidate in ("/opt/shift-agent", str(_Path(__file__).resolve().parent)):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
    from schemas import E164Phone  # raises if genuinely unavailable
    return E164Phone


def _norm_lid(value: Any) -> Optional[str]:
    """LIDs are compared verbatim by the resolver -- mirror that exactly."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _is_effective(entry: dict, now: datetime) -> bool:
    """Mirror ``Roster.find_by_phone``'s window test, not lid-learn's.

    ``find_by_phone`` (schemas.py) skips an assignment whose window has closed;
    ``shift-agent-lid-learn`` does NOT, and that difference is the N5 defect.
    The invariant follows the RESOLVER, because the resolver decides who a
    message is attributed to -- an expired reassignment is legitimate and must
    not be flagged.
    """
    start = _as_dt(entry.get("effective_from"))
    end = _as_dt(entry.get("effective_to"))
    if start is not None and start > now:
        return False
    if end is not None and end < now:
        return False
    return True


@dataclass(frozen=True)
class Principal:
    """One owner-side identity: the primary owner, or one authorized alias."""

    label: str
    phone: Optional[str]
    lid: Optional[str]


@dataclass
class Violation:
    """One roster row that stitches identifiers across principals."""

    employee_id: str
    reason: str
    detail: str
    conflicting: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "employee_id": self.employee_id,
            "reason": self.reason,
            "detail": self.detail,
            "conflicting": self.conflicting,
        }


def _owner_principals(owner: Any) -> list[Principal]:
    """Every principal that ``_match_owner_identity`` would match against.

    Includes the primary owner AND every authorized identity -- the alias case
    is not optional: a stitch against an authorized identity reaches owner just
    as the primary does, and an invariant written against `owner.phone` /
    `owner.lid` alone would miss it entirely.
    """
    def _get(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    out = [
        Principal(
            "owner",
            canonical_phone(_get(owner, "phone")),
            _norm_lid(_get(owner, "lid")),
        )
    ]
    aliases = _get(owner, "authorized_identities") or []
    for i, alias in enumerate(aliases):
        out.append(
            Principal(
                f"authorized_identities[{i}]",
                canonical_phone(_get(alias, "phone")),
                _norm_lid(_get(alias, "lid")),
            )
        )
    return out


def _matching(principals: list[Principal], *, phone=None, lid=None) -> list[Principal]:
    hits = []
    for p in principals:
        if phone is not None and p.phone is not None and p.phone == phone:
            hits.append(p)
            continue
        if lid is not None and p.lid is not None and p.lid == lid:
            hits.append(p)
    return hits


def check_privileged_identity_integrity(
    roster_doc: Any,
    owner_config: Any,
    *,
    now: Optional[datetime] = None,
) -> list[Violation]:
    """Return every roster row that stitches identifiers across principals.

    Pure: no I/O, no mutation. Never raises on ordinary bad input -- malformed
    rows simply cannot be evaluated and are skipped, because this function's
    job is the privilege invariant, and schema validity is ``Roster``'s job.
    A row this function cannot parse has already failed validation upstream.

    ``roster_doc`` and ``owner_config`` accept dicts or pydantic models.
    """
    now = now or datetime.now(timezone.utc)
    principals = _owner_principals(owner_config)
    if not principals:
        return []

    employees = (
        roster_doc.get("employees")
        if isinstance(roster_doc, dict)
        else getattr(roster_doc, "employees", None)
    ) or []

    violations: list[Violation] = []
    for emp in employees:
        get = (lambda k: emp.get(k)) if isinstance(emp, dict) else (lambda k: getattr(emp, k, None))
        eid = str(get("id") or "<unknown>")
        phone = canonical_phone(get("phone"))
        lid = _norm_lid(get("lid"))

        # Effective history entries reach the resolver exactly as a current
        # phone does; expired ones do not, and flagging them would break
        # legitimate number reassignment.
        history = []
        for entry in (get("phone_history") or []):
            e = entry if isinstance(entry, dict) else {
                "phone": getattr(entry, "phone", None),
                "effective_from": getattr(entry, "effective_from", None),
                "effective_to": getattr(entry, "effective_to", None),
            }
            if _is_effective(e, now):
                canon = canonical_phone(e.get("phone"))
                if canon is not None:
                    history.append(canon)

        phone_hits = _matching(principals, phone=phone) if phone else []
        lid_hits = _matching(principals, lid=lid) if lid else []
        hist_hits: list[tuple[str, Principal]] = []
        for h in history:
            for p in _matching(principals, phone=h):
                hist_hits.append((h, p))

        owner_side = phone_hits or lid_hits or hist_hits
        if not owner_side:
            continue  # ordinary employee -- nothing privileged on this row

        # Which principals does this row touch, and via which identifier?
        touched: dict[str, list[dict]] = {}
        for p in phone_hits:
            touched.setdefault(p.label, []).append({"identifier": "phone", "value": phone})
        for p in lid_hits:
            touched.setdefault(p.label, []).append({"identifier": "lid", "value": lid})
        for h, p in hist_hits:
            touched.setdefault(p.label, []).append(
                {"identifier": "phone_history", "value": h}
            )

        if len(touched) > 1:
            violations.append(
                Violation(
                    employee_id=eid,
                    reason="identifiers_span_multiple_owner_principals",
                    detail=(
                        f"row {eid} carries identifiers belonging to "
                        f"{len(touched)} different owner-side principals: "
                        + ", ".join(sorted(touched))
                    ),
                    conflicting=[
                        {"principal": k, "via": v} for k, v in sorted(touched.items())
                    ],
                )
            )
            continue

        principal_label = next(iter(touched))
        principal = next(p for p in principals if p.label == principal_label)

        # The row is owner-side. Every OTHER identifier it carries must belong
        # to that same principal, or the row stitches an unrelated identifier
        # onto privileged authority. This is the N5 shape.
        unrelated: list[dict] = []
        # An ACTIVE history entry is equivalent to a current phone: both resolve
        # to this row. So a row whose current phone differs from the principal's
        # is carrying an unrelated identifier even when the principal's phone
        # sits in its open history -- that row claims TWO live numbers, one of
        # them the principal's. `patch_employee` closes the old window on a
        # phone change, so this shape is anomalous by construction. Expired
        # entries never reach here; `_is_effective` filtered them out, which is
        # what keeps legitimate number reassignment legal.
        if phone is not None and principal.phone is not None and phone != principal.phone:
            unrelated.append({"identifier": "phone", "value": phone})
        if lid is not None and principal.lid is not None and lid != principal.lid:
            unrelated.append({"identifier": "lid", "value": lid})
        # DELIBERATELY NOT FLAGGED: a row whose principal has no `lid` recorded
        # in config. See "Known limitation" in the module docstring -- stored
        # state cannot distinguish e008's own LID from a stranger's LID written
        # onto e008's row, and flagging it would reject the legitimate
        # deployed configuration.

        if unrelated:
            violations.append(
                Violation(
                    employee_id=eid,
                    reason="unrelated_identifier_on_privileged_row",
                    detail=(
                        f"row {eid} holds owner-side principal {principal_label} "
                        f"but also carries {len(unrelated)} identifier(s) not "
                        f"bound to that principal"
                    ),
                    conflicting=[{"principal": principal_label, "unrelated": unrelated}],
                )
            )

    return violations
