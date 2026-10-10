"""Forms for backup export/import."""

from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import RadioField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import Length, Optional, ValidationError


class ExportForm(FlaskForm):
    scope = SelectField(
        "Escopo",
        choices=[("user", "Minhas tarefas"), ("full", "Instância completa (admin)")],
        default="user",
    )
    submit = SubmitField("Exportar JSON")


class ImportForm(FlaskForm):
    file = FileField(
        "Arquivo de backup (.json)",
        validators=[FileAllowed(["json"], "Envie um arquivo .json"), Optional()],
    )
    text = TextAreaField(
        "…ou cole o conteúdo JSON",
        validators=[Optional(), Length(max=8_000_000)],
        render_kw={"rows": 6, "placeholder": '{"format": "todonow.backup", ...}'},
    )
    mode = RadioField(
        "Modo de restauração",
        choices=[
            ("merge", "Mesclar — atualiza pelo UUID e adiciona o que falta (recomendado)"),
            ("replace", "Substituir — apaga as tarefas atuais antes de restaurar"),
        ],
        default="merge",
    )
    submit = SubmitField("Restaurar")

    def validate(self, extra_validators=None):
        if not super().validate(extra_validators):
            return False
        if not (self.file.data and getattr(self.file.data, "filename", "")) and not (self.text.data or "").strip():
            self.text.errors.append("Envie um arquivo ou cole o JSON do backup.")
            return False
        return True


class SnapshotForm(FlaskForm):
    scope = SelectField(
        "Escopo do snapshot",
        choices=[("user", "Minhas tarefas"), ("full", "Instância completa (admin)")],
        default="user",
    )
    label = StringField("Rótulo (opcional)", validators=[Optional(), Length(max=60)])
    submit = SubmitField("Criar snapshot no servidor")

    def validate_label(self, field):
        import re

        if field.data and not re.fullmatch(r"[A-Za-z0-9 _-]+", field.data.strip()):
            raise ValidationError("Use apenas letras, números, espaços, hífen e underscore.")


class RestoreSnapshotForm(FlaskForm):
    mode = RadioField(
        "Modo",
        choices=[("merge", "Mesclar"), ("replace", "Substituir")],
        default="merge",
    )
    submit = SubmitField("Restaurar snapshot")
