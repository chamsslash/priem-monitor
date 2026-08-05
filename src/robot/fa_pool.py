from __future__ import annotations

import io
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..config_loader import load_programs
from ..models import ProgramConfig
from ..parsers.utils import normalize_yes, to_float, to_int
from .direction_keys import fa_list_title
from .models import ProgramChoice, RobotPerson, RobotProgram

LIST_URL = "https://www.fa.ru/spiski/listabit.php"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
# Ровно те параметры, с которыми listabit.php встроен на официальную страницу
# конкурсных списков бакалавриата (/for-applicants/bachelor/Rateabit/applications.php).
# Параметр уровня называется type_list: раньше здесь стояло itype_list, и сервер
# его молча игнорировал — каталог приезжал по ВСЕМ уровням (144 значения вместо
# 82), а до бакалавриата его доводил только фильтр по названию
# (_is_bachelor_day_title). Итог совпадал, но держался на подстроке «, Бакалавр,»
# в названии программы. Фильтр оставлен как вторая линия: он отсекает ДОТ,
# очно-заочные и заочные, которых в выдаче хватает и с правильным уровнем.
BASE_PARAMS = {
    "type_list": "бкл",
    "type_conkurs": "Общий конкурс",
    "form_pay": "Бюджет",
}
POOL_SCOPE = "full"
PAGE_SIZE = 100
MIN_CATALOG_PROGRAMS = 30
CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "cache" / "fa_robot_pool.json"
CACHE_TTL_SEC = 7200
MAX_WORKERS = 6
FETCH_RETRIES = 3
RETRYABLE_STATUS = {500, 502, 503, 504}
UNTRACKED_FALLBACK_PLACES = 30  # аппроксимация мест ТОЛЬКО для непрофильных untracked-программ каталога ФА (вне охвата гарантии реальных мест)

KCP_PAGE_URL = "https://www.fa.ru/for-applicants/bachelor/control/"
# Секция приказа, которая нас касается. Всё остальное — заочные, очно-заочные,
# ДОТ и филиалы; в бюджетный очный конкурс они не входят, а программы там
# называются так же, и без фильтра по секции затирали бы московские числа.
KCP_SECTION = "Бакалавриат (очная форма обучения, г. Москва)"
_KCP_SECTION_PREFIXES = ("Бакалавриат (", "Специалитет (", "Магистратура (")
_KCP_SECTION_MARKER = "филиал Финуниверситета"


def _kcp_norm(value: str | None) -> str:
    return " ".join(str(value or "").replace("\n", " ").split())


def _kcp_match_key(direction: str, program: str) -> str:
    """Ключ сопоставления строки приказа со строкой каталога списков.

    Сравниваем без пробелов и регистра: в PDF слова рвутся переносами
    («информационно- аналитических»), а в каталоге пишутся слитно.
    """
    direction_name = re.sub(r"^\d{2}\.\d{2}\.\d{2}\s*", "", _kcp_norm(direction))
    return re.sub(r"\s+", "", f"{direction_name}|{_kcp_norm(program)}").lower()


def _kcp_pdf_url() -> str:
    """Ссылка на действующий приказ о КЦП — со страницы, а не захардкоженная:
    в пути файла лежит хеш, который меняется при каждой перепубликации."""
    html = _session().get(KCP_PAGE_URL, timeout=60).text
    soup = BeautifulSoup(html, "lxml")
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if href.lower().endswith(".pdf") and "ktsp" in href.lower():
            return href if href.startswith("http") else f"https://www.fa.ru{href}"
    raise ValueError(f"На {KCP_PAGE_URL} не найдена ссылка на PDF с КЦП")


_KCP_OKSO_RE = re.compile(r"^\d{2}\.\d{2}\.\d{2}\s")
# Второй проход разбора таблицы: границы строк берутся по текстовым строкам, а
# не по нарисованным рамкам. Нужен для строк, разорванных концом страницы: у
# них нет нижней линии, ячейки первых колонок не замыкаются, и основной проход
# отдаёт строку с пустыми «направление/программа/места». Основным его сделать
# нельзя — он рвёт многострочные названия программ на отдельные строки.
_KCP_RECOVERY_SETTINGS = {"vertical_strategy": "lines", "horizontal_strategy": "text"}


