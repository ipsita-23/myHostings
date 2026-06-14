import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.models import SplitType, User


def _round2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_splits(
    total_amount_inr: Decimal,
    split_type: SplitType,
    split_details: dict[str, Any],
    active_members: list[User],
) -> dict[uuid.UUID, Decimal]:
    """
    Returns {user_id: share_amount_inr} for every active member.

    - equal: total / len(active_members). Remainder (from rounding) goes to first member.
    - exact: split_details maps str(user_id) → exact INR amount. Must sum to total ±1.
    - percentage: split_details maps str(user_id) → percentage float/Decimal.
                  Must sum to 100 ±0.01. Largest share absorbs rounding remainder.
    - shares: split_details maps str(user_id) → share count (int or float).
              Compute ratio, round each, largest absorbs remainder.
    """
    if not active_members:
        raise ValueError("No active members to split expense among.")

    if split_type == SplitType.EQUAL:
        return _split_equal(total_amount_inr, active_members)
    elif split_type == SplitType.EXACT:
        return _split_exact(total_amount_inr, split_details, active_members)
    elif split_type == SplitType.PERCENTAGE:
        return _split_percentage(total_amount_inr, split_details, active_members)
    elif split_type == SplitType.SHARES:
        return _split_shares(total_amount_inr, split_details, active_members)
    else:
        raise ValueError(f"Unknown split type: {split_type}")


def _split_equal(
    total: Decimal, members: list[User]
) -> dict[uuid.UUID, Decimal]:
    n = len(members)
    base_share = _round2(total / Decimal(n))
    result: dict[uuid.UUID, Decimal] = {}
    for member in members:
        result[member.id] = base_share

    # Adjust first member to absorb rounding remainder
    distributed = base_share * n
    remainder = total - distributed
    first_id = members[0].id
    result[first_id] = _round2(result[first_id] + remainder)
    return result


def _split_exact(
    total: Decimal,
    split_details: dict[str, Any],
    members: list[User],
) -> dict[uuid.UUID, Decimal]:
    result: dict[uuid.UUID, Decimal] = {}
    member_ids = {str(m.id) for m in members}

    total_assigned = Decimal("0")
    for member in members:
        key = str(member.id)
        raw = split_details.get(key, Decimal("0"))
        amount = _round2(Decimal(str(raw)))
        result[member.id] = amount
        total_assigned += amount

    diff = abs(total - total_assigned)
    if diff > Decimal("1"):
        raise ValueError(
            f"Exact split amounts sum to {total_assigned}, expected {total} (diff={diff})"
        )
    return result


def _split_percentage(
    total: Decimal,
    split_details: dict[str, Any],
    members: list[User],
) -> dict[uuid.UUID, Decimal]:
    percentages: dict[uuid.UUID, Decimal] = {}
    for member in members:
        key = str(member.id)
        pct = Decimal(str(split_details.get(key, "0")))
        percentages[member.id] = pct

    total_pct = sum(percentages.values())
    if abs(total_pct - Decimal("100")) > Decimal("0.01"):
        raise ValueError(
            f"Percentages sum to {total_pct}, must be 100 (±0.01)"
        )

    result: dict[uuid.UUID, Decimal] = {}
    for member in members:
        share = _round2(total * percentages[member.id] / Decimal("100"))
        result[member.id] = share

    # Largest share absorbs remainder
    distributed = sum(result.values())
    remainder = total - distributed
    if remainder != 0:
        largest_id = max(result, key=lambda uid: result[uid])
        result[largest_id] = _round2(result[largest_id] + remainder)

    return result


def _split_shares(
    total: Decimal,
    split_details: dict[str, Any],
    members: list[User],
) -> dict[uuid.UUID, Decimal]:
    shares: dict[uuid.UUID, Decimal] = {}
    for member in members:
        key = str(member.id)
        s = Decimal(str(split_details.get(key, "1")))
        shares[member.id] = s

    total_shares = sum(shares.values())
    if total_shares == 0:
        raise ValueError("Total shares is zero — cannot split.")

    result: dict[uuid.UUID, Decimal] = {}
    for member in members:
        ratio = shares[member.id] / total_shares
        share = _round2(total * ratio)
        result[member.id] = share

    # Largest share absorbs remainder
    distributed = sum(result.values())
    remainder = total - distributed
    if remainder != 0:
        largest_id = max(result, key=lambda uid: result[uid])
        result[largest_id] = _round2(result[largest_id] + remainder)

    return result
