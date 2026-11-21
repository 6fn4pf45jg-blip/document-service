
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.document_block import DocumentBlock


def save_document_blocks(
    db: Session,
    version_id: str,
    blocks: list[dict]
):
    """Сохраняет структурированные блоки версии документа."""

    for block in blocks:
        if block["type"] == "table":
            rows = block.get("rows", [])

            table_text = block.get("text") or "\n".join(
                " | ".join(str(cell) for cell in row)
                for row in rows
            )

            content = {
                "text": table_text,
                "rows": rows
            }

        else:
            content = {
                "text": block["text"]
            }

        db_block = DocumentBlock(
            id=str(uuid4()),
            version_id=version_id,
            block_id=block["block_id"],
            type=block["type"],
            section=block.get("section"),
            content=content
        )

        db.add(db_block)

    db.commit()
