from pathlib import Path
from copy import deepcopy
from uuid import uuid4
import logging
import json
from difflib import SequenceMatcher

from fastapi import (
    APIRouter,
    UploadFile,
    File,
    HTTPException,
    Depends,
)
from fastapi.responses import FileResponse
from sqlalchemy import cast, String
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.document_block import DocumentBlock
from app.schemas.document import DocumentResponse

from app.services.docling_reader import extract_document
from app.services.document_comparator import compare_texts
from app.services.vector_store import index_blocks

from pydantic import BaseModel
from app.services.vector_store import search_blocks

from app.models.document_attribute import DocumentAttribute
from app.services.attribute_extractor import extract_attributes

from app.services.llm_service import generate_answer

from docx import Document as DocxDocument
from app.services.llm_service import generate_edited_text

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parents[2]

UPLOAD_DIR = BASE_DIR / "storage" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

PENDING_EDIT_DIR = BASE_DIR / "storage" / "pending_edits"
PENDING_EDIT_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger(__name__)


def resolve_project_path(path_value: str | Path) -> Path:
    """Преобразует сохранённый путь к файлу в абсолютный путь проекта."""
    path = Path(path_value)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def add_document_blocks(
    db: Session,
    version_id: str,
    blocks: list[dict],
):
    """
    Добавляет структурированные блоки версии документа
    в сессию SQLAlchemy.

    Commit здесь не вызывается:
    транзакцией управляет вызывающая функция.
    """

    db_blocks = []

    for block in blocks:
        content = {
            "text": block.get("text", ""),
            "page_number": block.get("page_number"),
        }

        if "rows" in block:
            content["rows"] = block["rows"]

        if "level" in block:
            content["level"] = block["level"]

        db_block = DocumentBlock(
            id=str(uuid4()),
            version_id=version_id,
            block_id=block["block_id"],
            type=block["type"],
            section=block.get("section"),
            content=content,
        )

        db_blocks.append(db_block)

    db.add_all(db_blocks)



def apply_text_to_docx_preserving_structure(
    source_path: str,
    edited_text: str,
    output_path: str,
):
    """Редактирует абзацы и таблицы DOCX с сохранением форматирования."""

    from docx.text.paragraph import Paragraph
    from docx.table import Table

    source_file = resolve_project_path(source_path)

if not source_file.is_file():
    raise FileNotFoundError(
        f"DOCX не найден. "
        f"Переданный путь: {source_path!r}. "
        f"Абсолютный путь: {source_file}"
    )

doc = DocxDocument(str(source_file))

edited_lines = [
        line.strip()
        for line in edited_text.splitlines()
        if line.strip()
    ]

def replace_paragraph_text(paragraph, new_text):
        """Заменяет текст абзаца, сохраняя форматирование runs."""

        old_text = paragraph.text

        if old_text == new_text:
            return

        allowed_paragraph_tags = {
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr",
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}r",
        }

        if any(child.tag not in allowed_paragraph_tags for child in paragraph._p):
            raise ValueError(
                "Абзац содержит нетекстовые элементы. "
                "Автоматическое редактирование остановлено."
            )

        runs = list(paragraph.runs)

        allowed_run_tags = {
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}rPr",
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t",
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tab",
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}br",
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}cr",
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}noBreakHyphen",
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}softHyphen",
        }

        for run in runs:
            if any(child.tag not in allowed_run_tags for child in run._r):
                raise ValueError(
                    "Run содержит сложное содержимое. "
                    "Автоматическая замена остановлена."
                )

        def run_index_at(position):
            if not runs:
                return None

            cursor = 0

            for index, run in enumerate(runs):
                next_cursor = cursor + len(run.text)

                if cursor <= position < next_cursor:
                    return index

                cursor = next_cursor

            return len(runs) - 1

        def original_chunks(start, end):
            chunks = []
            cursor = 0

            for index, run in enumerate(runs):
                run_text = run.text
                run_start = cursor
                run_end = cursor + len(run_text)

                left = max(start, run_start)
                right = min(end, run_end)

                if left < right:
                    chunks.append((
                        run_text[left - run_start:right - run_start],
                        index
                    ))

                cursor = run_end

            return chunks

        matcher = SequenceMatcher(
            a=old_text,
            b=new_text,
            autojunk=False
        )

        chunks = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                chunks.extend(original_chunks(i1, i2))

            elif tag in {"replace", "insert"}:
                replacement = new_text[j1:j2]

                if replacement:
                    chunks.append((
                        replacement,
                        run_index_at(i1)
                    ))

        merged = []

        for text_part, run_index in chunks:
            if not text_part:
                continue

            if merged and merged[-1][1] == run_index:
                merged[-1] = (
                    merged[-1][0] + text_part,
                    run_index
                )
            else:
                merged.append((text_part, run_index))

        run_properties = [
            deepcopy(run._r.rPr)
            if run._r.rPr is not None
            else None
            for run in runs
        ]

        paragraph.clear()

        for text_part, run_index in merged:
            new_run = paragraph.add_run(text_part)

            if (
                run_index is not None
                and run_properties[run_index] is not None
            ):
                new_run._r.insert(
                    0,
                    deepcopy(run_properties[run_index])
                )

    # Собираем редактируемые элементы в исходном порядке.
