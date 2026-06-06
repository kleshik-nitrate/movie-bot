import asyncio
import base64
import os
import aiohttp
from anthropic import AsyncAnthropic
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.filters import CommandStart

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8817836406:AAH8Lo5YahTpaS8-PWodecoSbMabGYzuXco")
KINOPOISK_API_KEY = os.environ.get("KINOPOISK_API_KEY", "MPB6XPE-HWH48B6-KVMA1PT-EMP1DWN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "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")
KINOPOISK_API_URL = "https://api.kinopoisk.dev/v1.4/movie/search"

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
anthropic_client = AsyncAnthropic(api_key=ANTHROPIC_API_KEY)


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
                        "text": "На этом изображении есть название фильма или постер фильма. Извлеки только название фильма. Ответь только названием фильма, ничего лишнего. Если названия фильма нет — ответь NONE.",
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

    if not movie:
        await message.answer("❌ Фильм не найден в базе.")
        return

    text = format_movie(movie)
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=False)


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
