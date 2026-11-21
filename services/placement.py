"""
Where an employee works: department and location, read from the database.

Rules (they replace the old hardcoded "Savdo" checks):
- a department with ONE active location: the employee gets it automatically;
- a department with SEVERAL active locations: the employee chooses one
  (the choice is remembered in employees.location_id);
- an employee whose department is missing or deactivated chooses a department again.
"""
from dataclasses import dataclass

import database as db


@dataclass(frozen=True)
class Location:
    id: int
    name: str
    lat: float
    lon: float
    group_id: int | None  # Telegram group for videos and messages; None = not set


@dataclass(frozen=True)
class Department:
    id: int
    name: str


def _location(row) -> Location:
    location_id, name, lat, lon, group_id, _is_active = row
    return Location(location_id, name, lat, lon, group_id)


async def get_location(location_id: int) -> Location | None:
    """Active location by id, or None."""
    row = await db.get_location(location_id)
    return _location(row) if row and row[5] else None


async def get_location_any(location_id: int) -> Location | None:
    """Location by id even when it is deactivated, or None."""
    row = await db.get_location(location_id)
    return _location(row) if row else None


async def open_shift_location(attendance_id: int) -> Location | None:
    """
    Where an open shift started. A shift is finished at that location even if the
    admin deactivated it or unlinked it from the department after the check-in.
    None for old records that have no location_id.
    """
    location_id = await db.get_attendance_location_id(attendance_id)
    return await get_location_any(location_id) if location_id else None


async def get_department(department_id: int) -> Department | None:
    """Active department by id, or None."""
    row = await db.get_department(department_id)
    return Department(row[0], row[1]) if row and row[2] else None


async def list_departments() -> list[Department]:
    return [Department(dep_id, name) for dep_id, name, _ in await db.list_departments()]


async def department_locations(department_id: int) -> list[Location]:
    return [_location(row) for row in await db.get_department_locations(department_id)]


async def employee_placement(user_id: int):
    """
    (department, locations, last_location_id) of the employee.
    department is None when the employee has no active department (must choose one);
    locations are the active locations of that department.
    """
    row = await db.get_employee_placement(user_id)
    if row is None or row[0] is None:
        return None, [], None
    department_id, last_location_id = row

    department = await get_department(department_id)
    if department is None:
        return None, [], None
    return department, await department_locations(department.id), last_location_id


async def current_location(user_id: int, chosen_id: int | None = None) -> Location | None:
    """
    Location to use for the employee right now, or None when the employee must
    choose again with /start. chosen_id (from the FSM data) wins while it is still
    a valid location of the employee's department.
    """
    _department, locations, last_location_id = await employee_placement(user_id)
    by_id = {location.id: location for location in locations}

    if chosen_id in by_id:
        return by_id[chosen_id]
    if len(locations) == 1:
        return locations[0]
    return by_id.get(last_location_id)