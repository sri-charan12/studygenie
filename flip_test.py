"""
Flip tests to their opposite for the live review demo, then restore them.

Run from the project folder (next to studygenie.py and the tests folder).

    python flip_test.py test_estimate_minutes_is_difficulty_times_30
        Flip one test. It wraps the test's first assert in  not (...)  so a passing test now fails.

    python flip_test.py tests/test_core.py
        Flip every test in one file.

    python flip_test.py --all
        Flip every test in every file.

    python flip_test.py --restore
        Put every file back exactly as it was. Run this when the demo is over.

Original files are saved in the .flip_backup folder before anything changes.
"""
import ast
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = ROOT / "tests"
BACKUP = ROOT / ".flip_backup"


def backup(path):
    BACKUP.mkdir(exist_ok=True)
    dest = BACKUP / path.name
    if not dest.exists():
        shutil.copy2(path, dest)


def restore():
    if not BACKUP.exists() or not any(BACKUP.iterdir()):
        print("Nothing to restore.")
        return
    for saved in BACKUP.iterdir():
        shutil.copy2(saved, TESTS / saved.name)
        print(f"Restored tests/{saved.name}")
    shutil.rmtree(BACKUP)
    print("All tests are back to normal.")


def first_assert(func):
    asserts = [n for n in ast.walk(func) if isinstance(n, ast.Assert)]
    return min(asserts, key=lambda n: (n.lineno, n.col_offset)) if asserts else None


def flip_functions(path, wanted):
    """Flip the first assert in each test function whose name passes wanted(name)."""
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    tree = ast.parse(text)

    starts, pos = [], 0
    for line in raw.splitlines(keepends=True):
        starts.append(pos)
        pos += len(line)

    edits, flipped = [], []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test") and wanted(node.name):
            target = first_assert(node)
            if target is None:
                continue
            t = target.test
            begin = starts[t.lineno - 1] + t.col_offset
            end = starts[t.end_lineno - 1] + t.end_col_offset
            if raw[begin:end].startswith(b"not  ("):
                continue  # already flipped
            edits.append((begin, end))
            flipped.append((node.name, t.lineno))

    if not edits:
        return []
    backup(path)
    for begin, end in sorted(edits, reverse=True):
        raw = raw[:begin] + b"not  (" + raw[begin:end] + b")" + raw[end:]
    path.write_bytes(raw)
    return flipped


def find_test_files():
    return sorted(p for p in TESTS.glob("test_*.py"))


def main():
    args = sys.argv[1:]
    if not TESTS.is_dir():
        print("Could not find a 'tests' folder. Put flip_test.py in the project folder and run it again.")
        return
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    if args[0] == "--restore":
        restore()
        return

    target = args[0].replace("\\", "/")
    files = find_test_files()
    total = 0

    if target == "--all":
        for f in files:
            for name, line in flip_functions(f, lambda n: True):
                print(f"Flipped {f.name}::{name} (line {line})")
                total += 1
    elif target.endswith(".py"):
        match = [f for f in files if f.name == Path(target).name]
        if not match:
            print(f"No test file called {target}")
            return
        for name, line in flip_functions(match[0], lambda n: True):
            print(f"Flipped {match[0].name}::{name} (line {line})")
            total += 1
    else:
        name = target.split("::")[-1].split("[")[0]
        for f in files:
            for n, line in flip_functions(f, lambda x: x == name):
                print(f"Flipped {f.name}::{n} (line {line})")
                total += 1

    if total == 0:
        print("No test flipped. Check the name, or the test has no assert to flip.")
    else:
        print(f"\n{total} test(s) flipped. Run the tests now, then use:  python flip_test.py --restore")


if __name__ == "__main__":
    main()
