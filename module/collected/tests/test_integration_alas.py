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

    def test_gui_page_installed(self):
        from module.webui.app import AlasGUI
        self.assertTrue(getattr(AlasGUI.set_aside, '__collected_wrapped__', False))
