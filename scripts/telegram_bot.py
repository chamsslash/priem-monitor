#!/usr/bin/env python3
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.run_update import UpdateStatus, run_update
from src.service import load_results
from src.telegram_api import (
    TelegramAPIError,
    answer_callback_query,
    edit_message_text,
    get_updates,
    load_offset,
    save_offset,
    send_message,
)
from src.telegram_config import load_telegram_config
from src.telegram_format import format_push_message
from src.telegram_notify import send_status, send_to_chats, send_university_report

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
    from src.telegram_users import get_user_code

    code = get_user_code(chat_id)
    header = (
        f"Вы зарегистрированы с кодом {code}.\n\n"
        if code
        else "Пришлите ваш уникальный код поступающего (только цифры), чтобы зарегистрироваться.\n\n"
    )
    return (
        header
        + "Команды:\n"
        "/статус — ваш статус по 4 вузам (Финуниверситет, МИРЭА, МЭИ, СТАНКИН)\n"
        "/обновить — загрузить свежие списки\n"
        "/робот [вуз] — подробная симуляция зачисления по одному вузу\n"
        "/робот обновить [вуз] — обновить кэш списков робота\n"
        "/приоритет [вуз] — свой порядок приоритетов (по умолчанию — как подано на сайте вуза)\n"
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
        "/обновить — запустить парсинг (работает, пока включён Mac)\n"
        f"/робот [{supported}] — симуляция робота зачисления\n"
        f"/робот обновить [{supported}] — обновить кэш списков робота (сейчас: {ready})\n"
        f"/приоритет [{supported}] — текущие приоритеты и настройка кнопками\n"
        "/help — эта справка"
    )


def _format_multi_status(code: str, results: list) -> str:
    lines = [f"📊 Статус по коду {code}"]
    found_any = False
    for university, result in results:
        if result.error:
            continue
        found_any = True
        lines.append("")
        lines.append(f"— {university} —")
        if result.dima_placed_program_key is None:
            lines.append("Пока не проходите ни по одному отслеживаемому приоритету.")
        else:
            via = "БВИ" if result.dima_placed_via == "bvi" else "общий конкурс"
            lines.append(f"✅ Зачислитесь: {result.dima_placed_title}")
            lines.append(f"{result.dima_priority_used}-й приоритет · {via} · балл {result.dima_score}")
    if not found_any:
        lines.append("")
        lines.append("Код не найден ни в одном из 4 вузов (Финуниверситет, МИРЭА, МЭИ, СТАНКИН).")
        lines.append("Проверьте код через /код <номер>, либо вы подавали в другой вуз.")
    lines.append("")
    lines.append("Подробности по одному вузу: /робот <вуз>")
    return "\n".join(lines)


def _send_priority_view(
    config,
    chat_id: int,
    university: str = "МИРЭА",
    *,
    reply_to: int | None = None,
) -> None:
    from src.robot.telegram_priorities import (
        build_priority_view_keyboard,
        format_priority_view,
        load_priority_editor,
    )

    state = load_priority_editor(chat_id, university=university)
    send_message(
        config.bot_token,
        chat_id,
        format_priority_view(state, editing=False),
        reply_to=reply_to,
        reply_markup=build_priority_view_keyboard(),
    )


def _send_priority_editor(
    config,
    chat_id: int,
    university: str = "МИРЭА",
    *,
    reply_to: int | None = None,
) -> None:
    from src.robot.telegram_priorities import (
        build_priority_keyboard,
        format_priority_view,
        load_priority_editor,
        save_priority_editor,
    )

    state = load_priority_editor(chat_id, university=university)
    save_priority_editor(chat_id, state)
    send_message(
        config.bot_token,
        chat_id,
        format_priority_view(state, editing=True),
        reply_to=reply_to,
        reply_markup=build_priority_keyboard(state),
    )


