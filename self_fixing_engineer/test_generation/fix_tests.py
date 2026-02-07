# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

import os
import re

ROOT = os.getcwd()
PKG = "test_generation"
BASE = os.path.join(ROOT, PKG)

# from ..foo.bar import Baz  -> from test_generation.foo.bar import Baz
REL_FROM = re.compile(r"^(from\s+)(\.+)([\w\.]*)(\s+import\s+)", re.M)


def fix_file(path):
    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    def repl(m):
        prefix, dots, tail, suffix = m.groups()
        tail = tail.strip(".")
        new_mod = PKG if not tail else f"{PKG}.{tail}"
        return f"{prefix}{new_mod}{suffix}"

    new_src = REL_FROM.sub(repl, src)
    if new_src != src:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_src)
        print(f"fixed: {path}")


def main():
    for root, _, files in os.walk(BASE):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            # only touch test files
            if "tests" in root or fn.startswith("test_"):
                fix_file(os.path.join(root, fn))
    print("done.")


if __name__ == "__main__":
    main()
