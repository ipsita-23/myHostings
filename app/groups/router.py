import datetime
import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.groups import service as group_svc
from app.balances.service import compute_raw_balances

router = APIRouter(tags=["groups"])
templates = Jinja2Templates(directory="templates")


def flash(response: RedirectResponse, message: str, category: str = "success") -> None:
    response.set_cookie("flash_message", message, max_age=10, httponly=True)
    response.set_cookie("flash_category", category, max_age=10, httponly=True)


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    groups = await group_svc.get_user_groups(db, current_user.id)
    group_balances = {}
    for g in groups:
        balances = await compute_raw_balances(db, g.id)
        group_balances[g.id] = balances.get(current_user.id, 0)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "current_user": current_user,
            "groups": groups,
            "group_balances": group_balances,
        },
    )


@router.get("/groups/new", response_class=HTMLResponse)
async def new_group_page(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    return templates.TemplateResponse(
        "groups/create.html",
        {"request": request, "current_user": current_user},
    )


@router.post("/groups")
async def create_group(
    request: Request,
    name: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    group = await group_svc.create_group(db, name=name, creator_user_id=current_user.id)
    resp = RedirectResponse(url=f"/groups/{group.id}", status_code=303)
    flash(resp, f"Group '{group.name}' created.")
    return resp


@router.get("/groups/{group_id}", response_class=HTMLResponse)
async def group_detail(
    group_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    group = await group_svc.get_group(db, group_id)
    if not group:
        return templates.TemplateResponse(
            "404.html", {"request": request}, status_code=404
        )
    members_raw = await group_svc.get_group_members(db, group_id)
    # Load user objects for each membership
    from sqlalchemy import select
    from app.models import User as UserModel, Expense
    user_ids = [m.user_id for m in members_raw]
    users_result = await db.execute(
        select(UserModel).where(UserModel.id.in_(user_ids))
    )
    users_map = {u.id: u for u in users_result.scalars().all()}
    members = [(m, users_map.get(m.user_id)) for m in members_raw]

    # Recent expenses (non-deleted)
    expenses_result = await db.execute(
        select(Expense)
        .where(Expense.group_id == group_id)
        .where(Expense.is_deleted == False)
        .order_by(Expense.date.desc())
        .limit(10)
    )
    recent_expenses = list(expenses_result.scalars().all())

    balances = await compute_raw_balances(db, group_id)

    return templates.TemplateResponse(
        "groups/detail.html",
        {
            "request": request,
            "current_user": current_user,
            "group": group,
            "members": members,
            "recent_expenses": recent_expenses,
            "balances": balances,
            "users_map": users_map,
        },
    )


@router.post("/groups/{group_id}/members")
async def add_member(
    group_id: uuid.UUID,
    request: Request,
    email: str = Form(...),
    joined_at: str = Form(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    group = await group_svc.get_group(db, group_id)
    if not group:
        resp = RedirectResponse(url="/", status_code=303)
        flash(resp, "Group not found.", "error")
        return resp

    user = await group_svc.find_user_by_email(db, email)
    if not user:
        resp = RedirectResponse(url=f"/groups/{group_id}", status_code=303)
        flash(resp, f"No user found with email: {email}", "error")
        return resp

    join_date = datetime.date.today()
    if joined_at:
        try:
            join_date = datetime.date.fromisoformat(joined_at)
        except ValueError:
            pass

    await group_svc.add_member(db, group_id=group_id, user_id=user.id, joined_at=join_date)
    resp = RedirectResponse(url=f"/groups/{group_id}", status_code=303)
    flash(resp, f"{user.name} added to group.")
    return resp


@router.post("/groups/{group_id}/members/{user_id}/remove")
async def remove_member(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await group_svc.remove_member(
        db,
        group_id=group_id,
        user_id=user_id,
        left_at=datetime.date.today(),
    )
    resp = RedirectResponse(url=f"/groups/{group_id}", status_code=303)
    flash(resp, "Member removed from group.")
    return resp