elements = []

for item in doc.iter_inner_content():

        if isinstance(item, Paragraph):
            if item.text.strip():
                elements.append(("paragraph", item))

        elif isinstance(item, Table):
            for row in item.rows:
                cells = row.cells

                # Пока не поддерживаем объединённые ячейки.
                cell_ids = [id(cell._tc) for cell in cells]

                if len(cell_ids) != len(set(cell_ids)):
                    raise ValueError(
                        "Таблица содержит объединённые ячейки. "
                        "Автоматическое редактирование остановлено."
                    )

                elements.append(("table_row", row))

if len(elements) != len(edited_lines):
        raise ValueError(
            "Модель изменила структуру документа: количество "
            "абзацев и строк таблиц отличается от исходного."
        )

for element, new_text in zip(elements, edited_lines):

        element_type, obj = element

        if element_type == "paragraph":
            replace_paragraph_text(obj, new_text)

        elif element_type == "table_row":
            cells = obj.cells
            new_cells = [
                cell.strip()
                for cell in new_text.split("|")
            ]

            if len(cells) != len(new_cells):
                raise ValueError(
                    "Модель изменила количество столбцов таблицы. "
                    "Редактирование остановлено."
                )

            for cell, new_cell_text in zip(cells, new_cells):
                paragraphs = [
                    paragraph
                    for paragraph in cell.paragraphs
                    if paragraph.text.strip()
                ]

                if len(paragraphs) > 1:
                    raise ValueError(
                        "Ячейка содержит несколько абзацев. "
                        "Автоматическое редактирование такой ячейки "
                        "пока не поддерживается."
                    )

                if paragraphs:
                    replace_paragraph_text(
                        paragraphs[0],
                        new_cell_text
                    )
                elif new_cell_text:
                    cell.add_paragraph(new_cell_text)

doc.save(output_path)

def validate_file(filename: str) -> str:
    """Проверяет расширение загружаемого файла."""

    extension = Path(filename).suffix.lower()

    if extension not in {".pdf", ".docx"}:
        raise HTTPException(
            status_code=400,
            detail="Поддерживаются только PDF и DOCX",
        )

    return extension


def index_document_safely(
    document_id: str,
    version_id: str,
    blocks: list[dict],
) -> dict:
    """
    Индексирует блоки в Qdrant.

    Ошибка индексации не отменяет сохранение
    документа в PostgreSQL.
    """

    try:
        indexed_count = index_blocks(
            document_id=document_id,
            version_id=version_id,
            blocks=blocks,
        )

        return {
            "indexed_blocks": indexed_count,
            "indexing_status": "completed",
        }

    except Exception:
        logger.exception(
            "Не удалось проиндексировать документ %s, версию %s",
            document_id,
            version_id,
        )

        return {
            "indexed_blocks": 0,
            "indexing_status": "failed",
        }


@router.get("/")
def home():
    return {
        "message": "Служба «Document Service» работает!"
    }


