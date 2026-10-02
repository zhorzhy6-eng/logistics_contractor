# -*- coding: utf-8 -*-
"""
Тесты реестра типов договоров и фабрики (Шаг 7 рефакторинга).

Проверяют: регистрацию (в т.ч. дубли и заморозку), resolve/get/find,
ленивую изолированную загрузку встроенных типов (падение одного модуля
не мешает остальным), фабрику генераторов и валидаторов с правилами
fallback/strict.
"""

import logging
from typing import Any, Dict

import pytest

import core.contracts.registry as registry_module
from core.contracts.base_generator import BaseContractGenerator
from core.contracts.base_validator import BaseValidator
from core.contracts.contract_types import ContractType, DEFAULT_CONTRACT_TYPE
from core.contracts.factory import GeneratorFactory
from core.contracts.paths import PROJECT_ROOT, TEMPLATES_DIR
from core.contracts.perevozka.generator import PerevozkaGenerator
from core.contracts.perevozka.validator import PerevozkaValidator
from core.contracts.registry import (
    BUILTIN_TYPE_MODULES,
    ContractTypeError,
    ContractTypeRegistry,
    ContractTypeSpec,
    UnknownContractTypeError,
    register_contract_type,
)


@pytest.fixture
def registry_state():
    """Сохраняет и восстанавливает глобальное состояние реестра."""
    specs = dict(ContractTypeRegistry._specs)
    failures = dict(ContractTypeRegistry._failures)
    frozen = ContractTypeRegistry._frozen
    yield
    ContractTypeRegistry._specs.clear()
    ContractTypeRegistry._specs.update(specs)
    ContractTypeRegistry._failures.clear()
    ContractTypeRegistry._failures.update(failures)
    ContractTypeRegistry._frozen = frozen


def _stub_validator_class():
    class StubValidator(BaseValidator):
        CONTRACT_TYPE = "stub"

        def check_specific(self, cd, report) -> None:
            return None

    return StubValidator


def _stub_generator_class():
    class StubGenerator(BaseContractGenerator):
        CONTRACT_TYPE = "stub"

        def _get_template_path(self, carrier_type: str) -> str:
            return ""

        def _build_replacements_map(self, data: Any) -> Dict[str, str]:
            return {}

    return StubGenerator


# ─────────────────────────────────────────────────────────────
# Регистрация
# ─────────────────────────────────────────────────────────────

def test_register_and_find(registry_state):
    spec = register_contract_type(
        "test_type_xyz",
        "Тестовый тип",
        generator_class=_stub_generator_class(),
        validator_class=_stub_validator_class(),
    )
    assert ContractTypeRegistry.find("test_type_xyz") is spec
    assert "test_type_xyz" in ContractTypeRegistry.known_types()
    assert ContractTypeRegistry.titles()["test_type_xyz"] == "Тестовый тип"


def test_register_duplicate_raises(registry_state):
    register_contract_type(
        "test_dup_xyz", "Первый",
        generator_class=_stub_generator_class(),
    )
    with pytest.raises(ContractTypeError):
        register_contract_type(
            "test_dup_xyz", "Второй",
            generator_class=_stub_generator_class(),
        )


def test_register_without_title_raises(registry_state):
    with pytest.raises(ContractTypeError):
        ContractTypeRegistry.register(ContractTypeSpec("test_notitle", ""))


def test_register_after_freeze_is_ignored(registry_state, caplog):
    ContractTypeRegistry.freeze()
    with caplog.at_level(logging.WARNING, logger="core.contracts.registry"):
        register_contract_type(
            "test_frozen_xyz", "После заморозки",
            generator_class=_stub_generator_class(),
        )
    assert "test_frozen_xyz" not in ContractTypeRegistry.known_types()
    assert "заморожен" in caplog.text


# ─────────────────────────────────────────────────────────────
# Разрешение ключей
# ─────────────────────────────────────────────────────────────

def test_resolve_key_none_returns_default(registry_state):
    assert ContractTypeRegistry.resolve_key(None) == DEFAULT_CONTRACT_TYPE.value


def test_resolve_key_unknown_fallback_with_warning(registry_state, caplog):
    ContractTypeRegistry.load_builtin()  # perevozka уже может быть в реестре
    with caplog.at_level(logging.WARNING, logger="core.contracts.registry"):
        key = ContractTypeRegistry.resolve_key("неизвестный_тип")
    assert key == DEFAULT_CONTRACT_TYPE.value
    assert "fallback" in caplog.text


