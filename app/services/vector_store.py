
from functools import lru_cache
from uuid import uuid5, NAMESPACE_URL

from qdrant_client import QdrantClient, models
from sentence_transformers import SentenceTransformer


QDRANT_URL = "http://localhost:6333"
COLLECTION_NAME = "document_chunks"
MODEL_NAME = "intfloat/multilingual-e5-small"


@lru_cache(maxsize=1)
def get_client():
    """Создаёт подключение к Qdrant."""
    return QdrantClient(
        url=QDRANT_URL,
        timeout=60,
        check_compatibility=False
    )


@lru_cache(maxsize=1)
def get_model():
    """Загружает модель для преобразования текста в векторы."""
    return SentenceTransformer(MODEL_NAME)


def ensure_collection():
    """Создаёт коллекцию Qdrant, если её ещё нет."""

    client = get_client()

    if not client.collection_exists(COLLECTION_NAME):
        model = get_model()

        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=model.get_sentence_embedding_dimension(),
                distance=models.Distance.COSINE
            )
        )

    return COLLECTION_NAME


def index_blocks(
    document_id: str,
    version_id: str,
    blocks: list[dict]
):
    """Индексирует текстовые блоки и таблицы документа в Qdrant."""

    if not blocks:
        return 0

    client = get_client()
    model = get_model()

    ensure_collection()

    points = []

    for block in blocks:
        content = block.get("content") or block

        block_type = block.get("type", "paragraph")
        rows = content.get("rows", [])

        text = content.get("text", "").strip()

        # Если текст таблицы отсутствует, формируем его из строк.
        if not text and rows:
            text = "\n".join(
                " | ".join(str(cell) for cell in row)
                for row in rows
            )

        if not text:
            continue

        vector = model.encode(
            f"passage: {text}",
            normalize_embeddings=True
        ).tolist()

        point_id = str(
            uuid5(
                NAMESPACE_URL,
                f"{version_id}:{block['block_id']}"
            )
        )

        payload = {
            "document_id": document_id,
            "version_id": version_id,
            "block_id": block["block_id"],
            "type": block_type,
            "section": block.get("section"),
            "text": text,
            "page_number": content.get("page_number")
        }

        if block_type == "table":
            payload["rows"] = rows

        points.append(
            models.PointStruct(
                id=point_id,
                vector=vector,
                payload=payload
            )
        )

    if points:
        client.upsert(
            collection_name=COLLECTION_NAME,
            points=points,
            wait=True
        )

    return len(points)


def search_blocks(
    document_id: str,
    version_id: str,
    query: str,
    limit: int = 5
):
    """Ищет информацию в конкретной версии документа."""

    query = query.strip()

    if not query:
        raise ValueError("Поисковый запрос не может быть пустым")

    client = get_client()
    model = get_model()

    ensure_collection()

    query_vector = model.encode(
        f"query: {query}",
        normalize_embeddings=True
    ).tolist()

    response = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=models.Filter(
            must=[
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchValue(value=document_id)
                ),
                models.FieldCondition(
                    key="version_id",
                    match=models.MatchValue(value=version_id)
                )
            ]
        ),
        limit=limit,
        with_payload=True
    )

    return [
        {
            **point.payload,
            "score": point.score
        }
        for point in response.points
    ]
