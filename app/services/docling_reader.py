
from pathlib import Path

from docling.document_converter import DocumentConverter


def _get_page_number(item: dict):
    """Возвращает номер страницы из provenance, если он доступен."""

    provenance = item.get("prov", [])

    if not provenance:
        return None

    return provenance[0].get("page_no")


def _get_table_rows(item: dict) -> list[list[str]]:
    """Преобразует таблицу Docling в двумерный список строк."""

    grid = item.get("data", {}).get("grid", [])

    rows = []

    for row in grid:
        row_values = [
            cell.get("text", "").strip()
            for cell in row
        ]
        rows.append(row_values)

    return rows


def _table_to_text(rows: list[list[str]]) -> str:
    """Преобразует таблицу в текст для полнотекстового поиска."""

    return "\n".join(" | ".join(row) for row in rows)


def extract_document(file_path: str) -> dict:
    """
    Извлекает текст и структуру PDF/DOCX через Docling.

    Возвращает:
    - text: полный текст документа;
    - blocks: структурированные блоки документа.
    """

    path = Path(file_path)

    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {file_path}")

    extension = path.suffix.lower()

    if extension not in {".pdf", ".docx"}:
        raise ValueError("Поддерживаются только PDF и DOCX")

    converter = DocumentConverter()
    result = converter.convert(str(path))

    data = result.document.export_to_dict()

    # Собираем все элементы в единый индекс по self_ref.
    elements = {}

    for collection_name in ("groups", "texts", "tables"):
        for item in data.get(collection_name, []):
            elements[item["self_ref"]] = item

    blocks = []
    visited = set()

    def walk(ref: str, section_path: list[str]) -> list[str]:
        """Обходит дерево и передаёт контекст раздела следующим элементам."""

        if ref in visited:
            return section_path

        item = elements.get(ref)

        if item is None:
            return section_path

        visited.add(ref)

        label = item.get("label")
        current_section_path = section_path

        # Обрабатываем текстовые элементы.
        if ref.startswith("#/texts/"):
            text = item.get("text", "").strip()

            if label not in {"page_header", "page_footer"} and text:

                if label == "section_header":
                    level = item.get("level") or 1

                    current_section_path = (
                        section_path[:level - 1] + [text]
                    )

                    blocks.append({
                        "block_id": len(blocks) + 1,
                        "type": "heading",
                        "section": " / ".join(current_section_path[:-1]) or None,
                        "text": text,
                        "level": level,
                        "page_number": _get_page_number(item),
                    })

                else:
                    blocks.append({
                        "block_id": len(blocks) + 1,
                        "type": (
                            "list_item"
                            if label == "list_item"
                            else "paragraph"
                        ),
                        "section": " / ".join(current_section_path) or None,
                        "text": text,
                        "page_number": _get_page_number(item),
                    })

        # Обрабатываем таблицы.
        elif ref.startswith("#/tables/"):
            rows = _get_table_rows(item)

            if rows:
                blocks.append({
                    "block_id": len(blocks) + 1,
                    "type": "table",
                    "section": " / ".join(current_section_path) or None,
                    "rows": rows,
                    "text": _table_to_text(rows),
                    "page_number": _get_page_number(item),
                })

        # Обходим дочерние элементы в исходном порядке.
        for child in item.get("children", []):
            child_ref = child.get("$ref")

            if child_ref:
                current_section_path = walk(
                    child_ref,
                    current_section_path
                )

        return current_section_path

    # Начинаем обход с корня документа.
    current_section_path = []

    for child in data.get("body", {}).get("children", []):
        child_ref = child.get("$ref")

        if child_ref:
            current_section_path = walk(
                child_ref,
                current_section_path
            )

    # Резервная обработка элементов, не попавших в дерево body.
    for ref in elements:
        if ref not in visited:
            walk(ref, [])

    # Формируем общий текст документа из блоков.
    text_parts = []

    for block in blocks:
        if block.get("text"):
            text_parts.append(block["text"])

    full_text = "\n\n".join(text_parts)

    return {
        "text": full_text,
        "blocks": blocks,
    }

