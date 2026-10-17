#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Подсказки по нормам права в реальном времени (уровень 3 юр. помощи).

Зачем отдельный слой
--------------------
Проверка формы (legal_check.py) отвечает на вопрос «что не так» по кнопке.
Подсказка отвечает на него САМА, когда оператор вписал значение: срок
оплаты, ставку НДС, маршрут или дату окончания ВУ. Разница только в
моменте, но правила другие:

  * подсказка — ТЕКСТ, а не правка: форма не меняется ни на йоту;
  * не чаще одной в LEGAL_HINT_INTERVAL секунд (по умолчанию 5 минут) и
    не повторяя тот же текст;
  * включается настройкой agent.legal_hints (по умолчанию включена) —
    оператор может выключить и больше не видеть замечаний;
  * только по полям, которые заполняют руками (LEGAL_HINT_RULES);
  * ПУСТАЯ ФОРМА НЕ ПОДСКАЗЫВАЕТ: пока оператор ничего не ввёл, «поле не
    заполнено» — это состояние новой формы, а не нарушение (см.
    ``any_filled``). Иначе панель, открытая вместе с программой, начинала
    разговор с упрёка.

Как устроено
------------
Движок «не рано ли снова» уже есть — core.agent.proactivity.SuggestionEngine:
он же ограничивает обычные проактивные подсказки. Здесь он переиспользуется,
а не переписывается: у подсказки и юридического замечания одинаковый закон
частоты.

Модуль без Qt: панель только спрашивает «есть что сказать?» и показывает
ответ. Поэтому подсказки проверяются тестами без поднятия интерфейса.
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from services.agent.tools import legal_check
from services.agent.tools import proactivity

logger = logging.getLogger("services.agent.tools.legal_hints")

#: Пауза между юридическими подсказками, секунды. Задание: «подсказки не
#: чаще 1 в 5 минут».
LEGAL_HINT_INTERVAL = proactivity.SUGGESTION_INTERVAL

#: Ключ настройки, которой подсказки включаются и выключаются.
SETTINGS_KEY = "legal_hints"

#: Значение по умолчанию: подсказки включены. Они не меняют форму и не
#: лезут в диалог сами — только строка в панели, когда панель открыта.
DEFAULT_ENABLED = True


def enabled_from_settings(settings: Optional[Any] = None) -> bool:
    """
    Включены ли юридические подсказки по настройкам (agent.legal_hints).

    Ошибка чтения настроек подсказки НЕ выключает и не роняет окно:
    подсказка — вспомогательное, а не обязательное поведение.
    """
    try:
        if settings is None:
            from core.settings_service import get_settings_service

            settings = get_settings_service()
        values = settings.agent_settings()
        if SETTINGS_KEY not in values:
            return DEFAULT_ENABLED
        return bool(values.get(SETTINGS_KEY))
    except Exception as error:  # noqa: BLE001 — настройки не должны ронять окно
        logger.warning("Юридические подсказки: настройка не прочитана (%s)",
                       type(error).__name__)
        return DEFAULT_ENABLED


