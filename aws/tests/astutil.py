"""Compare Python files structurally (comments and formatting are ignored)."""
import ast
from pathlib import Path


def parse(path):
    return ast.parse(Path(path).read_text(encoding="utf-8"))


def _is_main_guard(node):
    return (
        isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    )


def _find_function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise LookupError(f"function {name!r} not found")


def function_dump(path, name):
    return ast.dump(_find_function(parse(path), name))


def shared_top_level(path, exclude=("main",)):
    """Top-level statements except imports, the excluded functions and the __main__ guard."""
    out = []
    for node in parse(path).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) or _is_main_guard(node):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in exclude:
            continue
        out.append(ast.dump(node))
    return out


def import_dumps(path):
    return {ast.dump(node) for node in parse(path).body if isinstance(node, (ast.Import, ast.ImportFrom))}


def _assigned_names(stmt):
    if isinstance(stmt, ast.Assign):
        return {target.id for target in stmt.targets if isinstance(target, ast.Name)}
    return set()


def assigned_value_dump(path, func_name, target):
    for node in ast.walk(_find_function(parse(path), func_name)):
        if isinstance(node, ast.Assign) and target in _assigned_names(node):
            return ast.dump(node.value)
    raise LookupError(f"{target!r} is never assigned in {func_name}()")


def statement_slice(path, func_name, first_target, last_target):
    """Dumps of the statements from `first_target = ...` to `last_target = ...` (inclusive)."""
    for node in ast.walk(_find_function(parse(path), func_name)):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if not isinstance(stmts, list):
                continue
            index = {}
            for i, stmt in enumerate(stmts):
                for name in _assigned_names(stmt):
                    index.setdefault(name, i)
            if first_target in index and last_target in index:
                return [ast.dump(s) for s in stmts[index[first_target]:index[last_target] + 1]]
    raise LookupError(f"no block assigns both {first_target!r} and {last_target!r} in {func_name}()")
