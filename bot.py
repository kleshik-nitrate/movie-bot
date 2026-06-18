"""Телеграм-бот для заучивания болгарских слов.

Два режима:
  1) Пополнение словаря — добавляешь русское слово, бот предлагает болгарский
     перевод, ты сохраняешь или правишь.
  2) Повторение — бот показывает русское слово, ты вспоминаешь перевод,
     открываешь его и отмечаешь, вспомнил или нет. Работает интервальное
     повторение: за сессию показывается до 10 слов.
"""

import logging
import os
import random

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

import db
from claude_dict import analyze, translate_phrase
from dictionary import lookup

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

WORDS_PER_SESSION = 10

# Состояния диалога добавления слова
ADD_WAIT_RU, ADD_CONFIRM, ADD_WAIT_BG = range(3)

# Состояния диалога добавления фразы
ADDP_WAIT, ADDP_CONFIRM, ADDP_WAIT_BG = range(3, 6)

# Кнопки главного меню
BTN_ADD = "➕ Слово"
BTN_PHRASE = "➕ Фраза"
BTN_REVIEW = "🔁 Повторять"
BTN_STATS = "📊 Статистика"
BTN_BASIC = "📥 Базовые слова"
BTN_WORDS = "📝 Мои слова"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [[BTN_ADD, BTN_PHRASE], [BTN_REVIEW, BTN_WORDS], [BTN_STATS, BTN_BASIC]],
    resize_keyboard=True,
)

WORDS_PER_PAGE = 8

# Состояния диалога редактирования словаря
WORDS_BROWSE, WORDS_EDIT_RU, WORDS_EDIT_BG, WORDS_SEARCH = range(10, 14)

# Состояния режима повторения
REVIEW_Q, REVIEW_GRADE = range(20, 22)

# Регэксп кнопок меню — чтобы выходить из диалогов по нажатию меню.
MENU_RE = (
    f"^({BTN_ADD}|{BTN_PHRASE}|{BTN_REVIEW}|{BTN_STATS}|{BTN_BASIC}|{BTN_WORDS})$"
)


# ---------------------------------------------------------------------------
# Главное меню
# ---------------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    db.ensure_user(user_id)

    # Новому пользователю сразу выдаём базовый набор A1-A2.
    seeded_note = ""
    if db.count_words(user_id) == 0:
        added, _ = db.seed_words(user_id)
        seeded_note = f"\n📥 Загрузил {added} базовых слов A1–A2 — можно сразу повторять!\n"

    await update.message.reply_text(
        "Здравей! 🇧🇬\n\n"
        "Я помогу заучивать болгарские слова.\n"
        f"{seeded_note}\n"
        f"{BTN_ADD} — добавить слово (рус. или болг.).\n"
        f"{BTN_PHRASE} — добавить фразу/пословицу (учатся с пропуском слова).\n"
        f"{BTN_REVIEW} — повторить (до {WORDS_PER_SESSION} карточек за раз).\n"
        f"{BTN_WORDS} — список, поиск, правка, удаление.\n"
        f"{BTN_STATS} — прогресс.\n\n"
        "Выбери действие на клавиатуре ниже.",
        reply_markup=MAIN_KEYBOARD,
    )


async def load_basic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # overwrite=True — обновляет карточки уже добавленных базовых слов до полных.
    added, updated = db.seed_words(update.effective_user.id, overwrite=True)
    if added or updated:
        text = (
            f"Готово ✅\nДобавлено новых: {added}\n"
            f"Обновлено карточек (ед./мн., виды): {updated}"
        )
    else:
        text = "Все базовые слова уже актуальны 👍"
    await update.message.reply_text(text, reply_markup=MAIN_KEYBOARD)


async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = db.stats(update.effective_user.id)
    await update.message.reply_text(
        f"📊 Твоя коллекция: {s['total']} карточек\n\n"
        f"• Базовые слова: {s['seed']}\n"
        f"• Добавлено пользователем: {s['user']}\n"
        f"• Фразы: {s['phrases']}\n"
        f"• К повторению сейчас: {s['due']}",
        reply_markup=MAIN_KEYBOARD,
    )


