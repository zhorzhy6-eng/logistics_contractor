#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Водители — таблицы drivers, driver_vehicles-поля и driver_carriers.

Мягкое удаление (вариант В): delete_driver() не стирает запись, а ставит
is_deleted = 1 — водитель исчезает из списков и поиска, но договоры
(contracts.driver_id) и данные тягача/прицепа остаются целыми, а запись
можно вернуть restore_driver().

Поиск идёт двумя путями: FTS5 по ФИО (регистронезависимо для кириллицы) и
прежний LIKE — по паспорту («серия + пробел + номер»), телефону и как
резерв, когда FTS ничего не нашёл.

Таблица driver_carriers — история работы водителя у перевозчиков: запись
без ended_at считается активной. Мягкое удаление историю не рвёт:
delete_driver() её не трогает (после restore_driver она снова видна),
а delete_organization(is_carrier=True) не обнуляет default_carrier_id.
"""

import datetime
import logging
import sqlite3
from typing import Any, Dict, List, Optional

from core import audit

from db import fts
from db.connection import get_connection
from db.crud.search import ensure_fts_fresh

logger = logging.getLogger("db.crud.drivers")


def _carrier_id_or_none(value: Any) -> Optional[int]:
    """
    ID перевозчика из данных формы: пустое значение — это NULL.

    Вкладка отдаёт «— не указан —» как None, но через промежуточные
    словари значение может прийти пустой строкой или нулём — все они
    значат «привязки нет».
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def save_driver(driver_data: Dict[str, Any]) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO drivers (
            full_name, birth_date, birth_place,
            passport_series, passport_number,
            passport_issue_date, passport_issuer, passport_code,
            registration_address,
            license_series, license_number,
            license_issue_date, license_expiry_date, license_categories,
            phone, default_carrier_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        driver_data.get("full_name", ""),
        driver_data.get("birth_date", ""),
        driver_data.get("birth_place", ""),
        driver_data.get("passport_series", ""),
        driver_data.get("passport_number", ""),
        driver_data.get("passport_issue_date", ""),
        driver_data.get("passport_issuer", ""),
        driver_data.get("passport_code", ""),
        driver_data.get("registration_address", ""),
        driver_data.get("license_series", ""),
        driver_data.get("license_number", ""),
        driver_data.get("license_issue_date", ""),
        driver_data.get("license_expiry_date", ""),
        driver_data.get("license_categories", ""),
        driver_data.get("phone", ""),
        # Основной перевозчик (ШАГ «Привязка водителей к перевозчикам»).
        # Ключа нет — водитель заводится без привязки (NULL).
        _carrier_id_or_none(driver_data.get("default_carrier_id")),
    ))
    driver_id = cursor.lastrowid
    # FTS5: без триггеров — обновляем индекс вручную там же, где пишем данные.
    fts.replace_row(
        conn, "fts_drivers", driver_id, (driver_data.get("full_name", ""),)
    )
    conn.commit()
    conn.close()
    logger.info(f"Водитель сохранён: ID={driver_id}")
    return driver_id