@router.get("/health")
def health_check():
    return {"status": "Ok"}


@router.get(
    "/documents/example",
    response_model=DocumentResponse,
)
def get_document():
    return {
        "id": "1",
        "filename": "test.pdf",
        "status": "uploaded",
    }


@router.post(
    "/documents/upload",
    response_model=DocumentResponse,
)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Загружает новый документ.

    Создаёт:
    - запись документа;
    - первую версию;
    - структурированные блоки;
    - векторные представления блоков в Qdrant.
    """

    filename = file.filename or "document"
    extension = validate_file(filename)

    document_id = str(uuid4())
    version_id = str(uuid4())

    file_path = UPLOAD_DIR / f"{document_id}{extension}"

    content = await file.read()

    if not content:
        raise HTTPException(
            status_code=400,
            detail="Загружен пустой файл",
        )

    file_path.write_bytes(content)

    try:
        # 1. Извлекаем текст и структуру через Docling.
        extracted = extract_document(str(file_path))

        extracted_text = extracted["text"]
        blocks = extracted["blocks"]

        if not extracted_text.strip() or not blocks:
            raise ValueError(
                "Документ не содержит извлекаемого текста"
            )

        # 2. Создаём запись документа.
        db_document = Document(
            id=document_id,
            filename=filename,
            file_path=str(file_path),
            file_type=extension,
            status="processed",
        )

        # 3. Создаём первую версию.
        db_version = DocumentVersion(
            id=version_id,
            document_id=document_id,
            version_number=1,
            filename=filename,
            file_path=str(file_path),
            file_type=extension,
            extracted_text=extracted_text,
        )

        db.add(db_document)
        db.add(db_version)

        # 4. Сохраняем документ и версию в сессии.
        db.flush()

        # 5. Добавляем структурированные блоки.
        add_document_blocks(
            db=db,
            version_id=version_id,
            blocks=blocks,
        )

        # 6. Сохраняем всё в PostgreSQL.
        db.commit()

    except Exception:
        logger.exception(
            "Ошибка при обработке или сохранении документа"
        )

        db.rollback()
        file_path.unlink(missing_ok=True)

        raise HTTPException(
            status_code=400,
            detail="Не удалось обработать или сохранить документ",
        )

    # 7. Индексируем документ в Qdrant отдельно.
    # Ошибка Qdrant не удалит сохранённый документ.
    indexing_result = index_document_safely(
        document_id=document_id,
        version_id=version_id,
        blocks=blocks,
    )

    return {
        "id": document_id,
        "filename": filename,
        "status": "processed",
    }


@router.get("/documents/{document_id}")
def get_document_with_versions(
    document_id: str,
    db: Session = Depends(get_db),
):
    """Возвращает документ и список его версий."""

    document = db.query(Document).filter(
        Document.id == document_id
    ).first()

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Документ не найден",
        )

    versions = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == document_id
    ).order_by(
        DocumentVersion.version_number
    ).all()

    return {
        "id": document.id,
        "filename": document.filename,
        "status": document.status,
        "versions": [
            {
                "id": version.id,
                "version_number": version.version_number,
                "filename": version.filename,
                "file_type": version.file_type,
                "created_at": version.created_at,
            }
            for version in versions
        ],
    }


@router.get(
    "/documents/{document_id}/versions/{version_id}/download"
)
def download_document_version(
    document_id: str,
    version_id: str,
    db: Session = Depends(get_db),
):
    """Скачивает конкретную версию документа."""

    version = db.query(DocumentVersion).filter(
        DocumentVersion.id == version_id,
        DocumentVersion.document_id == document_id,
    ).first()

    if version is None:
        raise HTTPException(
            status_code=404,
            detail="Версия документа не найдена",
        )

    file_path = resolve_project_path(version.file_path)

    if not file_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Файл не найден на сервере",
        )

    media_type = (
        "application/pdf"
        if version.file_type == ".pdf"
        else (
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        )
    )

    return FileResponse(
        path=file_path,
        filename=version.filename,
        media_type=media_type,
    )


@router.post("/documents/{document_id}/versions")
async def create_document_version(
    document_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Создаёт новую версию существующего документа.

    Извлекает текст и структуру через Docling,
    сохраняет версию и блоки в PostgreSQL,
    затем индексирует блоки в Qdrant.
    """

    document = db.query(Document).filter(
        Document.id == document_id
    ).first()

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Документ не найден",
        )

    filename = file.filename or "document"
    extension = validate_file(filename)

    last_version = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == document_id
    ).order_by(
        DocumentVersion.version_number.desc()
    ).first()

    next_version_number = (
        last_version.version_number + 1
        if last_version
        else 1
    )

    version_id = str(uuid4())
    file_path = UPLOAD_DIR / f"{version_id}{extension}"

    content = await file.read()

    if not content:
        raise HTTPException(
            status_code=400,
            detail="Загружен пустой файл",
        )

    file_path.write_bytes(content)

    try:
        # 1. Извлекаем текст и структуру.
        extracted = extract_document(str(file_path))

        extracted_text = extracted["text"]
        blocks = extracted["blocks"]

        if not extracted_text.strip() or not blocks:
            raise ValueError(
                "Документ не содержит извлекаемого текста"
            )

        # 2. Создаём запись новой версии.
        db_version = DocumentVersion(
            id=version_id,
            document_id=document_id,
            version_number=next_version_number,
            filename=filename,
            file_path=str(file_path),
            file_type=extension,
            extracted_text=extracted_text,
        )

        db.add(db_version)
        db.flush()

        # 3. Добавляем структурированные блоки.
        add_document_blocks(
            db=db,
            version_id=version_id,
            blocks=blocks,
        )

        # 4. Сохраняем версию и блоки.
        db.commit()

    except Exception:
        logger.exception(
            "Ошибка при создании новой версии документа %s",
            document_id,
        )

        db.rollback()
        file_path.unlink(missing_ok=True)

        raise HTTPException(
            status_code=400,
            detail="Не удалось обработать или сохранить новую версию",
        )

    # 5. Индексируем новую версию отдельно.
    indexing_result = index_document_safely(
        document_id=document_id,
        version_id=version_id,
        blocks=blocks,
    )

    return {
        "document_id": document_id,
        "version_id": version_id,
        "version_number": next_version_number,
        "filename": filename,
        "status": "processed",
        **indexing_result,
    }


