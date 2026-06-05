import chromadb
from sentence_transformers import SentenceTransformer
import sys

chroma_client = chromadb.PersistentClient(path="./chroma_db")
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

def get_context(query: str, n_results: int = 4) -> str:
    collection = chroma_client.get_or_create_collection("persona")
    query_embedding = embedding_model.encode([query]).tolist()
    results = collection.query(query_embeddings=query_embedding, n_results=n_results)
    
    sources = [m["source"] for m in results["metadatas"][0]]
    print("RETRIEVED:", sources, flush=True)  # add flush=True
    sys.stdout.flush()
    
    docs = results.get("documents", [[]])[0]
    return "\n---\n".join(docs) if docs else ""