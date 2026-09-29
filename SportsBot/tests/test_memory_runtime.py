from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

import memory.runtime as runtime


class MemoryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        runtime.reset_shared_vector_memory()

    async def test_close_shared_vector_memory_closes_cached_instances(self):
        fake_vm = AsyncMock()
        runtime._VECTOR_MEMORY_BY_LOOP[1] = fake_vm
        runtime._VECTOR_MEMORY_BY_LOOP[2] = fake_vm

        await runtime.close_shared_vector_memory()

        self.assertEqual(fake_vm.close.await_count, 2)
        self.assertEqual(runtime._VECTOR_MEMORY_BY_LOOP, {})
        self.assertEqual(runtime._VECTOR_MEMORY_LOCKS, {})