class LegalHintTracker:
    """
    Решает, какую юридическую подсказку показать и не рано ли снова.

    Использование::

        tracker = LegalHintTracker()
        for text in tracker.refresh(window=window, contract_type="perevozka"):
            panel.append_message(text, "agent")

    :param enabled: включены ли подсказки;
    :param interval: пауза между подсказками, секунды;
    :param check: функция проверки формы (подменяется тестами);
    :param clock: часы (подменяются тестами: ждать пять минут нельзя).
    """

    def __init__(self, *, enabled: bool = DEFAULT_ENABLED,
                 interval: float = LEGAL_HINT_INTERVAL,
                 check: Optional[Callable[..., Dict[str, Any]]] = None,
                 clock: Optional[Callable[[], float]] = None):
        self.engine = proactivity.SuggestionEngine(
            enabled=bool(enabled), interval=interval, clock=clock,
        )
        self._check = check or legal_check.check_contract_legal
        #: Последние проверенные значения: пока форма не менялась, заново
        #: читать её и проверять незачем.
        self._last_signature: Optional[str] = None
        self._last_texts: List[str] = []
        #: Подсказка была отложена паузой: её нужно попробовать снова, даже
        #: если форма не менялась. Иначе замечание, пришедшееся на паузу,
        #: не показалось бы никогда — оператор так и не узнал бы о норме.
        self._pending = False

    # ── настройка ──

    @property
    def enabled(self) -> bool:
        """Включены ли подсказки сейчас."""
        return bool(self.engine.enabled)

    def configure(self, enabled: bool, interval: Optional[float] = None) -> None:
        """Включает/выключает подсказки (зовётся при смене настроек)."""
        self.engine.configure(bool(enabled), interval)

    def reset(self) -> None:
        """Забывает время и текст последней подсказки (смена окна)."""
        self.engine.reset()
        self._last_signature = None
        self._last_texts = []
        self._pending = False

    # ── подсказки ──

    def texts_for(self, values: Dict[str, Any],
                  contract_type: str = "") -> List[str]:
        """
        Тексты подсказок для готовых значений формы.

        Значения поднимаются до плоского словаря «раздел.поле» и
        проверяются тем же инструментом, что и кнопка «Юридическая
        проверка»: два разных мнения о форме в программе недопустимы.
        """
        if not values:
            return []
        result = self._check(contract_type=contract_type, values=dict(values))
        return proactivity.legal_hint_texts(result)

    def refresh(self, *, window: Any = None,
                values: Optional[Dict[str, Any]] = None,
                contract_type: str = "") -> List[str]:
        """
        Проверяет форму и возвращает подсказку, если её пора показать.

        Возвращается не больше ОДНОЙ подсказки за вызов: две строки подряд
        оператор читает как навязчивость. Если форма не менялась с прошлого
        раза, проверка не повторяется (но пауза движка своё дело знает).

        :return: список из нуля или одного текста.
        """
        if not self.engine.enabled:
            return []

        flat = legal_check.form_values(window=window, values=values)
        signature = _signature(flat)
        if signature == self._last_signature and not self._pending:
            return []
        self._last_signature = signature
        self._pending = False

        if not any_filled(flat):
            # Пустая форма — не повод для замечания: оператор её ещё не
            # заполнял, а «поле не заполнено» в этот момент читается как
            # упрёк сразу после запуска программы. Проверка по кнопке
            # «⚖» при этом работает как прежде.
            self._last_texts = []
            return []

        try:
            texts = self.texts_for(flat, contract_type=contract_type)
        except Exception as error:  # noqa: BLE001 — подсказка не должна ронять окно
            logger.warning("Юридическая подсказка не собрана: %s", error)
            return []
        self._last_texts = texts

        for text in texts:
            offered = self.engine.offer(text)
            if offered:
                logger.info("Юридическая подсказка показана (правил: %s)", len(texts))
                return [offered]
        # Подсказки есть, но пауза не прошла — попробуем на следующем шаге.
        self._pending = bool(texts)
        return []

    @property
    def last_texts(self) -> List[str]:
        """Все подсказки последней проверки (для тестов и журнала)."""
        return list(self._last_texts)


def _signature(values: Dict[str, Any]) -> str:
    """
    Подпись значений формы: менялись ли поля.

    Разделитель — символ, которого в значениях полей не бывает, чтобы
    «ab|с» и «a|b|с» не дали одинаковую подпись.
    """
    parts = [f"{key}\x1f{values[key]!r}" for key in sorted(values)]
    return "\x1e".join(parts)


def any_filled(values: Dict[str, Any]) -> bool:
    """
    Есть ли в форме хоть одно введённое значение.

    Нужно, чтобы отличить «оператор работает с формой» от «окно только что
    открылось»: у пустой формы замечание «поле не заполнено» — не помощь, а
    упрёк. Списки (машины, точки маршрута) проверяются по содержимому:
    одна пустая строка таблицы — это всё ещё пустая форма.
    """
    return any(_has_content(value) for value in (values or {}).values())


def _has_content(value: Any) -> bool:
    """
    Ввёл ли оператор хоть что-нибудь в это место формы.

    Отличие от ``legal_rules.is_blank``: там ноль — «не заполнено»
    (денежное поле с нулём смысла не имеет), а здесь ноль — ВВЕДЁННОЕ
    значение: раз в поле стоит 0, оператор уже работает с формой, и
    подсказка про другое поле приходится по делу.
    """
    if isinstance(value, dict):
        return any(_has_content(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_has_content(item) for item in value)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


__all__ = [
    "DEFAULT_ENABLED",
    "LEGAL_HINT_INTERVAL",
    "SETTINGS_KEY",
    "LegalHintTracker",
    "any_filled",
    "enabled_from_settings",
]
