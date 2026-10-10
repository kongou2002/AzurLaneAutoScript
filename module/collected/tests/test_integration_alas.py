"""
Integration test against real Alas modules. Needs the Alas runtime (Docker image), run from repo root:
    python -m unittest module.collected.tests.test_integration_alas
Skipped where Alas dependencies are not installed.
"""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

try:
    import numpy as np
    import pywebio  # noqa: F401
    HAS_ALAS = True
except ImportError:
    HAS_ALAS = False


@unittest.skipUnless(HAS_ALAS, 'Alas runtime not available')
class TestAlasIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        # Keep the test database out of ./config
        os.environ['ALAS_COLLECTED_DB'] = os.path.join(cls.tmp, 'collected.db')
        from module.config.server import set_server
        set_server('en')
        from module.collected import patch
        cls.patch = patch

    @classmethod
    def tearDownClass(cls):
        os.environ.pop('ALAS_COLLECTED_DB', None)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_all_hooks_resolve_after_alas_imports_modules(self):
        import importlib
        for _, module_name, _, _, _ in self.patch.HOOKS:
            importlib.import_module(module_name)
        status = {h['name']: h for h in self.patch.store().hooks()}
        bad = {n: (h['status'], h['detail']) for n, h in status.items() if h['status'] != 'ok'}
        self.assertEqual(bad, {})
        self.assertEqual(set(status), {h[0] for h in self.patch.HOOKS})

    def test_wrapped_method_records_and_returns_original_value(self):
        from module.shop.shop_status import ShopStatus
        main = object.__new__(ShopStatus)
        main.device = SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8))
        main.config = SimpleNamespace(config_name='itest', task=None)
        value = main.status_get_gems()
        self.assertIsInstance(value, int)
        hook = {h['name']: h for h in self.patch.store().hooks()}['gem']
        self.assertIsNotNone(hook['last_fired'])

    def test_purchase_hook_records_item(self):
        from module.shop.clerk import ShopClerk
        main = object.__new__(ShopClerk)
        main.config = SimpleNamespace(config_name='itest', task=SimpleNamespace(command='ShopFrequent'))
        main._currency = 12345
        item = SimpleNamespace(name='Cube', amount=2, price=900, cost='Coins')
        self.patch.on_shop_buy(main, None, (item,), {})
        rows = self.patch.store().purchases('itest')
        self.assertEqual((rows[0]['shop'], rows[0]['item'], rows[0]['amount'], rows[0]['balance']),
                         ('ShopFrequent', 'Cube', 2, 12345))

    def test_oil_hook_reads_coin_from_same_screenshot(self):
        import module.campaign.campaign_status as cs
        calls = []

        class FakeOcr:
            def ocr(self, image):
                calls.append(1)
                return 52000

        real = cs.OCR_COIN
        cs.OCR_COIN = FakeOcr()
        try:
            main = SimpleNamespace(config=SimpleNamespace(config_name='coin_test', task=None),
                                   device=SimpleNamespace(image=np.zeros((720, 1280, 3), dtype=np.uint8)))
            self.patch.on_get_oil(main, 9000, (), {})
            self.patch.on_get_oil(main, 8990, (), {})  # within interval: no second OCR
        finally:
            cs.OCR_COIN = real
        latest = {r['key']: r['latest'] for r in self.patch.store().resource_summary('coin_test', 0, 0)}
        self.assertEqual(latest, {'oil': 8990, 'coin': 52000})
        self.assertEqual(len(calls), 1)

    def ship_screen(self, animating):
        # Get-ship screen: light background, the name box is dark once the animation finishes
        image = np.full((720, 1280, 3), 200, dtype=np.uint8)
        if not animating:
            image[575:680, 160:1000] = 40
        return image

    def test_ship_card_animating(self):
        self.assertTrue(self.patch.ship_card_animating(self.ship_screen(animating=True)))
        self.assertFalse(self.patch.ship_card_animating(self.ship_screen(animating=False)))

    def fake_ship_main(self, frames, get_ship=True):
        main = SimpleNamespace(appear=lambda button, offset=0: get_ship, shots=0)

        def screenshot():
            main.shots += 1
            main.device.image = frames[min(main.shots, len(frames) - 1)]
            return main.device.image

        main.device = SimpleNamespace(image=frames[0], screenshot=screenshot)
        return main

    def test_before_get_ship_waits_for_animation(self):
        main = self.fake_ship_main([self.ship_screen(True), self.ship_screen(True), self.ship_screen(False)])
        self.patch.before_get_ship(main, (), {})
        self.assertEqual(main.shots, 2)
        self.assertFalse(self.patch.ship_card_animating(main.device.image))

    def test_before_get_ship_gives_up_after_timeout(self):
        real = self.patch.SHIP_ANIMATION_WAIT
        self.patch.SHIP_ANIMATION_WAIT = 0.2
        try:
            main = self.fake_ship_main([self.ship_screen(True)])
            self.patch.before_get_ship(main, (), {})
        finally:
            self.patch.SHIP_ANIMATION_WAIT = real
        self.assertGreater(main.shots, 0)

    def test_before_get_ship_ignores_other_screens(self):
        main = self.fake_ship_main([self.ship_screen(True)], get_ship=False)
        self.patch.before_get_ship(main, (), {})
        self.assertEqual(main.shots, 0)

    def test_ship_hook_waits_before_alas_clicks(self):
        from module.combat.combat import Combat
        fake = self.fake_ship_main([self.ship_screen(True), self.ship_screen(False)])
        main = object.__new__(Combat)
        main.__dict__.update(vars(fake))
        main.device = fake.device
        main.config = SimpleNamespace(config_name='ship_test', task=None, Campaign_Name='d3')
        clicked_on = []
        main.appear_then_click = lambda button, offset=0, interval=0: clicked_on.append(
            self.patch.ship_card_animating(main.device.image)) or True
        main.appear = lambda button, offset=0: button.name == 'GET_SHIP'
        self.patch.SHIP_IMAGE_FOLDER = os.path.join(self.tmp, 'ships')
        self.assertTrue(main.handle_get_ship())
        self.assertEqual(clicked_on, [False])
        self.assertEqual(len(self.patch.store().ships('ship_test')), 1)

    def test_gui_page_installed(self):
        from module.webui.app import AlasGUI
        self.assertTrue(getattr(AlasGUI.set_aside, '__collected_wrapped__', False))
