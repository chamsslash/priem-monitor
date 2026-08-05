"""Оракул мест: официальные числа мест прямо с сайтов вузов и сверка с тем,
что реально использует робот.

Зачем отдельный модуль. Число мест — вход робота, от которого зависит весь
прогноз, и оно дважды тихо протухало: локальный хардкод (`config/programs.json`
у МЭИ/ФА, `STANKIN_KCP_OVERRIDES` у СТАНКИНа) расходился с сайтом, а заметить
это было негде — робот считал по устаревшему числу и уверенно выдавал
неправильный ответ. Здесь живёт независимая от пулов сверка: сходить на
официальную страницу, посчитать места по официальной формуле и сравнить
с тремя вещами — числом, которое взял робот, локальным резервом и границами
здравого смысла.

Сверяемые оси по вузам:

| вуз      | что берёт робот                     | официальный оракул                     |
|----------|-------------------------------------|----------------------------------------|
| МЭИ      | «Количество вакантных мест» волны   | КЦП общего конкурса (speclist_simple)  |
| СТАНКИН  | kcp.php (места общего конкурса)     | тот же kcp.php → сверяем ОВЕРРАЙД      |
| МИРЭА    | plan бюджетного конкурса из API     | тот же API → сверяем config            |
| ФА       | config/programs.json                | нет машинного источника                |

У СТАНКИНа и МИРЭА робот уже берёт живое число, поэтому оракул для них сверяет
не робота (это было бы кругом), а ЗАХАРДКОЖЕННЫЙ РЕЗЕРВ — то самое, что тухнет
молча и всплывает в момент, когда сайт не ответил.

Многопрофильные конкурсные группы (МЭИ: «Информатика и вычислительная техника»
= ИВТИ + ИРЭ) считаются ОДНИМ числом — сумма мест по строкам группы. Основание:
у группы один конкурсный список, один проходной балл и одна страница волны
(«Информатика и вычислительная техника ИВТИ, ИРЭ бюджет (Очная)»), а
распределение по институтам вуз делает уже внутри, после зачисления.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from ..config_loader import load_programs
from .models import RobotProgram
from .stankin_pool import STANKIN_KCP_OVERRIDES, fetch_stankin_seats
from .stankin_pool import _tracked_direction_groups as stankin_tracked_groups

MPEI_KCP_URL = "https://pk.mpei.ru/info/speclist_simple.html"
MIREA_CATALOG_URL = "https://priem.mirea.ru/competitions_api"

# Статусы сверки одного направления.
STATUS_OK = "ok"  # робот и резерв сходятся с официальным числом
STATUS_STALE_LOCAL = "stale_local"  # захардкоженный резерв разошёлся с сайтом
STATUS_MISMATCH = "mismatch"  # число робота вне допустимых границ — считать нельзя
STATUS_NO_ORACLE = "no_oracle"  # у вуза нет машинно-читаемого источника мест
STATUS_UNAVAILABLE = "unavailable"  # оракул не ответил / направления нет в его выдаче


@dataclass
class SeatAudit:
    """Сверка мест по одному отслеживаемому направлению."""

    university: str
    title: str
    robot_places: int | None
    robot_source: str | None
    official_places: int | None
    local_places: int | None
    status: str
    note: str = ""

    @property
    def is_problem(self) -> bool:
        return self.status in (STATUS_STALE_LOCAL, STATUS_MISMATCH)


@dataclass
class MpeiKcpGroup:
    """Официальный КЦП одной конкурсной группы МЭИ (очная форма)."""

    kcp: int  # «Всего» по всем строкам группы
    quotas: int  # особая + отдельная + целевая
    general: int  # места общего конкурса = КЦП − квоты
    rows: int  # строк-профилей в группе (>1 — многопрофильная)


# --------------------------------------------------------------------------- МЭИ


def _expand_table(table) -> list[list[str]]:
    """Таблица → прямоугольная сетка с раскрытыми rowspan/colspan.

    У таблицы КЦП МЭИ название группы, направление и вступительные испытания
    объединены по вертикали (rowspan) на все профили многопрофильной группы, а
    часть заголовков — по горизонтали (colspan). Без раскрытия строки-продолжения
    начинаются с аббревиатуры института («ИРЭ»), и определить, к какой группе
    они относятся, по «сырым» ячейкам нельзя.
    """
    filled: dict[tuple[int, int], str] = {}
    rows = table.find_all("tr")
    for row_index, row in enumerate(rows):
        col_index = 0
        for cell in row.find_all(["td", "th"]):
            while (row_index, col_index) in filled:
                col_index += 1
            text = cell.get_text(" ", strip=True)
            rowspan = int(cell.get("rowspan") or 1)
            colspan = int(cell.get("colspan") or 1)
            for row_offset in range(rowspan):
                for col_offset in range(colspan):
                    filled[(row_index + row_offset, col_index + col_offset)] = text
            col_index += colspan
    if not filled:
        return []
    width = max(col for _, col in filled) + 1
    return [[filled.get((row_index, col), "") for col in range(width)] for row_index in range(len(rows))]


def _mpei_columns(grid: list[list[str]]) -> dict[str, int]:
    """Индексы нужных колонок по тексту шапки (а не по номеру — вёрстка меняется)."""
    wanted = {
        "kcp": "всего",
        "special": "особая квота",
        "separate": "отдельная квота",
        "target": "квота приема на целевое обучение",
    }
    for row in grid:
        lowered = [cell.lower() for cell in row]
        found: dict[str, int] = {}
        for name, marker in wanted.items():
            for index, cell in enumerate(lowered):
                if marker in cell:
                    found[name] = index
                    break
        if len(found) == len(wanted):
            return found
    raise ValueError("В таблице КЦП МЭИ не найдена шапка с колонками мест и квот")


def parse_mpei_kcp(html: str) -> dict[str, MpeiKcpGroup]:
    """Официальные КЦП очной формы по названию конкурсной группы.

    Места общего конкурса = «Всего» − особая − отдельная − целевая квота: робот
    моделирует именно общий конкурс по ЕГЭ, без квотных мест. Строки одной
    многопрофильной группы суммируются в одно число (см. докстринг модуля).
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="kcp-table")
    if table is None:
        raise ValueError("Не найдена таблица kcp-table на странице КЦП МЭИ")

    grid = _expand_table(table)
    columns = _mpei_columns(grid)

    groups: dict[str, MpeiKcpGroup] = {}
    in_daytime = False
    for row in grid:
        title = row[0].strip()
        if title == "Очная форма обучения":
            in_daytime = True
            continue
        if title.startswith("Очно-заочная") or title.startswith("Заочная"):
            break
        if not in_daytime or not title:
            continue
        try:
            kcp = int(row[columns["kcp"]])
            quotas = sum(int(row[columns[name]]) for name in ("special", "separate", "target"))
        except (ValueError, IndexError):
            continue
        group = groups.get(title)
        if group is None:
            groups[title] = MpeiKcpGroup(kcp=kcp, quotas=quotas, general=kcp - quotas, rows=1)
            continue
        group.kcp += kcp
        group.quotas += quotas
        group.general += kcp - quotas
        group.rows += 1
    if not groups:
        raise ValueError("Не удалось извлечь ни одной конкурсной группы из таблицы КЦП МЭИ")
    return groups


