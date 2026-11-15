"""Telegram-бот «Избранное+» — умное «Сохранённое».

Всё, что пользователь пересылает боту, автоматически классифицируется, тегируется,
датируется и попадает в локальную базу. Поиск — гибридный (BM25 + TF-IDF),
вопросы по заметкам — через бесплатную модель openrouter/free.
"""
from __future__ import annotations

import asyncio
import html as h_mod
import json
import logging
import logging.handlers
import re
import shutil
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, InlineQuery, InlineQueryResultArticle,
                           InputTextMessageContent, Message)

from .classifier import HybridClassifier
from .config import Config, load_config
from .llm import answer_question, dedupe_notes, escape_answer, make_digest
from .search import hybrid_search, llm_pick_relevant
from .storage import Storage
from .utils import (extract_tags, extract_url, extract_when, first_time_in_text,
                    fmt_note_card, truncate, wrap_secrets)
from .config import DB_PATH, LOG_DIR

log = logging.getLogger("favorites.bot")

router = Router()

PAGE_SIZE = 5

HELP_TEXT = (
    "<b>Избранное+ — ваше умное «Сохранённое»</b>\n\n"
    "Пересылайте мне <b>любые сообщения</b> — текст, фото, голосовые, документы, ссылки. "
    "Я сам определю категорию, проставлю теги и дату, и всё найдётся по первому запросу.\n\n"
    "<b>Где угодно:</b> в любом чате напишите <code>@favorites_plus_bot запрос</code> — "
    "подсуну заметку прямо в сообщение.\n\n"
    "<b>Команды:</b>\n"
    "🔎 <code>/find запрос</code> — гибридный поиск (понимает опечатки и транслит)\n"
    "🤖 <code>/ask вопрос</code> — ответ по смыслу заметок (ИИ), ключи выводит целиком\n"
    "🗂 <code>/list</code> — browse по категориям\n"
    "📊 <code>/stats</code> — статистика\n"
    "📅 <code>/upcoming</code> — заметки с датами\n"
    "⏰ напоминания: заметка с датой на сегодня придёт утром (или к времени из текста)\n"
    "📦 <code>/export</code> — выгрузить все заметки в JSON\n"
    "🌅 <code>/digest</code> — дайджест за сегодня (ИИ)\n"
    "🧹 <code>/clear</code> — очистить всё\n\n"
    "Если я неверно угадал категорию — нажмите <b>«Не то»</b> под карточкой: "
    "я запомню правку и <b>дообучусь</b> на ней."
)


def _extract_content(msg: Message) -> tuple[str, str, str | None]:
    """(kind, searchable_text, file_id)."""
    if msg.text:
        return "text", msg.text, None
    if msg.photo:
        return "photo", (msg.caption or "фото без подписи"), msg.photo[-1].file_id
    if msg.voice:
        return "voice", (msg.caption or "голосовое сообщение"), msg.voice.file_id
    if msg.video:
        return "video", (msg.caption or "видео"), msg.video.file_id
    if msg.video_note:
        return "video", "видео-кружок", msg.video_note.file_id
    if msg.audio:
        return "audio", (msg.caption or "аудио"), msg.audio.file_id
    if msg.document:
        name = msg.document.file_name or "документ"
        return "document", (msg.caption or f"документ {name}"), msg.document.file_id
    if msg.sticker:
        return "sticker", f"стикер {msg.sticker.emoji or ''}", msg.sticker.file_id
    return "text", (msg.text or msg.caption or ""), None


def _media_kb(note_id: int, kind: str, file_id: str | None) -> list[InlineKeyboardButton] | None:
    if kind in ("photo", "video", "audio", "document") and file_id:
        return [InlineKeyboardButton(text="📎 Открыть медиа", callback_data=f"media:{note_id}")]
    return None


def _note_kb(note_id: int, kind: str, file_id: str | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🏷 Не то", callback_data=f"recat:{note_id}"),
         InlineKeyboardButton(text="🗑", callback_data=f"del:{note_id}"),
         InlineKeyboardButton(text="🔍 Похожее", callback_data=f"similar:{note_id}")],
    ]
    media_btn = _media_kb(note_id, kind, file_id)
    if media_btn:
        rows.append(media_btn)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _category_titles(cfg: Config) -> dict[str, str]:
    return {c["id"]: c["title"] for c in cfg.categories}


