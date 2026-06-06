import asyncio
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart

TELEGRAM_TOKEN = "8817836406:AAH8Lo5YahTpaS8-PWodecoSbMabGYzuXco"
KINOPOISK_API_KEY = "MPB6XPE-HWH48B6-KVMA1PT-EMP1DWN"
KINOPOISK_API_URL = "https://api.kinopoisk.dev/v1.4/movie/search"

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()


async def search_movie(query: str) -> dict | None:
    params = {"query": query, "limit": 1}
    headers = {"X-API-KEY": KINOPOISK_API_KEY}
    connector = aiohttp.TCPConnector(ssl=False)

    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        async with session.get(KINOPOISK_API_URL, params=params, allow_redirects=True) as resp:
            if resp.status != 200:
                print(f"API error: {resp.status}")
                return None
            data = await resp.json(content_type=None)
            docs = data.get("docs", [])
            return docs[0] if docs else None


def format_rating(value) -> str:
    if value is None or value == 0:
        return "—"
    return f"{value:.1f}"


def format_movie(movie: dict) -> str:
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

    return (
        f"🎬 <b>{name_ru}</b>\n"
        f"🌍 <i>{name_orig}</i>\n\n"
        f"📅 Год: {year}\n"
        f"⭐ Кинопоиск: {kp_rating}\n"
        f"🎥 IMDb: {imdb_rating}\n"
        f"🔗 <a href='{kp_link}'>Открыть на Кинопоиске</a>\n\n"
        f"📝 {description}"
    )


@dp.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я помогу найти информацию о фильме.\n\n"
        "Просто напиши название фильма — на русском или на английском."
    )


@dp.message(F.text)
async def handle_movie_search(message: Message):
    query = message.text.strip()
    await message.answer("🔍 Ищу...")

    movie = await search_movie(query)

    if not movie:
        await message.answer("❌ Фильм не найден. Попробуйте уточнить название.")
        return

    text = format_movie(movie)
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=False)


async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