def update_driver(driver_id: int, driver_data: Dict[str, Any]) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Старое ФИО нужно FTS5: удаление из индекса идёт по старым значениям.
        old = cursor.execute(
            "SELECT full_name FROM drivers WHERE id = ?", (driver_id,)
        ).fetchone()

        cursor.execute("""
            UPDATE drivers SET
                full_name = ?, birth_date = ?, birth_place = ?,
                passport_series = ?, passport_number = ?,
                passport_issue_date = ?, passport_issuer = ?, passport_code = ?,
                registration_address = ?,
                license_series = ?, license_number = ?,
                license_issue_date = ?, license_expiry_date = ?, license_categories = ?,
                phone = ?
            WHERE id = ?
        """, (
            driver_data.get("full_name", ""),
            driver_data.get("birth_date", ""),
            driver_data.get("birth_place", ""),
            driver_data.get("passport_series", ""),
            driver_data.get("passport_number", ""),
            driver_data.get("passport_issue_date", ""),
            driver_data.get("passport_issuer", ""),
            driver_data.get("passport_code", ""),
            driver_data.get("registration_address", ""),
            driver_data.get("license_series", ""),
            driver_data.get("license_number", ""),
            driver_data.get("license_issue_date", ""),
            driver_data.get("license_expiry_date", ""),
            driver_data.get("license_categories", ""),
            driver_data.get("phone", ""),
            driver_id,
        ))
        if old:
            fts.replace_row(
                conn, "fts_drivers", driver_id,
                (driver_data.get("full_name", ""),), (old[0],),
            )

        # Основной перевозчик (ШАГ «Привязка водителей к перевозчикам»)
        # обновляется ТОЛЬКО когда ключ пришёл в данных: вкладка аренды
        # собирает запись из своих полей (merge_driver_records), и этого
        # ключа в ней нет — иначе сохранение из аренды обнулило бы
        # привязку, выставленную в «Экспедиторстве».
        if "default_carrier_id" in driver_data:
            cursor.execute(
                "UPDATE drivers SET default_carrier_id = ? WHERE id = ?",
                (
                    _carrier_id_or_none(driver_data.get("default_carrier_id")),
                    driver_id,
                ),
            )

        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления водителя: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def load_driver(driver_id: int) -> Optional[Dict[str, Any]]:
    """
    Водитель по ID — включая мягко удалённого (is_deleted = 1).

    Скрытие удалённых делает не эта функция, а списки/поиск
    (get_all_drivers / search_drivers): по ID запись нужна, например,
    чтобы показать связь договора или восстановить водителя.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM drivers WHERE id = ?", (driver_id,))
    row = cursor.fetchone()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return dict(zip(cols, row)) if row else None


def search_drivers(
    search_term: str,
    limit: int = 50,
    include_deleted: bool = False,
    with_carrier_name: bool = False,
) -> List[Dict[str, Any]]:
    """
    Поиск водителей по ФИО, паспорту или телефону.

    Шаг 5 оптимизации: поиск целиком перенесён в SQL. Раньше UI выгружал
    всю таблицу водителей (get_all_drivers) и фильтровал её в Python —
    на 20 000 записях это ~80 мс на каждое нажатие клавиши.

    Регистр больше не важен для кириллицы: словесные запросы («иванов»,
    «ИВАНОВ») идут через FTS5 и находят «Иванов». Запросы с цифрами
    (паспорт «18 22 926830», телефон) и случаи, когда FTS ничего не нашёл,
    обслуживает прежний LIKE — поведение сохранено.

    Паспорт сравнивается так же, как раньше в интерфейсе: склейка
    «серия + пробел + номер», поэтому поиск по «18 22 926830» работает.

    include_deleted=True показывает и мягко удалённых (режим «Показывать
    удалённых» в менеджере базы) — иначе они не должны попадаться в поиске.

    with_carrier_name=True добавляет к записи название основного
    перевозчика (`carrier_name`) — менеджеру базы нужна колонка
    «Перевозчик». По умолчанию (False) набор колонок прежний, чтобы
    не менять существующие вызовы.
    """
    conn = get_connection()
    cursor = conn.cursor()

    carrier_column = (
        ", COALESCE(NULLIF(TRIM(c.full_name), ''), c.short_name, '') "
        "AS carrier_name"
        if with_carrier_name else ""
    )
    carrier_join = (
        "LEFT JOIN carriers c ON c.id = d.default_carrier_id "
        if with_carrier_name else ""
    )

    try:
        only_active = 1 if include_deleted else 0

        # ── Путь 1: FTS5 по ФИО (регистронезависимо для кириллицы) ──
        match_query = fts.match_query_for(search_term) if search_term else None
        if match_query and fts.fts5_available(conn):
            try:
                ensure_fts_fresh(conn, "fts_drivers")
                cursor.execute(
                    f"SELECT d.*{carrier_column} FROM drivers d "
                    f"{carrier_join}"
                    "JOIN fts_drivers f ON f.rowid = d.id "
                    "WHERE fts_drivers MATCH ? AND (d.is_deleted = 0 OR ?) "
                    "ORDER BY d.full_name COLLATE NOCASE LIMIT ?",
                    (match_query, only_active, int(limit)),
                )
                rows = cursor.fetchall()
                if rows:
                    cols = [desc[0] for desc in cursor.description]
                    logger.debug(
                        f"Поиск водителей (FTS5): {search_term!r} -> "
                        f"{match_query!r}, найдено {len(rows)}"
                    )
                    return [dict(zip(cols, row)) for row in rows]
            except sqlite3.DatabaseError as e:
                logger.warning(f"FTS5-поиск водителей не удался, откат на LIKE: {e}")

        # ── Путь 2: LIKE (как раньше) ──
        like = f"%{search_term}%"
        cursor.execute(
            f"SELECT d.*{carrier_column} FROM drivers d "
            f"{carrier_join}"
            "WHERE (d.is_deleted = 0 OR ?) AND ("
            "      d.full_name LIKE ? "
            "   OR (COALESCE(d.passport_series, '') || ' ' || COALESCE(d.passport_number, '')) LIKE ? "
            "   OR COALESCE(d.phone, '') LIKE ?) "
            "ORDER BY d.full_name COLLATE NOCASE LIMIT ?",
            (only_active, like, like, like, int(limit)),
        )
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def get_all_drivers(
    include_deleted: bool = False,
    with_carrier_name: bool = False,
) -> List[Dict[str, Any]]:
    """
    Все водители; по умолчанию без мягко удалённых.

    with_carrier_name=True добавляет название основного перевозчика
    (`carrier_name`); по умолчанию набор колонок прежний.
    """
    carrier_column = (
        ", COALESCE(NULLIF(TRIM(c.full_name), ''), c.short_name, '') "
        "AS carrier_name"
        if with_carrier_name else ""
    )
    carrier_join = (
        "LEFT JOIN carriers c ON c.id = d.default_carrier_id "
        if with_carrier_name else ""
    )

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT d.*{carrier_column} FROM drivers d "
        f"{carrier_join}"
        "WHERE d.is_deleted = 0 OR ? "
        "ORDER BY d.full_name COLLATE NOCASE",
        (1 if include_deleted else 0,),
    )
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def restore_driver(driver_id: int) -> bool:
    """
    Возвращает мягко удалённого водителя в справочник (вариант В).

    Данные тягача/прицепа не трогались при удалении, поэтому после
    восстановления они снова доступны.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE drivers SET is_deleted = 0 WHERE id = ?", (driver_id,)
        )
        conn.commit()
        logger.info(f"Водитель восстановлен: ID={driver_id}")
        audit.log_event("driver_restored", driver_id=driver_id)
        return True
    except Exception as e:
        logger.error(f"Ошибка восстановления водителя: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def delete_driver(driver_id: int) -> bool:
    """
    Мягкое удаление водителя (вариант В): is_deleted = 1.

    Запись НЕ стирается и ссылки не рвутся:
      * водитель исчезает из списков и поиска (get_all_drivers,
        search_drivers), то есть для пользователя он удалён;
      * contracts.driver_id остаётся заполненным — история перевозок
        сохраняет, кто вёз;
      * данные тягача/прицепа (driver_vehicles) не удаляются;
      * запись можно вернуть: restore_driver().

    История изменений: сначала здесь был каскад (DELETE FROM contracts),
    затем SET NULL, теперь мягкое удаление.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT COUNT(*) FROM contracts WHERE driver_id = ?", (driver_id,)
        )
        linked_contracts = cursor.fetchone()[0]

        cursor.execute(
            "UPDATE drivers SET is_deleted = 1 WHERE id = ?", (driver_id,)
        )
        conn.commit()

        logger.info(
            f"Водитель удалён (мягко): ID={driver_id}, "
            f"договоров по-прежнему связано: {linked_contracts}"
        )
        audit.log_event(
            "driver_deleted",
            driver_id=driver_id,
            count=linked_contracts,
            entities="soft_delete",
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления водителя: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


# ШАГ «Привязка водителей к перевозчикам». До него связь «водитель ↔
# перевозчик» существовала только через договоры (contracts.driver_id +
# contracts.carrier_id), и в справочнике водителей не было видно, на кого
# он работает. Теперь у водителя есть основной перевозчик
# (drivers.default_carrier_id), а таблица driver_carriers хранит историю:
# запись без ended_at — активная связь.
#
# Мягкое удаление ничего не рвёт: delete_driver() не трогает историю
# (её видно после restore_driver), а delete_organization(is_carrier=True)
# не обнуляет drivers.default_carrier_id — запись перевозчика остаётся
# в базе, как и ссылки договоров.


def _active_link_sql(prefix: str = "") -> str:
    """
    Условие «связь ещё активна» для колонки ended_at.

    Пустая строка приравнена к NULL: так активными остаются и записи,
    добавленные в обход link_driver_to_carrier.

    :param prefix: префикс таблицы («dc.»), если запрос с JOIN.
    """
    column = f"{prefix}ended_at"
    return f"({column} IS NULL OR {column} = '')"

#: То же условие без префикса таблицы — для запросов к одной driver_carriers.
ACTIVE_LINK_SQL = _active_link_sql()


def _today_iso() -> str:
    """Сегодняшняя дата в ISO (ГГГГ-ММ-ДД) — формат дат справочника."""
    return datetime.date.today().isoformat()


def link_driver_to_carrier(
    driver_id: int,
    carrier_id: int,
    started_at: str = "",
    ended_at: str = "",
) -> int:
    """
    Добавляет запись в driver_carriers.

    Если у водителя уже есть АКТИВНАЯ связь с этим же перевозчиком
    (ended_at пуст) — новую не создаёт, возвращает id существующей.

    Переход к ДРУГОМУ перевозчику закрывает прежние активные связи: у них
    проставляется ended_at (дата начала новой связи, а если она не задана —
    сегодняшняя). Так в истории не остаётся двух «текущих» перевозчиков.

    :param started_at: дата начала работы (пусто — сегодняшняя).
    :param ended_at: дата окончания (пусто — связь активная).
    :return: id записи в driver_carriers.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT id FROM driver_carriers "
            "WHERE driver_id = ? AND carrier_id = ? "
            f"  AND {ACTIVE_LINK_SQL} "
            "ORDER BY id DESC LIMIT 1",
            (driver_id, carrier_id),
        )
        row = cursor.fetchone()
        if row:
            logger.debug(
                f"Связь водителя ID={driver_id} с перевозчиком "
                f"ID={carrier_id} уже активна — запись не создаётся"
            )
            return int(row[0])

        start = str(started_at or "").strip() or _today_iso()
        finish = str(ended_at or "").strip() or None

        cursor.execute(
            "UPDATE driver_carriers SET ended_at = ? "
            "WHERE driver_id = ? AND carrier_id != ? "
            f"  AND {ACTIVE_LINK_SQL}",
            (start, driver_id, carrier_id),
        )
        closed = cursor.rowcount

        cursor.execute(
            "INSERT INTO driver_carriers "
            "(driver_id, carrier_id, started_at, ended_at) "
            "VALUES (?, ?, ?, ?)",
            (driver_id, carrier_id, start, finish),
        )
        link_id = cursor.lastrowid
        conn.commit()

        logger.info(
            f"Водитель привязан к перевозчику: driver_id={driver_id}, "
            f"carrier_id={carrier_id}, закрыто прежних связей: {closed}"
        )
        audit.log_event(
            "driver_linked_to_carrier",
            driver_id=driver_id,
            carrier_id=carrier_id,
            count=closed,
        )
        return int(link_id)
    except Exception as e:
        logger.error(f"Ошибка привязки водителя к перевозчику: {e}")
        if conn:
            conn.rollback()
        return 0
    finally:
        if conn:
            conn.close()


