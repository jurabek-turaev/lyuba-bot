from geopy.distance import geodesic


def distance_to_branch(user_loc: tuple, branch: dict) -> float:
    """user_loc: (lat, lon). branch: {'lat': .., 'lon': ..}."""
    return geodesic(user_loc, (branch["lat"], branch["lon"])).meters


def is_within_range(user_loc: tuple, branch: dict, max_distance: float) -> bool:
    return distance_to_branch(user_loc, branch) <= max_distance