# ---------------------------------------------------------------------------
# Режим 1: пополнение словаря (ConversationHandler)
# ---------------------------------------------------------------------------
_CANCEL_KB = InlineKeyboardMarkup(
    [[InlineKeyboardButton("✖️ Отмена", callback_data="add_cancel")]]
)


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Напиши слово, которое хочешь выучить — на русском или болгарском:",
        reply_markup=_CANCEL_KB,
    )
    return ADD_WAIT_RU


async def add_received_ru(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    word = update.message.text.strip()
    user_id = update.effective_user.id

    # Ранняя проверка (экономит запрос к API при повторном вводе того же слова).
    if db.word_exists(user_id, word):
        await update.message.reply_text(
            f"Слово «{word}» уже есть в словаре. Напиши другое:",
            reply_markup=_CANCEL_KB,
        )
        return ADD_WAIT_RU

    await update.message.reply_text("Собираю карточку… ⏳")

    # Claude: определяет язык ввода (рус./болг.), даёт русскую форму и карточку.
    res = await analyze(word)

    ru = bg = None
    note = None
    if res:
        ru, bg = res["ru"], res["bg"]
        if res["lang"] == "bg":
            note = f"Похоже, это болгарское слово «{word}». Русский: {ru}."
        else:
            note = "Карточка с грамматикой (ед./мн. число, виды глагола, ударения)."
    else:
        # Запасной вариант (нет ключа / сбой) — слово считаем русским.
        ru = word
        found = await lookup(word)
        if found["variants"]:
            bg = found["variants"][0]
            note = (
                "Из словаря Wiktionary."
                if found["source"] == "dict"
                else "Автоперевод (проверь!)."
            )

    # Проверка дубля по русской форме.
    if ru and db.word_exists(user_id, ru):
        await update.message.reply_text(
            f"Слово «{ru}» уже есть в словаре. Напиши другое:",
            reply_markup=_CANCEL_KB,
        )
        return ADD_WAIT_RU

    context.user_data["add_ru"] = ru or word
    context.user_data["add_bg"] = bg or ""

    if bg:
        text = f"🇷🇺 {ru}\n🇧🇬 {bg}\n\n{note}\nСохранить или поправить?"
        markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("✅ Сохранить", callback_data="add_save")],
                [
                    InlineKeyboardButton("✏️ Исправить", callback_data="add_edit"),
                    InlineKeyboardButton("✖️ Отмена", callback_data="add_cancel"),
                ],
            ]
        )
        await update.message.reply_text(text, reply_markup=markup)
        return ADD_CONFIRM

    # Перевод не нашли — просим ввести вручную (отдельное состояние).
    await update.message.reply_text(
        f"🇷🇺 {ru}\n\nНе нашёл перевод. Напиши болгарский вариант вручную:",
        reply_markup=_CANCEL_KB,
    )
    return ADD_WAIT_BG


async def add_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    ru = context.user_data.get("add_ru", "").strip()
    bg = context.user_data.get("add_bg", "").strip()

    if not ru or not bg:
        await query.edit_message_text(
            "Карточка неполная. Нажми «➕ Слово» и попробуй снова."
        )
        context.user_data.clear()
        return ConversationHandler.END

    db.add_word(update.effective_user.id, ru, bg)
    await query.edit_message_text(f"Сохранено ✅\n\n🇷🇺 {ru}\n🇧🇬 {bg}")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Добавь ещё слово или вернись в меню.",
        reply_markup=MAIN_KEYBOARD,
    )
    context.user_data.clear()
    return ConversationHandler.END


async def add_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"🇷🇺 {context.user_data.get('add_ru', '')}\n\n"
        "Напиши болгарский перевод вручную "
        "(для глаголов обе формы через запятую):"
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Жду перевод:",
        reply_markup=_CANCEL_KB,
    )
    return ADD_WAIT_BG


