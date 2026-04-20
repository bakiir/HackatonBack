"""
test_seating_manager.py – thin wrappers kept only for backwards-compat.
The real seating tests live inside test_proctor_manager.py (TestSeatingManager).
"""
import pytest


def test_seating_module_importable():
    """SeatingManager should import without error."""
    from services.scheduler_core.seating_manager import SeatingManager
    assert SeatingManager is not None
