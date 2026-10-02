"""Local layouts for identity, vehicle and bank documents. No digit repair."""
import re

DATE = r"\b[0-9]{2}[./-][0-9]{2}[./-][0-9]{4}\b"
VIN = r"\b[A-HJ-NPR-Z0-9]{17}\b"


def _lines(text):
    return [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]


def _following(lines, pattern, limit=2):
    """Label remainder and nearby lines, bounded so unrelated blocks cannot leak in."""
    for i, line in enumerate(lines):
        match = re.search(pattern, line, re.I)
        if match:
            yield [line[match.end():].strip(" :|—"), *lines[i+1:i+1+limit]]


def _one(pattern, text, flags=0):
    values = list(dict.fromkeys(re.findall(pattern, text, flags)))
    return values[0] if len(values) == 1 else ""


def _label_date(lines, pattern):
    for region in _following(lines, pattern, 2):
        for line in region:
            value = re.search(DATE, line)
            if value:
                return value.group()
    return ""


def _name_part(lines, label):
    for region in _following(lines, label, 2):
        for line in region:
            # Keep Cyrillic names only; skip bilingual transliterations and labels.
            words = re.findall(r"\b[А-ЯЁ][А-ЯЁа-яё-]{2,}\b", line)
            excluded = {"ФАМИЛИЯ", "ИМЯ", "ОТЧЕСТВО", "РОССИЯ", "МЕСТО", "ДАТА", "ПОЛ", "РОЖДЕНИЯ", "МУЖ", "ЖЕН"}
            words = [w for w in words if w.upper() not in excluded]
            if len(words) == 1:
                return words[0]
    return ""


def _record(section, kind, fields):
    fields = {k: v for k, v in fields.items() if v}
    if not any(not k.startswith("_") for k in fields):
        return {}
    return {section: [dict(fields, _kind=kind)]}


def _bank(lines, text):
    fields = {}
    patterns = {
        "bank_account": r"номер\s+сч[её]та\s+получателя\s*[:|]?\s*([0-9 ]{20,30})\b",
        "bik": r"БИК(?:\s+банка)?(?:\s+получателя)?\s*[:|]?\s*([0-9]{9})\b",
        "correspondent_account": r"[КK]\s*[/\\]\s*[СC](?:\s+банка)?(?:\s+получателя)?\s*[:|]?\s*([0-9 ]{20,30})\b",
        "bank_name": r"^банк\s+получателя\s*[:|]\s*(.+)$",
    }
    for key, pattern in patterns.items():
        fields[key] = _one(pattern, text, re.I | re.M)
    # Employer/recipient are different roles. Only an explicit business name is a carrier candidate.
    fields["full_name"] = _one(r"^\s*(ИП\s+[А-ЯЁ][а-яёА-ЯЁ-]+\s+[А-ЯЁ][а-яёА-ЯЁ-]+\s+[А-ЯЁ][а-яёА-ЯЁ-]+)\s*$", text, re.M)
    # Deliberately do not extract "ИНН банка получателя" as the carrier's INN.
    fields["_note"] = "Банковские реквизиты: проверьте получателя и назначение счёта; ИНН банка не является ИНН перевозчика."
    return _record("carrier", "bank", fields)


def _passport(lines, text):
    fields = {}
    parts = [_name_part(lines, r"\b" + name + r"\b") for name in ("фамилия", "имя", "отчество")]
    if all(parts):
        fields["full_name"] = " ".join(parts)
    if not fields.get("full_name"):
        # On passports the pale labels often disappear, but three separate printed name rows remain.
        candidates = []
        for line in lines:
            words = re.findall(r"\b[А-ЯЁ]{3,}\b", line)
            if len(words) == 1:
                candidates.append(words[0])
        names = []
        for i in range(len(candidates)-2):
            surname, given, patronymic = candidates[i:i+3]
            if re.search(r"(?:ОВ|ЕВ|ИН|ОВА|ЕВА|ИНА|ЯН|КО)$", surname) and re.search(r"(?:ОВИЧ|ЕВИЧ|ОВНА|ЕВНА)$", patronymic):
                names.append(" ".join((surname, given, patronymic)))
        if len(set(names)) == 1:
            fields["full_name"] = names[0]
    fields["passport_issue_date"] = _label_date(lines, r"дата\s+выдачи")
    fields["birth_date"] = _label_date(lines, r"дата\s+рождения")
    fields["passport_code"] = _one(r"\b[0-9]{3}[-–][0-9]{3}\b", text)
    # Issuer is independently labelled by the authority, even when "Паспорт выдан" is faint.
    fields["passport_issuer"] = _one(r"(?m)^.*\b((?:МВД|УФМС)\s+[^\n]+)$", text)
    for region in _following(lines, r"паспорт\s+выдан", 3):
        issuer = [line for line in region if re.search(r"МВД|УФМС|ОТДЕЛ", line, re.I)]
        if issuer:
            fields["passport_issuer"] = " ".join(issuer)
    for region in _following(lines, r"место\s+рождения", 3):
        address = [line for line in region if line and not re.search(r"PNRUS|RUS|[0-9]{6}|[<]{2}", line)]
        if address:
            fields["birth_place"] = " ".join(address)
    if not fields.get("birth_date"):
        # A date immediately next to the sex field belongs to the personal page.
        for i, line in enumerate(lines):
            if re.search(r"\b(?:МУЖ|ЖЕН)[.,]?\b", line):
                fields["birth_date"] = _one(DATE, " ".join(lines[i:i+2]))
    if not fields.get("passport_issue_date"):
        # Use proximity to the subdivision code, not chronological guessing between dates.
        for i, line in enumerate(lines):
            if re.search(r"\b[0-9]{3}[-–][0-9]{3}\b", line):
                fields["passport_issue_date"] = _one(DATE, " ".join(lines[max(0,i-1):i+1]))
    # Accept only complete, horizontal series/number explicitly printed together.
    number = _one(r"(?m)^\s*([0-9]{2}\s+[0-9]{2}\s+[0-9]{6})\s*$", text)
    if number:
        fields["passport_series"] = " ".join(number.split()[:2])
        fields["passport_number"] = number.split()[2]
    return _record("driver", "passport", fields)


