#!/usr/bin/env python3
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.telegram_api import (
    TelegramAPIError,
    answer_callback_query,
    get_updates,
    load_offset,
    save_offset,
    send_long_message,
    send_message,
)
from src.telegram_config import load_telegram_config

LOG_PATH = ROOT / "logs" / "telegram_bot.log"
OFFSET_PATH = ROOT / "logs" / "telegram_offset.json"

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("telegram_bot")


def _command(text: str) -> str:
    return text.strip().split()[0].split("@")[0].lower()


def _welcome(chat_id: int) -> str:
    # Список и число вузов — строго из SUPPORTED_UNIVERSITIES (как в
    # _help_text() ниже), а не хардкодом: иначе при появлении нового вуза
    # (только что так было с Политехом) это приветствие тихо начинает врать.
    from src.robot.universities import SUPPORTED_UNIVERSITIES
    from src.telegram_users import get_user_code

    code = get_user_code(chat_id)
    header = (
        f"Вы зарегистрированы с кодом {code}.\n\n"
        if code
        else "Пришлите ваш уникальный код поступающего (только цифры), чтобы зарегистрироваться.\n\n"
    )
    supported = ", ".join(sorted(SUPPORTED_UNIVERSITIES))
    return (
        header
        + "Команды:\n"
        f"/статус — ваш статус по {len(SUPPORTED_UNIVERSITIES)} вузам ({supported})\n"
        "/робот [вуз] — подробная симуляция зачисления по одному вузу\n"
        "/робот обновить [вуз] — обновить кэш списков робота\n"
        "/конкуренты [вуз] <номер приоритета> — кто впереди вас по направлению (номер см. в /робот)\n"
        "/код <номер> — перерегистрироваться другим кодом\n"
        "/help — справка"
    )


def _help_text() -> str:
    from src.robot.universities import SUPPORTED_UNIVERSITIES, robot_ready_universities

    supported = ", ".join(sorted(SUPPORTED_UNIVERSITIES))
    ready = ", ".join(sorted(robot_ready_universities())) if robot_ready_universities() else "пока нет"
    return (
        "Доступные команды:\n"
        "/статус — общая сводка и меню выбора вуза\n"
        f"/робот [{supported}] — симуляция робота зачисления\n"
        f"/робот обновить [{supported}] — обновить кэш списков робота (сейчас: {ready})\n"
        f"/конкуренты [{supported}] <номер приоритета> — кто впереди вас по направлению (номер см. в /робот)\n"
        "/help — эта справка"
    )