async def add_received_bg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ru = context.user_data.get("add_ru", "").strip()
    bg = update.message.text.strip()
    if not bg:
        await update.message.reply_text(
            "Перевод пустой. Напиши болгарский вариант:", reply_markup=_CANCEL_KB
        )
        return ADD_WAIT_BG

    db.add_word(update.effective_user.id, ru, bg)
    await update.message.reply_text(
        f"Сохранено ✅\n\n🇷🇺 {ru}\n🇧🇬 {bg}",
        reply_markup=MAIN_KEYBOARD,
    )
    context.user_data.clear()
    return ConversationHandler.END


async def add_confirm_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Любой текст на шаге подтверждения — подсказываем пользоваться кнопками.
    await update.message.reply_text(
        "Нажми «✅ Сохранить», «✏️ Исправить» или «✖️ Отмена»."
    )
    return ADD_CONFIRM


async def add_cancel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Отменено.")
    context.user_data.clear()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Можно добавить другое слово или вернуться в меню.",
        reply_markup=MAIN_KEYBOARD,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Пополнение фразами (пословицы, устойчивые выражения)
# ---------------------------------------------------------------------------
async def addp_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "Напиши фразу или пословицу — на русском или болгарском:",
        reply_markup=_CANCEL_KB,
    )
    return ADDP_WAIT


async def addp_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    user_id = update.effective_user.id

    if db.word_exists(user_id, text):
        await update.message.reply_text(
            "Такая фраза уже есть. Напиши другую:", reply_markup=_CANCEL_KB
        )
        return ADDP_WAIT

    await update.message.reply_text("Перевожу фразу… ⏳")
    res = await translate_phrase(text)

    if not res:
        # Без Claude фразу не перевести — просим ввести вторую сторону вручную.
        context.user_data["addp_ru"] = text
        context.user_data["addp_bg"] = ""
        await update.message.reply_text(
            "Не удалось перевести автоматически.\n"
            "Напиши болгарский вариант фразы (с ударениями, если знаешь):",
            reply_markup=_CANCEL_KB,
        )
        return ADDP_WAIT_BG

    ru, bg = res["ru"], res["bg"]
    if db.word_exists(user_id, ru):
        await update.message.reply_text(
            "Такая фраза уже есть. Напиши другую:", reply_markup=_CANCEL_KB
        )
        return ADDP_WAIT

    context.user_data["addp_ru"] = ru
    context.user_data["addp_bg"] = bg
    note = (
        f"Похоже, это болгарская фраза. Русский: {ru}."
        if res["lang"] == "bg"
        else "Перевод с ударениями."
    )
    markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("✅ Сохранить", callback_data="addp_save")],
            [
                InlineKeyboardButton("✏️ Исправить", callback_data="addp_edit"),
                InlineKeyboardButton("✖️ Отмена", callback_data="add_cancel"),
            ],
        ]
    )
    await update.message.reply_text(
        f"🇷🇺 {ru}\n🇧🇬 {bg}\n\n{note}\nСохранить или поправить?", reply_markup=markup
    )
    return ADDP_CONFIRM


async def addp_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    ru = context.user_data.get("addp_ru", "").strip()
    bg = context.user_data.get("addp_bg", "").strip()
    if not ru or not bg:
        await query.edit_message_text(
            "Фраза неполная. Нажми «➕ Фраза» и попробуй снова."
        )
        context.user_data.clear()
        return ConversationHandler.END

    db.add_word(update.effective_user.id, ru, bg, kind="phrase")
    await query.edit_message_text(f"Сохранено ✅\n\n🇷🇺 {ru}\n🇧🇬 {bg}")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Добавь ещё фразу или вернись в меню.",
        reply_markup=MAIN_KEYBOARD,
    )
    context.user_data.clear()
    return ConversationHandler.END


