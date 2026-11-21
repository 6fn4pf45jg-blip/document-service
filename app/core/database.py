
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = "postgresql://ekaterinapilkina@localhost:5432/document_service"

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Base = declarative_base()

def create_tables():
    from app.models.document import Document
    from app.models.document_version import DocumentVersion
    from app.models.document_block import DocumentBlock
    from app.models.document_attribute import DocumentAttribute

    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()