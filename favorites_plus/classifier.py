"""Классификатор «Избранное+».

Три режима:
- local       — лёгкая локальная модель TF-IDF + LogisticRegression (scikit-learn),
                обучается на встроенных сид-примерах и правках пользователя.
- openrouter  — бесплатные decision-модели OpenRouter через /api/alpha/decisions
                (choice-вопрос с калиброванными вероятностями).
- hybrid      — локальная модель отвечает первой; если её уверенность ниже порога,
                запрос уходит в OpenRouter (фолбэк-цепочка моделей).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from .config import Config, DEFAULT_CATEGORIES
from .utils import looks_like_secret

log = logging.getLogger("favorites.classifier")

# слова, по которым пользовательская категория считается «для ключей/токенов»
_SECRET_CATEGORY_KEYWORDS = ("api", "key", "token", "пароль", "ключ", "токен", "секрет",
                             "password", "secret", "apikey")


def secret_category_id(cfg: Config) -> str | None:
    """Категория пользователя, подходящая для API-ключей/токенов (по названию/описанию)."""
    for c in cfg.categories:
        blob = f"{c.get('title', '')} {c.get('criteria', '')}".lower()
        if any(k in blob for k in _SECRET_CATEGORY_KEYWORDS):
            return c["id"]
    return None

# ---------- сид-примеры для локальной модели ----------

SEED_DATA: dict[str, list[str]] = {
    "task": [
        "купить молоко и хлеб после работы",
        "не забыть позвонить маме вечером",
        "отправить отчёт до пятницы",
        "записаться к стоматологу на следующей неделе",
        "сделать домашку по математике к среде",
        "перезвонить в банк завтра утром",
        "убраться в комнате в субботу",
        "оплатить интернет до 10 числа",
        "вернуть книгу в библиотеку",
        "написать куратору насчёт проекта",
        "подготовиться к созвону в 15:00",
        "не забыть взять charger в поездку",
    ],
    "idea": [
        "придумал: можно сделать бота для напоминаний с ML",
        "интересная мысль про приложение для трекинга привычек",
        "а что если добавить голосовой ввод в заметки",
        "идея для подарка другу — механическая клавиатура",
        "мысль: изучить rust для пет-проекта",
        "хочу сделать свой сайт-портфолио",
        "придумал название для команды — «Мыслехват»",
        "можно автоматизировать отчёты через python",
        "идея: вести дневник успехов каждый вечер",
        "мысль о открытии блога про разработку",
        "концепция умного будильника по фазам сна",
    ],
    "link": [
        "https://habr.com/ru/articles/123456 — крутая статья про python",
        "посмотри это видео про нейросети www.youtube.com/watch?v=abc",
        "скинул ссылку на документацию aiogram https://docs.aiogram.dev",
        "пост в тг канале про стажировки: t.me/jobs/123",
        "вот репозиторий на github.com/user/project",
        "статья про векторные базы данных на хабре",
        "сохрани ссылку на курс по машинному обучению",
        "https://openrouter.ai/docs — документация openrouter",
        "рецепт блинов нашёл в интернете ссылка",
        "подкаст про разработку послушать позже",
    ],
    "event": [
        "в пятницу иду в кино в 19:00 с друзьями",
        "день рождения ани 14 марта",
        "встреча с командой во вторник в 18:30",
        "концерт 20 числа в 20:00",
        "созвон с заказчиком завтра в 11:00",
        "поездка в санкт-петербург 5.06",
        "экзамен по физике 12 января",
        "родительское собрание в четверг",
        "перелёт 23.04 в 6 утра",
        "свадьба сестры в сентябре",
    ],
    "purchase": [
        "купить наушники sony за 15000 рублей",
        "заказать пиццу на ужин",
        "купить подарок сестре на др",
        "цена на телефон упала до 40000 ₽",
        "нужно купить курсор и мышку",
        "оплатить подписку на spotify",
        "хочу заказать кроссовки nike 45 размер",
        "купить продукты: яйца, молоко, сыр",
        "заплатить за квартиру 32000 рублей",
        "скидка на ноутбук в днс — взять?",
    ],
    "study": [
        "конспект лекции по линейной алгебре: матрицы, определители",
        "дз по физике: задачи 4.1 и 4.2, сдать до пятницы",
        "формула байеса для наивного классификатора",
        "определение рекурсии из лекции по программированию",
        "готовиться к экзамену по истории",
        "курс по нейронным сетям — урок 2: обратное распространение",
        "методический материал по русскому языку",
        "теорема пифагора доказательство",
        "вопрос на зачёте: что такое инкапсуляция",
        "яндекс лицей: модуль по python, итоговый проект",
    ],
    "media": [
        "сохранил фото доски с конспектом",
        "скриншот ошибки компиляции",
        "видео с котиком скачать потом",
        "посмотреть сериал «чернобыль» вечером",
        "новый альбом скриптонита вышел",
        "картинка с мемом про программистов",
        "голосовое сообщение от друга",
        "трек для тренировки плейлист",
        "фильм интерстеллар пересмотреть",
        "фото меню ресторана сохраните",
    ],
    "other": [
        "пароль от вайфая: qwerty123456",
        "адрес доставки: улица ленина 5 квартира 12",
        "номер заказа 4839201",
        "код от домофона 123К",
        "инн организации 7701234567",
        "серия и номер паспорта 45 10 №123456",
        "диапазон роста 170-175 см",
        "розетка находится за диваном",
        "квартира на пятом этаже",
        "починить кран в ванной",
        "sk-or-v1-abcdef123456ghijklmnop",
        "wx2CFwDl4Qukgk0QVtuz4HH6Gtfg",
        "ghp_xKq9vT3bN7mZ2aQwErTyUiOpAs",
        "7f3c9d2e81ab45f6b0c8d7e9a1f2345b",
    ],
}


@dataclass
class ClassifyResult:
    category: str
    confidence: float
    source: str  # local | openrouter
    detail: str = ""
    uncertain: bool = False  # уверенность слишком низкая — показываем выбор категории


class LocalClassifier:
    """Лёгкая локальная модель: TF-IDF (1-2 граммы) + LogisticRegression."""

    def __init__(self) -> None:
        self.pipeline: Pipeline | None = None
        self.categories: list[str] = []

    def train(self, categories: list[dict[str, str]], corrections: list[tuple[str, str]]) -> None:
        cat_ids = [c["id"] for c in categories]
        default_title = {c["id"]: c["title"] for c in DEFAULT_CATEGORIES}
        texts: list[str] = []
        labels: list[str] = []
        for c in categories:
            cid = c["id"]
            # сиды даём только категориям со «штатным» смыслом: если пользователь
            # переименовал категорию (например, «Задачи» → «api»), старые примеры
            # учили бы модель неправильному
            if default_title.get(cid) != c.get("title"):
                continue
            for t in SEED_DATA.get(cid, SEED_DATA["other"]):
                texts.append(t)
                labels.append(cid)
        for text, cat in corrections:
            cat_ids_ok = cat if cat in cat_ids else "other"
            texts.append(text)
            labels.append(cat_ids_ok)

        self.pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True,
                                      min_df=1, max_features=20000)),
            ("clf", LogisticRegression(C=6.0, max_iter=400)),
        ])
        self.pipeline.fit(texts, labels)
        self.categories = cat_ids

    def predict(self, text: str) -> ClassifyResult | None:
        if self.pipeline is None or not text.strip():
            return None
        probs = self.pipeline.predict_proba([text])[0]
        idx = int(np.argmax(probs))
        cat = self.pipeline.classes_[idx]
        return ClassifyResult(category=cat, confidence=float(probs[idx]), source="local")


class OpenRouterClassifier:
    """Decision-модели OpenRouter: POST /api/alpha/decisions."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._url = "https://openrouter.ai/api/alpha/decisions"

    def _questions_choice(self, categories: list[dict[str, str]]) -> dict:
        criteria = {c["id"]: c.get("criteria") or c["title"] for c in categories}
        return {
            "category": {
                "type": "choice",
                "instructions": "К какой категории относится заметка пользователя?",
                "criteria": criteria,
            }
        }

    def _questions_noul(self, categories: list[dict[str, str]]) -> dict:
        # span-01-lite принимает только noul: по одному бинарному вопросу на категорию
        qs: dict = {}
        for c in categories:
            qs[f"cat_{c['id']}"] = {
                "type": "noul",
                "instructions": f"Является ли заметка именно «{c['title']}»? {c.get('criteria', '')}",
                "criteria": {"true": "да, точно эта категория", "false": "нет, другая категория"},
            }
        return qs

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.cfg.openrouter_key}",
            "Content-Type": "application/json",
        }

    async def _decide(self, model: str, text: str, categories: list[dict[str, str]]) -> dict:
        noul_mode = "span-01" in model
        body = {
            "model": model,
            "state": f"Заметка пользователя: {text[:1500]}",
            "questions": self._questions_noul(categories) if noul_mode else self._questions_choice(categories),
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(25.0)) as client:
            r = await client.post(self._url, headers=self._headers(), json=body)
            if r.status_code in (429, 500, 502, 503):
                await asyncio.sleep(1.2)
                r = await client.post(self._url, headers=self._headers(), json=body)
            r.raise_for_status()
            return r.json()

    @staticmethod
    def _parse_choice(data: dict, valid_ids: set[str]) -> ClassifyResult:
        ans = (data.get("answers") or {}).get("category") or {}
        if ans.get("type") == "refusal" or not ans:
            raise ValueError("model refusal")
        cat = ans.get("choice")
        if cat not in valid_ids:  # модель вернула несуществующую категорию
            raise ValueError(f"unknown category from model: {cat}")
        probs = ans.get("probabilities") or {}
        conf = float(ans.get("confidence") or probs.get(cat) or 0.0)
        return ClassifyResult(category=str(cat), confidence=conf, source="openrouter",
                              detail=f"probabilities={probs}")

    @staticmethod
    def _parse_noul(data: dict, categories: list[dict[str, str]]) -> ClassifyResult:
        answers = data.get("answers") or {}
        best, best_p = None, -1.0
        for c in categories:
            a = answers.get(f"cat_{c['id']}")
            if not a or a.get("type") == "refusal":
                continue
            p = float(a.get("noul") or 0.0)
            if p > best_p:
                best, best_p = c["id"], p
        if best is None:
            raise ValueError("no noul answers")
        return ClassifyResult(category=best, confidence=min(1.0, best_p), source="openrouter",
                              detail="noul-per-category")

    async def classify(self, text: str, categories: list[dict[str, str]]) -> ClassifyResult | None:
        chain = [self.cfg.classifier_model]
        if self.cfg.classifier_fallback_model and self.cfg.classifier_fallback_model != self.cfg.classifier_model:
            chain.append(self.cfg.classifier_fallback_model)
        for model in chain:
            t0 = time.monotonic()
            try:
                data = await self._decide(model, text, categories)
                valid_ids = {c["id"] for c in categories}
                result = (self._parse_noul(data, categories) if "span-01" in model
                          else self._parse_choice(data, valid_ids))
                log.info("openrouter classify ok model=%s %.2fs conf=%.2f cat=%s",
                         model, time.monotonic() - t0, result.confidence, result.category)
                return result
            except Exception as e:  # noqa: BLE001 — сеть/лимиты/чужая категория: идём дальше
                log.warning("openrouter classify failed model=%s: %s", model, e)
        return None