def _license(lines, text):
    fields = {}
    last = _one(r"(?m)^\s*1[.,]?\s+([А-ЯЁ][А-ЯЁа-яё-]{2,})\b", text)
    first = _one(r"(?m)^\s*2[.,]?\s+([А-ЯЁ][А-ЯЁа-яё-]+\s+[А-ЯЁ][А-ЯЁа-яё-]+)\b", text)
    if last and first:
        fields["full_name"] = last + " " + first
    if not fields.get("full_name"):
        # A numbered given-name row plus a single printed Cyrillic surname above it.
        for i, line in enumerate(lines):
            name = re.search(r"^\s*2[.,]?\s+([А-ЯЁ]{3,}\s+[А-ЯЁ]+(?:ОВИЧ|ЕВИЧ|ОВНА|ЕВНА))\b", line)
            if name:
                surnames = re.findall(r"\b[А-ЯЁ]{2,}(?:ОВ|ЕВ|ИН|ОВА|ЕВА|ИНА|КО|ЯН)\b", " ".join(lines[max(0,i-4):i]))
                if len(set(surnames)) == 1:
                    fields["full_name"] = surnames[0] + " " + name.group(1)
    fields["birth_date"] = _label_date(lines, r"^\s*3[.,]?\s*")
    fields["license_issue_date"] = _label_date(lines, r"4[аa][).:]?\s*")
    fields["license_expiry_date"] = _label_date(lines, r"4[бb][).:]?\s*")
    number = _one(r"(?m)^\s*5[.,]?\s+([0-9]{2}\s+[0-9]{2}\s+[0-9]{6})\b", text)
    if number:
        fields["license_series"] = " ".join(number.split()[:2])
        fields["license_number"] = number.split()[2]
    return _record("driver", "license", fields)