async def _store_and_reply(bot: Bot, cfg: Config, storage: Storage,
                           classifier: HybridClassifier, chat_id: int, uid: int,
                           kind: str, text: str, file_id: str | None,
                           force: bool = False) -> None:
    """Пайплайн сохранения: дедуп → классификация → карточка (или выбор категории)."""
    if not force:
        existing = storage.find_same_text(uid, text)
        if existing and kind == "text":
            key = f"{uid}:{abs(hash(text)) % 10**12}"
            bot_ctx.setdefault("dup_cache", {})[key] = (uid, kind, text, file_id)
            cache = bot_ctx["dup_cache"]
            if len(cache) > 50:
                for k in list(cache)[:-50]:
                    cache.pop(k, None)
            log.info("duplicate of #%s detected", existing["id"])
            await bot.send_message(
                chat_id,
                "♻️ <b>Это уже сохранено</b> — точная копия есть в базе:\n\n"
                + fmt_note_card(cfg, existing)
                + "\n\nСохранить ещё одну копию?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="♻️ Всё равно сохранить",
                                         callback_data=f"dup:{key}"),
                    InlineKeyboardButton(text="❌ Не надо", callback_data="noop"),
                ]]),
                disable_web_page_preview=True)
            return

    url = extract_url(text)
    when = extract_when(text)
    uncertain = False

    # медиа без подписи — сразу в «Медиа» без опроса моделей
    _default_media_texts = ("фото без подписи", "видео", "аудио", "видео-кружок",
                            "голосовое сообщение")
    if kind in ("sticker",) or (kind in ("photo", "video", "audio", "voice", "document")
                                and text in _default_media_texts):
        result_cat, conf, source = "media", 0.95, "local"
    else:
        result = await classifier.classify(text)
        result_cat, conf, source = result.category, result.confidence, result.source
        uncertain = result.uncertain
    if url and result_cat in ("other",) and conf < 0.5:
        result_cat, conf, source = "link", max(conf, 0.6), source
    tags = extract_tags(text, result_cat, cfg.category_title(result_cat))

    note_id = storage.add_note(
        user_id=uid, kind=kind, text=text, category=result_cat, confidence=conf,
        source=source, tags=tags, file_id=file_id, url=url, when_date=when,
    )
    log.info("saved #%s cat=%s conf=%.2f src=%s kind=%s", note_id, result_cat, conf, source, kind)
    note = storage.get_note(note_id) or {}
    card = fmt_note_card(cfg, note)
    if not uncertain and conf < 0.35:
        uncertain = True
    if uncertain:
        # честный режим: неуверенная категория — сразу предлагаем выбрать верную
        await bot.send_message(
            chat_id,
            f"🤔 <b>Не уверен в категории</b> ({int(conf * 100)}%). "
            f"Выберите верную — я запомню и дообучусь:\n\n{card}",
            reply_markup=_cat_keyboard("fix", note_id, cfg),
            disable_web_page_preview=True)
    else:
        await bot.send_message(chat_id, card, reply_markup=_note_kb(note_id, kind, file_id),
                               disable_web_page_preview=True)


async def save_and_reply(msg: Message, bot: Bot, cfg: Config, storage: Storage,
                         classifier: HybridClassifier) -> None:
    kind, text, file_id = _extract_content(msg)
    text = (text or "").strip()
    if not text:
        await msg.answer("🤔 Пока не умею сохранять пустоту. Пришлите текст или медиа с подписью.")
        return
    await _store_and_reply(bot, cfg, storage, classifier, msg.chat.id,
                           msg.from_user.id if msg.from_user else 0,
                           kind, text, file_id)


def _cat_keyboard(prefix: str, note_id: int, cfg: Config, cols: int = 3) -> InlineKeyboardMarkup:
    cats = cfg.categories
    rows = []
    for i in range(0, len(cats), cols):
        rows.append([
            InlineKeyboardButton(text=f"{c.get('emoji', '📦')} {c['title']}",
                                 callback_data=f"{prefix}:{note_id}:{c['id']}")
            for c in cats[i:i + cols]
        ])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="noop")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    await msg.answer(HELP_TEXT, disable_web_page_preview=True)


