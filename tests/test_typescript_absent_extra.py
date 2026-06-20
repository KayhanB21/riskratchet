"""The headline invariant: a default (Python-only) install degrades gracefully.

Unlike `test_typescript_discovery.py`, this module does NOT `importorskip` tree-sitter — it
forces the import to fail so the absent-extra path is exercised in *every* environment,
including CI where the extra is installed. It guards the promise that the experimental TS
path errors with an actionable install hint rather than a raw `ImportError` traceback.
"""

from __future__ import annotations

import sys

import pytest

from riskratchet.typescript import _INSTALL_HINT, _require_tree_sitter


def test_require_tree_sitter_raises_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force `import tree_sitter` to fail even if the extra is installed.
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    monkeypatch.setitem(sys.modules, "tree_sitter_typescript", None)
    with pytest.raises(ImportError) as excinfo:
        _require_tree_sitter()
    assert str(excinfo.value) == _INSTALL_HINT
    assert "riskratchet[typescript]" in str(excinfo.value)
