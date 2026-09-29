"""Memory subsystem — vector search over match history, lessons, and team patterns."""

from memory.vector_memory import VectorMemory
from memory.base_memory import BaseMemory
from memory.runtime import (
    close_shared_vector_memory,
    get_shared_vector_memory,
    reset_shared_vector_memory,
)

__all__ = [
    "VectorMemory",
    "BaseMemory",
    "get_shared_vector_memory",
    "close_shared_vector_memory",
    "reset_shared_vector_memory",
]