@router.message(Command("help"))
async def cmd_help(msg: Message) -> None:
    await msg.answer(HELP_TEXT, disable_web_page_preview=True)


@router.message(Command("find"), F.text)
async def cmd_find(msg: Message, command: CommandObject | None) -> None:
    cfg, storage, bot = bot_ctx["cfg"], bot_ctx["storage"], bot_ctx["bot"]
    query = (command.args or "").strip() if command else ""
    if not query:
        await msg.answer("🔎 Напишите, что найти: <code>/find курс по питону</code>")
        return
    results = hybrid_search(storage, msg.from_user.id, query, limit=cfg.search_limit,
                            category_titles=_category_titles(cfg))
    semantic = False
    if not results:
        # лексика не совпала — ищем по смыслу моделью (тексты могут не иметь
        # ни одного общего слова с запросом)
        await bot.send_chat_action(msg.chat.id, ChatAction.TYPING)
        recent = storage.list_notes(msg.from_user.id, limit=100)
        picked_ids = await llm_pick_relevant(cfg, query, recent, limit=cfg.search_limit)
        if picked_ids:
            results = [n for n in recent if n["id"] in picked_ids]
            semantic = True
    if not results:
        await msg.answer(
            f"По запросу «{query}» ничего не нашлось — ни по словам, ни по смыслу.\n"
            f"Попробуйте /ask — я отвечу на вопрос по заметкам.")
        return
    prefix = "🧠 Нашёл <b>по смыслу</b>" if semantic else f"🔎 <b>Найдено {len(results)}</b>"
    await msg.answer(prefix + ":")
    for note in results:
        card = fmt_note_card(cfg, note)
        score_line = "" if semantic else f"\n<i>релевантность {note.get('score', 0)}</i>"
        kb = _note_kb(note["id"], note.get("kind") or "text", note.get("file_id"))
        await msg.answer(card + score_line, reply_markup=kb, disable_web_page_preview=True)


def _cited_notes(answer: str, notes: list[dict]) -> list[dict]:
    """Заметки, на которые ответ сослался [N] (номер = позиция в контексте).

    Порядок — по появлению в ответе; без цитат возвращается пустой список.
    """
    by_pos = {i + 1: n for i, n in enumerate(notes)}
    cited: list[dict] = []
    seen: set[int] = set()
    for m in re.findall(r"\[(\d+)\]", answer or ""):
        n = by_pos.get(int(m))
        if n is not None and n["id"] not in seen:
            cited.append(n)
            seen.add(n["id"])
    return cited


def _render_sources(cited: list[dict], notes: list[dict], cfg: Config,
                    max_chars: int = 260) -> str:
    """«📍 Где лежит»: номер совпадает с цитатой [N] в ответе, текст почти целиком."""
    pos = {n["id"]: i + 1 for i, n in enumerate(notes)}
    lines = []
    for n in cited:
        i = pos.get(n["id"], "?")
        title = cfg.category_title(n.get("category") or "other")
        txt = (n.get("text") or "").strip()
        if len(txt) > max_chars:
            txt = txt[:max_chars] + "…"
        when = f" · 📅 {n['when_date']}" if n.get("when_date") else ""
        lines.append(f"[{i}] #{n['id']} · {title}{when}\n{h_mod.escape(txt)}")
    return "\n\n".join(lines)


@router.callback_query(F.data.startswith("full:"))
async def cb_full(call: CallbackQuery) -> None:
    """Кнопка «📍 Показать»: полная заметка, ключи/токены — копируемым моношрифтом."""
    cfg, storage = bot_ctx["cfg"], bot_ctx["storage"]
    note_id = int((call.data or "full:0").split(":")[1])
    note = storage.get_note(note_id)
    if not note:
        await call.answer("Заметка удалена", show_alert=True)
        return
    title = cfg.category_title(note.get("category") or "other")
    when = f" · 📅 {note['when_date']}" if note.get("when_date") else ""
    tags = " ".join(f"#{t}" for t in (note.get("tags") or []))
    text = (note.get("text") or "").strip() or "(пусто)"
    cut = "…" if len(text) > 3500 else ""
    body = (f"<b>{h_mod.escape(title)}</b> · #{note['id']}{when}"
            + (f"\n{h_mod.escape(tags)}" if tags else "")
            + f"\n\n{escape_answer(wrap_secrets(text[:3500]))}{cut}")
    kb = _note_kb(note_id, note.get("kind") or "text", note.get("file_id"))
    await call.message.answer(body, reply_markup=kb, disable_web_page_preview=True)
    await call.answer()


