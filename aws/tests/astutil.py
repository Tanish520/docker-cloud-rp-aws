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
    """Top-level statements except imports, the __main__ guard, and the excluded functions and
    statements that assign an excluded name (anywhere inside them, e.g. in a try block)."""
    out = []
    for node in parse(path).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) or _is_main_guard(node):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in exclude:
            continue
        if any(_assigned_names(stmt) & set(exclude) for stmt in ast.walk(node)):
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


_SIMPLE = (ast.Expr, ast.Assign, ast.AugAssign)
_STORAGE_OWNERS = {"raw_container", "trans_container", "store"}
_STORAGE_VERBS = {
    "download_blob": "DOWNLOAD", "download_raw": "DOWNLOAD",
    "upload_blob": "UPLOAD", "upload_translated": "UPLOAD",
    "delete_blob": "DELETE", "delete_raw": "DELETE",
    "list_blobs": "LIST", "list_raw": "LIST",
}


def _flatten(stmts):
    """Simple statements in source order, descending into if/for/while/with/try blocks."""
    for stmt in stmts:
        if isinstance(stmt, _SIMPLE):
            yield stmt
            continue
        for field in ("body", "orelse", "finalbody"):
            yield from _flatten(getattr(stmt, field, []) or [])
        for handler in getattr(stmt, "handlers", []) or []:
            yield from _flatten(handler.body)


def _storage_token(stmt):
    for node in ast.walk(stmt):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id in _STORAGE_OWNERS
                and node.func.attr in _STORAGE_VERBS):
            return _STORAGE_VERBS[node.func.attr]
    return None


def _is_timing(stmt):
    for node in ast.walk(stmt):
        if isinstance(node, ast.Name) and node.id == "profiler":
            return True
        if isinstance(node, ast.Attribute) and node.attr in ("sleep", "perf_counter"):
            return True
    return False


def main_timeline(path, func_name="main"):
    """Ordered timers, sleeps and storage calls of a function; storage calls become DOWNLOAD/UPLOAD/... tokens."""
    timeline = []
    for stmt in _flatten(_find_function(parse(path), func_name).body):
        token = _storage_token(stmt)
        if token:
            timeline.append(token)
        elif _is_timing(stmt):
            timeline.append(ast.dump(stmt))
    return timeline
