"""Vector memory using ChromaDB (local) with optional Pinecone cloud sync.

ChromaDB stores three collections:
  - match_history  — past predictions with outcomes
  - lessons        — learned insights from prediction errors
  - team_patterns  — recurring team behaviour snapshots

All ChromaDB operations are synchronous and wrapped in ``asyncio.to_thread``
so the bot's event loop stays unblocked.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

import config
from models import Lesson, SimilarMatch

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ChromaDB helpers
# ---------------------------------------------------------------------------

def _build_match_doc(
    team1: str, team2: str, league: str,
    form: str = "", h2h: str = "",
    prediction: str = "", result: str = "",
) -> str:
    """Build a single embedding string for a match document."""
    parts = [f"{team1} vs {team2}", f"League: {league}"]
    if form:
        parts.append(f"Form: {form}")
    if h2h:
        parts.append(f"H2H: {h2h}")
    if prediction:
        parts.append(f"Predicted: {prediction}")
    if result:
        parts.append(f"Result: {result}")
    return " | ".join(parts)


def _build_lesson_doc(lesson: Lesson) -> str:
    """Build a single embedding string for a lesson."""
    return (
        f"{lesson.category.value if hasattr(lesson.category, 'value') else lesson.category}: "
        f"{lesson.text} (confidence: {lesson.confidence:.2f}, "
        f"confirmed: {lesson.times_confirmed}x)"
    )


# ---------------------------------------------------------------------------
# VectorMemory
# ---------------------------------------------------------------------------

class VectorMemory:
    """Dual-store vector memory: ChromaDB (primary) + Pinecone (optional cloud).

    Usage::

        vm = VectorMemory()
        await vm.initialize()
        await vm.store_match(...)
        similar = await vm.find_similar_matches("Arsenal", "Chelsea", "EPL form strong")
    """

    def __init__(self) -> None:
        self._chroma: Optional[chromadb.ClientAPI] = None
        self._matches: Optional[chromadb.Collection] = None
        self._lessons: Optional[chromadb.Collection] = None
        self._teams: Optional[chromadb.Collection] = None
        self._pinecone_index = None
        self._ready = False

    # -- lifecycle -----------------------------------------------------------

    async def initialize(self) -> None:
        """Create ChromaDB client + collections; optionally connect Pinecone."""
        await asyncio.to_thread(self._init_chroma)
        if config.PINECONE_ENABLED:
            await asyncio.to_thread(self._init_pinecone)
        self._ready = True
        logger.info("VectorMemory initialized (chroma=%s, pinecone=%s)",
                     config.CHROMADB_PATH, config.PINECONE_ENABLED)

    def _init_chroma(self) -> None:
        self._chroma = chromadb.PersistentClient(
            path=config.CHROMADB_PATH,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        # get_or_create is idempotent
        self._matches = self._chroma.get_or_create_collection(
            name=config.CHROMADB_COLLECTION_MATCHES,
            metadata={"hnsw:space": "cosine"},
        )
        self._lessons = self._chroma.get_or_create_collection(
            name=config.CHROMADB_COLLECTION_LESSONS,
            metadata={"hnsw:space": "cosine"},
        )
        self._teams = self._chroma.get_or_create_collection(
            name=config.CHROMADB_COLLECTION_TEAM_PATTERNS,
            metadata={"hnsw:space": "cosine"},
        )

    def _init_pinecone(self) -> None:
        try:
            from pinecone import Pinecone

            pc = Pinecone(api_key=config.PINECONE_API_KEY)
            # If index exists, connect; otherwise caller should create it
            existing = [idx.name for idx in pc.list_indexes()]
            if config.PINECONE_INDEX_NAME in existing:
                self._pinecone_index = pc.Index(config.PINECONE_INDEX_NAME)
                logger.info("Connected to Pinecone index '%s'", config.PINECONE_INDEX_NAME)
            else:
                logger.warning(
                    "Pinecone index '%s' not found — cloud sync disabled. "
                    "Available: %s", config.PINECONE_INDEX_NAME, existing,
                )
        except Exception:
            logger.exception("Pinecone init failed — cloud sync disabled")

    @property
    def ready(self) -> bool:
        return self._ready

    async def close(self) -> None:
        """Best-effort cleanup for runtime shutdown and tests."""
        for resource in (self._pinecone_index, self._chroma):
            closer = getattr(resource, "close", None)
            if not callable(closer):
                continue
            result = closer()
            if inspect.isawaitable(result):
                await result
        self._matches = None
        self._lessons = None
        self._teams = None
        self._pinecone_index = None
        self._chroma = None
        self._ready = False

    # -- store ---------------------------------------------------------------

    async def store_match(
        self, *,
        match_id: str,
        team1: str, team2: str, league: str,
        form: str = "", h2h: str = "",
        prediction: str = "", result: str = "",
        metadata: Optional[dict] = None,
    ) -> None:
        """Embed and store a match in ChromaDB (upsert — safe to re-call)."""
        doc = _build_match_doc(team1, team2, league, form, h2h, prediction, result)
        meta = {
            "team1": team1, "team2": team2, "league": league,
            "has_result": bool(result),
        }
        if metadata:
            meta.update(metadata)
        await asyncio.to_thread(
            self._matches.upsert,
            ids=[match_id],
            documents=[doc],
            metadatas=[meta],
        )

    async def store_lesson(self, lesson: Lesson, lesson_db_id: int) -> None:
        """Embed a Lesson in ChromaDB."""
        doc = _build_lesson_doc(lesson)
        meta = {
            "category": lesson.category.value if hasattr(lesson.category, "value") else str(lesson.category),
            "confidence": lesson.confidence,
            "times_confirmed": lesson.times_confirmed,
            "db_id": lesson_db_id,
        }
        await asyncio.to_thread(
            self._lessons.upsert,
            ids=[f"lesson_{lesson_db_id}"],
            documents=[doc],
            metadatas=[meta],
        )

    async def store_team_pattern(
        self, *, team: str, pattern_id: str, text: str,
        metadata: Optional[dict] = None,
    ) -> None:
        """Embed a team behaviour pattern."""
        meta = {"team": team}
        if metadata:
            meta.update(metadata)
        await asyncio.to_thread(
            self._teams.upsert,
            ids=[pattern_id],
            documents=[text],
            metadatas=[meta],
        )

    # -- query ---------------------------------------------------------------

    async def find_similar_matches(
        self, team1: str, team2: str, context: str = "",
        top_k: int = config.VECTOR_TOP_K,
    ) -> list[SimilarMatch]:
        """Return the most similar past matches from ChromaDB."""
        query_text = f"{team1} vs {team2}"
        if context:
            query_text += f" | {context}"
        results = await asyncio.to_thread(
            self._matches.query,
            query_texts=[query_text],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        return self._parse_match_results(results)

    async def find_relevant_lessons(
        self, context: str, top_k: int = 10,
    ) -> list[Lesson]:
        """Return lessons most relevant to the given context string."""
        count = await asyncio.to_thread(self._lessons.count)
        if count == 0:
            return []
        actual_k = min(top_k, count)
        results = await asyncio.to_thread(
            self._lessons.query,
            query_texts=[context],
            n_results=actual_k,
            include=["documents", "metadatas", "distances"],
        )
        return self._parse_lesson_results(results)

    async def find_team_patterns(
        self, team: str, top_k: int = 5,
    ) -> list[dict]:
        """Find patterns related to a team."""
        count = await asyncio.to_thread(self._teams.count)
        if count == 0:
            return []
        actual_k = min(top_k, count)
        results = await asyncio.to_thread(
            self._teams.query,
            query_texts=[team],
            n_results=actual_k,
            include=["documents", "metadatas", "distances"],
        )
        patterns = []
        if results and results["documents"]:
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                patterns.append({
                    "text": doc,
                    "team": meta.get("team", ""),
                    "similarity": 1 - dist,  # cosine distance → similarity
                })
        return patterns

    # -- update --------------------------------------------------------------

    async def update_match_result(
        self, match_id: str, result: str, score: str = "",
    ) -> None:
        """Update an existing match document with the actual result."""
        existing = await asyncio.to_thread(
            self._matches.get,
            ids=[match_id],
            include=["documents", "metadatas"],
        )
        if not existing["documents"]:
            logger.warning("Match '%s' not found in vector store", match_id)
            return
        doc = existing["documents"][0]
        meta = existing["metadatas"][0]
        # Append result to the document text
        if "Result:" not in doc:
            doc += f" | Result: {result}"
            if score:
                doc += f" ({score})"
        meta["has_result"] = True
        meta["actual_result"] = result
        if score:
            meta["actual_score"] = score
        await asyncio.to_thread(
            self._matches.upsert,
            ids=[match_id],
            documents=[doc],
            metadatas=[meta],
        )

    # -- Pinecone sync -------------------------------------------------------

    async def sync_to_pinecone(self, lessons: list[dict]) -> int:
        """Sync unsynced lessons to Pinecone. Returns count synced."""
        if not self._pinecone_index or not lessons:
            return 0
        synced = 0
        try:
            records = []
            for les in lessons:
                records.append({
                    "_id": f"lesson_{les['id']}",
                    "text": (
                        f"{les['category']}: {les['text']} "
                        f"(confidence: {les['confidence']:.2f}, "
                        f"confirmed: {les['times_confirmed']}x)"
                    ),
                    "category": les["category"],
                    "confidence": les["confidence"],
                    "times_confirmed": les["times_confirmed"],
                    "db_id": les["id"],
                })
            await asyncio.to_thread(
                self._pinecone_index.upsert_records,
                "lessons",
                records,
            )
            synced = len(records)
            logger.info("Synced %d lessons to Pinecone", synced)
        except Exception:
            logger.exception("Pinecone sync failed")
        return synced

    # -- collection stats ----------------------------------------------------

    async def get_stats(self) -> dict:
        """Return document counts per collection."""
        m = await asyncio.to_thread(self._matches.count)
        l = await asyncio.to_thread(self._lessons.count)
        t = await asyncio.to_thread(self._teams.count)
        return {"matches": m, "lessons": l, "team_patterns": t}

    # -- internal helpers ----------------------------------------------------

    @staticmethod
    def _parse_match_results(results: dict) -> list[SimilarMatch]:
        matches: list[SimilarMatch] = []
        if not results or not results.get("documents"):
            return matches
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            # Extract prediction summary from document
            pred = ""
            for part in doc.split(" | "):
                if part.startswith("Predicted:"):
                    pred = part.replace("Predicted: ", "")
            matches.append(SimilarMatch(
                match_description=f"{meta.get('team1', '?')} vs {meta.get('team2', '?')}",
                prediction_summary=pred,
                actual_result=meta.get("actual_result"),
                actual_score=meta.get("actual_score"),
                similarity_score=round(1 - dist, 4),  # cosine distance → similarity
            ))
        return matches

    @staticmethod
    def _parse_lesson_results(results: dict) -> list[Lesson]:
        lessons: list[Lesson] = []
        if not results or not results.get("documents"):
            return lessons
        for doc, meta in zip(
            results["documents"][0],
            results["metadatas"][0],
        ):
            try:
                from models import LessonCategory
                lessons.append(Lesson(
                    id=meta.get("db_id"),
                    text=doc.split(": ", 1)[-1].split(" (confidence")[0] if ": " in doc else doc,
                    category=LessonCategory(meta.get("category", "OVERCONFIDENCE")),
                    confidence=meta.get("confidence", 0.5),
                    times_confirmed=meta.get("times_confirmed", 0),
                ))
            except (ValueError, KeyError):
                logger.debug("Skipping unparseable lesson result: %s", doc[:80])
        return lessons
