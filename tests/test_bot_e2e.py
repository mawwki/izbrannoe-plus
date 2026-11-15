"""E2E-тест бота «Избранное+»: обработчики aiogram через FakeSession (без сети)."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import CallbackQuery, Chat, Message, Update, User  # noqa: E402

from favorites_plus import bot as botmod  # noqa: E402
from favorites_plus.classifier import HybridClassifier  # noqa: E402
from favorites_plus.config import Config  # noqa: E402
from favorites_plus.storage import Storage  # noqa: E402

UID = 777


class FakeSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[tuple[str, object]] = []

    async def close(self) -> None:
        pass

    async def make_request(self, bot, method, timeout=None):
        name = method.__api_method__
        self.requests.append((name, method))
        now = datetime.now()
        chat = Chat(id=1, type="private")
        if name == "getMe":
            return User(id=9133059140, is_bot=True, first_name="Избранное+",
                        username="favorites_plus_bot")
        if name == "answerCallbackQuery":
            return True
        if name in ("sendMessage", "sendPhoto", "sendVideo", "sendAudio",
                    "sendDocument", "sendChatAction", "editMessageText",
                    "editMessageReplyMarkup"):
            return Message(message_id=len(self.requests), date=now, chat=chat)
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536,
                             raise_for_status=True):
        yield b""
        return


passed = failed = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name} {extra}")


def mk_message(text: str | None, message_id: int = 1) -> Message:
    return Message(message_id=message_id, date=datetime.now(),
                   chat=Chat(id=1, type="private"),
                   from_user=User(id=UID, is_bot=False, first_name="Тест"), text=text)


async def main() -> tuple[int, int]:
    session = FakeSession()
    bot = Bot(token="9133059140:TEST", session=session)
    cfg = Config()
    cfg.classifier_mode = "local"
    cfg.openrouter_key = ""  # сеть не нужна
    db = Path(tempfile.mkdtemp()) / "e2e.db"
    storage = Storage(db)
    classifier = HybridClassifier(cfg, storage)
    botmod.bot_ctx.update(cfg=cfg, storage=storage, classifier=classifier, bot=bot)

    dp = Dispatcher()
    dp.include_router(botmod.router)

    async def feed(update):
        await dp.feed_update(bot, update)

    # /start
    await feed(Update(update_id=1, message=mk_message("/start")))
    check("/start -> sendMessage", any(n == "sendMessage" for n, _ in session.requests))

    # сохранение текстовой заметки
    session.requests.clear()
    await feed(Update(update_id=2, message=mk_message("купить билет в кино на пятницу 19:00")))
    check("save -> sendMessage card", any(n == "sendMessage" for n, _ in session.requests))
    notes = storage.list_notes(UID)
    check("note stored", len(notes) == 1, str(notes))
    note = notes[0]
    check("note has date", note["when_date"] is not None, str(note["when_date"]))
    check("card has category", "Категория" not in str(note) and note["category"] in
          {c["id"] for c in cfg.categories})

    # /find — найдём сохранённое
    session.requests.clear()
    await feed(Update(update_id=3, message=mk_message("/find кино")))
    sends = [n for n, _ in session.requests if n == "sendMessage"]
    check("/find works", len(sends) >= 2, str(session.requests))

    # /stats
    session.requests.clear()
    await feed(Update(update_id=4, message=mk_message("/stats")))
    check("/stats works", any(n == "sendMessage" for n, _ in session.requests))

    # photo без подписи → сразу media
    session.requests.clear()
    photo_msg = Message(message_id=9, date=datetime.now(), chat=Chat(id=1, type="private"),
                        from_user=User(id=UID, is_bot=False, first_name="Т"),
                        photo=[])  # пустое поле photo парсится? проверим отдельно
    # проще: voice с file_id
    from aiogram.types import Voice
    voice_msg = Message(message_id=9, date=datetime.now(), chat=Chat(id=1, type="private"),
                        from_user=User(id=UID, is_bot=False, first_name="Т"),
                        voice=Voice(file_id="F1", file_unique_id="U1", duration=3, mime_type="audio/ogg"))
    await feed(Update(update_id=5, message=voice_msg))
    vnote = storage.list_notes(UID)[0]
    check("voice -> media", vnote["kind"] == "voice" and vnote["category"] == "media"
          and vnote["file_id"] == "F1", f"{vnote['kind']}/{vnote['category']}")

    # callback «Не то» → выбор категории → fix на study
    session.requests.clear()
    cq_note = storage.list_notes(UID)[-1]
    from aiogram.types import InlineKeyboardMarkup  # noqa: F401
    cb = CallbackQuery(id="c1", from_user=User(id=UID, is_bot=False, first_name="Т"),
                       chat_instance="ci", data=f"fix:{cq_note['id']}:study",
                       message=Message(message_id=10, date=datetime.now(),
                                       chat=Chat(id=1, type="private")))
    await feed(Update(update_id=6, callback_query=cb))
    upd = storage.get_note(cq_note["id"])
    check("callback fix -> category updated", upd["category"] == "study" and upd["source"] == "user")
    check("correction saved", len(storage.corrections(UID)) == 1)

    # callback удаления
    cb = CallbackQuery(id="c2", from_user=User(id=UID, is_bot=False, first_name="Т"),
                       chat_instance="ci", data=f"del:{cq_note['id']}",
                       message=Message(message_id=10, date=datetime.now(),
                                       chat=Chat(id=1, type="private")))
    await feed(Update(update_id=7, callback_query=cb))
    check("callback del -> deleted", storage.get_note(cq_note["id"]) is None)

    # неизвестная команда
    session.requests.clear()
    await feed(Update(update_id=8, message=mk_message("/nonexistent")))
    check("unknown command answered", any(n == "sendMessage" for n, _ in session.requests))

    # ---------- дубликат: сохраняем тот же текст два раза подряд ----------
    session.requests.clear()
    before = storage.total(UID)
    await feed(Update(update_id=10, message=mk_message("купить билет в кино на пятницу 19:00")))
    check("resaved after del", storage.total(UID) == before + 1)
    session.requests.clear()
    await feed(Update(update_id=10, message=mk_message("купить билет в кино на пятницу 19:00")))
    check("dup answered", any(n == "sendMessage" for n, _ in session.requests))
    check("dup not stored", storage.total(UID) == before + 1)
    dup_cache = botmod.bot_ctx.get("dup_cache", {})
    check("dup cache has key", len(dup_cache) >= 1)

    # «Всё равно сохранить» через callback dup:
    key = sorted(dup_cache)[-1]
    cb = CallbackQuery(id="c3", from_user=User(id=UID, is_bot=False, first_name="Т"),
                       chat_instance="ci", data=f"dup:{key}",
                       message=Message(message_id=20, date=datetime.now(),
                                       chat=Chat(id=1, type="private")))
    await feed(Update(update_id=11, callback_query=cb))
    check("dup forced stored", storage.total(UID) == before + 2)

    # ---------- undo: удалить и восстановить ----------
    victim = storage.list_notes(UID)[0]
    count_after_del = None
    cb = CallbackQuery(id="c4", from_user=User(id=UID, is_bot=False, first_name="Т"),
                       chat_instance="ci", data=f"del:{victim['id']}",
                       message=Message(message_id=21, date=datetime.now(),
                                       chat=Chat(id=1, type="private")))
    await feed(Update(update_id=12, callback_query=cb))
    check("del ok", storage.get_note(victim["id"]) is None)
    count_after_del = storage.total(UID)
    cb = CallbackQuery(id="c5", from_user=User(id=UID, is_bot=False, first_name="Т"),
                       chat_instance="ci", data=f"restore:{victim['id']}",
                       message=Message(message_id=22, date=datetime.now(),
                                       chat=Chat(id=1, type="private")))
    await feed(Update(update_id=13, callback_query=cb))
    check("restore ok", storage.total(UID) == count_after_del + 1
          and any(n["text"] == victim["text"] for n in storage.list_notes(UID)))

    # ---------- инлайн-поиск ----------
    from aiogram.types import InlineQuery
    session.requests.clear()
    iq = InlineQuery(id="iq1", from_user=User(id=UID, is_bot=False, first_name="Т"),
                     query="кино", offset="")
    await feed(Update(update_id=14, inline_query=iq))
    check("inline answered", any(n == "answerInlineQuery" for n, _ in session.requests))

    await bot.session.close()
    return passed, failed


asyncio.run(main())
print(f"\nE2E итог: {passed} ok, {failed} failed")
sys.exit(1 if failed else 0)
