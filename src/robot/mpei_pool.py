from __future__ import annotations

import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from ..config_loader import load_programs
from ..models import ProgramConfig
from ..parsers.utils import header_index, normalize_yes, to_int
from .models import ProgramChoice, RobotPerson, RobotProgram

CATALOG_URL = "https://pk.mpei.ru/info/entrants_list"

_SECTION_START = "Бакалавриат очная форма обучения"
_SECTION_END = "Бакалавриат очно-заочная форма обучения"
_LIST_ID_RE = re.compile(r"entrants_list\d+\.html")

FETCH_RETRIES = 3
RETRYABLE_STATUS = {500, 502, 503, 504}

POOL_SCOPE = "full"
# Каталог сужен до основного кампуса — 27 конкурсных групп (см. _catalog_and_kcp).
# Порог с запасом вниз: вуз может закрыть часть направлений.
MIN_CATALOG_PROGRAMS = 20
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "mpei_robot_pool.json"
CACHE_TTL_SEC = 7200
MAX_WORKERS = 6
# Доля упавших списков, после которой сбой считается массовым/системным
# (троттлинг, авария сайта), а не единичным сетевым сбоем одного списка —
# при превышении _fetch_all бросает исключение, чтобы build() откатился
# на устаревший кэш вместо сохранения почти пустого датасета.
MAX_FAILED_FRACTION = 0.5


def _extract_list_id(text: str) -> str | None:
    match = _LIST_ID_RE.search(text)
    return match.group(0) if match else None


# Классы ссылок на квотные списки в каталоге МЭИ. Общий конкурс — ссылка ровно
# с ["competitive-group", "listFilterBudget"]; у квотных списков к этому набору
# добавлен свой маркер вида квоты.
_QUOTA_LINK_CLASSES = {
    "listFilterSpecial": "special",
    "listFilterSeparate": "separate",
    "listFilterTarget": "target",
}


def _catalog_section(html: str) -> BeautifulSoup:
    start = html.find(_SECTION_START)
    if start == -1:
        raise ValueError(f"Не найдена секция «{_SECTION_START}»")
    end = html.find(_SECTION_END, start)
    return BeautifulSoup(html[start:end] if end != -1 else html[start:], "lxml")


def _catalog_from_page(html: str) -> list[tuple[str, str]]:
    """Возвращает [(название конкурсной группы, entrants_listNNN.html)]
    для бюджетных очных бакалаврских/специалитетных направлений."""
    soup = _catalog_section(html)
    catalog: list[tuple[str, str]] = []
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        title = cells[0].get_text(" ", strip=True)
        if not title:
            continue
        for link in cells[1].find_all("a"):
            classes = link.get("class") or []
            if classes != ["competitive-group", "listFilterBudget"]:
                continue
            list_id = _extract_list_id(link.get("href", ""))
            if list_id:
                catalog.append((title, list_id))
    if not catalog:
        raise ValueError("Каталог бюджетных очных направлений МЭИ пуст")
    return catalog


def quota_lists_from_page(html: str) -> dict[str, dict[str, list[str]]]:
    """Название конкурсной группы → {вид квоты: [entrants_listNNN.html, ...]}.

    Целевая квота почти всегда представлена НЕСКОЛЬКИМИ списками — по одному на
    заказчика целевого обучения, поэтому значение здесь список, а не строка.
    """
    soup = _catalog_section(html)
    result: dict[str, dict[str, list[str]]] = {}
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        title = cells[0].get_text(" ", strip=True)
        if not title:
            continue
        for link in cells[1].find_all("a"):
            classes = set(link.get("class") or [])
            if "listFilterBudget" not in classes:
                continue
            kind = next((k for cls, k in _QUOTA_LINK_CLASSES.items() if cls in classes), None)
            if kind is None:
                continue
            list_id = _extract_list_id(link.get("href", ""))
            if list_id:
                result.setdefault(title, {}).setdefault(kind, []).append(list_id)
    return result


_thread_local = threading.local()


def _session() -> requests.Session:
    """Thread-local Session: воркер переиспользует своё соединение вместо нового
    TCP+TLS-рукопожатия на каждую страницу. Сборка МЭИ — это 27 списков общего
    конкурса плюс ~200 квотных, то есть больше двухсот запросов к одному хосту;
    без keep-alive рукопожатие платится каждый раз. Своя сессия на поток →
    потокобезопасно (как в fa_pool)."""
    session = getattr(_thread_local, "mpei_session", None)
    if session is None:
        session = requests.Session()
        _thread_local.mpei_session = session
    return session


