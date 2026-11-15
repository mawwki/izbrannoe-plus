"""Тесты ядра «Избранное+»: хранилище, поиск, утилиты, локальный классификатор."""
from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from favorites_plus.classifier import HybridClassifier, LocalClassifier, secret_category_id  # noqa: E402
from favorites_plus.config import Config  # noqa: E402
from favorites_plus.search import hybrid_search  # noqa: E402
from favorites_plus.storage import Storage  # noqa: E402
from favorites_plus.utils import (extract_tags, extract_when, extract_url, first_time_in_text,
                                  fmt_note_card, looks_like_secret, truncate)  # noqa: E402

passed = failed = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name} {extra}")


print("=== utils ===")
check("url", extract_url("статья https://habr.com/ru/123, читай") == "https://habr.com/ru/123")
check("tags money", "💰" in extract_tags("куплю за 1500 руб", ""))
check("tags url", "url" in extract_tags("ссылка http://a.b/c", ""))
check("secret sk-key", "🔑" in extract_tags("sk-or-v1-0000fake111122223333444455556666", ""))
check("secret random token", looks_like_secret("wx2CFwDl4Qukgk0QVtuz4HH6GtfgPim2"))
check("not secret", not looks_like_secret("купить хлеб и молоко завтра"))
check("secret github", looks_like_secret("ключ ghp_xKq9vT3bN7mZ2aQwErTyUiOpAs"))
check("date numeric", extract_when("встреча 25.12") == f"{date.today().year}-12-25")
check("date no date", extract_when("просто записка без дат") is None)
check("date tomorrow", extract_when("позвонить завтра") == str(date.today() + timedelta(days=1)))
check("date weekday", extract_when("иду в пятницу в 19:00") is not None)
check("date month", extract_when("день рождения 14 марта") == f"{date.today().year}-03-14")
check("truncate", len(truncate("а" * 200, 80)) <= 80)

print("=== storage ===")
tmp = Path(tempfile.mkdtemp()) / "test.db"
st = Storage(tmp)
check("fts enabled", st.fts_enabled, "FTS5 недоступен")
nid1 = st.add_note(1, "text", "купить билет в кино на пятницу 19:00", "task", 0.9, "local", ["💰"], when_date="2026-01-01")
nid2 = st.add_note(1, "text", "конспект лекции по алгоритмам: деревья", "study", 0.8, "openrouter", [])
nid3 = st.add_note(2, "text", "заметка другого пользователя", "other", 0.5, "local", [])
check("add/get", st.get_note(nid1)["category"] == "task")
check("counts", st.count_by_category(1) == {"task": 1, "study": 1})
check("total user", st.total(1) == 2)
check("list by cat", [n["id"] for n in st.list_notes(1, category="study")] == [nid2])
check("user isolation", st.total(2) == 1)
st.update_category(nid1, "event", source="user")
check("update", st.get_note(nid1)["category"] == "event" and st.get_note(nid1)["source"] == "user")
st.add_correction(1, "купить билет в кино", "event")
check("corrections", st.corrections(1) == [("купить билет в кино", "event")])
check("dated", [n["id"] for n in st.dated_notes(1)] == [nid1])
check("today", len(st.today_notes(1, "1900-01-01")) == 2)
check("delete", st.delete_note(nid3) and st.get_note(nid3) is None)

print("=== search ===")
st2 = Storage(Path(tempfile.mkdtemp()) / "s.db")
st2.add_note(7, "text", "лекция по нейронным сетям: обратное распространение ошибки", "study", 0.9, "local")
st2.add_note(7, "text", "купить кефир и молоко", "purchase", 0.9, "local")
st2.add_note(7, "text", "https://habr.com статья про трансформеры", "link", 0.9, "local")
st2.add_note(7, "text", "встреча в пятницу в 18:00", "event", 0.9, "local")
r = hybrid_search(st2, 7, "нейронные сети", 3)
check("semantic find study", bool(r) and r[0]["category"] == "study", str([x["text"] for x in r]))
r = hybrid_search(st2, 7, "кефир", 3)
check("keyword find purchase", bool(r) and r[0]["category"] == "purchase")
r = hybrid_search(st2, 7, "трансформеры habr", 3)
check("multi-token", bool(r) and r[0]["category"] == "link")
check("other user empty", hybrid_search(st2, 99, "кефир", 3) == [])
check("empty query", hybrid_search(st2, 7, "", 3) == [])

print("=== local classifier ===")
lc = LocalClassifier()
lc.train(Config().categories, [])
for text, expect in [
    ("купить хлеб завтра утром", {"task", "purchase"}),
    ("созвон с командой в 15:00", {"task", "event"}),
    ("конспект по программированию: циклы", {"study",}),
    ("https://example.com крутая статья", {"link",}),
    ("придумал классную фичу для бота", {"idea",}),
    ("день рождения мамы 5 апреля", {"event",}),
]:
    res = lc.predict(text)
    check(f"'{text[:30]}' -> {res.category} ({res.confidence:.2f})", res.category in expect,
          f"expected in {expect}")

# дообучение на правке меняет предсказание при повторе текста
lc.train(Config().categories, [("купить хлеб завтра утром", "purchase")])
check("retrain works", lc.predict("купить хлеб завтра утром").category == "purchase")