def _kcp_compact(value: str) -> str:
    return re.sub(r"\s+", "", value).lower()


def _kcp_table_rows(page, table) -> list[list[str]]:
    """Строки таблицы, с восстановленными первыми колонками разорванных строк.

    Строку, которую разрезал конец страницы, основной проход отдаёт пустой в
    колонках «направление/программа/места» — рамка ячейки не замкнута. Пустой
    она хуже, чем просто потерянной: код продолжений принимает её за «ещё одну
    целевую организацию» и приписывает целевую квоту ПРЕДЫДУЩЕЙ программе.
    Так приказ терял «Корпоративные финансы» (56 мест) и «Юриспруденцию» (154),
    а их целевые квоты завышали соседей.

    Чиним геометрией, а не догадками: строка-коробка на месте, второй проход
    даёт её текст, и мы вписываем его в ту коробку, которая этот текст
    физически содержит. Целевые колонки берутся из основного прохода — там они
    разобраны правильно.

    Фрагменты многострочных названий («углубленным изучением экономики» от
    «Международная экономика и торговля (с углубленным изучением…)») второй
    проход тоже отдаёт отдельными строками. Их отсекаем по вхождению: если
    текст — часть уже разобранного названия, это не потерянная строка.
    """
    rows = [[_kcp_norm(cell) for cell in cells] + [""] * 6 for cells in table.extract()]
    boxes = [row.bbox for row in table.rows]
    known = {_kcp_compact(row[1]) for row in rows if row[1]}

    for recovered in page.find_tables(table_settings=_KCP_RECOVERY_SETTINGS):
        for row, cells in zip(recovered.rows, recovered.extract()):
            cells = [_kcp_norm(cell) for cell in cells] + [""] * 6
            direction, program, total = cells[0], cells[1], cells[2]
            if not (_KCP_OKSO_RE.match(direction) and program and total.isdigit()):
                continue
            compact = _kcp_compact(program)
            if any(compact in item for item in known):
                continue
            top = row.bbox[1]
            index = next((i for i, box in enumerate(boxes) if box[1] <= top <= box[3]), None)
            if index is None:
                continue
            rows[index][:5] = cells[:5]
            known.add(compact)
    return rows


def parse_kcp_pdf(data: bytes) -> dict[str, int]:
    """Приказ о КЦП → {ключ сопоставления: места общего конкурса}.

    Места общего конкурса = всего мест − отдельная − особая − целевая квота.

    Три тонкости разбора:

    * **Секции идут подряд, заголовок может стоять НИЖЕ таблицы** на той же
      странице (так «очно-заочная ДОТ» начинается на странице очной Москвы).
      Поэтому идём по странице по порядку сверху вниз, переключая секцию на
      каждом заголовке, а не решаем по странице целиком — иначе строки чужих
      секций затирают наши программы с такими же названиями.
    * **Целевая квота размазана по строкам**: у программы с несколькими
      целевыми организациями каждая занимает свою строку, где заполнена только
      колонка целевой квоты. Такие строки суммируются к текущей программе,
      иначе целевая квота занижается, а общий конкурс завышается.
    * **Строки рвутся концом страницы** — см. `_kcp_table_rows`. Продолжения
      такой строки уезжают на следующую страницу, поэтому `current_key` через
      границу страницы НЕ сбрасывается.
    """
    import pdfplumber

    quotas: dict[str, list[int]] = {}  # ключ -> [всего, отдельная, особая, целевая]
    in_section = False
    current_key: str | None = None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            events: list[tuple[float, str, object]] = []
            for line in page.extract_text_lines() or []:
                text = _kcp_norm(line["text"])
                if text.startswith(_KCP_SECTION_PREFIXES) or _KCP_SECTION_MARKER in text:
                    events.append((line["top"], "section", text))
            for table in page.find_tables():
                events.append((table.bbox[1], "table", table))

            for _, kind, payload in sorted(events, key=lambda item: item[0]):
                if kind == "section":
                    in_section = payload == KCP_SECTION
                    current_key = None
                    continue
                if not in_section:
                    continue
                for cells in _kcp_table_rows(page, payload):
                    direction, program, total, separate, special, target = cells[:6]
                    if direction == "Всего":
                        current_key = None
                        continue
                    if direction and program and total.isdigit():
                        current_key = _kcp_match_key(direction, program)
                        # Одна и та же программа не встречается в секции дважды;
                        # повтор означал бы, что разорванную строку разобрали и
                        # на второй странице тоже — перезапись обнулила бы уже
                        # накопленную целевую квоту.
                        if current_key not in quotas:
                            quotas[current_key] = [
                                int(total),
                                _kcp_int(separate),
                                _kcp_int(special),
                                _kcp_int(target),
                            ]
                    elif current_key and not direction and not program and target.isdigit():
                        # Продолжение: ещё одна целевая организация той же программы.
                        quotas[current_key][3] += int(target)
    if not quotas:
        raise ValueError(f"В приказе о КЦП не найдена секция «{KCP_SECTION}»")
    return {key: max(row[0] - row[1] - row[2] - row[3], 0) for key, row in quotas.items()}


