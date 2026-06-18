"""SQLite-хранилище: пользователи, слова, прогресс интервального повторения."""

import os
import sqlite3
import time
from pathlib import Path

# Путь к базе можно задать переменной окружения DB_PATH (нужно для Docker/volume).
# По умолчанию — файл vocab.db рядом с ботом.
DB_PATH = Path(os.environ.get("DB_PATH", Path(__file__).parent / "vocab.db"))

# Интервалы интервального повторения (Leitner).
# Значение — на сколько СЕССИЙ повторения слово прячется после правильного ответа.
# Индекс — уровень слова (level). Чем выше уровень, тем реже показываем.
INTERVALS = [1, 2, 5, 10, 20, 45]

# Базовые слова (is_seed=1) — фоновый приоритет: при правильном ответе
# прячутся в этот множитель дольше обычных, чтобы всплывать реже.
SEED_KNOWN_FACTOR = 3


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Создаёт таблицы при первом запуске."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id         INTEGER PRIMARY KEY,
                session_counter INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS words (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                ru         TEXT    NOT NULL,
                bg         TEXT    NOT NULL,
                level      INTEGER NOT NULL DEFAULT 0,
                next_due   INTEGER NOT NULL DEFAULT 0,
                is_seed    INTEGER NOT NULL DEFAULT 0,
                kind       TEXT    NOT NULL DEFAULT 'word',
                created_at INTEGER NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE INDEX IF NOT EXISTS idx_words_due
                ON words(user_id, next_due);
            """
        )
        # Миграции для баз, созданных до появления новых столбцов.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(words)")}
        if "is_seed" not in cols:
            conn.execute(
                "ALTER TABLE words ADD COLUMN is_seed INTEGER NOT NULL DEFAULT 0"
            )
        if "kind" not in cols:
            conn.execute(
                "ALTER TABLE words ADD COLUMN kind TEXT NOT NULL DEFAULT 'word'"
            )


def ensure_user(user_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id) VALUES (?)", (user_id,)
        )


def add_word(user_id: int, ru: str, bg: str, kind: str = "word") -> bool:
    """Добавляет карточку (kind='word' или 'phrase'). False, если сторона пустая.

    Новая карточка сразу доступна к повторению (next_due=0).
    """
    if not ru or not ru.strip() or not bg or not bg.strip():
        return False
    ensure_user(user_id)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO words(user_id, ru, bg, level, next_due, kind, created_at)
            VALUES (?, ?, ?, 0, 0, ?, ?)
            """,
            (user_id, ru.strip(), bg.strip(), kind, int(time.time())),
        )
    return True


def seed_words(user_id: int, overwrite: bool = False) -> tuple[int, int]:
    """Загружает базовый набор слов. Возвращает (добавлено, обновлено).

    overwrite=False — пропускает уже существующие слова.
    overwrite=True  — обновляет перевод (bg) существующих базовых слов до
                      актуальной карточки, прогресс повторения не трогает.
    """
    from seed_words import SEED_WORDS

    ensure_user(user_id)
    added = updated = 0
    now = int(time.time())
    with _connect() as conn:
        existing = {
            row["ru"].lower(): row["bg"]
            for row in conn.execute(
                "SELECT ru, bg FROM words WHERE user_id = ?", (user_id,)
            ).fetchall()
        }
        for ru, bg in SEED_WORDS:
            key = ru.lower()
            if key in existing:
                if overwrite:
                    # Помечаем как базовое (фоновый приоритет) и обновляем перевод.
                    conn.execute(
                        "UPDATE words SET is_seed = 1 WHERE user_id = ? AND lower(ru) = ?",
                        (user_id, key),
                    )
                    if existing[key] != bg:
                        conn.execute(
                            "UPDATE words SET bg = ? WHERE user_id = ? AND lower(ru) = ?",
                            (bg, user_id, key),
                        )
                        updated += 1
                continue
            conn.execute(
                """
                INSERT INTO words(user_id, ru, bg, level, next_due, is_seed, created_at)
                VALUES (?, ?, ?, 0, 0, 1, ?)
                """,
                (user_id, ru, bg, now),
            )
            existing[key] = bg
            added += 1
    return added, updated


def word_exists(user_id: int, ru: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM words WHERE user_id = ? AND lower(ru) = lower(?)",
            (user_id, ru.strip()),
        ).fetchone()
        return row is not None


