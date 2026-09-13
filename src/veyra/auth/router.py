"""FastAPI auth router + dependencies (Phase 8).

Gates the command-center behind an authenticated admin session. Local
email+password only; sessions are opaque bearer tokens stored as cookies.
General signup stays closed; the operator account is seeded from config.

The auth service is expected to be installed on the app as
``app.state.auth = AuthService(...)`` so the shared dependency and router both
read it from the request app (works with dependency-injected test apps, too).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .service import AuthError
from .ratelimit import LoginLimiter

SESSION_COOKIE = "veyra_session"
LOGIN_TEMPLATE = "login.html"


def get_auth_service(request: Request):
    return request.app.state.auth


def current_user(
    request: Request,
    veyra_session: Optional[str] = Cookie(default=None),
) -> Optional[object]:
    service = get_auth_service(request)
    return service.resolve_session(veyra_session)


def _is_browser_path(request: Request) -> bool:
    """True for HTML page routes, False for /api/* and /ws/*."""
    path = request.url.path
    return not (path.startswith("/api/") or path.startswith("/ws/"))


def require_user(
    request: Request,
    user: Optional[object] = Depends(current_user),
) -> object:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    return user


def _auth_redirect_middleware(app):
    """Middleware factory: redirect browser requests to /login on 401.

    ``require_user`` raises HTTPException(401) for unauthenticated users on
    ALL paths. This middleware catches those 401s on browser paths (HTML page
    requests) and converts them to 303 redirects to /login. API/WS paths
    keep the 401 response.

    Auth endpoints themselves (/login, /logout, /api/session) are excluded
    so they can return their own responses (e.g. 401 for bad credentials,
    303 for successful login).
    """
    from fastapi.responses import RedirectResponse as _RR

    @app.middleware("http")
    async def _redirect_401_to_login(request, call_next):
        response = await call_next(request)
        if response.status_code != 401:
            return response
        if not _is_browser_path(request):
            return response
        # Don't intercept auth endpoints — they handle their own 401s.
        path = request.url.path
        if path in ("/login", "/logout") or path.startswith("/api/session"):
            return response
        from urllib.parse import urlencode
        qs = urlencode({"next": path}) if path != "/login" else ""
        target = f"/login{'?' + qs if qs else ''}"
        return _RR(target, status_code=303)


def _login_url(next_path: str) -> str:
    from urllib.parse import urlencode

    return f"/login?{urlencode({'next': next_path})}"


def _secure_cookie(request: Request) -> bool:
    """Session cookies are Secure only in production.

    In prod the dashboard is expected to sit behind HTTPS; setting ``Secure``
    prevents the session token travelling over plain HTTP. Dev/test keep it
    False so a local ``http://127.0.0.1`` run still works.
    """
    try:
        from ..config import get_settings

        return get_settings().environment == "prod"
    except Exception:  # noqa: BLE001 - conservative default: plain HTTP local
        return False


def _client_key(request: Request, email: str) -> str:
    host = request.client.host if request.client else "?"
    return f"{host}|{(email or '').strip().lower()}"


def build_auth_router(templates: Optional[Jinja2Templates] = None) -> APIRouter:
    router = APIRouter()
    login_limiter = LoginLimiter()

    @router.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/") -> HTMLResponse:
        if templates is not None:
            return templates.TemplateResponse(
                request, LOGIN_TEMPLATE, {"next": next, "error": None}
            )
        return HTMLResponse("login")

    @router.post("/login")
    def login_submit(
        request: Request,
        next: str = Form(default="/"),
        email: str = Form(default=""),
        password: str = Form(default=""),
    ) -> HTMLResponse:
        service = get_auth_service(request)
        # Phase 10: throttle brute-force attempts (per IP+account). A blocked
        # key is told only that it is rate-limited — never *which* field was
        # wrong, so account enumeration stays unrewarding.
        if not login_limiter.allow(_client_key(request, email)):
            if templates is not None:
                return templates.TemplateResponse(
                    request,
                    LOGIN_TEMPLATE,
                    {"next": next, "error": "too many attempts; try again later", "email": email},
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many attempts; try again later",
            )
        try:
            token = service.authenticate(email, password)
        except AuthError as exc:
            if templates is not None:
                return templates.TemplateResponse(
                    request,
                    LOGIN_TEMPLATE,
                    {"next": next, "error": str(exc), "email": email},
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))
        resp = RedirectResponse(next if next.startswith("/") else "/", status_code=303)
        resp.set_cookie(
            key=SESSION_COOKIE,
            value=token,
            httponly=True,
            samesite="lax",
            secure=_secure_cookie(request),
            max_age=service.session_ttl_seconds,
        )
        return resp

    @router.get("/logout")
    def logout(
        request: Request,
        veyra_session: Optional[str] = Cookie(default=None),
    ) -> RedirectResponse:
        service = get_auth_service(request)
        if veyra_session:
            service.logout(veyra_session)
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(
            SESSION_COOKIE,
            httponly=True,
            samesite="lax",
            secure=_secure_cookie(request),
        )
        return resp

    @router.get("/api/session")
    def api_session(
        request: Request,
        user: Optional[object] = Depends(current_user),
    ) -> JSONResponse:
        if user is None:
            return JSONResponse({"authenticated": False})
        payload = {"authenticated": True, "email": user.email, "role": user.role}
        # Phase 9: include honest entitlement status when a subscription service
        # is installed on the app (the dashboard installs one).
        subs = getattr(request.app.state, "subscriptions", None)
        if subs is not None:
            try:
                payload["entitlements"] = subs.status(user.id)
            except Exception:  # noqa: BLE001 - entitlement display is non-fatal
                payload["entitlements"] = None
        return JSONResponse(payload)

    return router