async def addp_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(f"🇷🇺 {context.user_data.get('addp_ru', '')}")
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Напиши болгарскую фразу вручную (с ударениями):",
        reply_markup=_CANCEL_KB,
    )
    return ADDP_WAIT_BG


async def addp_received_bg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    ru = context.user_data.get("addp_ru", "").strip()
    bg = update.message.text.strip()
    if not bg:
        await update.message.reply_text(
            "Пусто. Напиши болгарскую фразу:", reply_markup=_CANCEL_KB
        )
        return ADDP_WAIT_BG
    db.add_word(update.effective_user.id, ru, bg, kind="phrase")
    await update.message.reply_text(
        f"Сохранено ✅\n\n🇷🇺 {ru}\n🇧🇬 {bg}", reply_markup=MAIN_KEYBOARD
    )
    context.user_data.clear()
    return ConversationHandler.END


async def addp_confirm_hint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Нажми «✅ Сохранить», «✏️ Исправить» или «✖️ Отмена»."
    )
    return ADDP_CONFIRM


# ---------------------------------------------------------------------------
# Режим 2: повторение
#   - направление случайное: рус->болг или болг->рус;
#   - базовые слова: «показать → помню/не помню»;
#   - твои слова: вперемешку печать ответа или «показать»;
#   - при печати: точное совпадение (без ударений/регистра) — сразу зачёт,
#     иначе бот показывает правильный ответ и спрашивает «Засчитать?».
#   - для глаголов печатается только первая форма, после ответа видны обе.
# ---------------------------------------------------------------------------
def _strip_stress(s: str) -> str:
    return s.replace("́", "")


def _primary(card: str) -> str:
    """Первая форма из карточки: 'да́вам (несв.) / дам (св.)' -> 'да́вам'."""
    first = card.split(" / ")[0]
    return first.split(" (")[0].strip()


def _norm(s: str) -> str:
    """Нормализация для сравнения: без ударений, регистра и лишних пробелов."""
    s = _strip_stress(s).lower().strip().strip(".!?,;")
    return " ".join(s.split())


_PUNCT = ".,!?;:«»\"'()—-…"


def _make_cloze(bg: str) -> tuple[str, str]:
    """Прячет одно слово болгарской фразы. Возвращает (фраза с пропуском, ответ)."""
    tokens = bg.split()
    cand = [i for i, t in enumerate(tokens) if any(ch.isalpha() for ch in t)]
    idx = random.choice(cand) if cand else 0
    answer = tokens[idx].strip(_PUNCT)
    shown = list(tokens)
    shown[idx] = "_____"
    return " ".join(shown), answer


async def review_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if db.count_words(user_id) == 0:
        await update.message.reply_text(
            "Пока пусто. Добавь слова («➕ Слово») или фразы («➕ Фраза»).",
            reply_markup=MAIN_KEYBOARD,
        )
        return ConversationHandler.END

    rows = db.start_review_session(user_id, WORDS_PER_SESSION)
    if not rows:
        await update.message.reply_text(
            "На сегодня всё повторено 🎉 Загляни позже или добавь новые слова.",
            reply_markup=MAIN_KEYBOARD,
        )
        return ConversationHandler.END

    context.user_data["queue"] = [dict(r) for r in rows]
    context.user_data["pos"] = 0
    context.user_data["correct"] = 0
    await _ask(update.effective_chat.id, context)
    return REVIEW_Q


