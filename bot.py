import asyncio
import base64
import os
import aiohttp
from anthropic import AsyncAnthropic
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
KINOPOISK_API_KEY = os.environ["KINOPOISK_API_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
KINOPOISK_API_URL = "https://api.kinopoisk.dev/v1.4/movie/search"
KINOPOISK_DETAIL_URL = "https://api.kinopoisk.dev/v1.4/movie/{}"

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
anthropic_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

# Известные престижные премии
MAJOR_AWARDS = {"Оскар", "Золотой глобус", "BAFTA", "Эмми", "Канны", "Берлинале", "Венеция"}


async def search_movie(query: str) -> dict | None | bool:
    """
    Возвращает:
      dict  — фильм найден
      None  — фильм не найден
      False — сервис недоступен
    """
    params = {"query": query, "limit": 1}
    headers = {"X-API-KEY": KINOPOISK_API_KEY}
    connector = aiohttp.TCPConnector(ssl=False)

    try:
        async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
            async with session.get(
                KINOPOISK_API_URL, params=params, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status in (502, 503, 504):
                    print(f"Kinopoisk unavailable: {resp.status}")
                    return False
                if resp.status != 200:
                    print(f"API error: {resp.status}")
                    return False
                data = await resp.json(content_type=None)
                docs = data.get("docs", [])
                return docs[0] if docs else None
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        print(f"Connection error: {e}")
        return False


async def get_movie_awards(movie_id: int) -> list:
    headers = {"X-API-KEY": KINOPOISK_API_KEY}
    connector = aiohttp.TCPConnector(ssl=False)
    url = KINOPOISK_DETAIL_URL.format(movie_id)

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
            return data.get("awards", [])


def get_hashtags(movie: dict) -> str:
    genres = {g.get("name", "").lower() for g in movie.get("genres", [])}
    age_rating = movie.get("ageRating") or 0
    mpaa = (movie.get("ratingMpaa") or "").lower()
    name = (movie.get("name") or "").lower()
    alt_name = (movie.get("alternativeName") or "").lower()
    description = ((movie.get("shortDescription") or "") + " " + (movie.get("description") or "")).lower()

    hashtags = []

    # #новыйгод — новогодние и рождественские фильмы
    ny_keywords = [
        "рождество", "новый год", "новогодн", "christmas", "xmas",
        "santa", "санта", "holiday", "эльф", "elf", "гринч", "grinch",
        "декабрь", "снегурочка", "дед мороз",
    ]
    if any(kw in name or kw in alt_name or kw in description for kw in ny_keywords):
        hashtags.append("#новыйгод")

    # #комедия
    if "комедия" in genres:
        hashtags.append("#комедия")

    # #криминал — боевики, триллеры, детективы
    if genres & {"криминал", "боевик", "триллер", "детектив", "военный"}:
        hashtags.append("#криминал")

    # #семейный — мультфильмы, детские, семейные
    family_genres = {"мультфильм", "семейный", "анимация", "для детей"}
    if (genres & family_genres) or (age_rating <= 12 and age_rating > 0):
        hashtags.append("#семейный")

    # #бездетей — взрослые фильмы
    adult_genres = {"эротика", "аниме"}
    if age_rating >= 18 or mpaa in ("r", "nc-17") or (genres & adult_genres):
        hashtags.append("#бездетей")

    return " ".join(hashtags)


def format_awards(awards: list) -> str:
    if not awards:
        return ""

    # Группируем по названию премии
    grouped: dict[str, dict] = {}
    for award in awards:
        nomination = award.get("nomination", {})
        award_info = nomination.get("award", {})
        title = award_info.get("title", "")
        year = award_info.get("year", "")
        won = award.get("winning", False)
        nom_title = nomination.get("title", "")

        key = f"{title} ({year})" if year else title
        if key not in grouped:
            grouped[key] = {"wins": [], "nominations": []}

        if won:
            grouped[key]["wins"].append(nom_title)
        else:
            grouped[key]["nominations"].append(nom_title)

    # Сортируем: сначала с победами, потом только номинации
    # Показываем только крупные премии или первые 3
    lines = []
    shown = 0
    for award_name, data in grouped.items():
        if shown >= 4:
            break
        wins = data["wins"]
        noms = data["nominations"]

        line = f"🏆 <b>{award_name}</b>"
        if wins:
            wins_str = ", ".join(wins[:2])
            if len(wins) > 2:
                wins_str += f" +{len(wins) - 2}"
            line += f"\n   ✅ Победа: {wins_str}"
        if noms:
            noms_str = ", ".join(noms[:2])
            if len(noms) > 2:
                noms_str += f" +{len(noms) - 2}"
            line += f"\n   📋 Номинации: {noms_str}"

        lines.append(line)
        shown += 1

    if not lines:
        return ""

    return "\n🏅 <b>Награды:</b>\n" + "\n".join(lines)


async def extract_movie_from_image(photo_bytes: bytes) -> str | None:
    image_data = base64.standard_b64encode(photo_bytes).decode("utf-8")

    message = await anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=100,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Определи название фильма на этом изображении. Это может быть: постер фильма с текстом, кадр из фильма, скриншот сцены. Используй все визуальные подсказки: актёры, костюмы, декорации, стиль, эпоха, текст если есть. Ответь только названием фильма, ничего лишнего. Если не можешь определить фильм — ответь NONE.",
                    },
                ],
            }
        ],
    )

    result = message.content[0].text.strip()
    return None if result == "NONE" else result


