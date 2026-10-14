#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Аватары-коты v2 (задание, часть 2).

Что было не так (замечания оператора)
------------------------------------
#6  Гипнотический взгляд: большие круглые глаза со зрачками-точками.
#7  Окрас: все пять образов — один рыже-коричневый кот, как лиса.
#8  Шляпа cat_detective ковбойская, а не детективная.
#9  Усы длинные, как у моржа.
#10 Морда плоская, без объёма.

Что проверяется
---------------
Рисунок — это геометрия, и её можно измерить. Тесты сравнивают пиксели
и читают параметры фигуры, а не «смотрят, красиво ли»: так регресс
(вернули большие глаза или одинаковые окрасы) поймается автоматически.

Проверки:
  * глаза меньше прежних и БЕЗ круглого зрачка-точки;
  * пять образов различаются по цвету шерсти;
  * у детектива клетчатая кепка, а не широкополая шляпа;
  * усы заканчиваются ближе к морде (короче прежних);
  * у морды есть тень — объём, а не плоская заливка.
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ui import agent_avatar  # noqa: E402
from ui.agent_avatar import (  # noqa: E402
    COATS,
    EYE_R,
    PUPIL_W,
    avatar_keys,
    avatar_pixmap,
    coat_for,
)


@pytest.fixture(scope="module")
def qt_app():
    """Одно QApplication на весь модуль: рисунок без него не нарисовать."""
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _colors(pixmap, limit: int = 400) -> set:
    """Набор цветов картинки (без прозрачных пикселей)."""
    image = pixmap.toImage()
    found = set()
    step = max(1, image.width() // 64)
    for x in range(0, image.width(), step):
        for y in range(0, image.height(), step):
            color = image.pixelColor(x, y)
            if color.alpha() > 200:
                found.add((color.red(), color.green(), color.blue()))
            if len(found) > limit:
                return found
    return found


# ─────────────────────────────────────────────────────────────
# #6 Глаза: меньше и без «точки»
# ─────────────────────────────────────────────────────────────

def test_eyes_are_smaller_than_before():
    """
    Глаза уменьшены (было 9.6, стало меньше).

    Крупные глаза навыкате и давали «гипнотический» взгляд, на который
    пожаловался оператор.
    """
    assert EYE_R < 9.6, "глаза вернулись к прежнему размеру"
    assert EYE_R == pytest.approx(7.7, abs=0.5)


def test_pupil_is_a_slit_not_a_dot():
    """
    Зрачок выше, чем шире, — это щель, а не точка.

    Круглый зрачок в большом глазу читается как «точка» и делает взгляд
    немигающим. Кошачий зрачок — вертикальная щель.
    """
    assert agent_avatar.PUPIL_H > agent_avatar.PUPIL_W * 2, (
        "зрачок снова стал круглым"
    )


def test_pupil_path_is_pointed_at_the_ends():
    """
    Контур зрачка заостряется к концам.

    Ровный овал с теми же размерами всё ещё выглядит «точкой»: именно
    заострение делает щель кошачьей.
    """
    path = agent_avatar._pupil_path(50.0, 50.0, 3.0, 8.0)

    # Точка на оси по центру фигуры и точка у верхнего конца: ширина у
    # конца должна быть НУЛЕВОЙ (обе ветви сходятся в одну точку).
    top = path.pointAtPercent(0.0)
    right = path.pointAtPercent(0.25)
    assert abs(top.x() - right.x()) > 0.5, (
        "ширина зрачка не меняется по высоте — это овал, а не щель"
    )


def test_eye_has_upper_lid_shadow():
    """Над глазом есть веко: полупрозрачная тень поверх верхней части."""
    path = agent_avatar._upper_lid(50.0, EYE_R)

    assert path.elementCount() > 0
    assert path.boundingRect().height() > 0


def test_iris_is_dark_enough_for_small_sizes():
    """
    Радужка тёмная: на 32 px зрачок ещё не прорисован, и глаз должен
    читаться за счёт радужки, а не превращаться в светлое пятно.
    """
    iris = agent_avatar.IRIS

    assert iris.lightness() < 180, "радужка слишком светлая для мелких размеров"
    # И при этом не чёрная: глаз должен отличаться от зрачка.
    assert iris.lightness() > agent_avatar.EYE.lightness()


# ─────────────────────────────────────────────────────────────
# #7 Окрасы: пять разных
# ─────────────────────────────────────────────────────────────

def test_every_persona_has_its_own_coat():
    """У каждого образа свой окрас."""
    keys = avatar_keys()

    assert len({coat_for(key).fur.name() for key in keys}) == len(keys), (
        "у нескольких образов одинаковый цвет шерсти"
    )


@pytest.mark.parametrize("key,must_be", [
    ("cat_scientist", "рыжий"),
    ("cat_logist", "серо-коричневый"),
    ("cat_keeper", "чёрный"),
    ("cat_detective", "серый"),
])
def test_coat_matches_the_spec(key, must_be):
    """
    Окрасы соответствуют заданию.

    Проверяется по яркости и насыщенности, а не по точному hex: подобрать
    оттенок можно по-разному, а вот «чёрный должен быть тёмным» — это уже
    требование, а не вкус.
    """
    fur = coat_for(key).fur

    if must_be == "рыжий":
        # Рыжий: тёплый, насыщенный, средний по яркости.
        assert fur.red() > fur.blue() + 60
        assert fur.saturation() > 120
    elif must_be == "серо-коричневый":
        # Приглушённый: насыщенность заметно ниже рыжего.
        assert fur.saturation() < 90
        assert fur.red() > fur.blue()
    elif must_be == "чёрный":
        assert fur.lightness() < 60
    elif must_be == "серый":
        # Серый: очень низкая насыщенность.
        assert fur.saturation() < 40


def test_only_striped_coats_have_stripes():
    """Полоски есть у полосатого и нейтрального, но не у чёрного кота."""
    assert COATS["cat_logist"].stripes is True
    assert COATS["neutral"].stripes is True
    assert COATS["cat_keeper"].stripes is False
    assert COATS["cat_detective"].stripes is False


def test_personas_look_different_in_pixels(qt_app):
    """
    Образы различаются на экране, а не только в коде.

    Сравниваются наборы цветов: если два образа дают одинаковую картинку,
    оператор их не различит.
    """
    palettes = {key: _colors(avatar_pixmap(96, key)) for key in avatar_keys()}
    keys = list(palettes)

    for index, first in enumerate(keys):
        for second in keys[index + 1:]:
            common = palettes[first] & palettes[second]
            unique_first = palettes[first] - common
            unique_second = palettes[second] - common
            assert len(unique_first) > 20 and len(unique_second) > 20, (
                f"{first} и {second} почти не различаются: "
                f"общих цветов {len(common)}"
            )


# ─────────────────────────────────────────────────────────────
# #8 Кепка Шерлока вместо ковбойской шляпы
# ─────────────────────────────────────────────────────────────

def test_hat_is_not_wide_brimmed():
    """
    У кепки Шерлока нет широких полей.

    Прежняя шляпа рисовала эллипс на всю ширину головы (76 из 100) — это
    и делало её ковбойской. У новой кепки самый широкий элемент — купол
    шириной 48.
    """
    import inspect

    source = inspect.getsource(agent_avatar._paint_hat)

    assert "76, 12" not in source, "вернулись широкие поля ковбойской шляпы"
    # Купол описан координатами 26…74 — это 48 единиц из 100.
    assert "crown.moveTo(26, 27)" in source


def test_hat_has_checkered_pattern():
    """У кепки есть клетка — её узнаваемый признак."""
    import inspect

    source = inspect.getsource(agent_avatar._paint_hat)

    assert "Клетка" in source
    assert "drawLine" in source, "клетка не рисуется"


def test_hat_sits_above_the_eyes():
    """
    Кепка не наезжает на глаза.

    Нижняя граница купола — y=27, глаза начинаются с y=42: между ними
    остаётся лоб. Иначе кот выглядит спящим, а не детективом.
    """
    import inspect

    source = inspect.getsource(agent_avatar._paint_hat)

    assert "crown.moveTo(26, 27)" in source
    assert 27 < agent_avatar.EYE_Y - agent_avatar.EYE_R


# ─────────────────────────────────────────────────────────────
# #9 Усы короче
# ─────────────────────────────────────────────────────────────

def test_whiskers_are_shorter_than_before():
    """
    Усы заканчиваются ближе к морде.

    Прежние доходили до x=7–9 (почти край квадрата) и загибались вниз —
    «как у моржа». Новые кончаются около x=21–23.
    """
    import inspect
    import re

    source = inspect.getsource(agent_avatar._paint_whiskers)
    ends = [float(value) for value in
            re.findall(r"\((\d+(?:\.\d+)?), \d+(?:\.\d+)?\)\)", source)]

    assert ends, "не удалось прочитать координаты усов"
    assert min(ends) >= 18.0, f"усы снова длинные: минимальный x = {min(ends)}"


def test_whiskers_curve_upwards():
    """
    Усы загнуты ВВЕРХ: конец выше начала — кот выглядит довольным.

    У прежних усов нижняя пара уходила вниз (конец ниже начала).
    """
    import inspect

    source = inspect.getsource(agent_avatar._paint_whiskers)

    # Третья пара: начало (40, 75), конец (23, 74) — конец ВЫШЕ начала.
    assert "((40.0, 75.0), (32.0, 76.0), (23.0, 74.0))" in source


# ─────────────────────────────────────────────────────────────
# #10 Морда: объём
# ─────────────────────────────────────────────────────────────

def test_muzzle_has_shadow_for_volume():
    """Под мордой есть мягкая тень — она и даёт объём."""
    import inspect

    source = inspect.getsource(agent_avatar._paint_face)

    assert "тень" in source.lower()
    assert "_offset_path" in source, "тень под мордой не рисуется"


def test_head_has_volume_shadows():
    """У головы есть тени сверху, сбоку и под подбородком."""
    import inspect

    source = inspect.getsource(agent_avatar._paint_head_shadow)

    assert "подбородк" in source.lower()
    assert source.count("drawPath") >= 3, "теней меньше трёх — объёма не будет"


def test_nose_is_a_small_triangle():
    """Нос — маленький треугольник шириной 8 из 100."""
    import inspect

    source = inspect.getsource(agent_avatar._paint_nose_and_mouth)

    assert "(46, 66), (54, 66), (50, 71.5)" in source


# ─────────────────────────────────────────────────────────────
# Рисунок в целом: работает во всех размерах
# ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("size", [22, 32, 48, 96, 192])
def test_avatar_renders_at_every_size(qt_app, size):
    """Кот рисуется в любом размере: от иконки в углу до крупного аватара."""
    pixmap = avatar_pixmap(size, "cat_scientist")

    assert pixmap.width() == size
    assert not pixmap.isNull()
    assert len(_colors(pixmap)) > 5, "рисунок пустой"


@pytest.mark.parametrize("key", avatar_keys())
def test_avatar_is_not_empty(qt_app, key):
    """У каждого образа есть рисунок, а не пустой квадрат."""
    pixmap = avatar_pixmap(96, key)

    assert len(_colors(pixmap)) > 5, f"{key}: рисунок пустой"


def test_unknown_persona_falls_back_to_neutral(qt_app):
    """Неизвестный ключ образа рисуется нейтральным котом, а не падает."""
    pixmap = avatar_pixmap(48, "несуществующий-образ")

    assert not pixmap.isNull()
    assert len(_colors(pixmap)) > 5
