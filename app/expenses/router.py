import uuid
import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.deps import get_current_user
from app.models import User, SplitType, ExpenseSplit
from app.groups.service import get_group, active_members_on
from app.expenses import service as expense_svc

router = APIRouter(tags=["expenses"])
templates = Jinja2Templates(directory="templates")


def flash(response: RedirectResponse, message: str, category: str = "success") -> None:
    response.set_cookie("flash_message", message, max_age=10, httponly=True)
    response.set_cookie("flash_category", category, max_age=10, httponly=True)


@router.get("/groups/{group_id}/expenses/new", response_class=HTMLResponse)
async def new_expense_page(
    group_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    group = await get_group(db, group_id)
    if not group:
        return templates.TemplateResponse(
            "404.html", {"request": request}, status_code=404
        )
    today = datetime.date.today()
    members = await active_members_on(db, group_id, today)
    return templates.TemplateResponse(
        "expenses/create.html",
        {
            "request": request,
            "current_user": current_user,
            "group": group,
            "members": members,
            "split_types": [s.value for s in SplitType],
            "today": today.isoformat(),
        },
    )


@router.post("/groups/{group_id}/expenses")
async def create_expense(
    group_id: uuid.UUID,
    request: Request,
    description: str = Form(...),
    total_amount: str = Form(...),
    currency_code: str = Form(default="INR"),
    split_type: str = Form(...),
    paid_by_user_id: uuid.UUID = Form(...),
    date: str = Form(...),
    notes: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    group = await get_group(db, group_id)
    if not group:
        resp = RedirectResponse(url="/", status_code=303)
        flash(resp, "Group not found.", "error")
        return resp

    try:
        amount = Decimal(total_amount.replace(",", ""))
        expense_date = datetime.date.fromisoformat(date)
        split_enum = SplitType(split_type)
    except (ValueError, Exception) as e:
        members = await active_members_on(db, group_id, datetime.date.today())
        return templates.TemplateResponse(
            "expenses/create.html",
            {
                "request": request,
                "current_user": current_user,
                "group": group,
                "members": members,
                "split_types": [s.value for s in SplitType],
                "today": datetime.date.today().isoformat(),
                "error": f"Invalid input: {e}",
            },
            status_code=400,
        )

    # Build split_details from active members (equal split by default)
    # For equal split, pass empty dict — calculate_splits handles it
    split_details: dict = {}

    try:
        expense = await expense_svc.create_expense(
            db=db,
            group_id=group_id,
            description=description,
            total_amount=amount,
            currency_code=currency_code.upper(),
            split_type=split_enum,
            split_details=split_details,
            paid_by_user_id=paid_by_user_id,
            date=expense_date,
            notes=notes or None,
        )
    except ValueError as e:
        resp = RedirectResponse(url=f"/groups/{group_id}", status_code=303)
        flash(resp, str(e), "error")
        return resp

    resp = RedirectResponse(url=f"/groups/{group_id}", status_code=303)
    flash(resp, f"Expense '{description}' added.")
    return resp


@router.get("/expenses/{expense_id}", response_class=HTMLResponse)
async def expense_detail(
    expense_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    expense = await expense_svc.get_expense(db, expense_id)
    if not expense or expense.is_deleted:
        return templates.TemplateResponse(
            "404.html", {"request": request}, status_code=404
        )

    # Load splits with user data
    splits_result = await db.execute(
        select(ExpenseSplit).where(ExpenseSplit.expense_id == expense_id)
    )
    splits = list(splits_result.scalars().all())

    from app.models import User as UserModel
    user_ids = [s.user_id for s in splits] + [expense.paid_by_user_id]
    users_result = await db.execute(
        select(UserModel).where(UserModel.id.in_(user_ids))
    )
    users_map = {u.id: u for u in users_result.scalars().all()}

    # Active members on the expense date
    active_on_date = await active_members_on(db, expense.group_id, expense.date)

    return templates.TemplateResponse(
        "expenses/detail.html",
        {
            "request": request,
            "current_user": current_user,
            "expense": expense,
            "splits": splits,
            "users_map": users_map,
            "active_on_date": active_on_date,
            "paid_by_user": users_map.get(expense.paid_by_user_id),
        },
    )


@router.post("/expenses/{expense_id}/delete")
async def delete_expense(
    expense_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    expense = await expense_svc.get_expense(db, expense_id)
    if not expense:
        resp = RedirectResponse(url="/", status_code=303)
        flash(resp, "Expense not found.", "error")
        return resp

    group_id = expense.group_id
    await expense_svc.soft_delete_expense(db, expense_id)
    resp = RedirectResponse(url=f"/groups/{group_id}", status_code=303)
    flash(resp, "Expense deleted.")
    return resp
