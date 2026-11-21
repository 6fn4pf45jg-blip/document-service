
from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table


def extract_text_from_docx(file_path: str) -> str:
    """Извлекает текст DOCX в исходном порядке, включая таблицы."""

    document = Document(file_path)
    document_parts = []

    for item in document.iter_inner_content():

        if isinstance(item, Paragraph):
            text = item.text.strip()

            if text:
                document_parts.append(text)

        elif isinstance(item, Table):
            for row in item.rows:
                row_text = [
                    cell.text.strip()
                    for cell in row.cells
                ]

                document_parts.append(" | ".join(row_text))

    return "\n".join(document_parts)


def extract_structure_from_docx(file_path: str) -> list[dict]:
    """Извлекает структуру DOCX: заголовки, абзацы и таблицы."""

    document = Document(file_path)
    blocks = []
    current_section = None

    for item in document.iter_inner_content():

        if isinstance(item, Paragraph):
            text = item.text.strip()

            if not text:
                continue

            if item.style.name.startswith("Heading"):
                current_section = text

                blocks.append({
                    "block_id": len(blocks) + 1,
                    "type": "heading",
                    "section": current_section,
                    "text": text
                })

            else:
                blocks.append({
                    "block_id": len(blocks) + 1,
                    "type": "paragraph",
                    "section": current_section,
                    "text": text
                })

        elif isinstance(item, Table):
            rows = []

            for row in item.rows:
                rows.append([
                    cell.text.strip()
                    for cell in row.cells
                ])

            table_text = "\n".join(
                " | ".join(row)
                for row in rows
            )

            blocks.append({
                "block_id": len(blocks) + 1,
                "type": "table",
                "section": current_section,
                "text": table_text,
                "rows": rows
            })

    return blocks