def _handle_priority_callback(config, callback_query: dict) -> None:
    from src.robot.priorities import save_priority_ids
    from src.robot.telegram_priorities import (
        build_priority_keyboard,
        clear_priority_session,
        format_priority_view,
        format_saved_confirmation,
        load_priority_editor,
        move_program,
        save_priority_editor,
        toggle_program,
        university_from_message,
    )

    callback_id = callback_query["id"]
    data = callback_query.get("data") or ""
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if chat_id is None or message_id is None:
        return

    chat_id = int(chat_id)
    message_id = int(message_id)

    from src.telegram_users import is_registered

    if not is_registered(chat_id):
        answer_callback_query(config.bot_token, callback_id, text="Сначала зарегистрируйтесь")
        return

    if not data.startswith("prio:"):
        return

    message_text = message.get("text") or ""
    university = university_from_message(message_text)
    state = load_priority_editor(chat_id, university=university)
    action = data.split(":", 2)

    if len(action) < 2:
        answer_callback_query(config.bot_token, callback_id)
        return

    op = action[1]
    toast: str | None = None

    if op == "edit":
        save_priority_editor(chat_id, state)
        edit_message_text(
            config.bot_token,
            chat_id,
            message_id,
            format_priority_view(state, editing=True),
            reply_markup=build_priority_keyboard(state),
        )
        answer_callback_query(config.bot_token, callback_id)
        return

    if op in {"tog", "up", "dn"} and len(action) == 3:
        try:
            program_id = int(action[2])
        except ValueError:
            answer_callback_query(config.bot_token, callback_id, text="Ошибка")
            return
        if op == "tog":
            toggle_program(state, program_id)
            toast = "Обновлено"
        elif op == "up":
            toast = "Выше" if move_program(state, program_id, "up") else "Уже первый"
        elif op == "dn":
            toast = "Ниже" if move_program(state, program_id, "down") else "Уже последний"
        save_priority_editor(chat_id, state)
        edit_message_text(
            config.bot_token,
            chat_id,
            message_id,
            format_priority_view(state, editing=True),
            reply_markup=build_priority_keyboard(state),
        )
        answer_callback_query(config.bot_token, callback_id, text=toast)
        return

    if op == "save":
        if not state.priority_ids:
            answer_callback_query(config.bot_token, callback_id, text="Выберите хотя бы одну программу")
            return
        from src.telegram_users import robot_config_path

        save_priority_ids(state.university, state.priority_ids, path=robot_config_path(chat_id))
        clear_priority_session(chat_id, state.university)
        from src.robot.telegram_priorities import build_priority_view_keyboard

        edit_message_text(
            config.bot_token,
            chat_id,
            message_id,
            format_saved_confirmation(state),
            reply_markup=build_priority_view_keyboard(),
        )
        answer_callback_query(config.bot_token, callback_id, text="Сохранено")
        return

    if op == "cancel":
        clear_priority_session(chat_id, state.university)
        state = load_priority_editor(chat_id, university=state.university)
        from src.robot.telegram_priorities import build_priority_view_keyboard

        edit_message_text(
            config.bot_token,
            chat_id,
            message_id,
            format_priority_view(state, editing=False),
            reply_markup=build_priority_view_keyboard(),
        )
        answer_callback_query(config.bot_token, callback_id, text="Отменено")
        return

    answer_callback_query(config.bot_token, callback_id)


def _handle_callback(config, callback_query: dict) -> None:
    callback_id = callback_query["id"]
    data = callback_query.get("data") or ""
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

    if data.startswith("prio:"):
        _handle_priority_callback(config, callback_query)
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


