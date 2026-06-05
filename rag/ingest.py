import os
import sys
import fitz  # pymupdf
import requests
import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

chroma_client = chromadb.PersistentClient(path="./chroma_db")
embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

def get_or_create_collection():
    return chroma_client.get_or_create_collection("persona")

def embed(texts: list[str]) -> list[list[float]]:
    return embedding_model.encode(texts).tolist()

def ingest_resume(pdf_path: str = "resume.pdf"):
    doc = fitz.open(pdf_path)
    full_text = ""
    for page in doc:
        full_text += page.get_text()

    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = splitter.split_text(full_text)

    collection = get_or_create_collection()

    # Delete existing resume chunks first
    try:
        existing = collection.get(where={"source": "resume"})
        if existing["ids"]:
            collection.delete(ids=existing["ids"])
    except:
        pass

    collection.add(
        documents=chunks,
        embeddings=embed(chunks),
        ids=[f"resume_{i}" for i in range(len(chunks))],
        metadatas=[{"source": "resume"} for _ in chunks]
    )
    print(f"Ingested {len(chunks)} resume chunks from {pdf_path}")

def ingest_github(github_username: str):
    headers = {"Authorization": f"token {os.getenv('GITHUB_TOKEN')}"} if os.getenv("GITHUB_TOKEN") else {}

    res = requests.get(
        f"https://api.github.com/users/{github_username}/repos",
        headers=headers,
        params={"per_page": 50}
    )
    repos = res.json()

    collection = get_or_create_collection()
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)

    for repo in repos:
        name = repo["name"]
        description = repo.get("description") or ""
        language = repo.get("language") or ""
        topics = ", ".join(repo.get("topics", []))

        readme_text = ""
        readme_res = requests.get(
            f"https://api.github.com/repos/{github_username}/{name}/readme",
            headers={**headers, "Accept": "application/vnd.github.raw"},
        )
        if readme_res.status_code == 200:
            readme_text = readme_res.text[:3000]

        full_text = f"""
Repo: {name}
Description: {description}
Language: {language}
Topics: {topics}
README:
{readme_text}
""".strip()

        chunks = splitter.split_text(full_text)
        if not chunks:
            continue

        # Delete existing chunks for this repo
        try:
            existing = collection.get(where={"repo": name})
            if existing["ids"]:
                collection.delete(ids=existing["ids"])
        except:
            pass

        collection.add(
            documents=chunks,
            embeddings=embed(chunks),
            ids=[f"{name}_{i}" for i in range(len(chunks))],
            metadatas=[{"source": "github", "repo": name} for _ in chunks]
        )
        print(f"Ingested {name} ({len(chunks)} chunks)")

def ingest_commits(github_username: str):
    headers = {"Authorization": f"token {os.getenv('GITHUB_TOKEN')}"} if os.getenv("GITHUB_TOKEN") else {}
    collection = get_or_create_collection()
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)

    # Get all public repos
    res = requests.get(
        f"https://api.github.com/users/{github_username}/repos",
        headers=headers,
        params={"per_page": 50}
    )
    repos = res.json()

    for repo in repos:
        repo_name = repo["name"]
        
        commits_res = requests.get(
            f"https://api.github.com/repos/{github_username}/{repo_name}/commits",
            headers=headers,
            params={"per_page": 50}  # last 50 commits per repo
        )
        if commits_res.status_code != 200:
            print(f"Skipping commits for {repo_name}: {commits_res.status_code}")
            continue

        commits = commits_res.json()
        if not commits:
            continue

        commit_texts = []
        for c in commits:
            sha = c["sha"][:7]
            msg = c["commit"]["message"]
            date = c["commit"]["author"]["date"][:10]
            commit_texts.append(f"[{date}] {sha}: {msg}")

        full_text = f"Commit history for {repo_name}:\n" + "\n".join(commit_texts)
        chunks = splitter.split_text(full_text)
        if not chunks:
            continue

        # Delete existing
        try:
            existing = collection.get(where={"type": f"commits_{repo_name}"})
            if existing["ids"]:
                collection.delete(ids=existing["ids"])
        except:
            pass

        collection.add(
            documents=chunks,
            embeddings=embed(chunks),
            ids=[f"commits_{repo_name}_{i}" for i in range(len(chunks))],
            metadatas=[{"source": "github_commits", "repo": repo_name, "type": f"commits_{repo_name}"} for _ in chunks]
        )
        print(f"Ingested {len(chunks)} commit chunks for {repo_name}")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    ingest_resume("resume.pdf")
    ingest_github("jahnavinischal")
    ingest_commits("jahnavinischal")  
    print("Ingestion complete!")