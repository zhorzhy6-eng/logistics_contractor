#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Образ «Без образа» (neutral) — СЕРО-ПОЛОСАТЫЙ (задача 6.1).

Проверка на живом коде показала: «нейтральный» окрас был ОРАНЖЕВЫМ
(``#E8A24A``) — то есть ровно тем, чем нейтральный образ быть не должен.
Полоски у него были, но рыжие, а лицо (радужка ``#B87A22``, нос ``#D96A78``,
усы ``#6B5334``, контур ``#4A3520``) бралось из ОБЩЕЙ палитры — цветным
выходило всё.

Что сделано (`ui/agent_avatar.py`):

  * окрас `neutral` — серый: шерсть ``#BCBCBC``, тень ``#7A7A7A``, морда
    ``#EDEDED``, полоски остаются (``stripes=True``);
  * у окраса появился признак ``mono`` — «вне цвета»;
  * лицо вынесено в `FacePalette`: общая цветная (`FACE_COLOR`) и серая
    (`FACE_MONO`), выбор — `face_palette(coat)`. Серые цвета лица нужны
    затем, что цвет — самый заметный признак образа, и «без образа» не
    должен читаться как чей-то характер;
  * все рисующие функции получают палитру параметром (у ушей, головы,
    морды, глаз, носа, усов, очков, лупы, кепки, жилета, планшета и лап) —
    цветных зашитых констант в рисовании не осталось.

Тесты идут на НАСТОЯЩЕМ рендере (offscreen QPainter) и смотрят на пиксели:
это единственная проверка, которая ловит «цвет вернулся» в любом месте
рисунка, а не только там, где о нём вспомнили.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt5.QtWidgets import QApplication

from core.agent.personas import PERSONAS

#: Размер картинки для проверки: мелкий образец даёт мало пикселей.
SIZE = 128


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(scope="module")
def avatar():
    """Модуль рисования (импорт после создания QApplication)."""
    import ui.agent_avatar as module

    return module


def image_of(avatar, key: str):
    """Картинка образа в оттенках, как её видит оператор."""
    return avatar.avatar_pixmap(SIZE, persona_key=key).toImage()


def colored_pixels(image, limit: int = 8):
    """
    Пиксели, у которых R, G и B РАЗНЫЕ (то есть цветные).

    Прозрачные пропускаются: фон не считается.
    """
    found = []
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 0:
                continue
            if not (color.red() == color.green() == color.blue()):
                found.append((x, y, color.red(), color.green(), color.blue()))
                if len(found) >= limit:
                    break
        if len(found) >= limit:
            break
    return found


def grey_levels(image):
    """Множество серых уровней (0–255) среди непрозрачных пикселей."""
    levels = set()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 0:
                continue
            levels.add(color.red())
    return levels


# ─────────────────────────────────────────────────────────────
# Главное: у neutral нет цветных пикселей
# ─────────────────────────────────────────────────────────────
def test_neutral_has_no_colored_pixels(qt_app, avatar):
    """
    «Без образа» — только серые, чёрные и белые пиксели.

    Это и есть проверка задачи: любой цветной пиксель означает, что в
    рисунок просочился цвет (шерсть, радужка, нос, усы, контур, очки).
    """
    image = image_of(avatar, "neutral")

    found = colored_pixels(image)

    assert found == [], f"у «Без образа» есть цветные пиксели: {found}"


def test_neutral_is_striped(qt_app, avatar):
    """
    Полоски у «Без образа» есть: серый не один, а несколько уровней.

    Проверяются два признака сразу: полоски включены в окрасе И уровни
    серого действительно разные (однотонная заливка дала бы один уровень).
    """
    coat = avatar.coat_for("neutral")
    image = image_of(avatar, "neutral")
    levels = grey_levels(image)

    assert coat.stripes is True, "у «Без образа» выключены полоски"
    assert coat.mono is True, "окрас «Без образа» не помечен как серый"
    assert len(levels) >= 3, (
        f"серых уровней всего {len(levels)} — рисунок однотонный: {sorted(levels)}"
    )
    # Тень (полоски и контур) темнее шерсти — иначе полосок не видно.
    assert coat.dark.red() < coat.fur.red() < coat.muzzle.red()


def test_neutral_palette_is_grey(avatar):
    """Все цвета окраса и лица «Без образа» — серые (R = G = B)."""
    coat = avatar.coat_for("neutral")
    face = avatar.face_palette(coat)

    for name, color in (
        ("шерсть", coat.fur), ("тень", coat.dark), ("морда", coat.muzzle),
        ("контур", face.outline), ("зрачок", face.eye), ("радужка", face.iris),
        ("белок", face.sclera), ("нос", face.nose),
        ("раковина уха", face.inner_ear), ("усы", face.whisker),
        ("стёкла", face.glasses),
    ):
        assert color.red() == color.green() == color.blue(), (
            f"{name} не серый: {color.name()}"
        )


# ─────────────────────────────────────────────────────────────
# Остальные образы остались цветными
# ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", [
    "cat_scientist", "cat_logist", "cat_detective", "cat_keeper",
])
def test_other_personas_stay_colored(qt_app, avatar, key):
    """
    Прочие образы не обесцвечены.

    Иначе «серо-полосатый» сделали бы всем сразу — и образы перестали бы
    различаться между собой (а различает их как раз цвет).
    """
    image = image_of(avatar, key)

    assert colored_pixels(image), f"{key} потерял цвет"


def test_neutral_is_distinguishable_from_others(avatar):
    """Окрас «Без образа» отличается от окраса каждого другого образа."""
    neutral = avatar.coat_for("neutral")

    for key in ("cat_scientist", "cat_logist", "cat_detective", "cat_keeper"):
        coat = avatar.coat_for(key)
        assert coat.fur.name() != neutral.fur.name(), (
            f"«Без образа» совпал по цвету с {key}"
        )


# ─────────────────────────────────────────────────────────────
# Образ объявлен в personas и доступен по своему ключу
# ─────────────────────────────────────────────────────────────
def test_persona_key_and_title(avatar):
    """Ключ и подпись образа — как в core/agent/personas.py."""
    assert "neutral" in PERSONAS
    assert PERSONAS["neutral"].key == "neutral"
    assert PERSONAS["neutral"].title == "Без образа"


def test_all_personas_render(avatar):
    """Каждый объявленный образ рисуется — серый никого не сломал."""
    for key in PERSONAS:
        image = image_of(avatar, key)

        assert not image.isNull(), f"{key} не нарисовался"
        assert grey_levels(image), f"{key} пустой"