@router.get("/documents/{document_id}/compare")
def compare_document_versions(
    document_id: str,
    old_version: int,
    new_version: int,
    db: Session = Depends(get_db),
):
    """Сравнивает текст и извлечённые атрибуты двух версий документа."""

    document = db.query(Document).filter(
        Document.id == document_id
    ).first()

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Документ не найден",
        )

    old = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == document_id,
        DocumentVersion.version_number == old_version,
    ).first()

    new = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == document_id,
        DocumentVersion.version_number == new_version,
    ).first()

    if old is None or new is None:
        raise HTTPException(
            status_code=404,
            detail="Одна или обе версии не найдены",
        )

    # Построчное сравнение полного текста версий.
    diff = compare_texts(
        old.extracted_text or "",
        new.extracted_text or "",
    )

    # Загружаем атрибуты обеих версий.
    old_attributes = db.query(DocumentAttribute).filter(
        DocumentAttribute.version_id == old.id
    ).all()

    new_attributes = db.query(DocumentAttribute).filter(
        DocumentAttribute.version_id == new.id
    ).all()

    def group_attributes(attributes):
        grouped = {}
        for attribute in attributes:
            grouped.setdefault(attribute.attribute_type, []).append(attribute)
        return grouped

    old_grouped = group_attributes(old_attributes)
    new_grouped = group_attributes(new_attributes)

    attribute_changes = []

    for attribute_type in sorted(set(old_grouped) | set(new_grouped)):
        old_items = old_grouped.get(attribute_type, [])
        new_items = new_grouped.get(attribute_type, [])

        old_values = [
            item.value for item in old_items if item.status == "found"
        ]
        new_values = [
            item.value for item in new_items if item.status == "found"
        ]

        old_statuses = [item.status for item in old_items]
        new_statuses = [item.status for item in new_items]

        # Не показываем неизменившиеся атрибуты.
        if old_values == new_values and old_statuses == new_statuses:
            continue

        attribute_changes.append({
            "attribute_type": attribute_type,
            "old_value": old_values,
            "new_value": new_values,
            "old_status": old_statuses,
            "new_status": new_statuses,
            "status": "changed",
            "old_source_fragments": [
                item.source_fragment
                for item in old_items
                if item.status == "found" and item.source_fragment
            ],
            "new_source_fragments": [
                item.source_fragment
                for item in new_items
                if item.status == "found" and item.source_fragment
            ],
        })

    return {
        "document_id": document_id,
        "old_version": old_version,
        "new_version": new_version,
        "diff": diff,
        "attribute_changes": attribute_changes,
    }


