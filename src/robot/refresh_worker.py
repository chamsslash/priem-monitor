from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

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

    force, подхвативший идущую сборку уже ПОСЛЕ того, как она решила «кэш
    свеж, качать не буду», не теряется: _run перечитывает флаг форса сразу
    после этого решения (self._force_requested) и, если он выставлен, всё
    равно уходит в сеть — иначе явное «/робот обновить» на границе TTL кэша
    молча превращалось бы в no-op с рапортом об успехе.

    Инвариант: под self._lock не зовём ничего из executor/Future, кроме
    самого executor.submit(). В частности, add_done_callback() всегда
    вызывается ПОСЛЕ выхода из `with self._lock` — Future умеет выполнить
    колбэк синхронно в вызывающем потоке, если задача уже завершена или
    отменена к моменту регистрации, а значит рискует дёрнуть self._lock
    реентрантно и подвесить поток изнутри же лока.
    """

    def __init__(self, max_workers: int = MAX_PARALLEL_REFRESH) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="refresh")
        self._lock = threading.Lock()
        self._in_flight: dict[str, list[DoneCallback]] = {}
        # force, прилетевший, пока идущая сборка ещё решает «кэш свеж или нет».
        self._force_requested: dict[str, bool] = {}
        self._closed = False

    def request(
        self,
        university: str,
        *,
        on_done: DoneCallback | None = None,
        force: bool = False,
        max_age: int | None = None,
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

        max_age — необязательный порог свежести в секундах, СВОЙ для этой
        заявки, вместо TTL пула. Нужен шедулеру прогрева: его период короче
        TTL, и без max_age он ничего не пересобирал бы вплоть до истечения
        полного TTL (см. is_pool_stale). Обработчики команд max_age не
        передают — им нужна свежесть ровно в смысле TTL пула.

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
        submitted: Future | None = None

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
                    if force:
                        # Идущая сборка могла уже прочитать кэш и решить, что
                        # он свеж — а значит, вот-вот завершится no-op'ом. Наш
                        # force обязан пробить этот no-op (см. _run).
                        self._force_requested[university] = True
                    logger.info("Пересборка %s уже идёт — заявка подхвачена", university)
                    return True

                self._in_flight[university] = [on_done] if on_done is not None else []
                try:
                    # Под self._lock не зовём ничего из executor, кроме
                    # submit(): add_done_callback переехал за пределы лока
                    # (см. ниже) — future умеет выполнить колбэк синхронно в
                    # ЭТОМ же потоке, если задача уже завершена/отменена к
                    # моменту регистрации, а значит рискует дёрнуть
                    # _drop_if_cancelled -> self._lock реентрантно.
                    submitted = self._executor.submit(self._run, university, parser_name, force, max_age)
                except RuntimeError as exc:
                    # На всякий случай: self._closed и submit() защищены одним
                    # и тем же self._lock, поэтому в штатной работе shutdown()
                    # не может «протиснуться» между проверкой self._closed и
                    # submit() — это подстраховка от гонки внутри самого
                    # executor, а не ожидаемый рабочий путь.
                    rejection_error = exc
                    rejected_waiters = self._in_flight.pop(university, [])
                    self._force_requested.pop(university, None)
                    submitted = None

        if submitted is not None:
            # Если shutdown(cancel_futures=True) отменит эту задачу до того,
            # как _run успеет стартовать, сам _run не выполнится и его finally
            # не снимет метку. add_done_callback — это единственная страховка
            # на такой случай (см. _drop_if_cancelled).
            submitted.add_done_callback(
                lambda f, u=university: self._drop_if_cancelled(u, f)
            )

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

    def _run(self, university: str, parser_name: str, force: bool, max_age: int | None = None) -> None:
        error: Exception | None = None
        # Если не None — метка in-flight уже снята внутри while (ветка «кэш
        # свеж»), и finally не должен снимать её повторно: между тем снятием
        # и этим finally есть окно, в которое request(force=True) успел бы
        # прицепиться к уже мёртвой сборке и получить «успех» без единого
        # сетевого запроса (см. P1 из ре-ревью).
        waiters: list[DoneCallback] | None = None
        try:
            while True:
                if not force:
                    cached = read_cached_pool(parser_name)
                    if cached is not None and not is_pool_stale(parser_name, cached[2], max_age=max_age):
                        logger.info("Кэш %s ещё свеж — пересборка не нужна", university)
                        with self._lock:
                            # force, прилетевший ПОКА мы читали кэш (заявка
                            # подхватила идущую сборку), обязан получить
                            # настоящую пересборку, а не наш no-op. Снимаем
                            # метку в ТОМ ЖЕ захвате лока, что и эту проверку —
                            # иначе после неё и до отдельного захвата в finally
                            # остаётся окно для гонки с force.
                            force = self._force_requested.pop(university, False)
                            if not force:
                                waiters = self._in_flight.pop(university, [])
                        if not force:
                            break
                        continue

                people, programs, _fetched_at, from_cache = fetch_university_pool(
                    parser_name, use_cache=False
                )
                if from_cache:
                    # use_cache=False обязан был сходить в сеть. from_cache=True
                    # значит пул сам откатился на протухший кэш после сетевой
                    # ошибки (см. build() в *_pool.py) — сборка провалилась, а
                    # не удалась, даже если исключение наружу не вылетело.
                    raise RuntimeError("сайт вуза недоступен, показываю прежние данные")
                logger.info(
                    "Пересобран %s: направлений %d, абитуриентов %d",
                    university,
                    len(programs),
                    len(people),
                )
                break
        except BaseException as exc:  # noqa: BLE001
            # BaseException, а не Exception: KeyboardInterrupt/SystemExit тоже
            # обязаны снять метку и дойти до колбэков в finally — иначе заявка
            # осталась бы висеть в _in_flight навсегда, а ожидающие не узнали
            # бы, что сборка не состоялась.
            error = exc if isinstance(exc, Exception) else RuntimeError(f"{type(exc).__name__}: {exc}")
            logger.warning("Пересборка %s не удалась: %r", university, exc)
        finally:
            # Снимаем метку и рассылаем колбэки ВСЕГДА, из одного места —
            # finally выполняется и при успехе, и при любом исключении. Если
            # её уже сняли выше (waiters не None) — не делаем этого повторно.
            if waiters is None:
                with self._lock:
                    waiters = self._in_flight.pop(university, [])
                    self._force_requested.pop(university, None)
            # Колбэки — вне лока: чужой код (отправка в Telegram) не должен
            # держать мьютекс и тормозить заявки по другим вузам.
            for callback in waiters:
                try:
                    callback(university, error)
                except Exception:  # noqa: BLE001
                    logger.exception("Колбэк пересборки %s упал", university)

    def _drop_if_cancelled(self, university: str, future: Future) -> None:
        """Страховка на случай, если задачу отменили до того, как она стартовала.

        shutdown(cancel_futures=True) отменяет ещё не начатые задачи — тогда
        _run для них вообще не запускается, и его finally (единственное
        место, которое снимает метку и рассылает колбэки) не выполняется.
        Без этой страховки заявка повисла бы в _in_flight навсегда, а
        ожидающие не получили бы ни успеха, ни ошибки. Вызывается из потока,
        который дёрнул shutdown() — это редкий путь остановки процесса, не
        основной цикл опроса.
        """
        if not future.cancelled():
            return  # обычный путь: _run сам всё сделал в своём finally
        with self._lock:
            waiters = self._in_flight.pop(university, [])
            self._force_requested.pop(university, None)
        error = RuntimeError("Воркер остановлен — пересборка отменена")
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

        cancel_futures=True отменяет только ещё НЕ начатые сборки (см.
        _drop_if_cancelled — их заявки всё равно получат колбэк с ошибкой, а
        не повиснут молча) — уже идущие задачи не прерываются, доработают до
        конца. Мгновенного выхода из процесса это не даёт: ThreadPoolExecutor
        регистрирует join своих активных потоков через
        threading._register_atexit независимо от wait=False, так что
        интерпретатор всё равно дождётся уже запущенных пересборок при
        выходе — это поведение стандартной библиотеки concurrent.futures, а
        не баг этого модуля.
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
    пулы до самого его завершения. Такое пересоздание залогировано: это
    неожиданный путь (после shutdown() обычно никто больше не должен звать
    воркер), и новый пул потоков, который никто явно не закрывал, стоит
    заметить в логах, а не проглатывать молча.
    """
    global _worker
    with _worker_lock:
        if _worker is not None and _worker.is_closed():
            logger.warning(
                "get_refresh_worker(): предыдущий воркер был остановлен (shutdown()) — создаю новый"
            )
            _worker = None
        if _worker is None:
            _worker = RefreshWorker()
        return _worker
