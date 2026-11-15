"""Конфигурация «Избранное+»: пути, категории, дефолты, load/save JSON."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.json"
LOG_DIR = PROJECT_ROOT / "logs"
DB_PATH = PROJECT_ROOT / "data" / "favorites.db"
LOGO_PATH = PROJECT_ROOT / "assets" / "logo.png"

BOT_TOKEN = os.environ.get("IZBRANNOE_BOT_TOKEN", "")
OPENROUTER_KEY = os.environ.get("IZBRANNOE_OPENROUTER_KEY", "")

# Модели-классификаторы (Decisions API). span-01-lite принимает только noul-вопросы.
DECIDER_MODELS = [
    "perplexity/pplx-decider-flash-v1.3:free",
    "perplexity/pplx-decider-v1.3:free",
    "respan/span-01-lite:free",
]
ANSWER_MODELS = ["openrouter/free", "openrouter/auto"]

CLASSIFIER_MODES = ["hybrid", "local", "openrouter"]

DEFAULT_CATEGORIES: list[dict[str, str]] = [
    {"id": "task", "title": "Задачи", "emoji": "✅",
     "criteria": "Задача, напоминание или просьба: есть действие, срок или «не забыть»."},
    {"id": "idea", "title": "Идеи", "emoji": "💡",
     "criteria": "Идея, мысль, наблюдение, творческая находка, план на будущее без конкретного срока."},
    {"id": "link", "title": "Ссылки и статьи", "emoji": "🔗",
     "criteria": "URL, статья, пост или рекомендация почитать/посмотреть что-то."},
    {"id": "event", "title": "События", "emoji": "📅",
     "criteria": "Событие с датой или временем: встреча, кино, день рождения, поездка."},
    {"id": "purchase", "title": "Покупки", "emoji": "🛒",
     "criteria": "Купить, заказать, цена, товар, чек, оплата."},
    {"id": "study", "title": "Учёба", "emoji": "📚",
     "criteria": "Конспект, домашнее задание, урок, курс, экзамен, определение, формула."},
    {"id": "media", "title": "Медиа", "emoji": "🎞",
     "criteria": "Фото, видео, музыка, фильм, сериал, трек — рекомендация или сохранённый файл."},
    {"id": "other", "title": "Прочее", "emoji": "📦",
     "criteria": "Всё остальное, что не подходит ни под одну категорию."},
]


@dataclass
class Config:
    bot_token: str = BOT_TOKEN
    openrouter_key: str = OPENROUTER_KEY
    classifier_mode: str = "hybrid"          # local | openrouter | hybrid
    classifier_model: str = DECIDER_MODELS[0]
    classifier_fallback_model: str = DECIDER_MODELS[1]
    confidence_threshold: float = 0.45       # ниже — запрос к OpenRouter (в hybrid)
    answer_model: str = "openrouter/free"    # «нахождение инфы»
    max_context_notes: int = 6
    search_limit: int = 5
    digest_enabled: bool = False
    digest_time: str = "21:00"
    reminder_enabled: bool = True
    autostart_bot: bool = True
    categories: list[dict[str, str]] = field(default_factory=lambda: [dict(c) for c in DEFAULT_CATEGORIES])
    admin_user_ids: list[int] = field(default_factory=list)

    def category(self, cat_id: str) -> dict[str, str] | None:
        for c in self.categories:
            if c["id"] == cat_id:
                return c
        return None

    def category_title(self, cat_id: str) -> str:
        c = self.category(cat_id)
        return f"{c['emoji']} {c['title']}" if c else "📦 Прочее"

    def to_dict(self) -> dict[str, Any]:
        return {
            "bot_token": self.bot_token,
            "openrouter_key": self.openrouter_key,
            "classifier": {
                "mode": self.classifier_mode,
                "model": self.classifier_model,
                "fallback_model": self.classifier_fallback_model,
                "confidence_threshold": self.confidence_threshold,
            },
            "ai": {
                "answer_model": self.answer_model,
                "max_context_notes": self.max_context_notes,
                "digest_enabled": self.digest_enabled,
                "digest_time": self.digest_time,
                "reminder_enabled": self.reminder_enabled,
            },
            "search": {"limit": self.search_limit},
            "autostart_bot": self.autostart_bot,
            "admin_user_ids": self.admin_user_ids,
            "categories": self.categories,
        }

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        cfg = cls()
        if not path.exists():
            cfg.save(path)
            return cfg
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return cfg
        cfg.bot_token = raw.get("bot_token") or cfg.bot_token
        cfg.openrouter_key = raw.get("openrouter_key") or cfg.openrouter_key
        cl = raw.get("classifier") or {}
        cfg.classifier_mode = cl.get("mode", cfg.classifier_mode)
        cfg.classifier_model = cl.get("model", cfg.classifier_model)
        cfg.classifier_fallback_model = cl.get("fallback_model", cfg.classifier_fallback_model)
        cfg.confidence_threshold = float(cl.get("confidence_threshold", cfg.confidence_threshold))
        ai = raw.get("ai") or {}
        cfg.answer_model = ai.get("answer_model", cfg.answer_model)
        cfg.max_context_notes = int(ai.get("max_context_notes", cfg.max_context_notes))
        cfg.digest_enabled = bool(ai.get("digest_enabled", cfg.digest_enabled))
        cfg.digest_time = ai.get("digest_time", cfg.digest_time)
        cfg.reminder_enabled = bool(ai.get("reminder_enabled", cfg.reminder_enabled))
        cfg.search_limit = int((raw.get("search") or {}).get("limit", cfg.search_limit))
        cfg.autostart_bot = bool(raw.get("autostart_bot", cfg.autostart_bot))
        cfg.admin_user_ids = list(raw.get("admin_user_ids") or [])
        cats = raw.get("categories")
        if isinstance(cats, list) and cats:
            cfg.categories = [c for c in cats if isinstance(c, dict) and "id" in c and "title" in c]
        return cfg


_lock = threading.Lock()


def save_config(cfg: Config) -> None:
    with _lock:
        cfg.save()


def load_config() -> Config:
    with _lock:
        return Config.load()
