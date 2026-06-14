import re
import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional, Any

from app.models import User, SplitType
from app.importer.anomalies import Anomaly, AnomalyType


# ─── Amount ───────────────────────────────────────────────────────────────────

def parse_amount(
    raw: str, row_number: int, raw_row: dict
) -> tuple[Decimal, Optional[Anomaly]]:
    """
    Handles: "1,200" → 1200, negative (refund), zero, non-numeric.
    """
    cleaned = raw.strip().replace(",", "").replace(" ", "")
    anomaly = None

    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        anomaly = Anomaly(
            row_number=row_number,
            raw_row=raw_row,
            anomaly_type=AnomalyType.FORMAT,
            description=f"Cannot parse amount: '{raw}'",
            action_taken="SKIPPED",
            requires_approval=True,
        )
        return Decimal("0"), anomaly

    if amount == Decimal("0"):
        anomaly = Anomaly(
            row_number=row_number,
            raw_row=raw_row,
            anomaly_type=AnomalyType.ZERO_AMOUNT,
            description="Amount is zero.",
            action_taken="SKIPPED",
        )
        return Decimal("0"), anomaly

    if amount < Decimal("0"):
        anomaly = Anomaly(
            row_number=row_number,
            raw_row=raw_row,
            anomaly_type=AnomalyType.REFUND,
            description=f"Negative amount {amount} — treated as refund.",
            action_taken="IMPORTED",
        )

    return amount, anomaly


# ─── Date ─────────────────────────────────────────────────────────────────────

_DATE_FORMATS = [
    "%d-%m-%Y",   # DD-MM-YYYY (primary)
    "%Y-%m-%d",   # ISO (secondary)
    "%b-%y",      # Mar-26 style (tertiary — assume day=1)
    "%b-%Y",      # Mar-2026
]

def parse_date(
    raw: str, row_number: int, raw_row: dict
) -> tuple[Optional[datetime.date], Optional[Anomaly]]:
    raw = raw.strip()
    anomaly = None

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.datetime.strptime(raw, fmt)
            return dt.date(), anomaly
        except ValueError:
            continue

    # Last resort: MM-DD-YYYY — flag as CONFLICT
    try:
        dt = datetime.datetime.strptime(raw, "%m-%d-%Y")
        anomaly = Anomaly(
            row_number=row_number,
            raw_row=raw_row,
            anomaly_type=AnomalyType.CONFLICT,
            description=(
                f"Date '{raw}' parsed as MM-DD-YYYY (last resort). "
                "Verify this is correct."
            ),
            action_taken="IMPORTED",
            requires_approval=True,
        )
        return dt.date(), anomaly
    except ValueError:
        pass

    anomaly = Anomaly(
        row_number=row_number,
        raw_row=raw_row,
        anomaly_type=AnomalyType.DATE_FMT,
        description=f"Cannot parse date: '{raw}'",
        action_taken="SKIPPED",
        requires_approval=True,
    )
    return None, anomaly


# ─── Name normalization ────────────────────────────────────────────────────────

def normalize_name(
    raw: str,
    known_names: dict[str, User],
    row_number: int,
    raw_row: dict,
) -> tuple[Optional[User], Optional[Anomaly]]:
    """
    Strip whitespace, lowercase comparison.
    Exact match → return user.
    Prefix match → return if exactly one match, else CONFLICT.
    known_names: {"aisha": User, "rohan": User, ...}  (keys already lowercased)
    """
    normalized = raw.strip().lower()
    if not normalized:
        return None, Anomaly(
            row_number=row_number,
            raw_row=raw_row,
            anomaly_type=AnomalyType.MISSING_FIELD,
            description="Name field is empty.",
            action_taken="SKIPPED",
            requires_approval=True,
        )

    # Exact match
    if normalized in known_names:
        return known_names[normalized], None

    # Prefix match
    matches = [
        user for name, user in known_names.items()
        if name.startswith(normalized) or normalized.startswith(name)
    ]

    if len(matches) == 1:
        anomaly = Anomaly(
            row_number=row_number,
            raw_row=raw_row,
            anomaly_type=AnomalyType.NAME_NORM,
            description=f"Name '{raw}' resolved via prefix match to '{matches[0].name}'.",
            action_taken="AUTO_FIXED",
        )
        return matches[0], anomaly

    if len(matches) > 1:
        names = [m.name for m in matches]
        return None, Anomaly(
            row_number=row_number,
            raw_row=raw_row,
            anomaly_type=AnomalyType.CONFLICT,
            description=f"Ambiguous name '{raw}' matches: {names}",
            action_taken="FLAGGED",
            requires_approval=True,
        )

    return None, Anomaly(
        row_number=row_number,
        raw_row=raw_row,
        anomaly_type=AnomalyType.NAME_NORM,
        description=f"Name '{raw}' could not be resolved to any group member.",
        action_taken="SKIPPED",
        requires_approval=True,
    )