@router.get(
    "/documents/{document_id}/versions/{version_id}/blocks"
)
def get_document_blocks(
    document_id: str,
    version_id: str,
    db: Session = Depends(get_db),
):
    """Возвращает структурированные блоки конкретной версии."""

    version = db.query(DocumentVersion).filter(
        DocumentVersion.id == version_id,
        DocumentVersion.document_id == document_id,
    ).first()

    if version is None:
        raise HTTPException(
            status_code=404,
            detail="Версия документа не найдена",
        )

    blocks = db.query(DocumentBlock).filter(
        DocumentBlock.version_id == version_id
    ).order_by(
        DocumentBlock.block_id
    ).all()

    return {
        "document_id": document_id,
        "version_id": version_id,
        "blocks": [
            {
                "block_id": block.block_id,
                "type": block.type,
                "section": block.section,
                "content": block.content,
            }
            for block in blocks
        ],
    }


@router.get(
    "/documents/{document_id}/versions/{version_id}/content"
)
def get_document_content(
    document_id: str,
    version_id: str,
    db: Session = Depends(get_db),
):
    """Возвращает полный текст конкретной версии документа."""

    version = db.query(DocumentVersion).filter(
        DocumentVersion.id == version_id,
        DocumentVersion.document_id == document_id,
    ).first()

    if version is None:
        raise HTTPException(
            status_code=404,
            detail="Версия документа не найдена",
        )

    return {
        "document_id": document_id,
        "version_id": version_id,
        "version_number": version.version_number,
        "filename": version.filename,
        "content": version.extracted_text or "",
    }


@router.get(
    "/documents/{document_id}/versions/{version_id}/search"
)
def search_document(
    document_id: str,
    version_id: str,
    q: str,
    db: Session = Depends(get_db),
):
    """
    Ищет текст внутри блоков конкретной версии документа.

    Это обычный текстовый поиск по PostgreSQL.
    Векторный поиск через Qdrant будет отдельным маршрутом.
    """

    query = q.strip()

    if not query:
        raise HTTPException(
            status_code=400,
            detail="Поисковый запрос не может быть пустым",
        )

    version = db.query(DocumentVersion).filter(
        DocumentVersion.id == version_id,
        DocumentVersion.document_id == document_id,
    ).first()

    if version is None:
        raise HTTPException(
            status_code=404,
            detail="Версия документа не найдена",
        )

    blocks = db.query(DocumentBlock).filter(
        DocumentBlock.version_id == version_id,
        cast(DocumentBlock.content, String).ilike(
            f"%{query}%"
        ),
    ).order_by(
        DocumentBlock.block_id
    ).limit(50).all()

    results = []

    for block in blocks:
        content = block.content or {}

        results.append({
            "block_id": block.block_id,
            "type": block.type,
            "section": block.section,
            "text": content.get("text", ""),
            "page_number": content.get("page_number"),
        })

    return {
        "document_id": document_id,
        "version_id": version_id,
        "query": query,
        "total": len(results),
        "results": results,
    }

class AskRequest(BaseModel):
    question: str