def _vehicle(lines, text):
    fields = {}
    # The generic caption "Категория ТС (ABCD, прицеп)" does not describe vehicle type.
    section = "tractor" if re.search(r"тягач|седельн", text, re.I) else (
        "trailer" if re.search(r"полуприцеп|автовоз|тип\s+тс\s*[:|]?\s*прицеп", text, re.I) else "vehicles")
    vins = list(dict.fromkeys(re.findall(VIN, text)))
    if len(vins) > 1:
        # Cannot know whether a photo contains multiple cars or an OCR disagreement.
        return {"vehicles": [{"vin": vin} for vin in vins]}
    if vins:
        fields["vin" if section == "vehicles" else "_vin"] = vins[0]
    for region in _following(lines, r"(?:государственный\s+)?регистраци[а-я]+\s+номер|registration\s*number", 2):
        for line in region:
            plate = _one(r"\b(?:[АВЕКМНОРСТУХABEKMHOPCTYX][0-9]{3}[АВЕКМНОРСТУХABEKMHOPCTYX]{2}[0-9]{2,3}|[0-9]{2}[A-Z]{2}[0-9]{3}[A-Z]{2})\b", line)
            if plate:
                fields["plate_number"] = plate
                break
            # Foreign plates may mix Cyrillic and Latin in OCR. Preserve that reading for
            # review; never transliterate lookalikes or turn letters into guessed digits.
            tokens = [v for v in re.findall(r"\b[А-ЯЁA-Z0-9]{6,12}\b", line)
                      if re.search(r"[0-9]", v) and re.search(r"[А-ЯЁA-Z]", v)]
            if len(tokens) == 1:
                fields["plate_number"] = tokens[0]
                break
    for region in _following(lines, r"год\s+выпуска(?:\s+ТС)?|year\s+of\s+manufacture", 2):
        year = _one(r"\b(?:19|20)[0-9]{2}\b", " ".join(region))
        if year:
            fields["year"] = year
    if not fields.get("year"):
        for i, line in enumerate(lines):
            if re.search(r"год\s+выпуска|year\s+of\s+manufacture", line, re.I):
                fields["year"] = _one(r"\b(?:19|20)[0-9]{2}\b", " ".join(lines[max(0,i-1):i+3]))
    # Colour vocabulary is categorical; no approximate string substitution.
    color = _one(r"\b(БЕЛЫЙ|СЕРЫЙ|ЧЕРНЫЙ|ЧЁРНЫЙ|СИНИЙ|КРАСНЫЙ|ЗЕЛЕНЫЙ|ЗЕЛЁНЫЙ|ЖЕЛТЫЙ|ЖЁЛТЫЙ|КОРИЧНЕВЫЙ)\b", text, re.I)
    fields["color"] = color
    for region in _following(lines, r"^марка(?:\s*[,/]\s*модель)?\s*|^модель\s*", 2):
        for line in region:
            if re.search(r"тип\s+тс|категор|год\s+выпуска|марка,|make|model", line, re.I):
                continue
            line = re.sub(r"^(?:марка|модель)\s+", "", line, flags=re.I)
            if re.search(r"[A-Z]{2,}", line) and not re.search(VIN, line) and not re.search(r"VIN|CERTIFICAT", line):
                fields["brand_model"] = line
                break
        if fields.get("brand_model"):
            break
    if not fields.get("brand_model"):
        for i, line in enumerate(lines):
            if re.search(r"марка.*модел|make.*model", line, re.I):
                candidates = [v for v in lines[max(0,i-1):i+3]
                              if re.fullmatch(r"[A-Z][A-Z0-9 .-]{3,}", v)
                              and re.search(r"[A-Z]{3,}", v)
                              and not re.search(r"VIN|MODEL|MAKE|YEAR|COLOUR|COLOR|NUMBER|IDENTIF|REGISTR|CERTIF", v)
                              and not re.search(VIN, v)]
                if candidates:
                    fields["brand_model"] = " ".join(candidates)
                    break
    if re.search(r"собственник|владелец|owner", text, re.I):
        fields["_note"] = "Собственник ТС не переносится в ФИО водителя. Роль ТС и связь сторон документа требуют проверки."
    record = _record(section, "vehicle", fields)
    owner = _vehicle_owner(lines)
    if owner:
        record["carrier"] = [_record("carrier", "vehicle_owner", owner)["carrier"][0]]
    return record


_OWNER_STOPWORDS = {
    "СОБСТВЕННИК", "ВЛАДЕЛЕЦ", "РЕСПУБЛИКА", "ОБЛАСТЬ", "КРАЙ", "РАЙОН",
    "РОССИЯ", "РОССИЙСКАЯ", "ГОРОД", "УЛИЦА", "ДОМ", "ОСОБЫЕ", "ОТМЕТКИ",
    "ГОСУДАРСТВЕННЫЙ", "РЕГИСТРАЦИОННЫЙ", "НОМЕР", "СВИДЕТЕЛЬСТВО",
    "ТРАНСПОРТНОГО", "СРЕДСТВА", "ПАСПОРТ", "ДАТА", "ВЫДАЧИ",
}


def _vehicle_owner(lines):
    """Owner block of a registration certificate: name and/or address only."""
    fields = {}
    for index, line in enumerate(lines):
        if not re.search(r"собственник|владелец|\bowner\b", line, re.I):
            continue
        region = [re.sub(r".*\)", "", line), *lines[index + 1:index + 5]]
        for candidate in region:
            words = [w for w in re.findall(r"\b[А-ЯЁ][А-ЯЁа-яё-]{1,}\b", candidate)
                     if w.upper() not in _OWNER_STOPWORDS]
            if len(words) >= 3:
                fields["full_name"] = " ".join(words[:3])
                break
        address = []
        for candidate in lines[index + 1:index + 12]:
            if re.search(r"особые\s+отметки|код\s+подразделения|подпись|\bГИБДД\b", candidate, re.I):
                break
            if re.fullmatch(r"(?:Корпус|Строение|Дом|Квартира|Улица|Район|Пункт)\s*\([^)]*\)", candidate, re.I):
                continue  # печатный заголовок бланка без значения
            if re.search(r"респ|обл\.|область|край|район|р-н|город|г\.\s|ул\.|улица|"
                         r"пр-кт|проспект|переулок|шоссе|дом|д\.\s|корп|кв\.|пункт",
                         candidate, re.I):
                address.append(candidate)
        if address:
            fields["legal_address"] = ", ".join(dict.fromkeys(address))
        if fields:
            break
    return fields


# ── реквизиты организаций ──────────────────────────────────────────────
# Spaced digit runs are common in FNS scans: "6 6 7 8 ..." is one number.
_DIGIT_SEPARATORS = re.compile(r"(?<=\d)[ \t\n|/]+(?=\d)")


def _packed(text):
    return _DIGIT_SEPARATORS.sub("", text)


def _unique_digit_run(text, length):
    """The only run of exactly `length` digits; None when ambiguous."""
    runs = {m.group(1) for m in re.finditer(r"(?<!\d)(\d{%d})(?!\d)" % length, _packed(text))}
    return runs.pop() if len(runs) == 1 else ""


