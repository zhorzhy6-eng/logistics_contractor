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
IRIS = QColor("#F2B33D")
OUTLINE = QColor("#4A3520")
WHISKER = QColor("#6B5334")
NOSE = QColor("#D96A78")
GLASS = QColor("#7FD3F7")
METAL = QColor("#B9C4D2")
VEST = QColor("#3F7FBF")
TABLET = QColor("#DCE6F2")
HAT = QColor("#5A4632")
HAT_BAND = QColor("#7A5C3E")

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
EYE_DX = 15.0
EYE_Y = 50.0
EYE_R = 9.6
EYE_R_WIDE = 10.8
IRIS_R = 6.4
#: Зрачок-щель: вертикальный, а не кружок.
PUPIL_W = 3.0
PUPIL_H = 9.4
#: Радиус линзы очков.
LENS_R = 13.6


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

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.translate(rect.left(), rect.top())
    # Работаем в системе координат 100×100: все числа ниже — «проценты».
    painter.scale(unit, unit)

    if persona == "cat_keeper":
        eyes = "closed"

    # Порядок важен: туловище — позади головы, шляпа и очки — поверх морды.
    if persona == "cat_logist":
        _paint_shoulders(painter, unit)
    _paint_ears(painter, unit)
    _paint_head(painter, unit)
    _paint_face(painter, unit, eyes)

    if persona == "cat_scientist":
        _paint_glasses(painter, unit)
        _paint_magnifier(painter, unit)
    elif persona == "cat_detective":
        _paint_hat(painter, unit)
        _paint_magnifier(painter, unit)
    elif persona == "cat_logist":
        _paint_vest(painter, unit)
        _paint_tablet(painter, unit)
    elif persona == "cat_keeper":
        _paint_paws_and_z(painter, unit)

    painter.restore()


def _paint_ears(painter: QPainter, unit: float) -> None:
    """Уши: острые треугольники торчком, внутри — розовая раковина."""
    painter.setBrush(QBrush(FUR))
    painter.setPen(_pen(OUTLINE, CONTOUR, unit))
    for sign in (1, -1):
        outer = LEFT_EAR if sign > 0 else _mirror(LEFT_EAR)
        inner = _shrink(outer, 0.45)
        painter.drawPath(_path(outer))
        painter.setBrush(QBrush(NOSE.lighter(135)))
        painter.setPen(Qt.NoPen)
        painter.drawPath(_path(inner))
        painter.setBrush(QBrush(FUR))
        painter.setPen(_pen(OUTLINE, CONTOUR, unit))


def _paint_head(painter: QPainter, unit: float) -> None:
    """
    Голова: шире, чем выше, со скулами и чуть заострённым подбородком.

    Одна замкнутая кривая — если рисовать эллипс и подбородок отдельно,
    на морде останется видимый шов.
    """
    head = QPainterPath()
    head.moveTo(14, 52)
    head.cubicTo(14, 33, 26, 27, 38, 28)     # левая скула → верх головы
    head.cubicTo(43, 26, 57, 26, 62, 28)
    head.cubicTo(74, 27, 86, 33, 86, 52)     # верх → правая скула
    head.cubicTo(86, 72, 68, 82, 50, 88)     # скула → подбородок
    head.cubicTo(32, 82, 14, 72, 14, 52)     # подбородок → левая скула
    head.closeSubpath()

    painter.setBrush(QBrush(FUR))
    painter.setPen(_pen(OUTLINE, CONTOUR, unit))
    painter.drawPath(head)


