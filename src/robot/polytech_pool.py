from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urljoin

import certifi
import requests
from bs4 import BeautifulSoup

from ..config_loader import load_programs
from ..models import ProgramConfig
from ..parsers.utils import normalize_yes, to_int
from .models import ProgramChoice, RobotPerson, RobotProgram

CATALOG_PAGE_URL = "https://mospolytech.ru/postupayushchim/priem-v-universitet/rating-abiturientov/"
LIST_URL = "https://mospolytech.ru/postupayushchim/priem-v-universitet/rating-abiturientov/fio_list_curl.php"

# select1="000000066_01" — институт "Москва (бакалавриат/специалитет)" в
# выпадающем списке страницы каталога. Единственный уровень/кампус, который
# симулирует робот (магистратура, аспирантура и филиалы — вне охвата).
BASE_PARAMS = {
    "select1": "000000066_01",
    "eduForm": "Очная",
    "eduFin": "Бюджетная основа",
    "f": "1",
}

FETCH_RETRIES = 3
RETRYABLE_STATUS = {500, 502, 503, 504}

_INTERMEDIATE_CERT_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "certs" / "globalsign-gcc-r3-dv-tls-ca-2020.pem"
)

# НЕ /tmp: он мир-доступен на запись и путь предсказуем, поэтому файл там был
# бы удобной мишенью для подмены доверенного CA между записью и чтением
# OpenSSL (тот самый MITM, из-за которого эта функция и отказалась от
# verify=False). data/cache/ — наш каталог, gitignored, права процесса.
_CA_BUNDLE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "polytech_ca_bundle.pem"

# Кэш собранного бандла на весь процесс: пересобирать его на каждый запрос
# незачем, сертификаты не меняются в рамках одного запуска. Лок — потому что
# _fetch_all зовёт _request из MAX_WORKERS потоков параллельно; без него два
# воркера могли бы одновременно писать и перезаписывать глобал (сейчас гонка
# случайно снята тем, что build() зовёт fetch_catalog() до пула в главном
# потоке, но это неявная зависимость, а не гарантия).
_ca_bundle_lock = threading.Lock()
_ca_bundle_path: str | None = None


def _ca_bundle() -> str:
    """Путь к CA-бандлу certifi + промежуточный сертификат GlobalSign.

    Сервер mospolytech.ru не досылает промежуточный сертификат (`openssl
    s_client` → `Verify return code: 21`). curl берёт недостающее звено из
    системного хранилища, Python с certifi — нет. Поэтому подкладываем его
    сами. `verify=False` не годится: это источник официальных чисел, и
    подменить его смог бы кто угодно в канале.
    """
    global _ca_bundle_path
    with _ca_bundle_lock:
        # Проверяем не только «путь уже вычислен», но и что файл физически
        # ещё на диске: data/cache/ чистится вручную как штатная операция
        # (это gitignored рабочий кэш), и если бандл снесли при живом боте,
        # закэшированный путь без этой проверки указывал бы в никуда —
        # все дальнейшие запросы Политеха падали бы по TLS до рестарта.
        if _ca_bundle_path is not None and Path(_ca_bundle_path).exists():
            return _ca_bundle_path
        _CA_BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=_CA_BUNDLE_PATH.parent, prefix=".polytech_ca_bundle-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(Path(certifi.where()).read_bytes())
                out.write(b"\n")
                out.write(_INTERMEDIATE_CERT_PATH.read_bytes())
            # os.replace — атомарная публикация на уровне ФС: читатель видит
            # либо старое содержимое целиком, либо новое, никогда огрызок
            # частичной записи, и подменить итоговый файл через симлинк между
            # записью и чтением OpenSSL здесь нельзя.
            os.replace(tmp_path, _CA_BUNDLE_PATH)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        _ca_bundle_path = str(_CA_BUNDLE_PATH)
        return _ca_bundle_path


def _request(
    method: str,
    url: str,
    *,
    data: dict | None = None,
    session: requests.Session | None = None,
    timeout: int = 60,
) -> requests.Response:
    caller = session.request if session is not None else requests.request
    last_error: Exception | None = None
    for attempt in range(FETCH_RETRIES):
        try:
            response = caller(method, url, data=data, verify=_ca_bundle(), timeout=timeout)
            response.raise_for_status()
            return response
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


