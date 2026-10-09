#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Точка входа для работы с локальной базой данных SQLite.

Здесь осталась только инициализация базы и имена, которыми пользуется
проект: `init_database()`, `get_connection()` и функции справочников.
Сама работа разложена по модулям пакета:

  connection.py  — соединение и PRAGMA (WAL, busy_timeout, foreign_keys);
  schema.py      — таблицы и индексы;
  migrations.py  — доведение существующей базы до текущей схемы + бэкап;
  fts.py         — полнотекстовый поиск FTS5 (регистр по кириллице);
  salons.py      — справочник салонов («Места выгрузок») из Excel;
  crud/          — запросы по таблицам (водители, организации, адреса, …).

ВАЖНО: `db.database` остаётся точкой входа. Приложение, генераторы и тесты
импортируют имена ОТСЮДА, о внутреннем устройстве пакета им знать не нужно.

Путь к базе (`DB_PATH`) принадлежит этому модулю: тесты и рабочие
инструменты подменяют его на уровне модуля, поэтому соединение спрашивает
путь у нас (см. db/connection.py::set_db_path_provider).
"""

import datetime
import logging
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from core import audit
from core.security import backup_database, restrict_to_current_user

from db import connection, fts, migrations, salons, schema
from db.connection import DB_PATH as _DEFAULT_DB_PATH
from db.connection import current_db_path, get_connection
from db.crud.addresses import (
    count_addresses,
    delete_address,
    get_addresses,
    import_addresses_from_list,
    save_address,
    update_address,
)
from db.crud.search import ensure_fts_fresh as _ensure_fts_fresh
from db.fts import rebuild_fts_index
from db.migrations import _needs_migration
from db.salons import (
    SALONS_XLSX_NAME,
    SALON_COLUMN_KEYS,
    read_salons_rows,
    salons_xlsx_path,
)
from db.schema import INDEXES, SOFT_DELETE_TABLES

logger = logging.getLogger("db.database")

DB_PATH = _DEFAULT_DB_PATH


def _db_path_for_connection() -> str:
    """
    Путь к базе для новых соединений — из ЭТОГО модуля.

    Тесты и инструменты подменяют `db.database.DB_PATH`; чтобы подмена
    действовала, модуль соединения спрашивает путь здесь (см. db/connection.py).
    """
    return DB_PATH


# Регистрируем источник пути: с этого момента connection.get_connection()
# открывает именно тот файл, на который указывает DB_PATH этого модуля.
connection.set_db_path_provider(_db_path_for_connection)


# ─────────────────────────────────────────────────────────────
# Инициализация базы
# ─────────────────────────────────────────────────────────────

def init_database() -> None:
    """
    Инициализирует таблицы в БД и добавляет недостающие колонки.

    Порядок тот же, что был: таблицы → миграции (с бэкапом) → индексы →
    city → FTS5 → закрытие соединения → справочник салонов → права на файлы.
    Резервное копирование и ограничение прав берутся ИЗ ЭТОГО модуля: тесты
    подменяют их здесь (tests/conftest.py::isolated_db).
    """
    conn = get_connection()
    schema.create_all(conn)
    migrations.run(conn, backup=backup_database, db_path=DB_PATH)

    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")
    audit.log_event("database_initialized", db=os.path.basename(DB_PATH))

    # ── Справочник салонов при первом запуске (ШАГ FIX-2.2, п. C.9) ──
    # Импорт идёт ПОСЛЕ закрытия основного соединения: _load_salons_if_empty
    # работает своим соединением, а два писателя на одной базе не нужны.
    # Файл читается и на НЕпустом справочнике, если в нём нет ни одного кода
    # салона: так база, набранная до FIX-2.2, получает наименования и адреса
    # из справочника, а её собственные записи остаются на месте.
    _load_salons_if_empty()

    # ── Права на файлы базы (Шаг 5 задания) ──
    # contracts.db содержит персональные данные водителей, поэтому доступ
    # оставляем только текущему пользователю (файл + WAL/SHM при наличии).
    migrations.secure_files(DB_PATH, restrict_to_current_user)


def _load_salons_if_empty() -> int:
    """
    Первый запуск: если справочник салонов ещё не залит, читает файл.

    Путь к файлу берётся ЗДЕСЬ (salons_xlsx_path), а не внутри db/salons.py:
    тесты подменяют `db.database.salons_xlsx_path`.

    :return: сколько записей импортировано (0 — импорта не было)
    """
    return salons.load_if_empty(salons_xlsx_path())


# ─────────────────────────────────────────────────────────────
# CRUD: Водители
# ─────────────────────────────────────────────────────────────

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
                _ensure_fts_fresh(conn, "fts_drivers")
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


# ─────────────────────────────────────────────────────────────
# CRUD: Привязка водителей к перевозчикам
# ─────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────
# CRUD: Тягач и прицеп водителя
# ─────────────────────────────────────────────────────────────
def save_driver_vehicle(driver_id: int, vehicle_data: Dict[str, Any]) -> bool:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM driver_vehicles WHERE driver_id = ?", (driver_id,))
        row = cursor.fetchone()

        if row:
            cursor.execute("""
                UPDATE driver_vehicles SET
                    tractor_brand = ?, tractor_plate = ?, tractor_color = ?, tractor_year = ?,
                    trailer_brand = ?, trailer_plate = ?, trailer_color = ?, trailer_year = ?
                WHERE driver_id = ?
            """, (
                vehicle_data.get("tractor_brand", ""),
                vehicle_data.get("tractor_plate", ""),
                vehicle_data.get("tractor_color", ""),
                vehicle_data.get("tractor_year", ""),
                vehicle_data.get("trailer_brand", ""),
                vehicle_data.get("trailer_plate", ""),
                vehicle_data.get("trailer_color", ""),
                vehicle_data.get("trailer_year", ""),
                driver_id,
            ))
        else:
            cursor.execute("""
                INSERT INTO driver_vehicles (
                    driver_id,
                    tractor_brand, tractor_plate, tractor_color, tractor_year,
                    trailer_brand, trailer_plate, trailer_color, trailer_year
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                driver_id,
                vehicle_data.get("tractor_brand", ""),
                vehicle_data.get("tractor_plate", ""),
                vehicle_data.get("tractor_color", ""),
                vehicle_data.get("tractor_year", ""),
                vehicle_data.get("trailer_brand", ""),
                vehicle_data.get("trailer_plate", ""),
                vehicle_data.get("trailer_color", ""),
                vehicle_data.get("trailer_year", ""),
            ))
        conn.commit()
        logger.info(f"Тягач/прицеп сохранены для водителя ID={driver_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения ТС: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def load_driver_vehicle(driver_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM driver_vehicles WHERE driver_id = ?", (driver_id,))
    row = cursor.fetchone()
    cols = [desc[0] for desc in cursor.description] if cursor.description else []
    conn.close()
    return dict(zip(cols, row)) if row else None


