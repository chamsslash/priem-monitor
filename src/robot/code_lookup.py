from __future__ import annotations

from dataclasses import dataclass

from .universities import SUPPORTED_UNIVERSITIES, read_cached_pool, robot_ready_universities


@dataclass
class CodePresence:
    found: list[str]  # вузы, где код есть в конкурсных списках
    absent: list[str]  # пул прочитан, кода нет
    unchecked: list[str]  # кэш не прочитался — судить не можем

    @property
    def is_valid(self) -> bool:
        return bool(self.found)


def find_code_presence(code: str) -> CodePresence:
    """Ищет код абитуриента по кэшам всех вузов, без единого сетевого запроса.

    Идёт строго через robot_ready_universities() — число и состав вузов не
    хардкодятся, чтобы добавление нового вуза (как только что Политеха) не
    требовало правки этого модуля. read_cached_pool() возвращает None, когда
    кэша ещё нет (например, первые минуты после рестарта бота) — это
    принципиально другое состояние, чем «пул прочитан, а кода в нём нет»:
    первое не даёт оснований отвергнуть код, второе даёт.
    """
    found: list[str] = []
    absent: list[str] = []
    unchecked: list[str] = []
    for university in robot_ready_universities():
        parser_name = SUPPORTED_UNIVERSITIES[university]
        cached = read_cached_pool(parser_name)
        if cached is None:
            unchecked.append(university)
            continue
        people, _programs, _fetched_at, _from_cache = cached
        if any(person.code == code for person in people):
            found.append(university)
        else:
            absent.append(university)
    return CodePresence(found=found, absent=absent, unchecked=unchecked)
