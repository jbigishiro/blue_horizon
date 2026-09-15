
from redisvl.schema import IndexSchema
import argparse
from sqlalchemy import text as sql_text
from llama_index.core import Document, StorageContext, VectorStoreIndex
from llama_index.core.settings import Settings
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.vector_stores.redis import RedisVectorStore
from src.utils.db import engine
from src.config.constants import REDIS_URL


INDEX_NAME = "blue_horizon_knowledge"
INDEX_PREFIX = "bh_doc"
EMBEDDING_DIMS = 1536
Settings.embed_model = OpenAIEmbedding(model="text-embedding-3-small")

def build_redis_schema() -> IndexSchema:
    return IndexSchema.from_dict({
        "index": {"name": INDEX_NAME, "prefix": INDEX_PREFIX},
        "fields": [
       
            {"type": "tag", "name": "id"},
            {"type": "tag", "name": "doc_id"},
            {"type": "text", "name": "text"},
            {
                "type": "vector",
                "name": "vector",
                "attrs": {
                    "dims": EMBEDDING_DIMS,
                    "algorithm": "hnsw",
                    "distance_metric": "cosine",
                },
            },
            {"type": "tag", "name": "source"},
            {"type": "tag", "name": "category"},
        ],
    })

def _fetch_rows(query: str) -> list[dict]:
    with engine.connect() as conn:
        result = conn.execute(sql_text(query))
        columns = result.keys()
        return [dict(zip(columns, row)) for row in result.fetchall()]


def build_documents() -> list[Document]:
    """
    Converts rows from all three source tables into LlamaIndex Documents.
    Each Document's `text` is what gets embedded and matched against a
    guest's question; `metadata` is extra context carried alongside for
    filtering/citation, not directly embedded.
    """
    documents = []

    # --- FAQs ---------------------------------------------------------
    faqs = _fetch_rows(
        "SELECT faq_id, category, subcategory, question, answer, keywords "
        "FROM faq_knowledge_base"
    )
    for row in faqs:
        text = f"Q: {row['question']}\nA: {row['answer']}"
        documents.append(Document(
            text=text,
            metadata={
                "source": "faq_knowledge_base",
                "id": row["faq_id"],
                "category": row["category"],
                "subcategory": row["subcategory"],
            },
        ))

    # --- Amenities ------------------------------------------------------
    amenities = _fetch_rows(
        "SELECT amenity_id, category, name, price, duration, description, "
        "availability, location, booking_required, min_notice_hours "
        "FROM amenities"
    )
    for row in amenities:
        text = (
            f"Amenity: {row['name']} ({row['category']})\n"
            f"Description: {row['description']}\n"
            f"Price: {row['price']}, Duration: {row['duration']} minutes\n"
            f"Availability: {row['availability']}, Location: {row['location']}\n"
            f"Booking required: {row['booking_required']}"
            + (f", minimum notice: {row['min_notice_hours']} hours"
               if row["booking_required"] else "")
        )
        documents.append(Document(
            text=text,
            metadata={
                "source": "amenities",
                "id": row["amenity_id"],
                "category": row["category"],
            },
        ))

    # --- Recommendations 
    recs = _fetch_rows(
        "SELECT recommendation_id, category, name, description, address, "
        "distance_km, price_range, rating, booking_required "
        "FROM recommendations_knowledge_base"
    )
    for row in recs:
        text = (
            f"Recommendation: {row['name']} ({row['category']})\n"
            f"Description: {row['description']}\n"
            f"Address: {row['address']}, {row['distance_km']} km from the hotel\n"
            f"Price range: {row['price_range']}, Rating: {row['rating']}\n"
            f"Booking required: {row['booking_required']}"
        )
        documents.append(Document(
            text=text,
            metadata={
                "source": "recommendations_knowledge_base",
                "id": row["recommendation_id"],
                "category": row["category"],
            },
        ))

    return documents


def build_index(reset: bool = False):
    vector_store = RedisVectorStore(
        schema=build_redis_schema(),
        redis_url=REDIS_URL,
        overwrite=reset,
    )
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    documents = build_documents()
    print(f"Built {len(documents)} documents from Postgres "
          f"({sum(1 for d in documents if d.metadata['source'] == 'faq_knowledge_base')} FAQs, "
          f"{sum(1 for d in documents if d.metadata['source'] == 'amenities')} amenities, "
          f"{sum(1 for d in documents if d.metadata['source'] == 'recommendations_knowledge_base')} recommendations)")

    index = VectorStoreIndex.from_documents(
        documents,
        storage_context=storage_context,
    )
    print(f"Indexed into Redis (index name: '{INDEX_NAME}').")
    return index


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the Blue Horizon RAG index.")
    parser.add_argument(
        "--reset", action="store_true",
        help="Drop and rebuild the Redis index from scratch instead of updating it.",
    )
    args = parser.parse_args()
    build_index(reset=args.reset)