class HybridClassifier:
    """Комбинирует локальную модель и OpenRouter по режиму из конфига."""

    def __init__(self, cfg: Config, storage=None) -> None:
        self.cfg = cfg
        self.local = LocalClassifier()
        self.remote = OpenRouterClassifier(cfg)
        self.storage = storage
        self.retrain()

    def retrain(self) -> None:
        corrections = self.storage.corrections() if self.storage is not None else []
        try:
            self.local.train(self.cfg.categories, corrections)
            log.info("local classifier trained: %d corrections", len(corrections))
        except Exception as e:  # noqa: BLE001
            log.exception("local train failed: %s", e)

    async def classify(self, text: str) -> ClassifyResult:
        mode = self.cfg.classifier_mode
        cats = self.cfg.categories

        # Быстрый путь: ключи/токены не гоняем через модели — уверенно и мгновенно
        secret_cat = secret_category_id(self.cfg)
        if looks_like_secret(text) and secret_cat:
            log.info("secret fast-path -> %s", secret_cat)
            return ClassifyResult(secret_cat, 0.92, "local", "secret fast-path")

        local = self.local.predict(text)

        if mode == "local":
            return self._finalize(local or ClassifyResult("other", 0.2, "local"))
        if mode == "openrouter":
            remote = await self._remote(text, cats)
            if remote:
                return self._finalize(remote)
            return self._finalize(local or ClassifyResult("other", 0.2, "local"))

        # hybrid: локальная уверенность → порог → OpenRouter; слабый ответ модели
        # не принимаем как истину — возвращаемся к локальной или к «не уверен»
        if local and local.confidence >= self.cfg.confidence_threshold:
            return self._finalize(local)
        remote = await self._remote(text, cats)
        if remote and remote.confidence >= self.cfg.confidence_threshold:
            return self._finalize(remote)
        return self._finalize(local or ClassifyResult("other", 0.2, "local"))

    @staticmethod
    def _finalize(result: ClassifyResult) -> ClassifyResult:
        if result.confidence < 0.35:
            result.uncertain = True
        return result

    async def _remote(self, text: str, cats: list[dict[str, str]]) -> ClassifyResult | None:
        if not self.cfg.openrouter_key:
            return None
        try:
            return await asyncio.wait_for(self.remote.classify(text, cats), timeout=30.0)
        except asyncio.TimeoutError:
            log.warning("openrouter classify timeout")
            return None
