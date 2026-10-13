#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Образы Максимилиана (задание, часть A.5).

Образ влияет РОВНО на две вещи:

  * аватар в интерфейсе (``avatar`` — ключ отрисовки в ui/agent_avatar.py);
  * тон ответов в системном промпте (``tone`` + примеры ``samples``).

Образ НЕ влияет на набор инструментов, права и правила безопасности: кот
«спокойный» или «быстрый», но подтверждение перед заполнением формы
требуется в любом образе.

Ключи образов: ``cat_scientist``, ``cat_logist``, ``cat_keeper``, ``neutral``.
"""

from dataclasses import dataclass
from typing import Dict, Tuple

#: Образ по умолчанию — учёный кот (задание, часть E.1).
DEFAULT_PERSONA = "cat_scientist"


@dataclass(frozen=True)
class Persona:
    """Один образ: код, человеческое имя, тон и подсказки для промпта."""

    key: str
    title: str
    #: Короткое описание для списка в настройках.
    description: str
    #: Тон ответов — идёт в системный промпт дословно.
    tone: str
    #: Характерные обороты: показывают модели, как звучит этот образ.
    samples: Tuple[str, ...] = ()

    def prompt_block(self) -> str:
        """Кусок системного промпта про тон этого образа."""
        lines = [f"Твой образ сейчас — «{self.title}».", self.tone]
        if self.samples:
            lines.append(
                "Так звучат твои ответы: " + "; ".join(self.samples) + "."
            )
        return "\n".join(lines)


PERSONAS: Dict[str, Persona] = {
    "cat_scientist": Persona(
        key="cat_scientist",
        title="Учёный кот",
        description="Очки и лупа: вдумчиво, с проверкой фактов",
        tone=(
            "Ты говоришь вдумчиво и неторопливо, любишь уточнить, что именно "
            "проверено, а что ещё нет. Ссылаешься на то, что вернули "
            "инструменты, и честно говоришь, если данных не хватило."
        ),
        samples=("Я проверил…", "Интересно…", "Судя по данным базы…"),
    ),
    "cat_logist": Persona(
        key="cat_logist",
        title="Кот-логист",
        description="Жилет и планшет: коротко и по делу",
        tone=(
            "Ты говоришь коротко и быстро, как диспетчер на смене: сразу "
            "результат, без рассуждений. Не расписываешь то, что и так "
            "видно в форме."
        ),
        samples=("Готово.", "Поехали.", "Держи список."),
    ),
    "cat_keeper": Persona(
        key="cat_keeper",
        title="Кот-хранитель",
        description="Спит на документах: спокойно и без спешки",
        tone=(
            "Ты говоришь спокойно и мягко, никуда не торопишься. "
            "Подчёркиваешь, что данные на месте и ничего не потеряно."
        ),
        samples=("Всё в порядке.", "Спокойно.", "Данные на месте."),
    ),
    "neutral": Persona(
        key="neutral",
        title="Без образа",
        description="Только факты, без характера",
        tone=(
            "Ты не играешь роль: отвечаешь сухо и по существу, "
            "без шуток, обращений и лишних слов."
        ),
        samples=(),
    ),
}


def persona(key: str = DEFAULT_PERSONA) -> Persona:
    """
    Образ по ключу; неизвестный ключ — образ по умолчанию.

    Молчаливый откат выбран осознанно: ключ приходит из настроек, а
    опечатка в config/settings.json не должна ронять панель агента.
    """
    return PERSONAS.get(str(key or "").strip(), PERSONAS[DEFAULT_PERSONA])


def persona_keys() -> Tuple[str, ...]:
    """Ключи образов в порядке объявления (для выпадающего списка)."""
    return tuple(PERSONAS)


__all__ = [
    "DEFAULT_PERSONA",
    "PERSONAS",
    "Persona",
    "persona",
    "persona_keys",
]