def _paint_face(painter: QPainter, unit: float, eyes: str) -> None:
    """Светлая морда, нос-треугольник, рот «ω», усы и глаза."""
    # ── Морда: светлое пятно шире, чем выше, с подбородком ──
    muzzle = QPainterPath()
    muzzle.moveTo(31, 69)
    muzzle.cubicTo(31, 61, 40, 59, 50, 59)
    muzzle.cubicTo(60, 59, 69, 61, 69, 69)
    muzzle.cubicTo(69, 79, 58, 85, 50, 87)
    muzzle.cubicTo(42, 85, 31, 79, 31, 69)
    muzzle.closeSubpath()
    painter.setBrush(QBrush(MUZZLE))
    painter.setPen(Qt.NoPen)
    painter.drawPath(muzzle)

    _paint_eyes(painter, unit, eyes)
    _paint_nose_and_mouth(painter, unit)
    _paint_whiskers(painter, unit)


def _paint_eyes(painter: QPainter, unit: float, eyes: str) -> None:
    """Большие круглые глаза с вертикальным зрачком-щелью."""
    for sign in (-1, 1):
        cx = 50 + sign * EYE_DX
        if eyes == "closed":
            # Закрытый глаз — дуга «∩»: кот жмурится или спит.
            lid = QPainterPath()
            lid.moveTo(cx - 9, 53)
            lid.quadTo(cx, 43.5, cx + 9, 53)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(_pen(OUTLINE, 1.8, unit, min_px=1.0))
            painter.drawPath(lid)
            continue

        radius = EYE_R_WIDE if eyes == "wide" else EYE_R
        # Белок
        painter.setBrush(QBrush(QColor("#FFF8EC")))
        painter.setPen(_pen(OUTLINE, 1.4, unit, min_px=0.9))
        painter.drawEllipse(QRectF(cx - radius, EYE_Y - radius, radius * 2, radius * 2))
        # Радужка
        painter.setBrush(QBrush(IRIS))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(cx - IRIS_R, EYE_Y - IRIS_R, IRIS_R * 2, IRIS_R * 2))
        # Зрачок-щель: вертикальный, а не круглый «пятачок»
        painter.setBrush(QBrush(EYE))
        painter.drawEllipse(
            QRectF(cx - PUPIL_W / 2, EYE_Y - PUPIL_H / 2, PUPIL_W, PUPIL_H)
        )
        # Блик
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        painter.drawEllipse(QRectF(cx - radius * 0.55, EYE_Y - radius * 0.72, 3.2, 3.2))


def _paint_nose_and_mouth(painter: QPainter, unit: float) -> None:
    """Нос — маленький треугольник, под ним чёрточка и кошачий рот «ω»."""
    painter.setBrush(QBrush(NOSE))
    painter.setPen(_pen(OUTLINE, 1.0, unit, min_px=0.8))
    painter.drawPath(_path(((46, 66), (54, 66), (50, 71.5))))

    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(OUTLINE, 1.4, unit, min_px=0.9))
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


def _paint_whiskers(painter: QPainter, unit: float) -> None:
    """Усы: по три с каждой стороны, тонкие и чуть изогнутые."""
    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(WHISKER, 1.1, unit, min_px=0.7))

    #: (начало, контрольная точка изгиба, конец) для левой стороны.
    left_side = (
        ((40.0, 66.0), (24.0, 60.0), (9.0, 59.0)),
        ((39.0, 71.0), (23.0, 69.0), (7.0, 69.0)),
        ((40.0, 76.0), (24.0, 78.0), (9.0, 80.0)),
    )
    for start, control, end in left_side:
        right_side = _mirror((start, control, end))
        for points in ((start, control, end), right_side):
            path = QPainterPath()
            path.moveTo(*points[0])
            path.quadTo(*points[1], *points[2])
            painter.drawPath(path)


def _paint_glasses(painter: QPainter, unit: float) -> None:
    """Очки учёного кота: тонкая оправа, чтобы глаза читались."""
    painter.setBrush(QBrush(QColor(GLASS.red(), GLASS.green(), GLASS.blue(), 45)))
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