def format_rating(value) -> str:
    if value is None or value == 0:
        return "—"
    return f"{value:.1f}"


async def build_movie_text(movie: dict) -> str:
    name_ru = movie.get("name") or "—"
    name_orig = movie.get("alternativeName") or movie.get("enName") or "—"
    year = movie.get("year") or "—"

    rating = movie.get("rating", {})
    kp_rating = format_rating(rating.get("kp"))
    imdb_rating = format_rating(rating.get("imdb"))

    movie_id = movie.get("id")
    kp_link = f"https://www.kinopoisk.ru/film/{movie_id}/" if movie_id else "—"

    description = movie.get("shortDescription") or movie.get("description") or "Описание недоступно."
    if len(description) > 500:
        description = description[:500].rstrip() + "..."

    # Получаем награды
    awards_text = ""
    if movie_id:
        awards = await get_movie_awards(movie_id)
        awards_text = format_awards(awards)

    # Хэштеги
    hashtags = get_hashtags(movie)
    hashtags_text = f"\n\n{hashtags}" if hashtags else ""

    return (
        f"🎬 <b>{name_ru}</b>\n"
        f"🌍 <i>{name_orig}</i>\n\n"
        f"📅 Год: {year}\n"
        f"⭐ Кинопоиск: {kp_rating}\n"
        f"🎥 IMDb: {imdb_rating}\n"
        f"🔗 <a href='{kp_link}'>Открыть на Кинопоиске</a>\n\n"
        f"📝 {description}"
        f"{awards_text}"
        f"{hashtags_text}"
    )


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я помогу найти информацию о фильме.\n\n"
        "Напиши название фильма или пришли картинку/постер — я всё найду."
    )


@dp.message(F.photo)
async def handle_photo(message: Message):
    await message.answer("🖼 Анализирую изображение...")

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)

    async with aiohttp.ClientSession() as session:
        url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file.file_path}"
        async with session.get(url) as resp:
            photo_bytes = await resp.read()

    movie_name = await extract_movie_from_image(photo_bytes)

    if not movie_name:
        await message.answer("❌ Не удалось определить название фильма на изображении.")
        return

    await message.answer(f"🔍 Нашёл на картинке: <b>{movie_name}</b>\nИщу информацию...", parse_mode="HTML")

    movie = await search_movie(movie_name)

    if movie is False:
        await message.answer("⚠️ Кинопоиск сейчас недоступен. Попробуйте чуть позже.")
        return
    if movie is None:
        await message.answer("❌ Фильм не найден в базе.")
        return

    text = await build_movie_text(movie)
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=False)


@dp.message(F.text)
async def handle_movie_search(message: Message):
    query = message.text.strip()
    await message.answer("🔍 Ищу...")

    movie = await search_movie(query)

    if movie is False:
        await message.answer("⚠️ Кинопоиск сейчас недоступен. Попробуйте чуть позже.")
        return
    if movie is None:
        await message.answer("❌ Фильм не найден. Попробуйте уточнить название.")
        return

    text = await build_movie_text(movie)
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=False)


async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