async def _ask(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Готовит и отправляет очередной вопрос. Решает направление и режим."""
    queue = context.user_data["queue"]
    pos = context.user_data["pos"]
    word = queue[pos]
    header = f"Карточка {pos + 1} из {len(queue)}"

    # Фраза: показываем болгарскую фразу с пропуском — впиши слово.
    if word.get("kind") == "phrase":
        blanked, answer = _make_cloze(word["bg"])
        context.user_data["q"] = {"mode": "cloze", "answer": answer}
        text = (
            f"{header}  🗣 фраза\n\n{blanked}\n🇷🇺 {word['ru']}\n\n"
            "✍️ Впиши пропущенное слово:"
        )
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🤔 Не помню", callback_data="rev_idk")]]
        )
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)
        return

    direction = random.choice(["ru2bg", "bg2ru"])
    # Печать — только для пользовательских слов и не всегда (вперемешку).
    mode = "type" if (not word["is_seed"] and random.random() < 0.5) else "reveal"

    if direction == "ru2bg":
        shown, ask_lang = f"🇷🇺 {word['ru']}", "болгарский"
    else:
        shown, ask_lang = f"🇧🇬 {_primary(word['bg'])}", "русский"

    context.user_data["q"] = {"direction": direction, "mode": mode}

    if mode == "type":
        text = f"{header}\n\n{shown}\n\n✍️ Напиши перевод на {ask_lang}:"
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🤔 Не помню", callback_data="rev_idk")]]
        )
    else:
        text = f"{header}\n\n{shown}\n\nВспомни перевод…"
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("👁 Показать перевод", callback_data="rev_show")]]
        )
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)


def _full_card(word) -> str:
    return f"🇷🇺 {word['ru']}\n🇧🇬 {word['bg']}"


def _yes_no_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Засчитать", callback_data="rev_yes"),
                InlineKeyboardButton("❌ Не засчитать", callback_data="rev_no"),
            ]
        ]
    )


async def _advance(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Переходит к следующему слову или завершает сессию."""
    context.user_data["pos"] += 1
    queue = context.user_data["queue"]
    if context.user_data["pos"] >= len(queue):
        total = len(queue)
        correct = context.user_data["correct"]
        context.user_data.clear()
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"Сессия завершена! 🎯\n\n"
                f"Правильно: {correct} из {total}\n\n"
                "Что вспомнил — вернётся позже, ошибочное — уже в следующей сессии."
            ),
            reply_markup=MAIN_KEYBOARD,
        )
        return ConversationHandler.END
    await _ask(chat_id, context)
    return REVIEW_Q


def _current_word(context: ContextTypes.DEFAULT_TYPE):
    queue = context.user_data.get("queue")
    if not queue:
        return None
    return queue[context.user_data["pos"]]


