from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import User
from app.auth import service as auth_svc

router = APIRouter(tags=["auth"])
templates = Jinja2Templates(directory="templates")


def flash(response: RedirectResponse, message: str, category: str = "success") -> None:
    response.set_cookie("flash_message", message, max_age=10, httponly=True)
    response.set_cookie("flash_category", category, max_age=10, httponly=True)


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("auth/register.html", {"request": request})


@router.post("/register")
async def register(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    email_clean = email.lower().strip()
    if len(password) < 8:
        return templates.TemplateResponse(
            "auth/register.html",
            {"request": request, "error": "Password must be at least 8 characters long."},
        )

    stmt = select(User).where(User.email == email_clean)
    res = await db.execute(stmt)
    existing_user = res.scalar_one_or_none()
    if existing_user:
        return templates.TemplateResponse(
            "auth/register.html",
            {"request": request, "error": "Email is already registered."},
        )

    try:
        user = await auth_svc.create_user(db, name, email_clean, password)
        token = auth_svc.create_session_token(user.email)
        resp = RedirectResponse(url="/", status_code=303)
        resp.set_cookie("session", token, httponly=True, max_age=86400)
        flash(resp, f"Welcome to Spreetail, {user.name}!")
        return resp
    except Exception as e:
        return templates.TemplateResponse(
            "auth/register.html",
            {"request": request, "error": f"An error occurred: {str(e)}"},
        )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("auth/login.html", {"request": request})


@router.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    user = await auth_svc.authenticate_user(db, email, password)
    if not user:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "error": "Invalid email or password."},
        )

    token = auth_svc.create_session_token(user.email)
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie("session", token, httponly=True, max_age=86400)
    flash(resp, f"Logged in successfully. Welcome back, {user.name}!")
    return resp


@router.post("/logout")
async def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("session")
    resp.set_cookie("flash_message", "Logged out successfully.", max_age=10, httponly=True)
    resp.set_cookie("flash_category", "success", max_age=10, httponly=True)
    return resp
