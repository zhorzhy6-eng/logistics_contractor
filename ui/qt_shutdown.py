#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ожидание рабочих потоков перед реальным закрытием окна.

Зачем это нужно. Каждое окно типа держит свой `QThreadPool` (распознавание
GigaChat) и вкладки — свои (поиск в DaData). Пул — Qt-ребёнок окна, поэтому
закрытие окна уничтожает и его. Если в этот момент в пуле ещё работает задача,
её Python-обёртка (`RecognitionTask`/`DadataLookupTask`, наследники QRunnable)
может быть уничтожена раньше, чем закончится поток, — и поток работает с уже
освобождённой памятью. Это и есть падение при выходе:
`0xC0000005` (access violation) в `sip.cp314-win_amd64.pyd`/`Qt5Core.dll`,
плавающее — зависит от того, успел ли пользователь запустить распознавание
перед выходом.

В тестах проекта это уже знали и обходили: перед закрытием окна там стоит
`win.thread_pool.waitForDone(5000)` с комментарием «процесс при разрушении
QThreadPool (access violation на выходе)». В самом приложении такого ожидания
не было — здесь оно и появилось.

Отдельно — рабочие ПОТОКИ (`QThread`). Кроме пулов, окно держит потоки,
которые живут всю программу: у Максимилиана это поток запроса к модели
(`ui/agent_widget.py::AgentWidget._ensure_worker`). Он не входит в пул, и
`waitForDone` его не касается. Тот же корень и та же беда: поток,
доживший до разрушения QT-родителя, роняет процесс (`0xC0000005`).
Поэтому здесь есть вторая дверь — `wait_for_threads`, устроенная так же,
как первая.

Решение осознанное:

  * ждём ПЕРЕД закрытием окна, а не после: после `close()`/`deleteLater()`
    окно и его пулы уже разрушаются, и ждать нечего;
  * таймаут ограничен (по умолчанию 5 с — как в тестах проекта): вечное
    ожидание на выходе хуже, чем предупреждение в журнале. Если время вышло,
    пишем WARNING с числом активных потоков — это след для разбора;
  * чужие пулы (глобальный QThreadPool.instance() и пулы других окон) не
    трогаем: ждём только своих детей;
  * потокам, которые умеют останавливаться сами (`AgentWidget.shutdown`
    зовёт `quit()` и `wait()`), здесь ничего объяснять не нужно: ожидание
    только подтверждает, что они уже встали.
"""

import logging

from PyQt5.QtCore import QThread, QThreadPool

logger = logging.getLogger("ui.qt_shutdown")

#: Сколько ждать рабочие потоки при закрытии окна (мс).
DEFAULT_TIMEOUT_MS = 5000


def thread_pools_of(root) -> list:
    """Все QThreadPool внутри root (рекурсивно по дереву объектов Qt)."""
    if root is None or not hasattr(root, "findChildren"):
        return []
    return list(root.findChildren(QThreadPool))


def wait_for_thread_pools(root, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> int:
    """
    Ждёт завершения задач всех QThreadPool внутри root.

    Возвращает число пулов, которых пришлось ждать (0 — ждать было нечего).
    Пулы без активных потоков пропускаются: `waitForDone` на них — лишний
    вызов. Превышение таймаута не исключение: пишем WARNING и идём дальше —
    выход из приложения не должен падать из-за диагностики.
    """
    waited = 0
    for pool in thread_pools_of(root):
        try:
            active = pool.activeThreadCount()
        except RuntimeError:
            # Пул уже разрушен (C++ объект удалён) — ждать нечего.
            continue
        if active <= 0:
            continue

        waited += 1
        logger.info(
            "Закрытие окна: ждём рабочие потоки (%s активно, до %s мс)",
            active, timeout_ms,
        )
        try:
            finished = pool.waitForDone(timeout_ms)
        except RuntimeError:
            continue
        if not finished:
            logger.warning(
                "Закрытие окна: рабочие потоки не завершились за %s мс "
                "(активно %s) — закрываем без ожидания",
                timeout_ms, pool.activeThreadCount(),
            )
    return waited


def threads_of(root) -> list:
    """Все QThread внутри root (рекурсивно по дереву объектов Qt)."""
    if root is None or not hasattr(root, "findChildren"):
        return []
    return list(root.findChildren(QThread))


def wait_for_threads(root, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> int:
    """
    Просит рабочие ПОТОКИ внутри root остановиться и ждёт их.

    Вторая дверь того же выхода, что и ``wait_for_thread_pools``: пул —
    не единственный способ увести работу в сторону. Поток запроса к модели
    (Максимилиан) живёт вне пулов, и `waitForDone` его не касается.

    Что делает по шагам: ``quit()`` (просьба выйти из цикла событий) →
    ``wait(timeout_ms)``. Порядок именно такой: без просьбы поток с циклом
    событий не остановится никогда, и ожидание всегда упиралось бы в
    таймаут.

    Возвращает число потоков, которых пришлось ждать (0 — ждать было
    нечего). Уже остановленные потоки пропускаются. Превышение таймаута не
    исключение: пишем WARNING и идём дальше — выход из приложения не должен
    падать из-за диагностики.

    Почему ``quit()`` здесь безопасен: он не прерывает выполняющуюся работу,
    а только просит цикл событий завершиться ПОСЛЕ текущего слота. Поток без
    цикла событий (свой ``run``) на просьбу не отреагирует — его остановит
    владелец, а таймаут не даст выходу повиснуть.
    """
    waited = 0
    for thread in threads_of(root):
        try:
            running = thread.isRunning()
        except RuntimeError:
            # Поток уже разрушен (C++ объект удалён) — ждать нечего.
            continue
        if not running:
            continue

        waited += 1
        name = thread.objectName() or type(thread).__name__
        logger.info("Закрытие окна: останавливаем рабочий поток %r", name)
        try:
            thread.quit()
            finished = thread.wait(timeout_ms)
        except RuntimeError:
            continue
        if not finished:
            logger.warning(
                "Закрытие окна: поток %r не завершился за %s мс — "
                "закрываем без ожидания",
                name, timeout_ms,
            )
    return waited


def cancel_running_tasks(task_holder, attribute: str) -> int:
    """
    Помечает задачи отменёнными перед ожиданием пула.

    Задача сама решит не применять результат (см. RecognitionTask.cancel);
    это уменьшает время ожидания и убирает лишние диалоги после закрытия.
    Возвращает число отменённых задач.
    """
    task = getattr(task_holder, attribute, None)
    cancel = getattr(task, "cancel", None)
    if not callable(cancel):
        return 0
    try:
        cancel()
    except Exception as e:  # noqa: BLE001 — отмена не должна мешать выходу
        logger.warning("Не удалось отменить задачу %r: %s", attribute, e)
        return 0
    return 1


__all__ = [
    "DEFAULT_TIMEOUT_MS",
    "cancel_running_tasks",
    "thread_pools_of",
    "threads_of",
    "wait_for_thread_pools",
    "wait_for_threads",
]