def fetch_mpei_kcp() -> dict[str, MpeiKcpGroup]:
    response = requests.get(MPEI_KCP_URL, timeout=60)
    response.raise_for_status()
    response.encoding = "utf-8"
    return parse_mpei_kcp(response.text)


_OKSO_SUFFIX_RE = re.compile(r"\s*\(\d{2}\.\d{2}\.\d{2}\b.*\)\s*$")


def _mpei_group_title(catalog_title: str) -> str:
    """«Прикладная информатика (09.03.03 Прикладная информатика)» → «Прикладная информатика».

    В каталоге конкурсных списков к названию группы приписан код направления в
    скобках, в таблице КЦП — нет. Срезаем только хвост, который начинается с
    кода ОКСО: скобки внутри самого названия группы трогать нельзя.
    """
    return _OKSO_SUFFIX_RE.sub("", catalog_title).strip()


# ------------------------------------------------------------------------- МИРЭА


def fetch_mirea_plans(proxies: dict[str, str] | None = None) -> dict[str, int]:
    """comp_id → план приёма бюджетного общего конкурса, живьём из API МИРЭА."""
    from .mirea_pool import BUDGET_COMP_TYPE_ID, CATALOG_PARAMS, _PROXIES

    response = requests.get(
        MIREA_CATALOG_URL,
        params=CATALOG_PARAMS,
        timeout=60,
        proxies=proxies if proxies is not None else _PROXIES,
    )
    response.raise_for_status()
    plans: dict[str, int] = {}
    for program in response.json():
        for competition in program.get("competitions", []):
            if str(competition.get("compTypeId")) != BUDGET_COMP_TYPE_ID:
                continue
            plan = int(competition.get("plan") or 0)
            for comp_id in competition.get("compIds", []):
                plans[str(comp_id)] = plan
    if not plans:
        raise ValueError("API МИРЭА не отдал ни одного бюджетного конкурса")
    return plans