# ─────────────────────────────────────────────────────────────
# CRUD: Организации
# ─────────────────────────────────────────────────────────────

def save_organization(org_data: Dict[str, Any], is_carrier: bool = False) -> int:
    conn = get_connection()
    cursor = conn.cursor()

    if is_carrier:
        cursor.execute("""
            INSERT INTO carriers (
                full_name, short_name, inn, kpp, ogrn,
                legal_address, actual_address, bank_account,
                bik, correspondent_account, bank_name,
                director_name, director_position, phone, email,
                license_number, license_date, entity_type, basis
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            org_data.get("full_name", ""),
            org_data.get("short_name", ""),
            org_data.get("inn", ""),
            org_data.get("kpp", ""),
            org_data.get("ogrn", ""),
            org_data.get("legal_address", ""),
            org_data.get("actual_address", ""),
            org_data.get("bank_account", ""),
            org_data.get("bik", ""),
            org_data.get("correspondent_account", ""),
            org_data.get("bank_name", ""),
            org_data.get("director_name", ""),
            org_data.get("director_position", ""),
            org_data.get("phone", ""),
            org_data.get("email", ""),
            org_data.get("license_number", ""),
            org_data.get("license_date", ""),
            org_data.get("entity_type", ""),
            org_data.get("basis", ""),
        ))
    else:
        cursor.execute("""
            INSERT INTO customers (
                full_name, short_name, inn, kpp, ogrn,
                legal_address, actual_address, bank_account,
                bik, correspondent_account, bank_name,
                director_name, director_position, phone, email,
                entity_type, basis
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            org_data.get("full_name", ""),
            org_data.get("short_name", ""),
            org_data.get("inn", ""),
            org_data.get("kpp", ""),
            org_data.get("ogrn", ""),
            org_data.get("legal_address", ""),
            org_data.get("actual_address", ""),
            org_data.get("bank_account", ""),
            org_data.get("bik", ""),
            org_data.get("correspondent_account", ""),
            org_data.get("bank_name", ""),
            org_data.get("director_name", ""),
            org_data.get("director_position", ""),
            org_data.get("phone", ""),
            org_data.get("email", ""),
            org_data.get("entity_type", ""),
            org_data.get("basis", ""),
        ))

    org_id = cursor.lastrowid
    # FTS5: индекс обновляем вручную (триггеров нет).
    fts.replace_row(
        conn,
        "fts_carriers" if is_carrier else "fts_customers",
        org_id,
        (
            org_data.get("full_name", ""),
            org_data.get("short_name", ""),
            org_data.get("inn", ""),
            org_data.get("director_name", ""),
        ),
    )
    conn.commit()
    conn.close()
    logger.info(f"Организация сохранена: ID={org_id}")
    return org_id


