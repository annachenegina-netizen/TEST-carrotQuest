# Bridge: Suvvy.ai ⇆ Carrot Quest

Мост между виджетом Carrot Quest на сайте refettorio.ru и ИИ-ботом Suvvy.ai. Схема потока сообщений — в [../docs/integration.md](../docs/integration.md).

## Файлы
- `app.py` — Flask-сервер, два вебхука: `/webhook/carrotquest` и `/webhook/suvvy`
- `carrotquest_client.py` — отправка ответа в диалог Carrot Quest
- `suvvy_client.py` — отправка сообщения посетителя в Suvvy
- `dedup.py` — защита от повторной пересылки при ретраях
- `config.py` — настройки из `.env`
- `test_offline.py` — тесты без реальных аккаунтов (все запросы наружу подменены моками)

## Запуск локально
```
python -m venv venv
venv/Scripts/pip install -r requirements.txt   # или venv/bin/pip на Linux/Mac
cp .env.example .env   # и вписать реальные токены
venv/Scripts/python app.py                     # или venv/bin/python на Linux/Mac
```
Сервер поднимется на `http://localhost:8000` (порт — из `.env`).

## Проверка без реальных доступов
```
venv/Scripts/python test_offline.py
```
Прогоняет все сценарии (успешная пересылка в обе стороны, неверный токен/секрет, дедупликация ретраев) на моках — реальные Carrot Quest/Suvvy не нужны.

## Что нужно для настройки на реальных аккаунтах
1. **Carrot Quest**, админка клиента ЧТТ:
   - Интеграции → новый вебхук на событие "новое сообщение от посетителя" → URL: `https://<хост-моста>/webhook/carrotquest`
   - скопировать токен вебхука → `CARROTQUEST_WEBHOOK_TOKEN`
   - раздел "Разработчикам" → взять `auth_token` приложения → `CARROTQUEST_AUTH_TOKEN`
2. **Suvvy.ai**:
   - Каналы → Персональный канал (API) → указать URL: `https://<хост-моста>/webhook/suvvy`
   - скопировать выданный токен → `SUVVY_API_TOKEN`
   - задать секретное слово → `SUVVY_WEBHOOK_SECRET`
   - выполнить тестовый запрос из интерфейса Suvvy — сервер моста должен ответить 200 (проверено в `test_offline.py`, на реальном хостинге нужно перепроверить вживую)

## Деплой (тест на Vercel)
Для быстрого теста, не дожидаясь решения по постоянному хостингу — деплоим на Vercel. Код уже подготовлен под их serverless-рантайм: `api/index.py` (точка входа) и `vercel.json` (роутинг всех путей на неё), `dedup.py` при деплое на Vercel сам переключается на запись в `/tmp` (обычный диск там read-only).

```
cd bridge
npx vercel login          # откроет браузер для входа/регистрации
npx vercel                # первый деплой — ответить на вопросы по умолчанию (Enter), это создаст проект
```

Дальше вписать переменные окружения (значения — из локального `.env`, руками через дашборд vercel.com → проект → Settings → Environment Variables, или через CLI):
```
npx vercel env add CARROTQUEST_WEBHOOK_TOKEN production
npx vercel env add CARROTQUEST_AUTH_TOKEN production
npx vercel env add SUVVY_API_TOKEN production
npx vercel env add SUVVY_WEBHOOK_SECRET production
npx vercel --prod         # передеплой, чтобы функция подхватила переменные
```
`CARROTQUEST_WEBHOOK_TOKEN` и `SUVVY_API_TOKEN` пока в `.env` стоят как `pending-replace-...` — это нормально для первого деплоя (сервер поднимется, просто будет отвечать 403 на вебхуки, пока не заменим на настоящие значения после создания вебхука/канала).

После деплоя Vercel даст постоянный URL вида `https://<project>.vercel.app` — его и указывать в настройках вебхука Carrot Quest (`.../webhook/carrotquest`) и канала Suvvy (`.../webhook/suvvy`).

**Ограничения тестового варианта** (не критично для недельного MVP, но иметь в виду): бесплатный план Vercel — таймаут функции ~10 сек и "холодный старт" после простоя; дедупликация не переживает холодный старт (см. dedup.py) — на практике это значит, что раз в какое-то время повторный вебхук-ретрай может пройти дважды, не страшно для теста.

## Постоянный хостинг (на будущее)
Когда решится вопрос с постоянным сервером (см. [../CLAUDE.md](../CLAUDE.md)) — можно так и оставить на Vercel, либо перенести на тот же хост, где `sync-bitrix-altcraft`: тогда `app.py` запускается напрямую (`app.run(...)`), `api/`/`vercel.json` для этого не нужны, дедупликация пишет в обычный файл без `/tmp`.

## Ограничения текущей версии (сделано без доступа к реальным аккаунтам)
- Не проверялось на реальных Carrot Quest/Suvvy — только по документации и офлайн-тестами.
- Дедупликация грубая (по тексту + округлению времени до 5 сек) — Carrot Quest не даёт отдельного `message_id` на вебхуке.
- Нет очереди/ретраев на исходящие запросы — если Suvvy или Carrot Quest недоступны в момент запроса, сообщение просто теряется (лог пишется, но нет повторной попытки). Для недельного MVP это допустимо, но стоит иметь в виду.
