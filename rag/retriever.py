import chromadb
from sentence_transformers import SentenceTransformer
import sys
import os
from dotenv import load_dotenv
load_dotenv()

# Explicitly set HF token
hf_token = os.getenv("HF_TOKEN")

# chroma_client = chromadb.PersistentClient(path="./chroma_db")
import os
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "chroma_db")
chroma_client = chromadb.PersistentClient(path=DB_PATH)

# embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
_embedding_model= None

def get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    return _embedding_model

# def get_context(query: str, n_results: int = 4) -> str:
#     collection = chroma_client.get_or_create_collection("persona")
#     query_embedding = embedding_model.encode([query]).tolist()
#     results = collection.query(query_embeddings=query_embedding, n_results=n_results)
    
#     sources = [m["source"] for m in results["metadatas"][0]]
#     print("RETRIEVED:", sources, flush=True)  # add flush=True
#     sys.stdout.flush()
    
#     docs = results.get("documents", [[]])[0]
#     return "\n---\n".join(docs) if docs else ""

def get_context(query: str, n_results: int = 4) -> str:
    collection = chroma_client.get_or_create_collection("persona")
    model = get_embedding_model()
    query_embedding = model.encode([query]).tolist()
    results = collection.query(query_embeddings=query_embedding, n_results=n_results)
    docs = results.get("documents", [[]])[0]
    return "\n---\n".join(docs) if docs else ""