def _format_multi_status(code: str, results: list, *, stale: bool = False) -> str:
    from src.robot.simulator import POOL_NOT_READY_ERROR

    lines = [f"📊 Статус по коду {code}"]
    found_any = False
    # Отдельно от found_any: вузы, чей пул реально прочитался (данные есть,
    # кода в них может и не быть) — в отличие от POOL_NOT_READY_ERROR (пула
    # ещё нет вообще, например первые минуты после рестарта): это НЕ «код не
    # найден», а «данные ещё собираются». Список, а не просто счётчик/флаг —
    # чтобы честно называть вузы при частичной готовности (M3), а не всегда
    # утверждать «ни в одном из 4», когда реально прочитались только 2.
    ready_universities: list[str] = []
    for university, result in results:
        if result.config_error:
            # Вуз отключён/не поддержан/нет программ в config/robot.json —
            # это настройка бота, а не факт про пользователя. Пул для него
            # вообще не читался, поэтому не показываем вуз вовсе, а не
            # притворяемся, что там «нет данных» или «человек не участвует».
            continue
        if result.error == POOL_NOT_READY_ERROR:
            # Кэш ещё ни разу не собирался (например, первые минуты после
            # рестарта) — называем вуз поимённо, а не молчим, но и не пишем
            # «не участвуете»: мы просто ещё не смотрели.
            lines.append("")
            lines.append(f"— {university} —")
            lines.append("⏳ Данные ещё собираются")
            continue
        if result.error:
            # "Не найден в списках вуза" — единственная распознанная ошибка,
            # означающая факт про ЧЕЛОВЕКА (см. _resolve_dima_person в
            # simulator.py: пул успешно прочитан, кода в нём просто нет).
            # Любая другая ошибка на этой ветке (например, кэш повреждён) —
            # сбой инфраструктуры, а не отсутствие человека в конкурсе;
            # выдавать её за «вас там нет» нельзя, подписываем нейтрально.
            lines.append("")
            lines.append(f"— {university} —")
            if "не найден в списках" in result.error:
                ready_universities.append(university)
                lines.append("Вы не участвуете в этом вузе")
            else:
                lines.append("⏳ Не удалось проверить, попробуйте позже")
            continue
        found_any = True
        ready_universities.append(university)
        lines.append("")
        lines.append(f"— {university} —")
        if result.dima_placed_program_key is None:
            lines.append("Пока не проходите ни по одному отслеживаемому приоритету.")
        else:
            via = "БВИ" if result.dima_placed_via == "bvi" else "общий конкурс"
            lines.append(f"✅ Зачислитесь: {result.dima_placed_title}")
            lines.append(f"{result.dima_priority_used}-й приоритет · {via} · балл {result.dima_score}")
    if not found_any:
        # Поимённые строки выше (найдено/не участвуете/собирается/не удалось
        # проверить) уже назвали каждый вуз — здесь только то, чего в них нет:
        # либо совет попробовать другой код, либо, если по циклу не напечатано
        # вообще ни одной строки (все вузы — config_error), единственное
        # сообщение о состоянии. Раньше здесь же второй раз перечислялись те
        # же вузы («Код не найден среди N из M...») — дубль факта, который
        # правили по ре-ревью (Important 1).
        if ready_universities:
            lines.append("")
            lines.append("Проверьте код через /код <номер>, либо вы подавали в другой вуз.")
        elif len(lines) == 1:
            lines.append("")
            lines.append("⏳ Данные ещё собираются после перезапуска — вернитесь через пару минут.")
    if stale and ready_universities:
        from src.robot.universities import (
            SUPPORTED_UNIVERSITIES,
            expected_refresh_hint,
            expected_refresh_seconds,
            is_pool_stale,
        )

        # Срок — по САМОМУ МЕДЛЕННОМУ из протухших вузов: пользователь ждёт,
        # пока обновится всё, а разброс тут полтора порядка (МИРЭА 3 секунды,
        # СТАНКИН почти 8 минут). Вузы обновляются параллельно, поэтому именно
        # максимум, а не сумма.
        stale_seconds = [
            value
            for university, result in results
            if (parser := SUPPORTED_UNIVERSITIES.get(university)) is not None
            and is_pool_stale(parser, result.fetched_at)
            and (value := expected_refresh_seconds(parser)) is not None
        ]
        lines.append("")
        lines.append(
            "⏳ Списки сейчас подтягиваются с сайтов вузов — это "
            f"{expected_refresh_hint(seconds=max(stale_seconds)) if stale_seconds else 'несколько минут'}. "
            "Показаны прошлые данные; повторите команду позже."
        )
    lines.append("")
    lines.append("Подробности по одному вузу: /робот <вуз>")
    return "\n".join(lines)


def _handle_callback(config, callback_query: dict) -> None:
    callback_id = callback_query["id"]
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return

    chat_id = int(chat_id)

    from src.telegram_users import is_registered

    if not is_registered(chat_id):
        answer_callback_query(config.bot_token, callback_id, text="Сначала зарегистрируйтесь")
        return

    # Клавиатура меню старого /статус (menu:back, uni:...) — эти callback'и
    # больше никогда не придут, т.к. сама клавиатура (build_university_keyboard/
    # build_back_to_menu_keyboard) с Task 6 не отправляется. Закомментировано,
    # не удалено — на случай возврата к старой механике.
    # if data == "menu:back":
    #     results = load_results()
    #     if not results.get("rows"):
    #         answer_callback_query(config.bot_token, callback_id, text="Нет данных")
    #         return
    #     send_status(results, chat_id)
    #     answer_callback_query(config.bot_token, callback_id, text="Меню вузов")
    #     return
    #
    # if not data.startswith("uni:"):
    #     answer_callback_query(config.bot_token, callback_id)
    #     return
    #
    # try:
    #     university_index = int(data.split(":", 1)[1])
    # except ValueError:
    #     answer_callback_query(config.bot_token, callback_id, text="Некорректный выбор")
    #     return
    #
    # results = load_results()
    # if not results.get("rows"):
    #     answer_callback_query(config.bot_token, callback_id, text="Нет данных")
    #     return
    #
    # try:
    #     university = send_university_report(results, chat_id, university_index)
    #     answer_callback_query(config.bot_token, callback_id, text=university)
    # except (ValueError, RuntimeError) as exc:
    #     answer_callback_query(config.bot_token, callback_id, text=str(exc))
    # except TelegramAPIError as exc:
    #     logger.error("Callback failed for chat %s: %s", chat_id, exc)
    #     answer_callback_query(config.bot_token, callback_id, text="Ошибка отправки")

    answer_callback_query(config.bot_token, callback_id)