def _kcp_int(value: str) -> int:
    return int(value) if value.isdigit() else 0


def _catalog_match_key(title: str) -> str | None:
    """«Прикладная информатика, Бакалавр, Прикладные ИС…, Очная» → ключ приказа."""
    match = re.match(r"^(.*?),\s*Бакалавр,\s*(.*?),\s*Очная\s*$", title, flags=re.IGNORECASE)
    if match is None:
        return None
    return _kcp_match_key(match.group(1), match.group(2))


def fetch_kcp_places() -> dict[str, int]:
    """Места общего конкурса по программам очного московского бакалавриата из
    действующего приказа о КЦП. Ключ — название программы как в каталоге списков."""
    data = _session().get(_kcp_pdf_url(), timeout=120).content
    return parse_kcp_pdf(data)


# Места для программ вне config/programs.json (с сайта fa.ru, раздел программ).
FA_PLACES_OVERRIDES: dict[str, int] = {
    "Прикладная информатика, Бакалавр, Прикладные информационные системы в экономике и финансах, Очная": 25,
    "Бизнес-информатика, Бакалавр, Цифровая трансформация управления бизнесом, Очная": 20,
    "Инноватика, Бакалавр, Управление цифровыми инновациями, Очная": 25,
}


_thread_local = threading.local()


def _session() -> requests.Session:
    """Thread-local Session: каждый воркер-поток переиспользует своё соединение
    (без нового TCP+TLS на каждую страницу). Своя сессия на поток → потокобезопасно."""
    session = getattr(_thread_local, "fa_session", None)
    if session is None:
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT})
        _thread_local.fa_session = session
    return session


def _is_bachelor_day_title(title: str) -> bool:
    lowered = title.lower()
    if ", бакалавр," not in lowered:
        return False
    if ", очная" not in lowered:
        return False
    if any(marker in lowered for marker in ("дот", "заоч", "очно-заоч")):
        return False
    return True