# --------------------------------------------------------------------------- сверка


def _config_places(university: str) -> dict[int, int | None]:
    return {
        program.id: program.budget_places
        for program in load_programs()
        if program.university == university
    }


def _tracked(programs: list[RobotProgram]) -> list[RobotProgram]:
    return [program for program in programs if program.tracked_id is not None]


def audit_mpei(programs: list[RobotProgram]) -> list[SeatAudit]:
    """МЭИ: живые «вакантные места» волны против официального КЦП.

    Две разные проверки, потому что это два разных числа:

    * **робот** берёт вакантные места текущей волны. Они НЕ обязаны совпадать с
      КЦП общего конкурса: вуз уже перенёс в общий конкурс незаполненные квоты
      (вверх) и вычел зачисленных предыдущими приказами (вниз). Поэтому здесь
      проверяются не равенство, а границы `0 < вакантные ≤ КЦП` — выход за них
      означает, что со страницы читается не то число.
    * **резерв в config** (его берут, когда сайт не отдал вакантные места)
      сверяется с КЦП общего конкурса. Резерв обязан быть именно этим числом:
      оно официальное, выводится по формуле и стабильно между волнами, в отличие
      от снимка вакантных мест, который протухает к следующему приказу.
    """
    kcp = fetch_mpei_kcp()
    config = _config_places("МЭИ")
    audits: list[SeatAudit] = []
    for program in _tracked(programs):
        group = kcp.get(_mpei_group_title(program.title))
        local = config.get(program.tracked_id)
        if group is None:
            audits.append(
                SeatAudit(
                    university="МЭИ",
                    title=program.title,
                    robot_places=program.budget_places,
                    robot_source=program.seat_source,
                    official_places=None,
                    local_places=local,
                    status=STATUS_UNAVAILABLE,
                    note="конкурсной группы нет в таблице КЦП — сверять не с чем",
                )
            )
            continue

        multi = f", многопрофильная: {group.rows} строк(и) сложены" if group.rows > 1 else ""
        base = f"КЦП {group.kcp} − квоты {group.quotas} = {group.general} мест общего конкурса{multi}"

        if program.budget_places is None:
            status, note = STATUS_MISMATCH, f"робот не знает числа мест; {base}"
        elif program.seat_source != "live":
            status = STATUS_STALE_LOCAL if program.budget_places != group.general else STATUS_OK
            note = f"сайт не отдал вакантные места, взят резерв «{program.seat_source}»; {base}"
        elif not 0 < program.budget_places <= group.kcp:
            status = STATUS_MISMATCH
            note = f"вакантных мест {program.budget_places} вне границ 1..{group.kcp}; {base}"
        elif local is not None and local != group.general:
            status = STATUS_STALE_LOCAL
            note = f"резерв config={local} не равен официальным местам общего конкурса; {base}"
        else:
            status = STATUS_OK
            note = f"вакантных мест волны {program.budget_places}; {base}"
        audits.append(
            SeatAudit(
                university="МЭИ",
                title=program.title,
                robot_places=program.budget_places,
                robot_source=program.seat_source,
                official_places=group.general,
                local_places=local,
                status=status,
                note=note,
            )
        )
    return audits


def audit_stankin(programs: list[RobotProgram]) -> list[SeatAudit]:
    """СТАНКИН: живой kcp.php против захардкоженного STANKIN_KCP_OVERRIDES."""
    directions = list(stankin_tracked_groups())
    with requests.Session() as session:
        live = {
            direction: fetch_stankin_seats(direction, session=session) for direction in directions
        }
    audits: list[SeatAudit] = []
    for program in _tracked(programs):
        # У СТАНКИНа title программы пула — это и есть строка направления сайта.
        official = live.get(program.title)
        local = STANKIN_KCP_OVERRIDES.get(program.title)
        if official is None:
            status, note = STATUS_UNAVAILABLE, "kcp.php не ответил по направлению"
        elif program.budget_places is None:
            status, note = STATUS_MISMATCH, "робот не знает числа мест"
        elif program.seat_source == "live" and program.budget_places != official:
            status = STATUS_MISMATCH
            note = f"робот взял {program.budget_places}, а kcp.php сейчас отдаёт {official}"
        elif local is None:
            status = STATUS_OK
            note = "резерва в STANKIN_KCP_OVERRIDES нет — при недоступности сайта откат на nap/config"
        elif local != official:
            status = STATUS_STALE_LOCAL
            note = f"резерв STANKIN_KCP_OVERRIDES={local} протух против живого {official}"
        else:
            status, note = STATUS_OK, "резерв совпадает с живым числом"
        audits.append(
            SeatAudit(
                university="СТАНКИН",
                title=program.title,
                robot_places=program.budget_places,
                robot_source=program.seat_source,
                official_places=official,
                local_places=local,
                status=status,
                note=note,
            )
        )
    return audits