def _get(url: str) -> str:
    last_error: Exception | None = None
    for attempt in range(FETCH_RETRIES):
        try:
            response = _session().get(url, timeout=60)
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text
        except requests.HTTPError as exc:
            last_error = exc
            status = exc.response.status_code if exc.response is not None else None
            if status in RETRYABLE_STATUS and attempt < FETCH_RETRIES - 1:
                continue
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt < FETCH_RETRIES - 1:
                continue
            break
    raise last_error or RuntimeError(f"Не удалось загрузить {url}")


def fetch_catalog() -> list[tuple[str, str]]:
    html = _get(CATALOG_URL)
    return _catalog_from_page(html)


UNTRACKED_FALLBACK_PLACES = 30  # аппроксимация мест ТОЛЬКО для непрофильных untracked-программ каскада (вне охвата гарантии реальных мест)

BACC_URL_TEMPLATE = "https://pk.mpei.ru/inform/list{n}bacc.html"
_VACANT_RE = re.compile(r"Количество вакантных мест:\s*(\d+)")
_LIST_ID_NUMBER_RE = re.compile(r"entrants_list(\d+)\.html")

KCP_URL = "https://pk.mpei.ru/info/speclist_simple.html"
_OKSO_SUFFIX_RE = re.compile(r"\s*\(\d{2}\.\d{2}\.\d{2}\b.*\)\s*$")


@dataclass
class KcpGroup:
    """Официальный КЦП одной конкурсной группы (очная форма)."""

    kcp: int  # «Всего» по всем строкам группы
    quotas: int  # особая + отдельная + целевая
    general: int  # места общего конкурса = КЦП − квоты
    rows: int  # строк-профилей в группе (>1 — многопрофильная)
    # Квоты по видам. Нужны по отдельности, а не суммой: недобор считается по
    # каждому виду своим списком (у особой, отдельной и целевой квот они разные).
    special: int = 0
    separate: int = 0
    target: int = 0

    def quota_places(self, kind: str) -> int:
        return {"special": self.special, "separate": self.separate, "target": self.target}[kind]