def update_organization(org_id: int, org_data: Dict[str, Any], is_carrier: bool = False) -> bool:
    conn = None
    table = "carriers" if is_carrier else "customers"
    fts_name = "fts_carriers" if is_carrier else "fts_customers"
    fts_fields = ("full_name", "short_name", "inn", "director_name")
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # Старые значения для FTS5: удаление из индекса идёт по ним.
        old = cursor.execute(
            f"SELECT {', '.join(fts_fields)} FROM {table} WHERE id = ?", (org_id,)
        ).fetchone()

        if is_carrier:
            cursor.execute("""
                UPDATE carriers SET
                    full_name = ?, short_name = ?, inn = ?, kpp = ?, ogrn = ?,
                    legal_address = ?, actual_address = ?, bank_account = ?,
                    bik = ?, correspondent_account = ?, bank_name = ?,
                    director_name = ?, director_position = ?, phone = ?, email = ?,
                    license_number = ?, license_date = ?,
                    entity_type = ?, basis = ?
                WHERE id = ?
            """, (
                org_data.get("full_name", ""), org_data.get("short_name", ""),
                org_data.get("inn", ""), org_data.get("kpp", ""),
                org_data.get("ogrn", ""), org_data.get("legal_address", ""),
                org_data.get("actual_address", ""), org_data.get("bank_account", ""),
                org_data.get("bik", ""), org_data.get("correspondent_account", ""),
                org_data.get("bank_name", ""), org_data.get("director_name", ""),
                org_data.get("director_position", ""), org_data.get("phone", ""),
                org_data.get("email", ""), org_data.get("license_number", ""),
                org_data.get("license_date", ""),
                org_data.get("entity_type", ""), org_data.get("basis", ""),
                org_id,
            ))
        else:
            cursor.execute("""
                UPDATE customers SET
                    full_name = ?, short_name = ?, inn = ?, kpp = ?, ogrn = ?,
                    legal_address = ?, actual_address = ?, bank_account = ?,
                    bik = ?, correspondent_account = ?, bank_name = ?,
                    director_name = ?, director_position = ?, phone = ?, email = ?,
                    entity_type = ?, basis = ?
                WHERE id = ?
            """, (
                org_data.get("full_name", ""), org_data.get("short_name", ""),
                org_data.get("inn", ""), org_data.get("kpp", ""),
                org_data.get("ogrn", ""), org_data.get("legal_address", ""),
                org_data.get("actual_address", ""), org_data.get("bank_account", ""),
                org_data.get("bik", ""), org_data.get("correspondent_account", ""),
                org_data.get("bank_name", ""), org_data.get("director_name", ""),
                org_data.get("director_position", ""), org_data.get("phone", ""),
                org_data.get("email", ""),
                org_data.get("entity_type", ""), org_data.get("basis", ""),
                org_id,
            ))
        if old:
            fts.replace_row(
                conn, fts_name, org_id,
                tuple(org_data.get(f, "") for f in fts_fields),
                old,
            )
        conn.commit()
        logger.info(f"Организация обновлена: ID={org_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления организации: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def search_organizations(
    search_term: str,
    is_carrier: bool = False,
    limit: int = 50,
    include_deleted: bool = False,
) -> List[Dict[str, Any]]:
    """
    Поиск организаций по наименованию, ИНН или ФИО руководителя.

    Шаг 5 оптимизации: поиск по руководителю добавлен в SQL — раньше UI
    добирал такие записи, выгружая всю таблицу организаций и фильтруя её
    в Python на каждое нажатие клавиши.

    Словесные запросы («ромашка») идут через FTS5 и не зависят от регистра
    («Ромашка», «РОМАШКА»). Запросы с цифрами (ИНН, ОГРН), а также случаи,
    когда FTS ничего не нашёл, обслуживает прежний LIKE.

    include_deleted=True показывает и мягко удалённые записи.
    """
    table = "carriers" if is_carrier else "customers"
    fts_name = "fts_carriers" if is_carrier else "fts_customers"
    conn = get_connection()
    cursor = conn.cursor()

    try:
        only_active = 1 if include_deleted else 0

        # ── Путь 1: FTS5 по наименованию/ИНН/руководителю ──
        match_query = fts.match_query_for(search_term) if search_term else None
        if match_query and fts.fts5_available(conn):
            try:
                _ensure_fts_fresh(conn, fts_name)
                cursor.execute(
                    f"SELECT o.* FROM {table} o "
                    f"JOIN {fts_name} f ON f.rowid = o.id "
                    f"WHERE {fts_name} MATCH ? AND (o.is_deleted = 0 OR ?) "
                    f"ORDER BY o.full_name COLLATE NOCASE LIMIT ?",
                    (match_query, only_active, int(limit)),
                )
                rows = cursor.fetchall()
                if rows:
                    cols = [desc[0] for desc in cursor.description]
                    logger.debug(
                        f"Поиск организаций (FTS5, {table}): {search_term!r} -> "
                        f"{match_query!r}, найдено {len(rows)}"
                    )
                    return [dict(zip(cols, row)) for row in rows]
            except sqlite3.DatabaseError as e:
                logger.warning(
                    f"FTS5-поиск организаций не удался, откат на LIKE: {e}"
                )

        # ── Путь 2: LIKE (как раньше) ──
        like = f"%{search_term}%"
        cursor.execute(
            f"SELECT * FROM {table} "
            f"WHERE (is_deleted = 0 OR ?) AND ("
            f"      full_name LIKE ? "
            f"   OR COALESCE(inn, '') LIKE ? "
            f"   OR COALESCE(director_name, '') LIKE ?) "
            f"ORDER BY full_name COLLATE NOCASE LIMIT ?",
            (only_active, like, like, like, int(limit)),
        )
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def get_all_organizations(
    is_carrier: bool = False,
    include_deleted: bool = False,
) -> List[Dict[str, Any]]:
    """Все организации; по умолчанию без мягко удалённых."""
    table = "carriers" if is_carrier else "customers"
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT * FROM {table} WHERE is_deleted = 0 OR ? "
        f"ORDER BY full_name COLLATE NOCASE",
        (1 if include_deleted else 0,),
    )
    rows = cursor.fetchall()
    cols = [desc[0] for desc in cursor.description]
    conn.close()
    return [dict(zip(cols, row)) for row in rows]


