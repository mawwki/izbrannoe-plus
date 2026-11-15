"""OpenRouter chat-клиент «Избранное+»: ответы по заметкам (RAG) и дайджесты.

Модель «нахождения инфы» — openrouter/free (бесплатный авто-роутер OpenRouter).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .config import Config

log = logging.getLogger("favorites.llm")

API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(RuntimeError):
    pass


async def chat(cfg: Config, messages: list[dict[str, str]], max_tokens: int = 700,
               timeout: float = 60.0, retries: int = 3) -> str:
    """Chat completion с retry на 429/5xx/сеть. Возвращает текст ответа."""
    headers = {
        "Authorization": f"Bearer {cfg.openrouter_key}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {"model": cfg.answer_model, "messages": messages, "max_tokens": max_tokens}
    last_err: Exception | None = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        for attempt in range(retries):
            try:
                r = await client.post(API_URL, headers=headers, json=body)
                if r.status_code in (429, 500, 502, 503, 504):
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                r.raise_for_status()
                data = r.json()
                msg = ((data.get("choices") or [{}])[0].get("message") or {})
                content = msg.get("content")
                if isinstance(content, list):  # некоторые модели возвращают список частей
                    content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                content = (content or "").strip()
                if content:
                    return content
                raise OpenRouterError(f"пустой ответ модели: {data.get('model')}")
            except (httpx.HTTPError, OpenRouterError) as e:  # noqa: PERF203
                last_err = e
                await asyncio.sleep(1.0 * (attempt + 1))
        raise OpenRouterError(str(last_err) if last_err else "неизвестная ошибка OpenRouter")


def _context_from_notes(notes: list[dict[str, Any]], cfg: Config) -> str:
    import html as h

    parts = []
    for i, n in enumerate(notes, 1):
        title = cfg.category_title(n.get("category") or "other")
        when = f", дата: {n['when_date']}" if n.get("when_date") else ""
        text = (n.get("text") or "").strip()
        if len(text) > 600:
            text = text[:600] + "…"
        created = (n.get("created_at") or "")[:16].replace("T", " ")
        parts.append(f"[{i}] ({title}{when}, сохранено {created}): {h.escape(text)}")
    return "\n".join(parts)


SYSTEM_ANSWER = (
    "Ты — «Избранное+», отвечаешь на вопросы по личным заметкам пользователя в Telegram.\n"
    "ЖЁСТКИЕ ПРАВИЛА:\n"
    "1. Сначала ВЫВОД — конкретные данные из заметок (ключи, токены, пароли, даты, суммы, "
    "названия, ссылки), каждый элемент на отдельной строке. Ключи/токены/пароли выводи "
    "ПОЛНОСТЬЮ, символ в символ, без сокращений и «…», каждый оберни так: ⟦текст⟧ "
    "(в сообщении это станет копируемой строкой).\n"
    "2. Затем максимум 1-2 короткие строки пояснения.\n"
    "3. Опирайся только на заметки; ссылки на источники — номерами в квадратных скобках "
    "([1], [2]), номера соответствуют списку заметок в вопросе.\n"
    "4. Никаких «могу подсказать», «если нужно», никаких вступлений и воды.\n"
    "5. Не используй Markdown (**, *) и HTML-теги."
)


def escape_answer(text: str) -> str:
    """HTML-эскейп ответа модели; ⟦…⟧ превращаем в копируемые <code>-поля."""
    import html as h

    out = h.escape(text, quote=False)
    return out.replace("⟦", "<code>").replace("⟧", "</code>")


def dedupe_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Убирает заметки с одинаковым текстом (пользователь сохраняет ключ дважды)."""
    import re as _re

    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for n in notes:
        key = _re.sub(r"\s+", " ", (n.get("text") or "").strip().lower())
        if key and key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


def format_sources(notes: list[dict[str, Any]], cfg: Config, max_chars: int = 300) -> str:
    """Компактный блок источников: номер = номер цитаты в ответе, текст целиком до max_chars."""
    import html as h

    lines = []
    for i, n in enumerate(notes, 1):
        title = cfg.category_title(n.get("category") or "other")
        txt = (n.get("text") or "").strip()
        if len(txt) > max_chars:
            txt = txt[:max_chars] + "…"
        lines.append(f"[{i}] #{n['id']} · {title} — {h.escape(txt)}")
    return "\n".join(lines)


async def answer_question(cfg: Config, question: str, notes: list[dict[str, Any]]) -> str:
    """RAG: вопрос + отобранные заметки → структурированный ответ с копируемыми данными.

    notes уже отобраны вызывающим (лексика/добор/LLM-выбор), дубликаты убраны.
    """
    if not notes:
        return ("В заметках пока ничего релевантного не нашлось. "
                "Сохраняйте больше — и я найду всё, что вы отложили!")
    context = _context_from_notes(notes[:25], cfg)
    messages = [
        {"role": "system", "content": SYSTEM_ANSWER},
        {"role": "user", "content": f"Заметки пользователя:\n{context}\n\nВопрос: {question}"},
    ]
    raw = await chat(cfg, messages, max_tokens=700)
    return escape_answer(raw)


SYSTEM_DIGEST = (
    "Ты — «Избранное+». Составь короткий дайджест сохранённого за день по-русски: "
    "1-2 предложения-обобщение, затем 3-5 пунктов списка самого важного (по одной строке, с эмодзи). "
    "Без преамбулы и без Markdown-заголовков."
)


async def make_digest(cfg: Config, notes: list[dict[str, Any]], day_label: str) -> str:
    if not notes:
        return f"📭 За {day_label} ничего не сохранено — закидывайте заметки, я всё разложу."
    context = _context_from_notes(notes[:25], cfg)
    messages = [
        {"role": "system", "content": SYSTEM_DIGEST},
        {"role": "user", "content": f"День: {day_label}\nСохранённые заметки:\n{context}"},
    ]
    return await chat(cfg, messages, max_tokens=400)


async def ping(cfg: Config) -> tuple[bool, str]:
    """Проверка ключа OpenRouter из GUI: маленький запрос."""
    try:
        out = await asyncio.wait_for(chat(cfg, [{"role": "user", "content": "Ответь одним словом: ок"}],
                                         max_tokens=2000, timeout=45.0, retries=1), timeout=50.0)
        return True, f"OK: {out[:60]}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]
