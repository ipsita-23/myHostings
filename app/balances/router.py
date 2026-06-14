import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.deps import get_current_user
from app.models import User
from app.groups.service import get_group
from app.balances.service import (
    compute_raw_balances,
    minimize_transactions,
    get_member_expense_breakdown,
)

router = APIRouter(tags=["balances"])
templates = Jinja2Templates(directory="templates")


@router.get("/groups/{group_id}/balances", response_class=HTMLResponse)
async def balance_summary(
    group_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    group = await get_group(db, group_id)
    if not group:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

    raw_balances = await compute_raw_balances(db, group_id)
    settlements = minimize_transactions(raw_balances)

    # Load user names for display
    all_user_ids = list(raw_balances.keys())
    settlement_user_ids = [t["from"] for t in settlements] + [t["to"] for t in settlements]
    all_ids = list(set(all_user_ids + settlement_user_ids))

    users_result = await db.execute(
        select(User).where(User.id.in_(all_ids))
    )
    users_map = {u.id: u for u in users_result.scalars().all()}

    return templates.TemplateResponse(
        "balances/summary.html",
        {
            "request": request,
            "current_user": current_user,
            "group": group,
            "raw_balances": raw_balances,
            "settlements": settlements,
            "users_map": users_map,
        },
    )


@router.get("/groups/{group_id}/balances/{user_id}", response_class=HTMLResponse)
async def balance_detail(
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    group = await get_group(db, group_id)
    if not group:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

    user_result = await db.execute(select(User).where(User.id == user_id))
    target_user = user_result.scalar_one_or_none()
    if not target_user:
        return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

    breakdown = await get_member_expense_breakdown(db, group_id, user_id)

    return templates.TemplateResponse(
        "balances/detail.html",
        {
            "request": request,
            "current_user": current_user,
            "group": group,
            "target_user": target_user,
            "breakdown": breakdown,
        },
    )
