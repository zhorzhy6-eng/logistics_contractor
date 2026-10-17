#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Стресс-тест агента «Максимилиан» (промпт «Фиксы + мощные тесты», подэтап 1.F).

50 запросов по пяти категориям проходят через НАСТОЯЩИЙ цикл агента и
НАСТОЯЩИЙ реестр инструментов (40 штук). Подставная только модель: живые
запросы к GigaChat стоили бы тысячи токенов и давали бы нестабильный
результат, а проверяется здесь не качество модели, а устойчивость программы —
что реестр отвечает, предохранители держат, ответ всегда непустой.

    python tools/powerful_agent_stress.py

Категории (по 10 запросов): поиск, справка, аналитика, правовая база, форма.

Что НЕ вызывается и почему:
  * ``check_inn_dadata``, ``search_company_dadata``, ``check_address_dadata``,
    ``compare_with_dadata``, ``download_legal`` — уходят в сеть; в стресс-тесте
    сети нет, их проверяют отдельно;
  * ``recognize_document`` — поднимает OCR на файлах, это десятки секунд на
    запрос и отдельный сценарий (см. tools/powerful_e2e.py).

Метрики печатаются и складываются в ``tests/_tmp/powerful_tests/stress_agent.json``.
Код возврата: 0 — все 50 прошли, 1 — есть провал.
"""

import argparse
import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.powerful_e2e import (  # noqa: E402
    DRIVER, ORGANIZATION, ScriptedChat, StubWindow, text_reply, tool_reply,
)

OUT_ROOT = PROJECT_ROOT / "tests" / "_tmp" / "powerful_tests"
REPORT = OUT_ROOT / "stress_agent.json"

#: Лимит вызовов инструментов на один запрос (как в приложении).
MAX_TOOL_STEPS = 8


# ─────────────────────────────────────────────────────────────
# Пять категорий по десять запросов
# ─────────────────────────────────────────────────────────────
# Формат: (запрос оператора, имя инструмента, аргументы).
# Аргументы подобраны так, чтобы инструмент реально отработал: пустая строка
# в запросе — это тоже настоящий случай (модель часто зовёт поиск без слов),
# но половина запросов должна доходить до данных.

QUERIES: Dict[str, Tuple[Tuple[str, str, Dict[str, Any]], ...]] = {
    "поиск": (
        ("найди водителя Абрамяна", "search_driver", {"query": "Абрамян"}),
        ("найди водителя по телефону +7 999 123-45-67", "search_driver",
         {"query": "+7 999 123-45-67"}),
        ("найди перевозчика Ромашка", "search_carrier", {"query": "Ромашка"}),
        ("покажи заказчиков", "search_customer", {"query": "Заказчик"}),
        ("найди машину с VIN EC3TEUMB0T0002608", "search_vehicle",
         {"query": "EC3TEUMB0T0002608"}),
        ("покажи договоры за октябрь", "search_contract", {"query": "2026-10"}),
        ("найди ПВ-2026-101", "search_contract", {"query": "ПВ-2026-101"}),
        ("поищи везде по слову Пятигорск", "search_anything",
         {"query": "Пятигорск"}),
        ("какие договоры истекают в ближайшие 30 дней",
         "list_expiring_contracts", {"days": 30}),
        ("сколько всего водителей", "count_records",
         {"entity_type": "driver"}),
    ),
    "справка": (
        ("что такое ИНН", "explain_field", {"field": "inn"}),
        ("как заполнить VIN", "explain_field", {"field": "vin"}),
        ("объясни поле цена с НДС", "explain_field",
         {"field": "price_with_vat"}),
        ("что писать в КПП", "explain_field", {"field": "kpp"}),
        ("покажи справку по программе", "get_help", {}),
        ("какие разделы есть в обучении", "tutorial", {}),
        ("покажи схему полей договора", "get_schema", {}),
        ("какие вкладки открыты", "get_open_tabs", {}),
        ("какое окно открыто", "get_current_window", {}),
        ("что заполнять дальше", "suggest_next_field", {}),
    ),
    "аналитика": (
        ("сколько договоров за октябрь", "aggregate_contracts",
         {"period": "2026-10"}),
        ("топ перевозчиков", "top_contractors", {"limit": 5}),
        ("сравни сентябрь и октябрь", "compare_periods",
         {"period_a": "2026-09", "period_b": "2026-10"}),
        ("сколько всего договоров", "count_records",
         {"entity_type": "contract"}),
        ("сколько перевозчиков в базе", "count_records",
         {"entity_type": "carrier"}),
        ("проверь качество данных", "check_data_quality", {}),
        ("что истекает", "warn_expiring", {"days": 14}),
        ("что заполняли в прошлый раз", "suggest_from_history", {}),
        ("сколько договоров было в сентябре", "aggregate_contracts",
         {"period": "2026-09"}),
        ("сравни прошлый и этот месяц", "compare_periods",
         {"period_a": "2026-09", "period_b": "2026-10"}),
    ),
    "правовая база": (
        ("ответственность за просрочку доставки", "search_legal",
         {"query": "просрочка"}),
        ("что говорит закон о штрафах за перегруз", "search_legal",
         {"query": "перегруз штраф"}),
        ("правила перевозки опасных грузов", "search_legal",
         {"query": "опасные грузы"}),
        ("сроки доставки по закону", "search_legal", {"query": "сроки доставки"}),
        ("требования к путевому листу", "search_legal",
         {"query": "путевой лист"}),
        ("что такое транспортная накладная", "search_legal",
         {"query": "транспортная накладная"}),
        ("нормы времени отдыха водителя", "search_legal",
         {"query": "режим труда и отдыха"}),
        ("ответственность за ДТП с грузом", "search_legal",
         {"query": "ДТП"}),
        ("лицензирование перевозок", "search_legal", {"query": "лицензия"}),
        ("найди ФЗ-259", "search_legal", {"query": "259-ФЗ"}),
    ),
    "форма": (
        ("что не заполнено", "check_required", {}),
        ("проверь форму", "validate_current_form", {}),
        ("прочитай состояние формы", "get_form_state", {}),
        ("покажи данные формы", "get_current_form", {}),
        ("что выбрано в таблице", "get_selected_row", {}),
        ("что я делал последние пять минут", "get_recent_actions",
         {"minutes": 5}),
        ("заполни поле название перевозчика", "fill_field",
         {"field": "carrier_name", "value": ORGANIZATION["full_name"]}),
        ("заполни данные водителя из базы", "fill_entity_fields",
         {"entity_type": "driver", "query": DRIVER["full_name"]}),
        ("примени распознанные поля", "apply_recognized_fields",
         {"data": {}}),
        ("заполни из справочника", "fill_from_entity",
         {"entity_type": "carrier", "query": "Ромашка"}),
    ),
}

#: Опасные инструменты: агент обязан спросить, и на отказ — не выполнять.
DANGEROUS_TOOLS = frozenset({"fill_field", "fill_entity_fields",
                             "parse_and_fill", "apply_recognized_fields"})


@dataclass
class QueryResult:
    """Итог одного запроса."""

    category: str
    query: str
    tool: str
    ok: bool
    problem: str = ""
    seconds: float = 0.0
    steps: int = 0
    tool_names: List[str] = field(default_factory=list)
    text_chars: int = 0


def _run_query(agent, chat, window, asked, category: str, query: str,
               tool_name: str, arguments: Dict[str, Any]) -> QueryResult:
    """Один запрос оператора: план модели, вызов, проверка ответа."""
    chat.plan([
        tool_reply(tool_name, arguments),
        text_reply(f"Готово: {tool_name} по запросу «{query}»."),
    ])
    started = time.time()
    result = QueryResult(category=category, query=query, tool=tool_name,
                         ok=True)
    asked_before = len(asked)
    try:
        reply = agent.send(query)
    except Exception as exc:                                # noqa: BLE001
        result.ok = False
        result.problem = f"{type(exc).__name__}: {exc}"
        result.seconds = time.time() - started
        return result

    result.seconds = time.time() - started
    result.steps = reply.steps
    result.tool_names = reply.tool_names
    result.text_chars = len(reply.text.strip())

    if not reply.text.strip():
        result.ok = False
        result.problem = "пустой текст ответа"
    elif reply.step_limit_reached:
        result.ok = False
        result.problem = f"предохранитель: {reply.stop_reason}"
    elif reply.timed_out:
        result.ok = False
        result.problem = "таймаут запроса"
    elif result.steps > MAX_TOOL_STEPS:
        result.ok = False
        result.problem = f"шагов {result.steps} > {MAX_TOOL_STEPS}"
    elif tool_name in DANGEROUS_TOOLS and len(asked) == asked_before:
        result.ok = False
        result.problem = "опасный инструмент выполнен без подтверждения"
    return result


def _percentile(values: List[float], part: float) -> float:
    """Простая перцентиль без зависимостей (значения уже отсортированы)."""
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, int(round(part * (len(values) - 1)))))
    return values[index]


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    parser = argparse.ArgumentParser(description="Стресс-тест Максимилиана")
    parser.add_argument("--limit", type=int, default=0,
                        help="ограничить число запросов (отладка)")
    args = parser.parse_args(argv)

    from core.private_path import attach_private_root
    from services.agent.core.agent import Agent
    from services.agent.tools.registry import AgentContext, build_registry

    attach_private_root()

    window = StubWindow()
    asked: List[Tuple[str, Dict[str, Any]]] = []

    def confirm(tool, arguments) -> bool:
        asked.append((tool.name, dict(arguments)))
        return False

    context = AgentContext(window=window, contract_type="perevozka",
                           confirm=confirm)
    registry = build_registry(context)
    chat = ScriptedChat()
    agent = Agent(client=chat, context=context, registry=registry,
                  max_tool_steps=MAX_TOOL_STEPS, max_request_seconds=0)

    print("=" * 72)
    print("СТРЕСС-ТЕСТ АГЕНТА: 50 запросов, 5 категорий, подставная модель")
    print("=" * 72)
    print(f"Инструментов в реестре: {len(registry.names())}")

    results: List[QueryResult] = []
    for category, queries in QUERIES.items():
        for query, tool_name, arguments in queries:
            if args.limit and len(results) >= args.limit:
                break
            outcome = _run_query(agent, chat, window, asked, category, query,
                                 tool_name, arguments)
            results.append(outcome)
            mark = "OK  " if outcome.ok else "FAIL"
            note = "" if outcome.ok else f" — {outcome.problem}"
            print(f"[{mark}] {category:14s} {query[:46]:48s} "
                  f"{outcome.seconds:6.3f} с{note}")

    return _report(results, registry)


def _report(results: List[QueryResult], registry) -> int:
    """Сводка: по категориям, время, покрытие инструментов, JSON-отчёт."""
    total = len(results)
    passed = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    timings = sorted(r.seconds for r in results)
    used = sorted({name for r in results for name in r.tool_names})
    expected_tools = sorted({r.tool for r in results})
    by_category: Dict[str, Dict[str, Any]] = {}
    for result in results:
        bucket = by_category.setdefault(
            result.category, {"queries": 0, "ok": 0, "seconds": []}
        )
        bucket["queries"] += 1
        bucket["ok"] += 1 if result.ok else 0
        bucket["seconds"].append(result.seconds)

    print()
    print("-" * 72)
    for category, bucket in by_category.items():
        average = statistics.fmean(bucket["seconds"]) if bucket["seconds"] else 0
        print(f"{category:16s} {bucket['ok']}/{bucket['queries']} "
              f"среднее {average:.3f} с")
    print("-" * 72)
    print(f"Всего: {len(passed)}/{total} прошло, {len(failed)} провалов")
    if timings:
        print(f"Время: среднее {statistics.fmean(timings):.3f} с, "
              f"медиана {statistics.median(timings):.3f} с, "
              f"p95 {_percentile(timings, 0.95):.3f} с, "
              f"максимум {timings[-1]:.3f} с")
    print(f"Инструментов в реестре: {len(registry.names())}; "
          f"позвано при прогоне: {len(used)}")
    missing = sorted(set(expected_tools) - set(used))
    if missing:
        print(f"Не позваны (модель их не выбрала): {', '.join(missing)}")
    never = sorted(set(registry.names()) - set(used))
    print(f"Инструменты реестра, не задействованные вовсе: "
          f"{len(never)} — {', '.join(never)}")

    if failed:
        print()
        print("ПРОВАЛЫ:")
        for result in failed:
            print(f"  - [{result.category}] «{result.query}» "
                  f"({result.tool}): {result.problem}")

    report = {
        "queries": total,
        "passed": len(passed),
        "failed": len(failed),
        "max_tool_steps": MAX_TOOL_STEPS,
        "seconds": {
            "mean": round(statistics.fmean(timings), 4) if timings else 0,
            "median": round(statistics.median(timings), 4) if timings else 0,
            "p95": round(_percentile(timings, 0.95), 4),
            "max": round(timings[-1], 4) if timings else 0,
            "total": round(sum(timings), 3),
        },
        "tools_in_registry": len(registry.names()),
        "tools_used": used,
        "tools_never_used": never,
        "by_category": {
            name: {"queries": bucket["queries"], "ok": bucket["ok"],
                   "seconds": round(sum(bucket["seconds"]), 3)}
            for name, bucket in by_category.items()
        },
        "failures": [
            {"category": r.category, "query": r.query, "tool": r.tool,
             "problem": r.problem, "steps": r.steps}
            for r in failed
        ],
        "details": [
            {"category": r.category, "query": r.query, "tool": r.tool,
             "ok": r.ok, "problem": r.problem,
             "seconds": round(r.seconds, 4), "steps": r.steps,
             "tools_called": r.tool_names, "text_chars": r.text_chars}
            for r in results
        ],
    }
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                      encoding="utf-8")
    print(f"\nОтчёт: {REPORT}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