class FaFullPool:
    FIELD_LABELS = {
        "code": "Уникальный код поступающего",
        "priority": "Приоритет зачисления, указанный поступающим по данной конкурсной группе",
        "is_bvi": "Без ВИ",
        "score": "Сумма конкурсных баллов",
        "consent": "Наличие согласия на зачисление",
    }
    # Вердикт самого вуза: «Да» у тех, кто РЕАЛЬНО проходит сюда по своему высшему
    # проходящему приоритету с учётом согласия — проверено живьём на «Прикладном
    # машинном обучении»: все 78 отмеченных подали согласие. Семантически это то
    # же, что «Высший проходной приоритет» у СТАНКИНа, и берём именно её, а не
    # «Основной высший приоритет» (та консент-независимая, мерила бы другой
    # вопрос). Каскад её НЕ читает — она уходит в passing_cutoff как независимый
    # оракул для сверки прогноза. Колонки может не быть (другая стадия приёма) —
    # тогда сверять не с чем, и это не повод ронять сборку.
    TOP_PASSING_LABEL = "Высший проходной приоритет"
    # Колонка кампуса. В одном конкурсном списке ФА лежат абитуриенты головного
    # вуза И филиалов: у московских здесь факультет («Факультет информационных
    # технологий и анализа больших данных»), у филиальских — «<Город> филиал
    # ФГОБУ ВО …». Места мы берём московские (приказ, секция «Бакалавриат
    # (очная форма обучения, г. Москва)»), поэтому и конкурентов надо брать
    # московских — иначе филиальские абитуриенты воюют за московские места.
    # Замер на «Прикладных ИС в экономике и финансах»: 3239 строк всего, из них
    # 119 алтайских; вместе сайт отмечает проходящими 78 человек с порогом 205,
    # а по одной Москве — 59 с порогом 267 (мест 62). Без фильтра число
    # проходящих выглядит больше числа мест, и это ложная тревога.
    BRANCH_LABEL = "Факультет/филиал"

    def build(self, *, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
        if use_cache:
            cached = self._load_cache()
            if cached is not None:
                return cached

        try:
            catalog = self._catalog_from_api()
            rows = self._fetch_all_rows(catalog)
            people = self._merge_people(rows)
            programs = self._build_programs(catalog, self._site_verdict(rows), self._safe_kcp())
            fetched_at = datetime.now(timezone.utc).isoformat()
            self._save_cache(people, programs, fetched_at)
            return people, programs, fetched_at, False
        except Exception:
            stale = self._load_cache(ignore_ttl=True)
            if stale is not None:
                return stale
            raise

    def _load_cache(
        self,
        *,
        ignore_ttl: bool = False,
    ) -> tuple[list[RobotPerson], list[RobotProgram], str, bool] | None:
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
                    "code": person.code,
                    "score": person.score,
                    "consent": person.consent,
                    "is_bvi": person.is_bvi,
                    "choices": [asdict(choice) for choice in person.choices],
                }
                for person in people
            ],
            "programs": [asdict(program) for program in programs],
        }
        CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def _get(cls, params: dict[str, str | int]) -> str:
        last_error: Exception | None = None
        for attempt in range(FETCH_RETRIES):
            try:
                response = _session().get(LIST_URL, params=params, timeout=120)
                response.raise_for_status()
                return response.text
            except requests.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else None
                if status in RETRYABLE_STATUS and attempt < FETCH_RETRIES - 1:
                    time.sleep(2**attempt)
                    continue
                break
            except requests.RequestException as exc:
                last_error = exc
                if attempt < FETCH_RETRIES - 1:
                    time.sleep(2**attempt)
                    continue
                break
        raise last_error or RuntimeError("Не удалось загрузить список Финуниверситета")

    @classmethod
    def _catalog_from_api(cls) -> list[str]:
        html = cls._get({**BASE_PARAMS, "action": "get_dict", "field": "facultet", "page": 1})
        payload = json.loads(html)
        titles = [str(item).strip() for item in payload.get("values", []) if str(item).strip()]
        catalog = [title for title in titles if _is_bachelor_day_title(title)]
        if not catalog:
            raise ValueError("Каталог программ Финуниверситета пуст")
        return sorted(catalog)

    def _fetch_all_rows(self, catalog: list[str]) -> list[dict]:
        all_rows: list[dict] = []
        totals: dict[str, int] = {}

        # Фаза 1: первая страница каждой программы — строки + total (число абитуриентов).
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(self._fetch_page, title, 1): title for title in catalog}
            for future in as_completed(futures):
                title = futures[future]
                rows, total = future.result()
                all_rows.extend(rows)
                totals[title] = total

        # Фаза 2: все ОСТАЛЬНЫЕ страницы всех программ через один пул. Так огромная
        # программа (напр. 6368 чел = 64 стр.) не висит последовательным «хвостом» —
        # её страницы размазываются по воркерам вместе со всеми. page_size=100 не
        # трогаем: fa.ru не уважает большие значения (на 500 отдаёт 20 → потеря данных).
        tasks = [
            (title, page)
            for title, total in totals.items()
            for page in range(2, (total + PAGE_SIZE - 1) // PAGE_SIZE + 1)
        ]
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(self._fetch_page, title, page) for title, page in tasks]
            for future in as_completed(futures):
                rows, _ = future.result()
                all_rows.extend(rows)

        if not all_rows:
            raise ValueError("Список абитуриентов Финуниверситета пуст")
        return all_rows

    def _fetch_page(self, title: str, page: int) -> tuple[list[dict], int]:
        """Одна страница программы: (строки, total). total парсится из ответа —
        по нему считаем, сколько всего страниц у программы."""
        html = self._get({**BASE_PARAMS, "facultet": title, "page": page, "page_size": PAGE_SIZE})
        match = re.search(r"total:\s*(\d+)", html)
        total = int(match.group(1)) if match else 0
        return self._parse_rows(html, title), total

    def _parse_rows(self, html: str, title: str) -> list[dict]:
        soup = BeautifulSoup(html, "lxml")
        rows: list[dict] = []
        for row in soup.select("tbody tr"):
            cells = row.find_all("td")
            if not cells or not cells[0].get_text(strip=True).isdigit():
                continue
            fields = {(cell.get("data-label") or "").strip(): cell.get_text(" ", strip=True) for cell in cells}
            for label in self.FIELD_LABELS.values():
                if label not in fields:
                    raise ValueError(f"На сайте ФА не найдена колонка {label!r} — вёрстка снова изменилась")
            score = int(to_float(fields[self.FIELD_LABELS["score"]]) or 0)
            if score <= 0:
                continue
            code = fields[self.FIELD_LABELS["code"]].strip()
            if not code:
                continue
            if "филиал" in (fields.get(self.BRANCH_LABEL) or "").lower():
                continue
            top_passing = fields.get(self.TOP_PASSING_LABEL)
            rows.append(
                {
                    "code": code,
                    "program_key": title,
                    "score": score,
                    "consent": normalize_yes(fields[self.FIELD_LABELS["consent"]]),
                    "is_bvi": normalize_yes(fields[self.FIELD_LABELS["is_bvi"]]),
                    "priority": to_int(fields[self.FIELD_LABELS["priority"]]) or 99,
                    "top_passing": normalize_yes(top_passing) if top_passing is not None else None,
                }
            )
        return rows

    @staticmethod
    def _site_verdict(rows: list[dict]) -> dict[str, tuple[int, int]]:
        """Программа → (проходной балл сайта, сколько человек сайт туда зачисляет).

        Балл — минимальный среди отмеченных «Высший проходной приоритет»; 0 —
        колонка есть, но не проходит никто (места открыты). Счётчик нужен как
        нижняя граница числа мест: сайт не зачислит больше, чем есть мест, и если
        он зачисляет больше, чем стоит у нас в config, — значит наше число врёт.
        Программы без колонки в словарь не попадают вовсе."""
        scores: dict[str, list[int]] = {}
        for row in rows:
            if row.get("top_passing") is None:
                continue
            bucket = scores.setdefault(row["program_key"], [])
            if row["top_passing"]:
                bucket.append(row["score"])
        return {
            title: ((min(values) if values else 0), len(values)) for title, values in scores.items()
        }

    @staticmethod
    def _merge_people(rows: list[dict]) -> list[RobotPerson]:
        people: dict[str, RobotPerson] = {}
        for row in rows:
            choice = ProgramChoice(
                program_key=row["program_key"],
                priority=row["priority"],
                is_bvi=row["is_bvi"],
            )
            person = people.get(row["code"])
            if person is None:
                people[row["code"]] = RobotPerson(
                    code=row["code"],
                    score=row["score"],
                    consent=row["consent"],
                    is_bvi=row["is_bvi"],
                    choices=[choice],
                )
                continue
            person.score = max(person.score, row["score"])
            person.consent = person.consent or row["consent"]
            person.choices.append(choice)
            person.is_bvi = person.has_bvi_choice()
        return list(people.values())

    @staticmethod
    def _safe_kcp() -> dict[str, int]:
        """Приказ о КЦП, но его недоступность не должна ронять сборку пула:
        без него места откатятся на config, и сверка подсветит это как «не live»."""
        try:
            return fetch_kcp_places()
        except Exception as exc:  # noqa: BLE001
            print(
                f"ВНИМАНИЕ: не удалось получить приказ о КЦП ФА ({exc}) — места взяты "
                "из резерва config/заглушки.",
                file=sys.stderr,
            )
            return {}

    def _build_programs(
        self,
        catalog: list[str],
        verdict: dict[str, tuple[int, int]],
        kcp: dict[str, int],
    ) -> list[RobotProgram]:
        """Источники мест по убыванию доверия: приказ о КЦП → config → заглушка.

        Приказ (`fetch_kcp_places`) закрывает все очные московские программы
        разом, поэтому config и заглушка «30 мест» остаются только аварийным
        путём — на случай, если приказ не скачался или вуз переименовал
        программу так, что она не сопоставилась.
        """
        tracked = _tracked_title_map()
        places = _places_map()
        programs: list[RobotProgram] = []
        for title in catalog:
            official = kcp.get(_catalog_match_key(title) or "")
            known = places.get(title)
            if official is not None:
                seats, seat_source = official, "live"
            elif known is not None:
                seats, seat_source = known, "config"
            else:
                seats, seat_source = UNTRACKED_FALLBACK_PLACES, "approx"
            programs.append(
                RobotProgram(
                    key=title,
                    title=title,
                    budget_places=seats,
                    tracked_id=tracked.get(title),
                    seat_source=seat_source,
                    passing_cutoff=verdict[title][0] if title in verdict else None,
                    site_passing_count=verdict[title][1] if title in verdict else None,
                )
            )
        _warn_unmatched(programs)
        return programs


