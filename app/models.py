import uuid
import datetime
from decimal import Decimal
from typing import Optional, List
import enum

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class SplitType(str, enum.Enum):
    EQUAL = "equal"
    EXACT = "exact"
    PERCENTAGE = "percentage"
    SHARES = "shares"


class Currency(Base):
    __tablename__ = "currencies"

    code: Mapped[str] = mapped_column(sa.String(10), primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(100), nullable=False)

    exchange_rates: Mapped[List["ExchangeRate"]] = relationship(
        "ExchangeRate", back_populates="currency"
    )
    expenses: Mapped[List["Expense"]] = relationship(
        "Expense", back_populates="currency"
    )
    payments: Mapped[List["Payment"]] = relationship(
        "Payment", back_populates="currency"
    )


class ExchangeRate(Base):
    __tablename__ = "exchange_rates"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    currency_code: Mapped[str] = mapped_column(
        sa.String(10), sa.ForeignKey("currencies.code"), nullable=False
    )
    rate_date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    rate_to_inr: Mapped[Decimal] = mapped_column(sa.Numeric(12, 6), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint(
            "currency_code", "rate_date", name="uq_exchange_rates_currency_date"
        ),
    )

    currency: Mapped["Currency"] = relationship("Currency", back_populates="exchange_rates")


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    email: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        nullable=False,
    )

    group_memberships: Mapped[List["GroupMember"]] = relationship(
        "GroupMember", back_populates="user"
    )
    expenses_paid: Mapped[List["Expense"]] = relationship(
        "Expense", back_populates="paid_by_user"
    )
    expense_splits: Mapped[List["ExpenseSplit"]] = relationship(
        "ExpenseSplit", back_populates="user"
    )
    payments_sent: Mapped[List["Payment"]] = relationship(
        "Payment", foreign_keys="[Payment.from_user_id]", back_populates="from_user"
    )
    payments_received: Mapped[List["Payment"]] = relationship(
        "Payment", foreign_keys="[Payment.to_user_id]", back_populates="to_user"
    )
    import_sessions: Mapped[List["ImportSession"]] = relationship(
        "ImportSession", back_populates="imported_by_user"
    )


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        nullable=False,
    )

    members: Mapped[List["GroupMember"]] = relationship(
        "GroupMember", back_populates="group"
    )
    expenses: Mapped[List["Expense"]] = relationship(
        "Expense", back_populates="group"
    )
    payments: Mapped[List["Payment"]] = relationship(
        "Payment", back_populates="group"
    )


class GroupMember(Base):
    __tablename__ = "group_members"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), sa.ForeignKey("groups.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
    )
    joined_at: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    left_at: Mapped[Optional[datetime.date]] = mapped_column(sa.Date, nullable=True)

    __table_args__ = (
        sa.UniqueConstraint(
            "group_id", "user_id", name="uq_group_members_group_user"
        ),
    )

    group: Mapped["Group"] = relationship("Group", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="group_memberships")


class Expense(Base):
    __tablename__ = "expenses"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), sa.ForeignKey("groups.id"), nullable=False
    )
    description: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), nullable=False)
    currency_code: Mapped[str] = mapped_column(
        sa.String(10), sa.ForeignKey("currencies.code"), nullable=False
    )
    split_type: Mapped[SplitType] = mapped_column(
        sa.Enum(SplitType, name="split_type_enum", native_enum=True), nullable=False
    )
    paid_by_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
    )
    date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    is_settlement: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, nullable=False
    )
    source_row: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        nullable=False,
    )

    group: Mapped["Group"] = relationship("Group", back_populates="expenses")
    currency: Mapped["Currency"] = relationship("Currency", back_populates="expenses")
    paid_by_user: Mapped["User"] = relationship("User", back_populates="expenses_paid")
    splits: Mapped[List["ExpenseSplit"]] = relationship(
        "ExpenseSplit", back_populates="expense", cascade="all, delete-orphan"
    )


class ExpenseSplit(Base):
    __tablename__ = "expense_splits"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    expense_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), sa.ForeignKey("expenses.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
    )
    raw_share: Mapped[Decimal] = mapped_column(sa.Numeric(12, 6), nullable=False)
    share_amount_inr: Mapped[Decimal] = mapped_column(
        sa.Numeric(12, 2), nullable=False
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "expense_id", "user_id", name="uq_expense_splits_expense_user"
        ),
    )

    expense: Mapped["Expense"] = relationship("Expense", back_populates="splits")
    user: Mapped["User"] = relationship("User", back_populates="expense_splits")


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), sa.ForeignKey("groups.id"), nullable=False
    )
    from_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
    )
    to_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), nullable=False)
    currency_code: Mapped[str] = mapped_column(
        sa.String(10), sa.ForeignKey("currencies.code"), nullable=False
    )
    amount_inr: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), nullable=False)
    date: Mapped[datetime.date] = mapped_column(sa.Date, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(sa.Text, nullable=True)
    source_row: Mapped[Optional[int]] = mapped_column(sa.Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        nullable=False,
    )

    group: Mapped["Group"] = relationship("Group", back_populates="payments")
    from_user: Mapped["User"] = relationship(
        "User", foreign_keys=[from_user_id], back_populates="payments_sent"
    )
    to_user: Mapped["User"] = relationship(
        "User", foreign_keys=[to_user_id], back_populates="payments_received"
    )
    currency: Mapped["Currency"] = relationship("Currency", back_populates="payments")


class ImportSession(Base):
    __tablename__ = "import_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), sa.ForeignKey("groups.id"), nullable=False
    )
    filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    imported_at: Mapped[datetime.datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        nullable=False,
    )
    imported_by_user_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False
    )
    rows_total: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    rows_imported: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    rows_skipped: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    rows_flagged: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    imported_by_user: Mapped["User"] = relationship(
        "User", back_populates="import_sessions"
    )
    anomalies: Mapped[List["ImportAnomaly"]] = relationship(
        "ImportAnomaly", back_populates="session", cascade="all, delete-orphan"
    )
    group: Mapped["Group"] = relationship("Group")


class ImportAnomaly(Base):
    __tablename__ = "import_anomalies"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.UUID(as_uuid=True),
        sa.ForeignKey("import_sessions.id"),
        nullable=False,
    )
    row_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    raw_row: Mapped[dict] = mapped_column(sa.JSON, nullable=False)
    anomaly_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    description: Mapped[str] = mapped_column(sa.Text, nullable=False)
    action_taken: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(
        sa.Boolean, default=False, nullable=False
    )
    approved: Mapped[Optional[bool]] = mapped_column(sa.Boolean, nullable=True)

    session: Mapped["ImportSession"] = relationship(
        "ImportSession", back_populates="anomalies"
    )
