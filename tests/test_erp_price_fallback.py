"""`_try_load_erp_prices` — three branches of the resilience policy.

Branch matrix:
  1. Fresh cache present                 → return cached (no API call)
  2. Cache miss, API succeeds            → API result cached for future calls
  3. Cache miss, API fails, stale cache  → return stale cache (forced TTL read)
  4. Cache miss, API fails, no cache     → return {} (loud but non-fatal)

This is critical: ERPNext is in customer's network and frequently flaky.
The "stale cache fallback" is the entire reason production keeps generating
correct reports during outages — silently breaking it would be invisible.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import _setup  # noqa: F401

from bq_reports import profit_margin_report as pmr
from utils import cache as cache_mod


class ErpPriceFallbackTests(unittest.TestCase):
    def setUp(self):
        # Redirect cache module's default dir to a tempdir so tests don't pollute
        # the real `.cache/bq_reports/` (and don't see prior runs' cache).
        self.tmp = Path(tempfile.mkdtemp())
        self._orig_cache_dir = cache_mod.DEFAULT_CACHE_DIR
        cache_mod.DEFAULT_CACHE_DIR = self.tmp

    def tearDown(self):
        cache_mod.DEFAULT_CACHE_DIR = self._orig_cache_dir
        shutil.rmtree(self.tmp, ignore_errors=True)

    # Cache JSON-round-trips: tuples become lists. _resolve_base_unit_price
    # accepts either (it just does `price, _uom = …`), so the production code
    # is happy with the list shape. Test expectations match that reality.
    LIST_VAL = [1.5, "kg"]
    LIST_VAL_2 = [9.9, "kg"]

    # ------------------------------------------------------------------

    def test_fresh_cache_short_circuits_api(self):
        # cache key 需与 _try_load_erp_prices 默认使用的 COST_PROFIT_PRICE_LIST 一致
        key = cache_mod.cache_key("erpnext_prices", {"price_list": "Buying - Internal"})
        cache_mod.set_cache(key, {"M1": (1.5, "kg")})

        with mock.patch("bq_reports.utils.erpnext_api.load_erpnext_prices") as mock_api:
            result = pmr._try_load_erp_prices(cache_ttl=3600)
            self.assertEqual(result, {"M1": self.LIST_VAL})
            mock_api.assert_not_called()

    def test_api_success_writes_cache_then_short_circuits(self):
        fake_prices = {"M2": (2.0, "kg")}
        with mock.patch("bq_reports.utils.erpnext_api.load_erpnext_prices",
                        return_value=fake_prices) as mock_api:
            first = pmr._try_load_erp_prices(cache_ttl=3600)
            self.assertEqual(first, fake_prices)        # fresh fetch keeps tuples
            mock_api.assert_called_once()

        # Cache now hot → API must not be called again.
        with mock.patch("bq_reports.utils.erpnext_api.load_erpnext_prices") as mock_api_2:
            second = pmr._try_load_erp_prices(cache_ttl=3600)
            self.assertEqual(second, {"M2": [2.0, "kg"]})  # cache round-trips to list
            mock_api_2.assert_not_called()

    def test_api_failure_falls_back_to_stale_cache(self):
        """Headline behaviour of the recent change — keep customer reports
        generating during ERPNext outages."""
        # cache key 需与 _try_load_erp_prices 默认使用的 COST_PROFIT_PRICE_LIST 一致
        key = cache_mod.cache_key("erpnext_prices", {"price_list": "Buying - Internal"})
        cache_mod.set_cache(key, {"M3": (9.9, "kg")})

        # cache_ttl=0 forces the "fresh cache" lookup at the top to miss,
        # so the implementation has to traverse: API → fails → stale-cache
        # forced read (TTL=99999999) → return.
        with mock.patch("bq_reports.utils.erpnext_api.load_erpnext_prices",
                        side_effect=RuntimeError("ERPNext down")):
            result = pmr._try_load_erp_prices(cache_ttl=0)
            self.assertEqual(result, {"M3": self.LIST_VAL_2},
                             "stale-cache fallback regressed")

    def test_api_failure_no_cache_returns_empty(self):
        """Loud but non-fatal: cost columns degrade to 0, report still produced."""
        with mock.patch("bq_reports.utils.erpnext_api.load_erpnext_prices",
                        side_effect=RuntimeError("ERPNext down")):
            result = pmr._try_load_erp_prices(cache_ttl=0)
            self.assertEqual(result, {})

    def test_price_list_name_affects_cache_key(self):
        """Different price lists must have isolated caches — anchors the key."""
        k1 = cache_mod.cache_key("erpnext_prices", {"price_list": "Standard Buying"})
        k2 = cache_mod.cache_key("erpnext_prices", {"price_list": "Custom List"})
        self.assertNotEqual(k1, k2)


if __name__ == "__main__":
    unittest.main()