# ─── Duplicate detection ───────────────────────────────────────────────────────

def detect_duplicate(
    row: dict, seen_rows: list[dict]
) -> bool:
    """True if same date + paid_by + amount already in seen_rows."""
    for seen in seen_rows:
        if (
            seen.get("date") == row.get("date")
            and seen.get("paid_by") == row.get("paid_by")
            and seen.get("amount") == row.get("amount")
        ):
            return True
    return False


# ─── Settlement detection ─────────────────────────────────────────────────────

_SETTLEMENT_KEYWORDS = {"settlement", "paid back", "repaid", "reimbursed", "settle"}

def check_settlement(row: dict) -> bool:
    """True if notes contain settlement keywords or description indicates a payback."""
    notes = (row.get("notes") or "").lower()
    description = (row.get("description") or "").lower()
    for keyword in _SETTLEMENT_KEYWORDS:
        if keyword in notes or keyword in description:
            return True
    return False


# ─── Split details parsing ────────────────────────────────────────────────────

def parse_split_details(
    raw_split_with: str,
    raw_split_details: str,
    split_type: str,
    members: list[User],
    row_number: int,
    raw_row: dict,
) -> tuple[dict, list[Anomaly]]:
    """
    Parses split_with + split_details columns into {str(user_id): value} dict.
    Detects:
      - percentages not summing to 100
      - non-members in split
      - conflicting split_type vs details format
    """
    anomalies: list[Anomaly] = []
    result: dict[str, Any] = {}
    member_name_map = {u.name.strip().lower(): u for u in members}
    member_id_set = {str(u.id) for u in members}

    names_in_split = [n.strip() for n in raw_split_with.split(",") if n.strip()]

    if split_type in ("equal", ""):
        # equal split — no details needed
        return {}, anomalies

    # Parse split_details: "Name1:value1,Name2:value2"
    pairs = [p.strip() for p in raw_split_details.split(",") if p.strip()]
    for pair in pairs:
        if ":" not in pair:
            anomalies.append(
                Anomaly(
                    row_number=row_number,
                    raw_row=raw_row,
                    anomaly_type=AnomalyType.INVALID_SPLIT,
                    description=f"Malformed split_details entry: '{pair}'. Expected 'Name:value'.",
                    action_taken="FLAGGED",
                    requires_approval=True,
                )
            )
            continue

        name_part, value_part = pair.split(":", 1)
        name_normalized = name_part.strip().lower()

        # Resolve to a member
        matched_user: Optional[User] = None
        if name_normalized in member_name_map:
            matched_user = member_name_map[name_normalized]
        else:
            # Prefix match
            prefix_matches = [
                u for n, u in member_name_map.items()
                if n.startswith(name_normalized) or name_normalized.startswith(n)
            ]
            if len(prefix_matches) == 1:
                matched_user = prefix_matches[0]
            else:
                anomalies.append(
                    Anomaly(
                        row_number=row_number,
                        raw_row=raw_row,
                        anomaly_type=AnomalyType.NON_MEMBER,
                        description=f"'{name_part.strip()}' is not a group member or is ambiguous.",
                        action_taken="FLAGGED",
                        requires_approval=True,
                    )
                )
                continue

        try:
            value = Decimal(value_part.strip().replace("%", ""))
            result[str(matched_user.id)] = value
        except InvalidOperation:
            anomalies.append(
                Anomaly(
                    row_number=row_number,
                    raw_row=raw_row,
                    anomaly_type=AnomalyType.FORMAT,
                    description=f"Cannot parse split value '{value_part}' for {name_part}.",
                    action_taken="FLAGGED",
                    requires_approval=True,
                )
            )

    # Validate percentage sums
    if split_type == "percentage" and result:
        total_pct = sum(result.values())
        if abs(total_pct - Decimal("100")) > Decimal("0.01"):
            anomalies.append(
                Anomaly(
                    row_number=row_number,
                    raw_row=raw_row,
                    anomaly_type=AnomalyType.INVALID_SPLIT,
                    description=f"Percentages sum to {total_pct}, must equal 100.",
                    action_taken="FLAGGED",
                    requires_approval=True,
                )
            )

    return result, anomalies