def _expand_table(table) -> list[list[str]]:
    """Таблица → прямоугольная сетка с раскрытыми rowspan/colspan.

    В таблице КЦП название группы, направление и вступительные испытания
    объединены по вертикали на все профили многопрофильной группы, а часть
    заголовков — по горизонтали. Без раскрытия строки-продолжения начинаются с
    аббревиатуры института («ИРЭ»), и к какой группе они относятся, по «сырым»
    ячейкам не понять.
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


def _kcp_columns(grid: list[list[str]]) -> dict[str, int]:
    """Индексы колонок мест по тексту шапки (а не по номеру — вёрстка меняется)."""
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


def parse_kcp_page(html: str) -> dict[str, KcpGroup]:
    """Официальные КЦП очной формы по названию конкурсной группы.

    Места общего конкурса = «Всего» − особая − отдельная − целевая квота: робот
    моделирует именно общий конкурс по ЕГЭ, без квотных мест.

    Строки одной многопрофильной группы складываются в ОДНО число. У группы один
    конкурсный список, один проходной балл и одна страница волны («Информатика и
    вычислительная техника ИВТИ, ИРЭ бюджет (Очная)») — разброс по институтам вуз
    делает уже внутри, после зачисления, и на конкурс он не влияет.
    """
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="kcp-table")
    if table is None:
        raise ValueError("Не найдена таблица kcp-table на странице КЦП МЭИ")

    grid = _expand_table(table)
    columns = _kcp_columns(grid)

    groups: dict[str, KcpGroup] = {}
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
            by_kind = {name: int(row[columns[name]]) for name in ("special", "separate", "target")}
            quotas = sum(by_kind.values())
        except (ValueError, IndexError):
            continue
        title = " ".join(title.replace("\xa0", " ").split())
        group = groups.get(title)
        if group is None:
            groups[title] = KcpGroup(
                kcp=kcp,
                quotas=quotas,
                general=kcp - quotas,
                rows=1,
                special=by_kind["special"],
                separate=by_kind["separate"],
                target=by_kind["target"],
            )
            continue
        group.kcp += kcp
        group.quotas += quotas
        group.general += kcp - quotas
        group.rows += 1
        group.special += by_kind["special"]
        group.separate += by_kind["separate"]
        group.target += by_kind["target"]
    if not groups:
        raise ValueError("Не удалось извлечь ни одной конкурсной группы из таблицы КЦП МЭИ")
    return groups


def fetch_kcp_groups() -> dict[str, KcpGroup]:
    return parse_kcp_page(_get(KCP_URL))


def kcp_group_title(catalog_title: str) -> str:
    """«Прикладная информатика (09.03.03 Прикладная информатика)» → «Прикладная информатика».

    В каталоге конкурсных списков к названию группы приписан код направления в
    скобках, в таблице КЦП — нет. Срезается только хвост, начинающийся с кода
    ОКСО: скобки внутри самого названия трогать нельзя.

    Пробелы нормализуются: в таблице КЦП встречаются неразрывные пробелы, и без
    этого «Мехатроника, робототехника, машиностроение и автоматизация» не
    находилась в КЦП, хотя она там есть.
    """
    return " ".join(_OKSO_SUFFIX_RE.sub("", catalog_title).replace("\xa0", " ").split())


def _bacc_url_for_list_id(list_id: str) -> str | None:
    match = _LIST_ID_NUMBER_RE.fullmatch(list_id)
    if not match:
        return None
    return BACC_URL_TEMPLATE.format(n=match.group(1))


def _expand_row_cells(row) -> list[str]:
    cells: list[str] = []
    for cell in row.find_all(["td", "th"]):
        text = cell.get_text(" ", strip=True)
        span = int(cell.get("colspan") or 1)
        cells.extend([text] * span)
    return cells


def _rows_and_meta_from_bacc(html: str) -> tuple[list[dict], int | None, int | None, int | None]:
    """Абитуриенты, «Количество вакантных мест» и проходной балл сайта со
    страницы волны зачисления (/inform/list<N>bacc.html, индекс — /inform/list).

    Три вещи со страницы и что с ними делает робот:

    * **строки списка** — вход каскада;
    * **колонка «Высший проходной»** — ОФИЦИАЛЬНЫЙ ВЕРДИКТ вуза: галочка у тех,
      кто реально проходит сюда по высшему приоритету с учётом согласия. Каскад
      её не читает — она уходит в `passing_cutoff` как независимый оракул, с
      которым потом сверяется наш прогноз;
    * **«Количество вакантных мест»** — сколько основных мест ещё свободно
      прямо сейчас. Тоже вердикт сайта, и как источник мест не используется:
      каскад считает от официального КЦП (см. `fetch_kcp_groups`), иначе сверка
      выродилась бы в круг — на list14 вакантных мест ровно 170 и ровно 170
      человек помечены «Высший проходной», то есть число мест и есть ответ.
      Возвращается для сверки в `seat_oracle`.

    Из конкурса исключаются только те, кто выбыл ФАКТИЧЕСКИ («Забрал документы»,
    «Исключен из списка»). Пометка «Зачисляется в другой КГ» — это уже решение
    сайта о распределении, и робот его не берёт: кто куда попадёт по своим
    приоритетам, он обязан вывести сам, иначе сверять его не с чем.
    """
    vacant_match = _VACANT_RE.search(html)
    vacant = int(vacant_match.group(1)) if vacant_match else None

    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if not tables:
        raise ValueError("Таблица конкурсного списка не найдена")
    table = max(tables, key=lambda item: len(item.find_all("tr")))
    rows = table.find_all("tr")
    if len(rows) < 3:
        raise ValueError("Таблица конкурсного списка пуста")

    headers = _expand_row_cells(rows[0])
    code_idx = header_index(headers, "уникальный код")
    score_idx = header_index(headers, "сумма")
    consent_idx = header_index(headers, "согласие")
    priority_idx = header_index(headers, "приоритет")
    note_idx = header_index(headers, "примечание")
    # Оракул сайта. Берётся «Высший проходной», а не «Основной высший»: первая
    # учитывает согласие (как и робот с require_consent), вторая — чистый ранг.
    top_idx = header_index(headers, "высший проходной")
    if None in (code_idx, score_idx, consent_idx, priority_idx):
        raise ValueError("Не удалось определить колонки таблицы МЭИ")

    result: list[dict] = []
    passing_scores: list[int] = []
    has_top_column = top_idx is not None
    for row in rows[2:]:
        cells = _expand_row_cells(row)
        if len(cells) <= max(code_idx, score_idx, consent_idx, priority_idx):
            continue
        note = cells[note_idx] if note_idx is not None and note_idx < len(cells) else ""
        if "Забрал документы" in note or "Исключен из списка" in note:
            continue
        score = to_int(cells[score_idx])
        if not score or score <= 0:
            continue
        code = cells[code_idx].strip()
        if not code:
            continue
        if has_top_column and top_idx < len(cells) and normalize_yes(cells[top_idx]):
            passing_scores.append(score)
        result.append(
            {
                "code": code,
                "score": score,
                "consent": normalize_yes(cells[consent_idx]),
                "priority": to_int(cells[priority_idx]) or 99,
            }
        )
    # None — колонки вердикта не было (сверять не с чем); 0 — колонка есть, но
    # сюда не проходит никто (места открыты для любого балла).
    cutoff = (min(passing_scores) if passing_scores else 0) if has_top_column else None
    # Сколько человек сайт отметил проходящими. Без этого числа проходной балл
    # сайта нельзя интерпретировать: на направлении, где отмечен один человек
    # из полусотни мест, «проходной» — это просто его балл, а не порог.
    passing_count = len(passing_scores) if has_top_column else None
    return result, vacant, cutoff, passing_count


def fetch_list_rows_and_meta(list_id: str) -> tuple[list[dict], int | None, int | None, int | None]:
    url = _bacc_url_for_list_id(list_id) or urljoin(CATALOG_URL, list_id)
    html = _get(url)
    return _rows_and_meta_from_bacc(html)


def fetch_list_vacant(list_id: str) -> int | None:
    """«Количество вакантных мест» со страницы волны — сколько основных мест
    ещё свободно прямо сейчас. Каскад это число не использует (см.
    `_rows_and_meta_from_bacc`), оно нужно сверке в `seat_oracle`: разница с
    плановым КЦП общего конкурса показывает, сколько мест вуз перенёс из
    незаполненных квот."""
    url = _bacc_url_for_list_id(list_id) or urljoin(CATALOG_URL, list_id)
    match = _VACANT_RE.search(_get(url))
    return int(match.group(1)) if match else None


QUOTA_URL_TEMPLATE = "https://pk.mpei.ru/info/{list_id}"


def fetch_quota_enrolled(list_id: str) -> set[str]:
    """КОДЫ тех, кто реально занял место по этому квотному списку.

    Квотные списки лежат по другому адресу, чем списки общего конкурса:
    /info/entrants_listNNN.html вместо /inform/listNNNbacc.html (последний на
    квотном id молча отдаёт индексную страницу). Берём отметки «Высший
    проходной» — это вердикт вуза по КВОТНОМУ конкурсу, отдельному от общего.

    Возвращаются именно коды, а не количество, по двум причинам:

    * **Один человек попадает в несколько списков.** У целевой квоты список
      свой на каждого заказчика (у «Электроэнергетики» их 20), и абитуриент
      подаёт сразу к нескольким. Складывая количества, мы считали его столько
      раз, в скольких списках он есть: выходило 127 «занятых» мест из 90.
    * **Забравшие документы место не занимают.** Их 18% от всех отмеченных
      (120 из 660), и засчитывать их как занявших — значит занижать недобор.
    """
    soup = BeautifulSoup(_get(QUOTA_URL_TEMPLATE.format(list_id=list_id)), "lxml")
    tables = soup.find_all("table")
    if not tables:
        return set()
    rows = max(tables, key=lambda item: len(item.find_all("tr"))).find_all("tr")
    if len(rows) < 3:
        return set()
    headers = _expand_row_cells(rows[0])
    top_idx = header_index(headers, "высший проходной")
    code_idx = header_index(headers, "уникальный код")
    note_idx = header_index(headers, "примечание")
    if top_idx is None or code_idx is None:
        return set()
    enrolled: set[str] = set()
    for row in rows[2:]:
        cells = _expand_row_cells(row)
        if len(cells) <= max(top_idx, code_idx) or not normalize_yes(cells[top_idx]):
            continue
        note = cells[note_idx] if note_idx is not None and note_idx < len(cells) else ""
        if "Забрал документы" in note or "Исключен" in note:
            continue
        code = cells[code_idx].strip()
        if code:
            enrolled.add(code)
    return enrolled


@dataclass
class QuotaShortfall:
    """Недобор по квотам одной конкурсной группы.

    Зачем считать самим, а не читать «вакантные места» со страницы волны:
    вакантные места — это уже ответ сайта (их ровно столько же, сколько людей
    он отметил проходящими), и каскад, взяв их на вход, сверять было бы не с
    чем. Недобор же выводится из НЕЗАВИСИМЫХ данных: официальный КЦП по видам
    квот минус фактически зачисленные по квотным спискам. Это наш собственный
    вывод, а не подсказка вуза.

    Целевая квота считается наравне с остальными — незанятые целевые места вуз
    тоже возвращает в общий конкурс. Проверено на 27 группах: с учётом целевой
    средняя ошибка предиктa 1.85 места, без неё 3.11. Нагляднее всего на
    «Интеллектуальных системах», где по плану мест общего конкурса ноль, а на
    волне 26 — и они объясняются именно недобором квот.
    """

    special: int = 0
    separate: int = 0
    target: int = 0

    @property
    def total(self) -> int:
        return self.special + self.separate + self.target


def compute_quota_shortfall(
    group: KcpGroup, lists_by_kind: dict[str, list[str]], group_title: str = ""
) -> QuotaShortfall:
    """Незанятые квотные места, которые вуз возвращает в общий конкурс."""
    shortfall = QuotaShortfall()
    for kind in ("special", "separate", "target"):
        places = group.quota_places(kind)
        if places <= 0:
            continue
        # Объединение, а не сумма: один абитуриент встречается в нескольких
        # списках одной квоты (у целевой список свой на каждого заказчика).
        enrolled: set[str] = set()
        for list_id in lists_by_kind.get(kind, []):
            enrolled |= fetch_quota_enrolled(list_id)
        if len(enrolled) > places:
            # Занявших больше, чем мест по КЦП, быть не может — значит по этой
            # квоте модель не работает (например, список общий на несколько
            # конкурсных групп). Молча писать «недобора нет» нельзя: это
            # прячет отказ модели под видом обычного результата.
            print(
                f"ВНИМАНИЕ: у группы МЭИ «{group_title}» по квоте «{kind}» мест {places}, "
                f"а занявшими отмечено {len(enrolled)} человек — больше, чем мест. "
                "Недобор по этой квоте не учитывается; предикт мест будет "
                "пессимистичнее реального.",
                file=sys.stderr,
            )
            continue
        setattr(shortfall, kind, places - len(enrolled))
    return shortfall


def tracked_programs() -> list[ProgramConfig]:
    return [p for p in load_programs() if p.university == "МЭИ" and p.parser == "mpei"]


def _tracked_by_list_id() -> dict[str, ProgramConfig]:
    mapping: dict[str, ProgramConfig] = {}
    for program in tracked_programs():
        list_id = _extract_list_id(program.list_url)
        if list_id:
            mapping[list_id] = program
    return mapping


class MpeiFullPool:
    def build(self, *, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
        if use_cache:
            cached = self._load_cache()
            if cached is not None:
                return cached
        try:
            catalog, kcp_groups, catalog_html = self._catalog_and_kcp()
            shortfalls = self._quota_shortfalls(catalog, kcp_groups, catalog_html)
            people, programs = self._fetch_all(catalog, kcp_groups, shortfalls)
            fetched_at = datetime.now(timezone.utc).isoformat()
            self._save_cache(people, programs, fetched_at)
            return people, programs, fetched_at, False
        except Exception:
            stale = self._load_cache(ignore_ttl=True)
            if stale is not None:
                return stale
            raise

    @staticmethod
    def _quota_shortfalls(
        catalog: list[tuple[str, str]], kcp_groups: dict[str, KcpGroup], html: str
    ) -> dict[str, int]:
        """Название группы каталога → сколько квотных мест осталось незанятыми.

        Считаем сами из независимых данных (официальный КЦП по видам квот минус
        зачисленные по квотным спискам), а не читаем «вакантные места» волны:
        те равны числу людей, которых сайт уже отметил проходящими, и опираться
        на них — значит подглядывать в ответ.

        Недоступность квотных списков не должна ронять сборку: без них останется
        плановый КЦП, то есть прежнее, более пессимистичное поведение.
        """
        try:
            quota_lists = quota_lists_from_page(html)
        except Exception as exc:  # noqa: BLE001
            print(f"ВНИМАНИЕ: не удалось разобрать квотные списки МЭИ ({exc}).", file=sys.stderr)
            return {}

        def one(item: tuple[str, str]) -> tuple[str, int] | None:
            title, _ = item
            group = kcp_groups.get(kcp_group_title(title))
            if group is None:
                return None
            try:
                return title, compute_quota_shortfall(
                    group, quota_lists.get(title, {}), title
                ).total
            except Exception:  # noqa: BLE001
                return None

        result: dict[str, int] = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            for item in executor.map(one, catalog):
                if item is not None:
                    result[item[0]] = item[1]
        return result

    @staticmethod
    def _catalog_and_kcp() -> tuple[list[tuple[str, str]], dict[str, KcpGroup], str]:
        """Каталог конкурсных групп, суженный до основного кампуса, и их КЦП.

        В каталоге списков 39 групп, в московской таблице КЦП очной формы — 31,
        и 12 групп каталога в ней отсутствуют: это филиалы (Смоленский,
        Дубненский) и их программы, у которых свои КЦП на отдельных страницах.
        Мы их выбрасываем — интересен основной кампус, а без них у мест остаётся
        ЕДИНСТВЕННЫЙ источник (официальный КЦП), и качать нужно 27 списков
        вместо 39.

        Цена решения измерена на живом пуле: из 1154 человек с выбором в филиале
        956 подавали ТОЛЬКО в филиалы (в московском конкурсе их и не было), и
        лишь у 69 филиал стоит выше московского приоритета — только они в модели
        перетекут в московский конкурс, которого в жизни не увидят. Смещение
        одностороннее и пессимистичное (конкурентов чуть больше, чем на самом
        деле), то есть в безопасную сторону.

        Если таблица КЦП недоступна — каталог не сужаем и работаем на резерве:
        лучше посчитать по конфигу, чем не посчитать вовсе.
        """
        catalog_html = _get(CATALOG_URL)
        catalog = _catalog_from_page(catalog_html)
        try:
            kcp_groups = fetch_kcp_groups()
        except Exception as exc:  # noqa: BLE001
            print(
                f"ВНИМАНИЕ: не удалось получить официальные КЦП МЭИ ({exc}) — каталог не "
                "сужен до основного кампуса, места взяты из резерва config; сверка "
                "подсветит это как «не live».",
                file=sys.stderr,
            )
            return catalog, {}, catalog_html
        main_campus = [item for item in catalog if kcp_group_title(item[0]) in kcp_groups]
        if not main_campus:
            raise ValueError("Ни одна конкурсная группа каталога МЭИ не нашлась в таблице КЦП")
        return main_campus, kcp_groups, catalog_html

    def _fetch_all(
        self,
        catalog: list[tuple[str, str]],
        kcp_groups: dict[str, KcpGroup],
        shortfalls: dict[str, int] | None = None,
    ) -> tuple[list[RobotPerson], list[RobotProgram]]:
        raw_programs: list[RobotProgram] = []
        rows_by_list: dict[str, list[dict]] = {}
        cutoff_by_list: dict[str, int] = {}
        vacant_by_list: dict[str, int] = {}
        passing_by_list: dict[str, int] = {}
        failed_lists: list[str] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(fetch_list_rows_and_meta, list_id): (title, list_id) for title, list_id in catalog
            }
            for future in as_completed(futures):
                title, list_id = futures[future]
                try:
                    rows, vacant, cutoff, passing_count = future.result()
                except Exception as exc:
                    failed_lists.append(list_id)
                    print(
                        f"ВНИМАНИЕ: не удалось загрузить конкурсный список МЭИ {title!r} "
                        f"({list_id}) после исчерпания ретраев ({exc}) — направление "
                        "попадёт в пул с пустым списком абитуриентов (места и заголовок "
                        "сохранятся, но конкурс по нему не будет учтён в этом цикле сборки).",
                        file=sys.stderr,
                    )
                    continue
                rows_by_list[list_id] = rows
                if cutoff is not None:
                    cutoff_by_list[list_id] = cutoff
                if vacant is not None:
                    vacant_by_list[list_id] = vacant
                if passing_count is not None:
                    passing_by_list[list_id] = passing_count

        if catalog and len(failed_lists) / len(catalog) > MAX_FAILED_FRACTION:
            raise RuntimeError(
                f"Массовый сбой загрузки конкурсных списков МЭИ: не удалось загрузить "
                f"{len(failed_lists)} из {len(catalog)} ({len(failed_lists) / len(catalog):.0%}) — "
                f"превышен порог {MAX_FAILED_FRACTION:.0%}, похоже на системный сбой "
                "(троттлинг или недоступность сайта), а не единичный сетевой сбой одного "
                "списка. Сборка пула прервана, чтобы не перезаписать кэш почти пустым датасетом."
            )

        tracked = _tracked_by_list_id()
        people: dict[str, RobotPerson] = {}
        for title, list_id in catalog:
            tracked_program = tracked.get(list_id)
            key = str(tracked_program.id) if tracked_program else list_id
            # Места — только официальный КЦП общего конкурса, как kcp.php у
            # СТАНКИНа и plan у МИРЭА. НЕ «вакантные места» со страницы волны:
            # в них уже зашит ответ сайта о том, кто зачислен (на list14 их 170
            # и ровно 170 человек помечены «Высший проходной»), и каскад, взяв
            # их на вход, сверять было бы не с чем. Резерв — только на случай
            # недоступности таблицы КЦП.
            group = kcp_groups.get(kcp_group_title(title))
            if group is not None:
                places, seat_source = group.general, "live"
            elif tracked_program:
                places = tracked_program.budget_places or None
                seat_source = "config"
            else:
                places = UNTRACKED_FALLBACK_PLACES
                seat_source = "approx"
            raw_programs.append(
                RobotProgram(
                    key=key,
                    title=title,
                    budget_places=places,
                    tracked_id=tracked_program.id if tracked_program else None,
                    seat_source=seat_source,
                    passing_cutoff=cutoff_by_list.get(list_id),
                    vacant_places=vacant_by_list.get(list_id),
                    quota_shortfall=(shortfalls or {}).get(title),
                    site_passing_count=passing_by_list.get(list_id),
                )
            )
            for row in rows_by_list.get(list_id, []):
                choice = ProgramChoice(program_key=key, priority=row["priority"])
                person = people.get(row["code"])
                if person is None:
                    people[row["code"]] = RobotPerson(
                        code=row["code"], score=row["score"], consent=row["consent"], choices=[choice]
                    )
                    continue
                person.score = max(person.score, row["score"])
                person.consent = person.consent or row["consent"]
                person.choices.append(choice)
        return list(people.values()), raw_programs

    def _load_cache(self, *, ignore_ttl: bool = False):
        if not CACHE_PATH.exists():
            return None
        try:
            payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            fetched_at = payload.get("fetched_at", "")
            fetched_dt = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - fetched_dt).total_seconds()
            if not ignore_ttl and age > CACHE_TTL_SEC:
                return None
            people = [
                RobotPerson(
                    code=item["code"],
                    score=int(item["score"]),
                    consent=bool(item["consent"]),
                    is_bvi=bool(item.get("is_bvi")),
                    choices=[ProgramChoice(**choice) for choice in item.get("choices", [])],
                )
                for item in payload.get("people", [])
            ]
            programs = [RobotProgram(**item) for item in payload.get("programs", [])]
            if payload.get("pool_scope") != POOL_SCOPE or len(programs) < MIN_CATALOG_PROGRAMS:
                return None
            return people, programs, fetched_at, True
        except (ValueError, KeyError, json.JSONDecodeError, TypeError):
            return None

    def _save_cache(self, people: list[RobotPerson], programs: list[RobotProgram], fetched_at: str) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": fetched_at,
            "pool_scope": POOL_SCOPE,
            "people": [
                {
                    "code": p.code,
                    "score": p.score,
                    "consent": p.consent,
                    "is_bvi": p.is_bvi,
                    "choices": [asdict(c) for c in p.choices],
                }
                for p in people
            ],
            "programs": [asdict(p) for p in programs],
        }
        CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_mpei_full_pool(*, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
    return MpeiFullPool().build(use_cache=use_cache)


def read_mpei_cached_pool() -> tuple[list[RobotPerson], list[RobotProgram], str, bool] | None:
    """Отдаёт кэш МЭИ ЛЮБОГО возраста и никогда не ходит в сеть. См. read_fa_cached_pool."""
    return MpeiFullPool()._load_cache(ignore_ttl=True)
