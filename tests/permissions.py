from __future__ import annotations

import os

import pytest

requires_permission_bits = pytest.mark.skipif(
    os.geteuid() == 0, reason="root ignores file permission bits, so chmod 0 cannot make a path unreadable"
)
