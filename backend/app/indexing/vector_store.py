from dataclasses import dataclass
from hashlib import sha256
from importlib import import_module
from typing import Any, Protocol

type MetadataValue = str | int | float | bool


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

    def deactivate_snapshot(self, repo_id: str, snapshot_id: str) -> None: ...

    def copy_active_records(
        self,
        repo_id: str,
        source_snapshot_id: str,
        target_snapshot_id: str,
        exclude_paths: set[str],
    ) -> None: ...


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

    def deactivate_snapshot(self, repo_id: str, snapshot_id: str) -> None:
        for record in self.records:
            if (
                record.metadata.get("repo_id") == repo_id
                and record.metadata.get("snapshot_id") == snapshot_id
            ):
                record.metadata["active"] = False

    def copy_active_records(
        self,
        repo_id: str,
        source_snapshot_id: str,
        target_snapshot_id: str,
        exclude_paths: set[str],
    ) -> None:
        copies = []
        for record in self.active_records(repo_id, source_snapshot_id):
            if str(record.metadata.get("path")) in exclude_paths:
                continue
            metadata = dict(record.metadata)
            metadata["snapshot_id"] = target_snapshot_id
            metadata["active"] = True
            copies.append(
                VectorRecord(
                    chunk_id=_copied_chunk_id(record.chunk_id, target_snapshot_id),
                    embedding=list(record.embedding),
                    text=record.text,
                    metadata=metadata,
                )
            )
        self.add_records(copies, self.embedding_model or "unknown")


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
            metadata = _chroma_metadata(record.metadata)
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

    def deactivate_snapshot(self, repo_id: str, snapshot_id: str) -> None:
        result = self._collection.get(
            where={"$and": [{"repo_id": repo_id}, {"snapshot_id": snapshot_id}]}
        )
        ids = result.get("ids", [])
        if ids:
            self._collection.update(ids=ids, metadatas=[{"active": False} for _ in ids])

    def copy_active_records(
        self,
        repo_id: str,
        source_snapshot_id: str,
        target_snapshot_id: str,
        exclude_paths: set[str],
    ) -> None:
        records = [
            record
            for record in self.active_records(repo_id, source_snapshot_id)
            if str(record.metadata.get("path")) not in exclude_paths
        ]
        if not records:
            return
        copied = []
        embedding_model = str(records[0].metadata.get("embedding_model") or "unknown")
        for record in records:
            metadata = dict(record.metadata)
            metadata["snapshot_id"] = target_snapshot_id
            metadata["active"] = True
            copied.append(
                VectorRecord(
                    chunk_id=_copied_chunk_id(record.chunk_id, target_snapshot_id),
                    embedding=record.embedding,
                    text=record.text,
                    metadata=metadata,
                )
            )
        self.add_records(copied, embedding_model)


def _copied_chunk_id(source_chunk_id: str, target_snapshot_id: str) -> str:
    return sha256(f"{source_chunk_id}|{target_snapshot_id}".encode()).hexdigest()


def _chroma_metadata(metadata: dict[str, str | int | bool | None]) -> dict[str, MetadataValue]:
    return {key: value for key, value in metadata.items() if value is not None}
