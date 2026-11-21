"""
Coordinates from what the admin sends when creating or moving a location:

- plain text:        "38.829001, 65.793878" (Google "copy coordinates" gives this)
- a full map link:   Google  .../place/...!3d<lat>!4d<lon>...   or  ?q=<lat>,<lon>  or  /@<lat>,<lon>,17z
                     Yandex  ?pt=<lon>,<lat>  ?whatshere[point]=<lon>,<lat>  ?ll=<lon>,<lat>
                     (Yandex puts LONGITUDE first)
- a short link:      maps.app.goo.gl/...  yandex.uz/maps/-/...
                     followed through its redirects until a link with coordinates appears.

Links shared from the MOBILE apps of Google and Yandex carry only the place id, no
coordinates, even after the redirects. Their pages are not scraped: the coordinates in
them are ambiguous and the sites answer bots with captchas. The admin is asked for a
browser link, coordinates text or a Telegram location instead.

Only Google and Yandex hosts are ever requested (never an arbitrary link).
"""
import logging
import re
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import aiohttp

logger = logging.getLogger(__name__)

# Uzbekistan bounding box. Used only to fix a swapped latitude/longitude pair:
# here latitude is 37..46 and longitude 56..73, so the two ranges never overlap.
REGION_LAT = (37.0, 45.7)
REGION_LON = (55.9, 73.2)

MAX_REDIRECTS = 6
REQUEST_TIMEOUT_SECONDS = 10
# A browser-like agent: the short-link services answer it with a plain redirect
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

_NUM = r"-?\d{1,3}(?:\.\d+)?"
_DECIMAL = r"-?\d{1,3}\.\d+"
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_TEXT_PAIR_RE = re.compile(rf"^\s*\(?\s*({_DECIMAL})\s*[,;\s]\s*({_DECIMAL})\s*\)?\s*$")
_GOOGLE_PIN_RE = re.compile(rf"!3d({_NUM})!4d({_NUM})")
_GOOGLE_PATH_PAIR_RE = re.compile(rf"/(?:place|search|dir)/({_NUM}),\s*\+?({_NUM})")
_GOOGLE_VIEWPORT_RE = re.compile(rf"/@({_NUM}),({_NUM})")
_PAIR_RE = re.compile(rf"^\s*({_NUM})\s*,\s*\+?({_NUM})")

_GOOGLE_HOST_RE = re.compile(r"(^|\.)google\.[a-z]{2,3}(\.[a-z]{2})?$")
_YANDEX_HOST_RE = re.compile(r"(^|\.)yandex\.[a-z]{2,3}$")
_SHORT_GOOGLE_HOSTS = ("goo.gl", "maps.app.goo.gl")


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")


def _is_google(host: str) -> bool:
    return bool(_GOOGLE_HOST_RE.search(host)) or host in _SHORT_GOOGLE_HOSTS


def _is_yandex(host: str) -> bool:
    return bool(_YANDEX_HOST_RE.search(host))


def is_allowed_url(url: str) -> bool:
    """Only Google / Yandex maps links are parsed or requested."""
    host = _host(url)
    return urlparse(url).scheme in ("http", "https") and (_is_google(host) or _is_yandex(host))


def _in_region(lat: float, lon: float) -> bool:
    return REGION_LAT[0] <= lat <= REGION_LAT[1] and REGION_LON[0] <= lon <= REGION_LON[1]


def _valid(lat: float, lon: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


def _finish(lat: float, lon: float):
    """Validate a pair and fix it when latitude and longitude are obviously swapped."""
    if not _in_region(lat, lon) and _in_region(lon, lat):
        lat, lon = lon, lat
    return (round(lat, 7), round(lon, 7)) if _valid(lat, lon) else None


def parse_coordinates_text(text: str):
    """"38.829001, 65.793878" -> (38.829001, 65.793878); None when it is not such a pair."""
    match = _TEXT_PAIR_RE.match(text or "")
    if not match:
        return None
    return _finish(float(match.group(1)), float(match.group(2)))


def _first_pair(value: str):
    """"65.79,38.82" or "65.79,38.82,pm2rdm~..." -> (65.79, 38.82); None otherwise."""
    match = _PAIR_RE.match(value.split("~")[0])
    return (float(match.group(1)), float(match.group(2))) if match else None


def parse_map_url(url: str):
    """
    (lat, lon) written inside a Google / Yandex maps link, or None.
    Exact place markers are preferred over the map centre.
    """
    if not is_allowed_url(url):
        return None
    parsed = urlparse(url)
    host = _host(url)
    query = parse_qs(parsed.query)
    path = unquote(parsed.path)

    if _is_yandex(host):
        # Yandex: LONGITUDE first. pt / whatshere = a marker, ll = map centre
        for key in ("whatshere[point]", "pt", "ll"):
            for value in query.get(key, []):
                pair = _first_pair(value)
                if pair:
                    lon, lat = pair
                    return _finish(lat, lon)
        return None

    # Google: !3d!4d is the place pin (the last one is the selected place)
    pins = _GOOGLE_PIN_RE.findall(url)
    if pins:
        lat, lon = pins[-1]
        return _finish(float(lat), float(lon))
    for key in ("q", "query", "ll", "center", "destination", "daddr"):
        for value in query.get(key, []):
            pair = _first_pair(value)
            if pair:
                return _finish(*pair)
    match = _GOOGLE_PATH_PAIR_RE.search(path) or _GOOGLE_VIEWPORT_RE.search(path)
    if match:
        return _finish(float(match.group(1)), float(match.group(2)))
    return None


async def _resolve_short_url(url: str):
    """
    Follow the redirects of a short link (only through Google / Yandex hosts) and
    return the first coordinates found in a redirect target, or None.
    The page bodies are never read.
    """
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT}) as session:
        for _ in range(MAX_REDIRECTS):
            if not is_allowed_url(url):
                logger.info("[geo] redirect left the allowed hosts: %s", _host(url))
                return None
            coordinates = parse_map_url(url)
            if coordinates:
                return coordinates

            async with session.get(url, allow_redirects=False) as response:
                target = response.headers.get("Location")
                if response.status not in (301, 302, 303, 307, 308) or not target:
                    return None
            url = urljoin(url, target)

            # Google may send a cookie-consent page first; the real link is in "continue"
            if _host(url) == "consent.google.com":
                url = parse_qs(urlparse(url).query).get("continue", [""])[0]
    return None


# Result codes of extract_coordinates()
OK = "ok"
NOT_FOUND = "not_found"          # no coordinates in the text / link
NOT_ALLOWED = "not_allowed"      # a link, but not a Google / Yandex one
NETWORK_ERROR = "network_error"  # the short link could not be opened


async def extract_coordinates(text: str):
    """
    (status, (lat, lon) or None) from the admin's text: coordinates or a maps link.
    """
    text = (text or "").strip()
    url_match = _URL_RE.search(text)
    if url_match is None:
        coordinates = parse_coordinates_text(text)
        return (OK, coordinates) if coordinates else (NOT_FOUND, None)

    url = url_match.group(0).rstrip(".,)")
    if not is_allowed_url(url):
        return NOT_ALLOWED, None

    coordinates = parse_map_url(url)
    if coordinates:
        return OK, coordinates

    try:
        coordinates = await _resolve_short_url(url)
    except (aiohttp.ClientError, TimeoutError) as error:
        logger.warning("[geo] could not open %s: %r", _host(url), error)
        return NETWORK_ERROR, None
    return (OK, coordinates) if coordinates else (NOT_FOUND, None)