async def review_show(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    word = _current_word(context)
    if not word:
        await query.edit_message_text("Сессия завершена. Нажми «🔁 Повторять» снова.")
        return ConversationHandler.END
    await query.edit_message_text(
        f"{_full_card(word)}\n\nВспомнил правильно?", reply_markup=_yes_no_markup()
    )
    return REVIEW_GRADE


async def review_idk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # «Не помню» в режиме печати — показываем ответ и даём оценить.
    return await review_show(update, context)


async def review_typed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    word = _current_word(context)
    q = context.user_data.get("q", {})
    if not word:
        await update.message.reply_text("Сессия завершена. Нажми «🔁 Повторять» снова.")
        return ConversationHandler.END
    mode = q.get("mode")
    if mode not in ("type", "cloze"):
        await update.message.reply_text("Нажми «👁 Показать перевод».")
        return REVIEW_Q

    typed = update.message.text.strip()
    if mode == "cloze":
        expected = q.get("answer", "")
    else:
        expected = (
            _primary(word["bg"]) if q["direction"] == "ru2bg" else _primary(word["ru"])
        )

    if _norm(typed) == _norm(expected):
        db.grade_word(word["id"], True)
        context.user_data["correct"] += 1
        await update.message.reply_text(f"✅ Верно!\n\n{_full_card(word)}")
        return await _advance(update.effective_chat.id, context)

    await update.message.reply_text(
        f"Ты написал: {typed}\n\nПравильно:\n{_full_card(word)}\n\nЗасчитать?",
        reply_markup=_yes_no_markup(),
    )
    return REVIEW_GRADE


async def review_grade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    word = _current_word(context)
    if not word:
        await query.edit_message_text("Сессия завершена. Нажми «🔁 Повторять» снова.")
        return ConversationHandler.END

    remembered = query.data == "rev_yes"
    db.grade_word(word["id"], remembered)
    if remembered:
        context.user_data["correct"] += 1
    mark = "✅" if remembered else "❌"
    await query.edit_message_text(f"{mark} 🇷🇺 {word['ru']} — 🇧🇬 {word['bg']}")
    return await _advance(update.effective_chat.id, context)


async def review_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "⏹ Повторение остановлено. Нажми кнопку ещё раз, чтобы продолжить.",
        reply_markup=MAIN_KEYBOARD,
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Редактирование словаря (📝 Мои слова)
# ---------------------------------------------------------------------------
def _page_markup(user_id: int, page: int) -> tuple[str, InlineKeyboardMarkup]:
    rows, total = db.list_words(user_id, page * WORDS_PER_PAGE, WORDS_PER_PAGE)
    buttons = [
        [InlineKeyboardButton(r["ru"], callback_data=f"w_open:{r['id']}")]
        for r in rows
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"w_page:{page - 1}"))
    if (page + 1) * WORDS_PER_PAGE < total:
        nav.append(InlineKeyboardButton("➡️", callback_data=f"w_page:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append(
        [
            InlineKeyboardButton("🔍 Поиск", callback_data="w_search"),
            InlineKeyboardButton("✖️ Закрыть", callback_data="w_close"),
        ]
    )

    if total == 0:
        text = "Пусто. Добавь слова («➕ Слово») или фразы («➕ Фраза»)."
    else:
        pages = (total + WORDS_PER_PAGE - 1) // WORDS_PER_PAGE
        text = f"📝 Твой словарь: {total} слов. Стр. {page + 1}/{pages}.\nВыбери слово:"
    return text, InlineKeyboardMarkup(buttons)


def _results_markup(rows, total: int) -> tuple[str, InlineKeyboardMarkup]:
    buttons = [
        [InlineKeyboardButton(f"{r['ru']} — {r['bg']}"[:60],
                              callback_data=f"w_open:{r['id']}")]
        for r in rows
    ]
    buttons.append(
        [
            InlineKeyboardButton("🔍 Новый поиск", callback_data="w_search"),
            InlineKeyboardButton("⬅️ К списку", callback_data="w_back"),
        ]
    )
    if not rows:
        text = "Ничего не найдено. Попробуй другой запрос."
    elif total > len(rows):
        text = f"Найдено {total}, показаны первые {len(rows)}. Уточни запрос:"
    else:
        text = f"Найдено: {total}. Выбери слово:"
    return text, InlineKeyboardMarkup(buttons)


def _card_markup(word_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✏️ Русское", callback_data=f"w_editru:{word_id}"),
                InlineKeyboardButton("✏️ Перевод", callback_data=f"w_editbg:{word_id}"),
            ],
            [InlineKeyboardButton("🔄 Пересобрать (Claude)", callback_data=f"w_rebuild:{word_id}")],
            [InlineKeyboardButton("🗑 Удалить", callback_data=f"w_del:{word_id}")],
            [InlineKeyboardButton("⬅️ К списку", callback_data="w_back")],
        ]
    )


def _card_text(row) -> str:
    return f"🇷🇺 {row['ru']}\n🇧🇬 {row['bg']}"


async def words_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["w_page"] = 0
    text, markup = _page_markup(update.effective_user.id, 0)
    await update.message.reply_text(text, reply_markup=markup)
    return WORDS_BROWSE


async def words_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    context.user_data["w_page"] = page
    text, markup = _page_markup(update.effective_user.id, page)
    await query.edit_message_text(text, reply_markup=markup)
    return WORDS_BROWSE


