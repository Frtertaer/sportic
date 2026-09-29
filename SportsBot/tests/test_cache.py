from __future__ import annotations

import asyncio
import unittest

from cache import TTLCache


class TTLCacheSingleFlightTest(unittest.IsolatedAsyncioTestCase):
    def test_make_key_preserves_team_order(self):
        direct = TTLCache.make_key("Arsenal", "Chelsea")
        reverse = TTLCache.make_key("Chelsea", "Arsenal")

        self.assertNotEqual(direct, reverse)

    async def test_claim_singleflight_computes_once(self):
        cache = TTLCache(ttl=60, maxsize=8)
        load_calls = 0

        async def load_once():
            nonlocal load_calls
            state, value = await cache.claim("match")
            if state == "cached":
                return value
            if state == "wait":
                return await value

            load_calls += 1
            await asyncio.sleep(0.05)
            result = {"report": "Arsenal Win"}
            await cache.resolve("match", result)
            return result

        results = await asyncio.gather(load_once(), load_once())

        self.assertEqual(load_calls, 1)
        self.assertEqual(results[0], {"report": "Arsenal Win"})
        self.assertEqual(results[1], {"report": "Arsenal Win"})

        state, value = await cache.claim("match")
        self.assertEqual(state, "cached")
        self.assertEqual(value, {"report": "Arsenal Win"})

    async def test_cache_stats_include_entries_and_inflight(self):
        cache = TTLCache(ttl=60, maxsize=8)
        await cache.set("match", {"report": "A"})
        state, future = await cache.claim("other")

        self.assertEqual(state, "compute")
        stats = await cache.get_stats()
        self.assertEqual(stats["entries"], 1)
        self.assertEqual(stats["inflight"], 1)
        self.assertEqual(stats["ttl_seconds"], 60)
        self.assertEqual(stats["maxsize"], 8)

        await cache.fail("other", RuntimeError("boom"))
        try:
            await future
        except RuntimeError:
            pass


if __name__ == "__main__":
    unittest.main()
