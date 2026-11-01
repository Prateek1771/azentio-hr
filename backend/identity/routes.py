import os

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from identity import service as svc
from identity.perms import ROLES

router = APIRouter(prefix="/api")


def _err(e: svc.AuthError):
    raise HTTPException(e.status, str(e))


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=512)


@router.post("/auth/login")
def login(body: LoginBody, response: Response):
    try:
        user, token = svc.login(body.email, body.password)
    except svc.AuthError as e:
        _err(e)
    response.set_cookie(svc.COOKIE, token, httponly=True, samesite="lax", path="/",
                        secure=os.getenv("COOKIE_SECURE", "false").lower() == "true",
                        max_age=int(os.getenv("SESSION_DAYS", "7")) * 86400)
    return {"user": user}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response):
    import audit
    u = svc.current_user.get()
    svc.logout(request.cookies.get(svc.COOKIE))
    if u:
        audit.record_audit("auth.logout", "user", str(u["id"]), f"{u['email']} signed out")
    response.delete_cookie(svc.COOKIE, path="/")


@router.get("/auth/me")
def me():
    return svc.current_user.get()


class PasswordBody(BaseModel):
    current: str = Field(max_length=512)
    new: str = Field(max_length=512)


@router.post("/auth/password", status_code=204)
def change_password(body: PasswordBody, request: Request):
    try:
        svc.change_password(svc.current_user.get(), body.current, body.new, request.cookies.get(svc.COOKIE))
    except svc.AuthError as e:
        _err(e)


@router.get("/users")
def list_users():
    return svc.list_users()


class NewUser(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    name: str = Field(min_length=1, max_length=200)
    role: str
    password: str = Field(max_length=512)


@router.post("/users", status_code=201)
def create_user(body: NewUser):
    if body.role not in ROLES:
        raise HTTPException(422, f"Role must be one of {', '.join(ROLES)}")
    try:
        return svc.create_user(body.email, body.name, body.role, body.password)
    except svc.AuthError as e:
        _err(e)


class UserPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    role: str | None = None
    active: bool | None = None
    password: str | None = Field(None, max_length=512)


@router.patch("/users/{uid}")
def update_user(uid: int, body: UserPatch):
    try:
        return svc.update_user(uid, body.name, body.role, body.active, body.password)
    except svc.AuthError as e:
        _err(e)
