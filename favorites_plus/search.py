"""Гибридный поиск «Избранное+»: BM25 (FTS5) + TF-IDF косинусная близость.

Семантический (векторный) поиск поверх лёгкого TF-IDF работает локально и мгновенно,
BM25 даёт точные совпадения слов. Итоговый ранг — взвешенная сумма нормированных.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .storage import Storage

log = logging.getLogger("favorites.search")


def _tokens(s: str) -> list[str]:
    return [t for t in re.split(r"[^\w]+", s.lower().replace("ё", "е"), flags=re.UNICODE) if len(t) > 1]

# Лёгкий стемминг для морфологии русского: обрезаем частые окончания.
# Точного снежного кома не нужно — слой ранжирования, FTS даёт точность.
_SUFFIXES = sorted([
    "ами", "ями", "ому", "ему", "ого", "его", "ыми", "ими", "ает", "уют", "яют",
    "ой", "ей", "ий", "ый", "ая", "ое", "ые", "ие", "ах", "ях", "ам", "ям", "ов",
    "ев", "ий", "сь", "ся", "ет", "ут", "ют", "ат", "ят", "ла", "ло", "ли", "ны",
    "ть", "м", "х", "й", "а", "я", "о", "е", "у", "ю", "ы", "и",
], key=len, reverse=True)


def _stem(t: str) -> str:
    for suf in _SUFFIXES:
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            return t[: len(t) - len(suf)]
    return t


def _stems(s: str) -> list[str]:
    return [_stem(t) for t in _tokens(s)]


def hybrid_search(storage: Storage, user_id: int, query: str, limit: int = 5,
                  category_titles: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Возвращает до limit заметок, отсортированных по смешанному рангу.

    category_titles — соответствие id категории → человеческое название (для
    поиска «api» по категории «api», даже если в текстах заметок слова нет).
    """
    query = (query or "").strip()
    if not query:
        return []
    notes = storage.list_notes(user_id, limit=500)
    if not notes:
        return []
    by_id = {n["id"]: n for n in notes}

    # 1) BM25-совпадения через FTS5 (или LIKE-фолбэк в хранилище)
    fts_hits = dict(storage.fts_search(query, user_id, limit=25))
    if not fts_hits:  # возможно, FTS не индексировал: прямой прогон по заметкам
        fts_hits = _plain_match(notes, query)

    # 2) TF-IDF косинусная близость (векторный поиск)
    vec_scores = _tfidf_scores(notes, query)

    # 3) нормировка по порядку (меньший BM25-ранг = лучше) и слияние
    ids_best_first = [nid for nid, _rank in sorted(fts_hits.items(), key=lambda kv: kv[1])]
    denom = max(1, len(ids_best_first) - 1)
    fts_norm = {nid: 1.0 - i / denom for i, nid in enumerate(ids_best_first)}
    vec_max = max(vec_scores.values()) if vec_scores else 0.0
    vec_norm = {nid: s / vec_max for nid, s in vec_scores.items()} if vec_max > 0 else {}

    score: dict[int, float] = {}
    for nid in set(fts_norm) | set(vec_norm):
        score[nid] = 0.6 * fts_norm.get(nid, 0.0) + 0.4 * vec_norm.get(nid, 0.0)

    # 3.5) слой опечаток/раскладки: «маргут» ≈ "MargyT" — char-ngram по транслиту,
    # включается только когда словесные слои ничего не дали
    if not score:
        char_scores = _char_scores(notes, query)
        cmax = max(char_scores.values()) if char_scores else 0.0
        if cmax > 0:
            score = {nid: 0.75 * (s / cmax) for nid, s in char_scores.items() if s / cmax > 0.25}

    # 4) буст за совпадение запроса с названием категории или тегом заметки
    q_stems = set(_stems(query))
    for n in notes:
        title = (category_titles or {}).get(n.get("category") or "", "")
        hay = _stems(title) + [str(t).lower() for t in (n.get("tags") or [])]
        if q_stems & set(hay):
            score[n["id"]] = score.get(n["id"], 0.0) + 0.5

    ranked = sorted(score.items(), key=lambda kv: kv[1], reverse=True)[:limit]
    results = []
    for nid, s in ranked:
        note = by_id.get(nid)
        if note and s > 0.01:
            note = dict(note)
            note["score"] = round(s, 3)
            results.append(note)
    return results


async def llm_pick_relevant(cfg, question: str, notes: list[dict[str, Any]],
                            limit: int = 6) -> list[int]:
    """Семантический выбор заметок моделью: возвращает список id (или [] при сбое).

    Fallback, когда лексический/векторный поиск ничего не дал: тексты заметок
    не обязаны делить слова с вопросом («опенроутер апи» ↔ строка ключа).
    """
    import html as h

    from .llm import chat
    if not notes:
        return []
    listing = "\n".join(
        f"[{n['id']}] {h.escape((n.get('text') or '').strip()[:200])}" for n in notes[:120])
    messages = [
        {"role": "system",
         "content": "Ты помогаешь искать по личным заметкам. Верни ТОЛЬКО JSON-массив "
                    "идентификаторов релевантных заметок, например [8, 3]. Без пояснений. "
                    "Если ничего не подходит — верни []."},
        {"role": "user",
         "content": f"Запрос: {question}\n\nЗаметки (id: текст):\n{listing}"},
    ]
    try:
        # reasoning-модели тратят токены на размышления: лимит 200 → пустой content
        raw = await chat(cfg, messages, max_tokens=900, timeout=45.0, retries=1)
        ids = [int(x) for x in re.findall(r"\d+", raw)]
        valid = {n["id"] for n in notes}
        return [i for i in ids if i in valid][:limit]
    except Exception as e:  # noqa: BLE001
        log.warning("llm_pick_relevant failed: %s", e)
        return []


def _plain_match(notes: list[dict[str, Any]], query: str) -> dict[int, float]:
    qt = set(_stems(query))
    hits: dict[int, float] = {}
    for n in notes:
        nt = set(_stems(n.get("text") or ""))
        if not nt:
            continue
        inter = len(qt & nt)
        if inter:
            hits[n["id"]] = float(inter)
    return hits


def _char_scores(notes: list[dict[str, Any]], query: str) -> dict[int, float]:
    """Нечёткий слой: char 3-4 граммы по транслитерации (опечатки, раскладка, «маргут»)."""
    from sklearn.feature_extraction.text import TfidfVectorizer

    from .utils import translit

    texts = [translit((n.get("text") or "").lower()) for n in notes]
    q = translit(query.lower())
    if not q.strip() or not any(t.strip() for t in texts):
        return {}
    try:
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4),
                              sublinear_tf=True, max_features=40000)
        matrix = vec.fit_transform(texts)
        sims = (matrix @ vec.transform([q]).T).toarray().ravel()
    except ValueError:
        return {}
    return {n["id"]: float(s) for n, s in zip(notes, sims) if s > 0.05}


def _tfidf_scores(notes: list[dict[str, Any]], query: str) -> dict[int, float]:
    from sklearn.feature_extraction.text import TfidfVectorizer

    texts = [" ".join(_stems(n.get("text") or "")) for n in notes]
    if not any(t.strip() for t in texts):
        return {}
    try:
        vec = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True, max_features=20000)
        matrix = vec.fit_transform(texts)
        q = vec.transform([" ".join(_stems(query))])
        # косинус по строкам
        sims = (matrix @ q.T).toarray().ravel()
    except ValueError:
        return {}
    return {n["id"]: float(s) for n, s in zip(notes, sims) if s > 0}
