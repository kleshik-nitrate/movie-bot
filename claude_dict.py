"""Грамматическая карточка рус->болг через Claude API (Anthropic SDK).

Существительное -> ед. + мн. число с ударением.
Глагол -> несвършен + свършен вид.
Прилагательное/прочее -> начальная форма (с ударением).

Ударение ставится знаком U+0301 (комбинируемое акутное) сразу после ударной гласной.
Нужен ANTHROPIC_API_KEY в окружении. Если ключа нет или сервис недоступен — вернёт None.
"""

import asyncio
import json
import logging
import os

import anthropic

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"

_SYSTEM = (
    "Ты — болгарско-русский словарь и эксперт по болгарской грамматике. "
    "На вход подаётся ОДНО слово на русском ИЛИ на болгарском языке "
    "(регистр и заглавная буква не важны).\n\n"
    "ШАГ 1. Определи язык ввода source_lang: 'ru' — русское слово, 'bg' — "
    "болгарское слово, 'other' — только если это вообще не слово (набор букв). "
    "Реальное слово ВСЕГДА классифицируй как 'ru' или 'bg', даже если сомневаешься.\n"
    "ШАГ 2. russian — русское слово в начальной форме: если ввод русский — он сам; "
    "если болгарский — его перевод на русский.\n"
    "ШАГ 3. Болгарский перевод с грамматикой:\n"
    "  - Существительное: ед. (singular) и мн. (plural) число.\n"
    "  - Глагол: несвършен (imperfective) и свършен (perfective) вид.\n"
    "  - Прилагательное и прочее: начальная форма (base).\n\n"
    "ВАЖНО про болгарские глаголы (частая ошибка — подмешать русский!):\n"
    "  - Болгарский глагол в словарной форме (1 л. ед. ч. наст. вр.) оканчивается "
    "на -м, -я или -а: да́вам, че́та, спя, оти́да, предпочи́там.\n"
    "  - Болгарские глаголы НИКОГДА не оканчиваются на -ть, -у, -ю (это русский!). "
    "Например рус. «предпочитать» → болг. предпочи́там (несв.) / предпочета́ (св.); "
    "НЕ «предпочту».\n"
    "  - Свършен вид тоже болгарский (напр. оти́да, ку́пя, предпочета́), не русский.\n\n"
    "Во ВСЕХ болгарских словах ставь ударение знаком U+0301 (комбинируемое "
    "акутное) сразу после ударной гласной.\n"
    "Заполняй только подходящие поля, остальные — пустая строка. "
    "source_lang='other' ставь ТОЛЬКО для не-слов.\n\n"
    "Примеры:\n"
    "  «счётчик» → ru, russian=счётчик, noun, singular=броя́ч, plural=броя́чи\n"
    "  «брояч» → bg, russian=счётчик, noun, singular=броя́ч, plural=броя́чи\n"
    "  «плешив» → bg, russian=лысый, adjective, base=плеши́в\n"
    "  «предпочитать» → ru, russian=предпочитать, verb, "
    "imperfective=предпочи́там, perfective=предпочета́"
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "source_lang": {"type": "string", "enum": ["ru", "bg", "other"]},
        "russian": {"type": "string", "description": "русское слово (начальная форма)"},
        "pos": {"type": "string", "enum": ["noun", "verb", "adjective", "other"]},
        "singular": {"type": "string", "description": "ед. число (существительное)"},
        "plural": {"type": "string", "description": "мн. число (существительное)"},
        "imperfective": {"type": "string", "description": "несвършен вид (глагол)"},
        "perfective": {"type": "string", "description": "свършен вид (глагол)"},
        "base": {"type": "string", "description": "начальная форма (прочее)"},
    },
    "required": [
        "source_lang", "russian", "pos",
        "singular", "plural", "imperfective", "perfective", "base",
    ],
    "additionalProperties": False,
}