def _register_code_reply(chat_id: int, candidate: str) -> tuple[str, bool]:
    """Проверяет код по кэшам всех вузов и либо сохраняет его, либо отказывает.

    Отказ — ТОЛЬКО когда все готовые вузы честно прочитались (unchecked
    пуст) и кода нет ни в одном из них. Если хоть один вуз не прочитался
    (кэш ещё не прогрелся, например сразу после рестарта бота), сохраняем с
    оговоркой: отказ по неполной информации запирал бы снаружи живого
    пользователя только потому, что его вуз пока не успел прочитаться.
    Возвращает (текст ответа, сохранён ли код).
    """
    from src.robot.code_lookup import find_code_presence
    from src.telegram_users import set_user_code

    presence = find_code_presence(candidate)
    if not presence.is_valid and not presence.unchecked:
        return f"Код {candidate} не найден ни в одном вузе. Проверьте номер и попробуйте снова.", False

    set_user_code(chat_id, candidate)
    lines = [f"Готово, код сохранён: {candidate}"]
    if presence.found:
        lines.append(f"Участвуете в конкурсе: {', '.join(sorted(presence.found))}.")
    if presence.absent:
        lines.append(f"Не участвуете: {', '.join(sorted(presence.absent))}.")
    if presence.unchecked:
        lines.append(f"Пока не смогли проверить (данные ещё собираются): {', '.join(sorted(presence.unchecked))}.")
    return "\n".join(lines), True