def _org_full_name(text):
    patterns = (
        r"(?:ОБЩЕСТВО\s+С\s+ОГРАНИЧЕННОЙ\s+ОТВЕТСТВЕННОСТЬЮ|ООО)\s*[«\"']([^»\"'\n]{2,80})[»\"']",
        r"(?:АКЦИОНЕРНОЕ\s+ОБЩЕСТВО|АО|ПАО|ЗАО)\s*[«\"']([^»\"'\n]{2,80})[»\"']",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip(" |")
    return ""


#: Заголовки блоков, которые в сканах часто идут без двоеточия:
#: «Паспорт РФ», «Водительское удостоверение», «Адрес регистрации».
#: Без них захват значения уходил в следующий блок и склеивал адрес
#: с чужим заголовком.
_BLOCK_TITLES = (
    r"паспорт(?:\s+рф)?",
    r"водительское\s+удостоверение",
    r"удостоверение",
    r"адрес\s+регистрации",
    r"адрес\s+места\s+жительства",
    r"адрес",
    r"место\s+рождения",
    r"место\s+жительства",
    r"кем\s+выдан",
    r"код\s+подразделения",
    r"дата\s+выдачи",
    r"дата\s+рождения",
    r"седельный\s+тягач",
    r"тягач",
    r"полуприцеп",
    r"п/прицеп",
    r"прицеп",
    r"телефон",
    r"тел",
    r"водитель",
    r"собственник",
    r"владелец",
)

#: Новая метка: номерной пункт, строка с двоеточием или «голый» заголовок.
#: Номерной пункт требует заглавной буквы после номера — иначе строка
#: «45 12 345678, выдан 20.06.2015,» считалась меткой, и значение
#: (серия и номер паспорта, номер ВУ) терялось.
_NEW_LABEL = re.compile(
    r"^[0-9]+(?:\.[0-9]+)*[.)]?\s+(?=[А-ЯЁ])"
    r"|[А-ЯЁ][^:|]{2,40}\s*[:|]"
    r"|^(?:" + "|".join(_BLOCK_TITLES) + r")\b[^0-9]{0,60}$",
    re.I,
)


def _label_value(lines, pattern, limit=3, stop=_NEW_LABEL):
    """Remainder of a labelled line plus following lines, until the next label."""
    values = []
    for index, line in enumerate(lines):
        match = re.search(pattern, line, re.I)
        if not match:
            continue
        parts = [line[match.end():].strip(" :|—")]
        for candidate in lines[index + 1:index + 1 + limit]:
            if stop and stop.match(candidate):
                break
            parts.append(candidate.strip(" :|—"))
        values.append(" ".join(p for p in parts if p))
    values = [v for v in dict.fromkeys(values) if v and len(v) > 1]
    return values[0] if len(values) == 1 else ""


def _counterparty_card(lines, text):
    """Bank's 'Карта контрагента' form with numbered labelled values."""
    fields = {
        "full_name": _label_value(lines, r"1\.1\s*Полное\s+наименование", 2),
        "short_name": _label_value(lines, r"1\.2\s*Сокращенное\s+наименование", 1),
        "legal_address": _label_value(lines, r"2\.1\s*Юридический\s+адрес", 2),
        "actual_address": _label_value(lines, r"2\.2\s*Фактический[^\n]*адрес", 2),
        "email": _label_value(lines, r"2\.3\s*E-?mail", 1),
        "phone": _label_value(lines, r"^3\.\s*Контактные\s+телефоны", 1),
        "ogrn": _label_value(lines, r"4\.3\s*ОГРН", 1),
        "bank_name": _label_value(lines, r"5\.1\s*Наименование\s+банка", 2),
        "director_name": _label_value(lines, r"6\.2\s*Ф\.?\s*И\.?\s*О\.?", 1),
    }
    position = _label_value(lines, r"6\.1\s*Исполнительный\s+орган", 3)
    position = re.search(r"(Генеральный\s+директор|Директор|Президент|Управляющий)", position or "", re.I)
    fields["director_position"] = position.group(1) if position else ""
    inn_kpp = _label_value(lines, r"4\.4\s*ИНН/?КПП", 1)
    if inn_kpp:
        digits = re.findall(r"\d+", inn_kpp)
        if len(digits) >= 1 and len(digits[0]) == 10:
            fields["inn"] = digits[0]
        if len(digits) >= 2 and len(digits[1]) == 9:
            fields["kpp"] = digits[1]
        elif len(digits) == 1 and len(digits[0]) == 19:
            fields["inn"], fields["kpp"] = digits[0][:10], digits[0][10:]
    account = _label_value(lines, r"5\.2\s*Расчетный\s+счет", 1)
    fields["bank_account"] = " ".join(re.findall(r"\d{20}", account)) or ""
    fields["correspondent_account"] = _unique_digit_run(account, 20) or ""
    correspondent = _label_value(lines, r"5\.3\s*Корр\s*/?\s*счет", 1)
    if correspondent:
        fields["correspondent_account"] = _unique_digit_run(correspondent, 20) or fields["correspondent_account"]
    fields["bik"] = _label_value(lines, r"5\.4\s*БИК", 1)
    return _record("carrier", "counterparty_card", fields)


def _registration_certificate(lines, text):
    """FNS certificate of registration: name plus spaced ИНН/КПП and ОГРН."""
    fields = {"full_name": _org_full_name(text)}
    inn_kpp = _unique_digit_run(text, 19)
    if inn_kpp:
        fields["inn"], fields["kpp"] = inn_kpp[:10], inn_kpp[10:]
    if not fields.get("inn"):
        same_line = re.search(r"ИНН\s*[:|]?\s*(\d{10})\b", text)
        if same_line:
            fields["inn"] = same_line.group(1)
    if not fields.get("ogrn"):
        fields["ogrn"] = _unique_digit_run(text, 13)
    return _record("carrier", "registration_certificate", fields)


def _egrul_record(lines, text):
    """EGRUL record sheet / extract: full name and (when unambiguous) ОГРН."""
    fields = {"full_name": _org_full_name(text)}
    if not fields.get("full_name"):
        for line in lines:
            if re.search(r"\bООО\b|\bАО\b|ОБЩЕСТВО\s+С\s+ОГРАНИЧЕННОЙ", line, re.I):
                fields["full_name"] = line.strip(" |")
                break
    ogrn = _unique_digit_run(text, 13)
    if ogrn and re.search(r"\(\s*ОГРН\s*\)|ОГРН", text):
        fields["ogrn"] = ogrn
    ogrnip = _unique_digit_run(text, 15)
    if not fields.get("ogrn") and ogrnip:
        fields["ogrn"] = ogrnip
        fields["entity_type"] = "ИП"
    address = _label_value(lines, r"адрес\s*\(место\s+нахождения\)", 3)
    if address:
        fields["legal_address"] = address
    return _record("carrier", "egrul_record", fields)


def _founder_decision(lines, text):
    """Sole founder decision: organisation name and appointed director."""
    fields = {"full_name": _org_full_name(text)}
    director = re.search(
        r"на\s+должность\s+(Генерального\s+директора|Директора)\s+"
        r"(?:Общества\s+)?([А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+\s+[А-ЯЁ][а-яё-]+)",
        text)
    if director:
        fields["director_position"] = ("Генеральный директор"
                                       if director.group(1).lower().startswith("генеральн")
                                       else "Директор")
        fields["director_name"] = director.group(2)
    return _record("carrier", "founder_decision", fields)


def _rostransnadzor_notice(lines, text):
    """Numbered Rostransnadzor registry extract: items 2, 3 and 12 hold the data."""
    fields = {}
    for line in lines:
        match = re.match(r"^2\.\s*(\d{13,15})\s*$", line)
        if match:
            fields["ogrn"] = match.group(1)
        match = re.match(r"^3\.\s*(.+)$", line)
        if match and re.search(r"ОБЩЕСТВО|ПРЕДПРИНИМАТЕЛЬ|ООО|ИП\b", match.group(1), re.I):
            fields["full_name"] = match.group(1).strip()
        match = re.match(r"^12\.\s*(\d{10}|[0-9]{12})\s*$", line)
        if match:
            fields["inn"] = match.group(1)
    return _record("carrier", "registry_notice", fields)


# ── карточка водителя и техники ────────────────────────────────────────

def _patronymic(word):
    """Отчество, в том числе дефисное («Иванович-Младший»)."""
    return bool(re.search(r"(?:ович|евич|овна|евна|ична|инична)", word or "", re.I))


#: Слово ФИО: дефис допускается внутри любой части («Иванов-Петров»).
_NAME_WORD = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁа-яё]+)*"