@router.message(Command("ask"), F.text)
async def cmd_ask(msg: Message, command: CommandObject | None) -> None:
    cfg, storage, bot = bot_ctx["cfg"], bot_ctx["storage"], bot_ctx["bot"]
    question = (command.args or "").strip() if command else ""
    if not question:
        await msg.answer("🤖 Спросите по своим заметкам: <code>/ask что у меня запланировано?</code>")
        return
    await bot.send_chat_action(msg.chat.id, ChatAction.TYPING)
    notes = hybrid_search(storage, msg.from_user.id, question, limit=cfg.max_context_notes,
                          category_titles=_category_titles(cfg))
    # если лексика не совпала — добираем свежими заметками: вопрос может не делить
    # ни одного слова с текстом заметки (например, «опенроутер апи» ↔ строка ключа)
    if len(notes) < min(3, cfg.max_context_notes):
        seen = {n["id"] for n in notes}
        for n in storage.list_notes(msg.from_user.id, limit=25):
            if n["id"] not in seen:
                notes.append(n)
                seen.add(n["id"])
            if len(notes) >= 15:
                break
    notes = dedupe_notes(notes)
    # кандидатов больше лимита контекста — пусть модель отберёт только релевантные,
    # чтобы в источниках не было простыни из всего подряд
    if len(notes) > cfg.max_context_notes:
        picked_ids = await llm_pick_relevant(cfg, question, notes,
                                             limit=cfg.max_context_notes)
        if picked_ids:
            by_id = {n["id"]: n for n in notes}
            notes = [by_id[i] for i in picked_ids if i in by_id]
    try:
        answer = await answer_question(cfg, question, notes)
    except Exception as e:  # noqa: BLE001
        log.warning("ask failed: %s", e)
        answer = "⚠️ Модель не ответила (лимиты бесплатного тарифа или сеть). Попробуйте ещё раз через минуту."
        await msg.answer(answer)
        return
    if notes:
        cited = _cited_notes(answer, notes) or notes[:3]
        body = answer + "\n\n<b>📍 Где лежит:</b>\n" + _render_sources(cited, notes, cfg)
        btns = [InlineKeyboardButton(text=f"📍 #{n['id']}", callback_data=f"full:{n['id']}")
                for n in cited[:4]]
        kb = InlineKeyboardMarkup(inline_keyboard=[btns])
    else:
        body = answer
        kb = None
    await msg.answer(body, reply_markup=kb, disable_web_page_preview=True)


