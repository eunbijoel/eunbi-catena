"""서로 다른 'SQL 행' 형태(JSON fixture) → ``canonical.canonical_telemetry``."""

from __future__ import annotations

from typing import Callable, Dict, List

from . import shape_a, shape_b

AdapterFn = Callable[[], List[Dict[str, object]]]

ADAPTERS: List[tuple[str, AdapterFn]] = [
    ("shape_a_legacy", shape_a.iter_canonical_records),
    ("shape_b_line", shape_b.iter_canonical_records),
]