@router.post("/documents/{document_id}/versions/{version_id}/ask")
def ask_document(
    document_id: str,
    version_id: str,
    request: AskRequest,
    db: Session = Depends(get_db),
):
    """Отвечает на вопросы по документу с помощью RAG и Qwen."""

    version = db.query(DocumentVersion).filter(
        DocumentVersion.id == version_id,
        DocumentVersion.document_id == document_id,
    ).first()

    if not version:
        raise HTTPException(
            status_code=404,
            detail="Версия документа не найдена",
        )

    question = request.question.strip()

    if not question:
        raise HTTPException(
            status_code=422,
            detail="Вопрос не должен быть пустым",
        )

    # 1. Ищем релевантные фрагменты в Qdrant.
    try:
        results = search_blocks(
            document_id=document_id,
            version_id=version_id,
            query=question,
            limit=5,
        )
    except Exception:
        logger.exception("Ошибка поиска в Qdrant")
        raise HTTPException(
            status_code=503,
            detail="Сервис поиска временно недоступен",
        )

    sources = [
        {
            "block_id": item.get("block_id"),
            "type": item.get("type"),
            "section": item.get("section"),
            "page_number": item.get("page_number"),
            "text": item.get("text"),
            "score": item.get("score"),
        }
        for item in results
    ]

    if not sources:
        return {
            "mode": "rag",
            "status": "insufficient_context",
            "answer": None,
            "message": "В документе не найдена информация для ответа.",
            "sources": [],
        }

    # 2. Формируем контекст для языковой модели.
    context_parts = []

    for source in sources:
        context_parts.append(
            f"""
Источник:
Раздел: {source.get("section")}
Страница: {source.get("page_number")}
Блок: {source.get("block_id")}

Текст:
{source.get("text")}
"""
        )

    context = "\n".join(context_parts)

    # 3. Генерируем ответ через Qwen.
    try:
        answer = generate_answer(
            question=question,
            context=context,
        )
    except Exception:
        logger.exception("Ошибка генерации ответа через Ollama")
        raise HTTPException(
            status_code=503,
            detail="Не удалось получить ответ от языковой модели",
        )

    return {
        "mode": "rag",
        "status": "success",
        "answer": answer,
        "sources": sources,
    }


@router.get(
    "/documents/{document_id}/versions/{version_id}/attributes"
)
def get_document_attributes(
    document_id: str,
    version_id: str,
    db: Session = Depends(get_db),
):
    version = db.query(DocumentVersion).filter(
        DocumentVersion.id == version_id,
        DocumentVersion.document_id == document_id,
    ).first()

    if not version:
        raise HTTPException(
            status_code=404,
            detail="Версия документа не найдена",
        )

    attributes = db.query(DocumentAttribute).filter(
        DocumentAttribute.version_id == version_id
    ).all()

    return {
        "version_id": version_id,
        "attributes": [
            {
                "attribute_type": item.attribute_type,
                "value": item.value,
                "status": item.status,
                "source_fragment": item.source_fragment,
                "date_role": item.date_role,
            }
            for item in attributes
        ],
    }



class EditInstructionRequest(BaseModel):
    instruction: str


