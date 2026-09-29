"""Runtime helpers for sharing a single initialized VectorMemory per event loop."""
from __future__ import annotations

import asyncio

from memory.vector_memory import VectorMemory

_VECTOR_MEMORY_BY_LOOP: dict[int, VectorMemory] = {}
_VECTOR_MEMORY_LOCKS: dict[int, asyncio.Lock] = {}


async def get_shared_vector_memory() -> VectorMemory:
    """Return a shared initialized VectorMemory for the current event loop."""
    loop = asyncio.get_running_loop()
    loop_key = id(loop)
    cached = _VECTOR_MEMORY_BY_LOOP.get(loop_key)
    if cached is not None:
        return cached

    lock = _VECTOR_MEMORY_LOCKS.get(loop_key)
    if lock is None:
        lock = asyncio.Lock()
        _VECTOR_MEMORY_LOCKS[loop_key] = lock

    async with lock:
        cached = _VECTOR_MEMORY_BY_LOOP.get(loop_key)
        if cached is not None:
            return cached
        vm = VectorMemory()
        await vm.initialize()
        _VECTOR_MEMORY_BY_LOOP[loop_key] = vm
        return vm


async def close_shared_vector_memory() -> None:
    """Best-effort shutdown for all shared VectorMemory instances."""
    cached = list(_VECTOR_MEMORY_BY_LOOP.values())
    _VECTOR_MEMORY_BY_LOOP.clear()
    _VECTOR_MEMORY_LOCKS.clear()
    for vm in cached:
        try:
            await vm.close()
        except Exception:
            pass


def reset_shared_vector_memory() -> None:
    """Clear cached VectorMemory instances. Intended for tests."""
    _VECTOR_MEMORY_BY_LOOP.clear()
    _VECTOR_MEMORY_LOCKS.clear()
