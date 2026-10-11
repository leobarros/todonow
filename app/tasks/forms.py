"""WTForms definitions for tasks."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import DateField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from ..utils import (
    KIND_LABELS,
    PRIORITY_LABELS,
    STATUS_LABELS,
    TASK_KINDS,
    TASK_PRIORITIES,
    TASK_STATUSES,
    today_local,
)


def _choices(values, labels):
    return [(value, labels[value]) for value in values]


class TaskForm(FlaskForm):
    """Full create/edit form (used by both screens)."""

    title = StringField(
        "Título",
        validators=[DataRequired(message="Descreva a tarefa."), Length(max=255)],
        render_kw={"autofocus": True, "placeholder": "O que precisa ser feito?"},
    )
    notes = TextAreaField(
        "Anotações",
        validators=[Optional(), Length(max=20000, message="Use no máximo 20.000 caracteres.")],
        render_kw={
            "rows": 10,
            "placeholder": "Escreva o que aconteceu… Markdown é bem-vindo:\n\n"
            "- [ ] atividade pendente\n- [x] atividade feita\n\n**destaque**, *ênfase*, `código`",
        },
    )
    kind = SelectField("Tipo", choices=_choices(TASK_KINDS, KIND_LABELS), default="task")
    status = SelectField("Situação", choices=_choices(TASK_STATUSES, STATUS_LABELS), default="open")
    priority = SelectField("Prioridade", choices=_choices(TASK_PRIORITIES, PRIORITY_LABELS), default="none")
    scheduled_date = DateField(
        "Começa em",
        validators=[DataRequired(message="Informe a data de início.")],
        default=today_local,
        format="%Y-%m-%d",
    )
    due_date = DateField(
        "Prazo (opcional)",
        validators=[Optional()],
        format="%Y-%m-%d",
    )
    submit = SubmitField("Salvar")

    def validate_due_date(self, field):
        if field.data and self.scheduled_date.data and field.data < self.scheduled_date.data:
            raise ValidationError("O prazo não pode ser anterior à data de início.")


class QuickTaskForm(FlaskForm):
    """Compact form used for the inline "add" box on dashboards."""

    title = StringField(
        "Nova tarefa",
        validators=[DataRequired(message="Descreva a tarefa."), Length(max=255)],
        render_kw={"placeholder": "Adicionar tarefa…", "autocomplete": "off"},
    )
    kind = SelectField("Tipo", choices=_choices(TASK_KINDS, KIND_LABELS), default="task")
    priority = SelectField("Prioridade", choices=_choices(TASK_PRIORITIES, PRIORITY_LABELS), default="none")
    scheduled_date = DateField("Data", validators=[Optional()], format="%Y-%m-%d", default=today_local)
    submit = SubmitField("Adicionar")
