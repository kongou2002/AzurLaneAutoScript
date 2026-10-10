import os
import tempfile
import unittest

from module.collected.store import CollectedStore

T0 = 1_760_000_000.0  # fixed epoch, avoids depending on wall clock
INST = 'alas'


class StoreTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = CollectedStore(os.path.join(self.tmp.name, 'collected.db'))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def rows(self, sql, *args):
        return [tuple(r) for r in self.store.conn.execute(sql, args).fetchall()]


class TestRecordResource(StoreTestBase):
    def test_first_value_is_recorded(self):
        self.assertTrue(self.store.record_resource(INST, 'oil', 9000, now=T0))
        self.assertEqual(self.rows('SELECT key, value FROM resource'), [('oil', 9000)])

    def test_same_value_within_interval_is_skipped(self):
        self.store.record_resource(INST, 'oil', 9000, now=T0)
        self.assertFalse(self.store.record_resource(INST, 'oil', 9000, now=T0 + 60))
        self.assertEqual(len(self.rows('SELECT * FROM resource')), 1)

    def test_same_value_after_interval_is_recorded(self):
        self.store.record_resource(INST, 'oil', 9000, now=T0)
        self.assertTrue(self.store.record_resource(INST, 'oil', 9000, now=T0 + 601))

    def test_changed_value_is_recorded_immediately(self):
        self.store.record_resource(INST, 'oil', 9000, now=T0)
        self.assertTrue(self.store.record_resource(INST, 'oil', 8990, now=T0 + 5))

    def test_non_positive_value_is_rejected(self):
        self.assertFalse(self.store.record_resource(INST, 'oil', 0, now=T0))
        self.assertFalse(self.store.record_resource(INST, 'oil', -3, now=T0))
        self.assertFalse(self.store.record_resource(INST, 'oil', None, now=T0))

    def test_implausible_jump_is_rejected(self):
        self.store.record_resource(INST, 'oil', 9000, now=T0)
        # OCR dropped digits: 9000 -> 9
        self.assertFalse(self.store.record_resource(INST, 'oil', 9, now=T0 + 5))
        # OCR merged digits: 9000 -> 90000
        self.assertFalse(self.store.record_resource(INST, 'oil', 90000, now=T0 + 5))
        self.assertTrue(self.store.record_resource(INST, 'oil', 8000, now=T0 + 5))

    def test_small_values_are_not_jump_checked(self):
        # Currencies like cubes can legitimately go 2 -> 30
        self.store.record_resource(INST, 'cube', 2, now=T0)
        self.assertTrue(self.store.record_resource(INST, 'cube', 30, now=T0 + 5))

    def test_keys_and_instances_are_independent(self):
        self.store.record_resource(INST, 'oil', 9000, now=T0)
        self.assertTrue(self.store.record_resource(INST, 'coin', 9000, now=T0 + 1))
        self.assertTrue(self.store.record_resource('alas2', 'oil', 9000, now=T0 + 1))


class TestRecordShip(StoreTestBase):
    def test_ship_recorded(self):
        self.assertTrue(self.store.record_ship(INST, True, '12-4', 'a.png', now=T0))
        self.assertEqual(self.rows('SELECT is_new, campaign, image FROM ship'), [(1, '12-4', 'a.png')])

    def test_duplicate_click_on_same_screen_is_ignored(self):
        self.store.record_ship(INST, False, '12-4', 'a.png', now=T0)
        self.assertFalse(self.store.record_ship(INST, False, '12-4', 'b.png', now=T0 + 3))
        self.assertTrue(self.store.record_ship(INST, False, '12-4', 'c.png', now=T0 + 30))


class TestRecordPurchase(StoreTestBase):
    def test_purchase_recorded(self):
        self.store.record_purchase(INST, 'ShopFrequent', 'Cube', 1, 900, 'Coins', 50000, now=T0)
        self.assertEqual(
            self.rows('SELECT shop, item, amount, price, currency, balance FROM purchase'),
            [('ShopFrequent', 'Cube', 1, 900, 'Coins', 50000)])


class TestHooks(StoreTestBase):
    def test_set_and_touch_hook(self):
        self.store.set_hook('oil', 'ok', now=T0)
        self.store.set_hook('gems', 'missing', 'ShopStatus.status_get_gems not found', now=T0)
        self.store.touch_hook('oil', now=T0 + 10)
        hooks = {h['name']: h for h in self.store.hooks()}
        self.assertEqual(hooks['oil']['status'], 'ok')
        self.assertEqual(hooks['oil']['last_fired'], T0 + 10)
        self.assertEqual(hooks['gems']['status'], 'missing')
        self.assertIsNone(hooks['gems']['last_fired'])


class TestQueries(StoreTestBase):
    def test_resource_summary_deltas(self):
        day = 86400
        s = self.store
        s.record_resource(INST, 'oil', 5000, now=T0 - 10 * day)  # first ever
        s.record_resource(INST, 'oil', 7000, now=T0 - 3 * day)
        s.record_resource(INST, 'oil', 8000, now=T0 - 60)  # last before today
        s.record_resource(INST, 'oil', 8500, now=T0 + 100)
        s.record_resource(INST, 'oil', 9000, now=T0 + 200)  # latest
        summary = {r['key']: r for r in s.resource_summary(INST, day_start=T0, week_start=T0 - 7 * day)}
        oil = summary['oil']
        self.assertEqual(oil['latest'], 9000)
        self.assertEqual(oil['delta_today'], 1000)  # 9000 - 8000
        self.assertEqual(oil['delta_week'], 4000)  # 9000 - 5000 (last before week start)
        self.assertEqual(oil['delta_all'], 4000)  # 9000 - first
        self.assertEqual(oil['first_ts'], T0 - 10 * day)

    def test_delta_today_without_earlier_value_uses_first_of_today(self):
        s = self.store
        s.record_resource(INST, 'event_pt@ev', 100, now=T0 + 10)
        s.record_resource(INST, 'event_pt@ev', 400, now=T0 + 20)
        row = s.resource_summary(INST, day_start=T0, week_start=T0 - 7 * 86400)[0]
        self.assertEqual(row['delta_today'], 300)
        self.assertEqual(row['delta_week'], 300)

    def test_resource_daily_last_value_per_day(self):
        s = self.store
        s.record_resource(INST, 'oil', 1000, now=T0)
        s.record_resource(INST, 'oil', 1100, now=T0 + 3600)
        s.record_resource(INST, 'oil', 1200, now=T0 + 86400)
        daily = s.resource_daily(INST, 'oil', tz_offset_hours=0)
        self.assertEqual([v for _, v in daily], [1100, 1200])

    def test_purchase_totals(self):
        s = self.store
        s.record_purchase(INST, 'ShopFrequent', 'Cube', 1, 900, 'Coins', 50000, now=T0)
        s.record_purchase(INST, 'ShopFrequent', 'Cube', 2, 900, 'Coins', 49100, now=T0 + 5)
        s.record_purchase(INST, 'EventShop', 'URpt', 10, 100, 'pt', 3000, now=T0 + 9)
        totals = {(r['shop'], r['item']): r for r in s.purchase_totals(INST)}
        self.assertEqual(totals[('ShopFrequent', 'Cube')]['amount'], 3)
        self.assertEqual(totals[('ShopFrequent', 'Cube')]['times'], 2)
        self.assertEqual(totals[('EventShop', 'URpt')]['amount'], 10)


if __name__ == '__main__':
    unittest.main()
