import logging
from typing import List, Dict

class GridManager:
    def __init__(self, rooms: List[str], dates: List[str], num_blocks: int):
        """
        Initializes a 3D-like grid for room availability.
        :param rooms: List of room strings.
        :param dates: List of date strings ('YYYY-MM-DD').
        :param num_blocks: Number of time slots per day.
        """
        self.grid = {
            day: {
                room: [False] * num_blocks 
                for room in rooms
            }
            for day in dates
        }
        self.num_blocks = num_blocks

    def is_slot_free(self, day_str: str, room_str: str, start_block: int, total_blocks: int) -> bool:
        """Checks if a room is free for a given period."""
        if day_str not in self.grid or room_str not in self.grid[day_str]:
            return False
            
        end_block = min(start_block + total_blocks, self.num_blocks)
        return not any(self.grid[day_str][room_str][i] for i in range(start_block, end_block))

    def get_available_rooms(self, day_str: str, start_block: int, total_blocks: int) -> List[str]:
        """Returns a list of rooms that are free for the given period."""
        if day_str not in self.grid:
            return []
        
        available = []
        for room_str in self.grid[day_str]:
            if self.is_slot_free(day_str, room_str, start_block, total_blocks):
                available.append(room_str)
        return available

    def book_slot(self, day_str: str, rooms: List[str], start_block: int, total_blocks: int):
        """Marks a room as booked in the grid."""
        for room in rooms:
            room_str = str(room)
            if day_str in self.grid and room_str in self.grid[day_str]:
                end_block = min(start_block + total_blocks, self.num_blocks)
                for i in range(start_block, end_block):
                    self.grid[day_str][room_str][i] = True

    def clear_slot(self, day_str: str, rooms: List[str], start_block: int, total_blocks: int):
        """Marks a room as free in the grid."""
        for room in rooms:
            room_str = str(room)
            if day_str in self.grid and room_str in self.grid[day_str]:
                end_block = min(start_block + total_blocks, self.num_blocks)
                for i in range(start_block, end_block):
                    self.grid[day_str][room_str][i] = False