@router.message(Command("list"))
async def cmd_list(msg: Message) -> None:
    cfg, storage = bot_ctx["cfg"], bot_ctx["storage"]
    counts = storage.count_by_category(msg.from_user.id)
    rows = []
    for c in cfg.categories:
        n = counts.get(c["id"], 0)
        rows.append([InlineKeyboardButton(text=f"{c.get('emoji','📦')} {c['title']} — {n}",
                                          callback_data=f"list:{c['id']}:0")])
    await msg.answer("🗂 Выберите категорию:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(Command("stats"))
async def cmd_stats(msg: Message) -> None:
    cfg, storage = bot_ctx["cfg"], bot_ctx["storage"]
    uid = msg.from_user.id
    counts = storage.count_by_category(uid)
    total = storage.total(uid)
    lines = [f"📊 <b>Статистика</b> — всего заметок: <b>{total}</b>", ""]
    for c in cfg.categories:
        n = counts.get(c["id"], 0)
        bar = "▇" * int(n / max(1, max(counts.values())) * 12) if counts else ""
        lines.append(f"{c.get('emoji','📦')} {c['title']}: {n} {bar}")
    src_local = sum(1 for n in storage.list_notes(uid, limit=500) if n.get("source") == "local")
    lines.append("")
    lines.append(f"🧠 Локальным классификатором угадано: {src_local}/{total}")
    await msg.answer("\n".join(lines))


@router.message(Command("upcoming"))
async def cmd_upcoming(msg: Message) -> None:
    cfg, storage = bot_ctx["cfg"], bot_ctx["storage"]
    notes = storage.dated_notes(msg.from_user.id, limit=15)
    if not notes:
        await msg.answer("📅 Заметок с распознанными датами пока нет. Пишите «в пятницу в 19:00 в кино» — я запомню.")
        return
    await msg.answer("📅 <b>Ближайшие даты:</b>")
    for n in notes:
        kb = _note_kb(n["id"], n.get("kind") or "text", n.get("file_id"))
        await msg.answer(f"📅 <b>{n['when_date']}</b> — {truncate(n.get('text') or '', 120)}",
                         reply_markup=kb)


@router.message(Command("digest"))
async def cmd_digest(msg: Message) -> None:
    cfg, storage, bot = bot_ctx["cfg"], bot_ctx["storage"], bot_ctx["bot"]
    await bot.send_chat_action(msg.chat.id, ChatAction.TYPING)
    today = datetime.now().strftime("%Y-%m-%d")
    notes = storage.today_notes(msg.from_user.id, today)
    try:
        text = await make_digest(cfg, notes, today)
    except Exception as e:  # noqa: BLE001
        log.warning("digest failed: %s", e)
        text = "⚠️ Не получилось составить дайджест (модель или сеть). Попробуйте позже."
    await msg.answer(f"🌅 <b>Дайджест за {today}</b>\n\n{text}")


@router.message(Command("clear"))
async def cmd_clear(msg: Message) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🗑 Да, удалить всё", callback_data="clear:yes"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="noop"),
    ]])
    await msg.answer("🧹 Точно удалить <b>все</b> заметки? Это необратимо.", reply_markup=kb)


@router.message(Command("export"))
async def cmd_export(msg: Message) -> None:
    cfg, storage = bot_ctx["cfg"], bot_ctx["storage"]
    notes = storage.list_notes(msg.from_user.id, limit=10000)
    out = LOG_DIR.parent / "data" / f"export-{msg.from_user.id}-{datetime.now():%Y-%m-%d}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(notes, ensure_ascii=False, indent=1), encoding="utf-8")
    await msg.answer_document(FSInputFile(str(out)),
                              caption=f"📦 Экспорт: {len(notes)} заметок (JSON)")


# ---------- все сообщения без команды: сохраняем ----------

@router.message(F.photo | F.voice | F.video | F.video_note | F.audio | F.document | F.sticker)
async def handle_media(msg: Message) -> None:
    await save_and_reply(msg, bot_ctx["bot"], bot_ctx["cfg"], bot_ctx["storage"], bot_ctx["classifier"])


@router.message(F.text)
async def handle_text(msg: Message) -> None:
    if msg.text and msg.text.startswith("/"):
        await msg.answer("🤷 Не знаю такой команды. /help — список команд.")
        return
    await save_and_reply(msg, bot_ctx["bot"], bot_ctx["cfg"], bot_ctx["storage"], bot_ctx["classifier"])


# ---------- callbacks ----------

@router.callback_query(F.data.startswith("recat:"))
async def cb_recat(call: CallbackQuery) -> None:
    cfg = bot_ctx["cfg"]
    note_id = int((call.data or "recat:0").split(":")[1])
    await call.message.edit_reply_markup(reply_markup=_cat_keyboard("fix", note_id, cfg))
    await call.answer("Выберите категорию")


@router.callback_query(F.data.startswith("fix:"))
async def cb_fix(call: CallbackQuery) -> None:
    cfg, storage = bot_ctx["cfg"], bot_ctx["storage"]
    _, note_id_s, cat_id = (call.data or "").split(":")
    note_id = int(note_id_s)
    note = storage.get_note(note_id)
    if note:
        storage.update_category(note_id, cat_id, confidence=1.0, source="user")
        storage.add_correction(note.get("user_id", 0), note.get("text") or "", cat_id)
        bot_ctx["classifier"].retrain()  # мгновенное дообучение на правке
        title = cfg.category_title(cat_id)
        await call.answer(f"✅ Запомнил: это {title}", show_alert=False)
        try:
            await call.message.edit_reply_markup(reply_markup=_note_kb(
                note_id, note.get("kind") or "text", note.get("file_id")))
        except Exception:  # noqa: BLE001
            pass