def _driver_name(lines):
    """ФИО водителя: строка без метки, либо значение метки «Водитель»."""
    for line in lines[:4]:
        if re.search(r"[:|]", line):
            continue
        words = re.findall(r"\b" + _NAME_WORD + r"\b", line)
        # A bare name line: exactly three Cyrillic words, patronymic suffix.
        if len(words) == 3 and _patronymic(words[2]):
            return " ".join(words)
    driver = _label_value(lines, r"^\s*(?:водитель|ФИО)\b", 1)
    match = re.search(r"(" + _NAME_WORD + r"(?:\s+" + _NAME_WORD + r"){2})", driver or "")
    return match.group(1) if match else ""


#: Пары «серия + номер» документа в порядке приоритета записи:
#: «45 12 345678» → «4512 345678» (серия слитно) → «4512345678» (10 цифр подряд).
_SERIES_NUMBER_PATTERNS = (
    re.compile(r"\b([0-9]{2})\s+([0-9]{2})\s+([0-9]{6})\b"),
    re.compile(r"\b([0-9]{4})\s+([0-9]{6})\b"),
    re.compile(r"\b([0-9]{10})\b"),
)

#: Крупные заголовки, на которых заканчивается блок паспорта.
_PASSPORT_BLOCK_STOP = re.compile(
    r"^\s*(?:кем\s+выдан|выдан[оа]?\s*[:\-–—]|адрес\s+регистрации|адрес\s+места\s+жительства|"
    r"водительское\s+удостоверение|тягач|полуприцеп|прицеп|телефон|тел\b|прописка|"
    r"собственник)", re.I)

