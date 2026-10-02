"""Single source of the application version.

pyproject.toml, src/ui/frontend/package.json and every runtime default (the
launcher, the update manager, the license flow) must agree with this value;
tests/unit/test_audit_2026_10_01.py fails when they drift apart.
"""

from __future__ import annotations

__version__ = "13.0.0"
