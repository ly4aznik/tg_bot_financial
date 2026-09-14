# Telegram-бот для учёта расходов 2.0

Бот собирает расход пошагово через интерфейс Telegram. Подтверждённые записи сохраняются в локальную SQLite-базу.

## Сценарий

1. Нажмите `Добавить расход` или отправьте `/add`.
2. Выберите `Сегодня`, `Вчера` или `Другая дата`. Для другой даты введите `ДД.ММ`; используется текущий год.
3. Введите положительную целую сумму без копеек, например `650` или `1250`.
4. Выберите одну из 20 категорий кнопкой.
5. Введите описание минимум из двух символов.
6. Проверьте итог: любое поле можно изменить, после чего запись можно подтвердить или отменить.

Команда `/cancel` отменяет активный сценарий. Повторная `/add` начинает новый сценарий вместо предыдущего.

## Хранение данных

По умолчанию расходы хранятся в `data/expenses.sqlite3`, таблица `expenses`. База и таблица создаются автоматически при запуске.

Секреты в `.env`, каталог `credentials/` и runtime-логи исключены из Git.

## Настройка

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Минимальный `.env`:

```env
TELEGRAM_BOT_TOKEN=<YOUR_TELEGRAM_BOT_TOKEN>
BOT_TIMEZONE=Europe/Moscow
AUDIT_LOG_PATH=logs/interactions.jsonl
SQLITE_DATABASE_PATH=data/expenses.sqlite3
```

## Запуск и тесты

```powershell
python main.py
python -m pytest
python -m compileall -q main.py expense_bot tests
```

## Docker

```bash
docker compose up -d --build
docker compose logs -f bot
```

Файл `.env` не входит в образ. Каталоги `data/` и `logs/` подключаются как постоянные тома с хоста.

Исходный код прежней версии доступен по Git-тегу `v1.0`.