#: Крупные заголовки, на которых заканчивается блок водительского удостоверения.
_LICENSE_BLOCK_STOP = re.compile(
    r"^\s*(?:паспорт|адрес\s+регистрации|кем\s+выдан|водитель\b|тягач|полуприцеп|"
    r"прицеп|телефон|тел\b|собственник|место\s+рождения)", re.I)


def _document_block(lines, label_pattern, stop_pattern, limit=5):
    """Строки блока документа: от метки до следующего крупного заголовка."""
    for index, line in enumerate(lines):
        match = re.search(label_pattern, line, re.I)
        if not match:
            continue
        parts = [line[match.end():].strip(" :|—")]
        for candidate in lines[index + 1:index + 1 + limit]:
            if stop_pattern.match(candidate):
                break
            parts.append(candidate.strip(" :|—"))
        return " ".join(part for part in parts if part)
    return ""


def _series_and_number(text):
    """
    Серия и номер документа (паспорт, водительское удостоверение).

    Понимает записи «45 12 345678», «4512 345678», «9609 188174»,
    «4512345678» и раздельные метки «Серия: 45 12» / «Номер: 345678».
    Серия всегда возвращается в формате «XX XX», номер — 6 цифр.
    """
    for pattern in _SERIES_NUMBER_PATTERNS:
        match = pattern.search(text or "")
        if not match:
            continue
        digits = "".join(group for group in match.groups() if group)
        return f"{digits[:2]} {digits[2:4]}", digits[4:]
    series = re.search(r"серия\s*[:\-–—]?\s*([0-9]{2})\s?([0-9]{2})\b", text or "", re.I)
    number = re.search(r"номер\s*[:\-–—]?\s*([0-9]{6})\b", text or "", re.I)
    if series and number:
        return f"{series.group(1)} {series.group(2)}", number.group(1)
    return "", ""


def _driver_passport(lines):
    """Серия, номер, дата выдачи и код подразделения паспорта РФ."""
    fields = {}
    block = _document_block(lines, r"^\s*паспорт\b", _PASSPORT_BLOCK_STOP)
    series, number = _series_and_number(block)
    if series and number:
        fields["passport_series"] = series
        fields["passport_number"] = number
    issued = re.search(r"(?:выдан[оа]?|дата\s+выдачи)[^\d]{0,25}(" + DATE + r")", block, re.I)
    if issued:
        fields["passport_issue_date"] = issued.group(1)
    code = _label_value(lines, r"код\s+подразделения", 1)
    # Разделителем может быть дефис, тире или пробел — формат приводим к «123-456».
    match = re.search(r"\b([0-9]{3})\s*[-–—]\s*([0-9]{3})\b|\b([0-9]{3})\s([0-9]{3})\b", code)
    if match:
        left, right = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
        fields["passport_code"] = left + "-" + right
    return fields


def _license_categories(text):
    """
    Категории ВУ строкой: «B, C, CE» (значения через запятую сохраняются),
    слитные написания разбираются по буквам: «BC» → «B, C».
    Прочерк («категории —») категорией не считается.
    """
    match = re.search(
        r"категори[а-яё]*\b\s*[:\-–—]?\s*([A-Za-zА-Яа-я][A-Za-zА-Яа-я,.\s]{0,24})",
        text, re.I)
    if not match:
        return ""
    raw = re.split(r"\b(?:срок|до|выдан|выдано|дата|номер|серия)\b", match.group(1), flags=re.I)[0]
    tokens = re.findall(r"[A-ZА-Я]{1,3}", raw.upper())
    if not tokens:
        return ""
    if "," in raw:
        items = list(tokens)
    else:
        items = []
        for token in tokens:
            items.extend(list(token) if len(token) > 1 else [token])
    ordered = list(dict.fromkeys(items))
    return ", ".join(ordered)


def _driver_license(lines):
    """Серия, номер, даты и категории водительского удостоверения."""
    fields = {}
    # Блок шире паспортного: серия, номер, дата выдачи, срок действия и
    # категории свободной записи стоят на разных строках одного блока.
    block = _document_block(lines, r"водительское\s+удостоверение", _LICENSE_BLOCK_STOP)
    if not block:
        return fields
    series, number = _series_and_number(block)
    if series and number:
        fields["license_series"] = series
        fields["license_number"] = number
    else:
        fallback = re.search(r"\b([A-Z]{2}\d{7}|\d{6,10})\b", block)
        if fallback:
            fields["license_number"] = fallback.group(1)
    issued = re.search(r"(?:выдан[оа]?|дата\s+выдачи)[^\d]{0,25}(" + DATE + r")", block, re.I)
    if not issued:
        dates = list(dict.fromkeys(re.findall(DATE, block)))
        # Одна дата в блоке — это дата выдачи (прежнее поведение).
        if len(dates) == 1:
            fields["license_issue_date"] = dates[0]
    else:
        fields["license_issue_date"] = issued.group(1)
    expiry = re.search(r"(?:срок\s+до|действительн[а-яё]*\s+до)\s*(" + DATE + r")", block, re.I)
    if expiry:
        fields["license_expiry_date"] = expiry.group(1)
    categories = _license_categories(block)
    if categories:
        fields["license_categories"] = categories
    return fields


