from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb

from rag.parser import parse_policy_markdown


class ChromaPolicyStore:
    def __init__(
        self,
        persist_directory: Path,
        embedding_model: Any,
        collection_name: str = "policy_chunks",
    ) -> None:
        persist_directory.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(persist_directory))
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        self._embedding_model = embedding_model

    def ensure_index(self, markdown_path: Path) -> None:
        if self._collection.count() == 0:
            self.rebuild(markdown_path)

    def rebuild(self, markdown_path: Path) -> None:
        chunks = parse_policy_markdown(markdown_path.read_text(encoding="utf-8"))
        texts = [c["rendered_text"] for c in chunks]
        embeddings = self._embedding_model.embed_documents(texts)
        ids = [f"chunk_{i}" for i in range(len(chunks))]
        metadatas = [
            {
                "section_h2": c["section_h2"],
                "section_h3": c["section_h3"],
                "citation": c["citation"],
            }
            for c in chunks
        ]

        existing = self._collection.get()
        if existing["ids"]:
            self._collection.delete(ids=existing["ids"])

        self._collection.add(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def search(self, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        total = self._collection.count()
        if total == 0:
            return []
        results = self._collection.query(
            query_embeddings=[self._embedding_model.embed_query(query)],
            n_results=min(top_k, total),
            include=["documents", "metadatas", "distances"],
        )
        hits: list[dict[str, Any]] = []
        for i in range(len(results["ids"][0])):
            hits.append({
                "citation": results["metadatas"][0][i]["citation"],
                "content": results["documents"][0][i],
                "distance": results["distances"][0][i],
            })
        return hits
