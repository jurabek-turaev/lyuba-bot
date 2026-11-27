"""
Where an employee works: department, position and location, read from the database.

Rules (they replace the old hardcoded "Savdo" checks):
- a department with active positions: the employee chooses one after the department
  (a department without positions skips that question);
- a department WITHOUT branches (Ishlab chiqarish, Rahbariyat) works at the base work
  place, Zavod: nothing is asked;
- a department with branches (Savdo shops): ONE active branch is chosen automatically,
  SEVERAL: the employee chooses one (remembered in employees.location_id);
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


@dataclass(frozen=True)
class Position:
    id: int
    department_id: int
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


async def department_positions(department_id: int) -> list[Position]:
    """Active positions of a department, in creation order."""
    return [Position(pos_id, department_id, name) for pos_id, name, _ in await db.list_positions(department_id)]


async def get_position(position_id: int) -> Position | None:
    """Active position of an active department by id, or None."""
    row = await db.get_position(position_id)
    if row is None or not row[3] or await get_department(row[1]) is None:
        return None
    return Position(row[0], row[1], row[2])


async def get_base_location() -> Location | None:
    """The active base work place (Zavod), or None."""
    row = await db.get_base_location()
    return _location(row) if row and row[5] else None


async def department_locations(department_id: int) -> list[Location]:
    """
    Where the department's employees may check in: its active branches, or the base
    work place when the department has no branches at all. A department whose
    branches are all deactivated gets nothing (it does not move to Zavod silently).
    """
    branches = await db.get_department_locations(department_id, active_only=False)
    if branches:
        return [_location(row) for row in branches if row[5]]
    base = await get_base_location()
    return [base] if base else []


async def has_branches(department_id: int) -> bool:
    return bool(await db.get_department_locations(department_id, active_only=False))


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