#: Метки блоков техники. Порядок важен: «полуприцеп» проверяется раньше «прицеп».
_VEHICLE_LABELS = (
    (r"(?:седельный\s+)?тягач[а-яё]*", "tractor"),
    (r"(?:полу)?прицеп[а-яё]*", "trailer"),
    (r"п/прицеп[а-яё]*", "trailer"),
)

#: Госномер РФ: А123ВС77 (авто) и АВ1234 77 (прицеп); допускаются латинские двойники.
_PLATE_RU_PATTERNS = (
    re.compile(r"\b([АВЕКМНОРСТУХABEKMHOPCTYX]\s?[0-9]{3}\s?[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s?[0-9]{2,3})\b"),
    re.compile(r"\b([АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s?[0-9]{4}\s?[0-9]{2,3})\b"),
)
#: Иностранный номер читается как есть: смешанный токен из букв и цифр.
_MIXED_PLATE_RE = re.compile(r"\b([A-Za-zА-Яа-я0-9]{5,12})\b")
_COLOR_RE = re.compile(r"\b(?:бел|сер|ч[её]рн|син|красн|зел[её]н|ж[её]лт|коричнев)\w*", re.I)
_BRAND_STOP_RE = re.compile(
    r"[,;]|\bгосномер\b|\bг/н\b|\bгос\.?\s*номер\b|\bрегистрационный\s+знак\b|\bцвет\b|\bгод\s+выпуска\b",
    re.I,
)


def _plate_from_text(text):
    """Госномер: сначала форматы РФ, затем иностранный (не отбрасывается)."""
    for pattern in _PLATE_RU_PATTERNS:
        match = pattern.search(text)
        if match:
            return re.sub(r"\s+", "", match.group(1))
    for token in _MIXED_PLATE_RE.findall(text):
        if re.search(r"[0-9]", token) and re.search(r"[A-Za-zА-Яа-я]", token):
            return token
    return ""


def _vehicle_block_fields(block):
    """Марка, госномер и цвет одного блока техники."""
    fields = {}
    head = _BRAND_STOP_RE.split(block, maxsplit=1)[0]
    brand = re.sub(r"^\s*(?:марка|модель)\s*[:\-–—]?\s*", "", head, flags=re.I).strip(" ,;:-–—")
    if brand and re.search(r"[A-Za-zА-Яа-я]", brand):
        fields["brand_model"] = brand
    plate = _plate_from_text(block)
    if plate:
        fields["plate_number"] = plate
    color = _COLOR_RE.search(block)
    if color:
        fields["color"] = color.group(0)
    return fields


def _tractor_trailer(lines):
    """
    Блоки «Тягач: …» и «Полуприцеп: …».

    Возвращает (tractor, trailer) без смешивания: данные тягача никогда
    не попадают в прицеп, а отсутствующий прицеп остаётся пустым.
    """
    found = {}
    for index, line in enumerate(lines):
        for pattern, section in _VEHICLE_LABELS:
            match = re.match(r"^\s*(?:марка\s+)?" + pattern + r"\b\s*[:\-–—]?\s*(.*)$", line, re.I)
            if not match or section in found:
                continue
            parts = [match.group(1)]
            for candidate in lines[index + 1:index + 3]:
                if _NEW_LABEL.match(candidate):
                    break
                parts.append(candidate)
            block = " ".join(part for part in parts if part).strip(" :|—")
            fields = _vehicle_block_fields(block)
            if fields:
                found[section] = fields
            break
    return found.get("tractor", {}), found.get("trailer", {})


def _driver_card(lines, text):
    """Hand-written driver card: labelled passport/licence/vehicle rows."""
    fields = {}
    fields["full_name"] = _driver_name(lines)
    fields["birth_date"] = _label_date(lines, r"дата\s+рождения|д\.\s?р\.|д/р")
    fields.update(_driver_passport(lines))
    fields.update(_driver_license(lines))
    if not fields.get("passport_issue_date"):
        fields["passport_issue_date"] = _label_date(lines, r"дата\s+выдачи(?!\s+водительского)")
    # Метка обязана иметь разделитель: иначе строка «выдан 20.06.2015»
    # считалась вторым кандидатом и значение отбрасывалось как неоднозначное.
    issuer = _label_value(lines, r"^\s*(?:кем\s+выдан\b|выдан[оа]?\s*[:\-–—])", 2)
    fields["passport_issuer"] = re.sub(r"\s*\d{2}\.\d{2}\.\d{4}.*$", "", issuer).strip()
    fields["birth_place"] = _label_value(lines, r"место\s+рождения", 2)
    fields["registration_address"] = _label_value(lines, r"^\s*(?:прописка|адрес\s+регистрации)", 2)
    phone = _label_value(lines, r"^\s*(?:тел|телефон)\b\.?", 1)
    number = re.search(r"\+?[0-9][0-9\s\-()]{9,}", phone)
    fields["phone"] = number.group(0).strip() if number else phone
    driver = _record("driver", "driver_card", fields)

    tractor, trailer = _tractor_trailer(lines)
    if tractor:
        driver.setdefault("tractor", []).append(tractor)
    if trailer:
        driver.setdefault("trailer", []).append(trailer)
    return driver


