#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Аватар Максимилиана: кот, нарисованный кодом (задание, часть D.2).

Почему рисунок, а не PNG/SVG-файлы: набор из пяти образов в трёх размерах —
это пятнадцать картинок, которые надо держать в репозитории и не забыть
перерисовать под тёмную тему. Кот здесь рисуется QPainter-ом: масштабируется
на любой размер (32 / 64 / 96 px и retina), не зависит от темы и одинаково
выглядит в сборке PyInstaller.

Образы (совпадают с core/agent/personas.py):

  * ``cat_scientist`` — очки и лупа;
  * ``cat_logist``    — жилет и планшет;
  * ``cat_detective`` — шляпа и лупа;
  * ``cat_keeper``    — спит (глаза закрыты);
  * ``neutral``       — просто кот, без атрибутов.

Как нарисован кот
-----------------
Голова — не круг: она ШИРЕ, чем выше, со скулами по бокам и чуть
заострённым подбородком (одна замкнутая кривая — без внутренних швов).
Уши — острые треугольники торчком, с розовой внутренней частью, кончики
видны НАД головой. Нос — маленький треугольник, ниже него короткая
вертикальная чёрточка и кошачий рот «ω». Усы — по три с каждой стороны,
тонкие и чуть изогнутые. Глаза — большие, круглые, с вертикальным
зрачком-щелью; у учёного и детектива поверх них очки.

Толщина линий адаптивная: на 32 px перо не может быть тоньше одной точки
экрана, иначе рисунок рассыпается в кашу (см. ``_pen`` и MIN_DEVICE_PX).

