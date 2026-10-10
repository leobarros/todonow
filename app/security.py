"""Shared access-control helpers."""

from __future__ import annotations

from functools import wraps
from urllib.parse import urlsplit

from flask import abort, current_app
from flask_login import current_user


def admin_required(view):
    """Allow only authenticated administrators."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if not getattr(current_user, "is_admin", False):
            abort(403)
        return view(*args, **kwargs)

    return wrapper


def registration_allowed() -> bool:
    """Resolve the ALLOW_REGISTRATION policy (yes / no / auto)."""
    from .models import User

    policy = str(current_app.config.get("ALLOW_REGISTRATION", "auto")).strip().lower()
    if policy in {"1", "true", "yes", "on", "y", "always"}:
        return True
    if policy in {"0", "false", "no", "off", "n", "never"}:
        return False
    # "auto": open only while the instance has no accounts yet.
    return User.query.count() == 0


def safe_redirect_target(target: str | None, fallback: str = "main.dashboard") -> str:
    """Return *target* only when it is a safe, local path."""
    if not target:
        from flask import url_for

        return url_for(fallback)
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/"):
        from flask import url_for

        return url_for(fallback)
    return target


def safe_referrer_path(fallback: str = "main.dashboard") -> str:
    """Local path taken from the ``Referer`` header, else ``url_for(fallback)``.

    Used to bounce the user back to the page that failed (e.g. an expired CSRF
    token) without ever redirecting to another host.
    """
    from flask import request, url_for

    referrer = request.referrer
    if referrer:
        parts = urlsplit(referrer)
        host = (parts.netloc or "").lower()
        current_host = (request.host or "").lower()
        if not host or host == current_host:
            path = parts.path or "/"
            if parts.query:
                path = f"{path}?{parts.query}"
            if path.startswith("/") and not path.startswith("//"):
                return path
    return url_for(fallback)
