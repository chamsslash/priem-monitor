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
| МЭИ      | КЦП общего конкурса (speclist)      | тот же КЦП → сверяем config-резерв     |
| СТАНКИН  | kcp.php (места общего конкурса)     | тот же kcp.php → сверяем ОВЕРРАЙД      |
| МИРЭА    | plan бюджетного конкурса из API     | тот же API → сверяем config            |
| ФА       | КЦП из приказа (PDF на fa.ru)       | тот же приказ → сверяем config-резерв   |

Места берутся живьём по ВСЕМ направлениям вуза, а не только по отслеживаемым.
Причина не в аккуратности, а в механике каскада: конкурент занимает место там,
где у него ВЫШЕ приоритет, поэтому завышенные места на чужом направлении уводят
туда людей, которые на самом деле давят на приоритеты Димы. Так и было у
СТАНКИНа: непрофильные направления брали места с nap-страницы (полный КЦП с
квотами, до 90 мест вместо 24), и проходной балл робота там уезжал вниз на
десятки баллов.

Робот везде берёт живое число мест, поэтому оракул сверяет не робота (это было
бы кругом), а ЗАХАРДКОЖЕННЫЙ РЕЗЕРВ — то самое, что тухнет молча и всплывает в
момент, когда сайт не ответил. Сверка самого прогноза — отдельная ось, она живёт
в `verification.py` и опирается на вердикт сайта о людях, а не о местах.

Многопрофильные конкурсные группы (МЭИ: «Информатика и вычислительная техника»
= ИВТИ + ИРЭ) считаются ОДНИМ числом — сумма мест по строкам группы. Основание:
у группы один конкурсный список, один проходной балл и одна страница волны
(«Информатика и вычислительная техника ИВТИ, ИРЭ бюджет (Очная)»), а
распределение по институтам вуз делает уже внутри, после зачисления.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

from ..config_loader import load_programs
from .models import RobotProgram
from .mpei_pool import _extract_list_id, fetch_kcp_groups, fetch_list_vacant, kcp_group_title
from .mpei_pool import tracked_programs as mpei_tracked_programs
from .stankin_pool import STANKIN_KCP_OVERRIDES, fetch_stankin_seats
from .stankin_pool import _tracked_direction_groups as stankin_tracked_groups

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
    """МЭИ: число робота и config-резерв против официального КЦП общего конкурса.

    Робот берёт места из той же таблицы КЦП, поэтому проверяется равенство — как
    у СТАНКИНа с kcp.php. Отдельно подтягиваются «вакантные места» со страницы
    волны: они законно отличаются от планового КЦП (вуз переносит в общий
    конкурс незаполненные квоты и вычитает уже зачисленных), и служат
    контекстом, а не критерием. Источником мест они быть не могут: на list14
    вакантных мест ровно 170 и ровно 170 человек помечены сайтом «Высший
    проходной» — то есть это уже ответ, а не вход.
    """
    kcp = fetch_kcp_groups()
    config = _config_places("МЭИ")
    tracked_lists = {
        str(program.id): _extract_list_id(program.list_url)
        for program in mpei_tracked_programs()
    }
    audits: list[SeatAudit] = []
    for program in _tracked(programs):
        group = kcp.get(kcp_group_title(program.title))
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
        list_id = tracked_lists.get(str(program.tracked_id))
        vacant = fetch_list_vacant(list_id) if list_id else None
        if vacant is not None:
            base += f"; сайт показывает {vacant} вакантных на волну"

        if program.budget_places is None:
            status, note = STATUS_MISMATCH, f"робот не знает числа мест; {base}"
        elif program.budget_places != group.general:
            status = STATUS_MISMATCH
            note = (
                f"робот взял {program.budget_places} (источник «{program.seat_source}»), "
                f"а официальный КЦП даёт {group.general}; {base}"
            )
        elif local is not None and local != group.general:
            status = STATUS_STALE_LOCAL
            note = f"резерв config={local} не равен официальным местам общего конкурса; {base}"
        else:
            status = STATUS_OK
            note = base
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
    """ФА: места из приказа о КЦП плюс проверка «проходящих не больше мест».

    Приказ (fa.ru/for-applicants/bachelor/control/, секция «Бакалавриат (очная
    форма обучения, г. Москва)») разбирается в `fa_pool.fetch_kcp_places` — это
    полноценный официальный источник, как kcp.php у СТАНКИНа.

    Вторая, независимая от приказа проверка: сколько человек сайт САМ отметил
    проходящими. Больше, чем есть мест, вуз зачислить не может, поэтому
    «проходящих больше мест» — сигнал, что число мест занижено. Считать её
    нужно ТОЛЬКО по московским строкам: в одном списке лежат и филиалы, и их
    проходящие идут на свои места (по «Прикладным ИС» это 78 против 59).
    """
    config = _config_places("Финансовый университет")
    audits: list[SeatAudit] = []
    for program in _tracked(programs):
        passing, places = program.site_passing_count, program.budget_places
        local = config.get(program.tracked_id)
        official = places if program.seat_source == "live" else None
        if places is None:
            status, note = STATUS_MISMATCH, "робот не знает числа мест"
        elif program.seat_source != "live":
            status = STATUS_NO_ORACLE
            note = f"программа не сопоставилась с приказом, число из «{program.seat_source}»"
        elif local is not None and local != places:
            status = STATUS_STALE_LOCAL
            note = f"резерв config={local} разошёлся с приказом ({places})"
        elif passing is not None and passing > places:
            status = STATUS_MISMATCH
            note = (
                f"сайт зачисляет {passing} москвичей при {places} местах по приказу — "
                "мест больше, чем в приказе (вуз мог вернуть незаполненные квоты в общий конкурс)"
            )
        else:
            status = STATUS_OK
            note = f"место из приказа о КЦП; сайт зачисляет {passing} — укладывается"
        audits.append(
            SeatAudit(
                university="Финансовый университет",
                title=program.title,
                robot_places=places,
                robot_source=program.seat_source,
                official_places=official,
                local_places=local,
                status=status,
                note=note,
            )
        )
    return audits


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