Анимация: ``CatAvatar`` мигает раз в 5–10 секунд и умеет «думать» —
во время запроса к GigaChat глаза широко открыты. Таймеры останавливаются
при скрытии виджета: анимация не должна крутиться в фоне.
"""

import logging
import random
from typing import Optional, Sequence, Tuple

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt5.QtWidgets import QWidget

from core.agent.personas import DEFAULT_PERSONA

logger = logging.getLogger("ui.agent_avatar")

#: Цвета кота. Фиксированные (не из темы): кот — персонаж, а не элемент
#: оформления. Контур тёмный, поэтому он читается и на светлом, и на
#: тёмном фоне.
FUR = QColor("#E8A24A")
FUR_DARK = QColor("#C97F2C")
MUZZLE = QColor("#FBEEDC")
EYE = QColor("#2B2118")
#: Радужка заметно темнее прежней (#F2B33D → #B87A22). Причина в размере
#: 32–48 px: на нём зрачок-щель шириной 2.6 единицы занимает меньше точки
#: экрана, и на светлой радужке глаз выглядел пустым — тот самый
#: «гипнотический» вид. Тёмная радужка даёт глазу опору даже тогда, когда
#: щель ещё не прорисовалась.
IRIS = QColor("#B87A22")
OUTLINE = QColor("#4A3520")
WHISKER = QColor("#6B5334")
NOSE = QColor("#D96A78")
GLASS = QColor("#7FD3F7")
METAL = QColor("#B9C4D2")
VEST = QColor("#3F7FBF")
TABLET = QColor("#DCE6F2")
HAT = QColor("#6E6250")
HAT_BAND = QColor("#8A7C64")


class Coat:
    """
    Окрас кота: шерсть, тень, морда и отметины.

    Зачем отдельный тип, а не три константы. Раньше все пять образов были
    ОДНИМ рыжим котом с разными атрибутами (очки, шляпа, жилет), и в
    маленьком размере они не различались вовсе. Окрас — самый заметный
    признак: его видно и на 32 px, где очков и шляпы уже не разобрать.

    :param fur: основной цвет шерсти;
    :param dark: цвет тени (полоски, контур морды);
    :param muzzle: цвет морды (у тёмных окрасов она заметно светлее);
    :param stripes: рисовать ли полоски на лбу и щеках;
    :param blaze: рисовать ли белую отметину от лба к носу
        («рыжий с белой мордой» — так задан образ учёного кота).
    """

    __slots__ = ("fur", "dark", "muzzle", "stripes", "blaze", "mono")

    def __init__(self, fur: str, dark: str, muzzle: str,
                 stripes: bool = False, blaze: bool = False,
                 mono: bool = False):
        self.fur = QColor(fur)
        self.dark = QColor(dark)
        self.muzzle = QColor(muzzle)
        self.stripes = bool(stripes)
        self.blaze = bool(blaze)
        #: Окрас вне цвета: ни один пиксель не выходит за серую шкалу.
        #: Нужно образу «Без образа»: он не должен читаться как чей-то
        #: характер, а цвет — самый заметный признак (см. `COATS`).
        self.mono = bool(mono)


class FacePalette:
    """
    Цвета лица, не зависящие от окраса: белок, радужка, зрачок, нос, усы.

    Вынесены в объект, потому что серо-полосатому образу нужны свои — серые:
    с общей палитрой «Без образа» получался бы с рыжей радужкой, розовым
    носом и коричневым контуром, то есть цветным.

    :param outline: контур головы, ушей, морды;
    :param eye: зрачок;
    :param iris: радужка;
    :param sclera: белок глаза;
    :param nose: нос;
    :param inner_ear: раковина уха;
    :param whisker: усы;
    :param glasses: линзы очков (у «нейтрального» их нет, но палитра полная).
    """

    __slots__ = ("outline", "eye", "iris", "sclera", "nose", "inner_ear",
                 "whisker", "glasses")

    def __init__(self, outline: QColor, eye: QColor, iris: QColor,
                 sclera: QColor, nose: QColor, inner_ear: QColor,
                 whisker: QColor, glasses: QColor):
        self.outline = outline
        self.eye = eye
        self.iris = iris
        self.sclera = sclera
        self.nose = nose
        self.inner_ear = inner_ear
        self.whisker = whisker
        self.glasses = glasses


#: Цветная палитра лица — как было у всех образов.
FACE_COLOR = FacePalette(
    outline=OUTLINE, eye=EYE, iris=IRIS, sclera=QColor("#FFF8EC"),
    nose=NOSE, inner_ear=NOSE.lighter(135), whisker=WHISKER, glasses=GLASS,
)

#: Серая палитра лица — для серо-полосатого образа «Без образа».
#: Все цвета с R = G = B, поэтому проверка «нет цветных пикселей» проходит.
FACE_MONO = FacePalette(
    outline=QColor("#4A4A4A"), eye=QColor("#1F1F1F"), iris=QColor("#8A8A8A"),
    sclera=QColor("#F5F5F5"), nose=QColor("#5E5E5E"),
    inner_ear=QColor("#C9C9C9"), whisker=QColor("#6B6B6B"),
    glasses=QColor("#9E9E9E"),
)


def face_palette(coat: Coat) -> FacePalette:
    """Палитра лица для окраса: серая для «вне цвета», цветная для остальных."""
    return FACE_MONO if coat.mono else FACE_COLOR


#: Окрасы по образам (задание, часть 2, B.1).
#:
#:   * cat_scientist — рыжий с белой мордой (краснее «нейтрального», чтобы
#:     образы различались не только очками);
#:   * cat_logist    — полосатый серо-коричневый;
#:   * cat_keeper    — чёрный с белой грудью (спит, глаза закрыты);
#:   * cat_detective — серый британец;
#:   * neutral       — СЕРО-ПОЛОСАТЫЙ: ни один цвет не выходит за серую
#:     шкалу (R = G = B), поэтому «без образа» ни с одним образом не
#:     спутать. Полоски есть, но они серые — образ остаётся безликим.
COATS = {
    "cat_scientist": Coat("#D2822F", "#9C5A18", "#FFF6E8", blaze=True),
    "cat_logist": Coat("#A9927A", "#7C6449", "#E6DCCC", stripes=True),
    "cat_keeper": Coat("#2E2A28", "#151312", "#F3EFE7"),
    "cat_detective": Coat("#9AA3AC", "#6E777F", "#E4E9ED"),
    "neutral": Coat("#BCBCBC", "#7A7A7A", "#EDEDED", stripes=True, mono=True),
}


def coat_for(persona_key: str) -> Coat:
    """Окрас образа (у неизвестного — нейтральный)."""
    return COATS.get(_norm(persona_key), COATS["neutral"])

#: Толщина линии, ниже которой рисунок рассыпается: одна точка экрана.
MIN_DEVICE_PX = 0.9
#: Толщина контура головы и ушей в системе 100×100.
CONTOUR = 2.0

#: Границы паузы между миганиями (задание, часть D.1.3: раз в 5–10 секунд).
BLINK_MIN_MS = 5000
BLINK_MAX_MS = 10000
#: Сколько держится закрытый глаз.
BLINK_HOLD_MS = 130

#: Геометрия глаз: смещение от центра и высота (в системе 100×100).
#: Глаза уменьшены на 20 % (задание, часть 2, A.1): прежние 9.6 при
#: расстоянии 15 смотрелись как два блюдца, и морда выглядела «гипнотической».
EYE_DX = 15.0
EYE_Y = 50.0
EYE_R = 7.7
EYE_R_WIDE = 8.4
IRIS_R = 5.6
#: Зрачок-щель: вертикальный, не круглый. Сужается к концам (см. _pupil_path):
#: ровный овал читается как «точка», а именно он и давал гипнотический взгляд.
#: Ширина 3.0 — компромисс: на 192 px щель видна отчётливо, на 32 px
#: превращается в тёмное пятно в центре радужки, а не исчезает.
PUPIL_W = 3.0
PUPIL_H = 8.4
#: Радиус линзы очков.
LENS_R = 12.4


def avatar_keys() -> tuple:
    """Ключи образов, у которых есть свой рисунок (для настроек)."""
    return ("cat_scientist", "cat_logist", "cat_detective", "cat_keeper", "neutral")


def _norm(key: str) -> str:
    """Ключ образа; неизвестный — образ по умолчанию."""
    value = str(key or "").strip()
    return value if value in avatar_keys() else DEFAULT_PERSONA


def _pen(color: QColor, width: float, unit: float,
         min_px: float = MIN_DEVICE_PX) -> QPen:
    """
    Перо с толщиной в системе 100×100, но не тоньше ``min_px`` точки экрана.

    ``unit`` — сколько точек экрана приходится на одну единицу рисунка.
    На 96 px контур 2.0 и так виден, а на 32 px те же 2.0 дают 0.64 точки —
    линия рвётся. Поэтому толщина поднимается до минимума: усы и контуры
    остаются сплошными в любом размере.
    """
    if unit <= 0:
        return QPen(color, width)
    return QPen(color, max(float(width), min_px / unit))


def _path(points: Sequence[Tuple[float, float]], close: bool = True) -> QPainterPath:
    """Замкнутая ломаная по точкам (уши, нос, «z» и прочая геометрия)."""
    path = QPainterPath()
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    if close:
        path.closeSubpath()
    return path


def _shrink(points: Sequence[Tuple[float, float]],
            factor: float) -> Tuple[Tuple[float, float], ...]:
    """Тот же треугольник, стянутый к своему центру (внутренняя часть уха)."""
    count = len(points)
    cx = sum(point[0] for point in points) / count
    cy = sum(point[1] for point in points) / count
    return tuple(
        (cx + (x - cx) * factor, cy + (y - cy) * factor) for x, y in points
    )


#: Уши: (внешний низ, кончик, внутренний низ) для левого уха. Правое — зеркало.
LEFT_EAR = ((16.0, 42.0), (24.0, 6.0), (46.0, 30.0))


def _mirror(points: Sequence[Tuple[float, float]]) -> Tuple[Tuple[float, float], ...]:
    """Зеркалит точки по вертикальной оси рисунка (x → 100 − x)."""
    return tuple((100.0 - x, y) for x, y in points)


def paint_cat(
    painter: QPainter,
    size: float,
    persona_key: str = DEFAULT_PERSONA,
    eyes: str = "open",
    rect: Optional[QRectF] = None,
) -> None:
    """
    Рисует кота в квадрате ``rect`` (по умолчанию — квадрат ``size``).

    :param eyes: «open» — глаза открыты, «closed» — закрыты (мигание или
        спящий хранитель), «wide» — широко открыты (кот думает).
    """
    rect = rect or QRectF(0, 0, size, size)
    unit = min(rect.width(), rect.height()) / 100.0
    persona = _norm(persona_key)
    coat = coat_for(persona)

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.translate(rect.left(), rect.top())
    # Работаем в системе координат 100×100: все числа ниже — «проценты».
    painter.scale(unit, unit)

    if persona == "cat_keeper":
        eyes = "closed"

    # Порядок важен: туловище — позади головы, шляпа и очки — поверх морды.
    face = face_palette(coat)
    if persona == "cat_logist":
        _paint_shoulders(painter, unit, coat, face)
    _paint_ears(painter, unit, coat, face)
    _paint_head(painter, unit, coat, face)
    _paint_face(painter, unit, eyes, coat, face)

    if persona == "cat_scientist":
        _paint_glasses(painter, unit, face)
        _paint_magnifier(painter, unit, face)
    elif persona == "cat_detective":
        _paint_hat(painter, unit, face)
        _paint_magnifier(painter, unit, face)
    elif persona == "cat_logist":
        _paint_vest(painter, unit, face)
        _paint_tablet(painter, unit, face)
    elif persona == "cat_keeper":
        _paint_paws_and_z(painter, unit, face)

    painter.restore()


def _paint_ears(painter: QPainter, unit: float, coat: Coat,
                face: FacePalette) -> None:
    """Уши: острые треугольники торчком, внутри — «раковина» телесного цвета."""
    painter.setBrush(QBrush(coat.fur))
    painter.setPen(_pen(face.outline, CONTOUR, unit))
    for sign in (1, -1):
        outer = LEFT_EAR if sign > 0 else _mirror(LEFT_EAR)
        inner = _shrink(outer, 0.45)
        painter.drawPath(_path(outer))
        painter.setBrush(QBrush(face.inner_ear))
        painter.setPen(Qt.NoPen)
        painter.drawPath(_path(inner))
        painter.setBrush(QBrush(coat.fur))
        painter.setPen(_pen(face.outline, CONTOUR, unit))


def _paint_head(painter: QPainter, unit: float, coat: Coat,
                face: FacePalette) -> None:
    """
    Голова: шире, чем выше, со скулами и чуть заострённым подбородком.

    Одна замкнутая кривая — если рисовать эллипс и подбородок отдельно,
    на морде останется видимый шов. Полоски на лбу рисуются ПОСЛЕ головы,
    но ДО морды: они часть шерсти, а не отдельный предмет.
    """
    head = QPainterPath()
    head.moveTo(14, 52)
    head.cubicTo(14, 33, 26, 27, 38, 28)     # левая скула → верх головы
    head.cubicTo(43, 26, 57, 26, 62, 28)
    head.cubicTo(74, 27, 86, 33, 86, 52)     # верх → правая скула
    head.cubicTo(86, 72, 68, 82, 50, 88)     # скула → подбородок
    head.cubicTo(32, 82, 14, 72, 14, 52)     # подбородок → левая скула
    head.closeSubpath()

    painter.setBrush(QBrush(coat.fur))
    painter.setPen(_pen(face.outline, CONTOUR, unit))
    painter.drawPath(head)

    _paint_head_shadow(painter, unit, coat)
    if coat.stripes:
        _paint_stripes(painter, unit, coat)


def _paint_head_shadow(painter: QPainter, unit: float, coat: Coat) -> None:
    """
    Объём головы: мягкая тень сверху и по бокам.

    Без неё морда выглядит плоской аппликацией (проблема #10). Тень —
    несколько полупрозрачных дуг по краю головы, а не сплошная заливка:
    сплошная превратила бы кота в пятно.
    """
    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(QColor(0, 0, 0, 26), 5.0, unit, min_px=1.0))
    top = QPainterPath()
    top.moveTo(20, 46)
    top.cubicTo(22, 31, 34, 28, 44, 28)
    painter.drawPath(top)

    painter.setPen(_pen(QColor(0, 0, 0, 20), 4.5, unit, min_px=1.0))
    side = QPainterPath()
    side.moveTo(84, 44)
    side.cubicTo(86, 62, 76, 76, 66, 82)
    painter.drawPath(side)

    # Тень под подбородком: отделяет голову от шеи и добавляет объём.
    painter.setPen(_pen(QColor(0, 0, 0, 30), 4.0, unit, min_px=1.0))
    chin = QPainterPath()
    chin.moveTo(38, 84)
    chin.quadTo(50, 90, 62, 84)
    painter.drawPath(chin)

    # Тень от шерсти на светлой морде почти не видна, но у тёмных окрасов
    # (чёрный хранитель) именно она отделяет морду от головы.
    painter.setPen(_pen(coat.dark, 1.6, unit, min_px=0.9))
    muzzle_edge = QPainterPath()
    muzzle_edge.moveTo(31, 66)
    muzzle_edge.cubicTo(34, 58, 42, 56, 50, 56)
    muzzle_edge.cubicTo(58, 56, 66, 58, 69, 66)
    painter.drawPath(muzzle_edge)


#: Полоски на лбу: (начало, конец) в системе 100×100. Симметричны
#: относительно центра (x=50).
FOREHEAD_STRIPES = (
    ((44.0, 36.0), (40.0, 30.5)),
    ((50.0, 34.5), (50.0, 28.5)),
    ((56.0, 36.0), (60.0, 30.5)),
    ((38.0, 40.0), (32.0, 36.0)),
    ((62.0, 40.0), (68.0, 36.0)),
)


def _paint_stripes(painter: QPainter, unit: float, coat: Coat) -> None:
    """
    Полоски на лбу и щеках — признак полосатого окраса.

    Рисуются тенью окраса: у рыжего они темнее рыжего, у серо-коричневого
    — темнее серо-коричневого. Отдельный «цвет полосок» не нужен, поэтому
    окрас остаётся одним объектом.
    """
    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(coat.dark, 2.2, unit, min_px=1.0))
    for start, end in FOREHEAD_STRIPES:
        painter.drawLine(QPointF(*start), QPointF(*end))


def _paint_face(painter: QPainter, unit: float, eyes: str, coat: Coat,
                face: FacePalette) -> None:
    """Светлая морда, нос-треугольник, рот «ω», усы и глаза."""
    # Отметина на лбу — часть окраса, поэтому идёт под мордой.
    if coat.blaze:
        _paint_blaze(painter, unit, coat)

    # ── Морда: светлое пятно шире, чем выше, с подбородком ──
    # Контур не линией, а мягкой тенью (проблема #10, E.3): линия делила
    # морду на «наклейку» и голову, тень же читается как объём.
    muzzle = QPainterPath()
    muzzle.moveTo(31, 69)
    muzzle.cubicTo(31, 61, 40, 59, 50, 59)
    muzzle.cubicTo(60, 59, 69, 61, 69, 69)
    muzzle.cubicTo(69, 79, 58, 85, 50, 87)
    muzzle.cubicTo(42, 85, 31, 79, 31, 69)
    muzzle.closeSubpath()
    painter.setBrush(QBrush(QColor(0, 0, 0, 22)))
    painter.setPen(Qt.NoPen)
    painter.drawPath(_offset_path(muzzle, 0.0, 1.6))
    painter.setBrush(QBrush(coat.muzzle))
    painter.drawPath(muzzle)

    _paint_eyes(painter, unit, eyes, face)
    _paint_nose_and_mouth(painter, unit, face)
    _paint_whiskers(painter, unit, face)


def _paint_blaze(painter: QPainter, unit: float, coat: Coat) -> None:
    """
    Белая отметина от лба к носу — «рыжий с белой мордой».

    Рисуется ДО морды, чтобы светлое пятно морды осталось сверху: отметина
    продолжает его вверх по лбу и связывает морду с головой.
    """
    blaze = QPainterPath()
    blaze.moveTo(50, 30)
    blaze.cubicTo(45, 38, 44, 50, 45.5, 60)
    blaze.cubicTo(48, 63, 52, 63, 54.5, 60)
    blaze.cubicTo(56, 50, 55, 38, 50, 30)
    blaze.closeSubpath()
    painter.setBrush(QBrush(coat.muzzle))
    painter.setPen(Qt.NoPen)
    painter.drawPath(blaze)


def _offset_path(path: QPainterPath, dx: float, dy: float) -> QPainterPath:
    """Копия пути, сдвинутая на (dx, dy) — для мягкой тени под фигурой."""
    moved = QPainterPath(path)
    moved.translate(dx, dy)
    return moved


def _pupil_path(cx: float, cy: float, width: float, height: float) -> QPainterPath:
    """
    Зрачок-щель: вертикальный, заострённый к концам.

    Ровный овал с теми же размерами читается как «точка» и вместе с
    крупными глазами давал гипнотический взгляд (проблема #6). Заострение
    к концам превращает его в кошачью щель — взгляд становится спокойным.
    """
    half_w = width / 2.0
    half_h = height / 2.0
    path = QPainterPath()
    path.moveTo(cx, cy - half_h)
    path.cubicTo(cx + half_w, cy - half_h * 0.45,
                 cx + half_w, cy + half_h * 0.45,
                 cx, cy + half_h)
    path.cubicTo(cx - half_w, cy + half_h * 0.45,
                 cx - half_w, cy - half_h * 0.45,
                 cx, cy - half_h)
    path.closeSubpath()
    return path


def _paint_eyes(painter: QPainter, unit: float, eyes: str,
                face: FacePalette) -> None:
    """
    Глаза: прищуренные, с вертикальным зрачком-щелью и веком.

    Что изменилось против первой версии (проблемы #6): глаза меньше на
    20 %, зрачок заострён к концам, сверху добавлено веко — тень, из-за
    которой глаз выглядит прищуренным, а не вытаращенным. Блик остался
    маленькой точкой: он даёт «живость», но не увеличивает глаз.
    """
    for sign in (-1, 1):
        cx = 50 + sign * EYE_DX
        if eyes == "closed":
            # Закрытый глаз — дуга «∩»: кот жмурится или спит.
            lid = QPainterPath()
            lid.moveTo(cx - 8, 53)
            lid.quadTo(cx, 44.5, cx + 8, 53)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(_pen(face.outline, 1.8, unit, min_px=1.0))
            painter.drawPath(lid)
            continue

        radius = EYE_R_WIDE if eyes == "wide" else EYE_R
        # Белок
        painter.setBrush(QBrush(face.sclera))
        painter.setPen(_pen(face.outline, 1.3, unit, min_px=0.9))
        painter.drawEllipse(QRectF(cx - radius, EYE_Y - radius, radius * 2, radius * 2))
        # Радужка
        painter.setBrush(QBrush(face.iris))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(cx - IRIS_R, EYE_Y - IRIS_R, IRIS_R * 2, IRIS_R * 2))
        # Зрачок-щель: вертикальный, заострённый к концам
        painter.setBrush(QBrush(face.eye))
        painter.drawPath(_pupil_path(cx, EYE_Y, PUPIL_W, PUPIL_H))
        # Верхнее веко: полупрозрачная тень поверх верхней части глаза —
        # взгляд становится прищуренным, а не «гипнотическим».
        painter.setBrush(QBrush(QColor(0, 0, 0, 38)))
        painter.setPen(Qt.NoPen)
        painter.drawPath(_upper_lid(cx, radius))
        # Блик — маленькая белая точка
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.drawEllipse(
            QRectF(cx - radius * 0.5, EYE_Y - radius * 0.62, 2.4, 2.4)
        )


def _upper_lid(cx: float, radius: float) -> QPainterPath:
    """Тень верхнего века: сегмент круга глаза над центром."""
    lid = QPainterPath()
    lid.moveTo(cx - radius * 0.96, EYE_Y - radius * 0.28)
    lid.quadTo(cx, EYE_Y - radius * 1.5, cx + radius * 0.96, EYE_Y - radius * 0.28)
    lid.quadTo(cx, EYE_Y - radius * 0.55, cx - radius * 0.96, EYE_Y - radius * 0.28)
    lid.closeSubpath()
    return lid


def _paint_nose_and_mouth(painter: QPainter, unit: float,
                          face: FacePalette) -> None:
    """Нос — маленький треугольник, под ним чёрточка и кошачий рот «ω»."""
    painter.setBrush(QBrush(face.nose))
    painter.setPen(_pen(face.outline, 1.0, unit, min_px=0.8))
    painter.drawPath(_path(((46, 66), (54, 66), (50, 71.5))))

    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(face.outline, 1.4, unit, min_px=0.9))
    # Короткая вертикальная чёрточка от носа вниз (губа)
    painter.drawLine(QPointF(50, 71.5), QPointF(50, 75))
    # Две дуги от неё вниз — «ω»
    left = QPainterPath()
    left.moveTo(50, 75)
    left.quadTo(45.5, 81, 40, 76.5)
    right = QPainterPath()
    right.moveTo(50, 75)
    right.quadTo(54.5, 81, 60, 76.5)
    painter.drawPath(left)
    painter.drawPath(right)


def _paint_whiskers(painter: QPainter, unit: float,
                    face: FacePalette) -> None:
    """
    Усы: по три с каждой стороны, КОРОТКИЕ и чуть поднятые вверх.

    Прежние усы уходили почти к краям квадрата и загибались вниз — кот
    выглядел как морж (проблема #9). Теперь они в 1.5–2 раза короче,
    тоньше и загнуты ВВЕРХ: так рисуют довольную кошку.
    """
    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(face.whisker, 1.0, unit, min_px=0.6))

    #: (начало, контрольная точка изгиба, конец) для левой стороны.
    #: Концы подняты выше начала — усы «улыбаются», а не свисают.
    left_side = (
        ((40.0, 66.0), (32.0, 62.5), (23.0, 61.5)),
        ((39.0, 70.5), (31.0, 69.0), (21.5, 67.0)),
        ((40.0, 75.0), (32.0, 76.0), (23.0, 74.0)),
    )
    for start, control, end in left_side:
        right_side = _mirror((start, control, end))
        for points in ((start, control, end), right_side):
            path = QPainterPath()
            path.moveTo(*points[0])
            path.quadTo(*points[1], *points[2])
            painter.drawPath(path)


def _paint_glasses(painter: QPainter, unit: float,
                   face: FacePalette) -> None:
    """Очки учёного кота: тонкая оправа, чтобы глаза читались."""
    painter.setBrush(QBrush(QColor(face.glasses.red(), face.glasses.green(),
                                   face.glasses.blue(), 45)))
    painter.setPen(_pen(METAL, 1.3, unit, min_px=0.8))
    for sign in (-1, 1):
        cx = 50 + sign * EYE_DX
        painter.drawEllipse(QRectF(cx - LENS_R, EYE_Y - LENS_R, LENS_R * 2, LENS_R * 2))
    # Мостик и дужки к скулам
    bridge = QPainterPath()
    bridge.moveTo(50 - EYE_DX + LENS_R - 1.5, EYE_Y - 1)
    bridge.quadTo(50, EYE_Y - 4.5, 50 + EYE_DX - LENS_R + 1.5, EYE_Y - 1)
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(bridge)
    painter.drawLine(QPointF(50 - EYE_DX - LENS_R + 1, EYE_Y - 2),
                     QPointF(13.5, EYE_Y - 6))
    painter.drawLine(QPointF(50 + EYE_DX + LENS_R - 1, EYE_Y - 2),
                     QPointF(86.5, EYE_Y - 6))


def _paint_magnifier(painter: QPainter, unit: float,
                     face: FacePalette) -> None:
    """Лупа сбоку: меньше прежней и не закрывает морду."""
    painter.setBrush(QBrush(QColor(face.glasses.red(), face.glasses.green(),
                                   face.glasses.blue(), 70)))
    painter.setPen(_pen(face.outline, 1.8, unit))
    painter.drawEllipse(QRectF(73, 73, 20, 20))
    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(face.outline, 3.0, unit, min_px=1.2))
    painter.drawLine(QPointF(89, 89), QPointF(96.5, 96.5))


def _paint_hat(painter: QPainter, unit: float, face: FacePalette) -> None:
    """
    Кепка Шерлока (deerstalker): купол, козырёк и клетка.

    Прежняя шляпа была ковбойской — с широкими полями и высокой тульей
    (проблема #8). У кепки Шерлока узнаваемые признаки другие: низкий
    купол, ПАРА КОЗЫРЬКОВ (спереди и сзади) и клетчатая ткань. Клетка
    рисуется тонкими линиями поверх купола: сплошной узор на 32 px
    превратился бы в грязь, а редкие линии читаются.
    """
    # ── Купол: низкий и округлый, СИДИТ ВЫШЕ ГЛАЗ ──
    # Нижняя граница купола — y=27, а глаза начинаются с y=42: между ними
    # остаётся лоб. Иначе кепка наезжает на глаза и кот выглядит спящим.
    crown = QPainterPath()
    crown.moveTo(26, 27)
    crown.cubicTo(26, 12, 34, 5, 50, 5)
    crown.cubicTo(66, 5, 74, 12, 74, 27)
    crown.closeSubpath()

    painter.setBrush(QBrush(HAT))
    painter.setPen(_pen(face.outline, 1.8, unit))
    painter.drawPath(crown)

    # ── Клетка: редкие линии по куполу ──
    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(HAT_BAND, 1.0, unit, min_px=0.7))
    for x in (36.0, 50.0, 64.0):
        painter.drawLine(QPointF(x, 8.0), QPointF(x, 26.0))
    for y in (14.0, 20.5):
        painter.drawLine(QPointF(28.0, y), QPointF(72.0, y))

    # ── Лента по краю купола ──
    painter.setBrush(QBrush(HAT_BAND))
    painter.setPen(Qt.NoPen)
    painter.drawRect(QRectF(26.0, 23.5, 48.0, 3.4))

    # ── Козырёк: один, спереди (у кепки Шерлока он узнаётся по нему) ──
    painter.setBrush(QBrush(HAT.darker(115)))
    painter.setPen(_pen(face.outline, 1.6, unit))
    visor = QPainterPath()
    visor.moveTo(29, 27)
    visor.cubicTo(37, 31, 63, 31, 71, 27)
    visor.cubicTo(63, 36, 37, 36, 29, 27)
    visor.closeSubpath()
    painter.drawPath(visor)

    # ── Задний козырёк и «хвостики» кепки поверх ушей ──
    painter.setBrush(QBrush(HAT))
    back = QPainterPath()
    back.moveTo(32, 7)
    back.cubicTo(40, 2, 60, 2, 68, 7)
    back.cubicTo(60, 11, 40, 11, 32, 7)
    back.closeSubpath()
    painter.drawPath(back)


def _paint_shoulders(painter: QPainter, unit: float, coat: Coat,
                     face: FacePalette) -> None:
    """Плечи кота-логиста: жилету нужна фигура, а не пустота под головой."""
    shoulders = QPainterPath()
    shoulders.moveTo(10, 100)
    shoulders.cubicTo(12, 89, 26, 84, 50, 84)
    shoulders.cubicTo(74, 84, 88, 89, 90, 100)
    shoulders.closeSubpath()
    painter.setBrush(QBrush(coat.fur))
    painter.setPen(_pen(face.outline, CONTOUR, unit))
    painter.drawPath(shoulders)


def _paint_vest(painter: QPainter, unit: float,
                face: FacePalette) -> None:
    """Сигнальный жилет кота-логиста (V-образный вырез, светоотражатели)."""
    vest = QPainterPath()
    vest.moveTo(12, 100)
    vest.cubicTo(14, 90, 26, 85, 40, 85)
    vest.lineTo(50, 96)
    vest.lineTo(60, 85)
    vest.cubicTo(74, 85, 86, 90, 88, 100)
    vest.closeSubpath()

    painter.setBrush(QBrush(VEST))
    painter.setPen(_pen(face.outline, 1.6, unit))
    painter.drawPath(vest)

    painter.setBrush(QBrush(QColor("#F2F7A0")))
    painter.setPen(Qt.NoPen)
    for y in (92, 96.5):
        painter.drawRect(QRectF(15, y, 22, 3))
        painter.drawRect(QRectF(63, y, 22, 3))


def _paint_tablet(painter: QPainter, unit: float,
                  face: FacePalette) -> None:
    """Планшет в лапах кота-логиста."""
    painter.setBrush(QBrush(TABLET))
    painter.setPen(_pen(face.outline, 1.6, unit))
    painter.drawRoundedRect(QRectF(62, 82, 30, 17), 2.5, 2.5)
    painter.setPen(_pen(QColor("#8FA3BF"), 1.3, unit, min_px=0.7))
    for index, width in enumerate((18, 14, 16)):
        y = 87 + index * 4
        painter.drawLine(QPointF(67, y), QPointF(67 + width, y))


def _paint_paws_and_z(painter: QPainter, unit: float,
                      face: FacePalette) -> None:
    """Спящий хранитель: лапы под подбородком и «z-z-z» в углу."""
    painter.setBrush(QBrush(MUZZLE))
    painter.setPen(_pen(face.outline, 1.5, unit))
    painter.drawEllipse(QRectF(34, 84, 18, 11))
    painter.drawEllipse(QRectF(48, 86, 18, 11))

    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(face.outline, 1.8, unit, min_px=0.9))
    for x, y, scale in ((78.0, 46.0, 9.0), (86.0, 35.0, 7.0), (93.0, 25.0, 5.0)):
        painter.drawLine(QPointF(x, y), QPointF(x + scale, y))
        painter.drawLine(QPointF(x + scale, y), QPointF(x, y + scale))
        painter.drawLine(QPointF(x, y + scale), QPointF(x + scale, y + scale))


def avatar_pixmap(
    size: int = 64,
    persona_key: str = DEFAULT_PERSONA,
    eyes: str = "open",
    device_ratio: float = 1.0,
) -> QPixmap:
    """Готовый рисунок кота (для иконок, подсказок и скриншотов)."""
    ratio = max(1.0, float(device_ratio))
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        paint_cat(painter, size * ratio, persona_key, eyes)
    finally:
        painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def avatar_icon(size: int = 32, persona_key: str = DEFAULT_PERSONA) -> QIcon:
    """Иконка кота для кнопки в углу окна."""
    return QIcon(avatar_pixmap(size, persona_key))


class CatAvatar(QWidget):
    """
    Живой аватар: статичный рисунок плюс мигание (задание, часть D.2).

    Виджет не знает, где он стоит: размер задаётся ``set_avatar_size``,
    образ — ``set_persona``, «думает» — ``set_thinking``. Таймер мигания
    останавливается, когда виджет скрыт: анимация в фоне не нужна.
    """

    #: Кот мигнул (нужно тестам: анимация проверяется без ожидания 10 секунд).
    blinked = pyqtSignal()

    def __init__(self, size: int = 64, persona_key: str = DEFAULT_PERSONA,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._size = max(16, int(size))
        self._persona = _norm(persona_key)
        self._eyes = "open"
        self._thinking = False
        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.timeout.connect(self._close_eyes)
        self._open_timer = QTimer(self)
        self._open_timer.setSingleShot(True)
        self._open_timer.timeout.connect(self._open_eyes)

        self.setFixedSize(QSize(self._size, self._size))
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._schedule_blink()

    # ---------------------------------------------------------
    # Свойства
    # ---------------------------------------------------------
    def set_avatar_size(self, size: int) -> None:
        """Меняет размер рисунка (32 — иконка, 64 — панель, 96 — плавающий)."""
        self._size = max(16, int(size))
        self.setFixedSize(QSize(self._size, self._size))
        self.update()

    def avatar_size(self) -> int:
        """Текущий размер рисунка."""
        return self._size

    def set_persona(self, persona_key: str) -> None:
        """Меняет образ (ключи — core/agent/personas.py)."""
        self._persona = _norm(persona_key)
        self.update()

    def persona(self) -> str:
        """Текущий образ."""
        return self._persona

    def set_thinking(self, thinking: bool) -> None:
        """
        «Думает»: глаза широко открыты, мигание приостановлено.

        Пока агент ждёт ответ, аватар должен выглядеть живым, а не мигать
        вразнобой с индикатором «думаю…».
        """
        self._thinking = bool(thinking)
        if self._thinking:
            self._blink_timer.stop()
            self._open_timer.stop()
            self._eyes = "wide"
        else:
            self._eyes = "open"
            self._schedule_blink()
        self.update()

    def is_thinking(self) -> bool:
        """Ждёт ли аватар ответ модели."""
        return self._thinking

    def eyes_state(self) -> str:
        """
        Состояние глаз: open / closed / wide (нужно тестам).

        У спящего хранителя глаза закрыты всегда — это его образ, а не
        мигание: так же считает и рисование (см. paint_cat).
        """
        if self._persona == "cat_keeper" and self._eyes != "wide":
            return "closed"
        return self._eyes

    def blink_now(self) -> None:
        """Мигает немедленно (тесты и ручная проверка анимации)."""
        if not self._thinking:
            self._close_eyes()
            self._schedule_blink()

    def stop_animation(self) -> None:
        """
        Останавливает мигание (вызывается при выходе из программы).

        Виджет может быть уже разрушен: тогда таймеры останавливать нечего.
        """
        try:
            self._blink_timer.stop()
            self._open_timer.stop()
        except RuntimeError:
            return

    # ---------------------------------------------------------
    # Анимация
    # ---------------------------------------------------------
    def _schedule_blink(self) -> None:
        """Планирует следующее мигание через 5–10 секунд."""
        if self._thinking or not self.isVisible():
            return
        self._blink_timer.start(random.randint(BLINK_MIN_MS, BLINK_MAX_MS))

    def _close_eyes(self) -> None:
        """Закрывает глаза на мгновение."""
        self._eyes = "closed"
        self.update()
        self._open_timer.start(BLINK_HOLD_MS)
        self.blinked.emit()

    def _open_eyes(self) -> None:
        """Открывает глаза и планирует следующее мигание."""
        self._eyes = "open"
        self.update()
        self._schedule_blink()

    def showEvent(self, event) -> None:
        """Показ виджета — возобновляем мигание."""
        super().showEvent(event)
        self._schedule_blink()

    def hideEvent(self, event) -> None:
        """Скрытие виджета — мигание останавливается."""
        self._blink_timer.stop()
        self._open_timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 — имя метода Qt
        """Рисует кота текущего размера и состояния."""
        painter = QPainter(self)
        try:
            paint_cat(painter, self._size, self._persona, self._eyes)
        finally:
            painter.end()


__all__ = [
    "BLINK_HOLD_MS",
    "BLINK_MAX_MS",
    "BLINK_MIN_MS",
    "CatAvatar",
    "avatar_icon",
    "avatar_keys",
    "avatar_pixmap",
    "paint_cat",
]