@router.post(
    "/documents/{document_id}/versions/{version_id}/edit-instruction-preview"
)
def preview_edit_by_instruction(
    document_id: str,
    version_id: str,
    request: EditInstructionRequest,
    db: Session = Depends(get_db),
):
    """
    Готовит предварительную редакцию DOCX по инструкции пользователя.
    Новая версия не создаётся до вызова edit-confirm.
    """

    version = db.query(DocumentVersion).filter(
        DocumentVersion.id == version_id,
        DocumentVersion.document_id == document_id,
    ).first()

    if version is None:
        raise HTTPException(
            status_code=404,
            detail="Версия документа не найдена",
        )

    instruction = request.instruction.strip()
    if not instruction:
        raise HTTPException(
            status_code=422,
            detail="Инструкция не может быть пустой",
        )

    # Пока поддерживаем DOCX: PDF требует отдельного механизма
    # восстановления страниц и исходного оформления.
    if version.file_type != ".docx":
        raise HTTPException(
            status_code=400,
            detail="Редактирование по инструкции пока поддерживает только DOCX",
        )

    source_text = version.extracted_text or ""
    if not source_text.strip():
        raise HTTPException(
            status_code=400,
            detail="В исходной версии нет текста для редактирования",
        )

    edit_id = str(uuid4())
    pending_path = PENDING_EDIT_DIR / f"{edit_id}.docx"
    metadata_path = PENDING_EDIT_DIR / f"{edit_id}.json"

    try:
        # 1. Готовим изменённый текст с помощью Qwen.
        edited_text = generate_edited_text(
            instruction=instruction,
            document_text=source_text,
        )

        if not edited_text or not edited_text.strip():
            raise ValueError("LLM вернула пустой текст")

        # Не создаём подтверждение, если модель ничего не изменила.
        diff = compare_texts(source_text, edited_text)
        if not diff:
            raise HTTPException(
                status_code=422,
                detail="Модель не внесла изменений. Уточните инструкцию.",
            )

        # 2. Применяем изменённый текст к копии исходного DOCX.
        # Сохраняем стили абзацев, колонтитулы и настройки страницы.
        source_file_path = resolve_project_path(version.file_path)

        if not source_file_path.is_file():
            raise FileNotFoundError(
                f"Исходный DOCX не найден: {source_file_path}"
            )

        apply_text_to_docx_preserving_structure(
            source_path=str(source_file_path),
            edited_text=edited_text,
            output_path=str(pending_path.resolve()),
        )

        # 3. Повторно извлекаем текст из созданного файла:
        # confirm endpoint будет использовать этот же файл.
        extracted = extract_document(str(pending_path))
        extracted_text = extracted.get("text", "")
        blocks = extracted.get("blocks", [])

        if not extracted_text.strip() or not blocks:
            raise ValueError("Не удалось извлечь текст из подготовленного DOCX")

        # Считаем diff по фактическому содержимому DOCX, а не только
        # по сырому ответу модели.
        diff = compare_texts(source_text, extracted_text)
        if not diff:
            raise HTTPException(
                status_code=422,
                detail="После формирования DOCX изменения не обнаружены",
            )

        # 4. Формат метаданных совместим с уже существующим
        # confirm_document_edit.
        metadata_path.write_text(
            json.dumps(
                {
                    "document_id": document_id,
                    "source_version_id": version_id,
                    "filename": version.filename,
                    "file_type": ".docx",
                    "pending_file": pending_path.name,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        return {
            "edit_id": edit_id,
            "document_id": document_id,
            "source_version_id": version_id,
            "status": "awaiting_confirmation",
            "message": (
                "Предварительная редакция готова. "
                "Новая версия ещё не создана."
            ),
            "instruction": instruction,
            "diff": diff,
        }

    except HTTPException:
        pending_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        raise

    except Exception:
        logger.exception(
            "Не удалось подготовить редактирование документа %s через LLM",
            document_id,
        )
        pending_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=503,
            detail="Не удалось подготовить изменения через языковую модель",
        )



class EditPreviewRequest(BaseModel):
    """Зарезервировано для будущего редактирования по текстовой инструкции."""
    pass


@router.post(
    "/documents/{document_id}/versions/{version_id}/edit-preview"
)
async def preview_document_edit(
    document_id: str,
    version_id: str,
    edited_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Загружает подготовленный пользователем изменённый файл,
    извлекает текст и показывает diff без создания новой версии.
    """
    version = db.query(DocumentVersion).filter(
        DocumentVersion.id == version_id,
        DocumentVersion.document_id == document_id,
    ).first()

    if version is None:
        raise HTTPException(status_code=404, detail="Версия документа не найдена")

    filename = edited_file.filename or version.filename
    extension = validate_file(filename)

    if extension != version.file_type:
        raise HTTPException(
            status_code=400,
            detail="Формат изменённого файла должен совпадать с исходным",
        )

    content = await edited_file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Загружен пустой файл")

    edit_id = str(uuid4())
    pending_path = PENDING_EDIT_DIR / f"{edit_id}{extension}"
    metadata_path = PENDING_EDIT_DIR / f"{edit_id}.json"

    try:
        pending_path.write_bytes(content)
        extracted = extract_document(str(pending_path))
        edited_text = extracted["text"]
        blocks = extracted["blocks"]

        if not edited_text.strip() or not blocks:
            raise ValueError("В изменённом файле не удалось извлечь текст")

        diff = compare_texts(version.extracted_text or "", edited_text)

        metadata_path.write_text(
            json.dumps(
                {
                    "document_id": document_id,
                    "source_version_id": version_id,
                    "filename": filename,
                    "file_type": extension,
                    "pending_file": pending_path.name,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        return {
            "edit_id": edit_id,
            "document_id": document_id,
            "source_version_id": version_id,
            "status": "awaiting_confirmation",
            "message": "Предварительный просмотр готов. Новая версия ещё не создана.",
            "diff": diff,
        }

    except HTTPException:
        pending_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        raise
    except Exception:
        logger.exception("Не удалось подготовить предварительный просмотр изменений")
        pending_path.unlink(missing_ok=True)
        metadata_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="Не удалось обработать изменённый файл",
        )


@router.post("/documents/{document_id}/edit-confirm/{edit_id}")
def confirm_document_edit(
    document_id: str,
    edit_id: str,
    db: Session = Depends(get_db),
):
    """
    Подтверждает ранее подготовленное изменение и создаёт новую версию.
    """
    metadata_path = PENDING_EDIT_DIR / f"{edit_id}.json"

    if not metadata_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Изменение не найдено или срок его хранения истёк",
        )

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Повреждены данные изменения")

    if metadata.get("document_id") != document_id:
        raise HTTPException(status_code=404, detail="Изменение не найдено")

    document = db.query(Document).filter(
        Document.id == document_id
    ).first()
    if document is None:
        raise HTTPException(status_code=404, detail="Документ не найден")

    source_version = db.query(DocumentVersion).filter(
        DocumentVersion.id == metadata.get("source_version_id"),
        DocumentVersion.document_id == document_id,
    ).first()
    if source_version is None:
        raise HTTPException(status_code=404, detail="Исходная версия не найдена")

    pending_path = PENDING_EDIT_DIR / metadata["pending_file"]
    if not pending_path.is_file():
        raise HTTPException(status_code=404, detail="Файл изменений не найден")

    last_version = db.query(DocumentVersion).filter(
        DocumentVersion.document_id == document_id
    ).order_by(DocumentVersion.version_number.desc()).first()

    next_version_number = last_version.version_number + 1 if last_version else 1
    version_id = str(uuid4())
    extension = metadata["file_type"]
    final_path = UPLOAD_DIR / f"{version_id}{extension}"

    try:
        # Повторно извлекаем содержимое при подтверждении.
        extracted = extract_document(str(pending_path))
        extracted_text = extracted["text"]
        blocks = extracted["blocks"]

        if not extracted_text.strip() or not blocks:
            raise ValueError("В файле нет извлекаемого текста")

        final_path.write_bytes(pending_path.read_bytes())

        db_version = DocumentVersion(
            id=version_id,
            document_id=document_id,
            version_number=next_version_number,
            filename=metadata["filename"],
            file_path=str(final_path),
            file_type=extension,
            extracted_text=extracted_text,
        )
        db.add(db_version)
        db.flush()

        add_document_blocks(
            db=db,
            version_id=version_id,
            blocks=blocks,
        )

        # Сохраняем атрибуты новой версии тем же правиловым экстрактором.
        attributes = extract_attributes(extracted_text)
        for item in attributes:
            db.add(
                DocumentAttribute(
                    id=str(uuid4()),
                    version_id=version_id,
                    attribute_type=item["attribute_type"],
                    value=item["value"],
                    status=item["status"],
                    source_fragment=item["source_fragment"],
                    date_role=item["date_role"],
                )
            )

        db.commit()

    except Exception:
        logger.exception("Не удалось подтвердить изменение документа %s", document_id)
        db.rollback()
        final_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="Не удалось сохранить новую версию",
        )

    indexing_result = index_document_safely(
        document_id=document_id,
        version_id=version_id,
        blocks=blocks,
    )

    pending_path.unlink(missing_ok=True)
    metadata_path.unlink(missing_ok=True)

    return {
        "document_id": document_id,
        "source_version_id": source_version.id,
        "version_id": version_id,
        "version_number": next_version_number,
        "filename": metadata["filename"],
        "status": "processed",
        **indexing_result,
    }
