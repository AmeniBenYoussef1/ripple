"""One option = one room = one capacity."""
from app.rooms import room_sizes, settle_rooms


def test_room_sizes_from_venue_texts():
    assert room_sizes("Three amphitheatres seating 220, 516 and 854; 18 reception rooms.") == {220, 516, 854}
    assert room_sizes("500-seat theatre (300 cocktail)") == {500}
    assert room_sizes("485-seat auditorium and 457 m² hall; 5 rooms of 80-120 seats") == {485, 120}
    assert room_sizes("Auditorium with 277 places") == {277}
    assert room_sizes("350 m² hall", None) == set()


def test_smallest_room_that_fits_and_room_to_confirm():
    cands = [{"name": "Maison de la Chimie", "url": "https://x.example.com", "capacity": 854,
              "fit_notes": "Three amphitheatres seating 220, 516 and 854"},
             {"name": "Small Hall", "url": "https://y.example.com", "capacity": 150, "fit_notes": ""}]
    scen = [{"id": "C", "capacity": 516, "attendees": 200,
             "changes": [{"node": "hall", "replace_with": "Maison de la Chimie – 516-seat amphitheatre"}]},
            {"id": "D", "capacity": None, "changes": [{"node": "hall", "replace_with": "Small Hall"}]},
            {"id": "E", "title": "Postpone", "changes": [{"node": "lunch", "set": {"start": "13:00"}}]}]
    c, d, e = settle_rooms(scen, cands, 200)
    assert c["capacity"] == 220 and c["venue"]["room_to_confirm"] and c["venue"]["rooms"] == [220, 516, 854]
    assert c["venue"]["name"] == "Maison de la Chimie" and c["venue"]["url"] == "https://x.example.com"
    assert d["capacity"] == 150 and not d["venue"]["room_to_confirm"]          # nothing fits: the largest it has
    assert "venue" not in e and e == scen[2]                                   # not a venue option: unchanged
