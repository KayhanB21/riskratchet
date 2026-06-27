"""The shared backend protocol (`models.DiscoveredFunctionLike`).

This is the structural unification of the two discovered-function shapes: the Python
`analysis.DiscoveredFunction` and the TypeScript `typescript.TsFunction`. The assignments to
a `DiscoveredFunctionLike`-typed name make **mypy** enforce conformance statically (this
module is in `mypy src tests`), and the `isinstance` checks enforce it at runtime — together
a drift guard, so neither backend can quietly drop or rename a common attribute.

Importing `TsFunction` does not pull in tree-sitter (it is a plain dataclass; the parser is
lazy-imported), so this runs in a default Python-only env.
"""

from __future__ import annotations

import ast

from riskratchet.analysis import DiscoveredFunction
from riskratchet.models import DiscoveredFunctionLike, FunctionId, FunctionSpan
from riskratchet.typescript import TsFunction


def _python_function() -> DiscoveredFunction:
    node = ast.parse("def f():\n    return 1\n").body[0]
    assert isinstance(node, ast.FunctionDef)
    return DiscoveredFunction(
        id=FunctionId(path="m.py", qualname="f"),
        span=FunctionSpan(start_line=1, end_line=2),
        is_public=True,
        is_async=False,
        fingerprint="fp",
        signature="sig",
        node=node,
    )


def _typescript_function() -> TsFunction:
    return TsFunction(
        id=FunctionId(path="m.ts", qualname="f"),
        span=FunctionSpan(start_line=1, end_line=2),
        is_public=True,
        is_async=False,
        kind="function",
    )


def test_python_discovered_function_conforms() -> None:
    fn: DiscoveredFunctionLike = _python_function()  # static conformance (mypy)
    assert isinstance(fn, DiscoveredFunctionLike)  # runtime conformance
    assert fn.id.qualname == "f"
    assert fn.span.start_line == 1
    assert fn.is_public is True
    assert fn.is_async is False


def test_typescript_function_conforms() -> None:
    fn: DiscoveredFunctionLike = _typescript_function()
    assert isinstance(fn, DiscoveredFunctionLike)
    assert fn.id.path.endswith(".ts")
    assert fn.is_public is True
    assert fn.is_async is False


def test_protocol_is_the_common_surface_only() -> None:
    # Identity (fingerprint/signature) is intentionally NOT part of the shared protocol —
    # it is the Python-only half TypeScript can't supply until it has token-stable
    # fingerprints. The protocol unifies the *shape*, not the identity.
    py = _python_function()
    ts = _typescript_function()
    assert hasattr(py, "fingerprint") and hasattr(py, "signature")
    assert not hasattr(ts, "fingerprint") and not hasattr(ts, "signature")


def test_backend_agnostic_code_can_consume_the_protocol() -> None:
    # A genuine use: one helper ranks/labels functions from either backend through the
    # protocol, with no concrete-type knowledge.
    def label(fn: DiscoveredFunctionLike) -> str:
        visibility = "public" if fn.is_public else "internal"
        return f"{fn.id.as_target()} [{visibility}] ({fn.span.start_line}-{fn.span.end_line})"

    assert label(_python_function()) == "m.py::f [public] (1-2)"
    assert label(_typescript_function()) == "m.ts::f [public] (1-2)"
