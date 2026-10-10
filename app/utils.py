"""Date, formatting and BUJO helper utilities.

The app deliberately avoids depending on system locales: Brazilian Portuguese
labels are defined here so formatting is identical inside any container.
"""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# --------------------------------------------------------------------------
# Task vocabularies (shared by models, forms, templates and backup/restore)
# --------------------------------------------------------------------------

KIND_TASK = "task"
KIND_EVENT = "event"
KIND_NOTE = "note"
TASK_KINDS = (KIND_TASK, KIND_EVENT, KIND_NOTE)

KIND_LABELS = {
    KIND_TASK: "Tarefa",
    KIND_EVENT: "Evento",
    KIND_NOTE: "Nota",
}

# BUJO rapid-logging symbols
KIND_SYMBOLS = {
    KIND_TASK: "•",
    KIND_EVENT: "○",
    KIND_NOTE: "–",
}

KIND_ICONS = {
    KIND_TASK: "bi-check2-square",
    KIND_EVENT: "bi-calendar-event",
    KIND_NOTE: "bi-journal-text",
}

STATUS_OPEN = "open"
STATUS_DONE = "done"
STATUS_MIGRATED = "migrated"
STATUS_CANCELLED = "cancelled"
TASK_STATUSES = (STATUS_OPEN, STATUS_DONE, STATUS_MIGRATED, STATUS_CANCELLED)

STATUS_LABELS = {
    STATUS_OPEN: "Aberta",
    STATUS_DONE: "Concluída",
    STATUS_MIGRATED: "Migrada",
    STATUS_CANCELLED: "Cancelada",
}

STATUS_SYMBOLS = {
    STATUS_OPEN: "",
    STATUS_DONE: "✕",
    STATUS_MIGRATED: ">",
    STATUS_CANCELLED: "~",
}

PRIORITY_NONE = "none"
PRIORITY_LOW = "low"
PRIORITY_MEDIUM = "medium"
PRIORITY_HIGH = "high"
TASK_PRIORITIES = (PRIORITY_NONE, PRIORITY_LOW, PRIORITY_MEDIUM, PRIORITY_HIGH)

PRIORITY_LABELS = {
    PRIORITY_NONE: "Normal",
    PRIORITY_LOW: "Baixa",
    PRIORITY_MEDIUM: "Média",
    PRIORITY_HIGH: "Alta",
}

# BUJO signifiers: * = priority, ! = important, ☆ = inspiring/idea
PRIORITY_SIGNIFIERS = {
    PRIORITY_NONE: "",
    PRIORITY_LOW: "",
    PRIORITY_MEDIUM: "!",
    PRIORITY_HIGH: "*",
}

PRIORITY_BADGES = {
    PRIORITY_NONE: "secondary",
    PRIORITY_LOW: "info",
    PRIORITY_MEDIUM: "warning",
    PRIORITY_HIGH: "danger",
}

WEEKDAY_SHORT_PT = ("Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom")
WEEKDAY_LONG_PT = (
    "Segunda-feira",
    "Terça-feira",
    "Quarta-feira",
    "Quinta-feira",
    "Sexta-feira",
    "Sábado",
    "Domingo",
)
MONTH_PT = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------------
# Timezone handling
# --------------------------------------------------------------------------


