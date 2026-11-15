"""Утилиты «Избранное+»: автотеги, извлечение дат, форматирование карточек."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
_MONEY_RE = re.compile(r"(\d[\d\s.,]*\s?(?:₽|руб|р\.|€|\$|usd|eur)|\b(?:оплат|заплат|чек)\w*)", re.IGNORECASE)
_TIME_RE = re.compile(r"\b(\d{1,2}[:.]\d{2})\b")
_PHONE_RE = re.compile(r"(?:\+7|8)[\s\-()]?\d{3}[\s\-()]?\d{3}[\s\-()]?\d{2}[\s\-()]?\d{2}")
_DATE_NUM_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?\b")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# Ключи/токены/пароли: явные префиксы сервисов и «похоже на случайный токен»
_SECRET_PATTERNS = (
    re.compile(r"\bsk-(?:or-v1-|proj-|ant-)?[A-Za-z0-9_-]{12,}"),          # openrouter/sk-*
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),                            # github
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                    # aws
    re.compile(r"\bxox[bpars]-[A-Za-z0-9-]{10,}"),                          # slack
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),             # JWT
    re.compile(r"\b[0-9a-f]{32,64}\b", re.IGNORECASE),                      # hex
    re.compile(r"\b[A-Za-z0-9_-]{24,}\b"),                                  # длинный случайный токен
)


def looks_like_secret(text: str) -> bool:
    """Похоже на API-ключ/токен/пароль или содержит его."""
    return any(p.search(text) for p in _SECRET_PATTERNS)


SECRET_COMBINED_RE = re.compile("|".join(p.pattern for p in _SECRET_PATTERNS))

# Транслитерация RU→LAT: «маргут» ≈ "MargyT" — поиск с опечатками/раскладкой
_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
})


def translit(text: str) -> str:
    return text.translate(_TRANSLIT)


def wrap_secrets(text: str) -> str:
    """Оборачивает найденные ключи/токены в ⟦…⟧ (далее escape_answer → <code>)."""
    return SECRET_COMBINED_RE.sub(lambda m: f"⟦{m.group(0)}⟧", text)


def escape_with_code(text: str) -> str:
    """HTML-эскейп + маркеры ⟦…⟧ → копируемые <code>-поля (для карточек)."""
    import html as h

    out = h.escape(text, quote=False)
    return out.replace("⟦", "<code>").replace("⟧", "</code>")


def first_time_in_text(text: str) -> str | None:
    """Первое время в тексте («в 19:00») → 'HH:MM' для напоминаний."""
    m = _TIME_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(".", ":")
    try:
        hh, mm = raw.split(":")
        hh, mm = int(hh), int(mm)
    except ValueError:
        return None
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return f"{hh:02d}:{mm:02d}"
    return None


def extract_url(text: str) -> str | None:
    m = _URL_RE.search(text)
    return m.group(0).rstrip(").,") if m else None


def extract_tags(text: str, category: str = "", category_title: str = "") -> list[str]:
    tags: list[str] = []
    if looks_like_secret(text):
        tags.append("🔑")
    if _URL_RE.search(text):
        tags.append("url")
    if _MONEY_RE.search(text):
        tags.append("💰")
    if _TIME_RE.search(text):
        tags.append("⏰")
    if _PHONE_RE.search(text):
        tags.append("☎️")
    if _EMAIL_RE.search(text):
        tags.append("✉️")
    if category == "task":
        tags.append("todo")
    if category_title:
        slug = category_title.strip().lower().replace(" ", "-")
        if slug and slug not in tags:
            tags.append(slug)
    return tags

_WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "среду": 2, "среда": 2, "четверг": 3,
    "пятницу": 4, "пятница": 4, "субботу": 5, "суббота": 5, "воскресенье": 6,
}
_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

def extract_when(text: str, today: date | None = None) -> str | None:
    """Извлекает дату из текста (числа, «5 ноября», «завтра», «в пятницу») → ISO."""
    base = today or date.today()
    low = text.lower()

    # версии вида 2.3.0 / v1.2 — не даты
    if re.search(r"\b(?:v)?\d+\.\d+\.\d+\b", low):
        return None

    m = _DATE_NUM_RE.search(low)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y = base.year
        if m.group(3):
            y = int(m.group(3))
            y += 2000 if y < 100 else 0
        try:
            cand = date(y, mo, d)
        except ValueError:
            cand = None
        if cand:
            return cand.isoformat()

    for name, month in _MONTHS.items():
        if name in low:
            m2 = re.search(r"(\d{1,2})\s+" + name, low)
            day = int(m2.group(1)) if m2 else base.day
            try:
                return date(base.year, month, day).isoformat()
            except ValueError:
                pass

    if "послезавтра" in low:
        return (base + timedelta(days=2)).isoformat()
    if "завтра" in low:
        return (base + timedelta(days=1)).isoformat()
    if "сегодня" in low:
        return base.isoformat()

    for name, wd in _WEEKDAYS.items():
        if f"в {name}" in low or f"во {name}" in low:
            delta = (wd - base.weekday()) % 7
            if delta == 0:
                delta = 7
            return (base + timedelta(days=delta)).isoformat()

    if _TIME_RE.search(low) and any(k in low for k in ("встреч", "кино", "звонок", "созвон", "в ")):
        return base.isoformat()
    return None


def fmt_note_card(cfg, note: dict, with_actions: bool = True) -> str:
    """HTML-карточка заметки для reply бота."""
    import html as h

    title = cfg.category_title(note.get("category") or "other")
    conf = note.get("confidence") or 0
    src_badge = {"local": "локальный классификатор", "openrouter": "OpenRouter",
                 "user": "ваша правка", "llm": "ИИ"}.get(note.get("source") or "", "")
    tags = " ".join(f"#{t}" for t in (note.get("tags") or []))
    created = (note.get("created_at") or "")[:16].replace("T", " ")
    kind_emoji = {"photo": "🖼", "voice": "🎙", "video": "🎬", "audio": "🎵",
                  "document": "📄", "sticker": "🎭"}.get(note.get("kind") or "text", "")
    raw = (note.get("text") or "").strip()
    if len(raw) > 700:
        raw = raw[:700] + "…"
    text = escape_with_code(wrap_secrets(raw))

    lines = [f"<b>{title}</b> <i>({int(conf * 100)}%, {h.escape(src_badge)})</i>"]
    if kind_emoji and note.get("kind") != "text":
        lines.append(f"{kind_emoji} медиа сохранено")
    if note.get("when_date"):
        lines.append(f"📅 дата: {note['when_date']}")
    if tags:
        lines.append(h.escape(tags))
    if text:
        lines.append(text)
    lines.append(f"<i>{h.escape(created)}</i>")
    return "\n".join(lines)


def truncate(s: str, n: int = 80) -> str:
    s = (s or "").strip().replace("\n", " ⏎ ")
    return s if len(s) <= n else s[: n - 1] + "…"
