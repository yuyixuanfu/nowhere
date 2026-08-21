"""Find all functions defined in .py files but never called project-wide.
Optimized: single pass to collect all call-site identifiers."""
import os, re

ROOT = "."
py_files = []
for r, _, fs in os.walk(ROOT):
    for f in fs:
        if f.endswith(".py"):
            py_files.append(os.path.join(r, f))

# Phase 1: collect all defs  (file, line, name, indent)
defs = []
for fp in py_files:
    with open(fp, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh, 1):
            m = re.match(r'^(\s*)def\s+([a-zA-Z_]\w*)\s*\(', line)
            if m:
                indent = len(m.group(1))
                name = m.group(2)
                if name.startswith("__") and name.endswith("__"):
                    continue
                defs.append((fp, i, name, indent))

# Phase 2: collect ALL call-site identifiers across the project
# A call-site is: identifier(  where identifier is not preceded by . or word char
# and the line is NOT a def line
call_re = re.compile(r'(?<![.\w])([a-zA-Z_]\w*)\s*\(')
called_names = set()
for fp in py_files:
    with open(fp, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.lstrip()
            is_def = stripped.startswith("def ")
            for m in call_re.finditer(line):
                name = m.group(1)
                if is_def and stripped.startswith("def " + name):
                    continue  # skip the def line itself
                called_names.add(name)

# Phase 3: find dead functions
dead = []
for fp, line_num, name, indent in defs:
    if name == "main":
        continue
    if name not in called_names:
        dead.append((fp, line_num, name, indent))

print(f"Total non-dunder functions: {len(defs)}")
print(f"Unique called names: {len(called_names)}")
print(f"Dead functions: {len(dead)}")
print()
for fp, line_num, name, indent in sorted(dead):
    tag = "[method]" if indent > 0 else "[top-level]"
    print(f"{fp}:{line_num}: {name}  {tag}")
