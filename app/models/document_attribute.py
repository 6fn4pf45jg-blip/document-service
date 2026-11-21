from sqlalchemy import Column, String, ForeignKey, Text, DateTime
from sqlalchemy.sql import func

from app.core.database import Base


class DocumentAttribute(Base):
    __tablename__ = "document_attributes"

    id = Column(String, primary_key=True, index=True)

    version_id = Column(
        String,
        ForeignKey("document_versions.id"),
        nullable=False,
        index=True,
    )

    attribute_type = Column(String, nullable=False)
    value = Column(Text, nullable=True)
    status = Column(String, nullable=False)

    source_fragment = Column(Text, nullable=True)
    date_role = Column(String, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