def load_organization(
    org_id: int,
    is_carrier: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Организация по ID — включая мягко удалённую (is_deleted = 1).

    Нужна, чтобы подставить в форму перевозчика, на которого закреплён
    водитель (drivers.default_carrier_id): по ID запись отдаётся даже
    убранной из справочника — ссылка договора и привязка водителя должны
    её пережить.
    """
    table = "carriers" if is_carrier else "customers"
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT * FROM {table} WHERE id = ?", (org_id,))
        row = cursor.fetchone()
        if not row:
            return None
        cols = [desc[0] for desc in cursor.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def load_organization_by_id(
    org_id: int,
    is_carrier: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Организация по ID — включая мягко удалённую.

    Нужна, когда нужно подтянуть перевозчика по `default_carrier_id` из
    карточки водителя. Скрытие удалённых делает не эта функция, а
    списки/поиск.

    Реализация — тонкая обёртка над `load_organization`: тело запроса уже
    живёт там, второй копии SQL в модуле не нужно. Имя оставлено явным
    («by_id»), потому что рядом есть `find_organization_id` — поиск по
    РЕКВИЗИТАМ, а не по номеру записи.
    """
    return load_organization(org_id, is_carrier=is_carrier)


def find_organization_id(
    org_data: Dict[str, Any],
    is_carrier: bool = False,
) -> Optional[int]:
    """
    ID существующей организации по её реквизитам.

    Ищет по ИНН (главный ключ), затем по полному наименованию.
    Возвращает None, если запись НЕ найдена. НИКОГДА не создаёт
    новую запись.

    Мягко удалённые (is_deleted=1) тоже находятся — ссылка в договоре
    должна пережить мягкое удаление из справочника.

    :param org_data: словарь с реквизитами (inn, full_name, ...)
    :param is_carrier: True → carriers, False → customers
    """
    table = "carriers" if is_carrier else "customers"

    inn = str(org_data.get("inn") or "").strip()
    full_name = str(org_data.get("full_name") or "").strip()

    if not inn and not full_name:
        return None

    conn = get_connection()
    try:
        cursor = conn.cursor()

        # 1. По ИНН (самый надёжный ключ)
        if inn:
            cursor.execute(
                f"SELECT id FROM {table} WHERE inn = ? LIMIT 1",
                (inn,),
            )
            row = cursor.fetchone()
            if row:
                return int(row[0])

        # 2. По полному наименованию (если ИНН пуст или не нашёлся)
        if full_name:
            cursor.execute(
                f"SELECT id FROM {table} "
                f"WHERE full_name = ? COLLATE NOCASE LIMIT 1",
                (full_name,),
            )
            row = cursor.fetchone()
            if row:
                return int(row[0])

        return None
    finally:
        conn.close()


def restore_organization(org_id: int, is_carrier: bool = False) -> bool:
    """Возвращает мягко удалённую организацию в справочник (вариант В)."""
    table = "carriers" if is_carrier else "customers"
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE {table} SET is_deleted = 0 WHERE id = ?", (org_id,)
        )
        conn.commit()
        logger.info(f"Организация восстановлена: ID={org_id} ({table})")
        audit.log_event(
            "organization_restored", org_id=org_id,
            entity="carrier" if is_carrier else "customer",
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка восстановления организации: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def delete_organization(org_id: int, is_carrier: bool = False) -> bool:
    """
    Мягкое удаление организации (вариант В): is_deleted = 1.

    Запись остаётся в базе, ссылки contracts.customer_id / carrier_id
    сохраняются, ТС перевозчика не удаляются — запись можно вернуть
    через restore_organization().
    """
    table = "carriers" if is_carrier else "customers"
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            f"UPDATE {table} SET is_deleted = 1 WHERE id = ?", (org_id,)
        )
        conn.commit()
        logger.info(f"Организация удалена (мягко): ID={org_id} ({table})")
        audit.log_event(
            "organization_deleted", org_id=org_id,
            entity="carrier" if is_carrier else "customer",
            entities="soft_delete",
        )
        return True
    except Exception as e:
        logger.error(f"Ошибка удаления организации: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


# ─────────────────────────────────────────────────────────────
# CRUD: КОНТРАГЕНТЫ (шаг 3 инфраструктуры типов договоров)
# ─────────────────────────────────────────────────────────────
# Таблица counterparties независима от carriers/customers: контрагент
# привязан к типу договора (contract_type) и роли стороны (role).
# «Экспедиторство» продолжает работать со своими таблицами — здесь только
# новый справочник для будущих типов (Формика, Логистикс Рус, аренда).

#: Редактируемые поля контрагента: один список для INSERT и UPDATE,
#: чтобы наборы колонок в запросах не разъезжались.
COUNTERPARTY_FIELDS: Tuple[str, ...] = (
    "contract_type", "role", "full_name", "short_name", "inn", "kpp", "ogrn",
    "legal_address", "actual_address", "bank_account", "bik",
    "correspondent_account", "bank_name", "director_name",
    "director_position", "phone", "email",
)


def _counterparty_values(data: Dict[str, Any]) -> List[Any]:
    """
    Значения полей контрагента в порядке COUNTERPARTY_FIELDS.

    Пустой ИНН пишется как NULL, а не как пустая строка: в SQLite пустые
    строки конфликтуют в UNIQUE(contract_type, role, inn) между собой,
    и второго контрагента без ИНН (физлицо, ИП без ИНН в тексте) было бы
    не сохранить. NULL в UNIQUE-ограничении не конфликтует.
    """
    values: List[Any] = []
    for name in COUNTERPARTY_FIELDS:
        value = data.get(name)
        if name == "inn":
            text = str(value).strip() if value is not None else ""
            values.append(text or None)
        else:
            values.append("" if value is None else value)
    return values


def _validate_counterparty(data: Dict[str, Any]) -> None:
    """Обязательные поля контрагента: тип договора, роль и наименование."""
    for name, title in (
        ("contract_type", "тип договора"),
        ("role", "роль"),
        ("full_name", "наименование"),
    ):
        if not str(data.get(name) or "").strip():
            raise ValueError(f"Контрагент: не заполнено обязательное поле «{title}»")


def save_counterparty(data: Dict[str, Any]) -> int:
    """
    Добавляет контрагента в справочник и возвращает его ID.

    Дубль по (contract_type, role, inn) не сохраняется: sqlite3.IntegrityError
    пробрасывается наружу, чтобы вызывающий код мог показать понятное
    сообщение. Ошибка не оставляет открытого соединения (finally).
    """
    _validate_counterparty(data)

    conn = get_connection()
    try:
        cursor = conn.cursor()
        columns = ", ".join(COUNTERPARTY_FIELDS)
        placeholders = ", ".join("?" for _ in COUNTERPARTY_FIELDS)
        cursor.execute(
            f"INSERT INTO counterparties ({columns}) VALUES ({placeholders})",
            _counterparty_values(data),
        )
        cp_id = cursor.lastrowid
        conn.commit()
        logger.info(
            f"Контрагент сохранён: ID={cp_id} "
            f"({data.get('contract_type')}/{data.get('role')})"
        )
        return cp_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_counterparty(cp_id: int, data: Dict[str, Any]) -> bool:
    """
    Обновляет запись контрагента. False — запись не найдена или ошибка БД.

    Поля, которых нет в data, очищаются (как в update_organization):
    вызывающий код передаёт полный набор данных формы.
    """
    _validate_counterparty(data)

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        assignments = ", ".join(f"{name} = ?" for name in COUNTERPARTY_FIELDS)
        cursor.execute(
            f"UPDATE counterparties SET {assignments} WHERE id = ?",
            (*_counterparty_values(data), int(cp_id)),
        )
        updated = cursor.rowcount > 0
        conn.commit()
        if updated:
            logger.info(f"Контрагент обновлён: ID={cp_id}")
        else:
            logger.warning(f"Контрагент не найден для обновления: ID={cp_id}")
        return updated
    except Exception as e:
        logger.error(f"Ошибка обновления контрагента ID={cp_id}: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def load_counterparty(cp_id: int) -> Optional[Dict[str, Any]]:
    """Контрагент по ID или None."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM counterparties WHERE id = ?", (int(cp_id),))
        row = cursor.fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in cursor.description]
        return dict(zip(cols, row))
    finally:
        conn.close()


def get_all_counterparties(
    contract_type: str = "",
    role: str = "",
    include_deleted: bool = False,
) -> List[Dict[str, Any]]:
    """
    Контрагенты справочника.

    Пустые contract_type / role означают «без фильтра»; по умолчанию
    мягко удалённые записи не показываются (include_deleted=True — показать).
    """
    sql = "SELECT * FROM counterparties WHERE 1 = 1"
    params: List[Any] = []

    if not include_deleted:
        sql += " AND is_deleted = 0"
    if contract_type:
        sql += " AND contract_type = ?"
        params.append(str(contract_type))
    if role:
        sql += " AND role = ?"
        params.append(str(role))
    sql += " ORDER BY full_name COLLATE NOCASE"

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def search_counterparties(
    contract_type: str,
    role: str,
    search_term: str,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """
    Поиск контрагентов по наименованию, ИНН или руководителю (LIKE).

    FTS5 для этого справочника не заводится (шаг 3 инфраструктуры):
    объём записей небольшой, а LIKE даёт предсказуемый результат
    и на подстроке, и на цифрах ИНН. Пустые contract_type / role —
    поиск по всем типам и ролям.
    """
    like = f"%{search_term or ''}%"

    sql = (
        "SELECT * FROM counterparties "
        "WHERE is_deleted = 0 AND ("
        "      full_name LIKE ? "
        "   OR COALESCE(short_name, '') LIKE ? "
        "   OR COALESCE(inn, '') LIKE ? "
        "   OR COALESCE(director_name, '') LIKE ?)"
    )
    params: List[Any] = [like, like, like, like]

    if contract_type:
        sql += " AND contract_type = ?"
        params.append(str(contract_type))
    if role:
        sql += " AND role = ?"
        params.append(str(role))
    sql += " ORDER BY full_name COLLATE NOCASE LIMIT ?"
    params.append(int(limit))

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]
    finally:
        conn.close()


def delete_counterparty(cp_id: int) -> bool:
    """
    Мягкое удаление контрагента: is_deleted = 1 (как у организаций).

    Запись остаётся в базе и возвращается через restore_counterparty().
    """
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE counterparties SET is_deleted = 1 WHERE id = ?", (int(cp_id),)
        )
        deleted = cursor.rowcount > 0
        conn.commit()
        if deleted:
            logger.info(f"Контрагент удалён (мягко): ID={cp_id}")
            audit.log_event(
                "counterparty_deleted", cp_id=cp_id, entities="soft_delete"
            )
        else:
            logger.warning(f"Контрагент не найден для удаления: ID={cp_id}")
        return deleted
    except Exception as e:
        logger.error(f"Ошибка удаления контрагента ID={cp_id}: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


def restore_counterparty(cp_id: int) -> bool:
    """Возвращает мягко удалённого контрагента в справочник."""
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE counterparties SET is_deleted = 0 WHERE id = ?", (int(cp_id),)
        )
        restored = cursor.rowcount > 0
        conn.commit()
        if restored:
            logger.info(f"Контрагент восстановлен: ID={cp_id}")
        else:
            logger.warning(f"Контрагент не найден для восстановления: ID={cp_id}")
        return restored
    except Exception as e:
        logger.error(f"Ошибка восстановления контрагента ID={cp_id}: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()


# ─────────────────────────────────────────────────────────────
# CRUD: ТС
# ─────────────────────────────────────────────────────────────

def save_vehicles(
    vehicles: List[Dict[str, Any]],
    carrier_id: Optional[int] = None,
    contract_id: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> List[int]:
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    cursor = conn.cursor()
    ids = []
    for vehicle in vehicles:
        cursor.execute("""
            INSERT INTO vehicles (carrier_id, contract_id, vin, brand_model, plate_number, year, color, vehicle_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            carrier_id,
            contract_id,
            vehicle.get("vin", ""),
            vehicle.get("brand_model", ""),
            vehicle.get("plate_number", ""),
            vehicle.get("year", 0),
            vehicle.get("color", ""),
            vehicle.get("vehicle_type", "Тягач"),
        ))
        ids.append(cursor.lastrowid)
    if own_connection:
        conn.commit()
        conn.close()
    logger.info(f"Сохранено ТС: {len(ids)}")
    return ids


# ─────────────────────────────────────────────────────────────
# Сохранение договора + точек
# ─────────────────────────────────────────────────────────────

def save_contract(contract_data: Dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> int:
    """
    Сохраняет шапку договора.

    Предоплата (ШАГ «Предоплата»): пишутся сумма `prepayment_amount` и
    процент `prepayment_percent`. Старые вызовы без этих ключей работают
    как раньше — в колонки уходит 0.
    """
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO contracts (
            contract_number, contract_date, start_date, end_date,
            route, price_without_vat, vat_rate, price_with_vat,
            currency, special_conditions, driver_id, customer_id, carrier_id,
            prepayment_amount, prepayment_percent
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        contract_data.get("number", ""),
        contract_data.get("date", ""),
        contract_data.get("start_date", ""),
        contract_data.get("end_date", ""),
        contract_data.get("route", ""),
        contract_data.get("price_without_vat", 0),
        contract_data.get("vat_rate", "20%"),
        contract_data.get("price_with_vat", 0),
        contract_data.get("currency", "RUB"),
        contract_data.get("special_conditions", ""),
        contract_data.get("driver_id", None),
        contract_data.get("customer_id", None),
        contract_data.get("carrier_id", None),
        contract_data.get("prepayment_amount", 0) or 0,
        contract_data.get("prepayment_percent", 0) or 0,
    ))
    contract_id = cursor.lastrowid
    if own_connection:
        conn.commit()
        conn.close()
    logger.info(f"Договор сохранён: ID={contract_id}")
    return contract_id


def save_contract_points(
    contract_id: int, loadings: List[Dict], unloadings: List[Dict],
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """
    Сохраняет точки маршрута договора (ШАГ FIX-6, часть F).

    У точки сохраняется НАИМЕНОВАНИЕ салона (`name`) — раньше колонки не
    было, и после перезагрузки договора из базы имя терялось, хотя в бланк
    попадало из формы. Точка без имени пишется пустой строкой: старые
    вызовы (без ключа `name`) работают как раньше.
    """
    own_connection = conn is None
    if conn is None:
        conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM contract_points WHERE contract_id = ?", (contract_id,))
    for i, l in enumerate(loadings):
        cursor.execute(
            "INSERT INTO contract_points "
            "(contract_id, point_type, sort_order, name, address, date, time_window) "
            "VALUES (?, 'loading', ?, ?, ?, ?, ?)",
            (contract_id, i, l.get("name", "") or "", l.get("address", ""),
             l.get("date", ""), l.get("time_window", ""))
        )
    for i, u in enumerate(unloadings):
        cursor.execute(
            "INSERT INTO contract_points "
            "(contract_id, point_type, sort_order, name, address, date, time_window) "
            "VALUES (?, 'unloading', ?, ?, ?, ?, ?)",
            (contract_id, i, u.get("name", "") or "", u.get("address", ""),
             u.get("date", ""), u.get("time_window", ""))
        )
    if own_connection:
        conn.commit()
        conn.close()


def save_contract_with_details(
    contract_data: Dict[str, Any], loadings: List[Dict],
    unloadings: List[Dict], vehicles: List[Dict[str, Any]],
) -> int:
    """Сохраняет договор, маршрут и транспорт одной транзакцией."""
    conn = get_connection()
    try:
        contract_id = save_contract(contract_data, conn=conn)
        save_contract_points(contract_id, loadings, unloadings, conn=conn)
        if vehicles:
            save_vehicles(vehicles, contract_id=contract_id, conn=conn)
        conn.commit()
        return contract_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_contract_points(contract_id: int) -> Dict[str, List[Dict]]:
    """
    Читает точки маршрута договора.

    Точка возвращается ЧЕТЫРЬМЯ полями — с наименованием салона (`name`,
    ШАГ FIX-6, часть F). У договоров, сохранённых до миграции, колонка
    добавлена пустой, поэтому имя приходит пустой строкой: данные не
    теряются и форма заполняется как раньше.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT point_type, sort_order, name, address, date, time_window "
        "FROM contract_points WHERE contract_id = ? ORDER BY point_type, sort_order",
        (contract_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    loadings, unloadings = [], []
    for ptype, order, name, addr, date, tw in rows:
        item = {
            "name": name or "",
            "address": addr or "",
            "date": date or "",
            "time_window": tw or "",
        }
        if ptype == "loading":
            loadings.append(item)
        else:
            unloadings.append(item)
    return {"loadings": loadings, "unloadings": unloadings}
