import os
import uuid
import logging
import datetime
from decimal import Decimal
from typing import Optional, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models import (
    Expense,
    ExpenseSplit,
    ExchangeRate,
    Currency,
    SplitType,
    User,
)
from app.groups.service import active_members_on
from app.expenses.splits import calculate_splits

logger = logging.getLogger("spreetail.expenses")


async def get_exchange_rate(
    db: AsyncSession,
    currency_code: str,
    rate_date: datetime.date,
) -> Decimal:
    """
    Look up rate for (currency_code, rate_date).
    Falls back to the most recent rate before the date.
    Uses USD_INR_RATE env var as last resort for USD, then logs CURRENCY anomaly.
    Never returns 1:1 silently for non-INR currencies.
    """
    if currency_code == "INR":
        return Decimal("1.0")

    # Exact date match
    result = await db.execute(
        select(ExchangeRate)
        .where(ExchangeRate.currency_code == currency_code)
        .where(ExchangeRate.rate_date == rate_date)
    )
    rate = result.scalar_one_or_none()
    if rate:
        return rate.rate_to_inr

    # Closest previous date
    result = await db.execute(
        select(ExchangeRate)
        .where(ExchangeRate.currency_code == currency_code)
        .where(ExchangeRate.rate_date <= rate_date)
        .order_by(ExchangeRate.rate_date.desc())
        .limit(1)
    )
    rate = result.scalar_one_or_none()
    if rate:
        logger.warning(
            "No exchange rate for %s on %s — using closest: %s (%s)",
            currency_code,
            rate_date,
            rate.rate_to_inr,
            rate.rate_date,
        )
        return rate.rate_to_inr

    # Last resort: env var for USD
    env_rate = os.getenv("USD_INR_RATE")
    if currency_code == "USD" and env_rate:
        fallback_rate = Decimal(env_rate)
        logger.warning(
            "No exchange rate for USD on %s — using USD_INR_RATE env var: %s. "
            "Inserting rate into exchange_rates.",
            rate_date,
            fallback_rate,
        )
        new_rate = ExchangeRate(
            currency_code="USD",
            rate_date=rate_date,
            rate_to_inr=fallback_rate,
        )
        db.add(new_rate)
        await db.flush()
        return fallback_rate

    raise ValueError(
        f"No exchange rate found for {currency_code} on or before {rate_date}. "
        "Add a rate to the exchange_rates table."
    )


async def create_expense(
    db: AsyncSession,
    group_id: uuid.UUID,
    description: str,
    total_amount: Decimal,
    currency_code: str,
    split_type: SplitType,
    split_details: dict[str, Any],
    paid_by_user_id: uuid.UUID,
    date: datetime.date,
    notes: Optional[str] = None,
    source_row: Optional[int] = None,
) -> Expense:
    # Get active members on the expense date
    members = await active_members_on(db, group_id, date)
    if not members:
        raise ValueError(f"No active members in group on {date}.")

    # Get exchange rate and convert to INR
    rate = await get_exchange_rate(db, currency_code, date)
    total_inr = (total_amount * rate).quantize(Decimal("0.01"))

    # Calculate per-member splits
    shares = calculate_splits(
        total_amount_inr=total_inr,
        split_type=split_type,
        split_details=split_details,
        active_members=members,
    )

    # Write the expense
    expense = Expense(
        group_id=group_id,
        description=description,
        total_amount=total_amount,
        currency_code=currency_code,
        split_type=split_type,
        paid_by_user_id=paid_by_user_id,
        date=date,
        notes=notes,
        source_row=source_row,
    )
    db.add(expense)
    await db.flush()

    # Write split rows — raw_share is the input value, share_amount_inr is frozen
    for member in members:
        raw_share_val = split_details.get(str(member.id), Decimal("0"))
        if split_type == SplitType.EQUAL:
            raw_share_val = Decimal("1")  # equal weight
        split = ExpenseSplit(
            expense_id=expense.id,
            user_id=member.id,
            raw_share=Decimal(str(raw_share_val)),
            share_amount_inr=shares[member.id],
        )
        db.add(split)

    await db.commit()
    await db.refresh(expense)
    return expense


async def get_expense(
    db: AsyncSession, expense_id: uuid.UUID
) -> Optional[Expense]:
    result = await db.execute(
        select(Expense).where(Expense.id == expense_id)
    )
    return result.scalar_one_or_none()


async def soft_delete_expense(db: AsyncSession, expense_id: uuid.UUID) -> None:
    expense = await get_expense(db, expense_id)
    if expense:
        expense.is_deleted = True
        await db.commit()


async def list_group_expenses(
    db: AsyncSession, group_id: uuid.UUID
) -> list[Expense]:
    result = await db.execute(
        select(Expense)
        .where(Expense.group_id == group_id)
        .where(Expense.is_deleted == False)
        .order_by(Expense.date.desc())
    )
    return list(result.scalars().all())