@router.callback_query(F.data.startswith("del:"))
async def cb_del(call: CallbackQuery) -> None:
    storage = bot_ctx["storage"]
    note_id = int((call.data or "del:0").split(":")[1])
    note = storage.get_note(note_id)
    ok = storage.delete_note(note_id)
    if ok and note:
        trash = bot_ctx.setdefault("trash", {})
        trash[note_id] = note
        if len(trash) > 50:
            for k in list(trash)[:-50]:
                trash.pop(k, None)
    await call.answer("🗑 Удалено" if ok else "Уже удалено")
    try:
        if call.message and call.message.text:
            kb = None
            if ok and note:
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="↩︎ Восстановить",
                                         callback_data=f"restore:{note_id}"),
                ]])
            await call.message.edit_text(
                "🗑 <s>" + h_mod.escape(call.message.text[:3500]) + "</s>", reply_markup=kb)
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.startswith("restore:"))
async def cb_restore(call: CallbackQuery) -> None:
    cfg, storage = bot_ctx["cfg"], bot_ctx["storage"]
    note_id = int((call.data or "restore:0").split(":")[1])
    note = bot_ctx.get("trash", {}).pop(note_id, None)
    if not note:
        await call.answer("Заметка не найдена в карзине", show_alert=True)
        return
    new_id = storage.add_note(
        user_id=note.get("user_id", 0), kind=note.get("kind") or "text",
        text=note.get("text") or "", category=note.get("category") or "other",
        confidence=note.get("confidence") or 0, source=note.get("source") or "user",
        tags=note.get("tags") or [], file_id=note.get("file_id"),
        url=note.get("url"), when_date=note.get("when_date"),
        created_at=note.get("created_at"),
    )
    await call.answer(f"↩︎ Восстановлено как #{new_id}", show_alert=True)
    try:
        await call.message.edit_text(f"↩︎ Восстановлено как #{new_id}:\n\n"
                                     + fmt_note_card(cfg, storage.get_note(new_id) or {}))
    except Exception:  # noqa: BLE001
        pass


@router.callback_query(F.data.startswith("dup:"))
async def cb_dup(call: CallbackQuery) -> None:
    """«Всё равно сохранить» для точного дубликата."""
    cfg, storage, classifier, bot = (bot_ctx["cfg"], bot_ctx["storage"],
                                     bot_ctx["classifier"], bot_ctx["bot"])
    key = (call.data or "dup:")[4:]
    payload = bot_ctx.get("dup_cache", {}).pop(key, None)
    if not payload:
        await call.answer("Срок действия кнопки истёк", show_alert=True)
        return
    uid, kind, text, file_id = payload
    await call.answer()
    await _store_and_reply(bot, cfg, storage, classifier,
                           call.message.chat.id, uid, kind, text, file_id, force=True)


@router.callback_query(F.data.startswith("similar:"))
async def cb_similar(call: CallbackQuery) -> None:
    cfg, storage = bot_ctx["cfg"], bot_ctx["storage"]
    note_id = int((call.data or "similar:0").split(":")[1])
    note = storage.get_note(note_id)
    if not note:
        await call.answer("Заметка удалена")
        return
    results = [n for n in hybrid_search(storage, note.get("user_id", 0), note.get("text") or "", limit=4)
               if n["id"] != note_id][:3]
    if not results:
        await call.answer("Похожих не нашлось", show_alert=True)
        return
    await call.answer("Нашёл похожие")
    for n in results:
        await bot_ctx["bot"].send_message(
            call.message.chat.id, fmt_note_card(cfg, n),
            reply_markup=_note_kb(n["id"], n.get("kind") or "text", n.get("file_id")),
            disable_web_page_preview=True)


@router.callback_query(F.data.startswith("media:"))
async def cb_media(call: CallbackQuery) -> None:
    storage = bot_ctx["storage"]
    note_id = int((call.data or "media:0").split(":")[1])
    note = storage.get_note(note_id)
    if not note or not note.get("file_id"):
        await call.answer("Файл не найден")
        return
    kind, fid = note["kind"], note["file_id"]
    chat_id = call.message.chat.id
    try:
        if kind == "photo":
            await bot_ctx["bot"].send_photo(chat_id, fid)
        elif kind in ("video",):
            await bot_ctx["bot"].send_video(chat_id, fid)
        elif kind == "audio":
            await bot_ctx["bot"].send_audio(chat_id, fid)
        else:
            await bot_ctx["bot"].send_document(chat_id, fid)
        await call.answer()
    except Exception as e:  # noqa: BLE001
        await call.answer(f"Не удалось: {e}", show_alert=True)


