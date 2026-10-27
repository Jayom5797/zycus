"""Run every test_*.py in tests/ without needing pytest installed.

    python tests/run_tests.py
Exit code is nonzero if any test fails.
"""
import importlib.util
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

total = passed = 0
for f in sorted(HERE.glob("test_*.py")):
    spec = importlib.util.spec_from_file_location(f.stem, f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fns = [v for k, v in sorted(vars(mod).items()) if k.startswith("test_") and callable(v)]
    print(f"\n== {f.name} ==")
    for fn in fns:
        total += 1
        try:
            fn()
            print(f"  PASS {fn.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL {fn.__name__}")
            traceback.print_exc()

print(f"\n{passed}/{total} tests passed")
sys.exit(0 if passed == total else 1)
