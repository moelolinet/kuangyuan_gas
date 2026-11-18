"""Tiny runner that executes every test_* function without pytest."""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    # test_e2e.py needs Home Assistant and pytest; run it with pytest.
    files = sorted(
        path
        for path in HERE.glob("test_*.py")
        if path.name in {"test_parsers.py", "test_settings.py"}
    )
    total = failed = 0
    for path in files:
        print(f"\n=== {path.name} ===")
        try:
            module = load(path)
        except Exception:
            print("IMPORT FAILED")
            traceback.print_exc()
            failed += 1
            continue
        for name in sorted(dir(module)):
            if not name.startswith("test_"):
                continue
            fn = getattr(module, name)
            if not callable(fn):
                continue
            total += 1
            try:
                fn()
                print("PASS", name)
            except Exception as exc:
                failed += 1
                print("FAIL", name, "->", repr(exc))
                traceback.print_exc()
    print(f"\n--- {total - failed}/{total} passed, {failed} failed ---")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

