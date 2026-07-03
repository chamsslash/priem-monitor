# Мониторинг поступления

Веб-дашборд и CLI для отслеживания места Димы в конкурсных списках вузов из файла `ВУЗы.xlsx`.

## Быстрый старт

```bash
cd "/Users/mariakhorokhorina/Projects/Парсинг списков"
pip3 install -r requirements.txt
python3 update.py --export-xlsx
python3 update.py
python3 -m streamlit run app.py
```

## Что уже работает

- загрузка 77 направлений из `ВУЗы.xlsx` → `config/programs.json`
- парсеры для всех 10 вузов:
  - **МИРЭА** — API `competitions_api/entrants`
  - **МЭИ** — HTML-таблица
  - **РЭУ** — Supabase REST API
  - **РУТ** — API `AdmissionPlan/rating`
  - **СТАНКИН** — `gridspisokpostupayushchikh`
  - **Московский политех** — POST `fio_list_curl.php`
  - **Финуниверситет** — `listabit.php` с пагинацией
  - **РАНХиГС** — HTML через Playwright
  - **МАИ** — HTTP-цепочка `public.mai.ru/priem/list/data/`
  - **РГУ им. Губкина** — API `abiturients_list/api/api.php`
- расчёт:
  - место по согласиям
  - место по согласиям + 1-й приоритет
  - цветовой статус (зелёный / жёлтый / красный)
  - оценка вероятности до 25.07
- фильтры для общих списков в `config/filters.json` (МАИ, FA, СТАНКИН)

## Автообновление по расписанию (macOS)

Рекомендуемый способ — **launchd** (работает в фоне, даже если Mac был выключен):

```bash
cd "/Users/mariakhorokhorina/Projects/Парсинг списков"

# каждые 2 часа (7200 сек)
./scripts/install_scheduler.sh 7200

# каждые 30 минут
./scripts/install_scheduler.sh 1800

# отключить
./scripts/uninstall_scheduler.sh
```

Логи обновлений: `logs/update.log`

Альтернатива — держать планировщик в терминале:

```bash
python3 scheduler.py --interval 7200
python3 scheduler.py --once   # одно обновление
```

Для РАНХиГС нужен Playwright: `python3 -m playwright install chromium`

## Telegram-бот

Бот отправляет сводку после каждого автообновления и отвечает на команды, пока Mac включён.

### Настройка

1. Создайте бота через [@BotFather](https://t.me/BotFather) и получите токен.
2. Скопируйте шаблон и вставьте токен:

```bash
cp config/telegram.example.json config/telegram.json
```

3. Второй человек пишет боту `/start` — в ответ придёт его `chat_id`.
4. Добавьте `chat_id` в `allowed_chat_ids` в `config/telegram.json`.
5. Установите бота как фоновый процесс:

```bash
./scripts/install_telegram_bot.sh
```

### Команды бота

- `/статус` — общая сводка и меню выбора вуза
- `/обновить` — запустить парсинг (1–2 мин, только когда Mac включён)
- `/help` — справка

После каждого автообновления по расписанию бот присылает сводку с изменениями и ошибками.

Отключить бота:

```bash
./scripts/uninstall_telegram_bot.sh
```

## Что настроить дальше

- ничего критичного — основной сценарий готов

## Файлы

- `ВУЗы.xlsx` — исходная таблица
- `config/programs.json` — конфиг направлений
- `config/filters.json` — ручные фильтры для общих списков
- `data/latest_results.json` — последний снимок расчётов
- `app.py` — Streamlit-дашборд
- `update.py` — CLI-обновление
- `scripts/telegram_bot.py` — Telegram-бот
- `config/telegram.json` — токен и список chat_id (не в git)
