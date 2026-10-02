# -*- coding: utf-8 -*-
"""
Реестр типов договоров (Шаг 1 рефакторинга архитектуры контрактов).

Регистрация — явная, через ContractTypeRegistry.register(spec): новый тип
договора = новый пакет в core/contracts/ + одна строка регистрации в его
__init__.py. Автообнаружения по папкам нет сознательно: ошибка импорта
одного типа не должна мешать загрузке остальных (изоляция L1).

Импорт модулей типов — ленивый и изолированный (load_builtin /
_import_type_module): падение модуля логируется в failures() и не
распространяется на другие типы.
"""

import importlib
import logging
from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Mapping, Optional, Tuple, Type

from core.contracts.contract_types import ContractType, DEFAULT_CONTRACT_TYPE

logger = logging.getLogger("core.contracts.registry")


class ContractTypeError(RuntimeError):
    """Ошибка конфигурации типа договора (регистрация/спецификация)."""


class UnknownContractTypeError(KeyError):
    """Запрошен тип договора, которого нет в реестре (строгий режим)."""


@dataclass(frozen=True)
class ContractTypeSpec:
    """
    Описание одного типа договора.

    contract_type — ключ (значение ContractType, "perevozka" и т.д.);
    title — человекочитаемое название для UI;
    module — имя модуля, где объявлены классы (для ленивой загрузки);
    generator_class / validator_class — классы генератора и валидатора;
    templates_subdir — подпапка шаблонов внутри templates/ ("" — общая папка).
    """

    contract_type: str
    title: str
    module: str = ""
    generator_class: Optional[Type[Any]] = None
    validator_class: Optional[Type[Any]] = None
    templates_subdir: str = ""


def register_contract_type(
    contract_type,
    title: str,
    generator_class: Optional[Type[Any]] = None,
    validator_class: Optional[Type[Any]] = None,
    templates_subdir: str = "",
) -> ContractTypeSpec:
    """
    Регистрирует тип договора и возвращает спецификацию.

    Вызывается из __init__.py пакета типа (например,
    core/contracts/perevozka/__init__.py). module подставляется по классу
    генератора (или валидатора, если генератора нет — Excel-тип).
    """
    if isinstance(contract_type, ContractType):
        key = contract_type.value
    else:
        key = str(contract_type)

    module = ""
    for klass in (generator_class, validator_class):
        if klass is not None:
            module = klass.__module__
            break

    spec = ContractTypeSpec(
        contract_type=key,
        title=title,
        module=module,
        generator_class=generator_class,
        validator_class=validator_class,
        templates_subdir=templates_subdir,
    )
    ContractTypeRegistry.register(spec)
    return spec


#: Встроенные типы, которые реестр подгружает при старте. Наполняется по
#: мере появления типов: шаг 2 — perevozka, шаг 8 — заглушки остальных.
BUILTIN_TYPE_MODULES: Tuple[str, ...] = (
    "core.contracts.perevozka",
    "core.contracts.arenda_ts",
    "core.contracts.expediciya",
    "core.contracts.zayavka",
)


class ContractTypeRegistry:
    """Единый реестр типов договоров (только классовые методы)."""

    _specs: ClassVar[Dict[str, ContractTypeSpec]] = {}
    _failures: ClassVar[Dict[str, str]] = {}
    _frozen: ClassVar[bool] = False

    # ─────────────────────────────────────────────────────────
    # Регистрация
    # ─────────────────────────────────────────────────────────

    @classmethod
    def register(cls, spec: ContractTypeSpec) -> None:
        """Регистрирует тип. Дубль ключа — ContractTypeError."""
        if cls._frozen:
            logger.warning(
                f"Реестр заморожен — регистрация типа {spec.contract_type!r} "
                f"игнорирована"
            )
            return

        key = str(spec.contract_type)
        if key in cls._specs:
            raise ContractTypeError(
                f"Тип договора {key!r} уже зарегистрирован"
            )
        if not spec.title:
            raise ContractTypeError(
                f"Тип договора {key!r}: не заполнено название (title)"
            )

        cls._specs[key] = spec
        logger.info(f"Тип договора зарегистрирован: {key} ({spec.title})")

    @classmethod
    def freeze(cls) -> None:
        """Замораживает реестр: поздняя регистрация только логируется."""
        if not cls._frozen:
            cls._frozen = True
            logger.info(f"Реестр заморожен: {len(cls._specs)} типов")

    # ─────────────────────────────────────────────────────────
    # Доступ
    # ─────────────────────────────────────────────────────────

    @classmethod
    def resolve_key(cls, contract_type=None, *, strict: bool = False) -> str:
        """
        Ключ типа из произвольного значения.

        None → тип по умолчанию (перевозка). Неизвестный ключ:
        strict=True → UnknownContractTypeError;
        strict=False → fallback на тип по умолчанию + warning.
        """
        if contract_type is None:
            return DEFAULT_CONTRACT_TYPE.value
        if isinstance(contract_type, ContractType):
            key = contract_type.value
        else:
            key = str(contract_type).strip()

        if key in cls._specs:
            return key

        if strict:
            raise UnknownContractTypeError(
                f"Неизвестный тип договора: {key!r}; "
                f"известны: {sorted(cls._specs)}"
            )

        default = DEFAULT_CONTRACT_TYPE.value
        logger.warning(
            f"Неизвестный тип договора {key!r} — fallback на {default!r}"
        )
        return default

    @classmethod
    def get(cls, contract_type=None, *, strict: bool = False) -> ContractTypeSpec:
        """Спецификация типа; неизвестный ключ — по правилам resolve_key."""
        return cls._specs[cls.resolve_key(contract_type, strict=strict)]

    @classmethod
    def find(cls, contract_type) -> Optional[ContractTypeSpec]:
        """Спецификация или None (без fallback и без исключений)."""
        if isinstance(contract_type, ContractType):
            key = contract_type.value
        else:
            key = str(contract_type).strip() if contract_type is not None else ""
        return cls._specs.get(key)

    @classmethod
    def known_types(cls) -> Tuple[str, ...]:
        """Зарегистрированные ключи типов в алфавитном порядке."""
        return tuple(sorted(cls._specs))

    @classmethod
    def titles(cls) -> Mapping[str, str]:
        """{ключ: название} — для селектора типа в UI."""
        return {key: spec.title for key, spec in cls._specs.items()}

    @classmethod
    def failures(cls) -> Mapping[str, str]:
        """Модули типов, которые не удалось импортировать: {модуль: причина}."""
        return dict(cls._failures)

    # ─────────────────────────────────────────────────────────
    # Ленивая загрузка встроенных типов (изоляция L1)
    # ─────────────────────────────────────────────────────────

    @classmethod
    def load_builtin(cls) -> None:
        """
        Импортирует модули встроенных типов из BUILTIN_TYPE_MODULES.

        Каждый модуль грузится в собственном try/except: ошибка импорта
        одного типа записывается в failures() и не мешает остальным.
        """
        for module in BUILTIN_TYPE_MODULES:
            cls._import_type_module(module)

    @classmethod
    def _import_type_module(cls, module: str) -> None:
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 — изоляция: тип не должен ронять остальных
            cls._failures[module] = f"{type(exc).__name__}: {exc}"
            logger.exception(
                f"Тип договора не загружен (модуль {module}): "
                f"{type(exc).__name__}: {exc}"
            )
        else:
            cls._failures.pop(module, None)