def count_words(user_id: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM words WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["c"]


def list_words(user_id: int, offset: int, limit: int) -> tuple[list[sqlite3.Row], int]:
    """Страница слов (по алфавиту рус.) + общее количество."""
    with _connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM words WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        rows = conn.execute(
            """
            SELECT id, ru, bg FROM words
            WHERE user_id = ?
            ORDER BY ru COLLATE NOCASE ASC
            LIMIT ? OFFSET ?
            """,
            (user_id, limit, offset),
        ).fetchall()
        return rows, total


def _norm_search(s: str) -> str:
    return s.replace("́", "").lower()


def search_words(user_id: int, query: str, limit: int = 15
                 ) -> tuple[list[sqlite3.Row], int]:
    """Ищет по русской и болгарской стороне (подстрока, без ударений/регистра).

    Возвращает (найденные строки до limit, всего совпадений).
    """
    q = _norm_search(query.strip())
    if not q:
        return [], 0
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, ru, bg FROM words WHERE user_id = ? ORDER BY ru COLLATE NOCASE",
            (user_id,),
        ).fetchall()
    matched = [
        r for r in rows
        if q in _norm_search(r["ru"]) or q in _norm_search(r["bg"])
    ]
    return matched[:limit], len(matched)


def get_word(user_id: int, word_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT id, ru, bg, kind FROM words WHERE id = ? AND user_id = ?",
            (word_id, user_id),
        ).fetchone()


def update_word(user_id: int, word_id: int, *, ru: str | None = None,
                bg: str | None = None) -> None:
    """Меняет русскую и/или болгарскую сторону карточки. Прогресс не трогает."""
    with _connect() as conn:
        if ru is not None:
            conn.execute(
                "UPDATE words SET ru = ? WHERE id = ? AND user_id = ?",
                (ru.strip(), word_id, user_id),
            )
        if bg is not None:
            conn.execute(
                "UPDATE words SET bg = ? WHERE id = ? AND user_id = ?",
                (bg.strip(), word_id, user_id),
            )


def delete_word(user_id: int, word_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM words WHERE id = ? AND user_id = ?", (word_id, user_id)
        )
        return cur.rowcount > 0


def start_review_session(user_id: int, limit: int = 10) -> list[sqlite3.Row]:
    """Начинает сессию повторения: +1 к счётчику и берёт до `limit` слов к показу.

    Берём слова, у которых next_due <= текущий номер сессии,
    в первую очередь самые «просроченные» и давно добавленные.
    """
    ensure_user(user_id)
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET session_counter = session_counter + 1 WHERE user_id = ?",
            (user_id,),
        )
        session = conn.execute(
            "SELECT session_counter FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()["session_counter"]

        rows = conn.execute(
            """
            SELECT id, ru, bg, level, next_due, is_seed, kind
            FROM words
            WHERE user_id = ? AND next_due <= ?
            ORDER BY next_due ASC, is_seed ASC, RANDOM()
            LIMIT ?
            """,
            (user_id, session, limit),
        ).fetchall()
        return rows


def current_session(user_id: int) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT session_counter FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["session_counter"] if row else 0


def grade_word(word_id: int, remembered: bool) -> None:
    """Обновляет прогресс слова после ответа.

    Правильно  -> уровень растёт, слово прячется на INTERVALS[level] сессий.
                  Базовое слово (is_seed) прячется в SEED_KNOWN_FACTOR раз дольше.
    Неправильно -> уровень сбрасывается, слово вернётся уже в следующей сессии.
    """
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT w.level, w.is_seed, u.session_counter
            FROM words w JOIN users u ON u.user_id = w.user_id
            WHERE w.id = ?
            """,
            (word_id,),
        ).fetchone()
        if row is None:
            return

        session = row["session_counter"]
        if remembered:
            level = min(row["level"] + 1, len(INTERVALS) - 1)
            gap = INTERVALS[level]
            if row["is_seed"]:
                gap *= SEED_KNOWN_FACTOR
        else:
            level = 0
            gap = 1

        conn.execute(
            "UPDATE words SET level = ?, next_due = ? WHERE id = ?",
            (level, session + gap, word_id),
        )


def stats(user_id: int) -> dict:
    with _connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM words WHERE user_id = ?", (user_id,)
        ).fetchone()["c"]
        session = current_session(user_id)
        due = conn.execute(
            "SELECT COUNT(*) AS c FROM words WHERE user_id = ? AND next_due <= ?",
            (user_id, session),
        ).fetchone()["c"]
        seed = conn.execute(
            "SELECT COUNT(*) AS c FROM words WHERE user_id = ? AND is_seed = 1",
            (user_id,),
        ).fetchone()["c"]
        phrases = conn.execute(
            "SELECT COUNT(*) AS c FROM words WHERE user_id = ? AND kind = 'phrase'",
            (user_id,),
        ).fetchone()["c"]
        # Добавленные пользователем слова = всё, кроме базовых и фраз.
        user_words = total - seed - phrases
        return {
            "total": total, "due": due, "seed": seed,
            "phrases": phrases, "user": user_words,
        }
