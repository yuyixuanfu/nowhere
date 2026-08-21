"""Find functions defined in .py files but never called project-wide.
Handles: bare calls, module-qualified calls, method calls, dict values, property access,
import aliases, multi-line imports, and @mcp.tool() decorators."""
import os, re, ast

ROOT = "."
SKIP = {".venv", ".git", "__pycache__", ".mypy_cache", ".pytest_cache", "node_modules"}

py_files = []
for r, dirs, fs in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP]
    for f in fs:
        if f.endswith(".py"):
            py_files.append(os.path.join(r, f))

print(f"Scanning {len(py_files)} Python files...", flush=True)

# Phase 1: collect all defs with decorator awareness
defs = []
for fp in py_files:
    with open(fp, encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    for i, line in enumerate(lines, 1):
        m = re.match(r'^(\s*)def\s+([a-zA-Z_]\w*)\s*\(', line)
        if m:
            indent = len(m.group(1))
            name = m.group(2)
            if name.startswith("__") and name.endswith("__"):
                continue
            # Check if preceded by @mcp.tool() or similar registration decorator
            is_entry_point = False
            if i >= 2:
                prev_line = lines[i - 2].strip()
                if re.match(r'@\w+\.tool\(', prev_line) or re.match(r'@mcp\.', prev_line):
                    is_entry_point = True
            defs.append((fp, i, name, indent, is_entry_point))

print(f"Found {len(defs)} non-dunder function defs", flush=True)

# Phase 2: build import graph using AST
import_aliases = {}  # fp -> {local_alias -> actual_imported_name}
for fp in py_files:
    aliases = {}
    try:
        with open(fp, encoding="utf-8", errors="replace") as fh:
            tree = ast.parse(fh.read(), filename=fp)
    except SyntaxError:
        import_aliases[fp] = aliases
        continue

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = alias.name
                local = alias.asname or mod.split(".")[-1]
                aliases[local] = mod.split(".")[-1]
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                for alias in node.names:
                    local = alias.asname or alias.name
                    aliases[local] = alias.name
            else:
                mod = node.module
                for alias in node.names:
                    local = alias.asname or alias.name
                    aliases[local] = alias.name
                top = mod.split(".")[0]
                aliases.setdefault(top, top)

    import_aliases[fp] = aliases

# Phase 3: read all file contents
file_lines = {}
for fp in py_files:
    with open(fp, encoding="utf-8", errors="replace") as fh:
        file_lines[fp] = fh.readlines()

# Phase 4: check callers
def has_callers(def_fp, def_line, name, is_method):
    def_basename = os.path.splitext(os.path.basename(def_fp))[0]

    search_files = [def_fp]
    for fp in py_files:
        if fp == def_fp:
            continue
        aliases = import_aliases.get(fp, {})
        if def_basename in aliases.values():
            search_files.append(fp)

    name_re = re.compile(r'(?<![.\w])' + re.escape(name) + r'\s*\(')
    method_re = re.compile(r'\.' + re.escape(name) + r'(?!\w)')
    value_re = re.compile(r'(?<![.\w])' + re.escape(name) + r'(?!\w|\s*\()')

    for fp in search_files:
        lines = file_lines[fp]
        mod_aliases = set()
        if fp == def_fp:
            mod_aliases.add(def_basename)
        else:
            for alias, mod_name in import_aliases.get(fp, {}).items():
                if mod_name == def_basename:
                    mod_aliases.add(alias)

        for i, line in enumerate(lines, 1):
            if i == def_line and fp == def_fp:
                continue
            stripped = line.lstrip()
            if stripped.startswith("def " + name):
                continue
            if stripped.startswith("#"):
                continue
            if name_re.search(line):
                return True
            for alias in mod_aliases:
                if re.search(re.escape(alias) + r'\.' + re.escape(name) + r'\s*\(', line):
                    return True
            if is_method and method_re.search(line):
                return True
            if fp == def_fp and value_re.search(line):
                return True
    return False

# Phase 5: find dead functions
dead = []
for idx, (fp, line_num, name, indent, is_entry) in enumerate(defs):
    if name == "main" or is_entry or name.startswith("test_"):
        continue
    is_method = indent > 0
    if not has_callers(fp, line_num, name, is_method):
        dead.append((fp, line_num, name, indent))

print(f"\nDead functions: {len(dead)}")
print()
for fp, line_num, name, indent in sorted(dead):
    tag = "[method]" if indent > 0 else "[top-level]"
    print(f"{fp}:{line_num}: {name}  {tag}")
