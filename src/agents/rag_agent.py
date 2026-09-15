import sys
from llama_index.core import VectorStoreIndex
from llama_index.core.settings import Settings
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAI as LlamaOpenAI
from llama_index.vector_stores.redis import RedisVectorStore
from src.utils.redis_index import build_redis_schema
from src.config.constants import REDIS_URL, MODEL_NAME


Settings.embed_model = OpenAIEmbedding(model="text-embedding-3-small")
Settings.llm = LlamaOpenAI(model=MODEL_NAME, temperature=0)

QA_INSTRUCTION = '''
    You are the Blue Horizon hotel concierge. Answer the guest's question
   using ONLY the information in the provided context below. If the 
    context doesn't contain enough information to answer confidently, say 
    so plainly and suggest the guest contact the front desk — do not 
    guess or use outside knowledge about hotels in general. Keep the 
    answer concise and guest-friendly, not a raw data dump.
'''


def _get_index() -> VectorStoreIndex:
    """
    Connects to the existing Redis index built by redis_index.py.
    Does NOT rebuild anything — if the index doesn't exist yet, this
    will raise an error telling you to run redis_index.py first.
    """
    vector_store = RedisVectorStore(
        schema=build_redis_schema(),
        redis_url=REDIS_URL,
        overwrite=False,
    )
    return VectorStoreIndex.from_vector_store(vector_store)


def answer_from_knowledge_base(question: str) -> dict:
    """
    Returns {"answer": str, "sources": list[dict]}.

    Each source dict has the metadata attached at index time (source
    table, id, category) plus a relevance score, so the answer can be
    traced back to what it was grounded in.
    """
    index = _get_index()
    query_engine = index.as_query_engine(
        similarity_top_k=5,
        system_prompt=QA_INSTRUCTION,
    )

    response = query_engine.query(question)

    sources = [
        {
            "text": node.node.get_content()[:200],  # preview, not full text
            "score": round(node.score, 3) if node.score is not None else None,
            **node.node.metadata,
        }
        for node in response.source_nodes
    ]

    return {"answer": str(response), "sources": sources}


if __name__ == "__main__":
    
    q = " ".join(sys.argv[1:]) or "What time is check-in?"
    print(f"Question: {q}\n")

    result = answer_from_knowledge_base(q)
    print(f"Answer: {result['answer']}\n")
    print("Sources:")
    for s in result["sources"]:
        print(f"  [{s['source']}] {s.get('id')} (score={s['score']}) — {s['text']}")