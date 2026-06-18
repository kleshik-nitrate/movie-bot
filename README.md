# 🎬 MovieBOT

**RU** | [EN](#english)

---

## Русский

Telegram-бот для поиска информации о фильмах. Найдёт фильм по названию, по постеру или по кадру из фильма.

### Возможности

- 🔤 Поиск по названию — на русском или английском, с опечатками
- 🖼 Распознавание постеров и кадров из фильмов (через Claude Vision AI)
- 📋 Выбор из нескольких результатов, если найдено несколько фильмов
- ⭐ Рейтинг Кинопоиска и IMDb
- 🏆 Награды и номинации (Оскар, Золотой глобус и др.)
- 📝 Краткое описание фильма
- 🔗 Ссылка на страницу фильма на Кинопоиске
- 🏷 Автоматические хэштеги: #комедия #криминал #семейный #бездетей #новыйгод
- 👥 Работает в групповых чатах

### Как запустить локально

1. Установите зависимости:
   ```bash
   pip install aiogram aiohttp anthropic
   ```

2. Создайте файл `.env` или задайте переменные окружения:
   ```
   TELEGRAM_TOKEN=ваш_токен
   KINOPOISK_API_KEY=ваш_ключ
   ANTHROPIC_API_KEY=ваш_ключ
   ```

3. Запустите бота:
   ```bash
   python3 bot.py
   ```

### Деплой на Railway

1. Форкните или загрузите репозиторий на GitHub
2. Создайте проект на [railway.app](https://railway.app) и подключите репозиторий
3. Добавьте переменные окружения в разделе Variables
4. Railway автоматически запустит бота

### Переменные окружения

| Переменная | Где получить |
|-----------|-------------|
| `TELEGRAM_TOKEN` | [@BotFather](https://t.me/BotFather) |
| `KINOPOISK_API_KEY` | [kinopoisk.dev](https://kinopoisk.dev) |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |

---

## English

<a name="english"></a>

A Telegram bot for searching movie information. Finds movies by title, poster image, or scene screenshot.

### Features

- 🔤 Search by title — in Russian or English, typo-tolerant
- 🖼 Movie poster and scene recognition (via Claude Vision AI)
- 📋 Multiple results selection when several movies are found
- ⭐ Kinopoisk and IMDb ratings
- 🏆 Awards and nominations (Oscar, Golden Globe, etc.)
- 📝 Short movie description
- 🔗 Link to the movie page on Kinopoisk
- 🏷 Auto hashtags: #comedy #crime #family #adultsonly #newyear
- 👥 Works in group chats

### Running Locally

1. Install dependencies:
   ```bash
   pip install aiogram aiohttp anthropic
   ```

2. Set environment variables:
   ```
   TELEGRAM_TOKEN=your_token
   KINOPOISK_API_KEY=your_key
   ANTHROPIC_API_KEY=your_key
   ```

3. Run the bot:
   ```bash
   python3 bot.py
   ```

### Deploy to Railway

1. Fork or upload the repository to GitHub
2. Create a project on [railway.app](https://railway.app) and connect the repository
3. Add environment variables in the Variables section
4. Railway will automatically start the bot

### Environment Variables

| Variable | Where to get |
|----------|-------------|
| `TELEGRAM_TOKEN` | [@BotFather](https://t.me/BotFather) |
| `KINOPOISK_API_KEY` | [kinopoisk.dev](https://kinopoisk.dev) |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |

---

Built with [aiogram](https://github.com/aiogram/aiogram), [Kinopoisk API](https://kinopoisk.dev) and [Claude AI](https://anthropic.com)