def test_resolve_key_unknown_strict_raises(registry_state):
    ContractTypeRegistry.load_builtin()
    with pytest.raises(UnknownContractTypeError):
        ContractTypeRegistry.resolve_key("неизвестный_тип", strict=True)


def test_get_returns_spec_of_default(registry_state):
    ContractTypeRegistry.load_builtin()
    spec = ContractTypeRegistry.get()
    assert spec.contract_type == "perevozka"
    assert spec.generator_class is PerevozkaGenerator
    assert spec.validator_class is PerevozkaValidator


# ─────────────────────────────────────────────────────────────
# Ленивая загрузка и изоляция импорта
# ─────────────────────────────────────────────────────────────

def test_load_builtin_registers_perevozka(registry_state):
    assert "core.contracts.perevozka" in BUILTIN_TYPE_MODULES
    # Регистрация происходит при (первом) импорте пакета perevozka;
    # load_builtin гарантирует этот импорт. Сам тестовый модуль уже
    # импортировал PerevozkaGenerator, поэтому тип в реестре есть.
    ContractTypeRegistry.load_builtin()
    assert "perevozka" in ContractTypeRegistry.known_types()


def test_import_failure_isolated(registry_state, monkeypatch):
    """
    Падение модуля одного типа не мешает загрузке остальных:
    сломанный модуль попадает в failures(), известные типы грузятся.
    """
    monkeypatch.setattr(
        registry_module,
        "BUILTIN_TYPE_MODULES",
        ("core.contracts.perevozka", "core.contracts.no_such_module_xyz"),
    )

    ContractTypeRegistry.load_builtin()

    assert "perevozka" in ContractTypeRegistry.known_types()
    assert "core.contracts.no_such_module_xyz" in ContractTypeRegistry.failures()


def test_load_builtin_is_idempotent(registry_state):
    ContractTypeRegistry.load_builtin()
    ContractTypeRegistry.load_builtin()
    known = ContractTypeRegistry.known_types()
    assert known.count("perevozka") == 1


# ─────────────────────────────────────────────────────────────
# Фабрика
# ─────────────────────────────────────────────────────────────

def test_factory_default_type_is_perevozka(registry_state):
    ContractTypeRegistry.load_builtin()
    generator = GeneratorFactory.get_generator()
    assert isinstance(generator, PerevozkaGenerator)


def test_factory_unknown_fallback_and_strict(registry_state):
    ContractTypeRegistry.load_builtin()
    fallback = GeneratorFactory.get_generator("неизвестный_тип")
    assert isinstance(fallback, PerevozkaGenerator)

    with pytest.raises(UnknownContractTypeError):
        GeneratorFactory.get_generator("неизвестный_тип", strict=True)


def test_factory_returns_new_instances(registry_state):
    ContractTypeRegistry.load_builtin()
    first = GeneratorFactory.get_generator()
    second = GeneratorFactory.get_generator()
    assert first is not second


def test_factory_default_templates_dir(registry_state):
    ContractTypeRegistry.load_builtin()
    generator = GeneratorFactory.get_generator()
    assert generator.templates_dir == str(TEMPLATES_DIR)


def test_factory_custom_templates_dir(registry_state):
    ContractTypeRegistry.load_builtin()
    generator = GeneratorFactory.get_generator(templates_dir="мой/путь")
    assert generator.templates_dir == "мой/путь"


def test_factory_validator(registry_state):
    ContractTypeRegistry.load_builtin()
    validator = GeneratorFactory.get_validator("perevozka")
    assert isinstance(validator, PerevozkaValidator)
    with pytest.raises(UnknownContractTypeError):
        GeneratorFactory.get_validator("неизвестный_тип", strict=True)


def test_factory_spec_with_templates_subdir(registry_state, tmp_path):
    register_contract_type(
        "test_subdir_xyz", "С подпапкой шаблонов",
        generator_class=_stub_generator_class(),
        validator_class=_stub_validator_class(),
        templates_subdir="test_subdir",
    )
    generator = GeneratorFactory.get_generator("test_subdir_xyz")
    assert generator.templates_dir == str(
        PROJECT_ROOT / "templates" / "test_subdir"
    )