def unlink_driver_from_carrier(
    driver_id: int,
    carrier_id: int,
    ended_at: str = "",
) -> bool:
    """
    Закрывает активную связь (ставит ended_at).

    Если ended_at пуст — сегодняшняя дата. Запись НЕ удаляется: история
    работы у перевозчика остаётся в базе.

    :return: True, если активная связь была и закрыта.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        finish = str(ended_at or "").strip() or _today_iso()
        cursor.execute(
            "UPDATE driver_carriers SET ended_at = ? "
            "WHERE driver_id = ? AND carrier_id = ? "
            f"  AND {ACTIVE_LINK_SQL}",
            (finish, driver_id, carrier_id),
        )
        closed = cursor.rowcount
        conn.commit()

        logger.info(
            f"Связь водителя с перевозчиком закрыта: driver_id={driver_id}, "
            f"carrier_id={carrier_id}, записей закрыто: {closed}"
        )
        if closed:
            audit.log_event(
                "driver_unlinked_from_carrier",
                driver_id=driver_id,
                carrier_id=carrier_id,
                count=closed,
            )
        return closed > 0
    except Exception as e:
        logger.error(f"Ошибка закрытия связи водителя с перевозчиком: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def get_driver_carriers(
    driver_id: int,
    active_only: bool = False,
) -> List[Dict[str, Any]]:
    """
    История работы водителя у перевозчиков.

    С JOIN на carriers, чтобы вернуть название перевозчика
    (`carrier_name`). Сортировка: активные первыми, потом по started_at
    по убыванию (свежие — выше).

    :param active_only: только действующие связи (ended_at пуст).
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        sql = (
            "SELECT dc.id, dc.driver_id, dc.carrier_id, "
            "       dc.started_at, dc.ended_at, dc.created_at, "
            "       COALESCE(NULLIF(TRIM(c.full_name), ''), c.short_name, '') "
            "           AS carrier_name, "
            "       COALESCE(c.short_name, '') AS carrier_short_name, "
            "       COALESCE(c.inn, '') AS carrier_inn "
            "FROM driver_carriers dc "
            "LEFT JOIN carriers c ON c.id = dc.carrier_id "
            "WHERE dc.driver_id = ?"
        )
        if active_only:
            sql += f" AND {_active_link_sql('dc.')}"
        sql += (
            " ORDER BY (dc.ended_at IS NULL OR dc.ended_at = '') DESC, "
            "          dc.started_at DESC, dc.id DESC"
        )

        cursor.execute(sql, (driver_id,))
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def get_carrier_drivers(
    carrier_id: int,
    active_only: bool = True,
) -> List[Dict[str, Any]]:
    """
    Водители, работающие у перевозчика.

    С JOIN на drivers: возвращаются все колонки водителя плюс поля связи
    (`link_id`, `carrier_id`, `started_at`, `ended_at`).

    :param active_only: только действующие связи (ended_at пуст).
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        sql = (
            "SELECT d.*, dc.id AS link_id, dc.carrier_id AS carrier_id, "
            "       dc.started_at AS started_at, dc.ended_at AS ended_at "
            "FROM driver_carriers dc "
            "JOIN drivers d ON d.id = dc.driver_id "
            "WHERE dc.carrier_id = ?"
        )
        if active_only:
            sql += f" AND {_active_link_sql('dc.')}"
        sql += " ORDER BY d.full_name COLLATE NOCASE"

        cursor.execute(sql, (carrier_id,))
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def set_default_carrier(
    driver_id: int,
    carrier_id: Optional[int],
) -> bool:
    """
    Устанавливает основной перевозчик водителя.

    Если carrier_id заполнен — обновляет drivers.default_carrier_id и (если
    активной связи ещё нет) пишет запись в driver_carriers.
    Если carrier_id = None — очищает default_carrier_id; история работы
    (driver_carriers) при этом НЕ трогается.

    :return: True, если значение записано.
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE drivers SET default_carrier_id = ? WHERE id = ?",
            (carrier_id, driver_id),
        )
        updated = cursor.rowcount
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка установки основного перевозчика: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

    if carrier_id is None:
        logger.info(f"Основной перевозчик водителя очищен: driver_id={driver_id}")
        return updated > 0

    logger.info(
        f"Основной перевозчик водителя: driver_id={driver_id}, "
        f"carrier_id={carrier_id}"
    )
    # Активной связи может не быть (например, водителя только что завели):
    # тогда она появляется здесь же. Если связь уже есть — link ничего
    # не создаёт.
    link_driver_to_carrier(driver_id, carrier_id)
    return True


__all__ = [
    "ACTIVE_LINK_SQL",
    "delete_driver",
    "get_all_drivers",
    "get_carrier_drivers",
    "get_driver_carriers",
    "link_driver_to_carrier",
    "load_driver",
    "restore_driver",
    "save_driver",
    "search_drivers",
    "set_default_carrier",
    "unlink_driver_from_carrier",
    "update_driver",
]
