"""
Run callbacks after Alas imports a module, and wrap methods without changing behaviour.

Alas imports task modules lazily, and some of them pick OCR settings by server at import
time, so we must never import them ourselves. Instead, patches are applied right after
Alas imports the module.
"""
import functools
import importlib.abc
import importlib.util
import sys
import threading

_callbacks = {}
_lock = threading.RLock()
_installed = False
_WRAPPED = '__collected_wrapped__'


def _run_callbacks(name, module):
    with _lock:
        callbacks = _callbacks.pop(name, [])
    for callback in callbacks:
        try:
            callback(module)
        except Exception as e:
            _report(f'Collected: callback for {name} failed: {e!r}')


def _report(message):
    try:
        from module.logger import logger
        logger.warning(message)
    except Exception:
        print(message)


class _LoaderProxy(importlib.abc.Loader):
    def __init__(self, loader, name):
        self._loader = loader
        self._name = name

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module):
        self._loader.exec_module(module)
        _run_callbacks(self._name, module)

    def __getattr__(self, item):
        return getattr(self._loader, item)


class _PostImportFinder(importlib.abc.MetaPathFinder):
    def __init__(self):
        self._busy = set()

    def find_spec(self, fullname, path, target=None):
        if fullname not in _callbacks or fullname in self._busy:
            return None
        self._busy.add(fullname)
        try:
            spec = importlib.util.find_spec(fullname)
        finally:
            self._busy.discard(fullname)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _LoaderProxy(spec.loader, fullname)
        return spec


def install():
    global _installed
    with _lock:
        if not _installed:
            sys.meta_path.insert(0, _PostImportFinder())
            _installed = True


def when_imported(name, callback):
    """
    Call `callback(module)` once module `name` is imported, or now if it already is.
    """
    install()
    module = sys.modules.get(name)
    if module is not None:
        try:
            callback(module)
        except Exception as e:
            _report(f'Collected: callback for {name} failed: {e!r}')
        return
    with _lock:
        _callbacks.setdefault(name, []).append(callback)


def wrap_method(cls, name, after=None, on_error=None, before=None):
    """
    Wrap `cls.name` so `before(self, args, kwargs)` runs before each call and
    `after(self, result, args, kwargs)` runs after each successful call.
    The original return value is passed through and exceptions in hooks never escape.

    Args:
        cls: Class that defines the method itself (not inherited).
        name (str):
        after (callable):
        on_error (callable): Receives the exception raised by a hook.
        before (callable):

    Returns:
        bool: If wrapped.
    """
    raw = cls.__dict__.get(name)
    if raw is None:
        return False
    is_static = isinstance(raw, staticmethod)
    func = raw.__func__ if is_static else raw
    if not callable(func) or getattr(func, _WRAPPED, False):
        return False

    def call_hook(hook, *hook_args):
        if hook is None:
            return
        try:
            hook(*hook_args)
        except Exception as e:
            if on_error is not None:
                on_error(e)
            else:
                _report(f'Collected: hook {cls.__name__}.{name} failed: {e!r}')

    if is_static:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            call_hook(before, None, args, kwargs)
            result = func(*args, **kwargs)
            call_hook(after, None, result, args, kwargs)
            return result

        setattr(wrapper, _WRAPPED, True)
        setattr(cls, name, staticmethod(wrapper))
    else:
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            call_hook(before, self, args, kwargs)
            result = func(self, *args, **kwargs)
            call_hook(after, self, result, args, kwargs)
            return result

        setattr(wrapper, _WRAPPED, True)
        setattr(cls, name, wrapper)
    return True