def _handle_message(config, chat_id: int, text: str, message_id: int) -> None:
    command = _command(text)

    if command in {"/start", "/help"}:
        send_message(config.bot_token, chat_id, _welcome(chat_id) if command == "/start" else _help_text(), reply_to=message_id)
        return

    from src.telegram_users import is_registered, looks_like_code

    if command in {"/код", "/code"}:
        parts = text.strip().split(maxsplit=1)
        candidate = parts[1].strip() if len(parts) > 1 else ""
        if not looks_like_code(candidate):
            send_message(config.bot_token, chat_id, "Код должен состоять только из цифр (4–15 символов). Пример: /код 1824102", reply_to=message_id)
            return
        reply, _saved = _register_code_reply(chat_id, candidate)
        send_message(config.bot_token, chat_id, reply, reply_to=message_id)
        return

    if not is_registered(chat_id):
        candidate = text.strip()
        if not text.startswith("/") and looks_like_code(candidate):
            reply, saved = _register_code_reply(chat_id, candidate)
            if saved:
                reply += "\nТеперь можно вызвать /статус."
            send_message(config.bot_token, chat_id, reply, reply_to=message_id)
            return
        send_message(
            config.bot_token,
            chat_id,
            "Сначала пришлите ваш уникальный код поступающего (только цифры).",
            reply_to=message_id,
        )
        return

    # Старая механика /статус — общий дашборд по всем 10 вузам через
    # dima_score в config/programs.json. Не удалена, закомментирована:
    # if command == "/статус":
    #     results = load_results()
    #     if not results.get("rows"):
    #         send_message(config.bot_token, chat_id, "Данных пока нет. Запустите /обновить.", reply_to=message_id)
    #         return
    #     send_status(results, chat_id)
    #     return

    if command == "/статус":
        try:
            from src.robot.refresh_worker import get_refresh_worker
            from src.robot.simulator import run_robot_simulation
            from src.robot.universities import SUPPORTED_UNIVERSITIES, is_pool_stale, robot_ready_universities
            from src.telegram_users import build_robot_settings, get_user_code

            code = get_user_code(chat_id)
            worker = get_refresh_worker()
            results = []
            stale_any = False
            for university in sorted(robot_ready_universities()):
                settings = build_robot_settings(code, university)
                # stale_ok=True: читаем кэш и отвечаем мгновенно. Сетевая пересборка
                # здесь подвесила бы цикл getUpdates для всех чатов сразу.
                result = run_robot_simulation(university, settings=settings, stale_ok=True)
                results.append((university, result))
                # config_error (вуз выключен/не поддерживается/нет программ в
                # конфиге) — пул в этом случае вообще не читался, fetched_at
                # пуст всегда, и is_pool_stale() был бы True навечно, заказывая
                # догрев на ровном месте (M1 из ре-ревью).
                if not result.config_error and is_pool_stale(SUPPORTED_UNIVERSITIES[university], result.fetched_at):
                    stale_any = True
                    worker.request(university)
            send_message(config.bot_token, chat_id, _format_multi_status(code, results, stale=stale_any), reply_to=message_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Статус не построился: %s", exc)
            send_message(config.bot_token, chat_id, f"Ошибка:\n{exc}", reply_to=message_id)
        return

    if command in {"/робот", "/robot"}:
        parts = text.strip().split()
        try:
            from src.robot.format import format_robot_cache_refresh, format_robot_result
            from src.robot.simulator import run_robot_simulation
            from src.robot.universities import SUPPORTED_UNIVERSITIES, parse_robot_command

            action, target = parse_robot_command(parts)

            if action == "refresh":
                universities = target if isinstance(target, list) else [target]
                unknown = [name for name in universities if name not in SUPPORTED_UNIVERSITIES]
                if unknown:
                    send_message(
                        config.bot_token,
                        chat_id,
                        f"Робот не поддерживает: {', '.join(unknown)}",
                        reply_to=message_id,
                    )
                    return

                if len(universities) > 1:
                    send_message(
                        config.bot_token,
                        chat_id,
                        "Обновляю кэш робота в фоне — пришлю результат по каждому вузу.",
                        reply_to=message_id,
                    )
                else:
                    send_message(
                        config.bot_token,
                        chat_id,
                        f"Обновляю кэш робота для {universities[0]} в фоне — пришлю результат, "
                        "как будет готово. Остальные команды работают как обычно.",
                        reply_to=message_id,
                    )

                # Пересборку ведёт воркер; здесь только ack, чтобы цикл опроса
                # не стоял 1–7 минут. Готовый результат прилетит колбэком.
                from src.robot.refresh_worker import get_refresh_worker
                from src.robot.universities import read_cached_pool

                def _notify(university: str, error: Exception | None) -> None:
                    if error is not None:
                        message_text = f"⚠️ {university}: не удалось обновить — {error}"
                    else:
                        cached = read_cached_pool(SUPPORTED_UNIVERSITIES[university])
                        if cached is None:
                            message_text = f"⚠️ {university}: обновление прошло, но кэш не читается"
                        else:
                            people, programs, fetched_at, _from_cache = cached
                            message_text = format_robot_cache_refresh(
                                university,
                                fetched_at=fetched_at,
                                people_count=len(people),
                                programs_count=len(programs),
                            )
                    try:
                        send_message(config.bot_token, chat_id, message_text)
                    except Exception as exc:  # noqa: BLE001
                        # TelegramAPIError теперь покрывает и ok=false с HTTP 200,
                        # и любой не-2xx ответ (403 «бот заблокирован», 400
                        # «чат удалён» и т.п. — см. _call в telegram_api.py).
                        # Сетевой сбой (ConnectionError/Timeout) в TelegramAPIError
                        # не заворачивается — ловим широко, чтобы никакой из этих
                        # отказов не улетал трейсбеком в лог воркера.
                        logger.error("Не отправить итог обновления %s: %s", university, exc)

                worker = get_refresh_worker()
                for university in universities:
                    worker.request(university, on_done=_notify, force=True)
                return

            university = target if isinstance(target, str) else "МИРЭА"
            if SUPPORTED_UNIVERSITIES.get(university) is None:
                send_message(
                    config.bot_token,
                    chat_id,
                    f"Робот не поддерживает «{university}». Доступно: {', '.join(sorted(SUPPORTED_UNIVERSITIES))}",
                    reply_to=message_id,
                )
                return

            from src.telegram_users import build_robot_settings, get_user_code

            code = get_user_code(chat_id)
            if not code:
                send_message(config.bot_token, chat_id, "Сначала пришлите ваш код поступающего.", reply_to=message_id)
                return

            from src.robot.refresh_worker import get_refresh_worker
            from src.robot.universities import is_pool_stale

            settings = build_robot_settings(code, university)
            result = run_robot_simulation(university, settings=settings, stale_ok=True)
            send_long_message(config.bot_token, chat_id, format_robot_result(result), reply_to=message_id)
            # config_error (вуз отключён/нет программ в конфиге) — пул вообще
            # не читался, fetched_at пуст всегда, is_pool_stale() был бы True
            # навечно и заказывал бы бессмысленную сетевую пересборку на
            # КАЖДУЮ команду (M1 из ре-ревью).
            if not result.config_error and is_pool_stale(SUPPORTED_UNIVERSITIES[university], result.fetched_at):
                get_refresh_worker().request(university)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Robot simulation failed: %s", exc)
            send_message(config.bot_token, chat_id, f"Ошибка симуляции робота:\n{exc}", reply_to=message_id)
        return

    if command in {"/конкуренты", "/competitors"}:
        parts = text.strip().split()
        try:
            from src.robot.format import format_competitors
            from src.robot.refresh_worker import get_refresh_worker
            from src.robot.simulator import run_robot_simulation
            from src.robot.universities import SUPPORTED_UNIVERSITIES, is_pool_stale, match_university_prefix

            if len(parts) < 2:
                send_message(
                    config.bot_token,
                    chat_id,
                    "Использование: /конкуренты <вуз> <номер приоритета> (номер виден в ответе /робот)",
                    reply_to=message_id,
                )
                return

            matched, consumed = match_university_prefix(parts[1:])
            university = matched if matched is not None else "МИРЭА"
            rest = parts[1 + consumed :] if matched is not None else parts[1:]

            if SUPPORTED_UNIVERSITIES.get(university) is None:
                send_message(
                    config.bot_token,
                    chat_id,
                    f"Робот не поддерживает «{university}». Доступно: {', '.join(sorted(SUPPORTED_UNIVERSITIES))}",
                    reply_to=message_id,
                )
                return

            if not rest or not rest[0].isdigit():
                send_message(
                    config.bot_token,
                    chat_id,
                    "Укажите номер приоритета, например: /конкуренты МЭИ 3",
                    reply_to=message_id,
                )
                return
            priority_number = int(rest[0])

            from src.telegram_users import build_robot_settings, get_user_code

            code = get_user_code(chat_id)
            if not code:
                send_message(config.bot_token, chat_id, "Сначала пришлите ваш код поступающего.", reply_to=message_id)
                return

            settings = build_robot_settings(code, university)
            result = run_robot_simulation(university, settings=settings, stale_ok=True)
            send_long_message(config.bot_token, chat_id, format_competitors(result, priority_number), reply_to=message_id)
            # См. аналогичный комментарий в /робот выше: config_error не
            # должен заказывать сетевую пересборку.
            if not result.config_error and is_pool_stale(SUPPORTED_UNIVERSITIES[university], result.fetched_at):
                get_refresh_worker().request(university)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Competitors lookup failed: %s", exc)
            send_message(config.bot_token, chat_id, f"Ошибка:\n{exc}", reply_to=message_id)
        return

    send_message(config.bot_token, chat_id, "Неизвестная команда. Напишите /help", reply_to=message_id)


# Период фонового прогрева робот-пулов. СТРОГО МЕНЬШЕ TTL кэша (7200с): при
# равенстве кэш протухал ровно к моменту очередного прогрева, и команда,
# попавшая в это окно, уходила пересобирать пул синхронно. 90 минут дают запас
# в полчаса, за который прогрев успевает обновить даже самый медленный вуз.
ROBOT_REFRESH_INTERVAL_SEC = 5400


def _prewarm_robot_pools_once() -> None:
    """Отправляет заявки на прогрев всех пулов и СРАЗУ возвращается.

    Сам ничего не качает: пересборкой владеет refresh_worker, он же держит
    single-flight — поэтому прогрев и команда пользователя, попавшие на один
    вуз, больше не дублируют работу. Свежие кэши воркер пропускает сам
    (force=False), так что перезапуск бота не перекачивает недавно собранное.
    """
    from src.robot.refresh_worker import get_refresh_worker
    from src.robot.universities import robot_ready_universities

    worker = get_refresh_worker()
    for university in robot_ready_universities():
        try:
            # max_age СТРОГО МЕНЬШЕ периода тика (ROBOT_REFRESH_INTERVAL_SEC),
            # а не равен ему. fetched_at ставится в МОМЕНТ ЗАВЕРШЕНИЯ сборки
            # (см. *_pool.py), поэтому на следующем тике возраст кэша всегда
            # РОВНО period − D, где D>0 — длительность самой сборки. При
            # max_age == period сравнение "возраст > max_age" всегда ложно
            # (period − D никогда не больше period), и is_pool_stale() решает
            # «кэш ещё свеж» на КАЖДОМ тике — прогрев тихо пропускает через
            # один, а реальный период откатывается обратно к TTL (тот самый
            # симптом, ради которого делался этот фикс). "// 2" — с запасом:
            # даже если одна сборка займёт до половины периода, следующий тик
            # всё равно её не спутает со «свежим» кэшем.
            worker.request(university, max_age=ROBOT_REFRESH_INTERVAL_SEC // 2)
        except ValueError as exc:
            logger.warning("Заявка на прогрев %s отклонена: %s", university, exc)
    logger.info("Заявки на прогрев отправлены воркеру")


def _robot_pool_refresh_loop() -> None:
    """Шлёт заявки на прогрев при старте и далее каждые 90 минут.

    Виток стоит доли секунды (заявки уходят в воркер и обрабатываются им
    параллельно), поэтому сон почти точно равен заданному интервалу."""
    while True:
        try:
            _prewarm_robot_pools_once()
        except Exception:  # noqa: BLE001
            # Без этого любое неожиданное исключение молча убивало бы
            # daemon-поток прогрева насовсем, до самого рестарта бота — а
            # цикл опроса Telegram (в соседнем потоке) продолжал бы работать,
            # не подавая виду, что пулы больше не обновляются.
            logger.exception("Виток прогрева робот-пулов упал")
        time.sleep(ROBOT_REFRESH_INTERVAL_SEC)


def main() -> int:
    config = load_telegram_config()
    if not config:
        print("Создайте config/telegram.json на основе config/telegram.example.json", file=sys.stderr)
        return 1

    offset = load_offset(OFFSET_PATH)
    logger.info("Бот запущен. offset=%s", offset)

    # Прогрев роботов в фоне при старте и далее каждые ~90 минут: бот сразу
    # опрашивает Telegram, а пулы догреваются рядом и держатся свежими.
    threading.Thread(target=_robot_pool_refresh_loop, name="robot-pool-refresh", daemon=True).start()

    while True:
        try:
            updates = get_updates(config.bot_token, offset=offset, timeout=30)
        except TelegramAPIError as exc:
            if str(exc).startswith("409:"):
                # 409 Conflict от getUpdates — другой процесс уже опрашивает
                # Telegram этим же токеном. Не трейсбек: без этой ветки
                # logger.exception печатал полный стек каждые ~10 секунд (в
                # логе живого прогона — 88 строк за минуту, почти все отсюда).
                logger.error(
                    "Другой инстанс бота опрашивает Telegram тем же токеном — остановите его"
                )
                time.sleep(30)
            else:
                logger.error("getUpdates failed: %s", exc)
                time.sleep(5)
            continue
        except Exception as exc:
            logger.exception("Unexpected polling error: %s", exc)
            time.sleep(5)
            continue

        for update in updates:
            offset = int(update["update_id"]) + 1
            save_offset(OFFSET_PATH, offset)

            callback_query = update.get("callback_query")
            if callback_query:
                try:
                    _handle_callback(config, callback_query)
                except Exception as exc:
                    logger.exception("Callback crashed: %s", exc)
                continue

            message = update.get("message") or update.get("edited_message")
            if not message:
                continue

            text = message.get("text") or ""
            chat_probe = message.get("chat") or {}
            chat_id_probe = chat_probe.get("id")
            if not text.startswith("/"):
                from src.telegram_users import is_registered

                if chat_id_probe is None or is_registered(int(chat_id_probe)):
                    continue

            chat = message.get("chat") or {}
            chat_id = chat.get("id")
            if chat_id is None:
                continue

            try:
                _handle_message(config, int(chat_id), text, int(message["message_id"]))
            except TelegramAPIError as exc:
                logger.error("Command failed for chat %s: %s", chat_id, exc)
                try:
                    send_message(config.bot_token, int(chat_id), f"Ошибка: {exc}")
                except Exception:  # noqa: BLE001
                    # НЕ except TelegramAPIError: сетевой сбой при повторной
                    # отправке (ConnectionError/Timeout из requests) не заворачивается
                    # в TelegramAPIError — не поймав его здесь, мы бы уронили
                    # main() целиком (внешний while True это исключение не ловит).
                    pass
            except Exception as exc:
                logger.exception("Command crashed for chat %s: %s", chat_id, exc)


if __name__ == "__main__":
    raise SystemExit(main())
