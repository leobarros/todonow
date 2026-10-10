"""WTForms definitions for authentication and profile screens."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import (
    DataRequired,
    EqualTo,
    Length,
    Optional,
    Regexp,
    ValidationError,
)

from ..extensions import db
from ..models import User
from ..utils import is_valid_email

USERNAME_RE = r"^[A-Za-z0-9._-]+$"


class OptionalEmail:
    """E-mail opcional que também aceita domínios internos de homelab.

    O validador ``Email`` do WTForms recusa ``casa.local``/``nas``, o que não faz
    sentido aqui: o campo é informativo e o app nunca envia e-mails.
    """

    def __init__(self, message: str = "E-mail inválido."):
        self.message = message

    def __call__(self, form, field):
        if not is_valid_email(field.data):
            raise ValidationError(self.message)


def _current_user():
    from flask_login import current_user

    return current_user if getattr(current_user, "is_authenticated", False) else None


class LoginForm(FlaskForm):
    username = StringField("Usuário", validators=[DataRequired(message="Informe o usuário."), Length(max=64)])
    password = PasswordField("Senha", validators=[DataRequired(message="Informe a senha.")])
    remember = BooleanField("Manter conectado")
    submit = SubmitField("Entrar")


class RegistrationForm(FlaskForm):
    username = StringField(
        "Usuário",
        validators=[
            DataRequired(message="Informe o usuário."),
            Length(min=3, max=64, message="Use de 3 a 64 caracteres."),
            Regexp(USERNAME_RE, message="Use apenas letras, números, ponto, hífen ou underscore."),
        ],
    )
    display_name = StringField("Nome de exibição", validators=[Optional(), Length(max=120)])
    email = StringField("E-mail (opcional)", validators=[Optional(), OptionalEmail(), Length(max=255)])
    password = PasswordField("Senha", validators=[DataRequired(message="Informe a senha.")])
    password2 = PasswordField(
        "Confirme a senha",
        validators=[DataRequired(message="Confirme a senha."), EqualTo("password", message="As senhas não conferem.")],
    )
    submit = SubmitField("Criar conta")

    def validate_username(self, field):
        existing = User.query.filter(db.func.lower(User.username) == field.data.strip().lower()).first()
        if existing:
            raise ValidationError("Este usuário já existe.")

    def validate_email(self, field):
        if field.data:
            existing = User.query.filter(db.func.lower(User.email) == field.data.strip().lower()).first()
            if existing:
                raise ValidationError("Este e-mail já está em uso.")

    def validate_password(self, field):
        from flask import current_app

        minimum = current_app.config.get("MIN_PASSWORD_LENGTH", 8)
        if len(field.data or "") < minimum:
            raise ValidationError(f"A senha deve ter ao menos {minimum} caracteres.")


class ProfileForm(FlaskForm):
    display_name = StringField("Nome de exibição", validators=[Optional(), Length(max=120)])
    email = StringField("E-mail", validators=[Optional(), OptionalEmail(), Length(max=255)])
    theme = SelectField(
        "Tema",
        choices=[("light", "Claro"), ("dark", "Escuro"), ("auto", "Automático (sistema)")],
        validators=[DataRequired()],
    )
    submit = SubmitField("Salvar perfil")

    def validate_email(self, field):
        current = _current_user()
        if not field.data or current is None:
            return
        existing = User.query.filter(db.func.lower(User.email) == field.data.strip().lower()).first()
        if existing and existing.id != current.id:
            raise ValidationError("Este e-mail já está em uso.")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Senha atual", validators=[DataRequired(message="Informe a senha atual.")])
    password = PasswordField("Nova senha", validators=[DataRequired(message="Informe a nova senha.")])
    password2 = PasswordField(
        "Confirme a nova senha",
        validators=[
            DataRequired(message="Confirme a nova senha."),
            EqualTo("password", message="As senhas não conferem."),
        ],
    )
    submit = SubmitField("Alterar senha")

    def validate_current_password(self, field):
        current = _current_user()
        if current is not None and not current.check_password(field.data):
            raise ValidationError("Senha atual incorreta.")

    def validate_password(self, field):
        from flask import current_app

        minimum = current_app.config.get("MIN_PASSWORD_LENGTH", 8)
        if len(field.data or "") < minimum:
            raise ValidationError(f"A senha deve ter ao menos {minimum} caracteres.")


class AdminUserForm(FlaskForm):
    username = StringField(
        "Usuário",
        validators=[
            DataRequired(message="Informe o usuário."),
            Length(min=3, max=64),
            Regexp(USERNAME_RE, message="Use apenas letras, números, ponto, hífen ou underscore."),
        ],
    )
    display_name = StringField("Nome de exibição", validators=[Optional(), Length(max=120)])
    email = StringField("E-mail (opcional)", validators=[Optional(), OptionalEmail(), Length(max=255)])
    password = PasswordField("Senha inicial", validators=[DataRequired(message="Informe a senha.")])
    is_admin = BooleanField("Administrador")
    submit = SubmitField("Criar usuário")

    def validate_username(self, field):
        if User.query.filter(db.func.lower(User.username) == field.data.strip().lower()).first():
            raise ValidationError("Este usuário já existe.")

    def validate_password(self, field):
        from flask import current_app

        minimum = current_app.config.get("MIN_PASSWORD_LENGTH", 8)
        if len(field.data or "") < minimum:
            raise ValidationError(f"A senha deve ter ao menos {minimum} caracteres.")


class AdminResetPasswordForm(FlaskForm):
    password = PasswordField("Nova senha", validators=[DataRequired(message="Informe a nova senha.")])
    password2 = PasswordField(
        "Confirme a nova senha",
        validators=[DataRequired(message="Confirme a senha."), EqualTo("password", message="As senhas não conferem.")],
    )
    submit = SubmitField("Redefinir senha")

    def validate_password(self, field):
        from flask import current_app

        minimum = current_app.config.get("MIN_PASSWORD_LENGTH", 8)
        if len(field.data or "") < minimum:
            raise ValidationError(f"A senha deve ter ao menos {minimum} caracteres.")