def fetch_catalog() -> list[str]:
    try:
        response = _request("GET", CATALOG_PAGE_URL)
        soup = BeautifulSoup(response.text, "lxml")
        select = soup.find("select", id="select2")
        if select is not None:
            codes = [
                opt.get("spec_code", "").strip()
                for opt in select.find_all("option")
                if opt.get("value") == BASE_PARAMS["select1"] and opt.get("spec_code", "").strip()
            ]
            if codes:
                return codes
    except requests.RequestException:
        pass
    return list(FALLBACK_CATALOG)


def fetch_direction_html(spec: str, *, session: requests.Session | None = None) -> str:
    # Пагинации на этом эндпоинте нет: сайт отдаёт список направления целиком
    # одним ответом (отсюда и таймаут 180 — страница может весить единицы
    # мегабайт). Цикла по страницам, как у СТАНКИНа, здесь быть не должно.
    response = _request(
        "POST",
        LIST_URL,
        data={**BASE_PARAMS, "specCode": spec},
        session=session,
        timeout=180,
    )
    return response.text


# Проверенный вживую список направлений (институт "Москва
# (бакалавриат/специалитет)", select1=000000066_01) — резерв ровно на один
# случай: страница каталога не ответила. Это НЕ источник чисел о местах или
# баллах (те берутся только из ответа fio_list_curl.php) — поэтому асимметрия
# ошибок здесь не симметрична. Лишнее направление в резерве безвредно: оно
# просто загрузится и даст ещё один RobotProgram. Недостающее — это молчаливая
# потеря целого направления из симуляции. Значит резерв должен содержать ВСЁ,
# что реально отдаёт сайт, а не подогнанное под ожидаемое число.
#
# Само число направлений — НЕ инвариант: каталог живой и меняется (сверено
# живьём 2026-08-06: 66 значений; более ранний ручной снимок ожидал 65 — это
# был снимок в моменте, а не контракт). Защита от усечённого/сломанного
# каталога — порог MIN_CATALOG_PROGRAMS в пуле (задача 2), а не точное
# равенство конкретной цифре.
FALLBACK_CATALOG: list[str] = [
    "01.03.02_Программирование и интеллектуальные системы управления транспортом",
    "08.03.01_Промышленное и гражданское строительство",
    "08.03.01_Урбанистика и строительство",
    "09.03.01_Веб-разработка и нейросетевые технологии; Веб-технологии и продуктовый дизайн",
    "09.03.01_Разработка инженерного программного обеспечения",
    "09.03.01_Программное обеспечение информационных систем",
    "09.03.01_Интеллектуальные беспилотные системы",
    "09.03.01_Системная и программная инженерия",
    "09.03.01_Программирование электронных устройств и систем; Информационные системы умных пространств",
    "09.03.01_Искусственный интеллект и машинное обучение",
    "09.03.02_Автоматизированные системы обработки информации и управления; Информационные технологии в креативных индустриях; Технологии дополненной и виртуальн...",
    "09.03.02_Автоматизированные системы обработки информации и управления",
    "09.03.02_Интеллектуальные информационно-измерительные системы",
    "09.03.03_Разработка и интеграция бизнес-приложений",
    "09.03.03_Большие и открытые данные",
    "09.03.03_Информационные технологии управления бизнесом",
    "09.03.03_Корпоративные решения на платформе 1С",
    "10.03.01",
    "10.05.03",
    "11.03.01_Интеллектуальная радиоэлектроника и промышленный интернет вещей",
    "13.03.01_Интеллектуальные тепловые энергосистемы",
    "13.03.02_Электрооборудование и промышленная электроника",
    "13.03.02_Электроснабжение",
    "13.03.02_Электроника и технологии сенсорики",
    "13.03.03_Поршневые двигатели и турбомашины",
    "15.03.01_Машины и технологии обработки материалов давлением; Оборудование и технологии сварочного производства; Высокоэффективные технологические процессы и...",
    "15.03.01_Комплексные технологические процессы и оборудование машиностроения",
    "15.03.01_Оборудование и технологии аддитивного производства",
    "15.03.02_Реверс-инжиниринг процессов и оборудования",
    "15.03.03_Программирование и цифровые технологии в динамике и прочности",
    "15.03.04_Роботы и робототехнические комплексы",
    "15.03.04_Автоматизация технологических производств",
    "15.03.05_Компьютерное проектирование оборудования и производств",
    "15.03.05_Средства автоматизации и базы данных для проектирования технологических производств",
    "15.03.06_Мехатронные системы в автоматизированном производстве",
    "19.03.01_Биотехнология",
    "20.03.01_Экологическая безопасность и охрана труда",
    "22.03.01_Перспективные материалы и технологии",
    "22.03.01_Инженерия полимерных материалов",
    "22.03.02_Инновации в металлургии",
    "23.03.02_Гоночные автомобили и мотоциклы",
    "23.03.03_Автомобили и транспортно-логистические системы",
    "23.03.03_Инжиниринг и эксплуатация транспортных систем",
    "23.05.01_Спортивные транспортные средства",
    "23.05.01_Перспективные автомобили",
    "23.05.01_Автомобили и автомобильный сервис",
    "23.05.01_Компьютерный инжиниринг в автомобилестроении",
    "27.03.02_Управление качеством на производстве",
    "27.03.04_Электронные системы управления",
    "27.03.05_Управление инновационной деятельностью",
    "27.03.05_Аддитивные технологии",
    "29.03.03_Дизайн и технологии создания визуального контента; Бизнес-процессы упаковочного производства",
    "29.03.04_Разработка и производство изделий промышленного дизайна; Художественное проектирование и цифровые технологии в ювелирном производстве",
    "29.03.04_Дизайн и конструирование рекламных и арт-объектов",
    "38.03.01_Экономика предприятий и организаций",
    "38.03.01_International business and foreign economic (study in English)",
    "38.03.02_Управление бизнес-процессами",
    "38.03.02_Business process Management (study in English)",
    "38.03.03_Экономико-правовое обеспечение трудовых процессов",
    "42.03.01_Реклама и связи с общественностью в цифровых медиа; Интегрированные бренд-коммуникации",
    "42.03.02_Мультимедийная журналистика и цифровое продюсирование; Корпоративные медиа и бренд-журналистика",
    "42.03.03_Книгоиздательское дело; Газетно-журнальное издательское дело",
    "54.03.01_Транспортный и промышленный дизайн",
    "54.03.01_Графический дизайн мультимедиа",
    "54.03.01_Цифровой дизайн",
    "54.05.03_Художник анимации и компьютерной графики; Художник-график (Оформление печатной продукции)",
]