def extract_document_fields(text):
    """Return None for generic text; an empty dict is a recognized but unreadable layout."""
    lines = _lines(text)
    if re.search(r"карта\s+контрагента", text, re.I):
        return _counterparty_card(lines, text)
    if (re.search(r"постановке\s+на\s+учет\s+российской\s+организации|поставлена\s+на\s+учет", text, re.I)
            and re.search(r"ИНН\s*/?\s*КПП", text)):
        return _registration_certificate(lines, text)
    if re.search(r"выписка\s+из\s+реестра\s+уведомлений|реестр[а-я]*\s+уведомлений\s+о\s+транспортно", text, re.I):
        return _rostransnadzor_notice(lines, text)
    if (re.search(r"единого\s+государственного\s+реестра\s+юридических\s+лиц", text, re.I)
            or re.search(r"лист\s+записи\s+егрюл", text, re.I)):
        return _egrul_record(lines, text)
    if (re.search(r"^\s*[РP]\s*Е\s*Ш\s*Е\s*Н\s*И\s*Е|решение\s+№?\s*\d*\s*единственного\s+учредителя", text, re.I | re.M)
            and re.search(r"учредител", text, re.I)):
        return _founder_decision(lines, text)
    license_marker = re.search(r"водительское\s+удостоверение|ID\s*карта|госномер\s+(?:тягача|прицепа)", text, re.I)
    card_marker = re.search(r"дата\s+рождения|д\.\s?р\.|прописка|прицеп|тягач|регистрац|место\s+рождения", text, re.I)
    # Свободная запись «серия номер выдано … категории …» без номерного бланка.
    free_form_license = (re.search(r"выдан[оа]|категори|срок\s+до", text, re.I)
                         and re.search(r"\b[0-9]{2}\s+[0-9]{2}\s+[0-9]{6}\b", text))
    driver_block = (re.search(r"(?m)^\s*водитель\b", text, re.I)
                    and re.search(r"д\.\s?р\.|место\s+рождения", text, re.I))
    if (license_marker and (card_marker or free_form_license)) or driver_block:
        return _driver_card(lines, text)
    if re.search(r"банк\s+получателя|номер\s+сч[её]та\s+получателя", text, re.I):
        return _bank(lines, text)
    if re.search(r"\bсобственник\b|\bвладелец\b|\bowner\b", text, re.I):
        return _vehicle(lines, text)
    if re.search(r"водительское\s+удостоверение|driving\s+licen[cs]e|permis\s+de\s+conduire", text, re.I):
        return _license(lines, text)
    if (re.search(r"паспорт\s+выдан|PNRUS|фамилия[\s\S]{0,150}отчество", text, re.I)
        or (re.search(r"\bМВД\b", text) and re.search(r"\b[0-9]{3}[-–][0-9]{3}\b", text)
            and len(re.findall(DATE, text)) >= 2)):
        return _passport(lines, text)
    if re.search(r"место\s+жительства|зарегистрирован|адрес\s+регистрации", text, re.I) and not re.search(r"собственник|владелец|vehicle", text, re.I):
        # This stamp does not identify a person. Keep separate, never use the stamp date as birth date.
        fields = {"registration_address": " ".join(line for line in lines
                  if re.search(r"респ|район|р-н|пункт|улица|ул\.|дом", line, re.I)
                  and not re.search(r"МВД|РОССИИ|ОТДЕЛ|МИГРАЦ", line, re.I))}
        return _record("driver", "registration", fields)
    if re.search(r"свидетельство[\s\S]{0,35}регистрации|vehicle\s+registration|регистрационн[а-я]+\s+номер|собственник\s*\(владелец\)", text, re.I):
        return _vehicle(lines, text)
    # Карточка техники без водителя: только «Тягач: …» и/или «Полуприцеп: …».
    if (re.search(r"(?m)^\s*(?:марка\s+)?(?:седельный\s+)?тягач[а-яё]*\b"
                  r"|^\s*(?:марка\s+)?(?:полу)?прицеп[а-яё]*\b"
                  r"|^\s*п/прицеп[а-яё]*\b", text, re.I)
            and re.search(r"госномер|г/н|регистрационный\s+знак", text, re.I)
            and not re.search(r"свидетельство|собственник|владелец|\bVIN\b", text, re.I)):
        tractor, trailer = _tractor_trailer(lines)
        fields = {}
        if tractor:
            fields["tractor"] = [tractor]
        if trailer:
            fields["trailer"] = [trailer]
        if fields:
            return fields
    return None