async def words_open(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    word_id = int(query.data.split(":")[1])
    row = db.get_word(update.effective_user.id, word_id)
    if not row:
        await query.edit_message_text("Слово не найдено.")
        return WORDS_BROWSE
    context.user_data["w_id"] = word_id
    await query.edit_message_text(_card_text(row), reply_markup=_card_markup(word_id))
    return WORDS_BROWSE


async def words_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    page = context.user_data.get("w_page", 0)
    text, markup = _page_markup(update.effective_user.id, page)
    await query.edit_message_text(text, reply_markup=markup)
    return WORDS_BROWSE


async def words_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Закрыто.")
    return ConversationHandler.END


async def words_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔍 Напиши слово (на русском или болгарском) для поиска:"
    )
    return WORDS_SEARCH


async def words_search_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    rows, total = db.search_words(update.effective_user.id, update.message.text)
    text, markup = _results_markup(rows, total)
    await update.message.reply_text(text, reply_markup=markup)
    return WORDS_BROWSE


async def words_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    word_id = int(query.data.split(":")[1])
    db.delete_word(update.effective_user.id, word_id)
    page = context.user_data.get("w_page", 0)
    text, markup = _page_markup(update.effective_user.id, page)
    await query.edit_message_text("Удалено 🗑\n\n" + text, reply_markup=markup)
    return WORDS_BROWSE


