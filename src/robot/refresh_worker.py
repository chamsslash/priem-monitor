from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from .universities import (
    SUPPORTED_UNIVERSITIES,
    fetch_university_pool,
    is_pool_stale,
    read_cached_pool,
)

logger = logging.getLogger(__name__)

# (university, error). error=None — пересборка удалась.
DoneCallback = Callable[[str, Exception | None], None]

# Четыре вуза бьют по четырём разным сайтам, поэтому греются параллельно:
# полный прогрев занимает время самого медленного, а не сумму.
MAX_PARALLEL_REFRESH = 4


class RefreshWorker:
    """Единственное место в кодовой базе, которое пересобирает пулы из сети.

    Ни обработчики команд, ни шедулер прогрева не качают сами — они шлют сюда
    заявку и сразу возвращаются. Так цикл getUpdates никогда не блокируется.

    Single-flight: пока вуз собирается, повторная заявка НЕ запускает вторую
    сборку, а подвешивает свой колбэк к идущей. Без этого шедулер и команда
    пользователя дублировали бы работу и удваивали нагрузку на сайт вуза.
    """

    def __init__(self, max_workers: int = MAX_PARALLEL_REFRESH) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="refresh")
        self._lock = threading.Lock()
        self._in_flight: dict[str, list[DoneCallback]] = {}

    def request(
        self,
        university: str,
        *,
        on_done: DoneCallback | None = None,
        force: bool = False,
    ) -> bool:
        """Заказать пересборку вуза. Возвращается немедленно, не блокирует.

        force=False — если кэш ещё свеж, пересборка не нужна и не запускается
        (так шедулер не перекачивает недавно обновлённое). force=True — качаем
        всегда: это явное «/робот обновить», где пользователь просит свежее.

        True  — запустили новую сборку,
        False — либо кэш уже свеж, либо подхватили идущую сборку.
        """
        parser_name = SUPPORTED_UNIVERSITIES.get(university)
        if parser_name is None:
            raise ValueError(f"Робот не поддерживает вуз «{university}»")

        if not force:
            cached = read_cached_pool(parser_name)
            if cached is not None and not is_pool_stale(parser_name, cached[2]):
                logger.info("Кэш %s ещё свеж — пересборка не нужна", university)
                if on_done is not None:
                    on_done(university, None)
                return False

        with self._lock:
            waiters = self._in_flight.get(university)
            if waiters is not None:
                if on_done is not None:
                    waiters.append(on_done)
                logger.info("Пересборка %s уже идёт — заявка подхвачена", university)
                return False
            self._in_flight[university] = [on_done] if on_done is not None else []

        self._executor.submit(self._run, university, parser_name)
        return True

    def _run(self, university: str, parser_name: str) -> None:
        error: Exception | None = None
        try:
            people, programs, _fetched_at, _from_cache = fetch_university_pool(
                parser_name, use_cache=False
            )
            logger.info(
                "Пересобран %s: направлений %d, абитуриентов %d",
                university,
                len(programs),
                len(people),
            )
        except Exception as exc:  # noqa: BLE001
            error = exc
            logger.warning("Пересборка %s не удалась: %s", university, exc)
        finally:
            # Снимаем метку ВСЕГДА, иначе одна упавшая сборка навсегда
            # заблокировала бы повторные попытки для этого вуза.
            with self._lock:
                waiters = self._in_flight.pop(university, [])

        # Колбэки — вне лока: чужой код (отправка в Telegram) не должен
        # держать мьютекс и тормозить заявки по другим вузам.
        for callback in waiters:
            try:
                callback(university, error)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Колбэк пересборки %s упал: %s", university, exc)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False)


_worker: RefreshWorker | None = None
_worker_lock = threading.Lock()


def get_refresh_worker() -> RefreshWorker:
    """Общий воркер процесса. Именно общий: single-flight имеет смысл, только
    если шедулер прогрева и обработчики команд шлют заявки в ОДИН экземпляр."""
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = RefreshWorker()
        return _worker
