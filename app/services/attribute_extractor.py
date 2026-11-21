import re


PATTERNS = {
    "process": [
        r"(?:процесс|предмет договора|предмет соглашения)\s*[:—-]\s*([^.\n;]+)",
        r"(?:исполнитель обязуется|задача состоит в)\s+([^.\n;]+)",
    ],
    "purpose": [
        r"(?:цель|назначение|для чего)\s*[:—-]\s*([^.\n;]+)",
        r"(?:в целях|с целью)\s+([^.\n;]+)",
    ],
    "responsible": [
        r"(?:ответственный|ответственные|ответственное лицо|исполнитель|заказчик)\s*[:—-]\s*([^.\n;]+)",
    ],
}

DATE_PATTERN = re.compile(
    r"\b(?:"
    r"\d{1,2}[./]\d{1,2}[./]\d{2,4}"
    r"|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}\s+"
    r"(?:января|февраля|марта|апреля|мая|июня|июля|"
    r"августа|сентября|октября|ноября|декабря)"
    r"\s+\d{4}\s*(?:года)?"
    r")\b",
    re.IGNORECASE,
)


def extract_attributes(text: str) -> list[dict]:
    """
    Извлекает атрибуты из текста по набору регулярных выражений.

    Это rule-based извлечение, а не анализ языковой моделью.
    Для каждого результата сохраняется исходный фрагмент.
    """

    results = []

    if not text or not text.strip():
        text = ""

    # Извлечение процесса, цели и ответственных
    for attribute_type, patterns in PATTERNS.items():
        matches = []

        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                value = match.group(1).strip()
                if value:
                    matches.append({
                        "value": value,
                        "source_fragment": match.group(0).strip(),
                    })

        # Убираем повторяющиеся совпадения
        unique_matches = []
        seen = set()

        for item in matches:
            key = item["value"].casefold()
            if key not in seen:
                seen.add(key)
                unique_matches.append(item)

        if not unique_matches:
            results.append({
                "attribute_type": attribute_type,
                "value": None,
                "status": "missing",
                "source_fragment": None,
                "date_role": None,
            })

        elif len(unique_matches) == 1:
            item = unique_matches[0]
            results.append({
                "attribute_type": attribute_type,
                "value": item["value"],
                "status": "found",
                "source_fragment": item["source_fragment"],
                "date_role": None,
            })

        else:
            for item in unique_matches:
                results.append({
                    "attribute_type": attribute_type,
                    "value": item["value"],
                    "status": "ambiguous",
                    "source_fragment": item["source_fragment"],
                    "date_role": None,
                })
    # Извлечение продолжительности сроков
    duration_pattern = re.compile(
        r"(?:срок выполнения|срок оказания услуг|"
        r"продолжительность|срок договора)"
        r"\s*[:—-]\s*([^.\n;]+)",
        re.IGNORECASE,
    )

    duration_matches = list(duration_pattern.finditer(text))

    if not duration_matches:
        results.append({
            "attribute_type": "duration",
            "value": None,
            "status": "missing",
            "source_fragment": None,
            "date_role": "deadline",
        })
    else:
        for match in duration_matches:
            results.append({
                "attribute_type": "duration",
                "value": match.group(1).strip(),
                "status": "found",
                "source_fragment": match.group(0).strip(),
                "date_role": "deadline",
            })
            
        # Извлечение денежных сумм
    amount_pattern = re.compile(
        r"(?:стоимость услуг|стоимость работ|цена договора|"
        r"сумма договора|стоимость)"
        r"\s*[:—-]\s*"
        r"([\d\s]+(?:[,.]\d{1,2})?\s*"
        r"(?:руб(?:лей|ля|ль)?|₽))",
        re.IGNORECASE,
    )

    amount_matches = list(amount_pattern.finditer(text))

    if not amount_matches:
        results.append({
            "attribute_type": "amount",
            "value": None,
            "status": "missing",
            "source_fragment": None,
            "date_role": None,
        })
    else:
        for match in amount_matches:
            results.append({
                "attribute_type": "amount",
                "value": match.group(1).strip(),
                "status": "found",
                "source_fragment": match.group(0).strip(),
                "date_role": None,
            })

    # Извлечение дат
    date_matches = list(DATE_PATTERN.finditer(text))

    if not date_matches:
        results.append({
            "attribute_type": "date",
            "value": None,
            "status": "missing",
            "source_fragment": None,
            "date_role": None,
        })

    else:
        for match in date_matches:
            start = max(0, match.start() - 100)
            end = min(len(text), match.end() + 100)
            context = text[start:end]

            context_lower = context.casefold()

            if any(word in context_lower for word in (
                "подписан", "заключен", "заключения"
            )):
                date_role = "contract_date"
            elif any(word in context_lower for word in (
                "окончан", "завершен", "до ", "срок выполнения"
            )):
                date_role = "deadline"
            elif any(word in context_lower for word in (
                "начал", "старт", "вступает в силу"
            )):
                date_role = "start_date"
            else:
                date_role = "unspecified"

            results.append({
                "attribute_type": "date",
                "value": match.group(0),
                "status": "found",
                "source_fragment": context.strip(),
                "date_role": date_role,
            })

    return results
