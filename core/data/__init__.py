# -*- coding: utf-8 -*-
"""
Модель данных договора и правила расчёта (НДС, суммы).

Ядро: не знает ни про БД, ни про Qt, ни про внешние API.

Импорты через реэкспорт:
    from core.data.contract_data import ContractData
    from core.data.vat import compute_vat
Или через старый путь:
    from core.contract_data import ContractData
"""