# пользователь переименовал «Задачи» в «api» — сиды задач не должны учиться на «api»
cfg_ren = Config()
renamed = [dict(c) for c in cfg_ren.categories]
for c in renamed:
    if c["id"] == "task":
        c["title"], c["emoji"] = "api", "🗝️"
        c["criteria"] = "api keys вроде sk-or-v1-..."
cfg_ren.categories = renamed
check("secret category found", secret_category_id(cfg_ren) == "task")
lc2 = LocalClassifier()
lc2.train(cfg_ren.categories, [("sk-or-v1-abcdef123456ghij", "task")])
check("renamed seeds dropped", lc2.predict("купить хлеб завтра утром").category != "task")

# fast-path для секретов: мгновенно, уверенно, без сети
hc_ren = HybridClassifier(cfg_ren, None)
res_s = asyncio.run(hc_ren.classify("sk-or-v1-0000fake111122223333444455556666"))
check("secret fast-path", res_s.category == "task" and res_s.source == "local"
      and not res_s.uncertain, str(res_s))
res_tok = asyncio.run(hc_ren.classify("wx2CFwDl4Qukgk0QVtuz4HH6GtfgPim2"))
check("token fast-path", res_tok.category == "task" and not res_tok.uncertain, str(res_tok))

# неуверенный ответ помечается (бот покажет выбор категории)
hc_def = HybridClassifier(Config(), None)
res_u = asyncio.run(hc_def.classify("блаблабла xyzzy невнятное"))
check("uncertain flagged", res_u.uncertain or res_u.confidence >= 0.35, str(res_u))

# поиск по названию категории (запрос «api» находит ключи без общих слов)
st3 = Storage(Path(tempfile.mkdtemp()) / "boost.db")
st3.add_note(7, "text", "sk-or-v1-0028f279960ab0a3e058267", "task", 0.9, "local", ["🔑"])
check("no lexical match", hybrid_search(st3, 7, "api", 3) == [])
r = hybrid_search(st3, 7, "api", 3, category_titles={"task": "api"})
check("category title boost", bool(r) and r[0]["category"] == "task", str(r))

print("=== /ask rendering ===")
from favorites_plus.bot import _cited_notes, _render_sources  # noqa: E402
from favorites_plus.utils import wrap_secrets  # noqa: E402

_n = lambda nid, txt, cat="other": {"id": nid, "text": txt, "category": cat, "tags": [],
                                    "created_at": "2026-11-15T22:00:00", "kind": "text",
                                    "confidence": 0.9, "source": "local", "when_date": None,
                                    "user_id": 1, "url": None, "file_id": None, "score": 1.0}
notes = [_n(12, "али опенроутера"), _n(11, "ЩШЩИЩЕРЛУГТОВЫЛУОУОЙОУЛЪ"),
         _n(9, "wx2CFwDl4Qukgk0QVtuz4HH6GtfgPim2"), _n(8, "sk-or-v1-0028f279960ab0a3e058267"),
         _n(5, "sk-or-v1-0028f279960ab0a3e058267")]
cited = _cited_notes("Ключ OpenRouter: [4] и [5].", notes)
check("cited only", [n["id"] for n in cited] == [8, 5], str([n["id"] for n in cited]))
check("cited empty on none", _cited_notes("ничего не нашлось", notes) == [])
cfg = Config()
src = _render_sources(cited, notes, cfg)
check("sources keep [N]", "[4] #8 ·" in src and "[5] #5 ·" in src, src[:120])
check("sources no uncited", "#12" not in src and "#11" not in src)
check("wrap secrets", "⟦sk-or-v1-0028f279960ab0a3e058267⟧" in
      wrap_secrets("ключ sk-or-v1-0028f279960ab0a3e058267 ок"))

print("=== опечатки, дедуп, карточки ===")
st4 = Storage(Path(tempfile.mkdtemp()) / "n.db")
st4.add_note(7, "text", "⚡ Motion Blur в MargyT 2.3.0", "other", 0.8, "local", [])
st4.add_note(7, "text", "лекция по алгоритмам: жадные методы", "study", 0.9, "local", [])
r = hybrid_search(st4, 7, "маргут", 3)
check("translit search", bool(r) and "MargyT" in r[0]["text"], str(r))
r = hybrid_search(st4, 7, "алгаритмы", 3)
check("typo ru search", bool(r) and r[0]["category"] == "study", str(r))
dup = st4.find_same_text(7, "⚡ Motion Blur в MargyT 2.3.0")
check("find_same_text", dup is not None and dup["id"] == 1)
card = fmt_note_card(Config(), {"id": 1, "kind": "text", "category": "other", "confidence": 0.9,
                                "source": "local", "tags": [], "file_id": None, "when_date": None,
                                "text": "ключ sk-or-v1-abcdef123456789012 внутри",
                                "created_at": "2026-01-01T10:00:00"})
check("card code key", "<code>sk-or-v1-abcdef123456789012</code>" in card, card[:200])
check("first_time", first_time_in_text("встреча в 19:05 в кино") == "19:05")
check("first_time none", first_time_in_text("встреча вечером") is None)

print("=== hybrid (offline fallback) ===")
cfg = Config()
cfg.classifier_mode = "local"
hc = HybridClassifier(cfg, None)
res = lc.predict("статья на хабре про python") if lc else None
res2 = __import__("asyncio").run(hc.classify("созвон завтра в 10"))
check("hybrid local mode", res2.source == "local")

print(f"\nИтог: {passed} ok, {failed} failed")
sys.exit(1 if failed else 0)
