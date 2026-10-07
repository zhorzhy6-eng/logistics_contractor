#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Замер маскирования ПДн перед GigaChat (core/pseudonymizer.py).

Считает на СИНТЕТИЧЕСКИХ документах (реальных ПДн в файле и в отчёте нет):

  * recall — доля фрагментов ПДн, которые не попали в «безопасный» текст;
  * задержку на документ: «только регулярки» против «регулярки + Natasha».

Запуск:

    python tools/measure_pseudonymizer.py
    python tools/measure_pseudonymizer.py --repeat 5

Значения ПДн нигде не печатаются: в отчёте только номера документов,
счётчики и время. Так же устроены логи проекта (AGENTS.md § 3.3).
"""

import argparse
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.pseudonymizer as pseudonymizer  # noqa: E402  (нужен sys.path выше)

# ─────────────────────────────────────────────────────────────
# Синтетические документы: (текст, [фрагменты ПДн, которые обязаны исчезнуть])
# ─────────────────────────────────────────────────────────────

DOCUMENTS: List[Tuple[str, List[str]]] = [
    (
        "Договор-заявка № 23092026-74 от 23.09.2026\n"
        "Водитель: Кузнецов Пётр Иванович, паспорт 60 26 123456 выдан "
        "20.06.2015, телефон +7 (999) 123-45-67\n",
        ["Кузнецов Пётр Иванович", "60 26 123456", "+7 (999) 123-45-67"],
    ),
    (
        "Водительское удостоверение 99 36 123456, категории B, C, E, "
        "действительно до 10.10.2030. Водитель Кузнецов Пётр Иванович.\n",
        ["99 36 123456", "Кузнецов Пётр Иванович"],
    ),
    (
        "Харуки Мураками прибыл с визитом, встреча в г. Москва, "
        "ул. Тверская, д. 5, кв. 17.\n",
        ["Харуки Мураками", "г. Москва, ул. Тверская, д. 5, кв. 17"],
    ),
    (
        "В анкете указано Сергеев Сергей, тел. 8 (495) 123-45-67, "
        "почта sergeev@example.ru\n",
        ["Сергеев Сергей", "8 (495) 123-45-67", "sergeev@example.ru"],
    ),
    (
        "Договор заключён с Остапом Бендером, претензий нет.\n",
        ["Остапом Бендером"],
    ),
    (
        "Автомобиль управлялся Кузнецовым Петром Ивановичем, "
        "претензий нет.\n",
        ["Кузнецовым Петром Ивановичем"],
    ),
    (
        "ИНН 7707083893, КПП 770101001, ОГРН 1027700132195, "
        "СНИЛС 112-233-445 95\n",
        ["7707083893", "112-233-445 95"],
    ),
    (
        "ИНН 500100732259, р/с 40702810000000000001, "
        "корр. счёт 30101810400000000225, БИК 044525225\n",
        ["500100732259"],
    ),
    (
        "VIN EC3TEUMB0T0002608, госномер А123ВС77, прицеп 71ABF18, "
        "водитель Петров Пётр Петрович\n",
        ["EC3TEUMB0T0002608", "А123ВС77", "71ABF18", "Петров Пётр Петрович"],
    ),
    (
        "Адрес погрузки: 183052, г. Мурманск, ул. Тестовая, д. 53, кв. 12\n"
        "Адрес выгрузки: г. Пятигорск, Бештаугорское шоссе 17\n",
        [
            "183052, г. Мурманск, ул. Тестовая, д. 53, кв. 12",
            "г. Пятигорск, Бештаугорское шоссе 17",
        ],
    ),
    (
        "Адрес: Московская область, г. Химки, ул. Ленинградская, д. 25, "
        "корп. 2, офис 3\n",
        ["Московская область, г. Химки, ул. Ленинградская, д. 25, корп. 2, офис 3"],
    ),
    (
        "Грузоотправитель: ООО «ВОТУР МОТОР РУС», адрес: г. Москва, "
        "ул. Ленинградская, д. 25\n",
        ["г. Москва, ул. Ленинградская, д. 25"],
    ),
    (
        "Директор Смирнов А. В. подписал. Адрес регистрации: "
        "г. Химки, ул. Центральная, д. 1\n",
        ["Смирнов А. В.", "г. Химки, ул. Центральная, д. 1"],
    ),
    (
        "Стоимость 180300 руб., срок оплаты 10 дней, дата 24.09.2026, "
        "номер договора 23092026-74\n",
        [],
    ),
    (
        "Автомобиль JETOUR T2, полуприцеп YANGMINDA, тягач Foton Auman, "
        "вес груза 20 тонн, стоимость 400000 руб.\n",
        [],
    ),
]


def _significant_tokens(fragment: str) -> List[str]:
    """
    Значимые куски фрагмента — слова и цифровые группы от 4 символов.

    Нужны для честной второй оценки: адрес может быть замаскирован
    ЧАСТИЧНО (улица скрыта, город остался). Тогда сам фрагмент в тексте
    уже не встречается, но ПДн в нём ещё видны.
    """
    return [part for part in re.split(r"[\s,;.()\-–—/]+", fragment) if len(part) >= 4]


#: Длинный синтетический документ: те же заготовки, повторённые 4 раза.
#: На коротких строках Natasha выглядит мгновенной, а в GigaChat уходит
#: текст документа целиком — задержку надо мерить и на нём.
LONG_DOCUMENT = "\n".join(text for text, _gold in DOCUMENTS * 4)


def _time_documents(texts: List[str], repeat: int) -> float:
    """Среднее время маскирования одного текста из списка."""
    pseudo = pseudonymizer.Pseudonymizer()
    pseudo.anonymize("Прогрев без персональных данных.")
    start = time.perf_counter()
    for _ in range(repeat):
        for text in texts:
            pseudo.anonymize(text)
    return (time.perf_counter() - start) / (len(texts) * repeat)


def _anonymize_all(mode: str, repeat: int) -> Tuple[float, Dict[str, object]]:
    """
    Прогон всех документов в заданном режиме.

    :return: (среднее время на документ, счётчики попаданий/промахов)
    """
    pseudo = pseudonymizer.Pseudonymizer()
    total_time = 0.0
    total_gold = 0
    missed = 0
    partial = 0
    leaked_docs: List[int] = []
    partial_docs: List[int] = []

    # Прогрев: первый вызов поднимает модели (в режиме natasha) и заполняет
    # кеши регулярных выражений, иначе он один съест всё время замера.
    pseudo.anonymize("Прогрев без персональных данных.")

    for _ in range(repeat):
        for index, (text, gold) in enumerate(DOCUMENTS):
            start = time.perf_counter()
            safe, _mapping = pseudo.anonymize(text)
            total_time += time.perf_counter() - start

            for fragment in gold:
                total_gold += 1
                if fragment in safe:
                    missed += 1
                    if index not in leaked_docs:
                        leaked_docs.append(index)
                    continue
                if any(part in safe for part in _significant_tokens(fragment)):
                    partial += 1
                    if index not in partial_docs:
                        partial_docs.append(index)

    return total_time / (len(DOCUMENTS) * repeat), {
        "gold": total_gold,
        "missed": missed,
        "partial": partial,
        "leaked_docs": leaked_docs,
        "partial_docs": partial_docs,
    }


def _set_natasha(enabled: bool) -> bool:
    """Включает/выключает слой Natasha; возвращает фактическое состояние."""
    pseudonymizer._NATASHA = {}
    if enabled:
        pseudonymizer._NATASHA_AVAILABLE = None
        return pseudonymizer._natasha_pipeline() is not None
    pseudonymizer._NATASHA_AVAILABLE = False
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Замер маскирования ПДн")
    parser.add_argument("--repeat", type=int, default=3,
                        help="сколько раз прогнать каждый документ (по умолчанию 3)")
    args = parser.parse_args()

    # Инициализация Natasha измеряется отдельно: это разовая плата за процесс.
    pseudonymizer._NATASHA = {}
    pseudonymizer._NATASHA_AVAILABLE = None
    start = time.perf_counter()
    pipeline = pseudonymizer._natasha_pipeline()
    init_time = time.perf_counter() - start

    print("Замер маскирования ПДн (core/pseudonymizer.py)")
    print(f"Документов: {len(DOCUMENTS)}, повторов: {args.repeat}")
    print(f"Natasha: {'установлена' if pipeline else 'НЕ установлена'}"
          f" (инициализация {init_time:.3f} с)")
    print("-" * 62)

    _set_natasha(False)
    regex_time, regex_stats = _anonymize_all("regex", args.repeat)
    long_regex = _time_documents([LONG_DOCUMENT], args.repeat)

    natasha_on = _set_natasha(True)
    natasha_time, natasha_stats = _anonymize_all("natasha", args.repeat)
    long_natasha = _time_documents([LONG_DOCUMENT], args.repeat) if natasha_on else 0.0

    def _recall(stats: Dict[str, object]) -> float:
        gold = int(stats["gold"])  # type: ignore[arg-type]
        missed = int(stats["missed"])  # type: ignore[arg-type]
        return 100.0 * (gold - missed) / gold if gold else 0.0

    print(f"{'режим':<22}{'recall':>10}{'сек/документ':>16}")
    print(f"{'только регулярки':<22}{_recall(regex_stats):>9.1f}%{regex_time:>16.4f}")
    label = "регулярки + Natasha" if natasha_on else "регулярки (Natasha нет)"
    print(f"{label:<22}{_recall(natasha_stats):>9.1f}%{natasha_time:>16.4f}")
    print("-" * 62)
    print(f"Прирост задержки: {natasha_time - regex_time:+.4f} с на документ")

    size_kb = len(LONG_DOCUMENT.encode("utf-8")) / 1024
    print(f"Длинный документ ({size_kb:.1f} КБ): регулярки {long_regex:.3f} с, "
          f"+ Natasha {long_natasha:.3f} с, прирост "
          f"{long_natasha - long_regex:+.3f} с")

    for name, stats in (("регулярки", regex_stats), ("+ Natasha", natasha_stats)):
        missed = int(stats["missed"])  # type: ignore[arg-type]
        partial = int(stats["partial"])  # type: ignore[arg-type]
        gold = int(stats["gold"])  # type: ignore[arg-type]
        leaked = ", ".join(str(i + 1) for i in stats["leaked_docs"]) or "нет"  # type: ignore[union-attr]
        partly = ", ".join(str(i + 1) for i in stats["partial_docs"]) or "нет"  # type: ignore[union-attr]
        print(f"{name}: фрагментов {gold}, в открытом виде {missed} "
              f"(документы: {leaked}), замаскировано частично {partial} "
              f"(документы: {partly})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
