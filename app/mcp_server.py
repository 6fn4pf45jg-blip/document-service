
from mcp.server.fastmcp import FastMCP
from sqlalchemy import or_, func
from fastapi import HTTPException

from app.core.database import SessionLocal
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.document_block import DocumentBlock
from app.api.routes import (
    preview_edit_by_instruction,
    EditInstructionRequest,
)


mcp = FastMCP("Document Service")


@mcp.tool()
def search_documents(query: str, limit: int = 10) -> list[dict]:
    """Ищет документы по названию или содержимому последней версии."""

    query = query.strip()

    if not query:
        return []

    limit = max(1, min(limit, 50))
    db = SessionLocal()

    try:
        latest_versions = (
            db.query(
                DocumentVersion.document_id,
                func.max(DocumentVersion.version_number).label("max_version")
            )
            .group_by(DocumentVersion.document_id)
            .subquery()
        )

        results = (
            db.query(Document, DocumentVersion)
            .join(
                latest_versions,
                latest_versions.c.document_id == Document.id
            )
            .join(
                DocumentVersion,
                (DocumentVersion.document_id == Document.id)
                & (DocumentVersion.version_number == latest_versions.c.max_version)
            )
            .filter(
                or_(
                    Document.filename.ilike(search_pattern),
                    DocumentVersion.extracted_text.ilike(search_pattern)
                )
            )
            .limit(limit)
            .all()
        )

        return [
            {
                "document_id": str(document.id),
                "filename": document.filename,
                "status": document.status,
                "version_id": str(version.id),
                "version_number": version.version_number,
                "file_type": version.file_type
            }
            for document, version in results
        ]

    finally:
        db.close()



@mcp.tool()
def get_document_version(document_id: str, version_number: int) -> dict:
    """Возвращает текст и структурные блоки указанной версии документа."""

    db = SessionLocal()

    try:
        version = (
            db.query(DocumentVersion)
            .filter(
                DocumentVersion.document_id == document_id,
                DocumentVersion.version_number == version_number
            )
            .first()
        )

        if version is None:
            return {
                "status": "not_found",
                "message": "Версия документа не найдена."
            }

        blocks = (
            db.query(DocumentBlock)
            .filter(DocumentBlock.version_id == version.id)
            .order_by(DocumentBlock.block_id)
            .all()
        )

        return {
            "status": "success",
            "document_id": str(document_id),
            "version_id": str(version.id),
            "version_number": version.version_number,
            "filename": version.filename,
            "file_type": version.file_type,
            "content": version.extracted_text or "",
            "blocks": [
                {
                    "block_id": block.block_id,
                    "type": block.type,
                    "section": block.section,
                    "content": block.content
                }
                for block in blocks
            ]
        }

    finally:
        db.close()


@mcp.tool()
def prepare_document_edit(
    document_id: str,
    version_id: str,
    instruction: str
) -> dict:
    """
    Подготавливает редакцию DOCX по инструкции.
    Возвращает diff и edit_id. Не создаёт новую версию.
    """

    db = SessionLocal()

    try:
        result = preview_edit_by_instruction(
            document_id=document_id,
            version_id=version_id,
            request=EditInstructionRequest(instruction=instruction),
            db=db,
        )
        return result

    except HTTPException as exc:
        return {
            "status": "error",
            "http_status": exc.status_code,
            "message": exc.detail
        }

    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc)
        }

    finally:
        db.close()


if __name__ == "__main__":

    mcp.run(transport="stdio")
