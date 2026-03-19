# Деплой MemeBot на Vercel

## Передумови

- Акаунт на [Vercel](https://vercel.com)
- Встановлений [Vercel CLI](https://vercel.com/docs/cli): `npm i -g vercel`
- Telegram-бот створений через [@BotFather](https://t.me/BotFather)
- Бот доданий як адміністратор каналу (з правом постити)

---

## Крок 1: Створити проєкт на Vercel

```bash
cd MemeBot
vercel link
```

Або прив'яжіть Git-репозиторій через Vercel Dashboard.

## Крок 2: Додати Vercel KV (Redis)

1. Відкрийте Vercel Dashboard → ваш проєкт → **Storage**
2. Натисніть **Create** → **KV (Upstash Redis)**
3. Оберіть безкоштовний план і створіть store
4. Прив'яжіть store до проєкту

Vercel автоматично додасть змінні `KV_REST_API_URL` і `KV_REST_API_TOKEN`.

## Крок 3: Додати змінні оточення

В Vercel Dashboard → **Settings** → **Environment Variables** додайте:

| Змінна | Значення |
|--------|----------|
| `BOT_TOKEN` | Токен бота від BotFather |
| `ADMIN_ID` | Ваш Telegram user ID (число) |
| `CHANNEL_ID` | ID каналу (напр. `-1001234567890`) |
| `WEBHOOK_SECRET` | Випадковий рядок для захисту webhook |

## Крок 4: Деплой

```bash
vercel --prod
```

Або просто push в main — Vercel задеплоїть автоматично.

Запам'ятайте URL деплою (напр. `https://meme-bot-xyz.vercel.app`).

## Крок 5: Зареєструвати Webhook

```bash
VERCEL_URL=https://meme-bot-xyz.vercel.app \
BOT_TOKEN=your-token \
WEBHOOK_SECRET=your-secret \
python scripts/set_webhook.py
```

Має вивести: `Webhook set successfully: https://meme-bot-xyz.vercel.app/api/webhook`

## Крок 6: Перевірити

1. Надішліть боту фото, відео або текст у приватні повідомлення
2. Бот має відповісти: «Фото додано. Заплановано на DD.MM о HH:MM»
3. Надішліть `/stats` — побачите список запланованих постів
4. У вказаний час пост автоматично з'явиться в каналі

---

## Команди бота

| Команда | Опис |
|---------|------|
| `/stats` | Показати заплановані пости |
| `/clear` | Очистити всі заплановані слоти |

## Обмеження

- **Максимум 3 пости на день** (за київським часом)
- **Робочі години:** 09:00–22:00
- **Інтервал між постами:** 3–5 годин (рандомізовано)
- **Скасування:** вже відправлені в Telegram заплановані пости неможливо скасувати через Bot API. `/clear` очищує лише внутрішній лічильник слотів.
