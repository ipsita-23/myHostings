import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth.router import router as auth_router
from app.groups.router import router as groups_router
from app.expenses.router import router as expenses_router
from app.balances.router import router as balances_router
from app.payments.router import router as payments_router
from app.importer.router import router as importer_router

app = FastAPI(title="Spreetail Shared Expenses")

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates for exception pages
templates = Jinja2Templates(directory="templates")

# Middleware to clear flash cookies after they have been read
@app.middleware("http")
async def clear_flash_cookies_middleware(request: Request, call_next):
    response = await call_next(request)
    if "flash_message" in request.cookies:
        response.delete_cookie("flash_message")
    if "flash_category" in request.cookies:
        response.delete_cookie("flash_category")
    return response

# Global exception handler for authentication redirection
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        # Redirect to login page on unauthorized error
        response = RedirectResponse(url="/login", status_code=303)
        response.delete_cookie("session")
        return response
    if exc.status_code == 404:
        return templates.TemplateResponse(
            "404.html", {"request": request}, status_code=404
        )
    raise exc

# Include routers
app.include_router(auth_router)
app.include_router(groups_router)
app.include_router(expenses_router)
app.include_router(balances_router)
app.include_router(payments_router)
app.include_router(importer_router)
