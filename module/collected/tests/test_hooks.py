import importlib
import os
import sys
import tempfile
import textwrap
import unittest

from module.collected import hooks


class TestWhenImported(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        sys.path.insert(0, self.tmp.name)
        hooks.install()

    def tearDown(self):
        sys.path.remove(self.tmp.name)
        for name in list(sys.modules):
            if name.startswith('collected_fake'):
                del sys.modules[name]
        self.tmp.cleanup()

    def make_module(self, name, body):
        path = os.path.join(self.tmp.name, name + '.py')
        with open(path, 'w') as f:
            f.write(textwrap.dedent(body))
        importlib.invalidate_caches()

    def test_callback_runs_after_module_is_imported(self):
        self.make_module('collected_fake_a', 'VALUE = 1\n')
        seen = []
        hooks.when_imported('collected_fake_a', lambda m: seen.append(m.VALUE))
        self.assertEqual(seen, [])
        importlib.import_module('collected_fake_a')
        self.assertEqual(seen, [1])

    def test_callback_runs_immediately_if_already_imported(self):
        self.make_module('collected_fake_b', 'VALUE = 2\n')
        importlib.import_module('collected_fake_b')
        seen = []
        hooks.when_imported('collected_fake_b', lambda m: seen.append(m.VALUE))
        self.assertEqual(seen, [2])

    def test_failing_callback_does_not_break_import(self):
        self.make_module('collected_fake_c', 'VALUE = 3\n')
        hooks.when_imported('collected_fake_c', lambda m: 1 / 0)
        module = importlib.import_module('collected_fake_c')
        self.assertEqual(module.VALUE, 3)


class Target:
    def get(self, x):
        return x * 2

    @staticmethod
    def static(x):
        return x + 1


class Child(Target):
    pass


class TestWrapMethod(unittest.TestCase):
    def setUp(self):
        self.saved = dict(Target.__dict__)

    def tearDown(self):
        Target.get = self.saved['get']
        Target.static = self.saved['static']

    def test_after_receives_result_and_result_is_unchanged(self):
        seen = []
        ok = hooks.wrap_method(Target, 'get', lambda self, result, args, kwargs: seen.append((result, args)))
        self.assertTrue(ok)
        self.assertEqual(Child().get(5), 10)
        self.assertEqual(seen, [(10, (5,))])

    def test_error_in_after_is_swallowed(self):
        errors = []
        hooks.wrap_method(Target, 'get', lambda *a: 1 / 0, on_error=errors.append)
        self.assertEqual(Target().get(3), 6)
        self.assertEqual(len(errors), 1)

    def test_staticmethod_stays_static(self):
        seen = []
        hooks.wrap_method(Target, 'static', lambda self, result, args, kwargs: seen.append((self, result)))
        self.assertEqual(Target.static(1), 2)
        self.assertEqual(Target().static(1), 2)
        self.assertEqual(seen, [(None, 2), (None, 2)])

    def test_missing_method_returns_false(self):
        self.assertFalse(hooks.wrap_method(Target, 'nope', lambda *a: None))

    def test_inherited_method_is_not_wrapped_on_subclass(self):
        # Wrap where it is defined, so every subclass sees one wrapper
        self.assertFalse(hooks.wrap_method(Child, 'get', lambda *a: None))

    def test_double_wrap_is_ignored(self):
        seen = []
        hooks.wrap_method(Target, 'get', lambda self, result, args, kwargs: seen.append(1))
        hooks.wrap_method(Target, 'get', lambda self, result, args, kwargs: seen.append(1))
        Target().get(1)
        self.assertEqual(seen, [1])

    def test_original_exception_propagates_without_after(self):
        seen = []

        class Boom:
            def run(self):
                raise ValueError('x')

        hooks.wrap_method(Boom, 'run', lambda *a: seen.append(1))
        with self.assertRaises(ValueError):
            Boom().run()
        self.assertEqual(seen, [])


if __name__ == '__main__':
    unittest.main()
