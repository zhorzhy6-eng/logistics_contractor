"""Cancellation shared by document readers and remote recognizers."""
import time

#: Шаг опроса отмены при ожидании. Компромисс: 0,1 секунды кнопка «Отменить»
#: замечает почти мгновенно, а пробуждений за 30 секунд паузы всего 300 —
#: на фоне сетевого запроса это ничто.
CANCEL_POLL_SECONDS = 0.1


class ImportCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise ImportCancelled()


def interruptible_sleep(seconds, cancel=None, step=CANCEL_POLL_SECONDS):
    """Пауза, которую прерывает отмена (кнопка «Отменить» в окне импорта).

    Одним куском ждать нельзя: после ответа 429 пауза доходит до 30 секунд
    (см. `DocumentImportService.RATE_LIMIT_PAUSE_SECONDS`), и всё это время
    кнопка «Отменить» не действовала бы — оператор видел бы зависшее окно.
    Ожидание идёт короткими шагами, между ними проверяется признак отмены;
    при отмене поднимается `ImportCancelled`.

    :param seconds: сколько всего ждать (0 и меньше — только проверка отмены).
    :param cancel: признак отмены (`threading.Event`) или None — тогда ждём
        обычным `time.sleep` одним куском: прерывать нечем.
    :param step: длина одного шага ожидания.
    """
    total = float(seconds or 0)
    if cancel is None:
        # Прерывать нечем — обычное ожидание, без дробления.
        if total > 0:
            time.sleep(total)
        return
    remaining = total
    while remaining > 1e-9:
        check_cancel(cancel)
        time.sleep(min(step, remaining))
        remaining -= step
    # Отмена, пришедшая на последнем шаге, тоже должна остановить импорт.
    check_cancel(cancel)
