"""Authentication, registration, profile and user administration routes."""

from __future__ import annotations

from flask import (
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..models import User
from ..security import admin_required, registration_allowed, safe_redirect_target
from ..utils import now_local
from . import bp
from .forms import (
    AdminResetPasswordForm,
    AdminUserForm,
    ChangePasswordForm,
    LoginForm,
    ProfileForm,
    RegistrationForm,
)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
        if user is None or not user.check_password(form.password.data):
            flash("Usuário ou senha inválidos.", "danger")
        elif not user.is_active:
            flash("Esta conta está desativada. Procure um administrador.", "warning")
        else:
            login_user(user, remember=form.remember.data)
            user.last_login_at = now_local()
            db.session.commit()
            flash(f"Bem-vindo de volta, {user.name}!", "success")
            return redirect(safe_redirect_target(request.args.get("next")))

    return _render_login(form)


def _render_login(login_form=None, register_form=None, active_tab: str = "login", status: int = 200):
    """Render the login screen, which also carries the sign-up tab."""
    return (
        render_template(
            "auth/login.html",
            form=login_form or LoginForm(),
            register_form=register_form or RegistrationForm(),
            active_tab=active_tab,
            allow_registration=registration_allowed(),
        ),
        status,
    )


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("Sessão encerrada.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/register", methods=["GET", "POST"])
def register():
    """Create an account.

    Reachable both from the dedicated page (``/auth/register``) and from the
    "Criar conta" tab on the login screen — the hidden ``origin`` field tells us
    where to re-render validation errors.
    """
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    from_login_tab = (request.form.get("origin") or "").strip() == "login"

    if not registration_allowed():
        flash(
            "O cadastro de novos usuários está desativado. Peça ao administrador para criar a sua conta.",
            "warning",
        )
        return redirect(url_for("auth.login"))

    form = RegistrationForm()
    if form.validate_on_submit():
        user = _create_user(form)
        login_user(user)
        flash("Conta criada com sucesso. Bom trabalho!", "success")
        if user.is_admin:
            flash("Você é o primeiro usuário e foi promovido a administrador.", "info")
        return redirect(url_for("main.dashboard"))

    if from_login_tab:
        # Mantém o usuário na aba de cadastro, com os erros visíveis.
        return _render_login(register_form=form, active_tab="register", status=200)

    return render_template("auth/register.html", form=form)


def _create_user(form: RegistrationForm) -> User:
    """Build and persist an account from a validated registration form."""
    is_first = User.query.count() == 0
    user = User(
        username=form.username.data.strip(),
        display_name=(form.display_name.data or "").strip() or None,
        email=(form.email.data or "").strip().lower() or None,
    )
    user.set_password(form.password.data)
    user.is_admin = bool(is_first and _first_user_is_admin())
    user.is_active_flag = True
    db.session.add(user)
    db.session.commit()
    return user


def _first_user_is_admin() -> bool:
    from flask import current_app

    return bool(current_app.config.get("FIRST_USER_IS_ADMIN", True))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ProfileForm(obj=current_user)
    password_form = ChangePasswordForm()
    if form.submit.data and form.validate_on_submit():
        current_user.display_name = (form.display_name.data or "").strip() or None
        current_user.email = (form.email.data or "").strip().lower() or None
        current_user.theme = form.theme.data
        db.session.commit()
        flash("Perfil atualizado.", "success")
        return redirect(url_for("auth.profile"))

    stats = _profile_stats()
    return render_template(
        "auth/profile.html",
        form=form,
        password_form=password_form,
        stats=stats,
    )


@bp.post("/profile/password")
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        current_user.set_password(form.password.data)
        db.session.commit()
        flash("Senha alterada com sucesso.", "success")
    else:
        for errors in form.errors.values():
            for error in errors:
                flash(error, "danger")
    return redirect(url_for("auth.profile"))


def _profile_stats() -> dict:
    from ..models import Task

    stats = Task.stats(current_user)
    recent = Task.for_user(current_user).order_by(Task.updated_at.desc()).limit(5).all()
    stats["recent"] = recent
    return stats


# --------------------------------------------------------------------------
# Administration
# --------------------------------------------------------------------------


@bp.route("/users")
@login_required
@admin_required
def users():
    form = AdminUserForm()
    all_users = User.query.order_by(User.username).all()
    counts = {user.id: len(user.tasks) for user in all_users}
    return render_template("auth/users.html", users=all_users, form=form, counts=counts)


@bp.post("/users/create")
@login_required
@admin_required
def create_user():
    form = AdminUserForm()
    if form.validate_on_submit():
        user = User(
            username=form.username.data.strip(),
            display_name=(form.display_name.data or "").strip() or None,
            email=(form.email.data or "").strip().lower() or None,
            is_admin=bool(form.is_admin.data),
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash(f"Usuário {user.username} criado.", "success")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                flash(f"{getattr(form, field).label.text}: {error}", "danger")
    return redirect(url_for("auth.users"))


@bp.post("/users/<int:user_id>/toggle-active")
@login_required
@admin_required
def toggle_active(user_id: int):
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id:
        flash("Você não pode desativar a própria conta.", "warning")
        return redirect(url_for("auth.users"))
    user.is_active_flag = not user.is_active_flag
    db.session.commit()
    flash(f"{user.username} {'ativado' if user.is_active_flag else 'desativado'}.", "info")
    return redirect(url_for("auth.users"))


@bp.post("/users/<int:user_id>/toggle-admin")
@login_required
@admin_required
def toggle_admin(user_id: int):
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id:
        flash("Você não pode remover o próprio acesso de administrador.", "warning")
        return redirect(url_for("auth.users"))
    user.is_admin = not user.is_admin
    db.session.commit()
    flash(f"Permissões de {user.username} atualizadas.", "info")
    return redirect(url_for("auth.users"))


@bp.route("/users/<int:user_id>/reset-password", methods=["GET", "POST"])
@login_required
@admin_required
def reset_password(user_id: int):
    user = db.session.get(User, user_id) or abort(404)
    form = AdminResetPasswordForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        db.session.commit()
        flash(f"Senha de {user.username} redefinida.", "success")
        return redirect(url_for("auth.users"))
    return render_template("auth/reset_password.html", form=form, user=user)


@bp.post("/users/<int:user_id>/delete")
@login_required
@admin_required
def delete_user(user_id: int):
    user = db.session.get(User, user_id) or abort(404)
    if user.id == current_user.id:
        flash("Você não pode excluir a própria conta.", "warning")
        return redirect(url_for("auth.users"))
    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f"Usuário {username} e suas tarefas foram removidos.", "info")
    return redirect(url_for("auth.users"))