async def words_rebuild(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    word_id = int(query.data.split(":")[1])
    row = db.get_word(update.effective_user.id, word_id)
    if not row:
        await query.edit_message_text("Слово не найдено.")
        return WORDS_BROWSE
    await query.edit_message_text(f"🇷🇺 {row['ru']}\nПересобираю карточку… ⏳")
    if row["kind"] == "phrase":
        res = await translate_phrase(row["ru"])
    else:
        res = await analyze(row["ru"])
    if res and res["bg"]:
        db.update_word(update.effective_user.id, word_id, bg=res["bg"])
        row = db.get_word(update.effective_user.id, word_id)
        note = "Карточка пересобрана ✅"
    else:
        note = "Не удалось пересобрать (нет ключа/сбой). Карточка без изменений."
    await query.edit_message_text(
        f"{note}\n\n{_card_text(row)}", reply_markup=_card_markup(word_id)
    )
    return WORDS_BROWSE


async def words_edit_ru_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["w_id"] = int(query.data.split(":")[1])
    await query.edit_message_text("Напиши новое русское слово (или /cancel):")
    return WORDS_EDIT_RU


async def words_edit_bg_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data["w_id"] = int(query.data.split(":")[1])
    await query.edit_message_text("Напиши новый болгарский перевод (или /cancel):")
    return WORDS_EDIT_BG


async def words_edit_ru_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    word_id = context.user_data.get("w_id")
    db.update_word(update.effective_user.id, word_id, ru=update.message.text)
    row = db.get_word(update.effective_user.id, word_id)
    await update.message.reply_text(
        "Обновлено ✅\n\n" + _card_text(row), reply_markup=_card_markup(word_id)
    )
    return WORDS_BROWSE


async def words_edit_bg_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    word_id = context.user_data.get("w_id")
    db.update_word(update.effective_user.id, word_id, bg=update.message.text)
    row = db.get_word(update.effective_user.id, word_id)
    await update.message.reply_text(
        "Обновлено ✅\n\n" + _card_text(row), reply_markup=_card_markup(word_id)
    )
    return WORDS_BROWSE


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------
def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Не задан BOT_TOKEN. Получи токен у @BotFather и запусти:\n"
            "  BOT_TOKEN=твой_токен python bot.py"
        )

    db.init_db()
    app = Application.builder().token(token).build()

    _free_text = filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_RE)
    add_conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            MessageHandler(filters.Regex(f"^{BTN_ADD}$"), add_start),
        ],
        states={
            ADD_WAIT_RU: [
                CallbackQueryHandler(add_cancel_cb, pattern="^add_cancel$"),
                MessageHandler(_free_text, add_received_ru),
            ],
            ADD_CONFIRM: [
                CallbackQueryHandler(add_save, pattern="^add_save$"),
                CallbackQueryHandler(add_edit, pattern="^add_edit$"),
                CallbackQueryHandler(add_cancel_cb, pattern="^add_cancel$"),
                MessageHandler(_free_text, add_confirm_hint),
            ],
            ADD_WAIT_BG: [
                CallbackQueryHandler(add_cancel_cb, pattern="^add_cancel$"),
                MessageHandler(_free_text, add_received_bg),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex(MENU_RE), cancel),
        ],
    )

    addp_conv = ConversationHandler(
        entry_points=[
            CommandHandler("phrase", addp_start),
            MessageHandler(filters.Regex(f"^{BTN_PHRASE}$"), addp_start),
        ],
        states={
            ADDP_WAIT: [
                CallbackQueryHandler(add_cancel_cb, pattern="^add_cancel$"),
                MessageHandler(_free_text, addp_received),
            ],
            ADDP_CONFIRM: [
                CallbackQueryHandler(addp_save, pattern="^addp_save$"),
                CallbackQueryHandler(addp_edit, pattern="^addp_edit$"),
                CallbackQueryHandler(add_cancel_cb, pattern="^add_cancel$"),
                MessageHandler(_free_text, addp_confirm_hint),
            ],
            ADDP_WAIT_BG: [
                CallbackQueryHandler(add_cancel_cb, pattern="^add_cancel$"),
                MessageHandler(_free_text, addp_received_bg),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex(MENU_RE), cancel),
        ],
    )

    words_conv = ConversationHandler(
        entry_points=[
            CommandHandler("words", words_start),
            MessageHandler(filters.Regex(f"^{BTN_WORDS}$"), words_start),
        ],
        states={
            WORDS_BROWSE: [
                CallbackQueryHandler(words_page, pattern=r"^w_page:\d+$"),
                CallbackQueryHandler(words_open, pattern=r"^w_open:\d+$"),
                CallbackQueryHandler(words_back, pattern="^w_back$"),
                CallbackQueryHandler(words_close, pattern="^w_close$"),
                CallbackQueryHandler(words_delete, pattern=r"^w_del:\d+$"),
                CallbackQueryHandler(words_rebuild, pattern=r"^w_rebuild:\d+$"),
                CallbackQueryHandler(words_edit_ru_start, pattern=r"^w_editru:\d+$"),
                CallbackQueryHandler(words_edit_bg_start, pattern=r"^w_editbg:\d+$"),
                CallbackQueryHandler(words_search_start, pattern="^w_search$"),
            ],
            WORDS_SEARCH: [
                MessageHandler(_free_text, words_search_run),
            ],
            WORDS_EDIT_RU: [
                MessageHandler(_free_text, words_edit_ru_save)
            ],
            WORDS_EDIT_BG: [
                MessageHandler(_free_text, words_edit_bg_save)
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Regex(MENU_RE), cancel),
        ],
    )

    review_conv = ConversationHandler(
        entry_points=[
            CommandHandler("review", review_start),
            MessageHandler(filters.Regex(f"^{BTN_REVIEW}$"), review_start),
        ],
        states={
            REVIEW_Q: [
                CallbackQueryHandler(review_show, pattern="^rev_show$"),
                CallbackQueryHandler(review_idk, pattern="^rev_idk$"),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & ~filters.Regex(MENU_RE),
                    review_typed,
                ),
            ],
            REVIEW_GRADE: [
                CallbackQueryHandler(review_grade, pattern="^rev_(yes|no)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", review_stop),
            MessageHandler(filters.Regex(MENU_RE), review_stop),
        ],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(add_conv)
    app.add_handler(addp_conv)
    app.add_handler(words_conv)
    app.add_handler(review_conv)
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_STATS}$"), show_stats))
    app.add_handler(CommandHandler("stats", show_stats))
    app.add_handler(MessageHandler(filters.Regex(f"^{BTN_BASIC}$"), load_basic))
    app.add_handler(CommandHandler("loadbasic", load_basic))

    logger.info("Бот запущен.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