def _format(card: dict) -> str:
    """Собирает читаемую строку перевода из полей схемы."""
    pos = card.get("pos")
    if pos == "noun":
        sg, pl = card.get("singular", "").strip(), card.get("plural", "").strip()
        if sg and pl:
            return f"{sg} (ед.) / {pl} (мн.)"
        return sg or pl
    if pos == "verb":
        impf = card.get("imperfective", "").strip()
        pf = card.get("perfective", "").strip()
        parts = []
        if impf:
            parts.append(f"{impf} (несв.)")
        if pf:
            parts.append(f"{pf} (св.)")
        return " / ".join(parts)
    return card.get("base", "").strip()


def _analyze_sync(word: str) -> dict | None:
    client = anthropic.Anthropic()  # читает ANTHROPIC_API_KEY из окружения
    resp = client.messages.create(
        model=MODEL,
        max_tokens=400,
        system=_SYSTEM,
        messages=[{"role": "user", "content": word.strip()}],
        output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
    )
    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        logger.warning("analyze(%r): пустой ответ модели", word)
        return None
    card = json.loads(text)
    bg = _format(card)
    ru = card.get("russian", "").strip()
    lang = card.get("source_lang", "other")
    logger.info("analyze(%r) -> lang=%s ru=%r bg=%r", word, lang, ru, bg)
    if lang == "other" or not bg or not ru:
        return None
    return {"lang": lang, "ru": ru, "bg": bg}


async def analyze(word: str) -> dict | None:
    """Разбирает слово (рус. или болг.).

    Возвращает {'lang': 'ru'|'bg', 'ru': русское слово, 'bg': карточка}
    либо None при ошибке/без ключа/неразборчивом вводе.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        # SDK синхронный — уводим в отдельный поток, чтобы не блокировать бота.
        return await asyncio.to_thread(_analyze_sync, word)
    except Exception:
        logger.exception("analyze(%r) упал", word)
        return None


# --- Фразы / пословицы ---------------------------------------------------

_PHRASE_SYSTEM = (
    "Ты — болгарско-русский переводчик фраз и пословиц. На вход — фраза на "
    "русском ИЛИ болгарском.\n"
    "1. source_lang: 'ru' или 'bg' (для реальной фразы; 'other' — только для "
    "бессмыслицы).\n"
    "2. russian — фраза на русском (естественный перевод, если ввод болгарский).\n"
    "3. bulgarian — фраза на болгарском. В КАЖДОМ болгарском слове поставь "
    "ударение знаком U+0301 (комбинируемое акутное) сразу после ударной гласной. "
    "Не путай с русским: болгарские слова пиши по-болгарски.\n"
    "Пример: «Мне не хватает пива» → russian='Мне не хватает пива', "
    "bulgarian='Ли́псва ми би́ра'."
)

_PHRASE_SCHEMA = {
    "type": "object",
    "properties": {
        "source_lang": {"type": "string", "enum": ["ru", "bg", "other"]},
        "russian": {"type": "string"},
        "bulgarian": {"type": "string"},
    },
    "required": ["source_lang", "russian", "bulgarian"],
    "additionalProperties": False,
}


def _phrase_sync(text: str) -> dict | None:
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=500,
        system=_PHRASE_SYSTEM,
        messages=[{"role": "user", "content": text.strip()}],
        output_config={"format": {"type": "json_schema", "schema": _PHRASE_SCHEMA}},
    )
    out = next((b.text for b in resp.content if b.type == "text"), None)
    if not out:
        return None
    card = json.loads(out)
    ru = card.get("russian", "").strip()
    bg = card.get("bulgarian", "").strip()
    lang = card.get("source_lang", "other")
    logger.info("phrase(%r) -> lang=%s ru=%r bg=%r", text, lang, ru, bg)
    if lang == "other" or not ru or not bg:
        return None
    return {"lang": lang, "ru": ru, "bg": bg}


async def translate_phrase(text: str) -> dict | None:
    """Переводит фразу (рус.<->болг.) с ударениями в болгарской части."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        return await asyncio.to_thread(_phrase_sync, text)
    except Exception:
        logger.exception("translate_phrase(%r) упал", text)
        return None
