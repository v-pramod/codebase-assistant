from dataclasses import dataclass
from importlib import import_module
from typing import Any, Protocol


class VectorStoreError(Exception):
    pass


@dataclass(frozen=True)
class VectorRecord:
    chunk_id: str
    embedding: list[float]
    text: str
    metadata: dict[str, str | int | bool | None]


class ChunkVectorStore(Protocol):
    def add_records(self, records: list[VectorRecord], embedding_model: str) -> None: ...

    def active_records(self, repo_id: str, snapshot_id: str) -> list[VectorRecord]: ...


class InMemoryChunkVectorStore:
    def __init__(self) -> None:
        self.records: list[VectorRecord] = []
        self.embedding_model: str | None = None
        self.dimension: int | None = None

    def add_records(self, records: list[VectorRecord], embedding_model: str) -> None:
        if not records:
            return
        dimension = len(records[0].embedding)
        if self.embedding_model is not None and self.embedding_model != embedding_model:
            raise VectorStoreError("Embedding model changed for this collection.")
        if self.dimension is not None and self.dimension != dimension:
            raise VectorStoreError("Embedding dimension changed for this collection.")
        if any(len(record.embedding) != dimension for record in records):
            raise VectorStoreError("Embedding batch contains mixed dimensions.")
        self.embedding_model = embedding_model
        self.dimension = dimension
        self.records.extend(records)

    def active_records(self, repo_id: str, snapshot_id: str) -> list[VectorRecord]:
        return [
            record
            for record in self.records
            if record.metadata.get("repo_id") == repo_id
            and record.metadata.get("snapshot_id") == snapshot_id
            and record.metadata.get("active") is True
        ]


class ChromaChunkVectorStore:
    def __init__(self, persist_path: str, collection_name: str = "code_chunks") -> None:
        chromadb: Any = import_module("chromadb")
        self._client: Any = chromadb.PersistentClient(path=persist_path)
        self._collection: Any = self._client.get_or_create_collection(collection_name)

    def add_records(self, records: list[VectorRecord], embedding_model: str) -> None:
        if not records:
            return
        metadatas = []
        for record in records:
            metadata = dict(record.metadata)
            metadata["embedding_model"] = embedding_model
            metadata["embedding_dimension"] = len(record.embedding)
            metadatas.append(metadata)
        self._collection.add(
            ids=[record.chunk_id for record in records],
            embeddings=[record.embedding for record in records],
            documents=[record.text for record in records],
            metadatas=metadatas,
        )

    def active_records(self, repo_id: str, snapshot_id: str) -> list[VectorRecord]:
        result = self._collection.get(
            where={"$and": [{"repo_id": repo_id}, {"snapshot_id": snapshot_id}, {"active": True}]},
            include=["embeddings", "documents", "metadatas"],
        )
        ids = result.get("ids", [])
        embeddings = result.get("embeddings", [])
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])
        return [
            VectorRecord(
                chunk_id=str(ids[index]),
                embedding=[float(value) for value in embeddings[index]],
                text=str(documents[index]),
                metadata=dict(metadatas[index]),
            )
            for index in range(len(ids))
        ]
