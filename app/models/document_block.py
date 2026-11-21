from sqlalchemy import Column, String, Integer, Text, ForeignKey, JSON, UniqueConstraint

from app.core.database import Base


class DocumentBlock(Base):
    __tablename__ = "document_blocks"

    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "block_id",
            name="uq_version_block"
        ),
    )

    id = Column(String, primary_key=True, index=True)

    version_id = Column(
        String,
        ForeignKey("document_versions.id"),
        nullable=False
    )

    block_id = Column(Integer, nullable=False)
    type = Column(String, nullable=False)
    section = Column(String, nullable=True)

    content = Column(JSON, nullable=False)
