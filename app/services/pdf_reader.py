
import fitz


def extract_text_from_pdf(file_path: str) -> str:
    """Извлекает текст из PDF-файла."""

    document = fitz.open(file_path)
    pages_text = []

    for page_number, page in enumerate(document, start=1):
        text = page.get_text()
        pages_text.append(f"Страница {page_number}:\n{text}")

    document.close()

    return "\n\n".join(pages_text)

