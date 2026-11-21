from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table

def extract_text_from_docx(file_path: str) -> str:
    """Извлекает текст из DOCX-документа."""

    document = Document(file_path)
    document_parts = []

    # Извлекаем абзацы
    for paragraph in document.paragraphs:
        if paragraph.text.strip():
            document_parts.append(paragraph.text)

    # Извлекаем таблицы
    for table in document.tables:
        for row in table.rows:
            row_text = [
                cell.text.strip()
                for cell in row.cells
                if cell.text.strip()
            ]

            if row_text:
                document_parts.append(" | ".join(row_text))

    return "\n".join(document_parts)




def extract_structure_from_docx(file_path: str) -> list[dict]:
    """Извлекает структуру DOCX с сохранением разделов."""

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

            blocks.append({
                "block_id": len(blocks) + 1,
                "type": "table",
                "section": current_section,
                "rows": rows
            })

    return blocks
