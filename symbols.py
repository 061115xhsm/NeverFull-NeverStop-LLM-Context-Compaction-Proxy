"""Multi-language symbol extraction for state freshness (V12 Phase 3).

Uses tree-sitter when the language wheel is installed (Python, JS/TS, Go, Rust);
falls back to the stdlib `ast` for Python. Returns symbol records with a content
hash so only changed functions/classes are marked stale.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path

try:
    from tree_sitter import Language, Parser
    import tree_sitter_python
    import tree_sitter_javascript
    import tree_sitter_typescript
    import tree_sitter_go
    import tree_sitter_rust
    _HAS_TS = True
except Exception:  # pragma: no cover
    Language = Parser = None
    _HAS_TS = False

_TS_TYPES = {
    "python": {"function_definition", "class_definition"},
    "javascript": {"function_declaration", "class_declaration", "method_definition",
                   "generator_function_declaration"},
    "typescript": {"function_declaration", "class_declaration", "method_definition",
                   "interface_declaration", "enum_declaration", "type_alias_declaration"},
    "go": {"function_declaration", "method_declaration", "type_declaration"},
    "rust": {"function_item", "struct_item", "enum_item", "trait_item", "impl_item"},
}
_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust",
}


def _language_for(path: str):
    ext = Path(path).suffix.lower()
    lang = _EXT.get(ext)
    if not lang or not _HAS_TS:
        return None
    try:
        if lang == "python":
            return Language(tree_sitter_python.language())
        if lang == "javascript":
            return Language(tree_sitter_javascript.language())
        if lang == "typescript":
            return Language(tree_sitter_typescript.language_typescript())
        if lang == "go":
            return Language(tree_sitter_go.language())
        if lang == "rust":
            return Language(tree_sitter_rust.language())
    except Exception:
        return None
    return None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _node_name(node, source: bytes) -> str:
    name_node = node.child_by_field_name("name")
    if name_node is not None:
        return source[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
    # go type_declaration / rust impl: first identifier-ish child
    for child in node.children:
        if child.type in ("type_spec", "type_identifier", "identifier", "field_identifier"):
            return source[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
    return ""


def extract_symbols(path: str, content: str) -> list:
    """Return [{name, kind, start, end, hash}] for functions/classes in the file."""
    lang_name = _EXT.get(Path(path).suffix.lower())
    if not lang_name:
        return []
    if _HAS_TS:
        language = _language_for(path)
        if language is not None:
            try:
                parser = Parser(language)
                source = content.encode("utf-8", errors="replace")
                tree = parser.parse(source)
                out = []

                def walk(node, scope=""):
                    if node.type in _TS_TYPES.get(lang_name, set()):
                        name = _node_name(node, source)
                        if name:
                            qualified = f"{scope}.{name}" if scope else name
                            out.append({
                                "name": qualified, "kind": node.type,
                                "start": node.start_point[0] + 1,
                                "end": node.end_point[0] + 1,
                                "hash": _hash(source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")),
                            })
                            walk_scope = qualified
                        else:
                            walk_scope = scope
                    else:
                        walk_scope = scope
                    for child in node.children:
                        walk(child, walk_scope)

                walk(tree.root_node)
                if out:
                    return out
            except Exception:
                pass
    if lang_name == "python":
        return _python_ast_symbols(content)
    return []


def _python_ast_symbols(content: str, scope: str = "") -> list:
    try:
        tree = ast.parse(content)
    except Exception:
        return []
    out = []
    for node in tree.body if not scope else []:
        pass
    def walk(nodes, prefix=""):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                name = f"{prefix}.{node.name}" if prefix else node.name
                out.append({
                    "name": name, "kind": kind,
                    "start": getattr(node, "lineno", 0),
                    "end": getattr(node, "end_lineno", 0) or 0,
                    "hash": _hash(ast.dump(node, include_attributes=False)),
                })
                if isinstance(node, ast.ClassDef):
                    walk(node.body, name)
    walk(tree.body)
    return out
