import csv
import io
import uuid
import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ImportSession, ImportAnomaly, SplitType, User
from app.groups.service import active_members_on
from app.expenses.service import create_expense, get_exchange_rate
from app.payments.router import record_payment
from app.importer.anomalies import Anomaly, AnomalyType
from app.importer.parser import (
    parse_amount,
    parse_date,
    normalize_name,
    detect_duplicate,
    check_settlement,
    parse_split_details,
)

logger = logging.getLogger("spreetail.importer")


async def run_import(
    db: AsyncSession,
    csv_file: bytes,
    group_id: uuid.UUID,
    imported_by_user_id: uuid.UUID,
    filename: str,
) -> ImportSession:
    """
    Full CSV import orchestrator.
    Transactional: on any unhandled exception, rolls back and re-raises.
    """
    session = ImportSession(
        group_id=group_id,
        filename=filename,
        imported_by_user_id=imported_by_user_id,
        rows_total=0,
        rows_imported=0,
        rows_skipped=0,
        rows_flagged=0,
    )
    db.add(session)
    await db.flush()

    try:
        rows_total = 0
        rows_imported = 0
        rows_skipped = 0
        rows_flagged = 0
        all_anomalies: list[Anomaly] = []

        text = csv_file.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))

        # Build name normalization map from current group members
        from sqlalchemy import select
        members_result = await db.execute(
            select(User)
            .join(
                __import__("app.models", fromlist=["GroupMember"]).GroupMember,
                __import__("app.models", fromlist=["GroupMember"]).GroupMember.user_id == User.id,
            )
            .where(
                __import__("app.models", fromlist=["GroupMember"]).GroupMember.group_id == group_id
            )
        )
        all_members = list(members_result.scalars().all())
        known_names: dict[str, User] = {
            u.name.strip().lower(): u for u in all_members
        }

        seen_rows: list[dict] = []

        for row_number, raw_row in enumerate(reader, start=2):
            rows_total += 1
            row_anomalies: list[Anomaly] = []
            should_skip = False

            # ── a. Parse date ──────────────────────────────────────────────
            raw_date = raw_row.get("date", "").strip()
            parsed_date, date_anomaly = parse_date(raw_date, row_number, raw_row)
            if date_anomaly:
                row_anomalies.append(date_anomaly)
                if date_anomaly.action_taken == "SKIPPED":
                    should_skip = True

            # ── b. Parse amount ────────────────────────────────────────────
            raw_amount = raw_row.get("amount", "").strip()
            parsed_amount, amount_anomaly = parse_amount(raw_amount, row_number, raw_row)
            if amount_anomaly:
                row_anomalies.append(amount_anomaly)
                if amount_anomaly.action_taken == "SKIPPED":
                    should_skip = True

            # ── c. Normalize paid_by ───────────────────────────────────────
            raw_paid_by = raw_row.get("paid_by", "").strip()
            paid_by_user, paid_by_anomaly = normalize_name(
                raw_paid_by, known_names, row_number, raw_row
            )
            if paid_by_anomaly:
                row_anomalies.append(paid_by_anomaly)

            # ── i. Missing paid_by → SKIP ──────────────────────────────────
            if paid_by_user is None:
                missing_anomaly = Anomaly(
                    row_number=row_number,
                    raw_row=raw_row,
                    anomaly_type=AnomalyType.MISSING_FIELD,
                    description=f"Cannot resolve paid_by '{raw_paid_by}' to a group member.",
                    action_taken="SKIPPED",
                    requires_approval=True,
                )
                row_anomalies.append(missing_anomaly)
                should_skip = True

            # ── d. Duplicate detection ─────────────────────────────────────
            normalized_row = {
                "date": raw_date,
                "paid_by": raw_paid_by,
                "amount": raw_amount,
            }
            if detect_duplicate(normalized_row, seen_rows):
                dup_anomaly = Anomaly(
                    row_number=row_number,
                    raw_row=raw_row,
                    anomaly_type=AnomalyType.DUPLICATE,
                    description="Duplicate row detected (same date, paid_by, amount).",
                    action_taken="FLAGGED",
                    requires_approval=True,
                )
                row_anomalies.append(dup_anomaly)
                should_skip = True
            else:
                seen_rows.append(normalized_row)

            # ── k. Zero amount ─────────────────────────────────────────────
            if parsed_amount == Decimal("0"):
                should_skip = True

            if should_skip:
                rows_skipped += 1
                _write_anomalies(db, session.id, row_anomalies)
                if any(a.requires_approval for a in row_anomalies):
                    rows_flagged += 1
                continue

            # ── e. Settlement detection ────────────────────────────────────
            is_settlement = check_settlement(raw_row)

            # ── f. Parse split details ─────────────────────────────────────
            raw_split_type = raw_row.get("split_type", "equal").strip().lower() or "equal"
            raw_split_with = raw_row.get("split_with", "")
            raw_split_details = raw_row.get("split_details", "")

            # ── g/h. Membership date check ─────────────────────────────────
            active_members_list: list[User] = []
            if parsed_date:
                active_members_list = await active_members_on(db, group_id, parsed_date)
                active_member_ids = {str(u.id) for u in active_members_list}

                # Check for non-members
                all_member_ids = {str(u.id) for u in all_members}
                for m in all_members:
                    if str(m.id) not in active_member_ids and str(m.id) in all_member_ids:
                        row_anomalies.append(
                            Anomaly(
                                row_number=row_number,
                                raw_row=raw_row,
                                anomaly_type=AnomalyType.MEMBERSHIP_DATE,
                                description=(
                                    f"{m.name} was not an active member on {parsed_date} "
                                    "and will not be included in this split."
                                ),
                                action_taken="AUTO_FIXED",
                            )
                        )

            split_details, split_anomalies = parse_split_details(
                raw_split_with,
                raw_split_details,
                raw_split_type,
                active_members_list or all_members,
                row_number,
                raw_row,
            )
            row_anomalies.extend(split_anomalies)

            # ── j. Invalid split (bad percentages) ────────────────────────
            has_invalid_split = any(
                a.anomaly_type == AnomalyType.INVALID_SPLIT and a.requires_approval
                for a in row_anomalies
            )
            if has_invalid_split:
                rows_skipped += 1
                rows_flagged += 1
                _write_anomalies(db, session.id, row_anomalies)
                continue

            # ── l. Refund (negative) ───────────────────────────────────────
            # Refund is already handled by parse_amount (negative amount allowed)

            # ── e. Route settlements to record_payment ─────────────────────
            currency_code = raw_row.get("currency", "INR").strip().upper() or "INR"

            if is_settlement and paid_by_user and parsed_date:
                # Determine recipient — who is being paid back
                split_names = [n.strip() for n in raw_split_with.split(",") if n.strip()]
                to_user = None
                for name in split_names:
                    resolved, _ = normalize_name(name, known_names, row_number, raw_row)
                    if resolved and resolved.id != paid_by_user.id:
                        to_user = resolved
                        break

                if to_user:
                    try:
                        await record_payment(
                            db=db,
                            group_id=group_id,
                            from_user_id=paid_by_user.id,
                            to_user_id=to_user.id,
                            amount=abs(parsed_amount),
                            currency_code=currency_code,
                            date=parsed_date,
                            notes=raw_row.get("notes"),
                            source_row=row_number,
                        )
                        row_anomalies.append(
                            Anomaly(
                                row_number=row_number,
                                raw_row=raw_row,
                                anomaly_type=AnomalyType.SETTLEMENT,
                                description=f"Routed as payment from {paid_by_user.name} to {to_user.name}.",
                                action_taken="IMPORTED",
                            )
                        )
                        rows_imported += 1
                    except Exception as e:
                        logger.error("Failed to record settlement payment row %d: %s", row_number, e)
                        rows_skipped += 1
                else:
                    rows_skipped += 1

                _write_anomalies(db, session.id, row_anomalies)
                continue

            # ── m. Create expense ──────────────────────────────────────────
            if paid_by_user and parsed_date:
                try:
                    split_enum = SplitType(raw_split_type) if raw_split_type in SplitType._value2member_map_ else SplitType.EQUAL
                except ValueError:
                    split_enum = SplitType.EQUAL

                try:
                    await create_expense(
                        db=db,
                        group_id=group_id,
                        description=raw_row.get("description", "Imported expense"),
                        total_amount=abs(parsed_amount),
                        currency_code=currency_code,
                        split_type=split_enum,
                        split_details=split_details,
                        paid_by_user_id=paid_by_user.id,
                        date=parsed_date,
                        notes=raw_row.get("notes"),
                        source_row=row_number,
                    )
                    rows_imported += 1
                except Exception as e:
                    logger.error("Failed to import row %d: %s", row_number, e)
                    row_anomalies.append(
                        Anomaly(
                            row_number=row_number,
                            raw_row=raw_row,
                            anomaly_type=AnomalyType.FORMAT,
                            description=f"Import failed: {e}",
                            action_taken="SKIPPED",
                            requires_approval=True,
                        )
                    )
                    rows_skipped += 1

            # ── n. Write anomalies for this row ────────────────────────────
            _write_anomalies(db, session.id, row_anomalies)
            if any(a.requires_approval for a in row_anomalies):
                rows_flagged += 1

        # ── 5. Update session counts ───────────────────────────────────────
        session.rows_total = rows_total
        session.rows_imported = rows_imported
        session.rows_skipped = rows_skipped
        session.rows_flagged = rows_flagged
        await db.commit()

    except Exception:
        await db.rollback()
        raise

    return session


def _write_anomalies(
    db: AsyncSession, session_id: uuid.UUID, anomalies: list[Anomaly]
) -> None:
    for a in anomalies:
        record = ImportAnomaly(
            session_id=session_id,
            row_number=a.row_number,
            raw_row=a.raw_row,
            anomaly_type=a.anomaly_type,
            description=a.description,
            action_taken=a.action_taken,
            requires_approval=a.requires_approval,
            approved=None,
        )
        db.add(record)
