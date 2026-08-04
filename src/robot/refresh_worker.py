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
    заявку и сразу возвращаются. Так цикл getUpdates никогда не блокируется:
    ни сетевым запросом, ни разбором JSON кэша, ни синхронным колбэком.

    Single-flight: пока вуз собирается, повторная заявка НЕ запускает вторую
    сборку, а подвешивает свой колбэк к идущей. Без этого шедулер и команда
    пользователя дублировали бы работу и удваивали нагрузку на сайт вуза.
    Проверка свежести кэша тоже происходит под меткой in-flight (внутри
    _run), а не до неё — иначе между чтением кэша и постановкой метки успела
    бы прошмыгнуть гонка и превратить single-flight в double-flight.
    """

    def __init__(self, max_workers: int = MAX_PARALLEL_REFRESH) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="refresh")
        self._lock = threading.Lock()
        self._in_flight: dict[str, list[DoneCallback]] = {}
        self._closed = False

    def request(
        self,
        university: str,
        *,
        on_done: DoneCallback | None = None,
        force: bool = False,
    ) -> bool:
        """Заказать пересборку вуза. Возвращается немедленно, не блокирует вызывающего.

        Проверка свежести кэша ушла из этого метода в воркер-поток (_run), под
        уже захваченную метку in-flight. Раньше она читала кэш (десятки МБ
        JSON) и при свежем кэше синхронно звала on_done прямо в потоке
        вызывающего — то есть в потоке опроса Telegram, который request()
        обязан не блокировать. Заодно это закрывает гонку check-then-act:
        свежесть теперь проверяется ПОСЛЕ захвата метки, а не до — писать кэш
        в этот момент уже некому, дублирующая сборка физически невозможна.

        force=False — если кэш ещё свеж, пересборка не нужна (так шедулер не
        перекачивает недавно обновлённое). force=True — качаем всегда: это
        явное «/робот обновить», где пользователь просит свежее. Обе ветки
        теперь одинаково асинхронны — результат (успех / ошибка / «кэш и так
        был свеж») доезжает только через on_done, из воркер-потока.

        Возврат значит «принята ли заявка», а НЕ «запущена ли новая сборка»
        (это раньше требовало звать on_done синхронно, чтобы правдиво вернуть
        True/False из того же вызова):
        True  — заявка принята: либо запущена новая сборка, либо подхвачена
                та, что уже идёт — в обоих случаях on_done будет вызван из
                воркер-потока, а не из потока вызывающего;
        False — заявка отклонена: воркер уже остановлен (shutdown()); если
                on_done был передан, он вызывается с ошибкой синхронно здесь
                же (отказ — это не асинхронная операция, ждать нечего).
        """
        parser_name = SUPPORTED_UNIVERSITIES.get(university)
        if parser_name is None:
            raise ValueError(f"Робот не поддерживает вуз «{university}»")

        rejection_error: Exception | None = None
        rejected_waiters: list[DoneCallback] = []

        with self._lock:
            if self._closed:
                logger.info("Воркер остановлен — заявка на %s отклонена", university)
                rejection_error = RuntimeError("Воркер остановлен")
                if on_done is not None:
                    rejected_waiters = [on_done]
            else:
                waiters = self._in_flight.get(university)
                if waiters is not None:
                    if on_done is not None:
                        waiters.append(on_done)
                    logger.info("Пересборка %s уже идёт — заявка подхвачена", university)
                    return True

                self._in_flight[university] = [on_done] if on_done is not None else []
                try:
                    self._executor.submit(self._run, university, parser_name, force)
                except RuntimeError as exc:
                    # На всякий случай: self._closed и submit() защищены одним
                    # и тем же self._lock, поэтому в штатной работе shutdown()
                    # не может «протиснуться» между проверкой self._closed и
                    # submit() — это подстраховка от гонки внутри самого
                    # executor, а не ожидаемый рабочий путь.
                    rejection_error = exc
                    rejected_waiters = self._in_flight.pop(university, [])

        if rejection_error is not None:
            # Колбэки — вне лока по тому же принципу, что и в _run: чужой код
            # (Telegram) не должен держать мьютекс.
            for callback in rejected_waiters:
                try:
                    callback(university, rejection_error)
                except Exception:  # noqa: BLE001
                    logger.exception("Колбэк пересборки %s упал", university)
            return False

        return True

    def _run(self, university: str, parser_name: str, force: bool) -> None:
        error: Exception | None = None
        try:
            cached = None if force else read_cached_pool(parser_name)
            if cached is not None and not is_pool_stale(parser_name, cached[2]):
                logger.info("Кэш %s ещё свеж — пересборка не нужна", university)
            else:
                people, programs, _fetched_at, _from_cache = fetch_university_pool(
                    parser_name, use_cache=False
                )
                logger.info(
                    "Пересобран %s: направлений %d, абитуриентов %d",
                    university,
                    len(programs),
                    len(people),
                )
        except BaseException as exc:  # noqa: BLE001
            # BaseException, а не Exception: KeyboardInterrupt/SystemExit тоже
            # обязаны снять метку и дойти до колбэков в finally — иначе заявка
            # осталась бы висеть в _in_flight навсегда, а ожидающие не узнали
            # бы, что сборка не состоялась.
            error = exc if isinstance(exc, Exception) else RuntimeError(str(exc))
            logger.warning("Пересборка %s не удалась: %s", university, exc)
        finally:
            # Снимаем метку и рассылаем колбэки ВСЕГДА, из одного места —
            # finally выполняется и при успехе, и при любом исключении.
            with self._lock:
                waiters = self._in_flight.pop(university, [])
            # Колбэки — вне лока: чужой код (отправка в Telegram) не должен
            # держать мьютекс и тормозить заявки по другим вузам.
            for callback in waiters:
                try:
                    callback(university, error)
                except Exception:  # noqa: BLE001
                    logger.exception("Колбэк пересборки %s упал", university)

    def is_closed(self) -> bool:
        """Остановлен ли воркер (shutdown() уже вызывался)."""
        with self._lock:
            return self._closed

    def shutdown(self) -> None:
        """Останавливает приём новых заявок.

        cancel_futures=True отменяет только ещё НЕ начатые сборки — уже
        идущие задачи не прерываются, доработают до конца. Мгновенного выхода
        из процесса это не даёт: ThreadPoolExecutor регистрирует join своих
        активных потоков через threading._register_atexit независимо от
        wait=False, так что интерпретатор всё равно дождётся уже запущенных
        пересборок при выходе — это поведение стандартной библиотеки
        concurrent.futures, а не баг этого модуля.
        """
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)


_worker: RefreshWorker | None = None
_worker_lock = threading.Lock()


def get_refresh_worker() -> RefreshWorker:
    """Общий воркер процесса. Именно общий: single-flight имеет смысл, только
    если шедулер прогрева и обработчики команд шлют заявки в ОДИН экземпляр.

    Если существующий воркер уже остановлен (shutdown()), создаёт новый —
    иначе один shutdown() необратимо лишил бы процесс возможности собирать
    пулы до самого его завершения.
    """
    global _worker
    with _worker_lock:
        if _worker is None or _worker.is_closed():
            _worker = RefreshWorker()
        return _worker