def _paint_magnifier(painter: QPainter, unit: float) -> None:
    """Лупа сбоку: меньше прежней и не закрывает морду."""
    painter.setBrush(QBrush(QColor(GLASS.red(), GLASS.green(), GLASS.blue(), 70)))
    painter.setPen(_pen(OUTLINE, 1.8, unit))
    painter.drawEllipse(QRectF(73, 73, 20, 20))
    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(OUTLINE, 3.0, unit, min_px=1.2))
    painter.drawLine(QPointF(89, 89), QPointF(96.5, 96.5))


def _paint_hat(painter: QPainter, unit: float) -> None:
    """Шляпа детектива: поля и тулья с лентой (уши остаются видны сверху)."""
    crown = QPainterPath()
    crown.moveTo(26, 31)
    crown.cubicTo(27, 17, 30, 11, 35, 10)
    crown.lineTo(65, 10)
    crown.cubicTo(70, 11, 73, 17, 74, 31)
    crown.closeSubpath()

    painter.setBrush(QBrush(HAT))
    painter.setPen(_pen(OUTLINE, 1.8, unit))
    painter.drawPath(crown)
    painter.setBrush(QBrush(HAT_BAND))
    painter.setPen(Qt.NoPen)
    painter.drawRect(QRectF(26, 23, 48, 5.5))
    painter.setBrush(QBrush(HAT))
    painter.setPen(_pen(OUTLINE, 1.8, unit))
    painter.drawEllipse(QRectF(12, 26, 76, 12))


def _paint_shoulders(painter: QPainter, unit: float) -> None:
    """Плечи кота-логиста: жилету нужна фигура, а не пустота под головой."""
    shoulders = QPainterPath()
    shoulders.moveTo(10, 100)
    shoulders.cubicTo(12, 89, 26, 84, 50, 84)
    shoulders.cubicTo(74, 84, 88, 89, 90, 100)
    shoulders.closeSubpath()
    painter.setBrush(QBrush(FUR))
    painter.setPen(_pen(OUTLINE, CONTOUR, unit))
    painter.drawPath(shoulders)


def _paint_vest(painter: QPainter, unit: float) -> None:
    """Сигнальный жилет кота-логиста (V-образный вырез, светоотражатели)."""
    vest = QPainterPath()
    vest.moveTo(12, 100)
    vest.cubicTo(14, 90, 26, 85, 40, 85)
    vest.lineTo(50, 96)
    vest.lineTo(60, 85)
    vest.cubicTo(74, 85, 86, 90, 88, 100)
    vest.closeSubpath()

    painter.setBrush(QBrush(VEST))
    painter.setPen(_pen(OUTLINE, 1.6, unit))
    painter.drawPath(vest)

    painter.setBrush(QBrush(QColor("#F2F7A0")))
    painter.setPen(Qt.NoPen)
    for y in (92, 96.5):
        painter.drawRect(QRectF(15, y, 22, 3))
        painter.drawRect(QRectF(63, y, 22, 3))


def _paint_tablet(painter: QPainter, unit: float) -> None:
    """Планшет в лапах кота-логиста."""
    painter.setBrush(QBrush(TABLET))
    painter.setPen(_pen(OUTLINE, 1.6, unit))
    painter.drawRoundedRect(QRectF(62, 82, 30, 17), 2.5, 2.5)
    painter.setPen(_pen(QColor("#8FA3BF"), 1.3, unit, min_px=0.7))
    for index, width in enumerate((18, 14, 16)):
        y = 87 + index * 4
        painter.drawLine(QPointF(67, y), QPointF(67 + width, y))


def _paint_paws_and_z(painter: QPainter, unit: float) -> None:
    """Спящий хранитель: лапы под подбородком и «z-z-z» в углу."""
    painter.setBrush(QBrush(MUZZLE))
    painter.setPen(_pen(OUTLINE, 1.5, unit))
    painter.drawEllipse(QRectF(34, 84, 18, 11))
    painter.drawEllipse(QRectF(48, 86, 18, 11))

    painter.setBrush(Qt.NoBrush)
    painter.setPen(_pen(OUTLINE, 1.8, unit, min_px=0.9))
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