def _handle_message(config, chat_id: int, text: str, message_id: int) -> None:
    command = _command(text)

    if command in {"/start", "/help"}:
        send_message(config.bot_token, chat_id, _welcome(chat_id) if command == "/start" else _help_text(), reply_to=message_id)
        return

    from src.telegram_users import is_registered, looks_like_code, set_user_code

    if command in {"/код", "/code"}:
        parts = text.strip().split(maxsplit=1)
        candidate = parts[1].strip() if len(parts) > 1 else ""
        if not looks_like_code(candidate):
            send_message(config.bot_token, chat_id, "Код должен состоять только из цифр (4–15 символов). Пример: /код 1824102", reply_to=message_id)
            return
        set_user_code(chat_id, candidate)
        send_message(config.bot_token, chat_id, f"Готово, код обновлён: {candidate}", reply_to=message_id)
        return

    if not is_registered(chat_id):
        if not text.startswith("/") and looks_like_code(text.strip()):
            set_user_code(chat_id, text.strip())
            send_message(
                config.bot_token,
                chat_id,
                f"Готово, код сохранён: {text.strip()}\nТеперь можно вызвать /статус.",
                reply_to=message_id,
            )
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
        from src.robot.priorities import get_saved_priority_ids
        from src.robot.simulator import run_robot_simulation
        from src.robot.universities import robot_ready_universities
        from src.telegram_users import build_robot_settings, get_user_code, robot_config_path

        code = get_user_code(chat_id)
        results = []
        for university in sorted(robot_ready_universities()):
            settings = build_robot_settings(code, university)
            priority_ids = get_saved_priority_ids(university, path=robot_config_path(chat_id))
            result = run_robot_simulation(university, settings=settings, use_cache=True, priority_ids=priority_ids)
            results.append((university, result))
        send_message(config.bot_token, chat_id, _format_multi_status(code, results), reply_to=message_id)
        return

    if command in {"/робот", "/robot"}:
        parts = text.strip().split()
        try:
            from src.robot.format import format_robot_cache_refresh, format_robot_result
            from src.robot.priorities import get_saved_priority_ids
            from src.robot.simulator import run_robot_simulation
            from src.robot.universities import SUPPORTED_UNIVERSITIES, fetch_university_pool, parse_robot_command

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
                        "Обновляю кэш робота…",
                        reply_to=message_id,
                    )
                else:
                    send_message(
                        config.bot_token,
                        chat_id,
                        f"Обновляю кэш робота для {universities[0]}… Это может занять 1–3 минуты.",
                        reply_to=message_id,
                    )

                messages: list[str] = []
                for university in universities:
                    parser_name = SUPPORTED_UNIVERSITIES[university]
                    try:
                        people, programs, fetched_at, _ = fetch_university_pool(parser_name, use_cache=False)
                    except ValueError as exc:
                        messages.append(f"⚠️ {university}: {exc}")
                        continue
                    messages.append(
                        format_robot_cache_refresh(
                            university,
                            fetched_at=fetched_at,
                            people_count=len(people),
                            programs_count=len(programs),
                        )
                    )
                send_message(config.bot_token, chat_id, "\n\n".join(messages), reply_to=message_id)
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

            from src.telegram_users import build_robot_settings, get_user_code, robot_config_path

            code = get_user_code(chat_id)
            if not code:
                send_message(config.bot_token, chat_id, "Сначала пришлите ваш код поступающего.", reply_to=message_id)
                return

            send_message(
                config.bot_token,
                chat_id,
                f"Запускаю симуляцию робота для {university}…\nЗагружаю списки, это может занять 1–3 минуты.",
                reply_to=message_id,
            )
            settings = build_robot_settings(code, university)
            priority_ids = get_saved_priority_ids(university, path=robot_config_path(chat_id))
            result = run_robot_simulation(university, settings=settings, use_cache=True, priority_ids=priority_ids)
            send_message(config.bot_token, chat_id, format_robot_result(result), reply_to=message_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Robot simulation failed: %s", exc)
            send_message(config.bot_token, chat_id, f"Ошибка симуляции робота:\n{exc}", reply_to=message_id)
        return

    if command in {"/приоритет", "/приоритеты", "/priority"}:
        parts = text.strip().split()
        try:
            from src.robot.priorities import save_priority_ids
            from src.robot.telegram_priorities import (
                PriorityEditorState,
                format_saved_confirmation,
                load_priority_editor,
                try_parse_priority_command,
            )
            from src.robot.universities import SUPPORTED_UNIVERSITIES, parse_university_arg

            university = parse_university_arg(parts, default="МИРЭА") or "МИРЭА"
            if SUPPORTED_UNIVERSITIES.get(university) is None:
                send_message(
                    config.bot_token,
                    chat_id,
                    f"Робот не поддерживает «{university}». Доступно: {', '.join(sorted(SUPPORTED_UNIVERSITIES))}",
                    reply_to=message_id,
                )
                return

            from src.telegram_users import robot_config_path

            parsed = try_parse_priority_command(
                text,
                default_university=university,
                supported_universities=set(SUPPORTED_UNIVERSITIES),
            )
            if parsed is not None:
                parsed_university, priority_ids = parsed
                if priority_ids:
                    save_priority_ids(parsed_university, priority_ids, path=robot_config_path(chat_id))
                    state = load_priority_editor(chat_id, university=parsed_university)
                    saved_state = PriorityEditorState(
                        university=parsed_university,
                        parser=state.parser,
                        priority_ids=priority_ids,
                        options=state.options,
                    )
                    send_message(
                        config.bot_token,
                        chat_id,
                        format_saved_confirmation(saved_state),
                        reply_to=message_id,
                    )
                    return

            _send_priority_view(config, chat_id, university, reply_to=message_id)
        except ValueError as exc:
            send_message(config.bot_token, chat_id, f"Ошибка приоритетов: {exc}", reply_to=message_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Priority editor failed: %s", exc)
            send_message(config.bot_token, chat_id, f"Ошибка: {exc}", reply_to=message_id)
        return

    if command in {"/обновить", "/update"}:
        send_message(config.bot_token, chat_id, "Запускаю обновление… Это может занять 1–2 минуты.", reply_to=message_id)
        old_results = load_results()
        result = run_update()
        if result.status == UpdateStatus.ALREADY_RUNNING:
            send_message(config.bot_token, chat_id, result.message, reply_to=message_id)
            return
        if result.status != UpdateStatus.SUCCESS:
            send_message(config.bot_token, chat_id, f"Ошибка обновления:\n{result.message}", reply_to=message_id)
            return

        new_results = load_results()
        send_to_chats(config, format_push_message(old_results, new_results), chat_id=chat_id)
        return

    send_message(config.bot_token, chat_id, "Неизвестная команда. Напишите /help", reply_to=message_id)


def main() -> int:
    config = load_telegram_config()
    if not config:
        print("Создайте config/telegram.json на основе config/telegram.example.json", file=sys.stderr)
        return 1

    offset = load_offset(OFFSET_PATH)
    logger.info("Бот запущен. offset=%s", offset)

    while True:
        try:
            updates = get_updates(config.bot_token, offset=offset, timeout=30)
        except TelegramAPIError as exc:
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
                except TelegramAPIError:
                    pass
            except Exception as exc:
                logger.exception("Command crashed for chat %s: %s", chat_id, exc)


if __name__ == "__main__":
    raise SystemExit(main())
