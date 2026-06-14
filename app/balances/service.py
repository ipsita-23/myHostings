import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import Expense, ExpenseSplit, Payment, User, GroupMember


async def compute_raw_balances(
    db: AsyncSession, group_id: uuid.UUID
) -> dict[uuid.UUID, Decimal]:
    """
    For each user in the group:
      balance =
        + Σ expense_splits.share_amount_inr WHERE expense.paid_by == user   (owed TO them)
        - Σ expense_splits.share_amount_inr WHERE split.user == user        (they OWE)
        + Σ payments.amount_inr WHERE payment.to_user == user               (received)
        - Σ payments.amount_inr WHERE payment.from_user == user             (paid out)

    Only non-deleted expenses. All amounts in INR.
    Positive = net owed to this user. Negative = this user owes money.
    """
    # Get all group members
    members_result = await db.execute(
        select(GroupMember).where(GroupMember.group_id == group_id)
    )
    memberships = list(members_result.scalars().all())
    all_user_ids = list({m.user_id for m in memberships})

    balances: dict[uuid.UUID, Decimal] = {uid: Decimal("0") for uid in all_user_ids}

    # Load all non-deleted expenses for this group with their splits
    expenses_result = await db.execute(
        select(Expense)
        .where(Expense.group_id == group_id)
        .where(Expense.is_deleted == False)
    )
    expenses = list(expenses_result.scalars().all())
    expense_ids = [e.id for e in expenses]
    expense_payer_map = {e.id: e.paid_by_user_id for e in expenses}

    if expense_ids:
        splits_result = await db.execute(
            select(ExpenseSplit).where(ExpenseSplit.expense_id.in_(expense_ids))
        )
        splits = list(splits_result.scalars().all())

        for split in splits:
            payer_id = expense_payer_map.get(split.expense_id)
            split_user_id = split.user_id
            amount = split.share_amount_inr

            # Payer is owed this split's amount (they fronted the money)
            if payer_id and payer_id in balances:
                balances[payer_id] += amount

            # The split member owes their share
            if split_user_id in balances:
                balances[split_user_id] -= amount

    # Load payments for this group
    payments_result = await db.execute(
        select(Payment).where(Payment.group_id == group_id)
    )
    payments = list(payments_result.scalars().all())

    for payment in payments:
        amount = payment.amount_inr
        if payment.to_user_id in balances:
            balances[payment.to_user_id] += amount
        if payment.from_user_id in balances:
            balances[payment.from_user_id] -= amount

    return balances


def minimize_transactions(
    balances: dict[uuid.UUID, Decimal]
) -> list[dict[str, Any]]:
    """
    Greedy debt simplification algorithm.
    Returns list of {"from": user_id, "to": user_id, "amount": Decimal}.
    Repeatedly matches the largest debtor with the largest creditor.
    """
    # Filter out zero balances
    creditors = sorted(
        [(uid, amt) for uid, amt in balances.items() if amt > 0],
        key=lambda x: x[1],
        reverse=True,
    )
    debtors = sorted(
        [(uid, -amt) for uid, amt in balances.items() if amt < 0],
        key=lambda x: x[1],
        reverse=True,
    )

    transactions = []
    creditors = list(creditors)
    debtors = list(debtors)

    ci, di = 0, 0
    while ci < len(creditors) and di < len(debtors):
        cred_id, cred_amt = creditors[ci]
        debt_id, debt_amt = debtors[di]

        transfer = min(cred_amt, debt_amt)
        if transfer > Decimal("0.01"):
            transactions.append(
                {
                    "from": debt_id,
                    "to": cred_id,
                    "amount": transfer.quantize(Decimal("0.01")),
                }
            )

        cred_amt -= transfer
        debt_amt -= transfer

        if cred_amt < Decimal("0.01"):
            ci += 1
        else:
            creditors[ci] = (cred_id, cred_amt)

        if debt_amt < Decimal("0.01"):
            di += 1
        else:
            debtors[di] = (debt_id, debt_amt)

    return transactions


async def get_member_expense_breakdown(
    db: AsyncSession, group_id: uuid.UUID, user_id: uuid.UUID
) -> list[dict[str, Any]]:
    """
    Returns every expense (non-deleted) that affects this user's balance.
    Each entry includes: expense description, date, share_amount_inr,
    whether they paid, and net effect on their balance.
    """
    expenses_result = await db.execute(
        select(Expense)
        .where(Expense.group_id == group_id)
        .where(Expense.is_deleted == False)
        .order_by(Expense.date.desc())
    )
    expenses = list(expenses_result.scalars().all())
    expense_ids = [e.id for e in expenses]
    expenses_map = {e.id: e for e in expenses}

    if not expense_ids:
        return []

    splits_result = await db.execute(
        select(ExpenseSplit)
        .where(ExpenseSplit.expense_id.in_(expense_ids))
        .where(ExpenseSplit.user_id == user_id)
    )
    user_splits = {s.expense_id: s for s in splits_result.scalars().all()}

    breakdown = []
    running_total = Decimal("0")

    for expense in expenses:
        split = user_splits.get(expense.id)
        if split is None:
            continue

        is_payer = expense.paid_by_user_id == user_id
        share_amt = split.share_amount_inr

        if is_payer:
            # They paid: they are owed the full split amount back
            net_effect = share_amt  # they paid this share, so net = +share (owed back)
            # Actually: they paid total, owed (total - their_share) from others
            # But in our formula: payer gets +share per split, loses -their_own_share
            # Net for payer = +share_amount_inr (since they also owe their own share)
            # So net_effect shown here: the full split amount credited to them
            net_effect_for_display = share_amt  # credited (owed to them)
        else:
            net_effect_for_display = -share_amt  # they owe this

        running_total += net_effect_for_display

        breakdown.append(
            {
                "expense": expense,
                "share_amount_inr": share_amt,
                "is_payer": is_payer,
                "net_effect": net_effect_for_display,
                "running_total": running_total,
            }
        )

    return breakdown
