import pytest
from services.scheduler_core.grid_manager import GridManager

@pytest.fixture
def empty_grid():
    rooms = ["101", "102"]
    dates = ["2026-01-10", "2026-01-11"]
    num_blocks = 5
    return GridManager(rooms, dates, num_blocks)

def test_grid_initialization(empty_grid):
    assert empty_grid.num_blocks == 5
    assert len(empty_grid.grid) == 2
    assert "2026-01-10" in empty_grid.grid
    assert "101" in empty_grid.grid["2026-01-10"]
    # All slots should be False initially
    assert all(not slot for slot in empty_grid.grid["2026-01-10"]["101"])

def test_is_slot_free(empty_grid):
    # Free slot
    assert empty_grid.is_slot_free("2026-01-10", "101", 0, 2) is True
    
    # Invalid day or room returns False
    assert empty_grid.is_slot_free("invalid-date", "101", 0, 2) is False
    assert empty_grid.is_slot_free("2026-01-10", "invalid-room", 0, 2) is False

def test_book_slot_and_check_free(empty_grid):
    empty_grid.book_slot("2026-01-10", ["101"], 1, 2)
    
    # Slots 1 and 2 should be booked
    assert empty_grid.is_slot_free("2026-01-10", "101", 1, 1) is False
    assert empty_grid.is_slot_free("2026-01-10", "101", 2, 1) is False
    
    # Slot 0 and 3 should be free
    assert empty_grid.is_slot_free("2026-01-10", "101", 0, 1) is True
    assert empty_grid.is_slot_free("2026-01-10", "101", 3, 1) is True
    
    # Checks that span over booked slot
    assert empty_grid.is_slot_free("2026-01-10", "101", 0, 3) is False

def test_get_available_rooms(empty_grid):
    # Book room 101 fully for day 1
    empty_grid.book_slot("2026-01-10", ["101"], 0, 5)
    
    available = empty_grid.get_available_rooms("2026-01-10", 0, 5)
    assert available == ["102"]  # 101 is booked

def test_clear_slot(empty_grid):
    empty_grid.book_slot("2026-01-10", ["101"], 0, 2)
    assert empty_grid.is_slot_free("2026-01-10", "101", 0, 2) is False
    
    empty_grid.clear_slot("2026-01-10", ["101"], 0, 1)
    
    # Slot 0 is now free, 1 is still booked
    assert empty_grid.is_slot_free("2026-01-10", "101", 0, 1) is True
    assert empty_grid.is_slot_free("2026-01-10", "101", 1, 1) is False