@router.callback_query(F.data.startswith("list:"))
async def cb_list(call: CallbackQuery) -> None:
    cfg, storage = bot_ctx["cfg"], bot_ctx["storage"]
    _, cat, off_s = (call.data or "list:other:0").split(":")
    offset = int(off_s)
    uid = call.from_user.id
    notes = storage.list_notes(uid, category=cat, limit=PAGE_SIZE, offset=offset)
    total = storage.count_by_category(uid).get(cat, 0)
    if not notes:
        await call.answer("Здесь пока пусто", show_alert=True)
        return
    title = cfg.category_title(cat)
    body = f"🗂 <b>{title}</b> ({total})\n\n" + "\n".join(
        f"#{n['id']} — {truncate(n.get('text') or '', 90)}" for n in notes)
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="◀️", callback_data=f"list:{cat}:{max(0, offset - PAGE_SIZE)}"))
    if offset + PAGE_SIZE < total:
        nav.append(InlineKeyboardButton(text="▶️", callback_data=f"list:{cat}:{offset + PAGE_SIZE}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[nav] if nav else [])
    await call.message.edit_text(body, reply_markup=kb, disable_web_page_preview=True)
    await call.answer()


@router.callback_query(F.data.startswith("clear:"))
async def cb_clear(call: CallbackQuery) -> None:
    storage = bot_ctx["storage"]
    if (call.data or "").endswith("yes"):
        n = storage.clear_user(call.from_user.id)
        await call.message.edit_text(f"🧹 Удалено заметок: {n}")
        await call.answer("Готово")
    else:
        await call.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery) -> None:
    await call.answer()


@router.inline_query()
async def inline_find(q: InlineQuery) -> None:
    """@favorites_plus_bot запрос в любом чате — мгновенный поиск без LLM."""
    cfg, storage = bot_ctx["cfg"], bot_ctx["storage"]
    query = (q.query or "").strip()
    if len(query) < 2:
        uname = q.from_user.username or "favorites_plus_bot"
        await q.answer([InlineQueryResultArticle(
            id="hint", title="🔎 Поиск по «Избранному»",
            description="Введите запрос: @" + uname + " <запрос>",
            input_message_content=InputTextMessageContent(
                message_text="🔎 Введите запрос: <code>@" + uname + " <запрос></code>")),
        ], cache_time=5, is_personal=True)
        return
    results = hybrid_search(storage, q.from_user.id, query, limit=8,
                            category_titles=_category_titles(cfg))
    articles = []
    for n in results:
        articles.append(InlineQueryResultArticle(
            id=f"n{n['id']}",
            title=f"{cfg.category_title(n.get('category') or 'other')} · #{n['id']}",
            description=truncate(n.get("text") or "", 120),
            input_message_content=InputTextMessageContent(
                message_text=fmt_note_card(cfg, n) + "\n\n<i>из поиска «"
                             + h_mod.escape(query[:100]) + "»</i>"),
        ))
    if not articles:
        articles = [InlineQueryResultArticle(
            id="none", title="Ничего не нашлось",
            description=f"По «{truncate(query, 60)}» заметок нет",
            input_message_content=InputTextMessageContent(
                message_text=f"По запросу «{h_mod.escape(query[:100])}» ничего не нашлось.")),
        ]
    await q.answer(articles, cache_time=10, is_personal=True)


# ---------- контекст и запуск ----------

bot_ctx: dict = {}


async def _digest_loop(bot: Bot, cfg: Config, storage: Storage) -> None:
    """Ежедневный дайджест, напоминания по датам и ночная копия БД."""
    last_sent: str = storage.get_meta("last_digest") or ""
    last_backup_day: str = storage.get_meta("last_backup") or ""
    while True:
        try:
            now = datetime.now()
            day = now.strftime("%Y-%m-%d")

            # ночная копия базы: раз в сутки + при старте
            if last_backup_day != day:
                _backup_db()
                storage.set_meta("last_backup", day)
                last_backup_day = day

            if cfg.digest_enabled:
                hh_mm = now.strftime("%H:%M")
                if hh_mm == cfg.digest_time and last_sent != day:
                    users = [r[0] for r in storage.conn.execute(
                        "SELECT DISTINCT user_id FROM notes").fetchall()]
                    for uid in users:
                        notes = storage.today_notes(uid, day)
                        try:
                            text = await make_digest(cfg, notes, day)
                        except Exception as e:  # noqa: BLE001
                            log.warning("auto digest failed for %s: %s", uid, e)
                            continue
                        try:
                            await bot.send_message(uid, f"🌅 <b>Дайджест за {day}</b>\n\n{text}")
                        except Exception as e:  # noqa: BLE001
                            log.warning("send digest to %s failed: %s", uid, e)
                    storage.set_meta("last_digest", day)
                    last_sent = day

            if cfg.reminder_enabled:
                await _send_due_reminders(bot, storage, now)
        except Exception:  # noqa: BLE001
            log.exception("digest loop error")
        await asyncio.sleep(30)


def _backup_db() -> None:
    """Копия SQLite-базы в data/backups (хранить 14 дней)."""
    try:
        backups = DB_PATH.parent / "backups"
        backups.mkdir(parents=True, exist_ok=True)
        day = datetime.now().strftime("%Y-%m-%d")
        target = backups / f"favorites-{day}.db"
        if not target.exists():
            shutil.copyfile(DB_PATH, target)
        backups_list = sorted(backups.glob("favorites-*.db"))
        for old in backups_list[:-14]:
            old.unlink(missing_ok=True)
        log.info("backup ok: %s", target.name)
    except Exception as e:  # noqa: BLE001
        log.warning("backup failed: %s", e)


async def _send_due_reminders(bot: Bot, storage: Storage, now: datetime) -> None:
    """Напоминания по заметкам с датой на сегодня: время из текста, иначе 09:00."""
    day = now.strftime("%Y-%m-%d")
    hhmm = now.strftime("%H:%M")
    key = f"reminded:{day}"
    reminded = set(json.loads(storage.get_meta(key) or "[]"))
    users = [r[0] for r in storage.conn.execute(
        "SELECT DISTINCT user_id FROM notes").fetchall()]
    for uid in users:
        for n in storage.notes_on_date(uid, day):
            if n["id"] in reminded:
                continue
            target = first_time_in_text(n.get("text") or "") or "09:00"
            if hhmm < target:
                continue
            reminded.add(n["id"])
            try:
                await bot.send_message(
                    uid,
                    f"⏰ <b>Напоминание на {day}</b> (срок {target}):\n"
                    + fmt_note_card(_ctx_cfg(), n),
                    reply_markup=_note_kb(n["id"], n.get("kind") or "text", n.get("file_id")),
                    disable_web_page_preview=True)
            except Exception as e:  # noqa: BLE001
                log.warning("reminder to %s failed: %s", uid, e)
                reminded.discard(n["id"])
    storage.set_meta(key, json.dumps(sorted(reminded)))


def _ctx_cfg() -> Config:
    return bot_ctx["cfg"]


async def run(cfg: Config | None = None) -> None:
    cfg = cfg or load_config()
    storage = Storage(DB_PATH)
    classifier = HybridClassifier(cfg, storage)

    from aiogram.client.default import DefaultBotProperties
    bot = Bot(token=cfg.bot_token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    bot_ctx.update(cfg=cfg, storage=storage, classifier=classifier, bot=bot)

    dp = Dispatcher()
    dp.include_router(router)

    me = await bot.get_me()
    log.info("bot @%s (%s) стартует; mode=%s model=%s", me.username, me.first_name,
             cfg.classifier_mode, cfg.classifier_model)

    digest_task = asyncio.create_task(_digest_loop(bot, cfg, storage))
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query",
                                                    "inline_query"])
    finally:
        digest_task.cancel()


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(LOG_DIR / "bot.log", maxBytes=2_000_000,
                                              backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)


def main() -> None:
    setup_logging()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("остановлено пользователем")


if __name__ == "__main__":
    main()