# --- Разбор ответа fio_list_curl.php -----------------------------------

_HEADER_CODE_RE = re.compile(r"^\d\d\.\d\d\.\d\d")
_TAG_RE = re.compile(r"<[^>]+>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_TR_RE = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_TD_RE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S | re.I)


def _cell_text(raw_cell_html: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", raw_cell_html))).strip()


def _rows_from_html(page_html: str) -> list[list[str]]:
    """Все <tr> страницы направления как списки текстов ячеек.

    Разбор — регулярками по сырому HTML, а не BeautifulSoup. Две причины:

    1. Производительность. Ответ на одно направление — до ~11 МБ HTML с
       несколькими тысячами строк; BeautifulSoup+lxml строит полное дерево
       Python-объектов, и на 66 направлениях (даже в 6 потоков — GIL не
       отпускается на чистом обходе дерева) сборка пула растягивалась до
       ~120 c при бюджете < 60 c. Регэксп-скан того же документа занимает
       ~0.1–0.2 c.
    2. Надёжность. На части направлений (например «Системная и программная
       инженерия») lxml из-за неаккуратной вёрстки сайта где-то в документе
       схлопывает большой кусок таблицы в ОДНУ <td> — get_text() на ней
       отдаёт склеенный текст всей области (десятки тысяч «слов»), и такая
       строка-мусор может перехватить поиск заголовка раньше настоящего.
       Плоский регэксп-скан по тексту такому сбою не подвержен: он просто
       парами находит теги, не пытаясь восстановить дерево.

    Комментарии вычищаются ИЗ ВСЕГО ДОКУМЕНТА заранее, а не после разбора на
    ячейки: в шаблоне строки абитуриента сайт физически оставляет закомме-
    ченную <td>...</td> (дубль «Суммы баллов по предметам»). Если не убрать
    комментарии до поиска <td>, регэксп посчитает её настоящей ячейкой и все
    индексы после неё съедут на одну позицию у части строк.
    """
    cleaned = _COMMENT_RE.sub(" ", page_html)
    rows: list[list[str]] = []
    for match in _TR_RE.finditer(cleaned):
        cells = [_cell_text(cell) for cell in _TD_RE.findall(match.group(1))]
        if cells:
            rows.append(cells)
    return rows


class EmptyDirection(Exception):
    """Направление без участников конкурса — не сбой разбора.

    Сайт честно отдаёт пустую шапку (9 ячеек, но cells[0] и cells[1] пусты)
    вместе с «УЧАСТВУЮЩИЕ В КОНКУРСЕ: 0» в тексте страницы: запрос удался с
    первой попытки, вёрстка не менялась, просто на направление никто не подал
    документы. Отличать от ValueError важно, чтобы не засорять `failed`:
    14 из 66 направлений (август 2026) устойчиво пустые, и если считать их
    сбоями, MAX_FAILED_FRACTION перестаёт измерять настоящие сбои разбора.
    """


def _parse_header(rows: list[list[str]]) -> tuple[str, str, int]:
    """Шапка направления: (код ОКСО, название, места общего конкурса).

    Места — это индекс 7 («Остаток бюджетных мест на общий конкурс»), а НЕ
    индекс 2 («Количество бюджетных мест») — тот включает квоты (целевую,
    особую, отдельную) и завышает то, что реально разыгрывается в общем
    конкурсе, который симулирует робот.
    """
    empty_header_seen = False
    for cells in rows:
        if len(cells) != 9:
            continue
        if _HEADER_CODE_RE.match(cells[0]):
            return cells[0], cells[1], int(cells[7])
        if not cells[0].strip():
            empty_header_seen = True
    if empty_header_seen:
        raise EmptyDirection("шапка направления пуста — участников конкурса нет")
    raise ValueError(
        "Не найдена строка шапки направления (9 ячеек, код вида XX.XX.XX) — "
        "сайт мог поменять вёрстку ответа fio_list_curl.php. Молча подставлять "
        "число мест нельзя: именно из-за этого места дважды протухали."
    )


def _exact_header_index(cells: list[str], name: str) -> int | None:
    """Индекс ячейки, ТОЧНО равной name (без учёта регистра/пробелов).

    header_index() из parsers.utils ищет по вхождению подстроки — она не
    годится для колонки «Приоритет»: это подстрока и «Балл приоритет 1», и
    «Основной высший приоритет», и «Высший проходной приоритет», так что
    первый же substring-матч попал бы не туда. Нужно точное совпадение.
    """
    target = re.sub(r"\s+", " ", name.strip().lower())
    for index, cell in enumerate(cells):
        if re.sub(r"\s+", " ", cell.strip().lower()) == target:
            return index
    return None


# Реальная строка заголовка таблицы абитуриентов — это несколько десятков
# ячеек (обычных колонок ИТ-направлений — 23, у самых «широких» творческих —
# заметно меньше полусотни). Верхняя граница нужна, чтобы не спутать её со
# строкой-мусором: замечено, что при DOM-разборе (BeautifulSoup/lxml) части
# страниц Политеха из-за битой вёрстки где-то раньше в документе схлопывают
# огромный кусок таблицы в ОДНУ <td>, и get_text() на ней возвращает текст
# всей схлопнутой области — такая строка тоже проходит проверку «есть код +
# есть балл» и перехватывает поиск раньше настоящего заголовка. У нашего
# регэксп-разбора (см. _rows_from_html) такого сбоя нет, но граница оставлена
# как дешёвая страховка — настоящих заголовков с сотней и более колонок на
# этом сайте не бывает.
_MAX_HEADER_CELLS = 100


def _people_columns(rows: list[list[str]]) -> dict[str, int | None] | None:
    """Позиции нужных колонок в таблице абитуриентов по тексту заголовка.

    НЕ фиксированные номера: число колонок «Балл приоритет N» у списка
    зависит от направления — у обычных (ИТ и т.п.) их 3, у творческих
    (архитектура/дизайн/графика) может быть больше, и тогда все индексы
    после них съезжают. Единственный устойчивый способ — искать колонки по
    названию в строке заголовка этой же таблицы, как в stankin_pool/mpei_pool.

    «Высший проходной приоритет» (top) — ОРАКУЛ сайта, а не обязательный
    столбец для сборки списка. Как у stankin_pool._rows_from_table: если его
    нет, `top` = None, и `_parse_people` пометит top_passing=None у всех
    строк — сверка проходного балла станет недоступна, но сами абитуриенты и
    их места по-прежнему разберутся. Раньше top_idx был в общем гейте вместе
    с обязательными колонками — тогда отсутствие только ЭТОЙ одной колонки
    роняло всё направление (ValueError → в пуле остаётся seat_source=None
    вместо уже успешно разобранного budget_places из шапки), хотя бриф прямо
    требовал копировать поведение stankin, где отсутствие top допустимо.
    """
    for cells in rows:
        if len(cells) > _MAX_HEADER_CELLS:
            continue
        code_idx = _exact_header_index(cells, "уникальный код")
        score_idx = _exact_header_index(cells, "конкурсный балл")
        if code_idx is None or score_idx is None:
            continue
        consent_idx = _exact_header_index(cells, "согласие")
        priority_idx = _exact_header_index(cells, "приоритет")
        if consent_idx is None or priority_idx is None:
            continue
        return {
            "code": code_idx,
            "score": score_idx,
            "consent": consent_idx,
            "priority": priority_idx,
            "top": _exact_header_index(cells, "высший проходной приоритет"),
        }
    return None


def _parse_people(rows: list[list[str]]) -> list[dict]:
    """Строки абитуриентов: колонки определяются по заголовку таблицы,
    строки — первая ячейка (порядковый номер) числовая."""
    columns = _people_columns(rows)
    if columns is None:
        raise ValueError(
            "Не найдена таблица абитуриентов Политеха (нет колонок «Уникальный "
            "код»/«Конкурсный балл»/«Согласие»/«Приоритет») — сайт мог поменять "
            "вёрстку ответа fio_list_curl.php."
        )
    top_col = columns["top"]
    # last_idx — по ОБЯЗАТЕЛЬНЫМ колонкам: top может отсутствовать вовсе, и
    # тогда его не должно быть в границе длины строки.
    last_idx = max(v for k, v in columns.items() if k != "top")

    result: list[dict] = []
    for cells in rows:
        if len(cells) <= last_idx or not cells[0].strip().isdigit():
            continue
        score = to_int(cells[columns["score"]])
        if not score or score <= 0:
            continue
        code = cells[columns["code"]].strip()
        if not code:
            continue
        priority_cell = cells[columns["priority"]].strip()
        if top_col is not None and top_col < len(cells):
            top_passing_cell = cells[top_col].strip()
            # «Проходит сюда»: высший проходной приоритет непуст и равен
            # заявленному приоритету абитуриента на это направление.
            top_passing: bool | None = bool(top_passing_cell) and top_passing_cell == priority_cell
        else:
            # Колонки «Высший проходной приоритет» нет вовсе — оракула сайта
            # для этого направления нет, а не «никто не проходит».
            top_passing = None
        result.append(
            {
                "code": code,
                "score": score,
                "consent": normalize_yes(cells[columns["consent"]]),
                "priority": to_int(priority_cell) or 99,
                "top_passing": top_passing,
            }
        )
    return result


def _passing_cutoff(rows: list[dict]) -> int | None:
    """Проходной балл среди прошедших. Семантика — как в stankin_pool."""
    if not any(row.get("top_passing") is not None for row in rows):
        return None
    scores = [row["score"] for row in rows if row.get("top_passing") is True]
    return min(scores) if scores else 0


def _passing_count(rows: list[dict]) -> int | None:
    """Сколько абитуриентов сайт отметил проходящими. Как в stankin_pool."""
    if not any(row.get("top_passing") is not None for row in rows):
        return None
    return sum(1 for row in rows if row.get("top_passing") is True)


# --- Сборка пула ---------------------------------------------------------

POOL_SCOPE = "full"
MIN_CATALOG_PROGRAMS = 50
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "polytech_robot_pool.json"
CACHE_TTL_SEC = 7200
MAX_WORKERS = 6
# Доля упавших направлений, после которой сбой считается массовым/системным
# (троттлинг, авария сайта, смена вёрстки), а не единичным сбоем одного
# направления — при превышении _fetch_all бросает исключение, чтобы build()
# откатился на устаревший кэш вместо сохранения почти пустого датасета.
MAX_FAILED_FRACTION = 0.5

_thread_local = threading.local()


def _session() -> requests.Session:
    """Thread-local Session: воркер переиспользует TCP+TLS-соединение вместо
    нового рукопожатия на каждое из ~66 направлений (как в mpei_pool)."""
    session = getattr(_thread_local, "polytech_session", None)
    if session is None:
        session = requests.Session()
        _thread_local.polytech_session = session
    return session


def tracked_programs() -> list[ProgramConfig]:
    return [p for p in load_programs() if p.university == "Московский политех" and p.parser == "mospolytech"]


def _tracked_by_spec() -> dict[str, ProgramConfig]:
    mapping: dict[str, ProgramConfig] = {}
    for program in tracked_programs():
        spec = program.parser_meta.get("specCode")
        if not spec:
            continue
        existing = mapping.get(spec)
        if existing is not None:
            # Не гипотетическое: до этой ветки id26 и id76 сидели в одном
            # названии направления. Если сайт снова сольёт два профиля в один
            # specCode, одна из программ молча выпадет из tracked — приоритеты
            # Димы пропадут из симуляции без единого сообщения. Полную
            # группировку (как stankin_pool._tracked_direction_groups) здесь
            # не делаем — этого предупреждения достаточно, чтобы заметить.
            print(
                f"ВНИМАНИЕ: specCode={spec!r} у Политеха отслеживают сразу id={existing.id} "
                f"и id={program.id} — второй перезапишет первого в tracked, id={existing.id} "
                "тихо выпадет из симуляции.",
                file=sys.stderr,
            )
        mapping[spec] = program
    return mapping


ORDERS_PAGE_URL = "https://mospolytech.ru/postupayushchim/priem-v-universitet/prikazi-o-zachislenii/"
# Подпись ссылки на приказ по общему конкурсу. Рядом лежит второй файл — по
# целевой/особой/отдельной квотам и БВИ; он про другие места и в модель не идёт.
_ORDER_LINK_MARK = "зачисленных на бюджетную основу"
_ORDER_FORM = "Очная"


def _norm_profile(value: str) -> str:
    return re.sub(r"[^а-яёa-z0-9]", "", (value or "").lower())


def _enrollment_pdf_url() -> str:
    """Ссылка на приказ о зачислении на бюджет — со страницы, а не захардкоженная:
    в пути файла лежит хеш, который меняется при каждой перепубликации (тот же
    приём, что у приказа о КЦП в fa_pool)."""
    html = _request("GET", ORDERS_PAGE_URL).text
    soup = BeautifulSoup(html, "lxml")
    for link in soup.find_all("a", href=True):
        text = " ".join(link.get_text(" ", strip=True).split()).lower()
        if _ORDER_LINK_MARK in text and link["href"].lower().endswith(".pdf"):
            return urljoin(ORDERS_PAGE_URL, link["href"])
    raise ValueError(f"На {ORDERS_PAGE_URL} не найдена ссылка на приказ о зачислении на бюджет")


def parse_enrollment_pdf(data: bytes) -> dict[str, tuple[str, str]]:
    """Приказ → {уникальный код: (направление, профили)} по очной форме.

    Колонка «Профиль» содержит СПИСОК профилей через «;»: Политех разыгрывает
    укрупнённую конкурсную группу сразу на несколько профилей. Длинные списки
    вуз обрезает многоточием, поэтому сопоставлять надо по частям и по префиксу,
    а не по точному равенству строки.
    """
    import pdfplumber

    enrolled: dict[str, tuple[str, str]] = {}
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables() or []:
                for cells in table:
                    if not cells or len(cells) < 10:
                        continue
                    code = (cells[0] or "").strip()
                    if not code.isdigit():
                        continue
                    if (cells[6] or "").strip() != _ORDER_FORM:
                        continue
                    direction = " ".join((cells[8] or "").split())
                    profiles = " ".join((cells[9] or "").split())
                    enrolled[code] = (direction, profiles)
    if not enrolled:
        raise ValueError("В приказе о зачислении Политеха не найдено ни одной строки очной формы")
    return enrolled


def fetch_enrolled() -> dict[str, tuple[str, str]]:
    data = _request("GET", _enrollment_pdf_url(), timeout=300).content
    return parse_enrollment_pdf(data)


def _enrolled_program_key(
    entry: tuple[str, str], programs_by_key: dict[str, RobotProgram]
) -> str | None:
    """Направление, на которое человек зачислён, по строке приказа.

    Сначала пробуем профиль (точнее), затем — название направления: у части
    строк приказа профиль пуст. Ничего не подошло → None, и это честнее догадки:
    приписать человеку чужое направление хуже, чем не назвать его.
    """
    direction, profiles = entry
    parts = [part.strip().rstrip(".") for part in profiles.split(";") if part.strip()]
    for part in parts:
        needle = _norm_profile(part)
        if len(needle) < 8:
            continue
        for key, program in programs_by_key.items():
            inner = re.search(r"\((.+)\)\s*$", program.title)
            haystack = _norm_profile(inner.group(1) if inner else program.title)
            if needle in haystack or haystack.startswith(needle[:24]):
                return key
    needle = _norm_profile(direction)
    if len(needle) >= 8:
        for key, program in programs_by_key.items():
            head = re.sub(r"\s*\(.*$", "", program.title)
            if _norm_profile(head) == needle:
                return key
    return None


class PolytechFullPool:
    def build(self, *, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
        if use_cache:
            cached = self._load_cache()
            if cached is not None:
                return cached
        try:
            catalog = fetch_catalog()
            people, programs = self._fetch_all(catalog)
            fetched_at = datetime.now(timezone.utc).isoformat()
            self._save_cache(people, programs, fetched_at)
            return people, programs, fetched_at, False
        except Exception:
            stale = self._load_cache(ignore_ttl=True)
            if stale is not None:
                return stale
            raise

    @staticmethod
    def _fetch_one(spec: str) -> tuple[str, str, int, list[dict]]:
        """(код ОКСО по шапке, название по шапке, места общего конкурса, строки абитуриентов)."""
        page_html = fetch_direction_html(spec, session=_session())
        rows = _rows_from_html(page_html)
        try:
            code, title, places = _parse_header(rows)
        except EmptyDirection:
            # Второй, независимый признак того же явления: таблица абитуриентов
            # тоже пуста. Если он не подтвердился — перед нами не пустое
            # направление, а что-то незнакомое, и глотать его нельзя: пусть
            # уйдёт в failed как настоящий ValueError.
            people = _parse_people(rows)
            if people:
                raise ValueError(
                    f"шапка направления {spec!r} пуста, но таблица абитуриентов "
                    f"не пуста ({len(people)} строк) — это не пустое направление, "
                    "похоже на сбой разбора"
                ) from None
            raise
        return code, title, places, _parse_people(rows)

    def _fetch_all(self, catalog: list[str]) -> tuple[list[RobotPerson], list[RobotProgram]]:
        fetched: dict[str, tuple[str, str, int, list[dict]]] = {}
        failed: list[str] = []
        empty: list[str] = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(self._fetch_one, spec): spec for spec in catalog}
            for future in as_completed(futures):
                spec = futures[future]
                try:
                    fetched[spec] = future.result()
                except EmptyDirection:
                    # Не сбой: направление честно пустое. В `failed` не попадает,
                    # чтобы не искажать MAX_FAILED_FRACTION и не пугать ложной
                    # тревогой — итог для данных тот же (seat_source=None), как
                    # у обычного failed-направления ниже по коду.
                    empty.append(spec)
                except Exception as exc:
                    failed.append(spec)
                    print(
                        f"ВНИМАНИЕ: не удалось загрузить/разобрать направление Политеха "
                        f"{spec!r} после исчерпания ретраев ({exc}) — направление попадёт "
                        "в пул без мест и без абитуриентов (seat_source=None), конкурс по "
                        "нему не будет учтён в этом цикле сборки.",
                        file=sys.stderr,
                    )

        if empty:
            print(
                f"{len(empty)} направлений Политеха без участников конкурса (сайт "
                "честно отдаёт «УЧАСТВУЮЩИЕ В КОНКУРСЕ: 0») — не сбой, в пуле у них "
                "seat_source=None: " + ", ".join(sorted(empty)),
                file=sys.stderr,
            )

        if catalog and len(failed) / len(catalog) > MAX_FAILED_FRACTION:
            raise RuntimeError(
                f"Массовый сбой загрузки направлений Политеха: не удалось обработать "
                f"{len(failed)} из {len(catalog)} ({len(failed) / len(catalog):.0%}) — "
                f"превышен порог {MAX_FAILED_FRACTION:.0%}, похоже на системный сбой "
                "(троттлинг, авария сайта или смена вёрстки), а не единичный сбой одного "
                "направления. Сборка пула прервана, чтобы не перезаписать кэш почти "
                "пустым датасетом."
            )

        tracked = _tracked_by_spec()
        catalog_set = set(catalog)
        for spec, program in tracked.items():
            if spec not in catalog_set:
                print(
                    f"ВНИМАНИЕ: отслеживаемое направление Политеха id={program.id} "
                    f"(specCode={spec!r}) не найдено среди реальных направлений каталога "
                    "сайта — оно не попадёт в пул, и приоритеты Димы на него тихо выпадут "
                    "из симуляции (возможно, сайт переименовал или убрал эту строку).",
                    file=sys.stderr,
                )

        programs: list[RobotProgram] = []
        people: dict[str, RobotPerson] = {}
        for spec in catalog:
            program = tracked.get(spec)
            key = str(program.id) if program else spec
            item = fetched.get(spec)
            if item is None:
                programs.append(
                    RobotProgram(
                        key=key,
                        title=spec,
                        budget_places=None,
                        tracked_id=program.id if program else None,
                    )
                )
                continue

            code, title, places, rows = item
            programs.append(
                RobotProgram(
                    key=key,
                    title=title,
                    budget_places=places,
                    tracked_id=program.id if program else None,
                    okso_code=code,
                    seat_source="live",
                    passing_cutoff=_passing_cutoff(rows),
                    site_passing_count=_passing_count(rows),
                )
            )
            for row in rows:
                choice = ProgramChoice(program_key=key, priority=row["priority"])
                person = people.get(row["code"])
                if person is None:
                    people[row["code"]] = RobotPerson(
                        code=row["code"], score=row["score"], consent=row["consent"], choices=[choice]
                    )
                    continue
                person.score = max(person.score, row["score"])  # noqa: PLW2901
                person.consent = person.consent or row["consent"]
                person.choices.append(choice)

        self._mark_enrolled(list(people.values()), programs)
        return list(people.values()), programs

    @staticmethod
    def _mark_enrolled(people: list[RobotPerson], programs: list[RobotProgram]) -> None:
        """Проставить `ProgramChoice.enrolled` по приказу о зачислении.

        Политех разыграл общий конкурс (приказ 4161-УД от 07.08.2026: 1988
        зачисленных на очную бюджетную — ровно столько же, сколько мест общего
        конкурса в шапках конкурсных списков). Колонка «Высший проходной
        приоритет» при этом пустая, поэтому без приказа сверка слепа: робот
        объявлял человека зачисленным туда, откуда приказ его не называет вовсе.

        Сбой загрузки приказа НЕ роняет сборку: пул останется без отметок, и
        сверка честно скажет «оракул недоступен» — как было до этой правки.
        """
        try:
            enrolled = fetch_enrolled()
        except Exception as exc:  # noqa: BLE001
            print(
                f"ВНИМАНИЕ: не удалось получить приказ о зачислении Политеха ({exc}) — "
                "пул останется без отметок о зачислении, сверка прогноза будет "
                "недоступна (места и конкурс не затронуты).",
                file=sys.stderr,
            )
            return

        by_key = {program.key: program for program in programs}
        resolved: dict[tuple[str, str], str | None] = {}
        marked = unresolved = 0
        for person in people:
            entry = enrolled.get(person.code)
            if entry is None:
                continue
            if entry not in resolved:
                resolved[entry] = _enrolled_program_key(entry, by_key)
            key = resolved[entry]
            # Пустая строка вместо None: «зачислен, но куда — не определили».
            # Это принципиально разные ответы, и путать их нельзя.
            person.enrolled_key = key if key is not None else ""
            if key is None:
                unresolved += 1
            else:
                marked += 1
            choice = next((item for item in person.choices if item.program_key == key), None)
            if choice is not None:
                choice.enrolled = True
        if unresolved:
            print(
                f"ВНИМАНИЕ: у {unresolved} зачисленных Политеха строка приказа не "
                "сопоставилась ни с одним направлением каталога — они помечены как "
                f"зачисленные без указания направления. Сопоставлено: {marked}.",
                file=sys.stderr,
            )

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
                    enrolled_key=item.get("enrolled_key"),
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
                    "enrolled_key": p.enrolled_key,
                    "choices": [asdict(c) for c in p.choices],
                }
                for p in people
            ],
            "programs": [asdict(p) for p in programs],
        }
        CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch_polytech_full_pool(*, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
    return PolytechFullPool().build(use_cache=use_cache)


def read_polytech_cached_pool() -> tuple[list[RobotPerson], list[RobotProgram], str, bool] | None:
    """Отдаёт кэш Политеха ЛЮБОГО возраста и никогда не ходит в сеть. См. read_fa_cached_pool."""
    return PolytechFullPool()._load_cache(ignore_ttl=True)
