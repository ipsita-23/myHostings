import uuid

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.deps import get_current_user
from app.models import User, ImportSession, ImportAnomaly
from app.importer.pipeline import run_import

router = APIRouter(prefix="/import", tags=["importer"])
templates = Jinja2Templates(directory="templates")


def flash(response: RedirectResponse, message: str, category: str = "success") -> None:
    response.set_cookie("flash_message", message, max_age=10, httponly=True)
    response.set_cookie("flash_category", category, max_age=10, httponly=True)


@router.get("/", response_class=HTMLResponse)
async def upload_page(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from app.groups.service import get_user_groups
    groups = await get_user_groups(db, current_user.id)
    return templates.TemplateResponse(
        "import/upload.html",
        {"request": request, "current_user": current_user, "groups": groups},
    )


@router.post("/")
async def upload_csv(
    request: Request,
    group_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    contents = await file.read()
    try:
        session = await run_import(
            db=db,
            csv_file=contents,
            group_id=group_id,
            imported_by_user_id=current_user.id,
            filename=file.filename,
        )
    except Exception as e:
        resp = RedirectResponse(url="/import", status_code=303)
        flash(resp, f"Import failed: {e}", "error")
        return resp

    resp = RedirectResponse(
        url=f"/import/{session.id}/report", status_code=303
    )
    flash(
        resp,
        f"Import complete: {session.rows_imported} imported, "
        f"{session.rows_skipped} skipped, {session.rows_flagged} flagged.",
    )
    return resp


@router.get("/{session_id}/report", response_class=HTMLResponse)
async def import_report(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ImportSession).where(ImportSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        return templates.TemplateResponse(
            "404.html", {"request": request}, status_code=404
        )

    anomalies_result = await db.execute(
        select(ImportAnomaly)
        .where(ImportAnomaly.session_id == session_id)
        .order_by(ImportAnomaly.row_number)
    )
    anomalies = list(anomalies_result.scalars().all())
    pending_count = sum(
        1 for a in anomalies if a.requires_approval and a.approved is None
    )

    return templates.TemplateResponse(
        "import/report.html",
        {
            "request": request,
            "current_user": current_user,
            "session": session,
            "anomalies": anomalies,
            "pending_count": pending_count,
        },
    )


@router.get("/{session_id}/approve", response_class=HTMLResponse)
async def approve_page(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ImportSession).where(ImportSession.id == session_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        return templates.TemplateResponse(
            "404.html", {"request": request}, status_code=404
        )

    pending_result = await db.execute(
        select(ImportAnomaly)
        .where(ImportAnomaly.session_id == session_id)
        .where(ImportAnomaly.requires_approval == True)
        .where(ImportAnomaly.approved == None)
        .order_by(ImportAnomaly.row_number)
    )
    pending = list(pending_result.scalars().all())

    return templates.TemplateResponse(
        "import/approve.html",
        {
            "request": request,
            "current_user": current_user,
            "session": session,
            "pending": pending,
        },
    )


@router.post("/{session_id}/approve/{anomaly_id}")
async def approve_anomaly(
    session_id: uuid.UUID,
    anomaly_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    import datetime
    from decimal import Decimal
    from app.models import Expense, Payment, SplitType
    from app.expenses.service import create_expense
    from app.payments.router import record_payment
    from app.groups.service import active_members_on

    result = await db.execute(
        select(ImportAnomaly).where(ImportAnomaly.id == anomaly_id)
    )
    anomaly = result.scalar_one_or_none()
    if anomaly:
        anomaly.approved = True
        
        # If the row was skipped/flagged (i.e. not already imported), import it now!
        if anomaly.action_taken != "IMPORTED":
            sess_res = await db.execute(
                select(ImportSession).where(ImportSession.id == session_id)
            )
            session = sess_res.scalar_one_or_none()
            if session:
                raw = anomaly.raw_row
                
                # 1. Parse date
                parsed_date = None
                raw_date = raw.get("date", "").strip()
                for fmt in ["%d-%m-%Y", "%Y-%m-%d", "%b-%y", "%b-%Y", "%m-%d-%Y"]:
                    try:
                        parsed_date = datetime.datetime.strptime(raw_date, fmt).date()
                        break
                    except ValueError:
                        continue
                if not parsed_date:
                    parsed_date = datetime.date.today()
                
                # 2. Parse amount
                parsed_amount = Decimal("0")
                try:
                    parsed_amount = Decimal(raw.get("amount", "0").strip().replace(",", "").replace(" ", ""))
                except Exception:
                    pass
                
                # 3. Resolve paid_by
                # Let's load group members to resolve paid_by
                members_res = await db.execute(
                    select(User)
                    .join(
                        __import__("app.models", fromlist=["GroupMember"]).GroupMember,
                        __import__("app.models", fromlist=["GroupMember"]).GroupMember.user_id == User.id,
                    )
                    .where(
                        __import__("app.models", fromlist=["GroupMember"]).GroupMember.group_id == session.group_id
                    )
                )
                all_members = list(members_res.scalars().all())
                paid_by_user = None
                raw_paid_by = raw.get("paid_by", "").strip().lower()
                # Try exact match or prefix
                for m in all_members:
                    if m.name.strip().lower() == raw_paid_by:
                        paid_by_user = m
                        break
                if not paid_by_user:
                    for m in all_members:
                        if m.name.strip().lower().startswith(raw_paid_by) or raw_paid_by.startswith(m.name.strip().lower()):
                            paid_by_user = m
                            break
                if not paid_by_user:
                    # Default to current user
                    paid_by_user = current_user

                # 4. Check if settlement
                from app.importer.parser import check_settlement, parse_split_details
                is_settlement = check_settlement(raw)
                
                currency_code = raw.get("currency", "INR").strip().upper() or "INR"
                
                if is_settlement:
                    # Find to_user
                    raw_split_with = raw.get("split_with", "")
                    split_names = [n.strip() for n in raw_split_with.split(",") if n.strip()]
                    to_user = None
                    for name in split_names:
                        name_norm = name.strip().lower()
                        for m in all_members:
                            if m.name.strip().lower() == name_norm or m.name.strip().lower().startswith(name_norm):
                                if m.id != paid_by_user.id:
                                    to_user = m
                                    break
                        if to_user:
                            break
                    if not to_user:
                        # Fallback to any member other than paid_by
                        for m in all_members:
                            if m.id != paid_by_user.id:
                                to_user = m
                                break
                    if to_user:
                        await record_payment(
                            db=db,
                            group_id=session.group_id,
                            from_user_id=paid_by_user.id,
                            to_user_id=to_user.id,
                            amount=abs(parsed_amount),
                            currency_code=currency_code,
                            date=parsed_date,
                            notes=raw.get("notes"),
                            source_row=anomaly.row_number,
                        )
                        anomaly.action_taken = "IMPORTED"
                        session.rows_imported += 1
                        session.rows_skipped -= 1
                else:
                    # Create expense
                    raw_split_type = raw.get("split_type", "equal").strip().lower() or "equal"
                    split_enum = SplitType.EQUAL
                    if raw_split_type in SplitType._value2member_map_:
                        split_enum = SplitType(raw_split_type)
                    
                    # Split details
                    active_members_list = await active_members_on(db, session.group_id, parsed_date)
                    split_details, _ = parse_split_details(
                        raw.get("split_with", ""),
                        raw.get("split_details", ""),
                        raw_split_type,
                        active_members_list or all_members,
                        anomaly.row_number,
                        raw,
                    )
                    
                    await create_expense(
                        db=db,
                        group_id=session.group_id,
                        description=raw.get("description", "Imported expense (approved)"),
                        total_amount=abs(parsed_amount),
                        currency_code=currency_code,
                        split_type=split_enum,
                        split_details=split_details,
                        paid_by_user_id=paid_by_user.id,
                        date=parsed_date,
                        notes=raw.get("notes"),
                        source_row=anomaly.row_number,
                    )
                    anomaly.action_taken = "IMPORTED"
                    session.rows_imported += 1
                    session.rows_skipped -= 1
        
        await db.commit()
    resp = RedirectResponse(url=f"/import/{session_id}/approve", status_code=303)
    return resp


@router.post("/{session_id}/reject/{anomaly_id}")
async def reject_anomaly(
    session_id: uuid.UUID,
    anomaly_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from app.models import Expense, Payment
    result = await db.execute(
        select(ImportAnomaly).where(ImportAnomaly.id == anomaly_id)
    )
    anomaly = result.scalar_one_or_none()
    if anomaly:
        anomaly.approved = False
        
        # If the row was already imported, we reject it: delete the imported expense/payment!
        if anomaly.action_taken == "IMPORTED":
            sess_res = await db.execute(
                select(ImportSession).where(ImportSession.id == session_id)
            )
            session = sess_res.scalar_one_or_none()
            if session:
                # Find and delete/soft-delete expense
                exp_res = await db.execute(
                    select(Expense)
                    .where(Expense.group_id == session.group_id)
                    .where(Expense.source_row == anomaly.row_number)
                )
                expense = exp_res.scalar_one_or_none()
                if expense:
                    # Hard delete
                    await db.delete(expense)
                    session.rows_imported -= 1
                    session.rows_skipped += 1
                else:
                    # Check payment
                    pay_res = await db.execute(
                        select(Payment)
                        .where(Payment.group_id == session.group_id)
                        .where(Payment.source_row == anomaly.row_number)
                    )
                    payment = pay_res.scalar_one_or_none()
                    if payment:
                        await db.delete(payment)
                        session.rows_imported -= 1
                        session.rows_skipped += 1
                
                anomaly.action_taken = "SKIPPED"
        
        await db.commit()
    resp = RedirectResponse(url=f"/import/{session_id}/approve", status_code=303)
    return resp