def audit_mirea(programs: list[RobotProgram]) -> list[SeatAudit]:
    """МИРЭА: живой план бюджетного конкурса против config/programs.json."""
    plans = fetch_mirea_plans()
    config = _config_places("МИРЭА")
    audits: list[SeatAudit] = []
    for program in _tracked(programs):
        official = plans.get(program.key)
        local = config.get(program.tracked_id)
        if official is None:
            status, note = STATUS_UNAVAILABLE, "конкурса нет в бюджетной выдаче API"
        elif program.budget_places != official:
            status = STATUS_MISMATCH
            note = f"робот взял {program.budget_places}, а API сейчас отдаёт {official}"
        elif local is not None and local != official:
            status = STATUS_STALE_LOCAL
            note = f"config={local} протух против живого плана {official}"
        else:
            status, note = STATUS_OK, "живой план, config совпадает"
        audits.append(
            SeatAudit(
                university="МИРЭА",
                title=program.title,
                robot_places=program.budget_places,
                robot_source=program.seat_source,
                official_places=official,
                local_places=local,
                status=status,
                note=note,
            )
        )
    return audits


def audit_fa(programs: list[RobotProgram]) -> list[SeatAudit]:
    """ФА: машинного источника мест нет — фиксируем это явно, а не молчим.

    fa.ru отдаёт конкурсные списки, но не КЦП: числа в config сняты вручную с
    официального КЦП 2026/2027 (очная, Москва) как КЦП − особая − отдельная −
    целевая. Автосверка тут невозможна без выбора неофициального источника, а
    угадывать источник для авторитетных цифр — ровно та ошибка, из-за которой
    места протухали. Строка со статусом no_oracle нужна, чтобы ФА не выглядел
    «проверенным» наравне с остальными.
    """
    config = _config_places("Финансовый университет")
    return [
        SeatAudit(
            university="Финансовый университет",
            title=program.title,
            robot_places=program.budget_places,
            robot_source=program.seat_source,
            official_places=None,
            local_places=config.get(program.tracked_id),
            status=STATUS_NO_ORACLE,
            note="fa.ru не публикует КЦП машинно — число из config, сверяется вручную",
        )
        for program in _tracked(programs)
    ]


def check_no_paid_seats(university: str, programs: list[RobotProgram]) -> tuple[bool, str]:
    """В пуле не должно быть ни одного платного конкурса.

    Почему это отдельная проверка, а не «и так же видно»: конкурент, которого
    каскад посадил на платное место, исчезает из борьбы за бюджет — робот видит
    свободный бюджет там, где его нет. Именно так МИРЭА и ошибался.

    У МИРЭА платный конкурс называется ровно так же, как бюджетный («общий
    конкурс»), поэтому по названию его не поймать — сверяем ключи пула со
    списком бюджетных compId из API. У остальных трёх вузов платное отсекается
    параметрами запроса (ФА `form_pay=Бюджет`, СТАНКИН `PROPERTY_388=Бюджетная
    основа`, МЭИ класс ссылки `listFilterBudget`), и машинного признака в самом
    пуле нет — там проверяем то, что доступно: платную пометку в названии.
    """
    if university == "МИРЭА":
        budget_ids = set(fetch_mirea_plans())
        alien = [program for program in programs if program.key not in budget_ids]
        if alien:
            titles = ", ".join(program.title for program in alien[:3])
            return False, f"{len(alien)} конкурсов вне бюджетной выдачи API (напр.: {titles})"
        return True, f"все {len(programs)} конкурсов — бюджетные по compId из API"

    markers = ("договор", "платн", "контракт")
    paid = [program for program in programs if any(m in program.title.lower() for m in markers)]
    if paid:
        titles = ", ".join(program.title for program in paid[:3])
        return False, f"{len(paid)} направлений с платной пометкой в названии: {titles}"
    return True, f"платных пометок нет ни у одного из {len(programs)} направлений"


_AUDITORS = {
    "МЭИ": audit_mpei,
    "СТАНКИН": audit_stankin,
    "МИРЭА": audit_mirea,
    "Финансовый университет": audit_fa,
}


def audit_university(university: str, programs: list[RobotProgram]) -> list[SeatAudit]:
    auditor = _AUDITORS.get(university)
    if auditor is None:
        raise ValueError(f"Нет оракула мест для вуза «{university}»")
    return auditor(programs)