def _warn_unmatched(programs: list[RobotProgram]) -> None:
    """Программы, которым не нашлось места в приказе, — вслух, а не молча.

    Штатный случай — программы, которых в московской очной секции приказа нет
    вовсе: они существуют только в филиалах (Журналистика, Медиакоммуникации,
    Анализ данных и т.п.). После фильтра по кампусу у них не остаётся ни одного
    абитуриента, и заглушка ни на что не влияет.

    Отличать штатный случай от дефекта разбора нужно ПО ЧИСЛУ ЛЮДЕЙ: пусто —
    филиальская программа, есть люди — приказ разобран неправильно. Так и нашли
    предыдущий дефект (строки на разрыве страниц, см. `_kcp_table_rows`): у
    «Корпоративных финансов» с заглушкой в 30 мест стояло 4682 абитуриента.
    """
    unmatched = [program for program in programs if program.seat_source == "approx"]
    if not unmatched:
        return
    print(
        f"ВНИМАНИЕ: {len(unmatched)} программ ФА не сопоставились с приказом о КЦП и "
        f"получили заглушку {UNTRACKED_FALLBACK_PLACES} мест: "
        + ", ".join(program.title for program in unmatched),
        file=sys.stderr,
    )


def _tracked_title_map() -> dict[str, int]:
    mapping: dict[str, int] = {}
    for program in tracked_programs():
        mapping[fa_list_title(program)] = program.id
    return mapping


def _places_map() -> dict[str, int]:
    places = {fa_list_title(program): program.budget_places for program in tracked_programs()}
    for title, budget_places in FA_PLACES_OVERRIDES.items():
        places.setdefault(title, budget_places)
    return places


def tracked_programs() -> list[ProgramConfig]:
    return [program for program in load_programs() if program.university == "Финансовый университет" and program.parser == "fa"]


def fetch_fa_full_pool(*, use_cache: bool = True) -> tuple[list[RobotPerson], list[RobotProgram], str, bool]:
    return FaFullPool().build(use_cache=use_cache)


def read_fa_cached_pool() -> tuple[list[RobotPerson], list[RobotProgram], str, bool] | None:
    """Отдаёт кэш ФА ЛЮБОГО возраста и никогда не ходит в сеть.

    Нужна обработчикам команд: они обязаны отвечать мгновенно, поэтому берут
    что есть на диске, а протухание — повод отправить заявку refresh-воркеру,
    а не качать прямо здесь. None — кэша нет вовсе (или он несовместим).
    """
    return FaFullPool()._load_cache(ignore_ttl=True)