def get_timezone(name: str = "America/Sao_Paulo") -> ZoneInfo:
    """Return a ZoneInfo, falling back to UTC when tzdata lacks the name."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def now_local(tz: ZoneInfo | None = None) -> datetime:
    """Naive datetime in the configured timezone (all DB columns are naive)."""
    tz = tz or get_timezone()
    return datetime.now(tz).replace(tzinfo=None)


def today_local(tz: ZoneInfo | None = None) -> date:
    return now_local(tz).date()


# --------------------------------------------------------------------------
# Date parsing / formatting
# --------------------------------------------------------------------------


def parse_date(value, default: date | None = None) -> date | None:
    """Best-effort conversion of user/JSON input into a ``date``."""
    if value is None or value == "":
        return default
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value, tz=UTC).date()
    text = str(value).strip()
    if not text:
        return default
    if DATE_RE.match(text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            return default
    # Accept full ISO timestamps too.
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return default


def parse_datetime(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def format_date_pt(value: date | None) -> str:
    """``10/10/2025`` — the format Brazilians expect in dense views."""
    if value is None:
        return "—"
    return value.strftime("%d/%m/%Y")


def format_date_long_pt(value: date | None) -> str:
    """``sexta-feira, 10 de outubro de 2025``."""
    if value is None:
        return "—"
    weekday = WEEKDAY_LONG_PT[value.weekday()]
    return f"{weekday}, {value.day} de {MONTH_PT[value.month - 1]} de {value.year}"


def format_date_short_pt(value: date | None) -> str:
    """``10 out``."""
    if value is None:
        return "—"
    return f"{value.day:02d} {MONTH_PT[value.month - 1][:3]}"


def month_label_pt(year: int, month: int) -> str:
    return f"{MONTH_PT[month - 1].capitalize()} de {year}"


def humanize_relative(value: date | None, reference: date | None = None) -> str:
    """``hoje`` / ``amanhã`` / ``ontem`` / ``em 3 dias`` / ``há 2 dias``."""
    if value is None:
        return "—"
    reference = reference or today_local()
    delta = (value - reference).days
    if delta == 0:
        return "hoje"
    if delta == 1:
        return "amanhã"
    if delta == -1:
        return "ontem"
    if delta > 1:
        return f"em {delta} dias"
    return f"há {abs(delta)} dias"


# --------------------------------------------------------------------------
# Calendar helpers
# --------------------------------------------------------------------------


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """First and last day of the month."""
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def add_months(value: date, months: int) -> date:
    """Shift a date by *months*, clamping the day to the target month."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def month_matrix(year: int, month: int, week_start: int = 6) -> list[list[date]]:
    """Weeks (each 7 days) covering the month, padded with adjacent days."""
    first, last = month_bounds(year, month)
    offset = (first.weekday() - week_start) % 7
    start = first - timedelta(days=offset)
    # Number of weeks needed so the whole month fits.
    total = offset + last.day
    weeks = (total + 6) // 7
    return [[start + timedelta(days=week * 7 + day) for day in range(7)] for week in range(weeks)]


def week_days(value: date, week_start: int = 6) -> list[date]:
    start = value - timedelta(days=(value.weekday() - week_start) % 7)
    return [start + timedelta(days=i) for i in range(7)]


def weekday_labels_pt(week_start: int = 6) -> list[str]:
    return [WEEKDAY_SHORT_PT[(week_start + i) % 7] for i in range(7)]


# --------------------------------------------------------------------------
# Misc
# --------------------------------------------------------------------------


def slugify(value: str) -> str:
    import unicodedata

    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()


def truncate(value: str | None, length: int = 60) -> str:
    if not value:
        return ""
    return value if len(value) <= length else value[: length - 1] + "…"


# --------------------------------------------------------------------------
# E-mail (opcional) — tolerante com domínios internos de homelab
# --------------------------------------------------------------------------

try:  # pragma: no cover - dependência declarada em requirements.txt
    from email_validator import EmailNotValidError as _EmailNotValidError
    from email_validator import validate_email as _validate_email
except ImportError:  # pragma: no cover
    _validate_email = None

    class _EmailNotValidError(Exception):
        pass


# Domínios internos são bem-vindos aqui: "casa.local", "homelab.local", "nas".
_INTERNAL_EMAIL_RE = re.compile(
    r"^(?P<local>[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64})"
    r"@(?P<domain>[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*)$"
)


def is_wellformed_email(value: str) -> bool:
    """Checagem sintática tolerante: ``local@dominio`` sem espaços.

    É o plano B de :func:`is_valid_email` e existe para aceitar endereços de
    domínios internos (``eu@casa.local``, ``admin@homelab.local``, ``ti@nas``)
    que validadores rígidos recusam por serem de uso especial ou sem ponto.
    """
    value = (value or "").strip()
    if not value or len(value) > 254 or value.count("@") != 1:
        return False

    match = _INTERNAL_EMAIL_RE.match(value)
    if not match:
        return False

    local = match.group("local")
    domain = match.group("domain")
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    if domain.startswith(".") or domain.endswith(".") or ".." in domain:
        return False
    # Domínio de um único caractere costuma ser erro de digitação.
    return len(domain) >= 2


def is_valid_email(value: str) -> bool:
    """Valida e-mail aceitando também domínios internos (``.local``, ``nas``).

    O campo é opcional e apenas informativo — o app nunca envia e-mails — então
    a checagem rígida da biblioteca não pode barrar um endereço da rede local.
    """
    value = (value or "").strip()
    if not value:
        return True
    if _validate_email is not None:
        try:
            _validate_email(value, check_deliverability=False)
            return True
        except _EmailNotValidError:
            pass
    return is_wellformed_email(value)
