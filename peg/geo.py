"""Geodesy: real Earth, real metres.

PEG's playable maps are 1 m tiles. Earth is an oblate spheroid, so a single
global 1 m square grid is impossible -- meridians converge and the cells stop
being square long before you reach the poles. Faking it (a Mercator grid where
a "metre" is 1 m at the equator and 30 cm at Reykjavik) would quietly corrupt
every distance, area and yield calculation downstream.

So PEG uses two frames:

* **Global** -- geodetic latitude/longitude on WGS84. Used by the strategic
  layer, worldgen and anything that spans more than a few kilometres.
* **Local** -- a plane tangent to the ellipsoid at a site's origin, with axes
  east and north, graduated in exact metres. This is the frame the tile grid
  lives in.

Over a site (at most a few km across) the tangent-plane error is millimetres,
so tiles really are 1 m squares. Cross-site travel converts back to geodetic
and uses proper great-circle maths.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# WGS84
A = 6378137.0                  # semi-major axis, m
F = 1.0 / 298.257223563        # flattening
B = A * (1.0 - F)              # semi-minor axis, m
E2 = F * (2.0 - F)             # first eccentricity squared
MEAN_R = 6371008.8             # mean radius, m

#: Earth's surface area in m^2, and therefore the number of 1 m^2 tiles that
#: would exist if PEG ever generated the whole planet at once. It does not.
EARTH_AREA_M2 = 5.10065600e14
LAND_AREA_M2 = 1.48940000e14

DEG = math.pi / 180.0
RAD = 180.0 / math.pi


def meters_per_degree_lat(lat_deg: float) -> float:
    """Length of one degree of latitude. Varies ~1.1 km between equator and
    pole because the ellipsoid is flatter at the poles."""
    lat = lat_deg * DEG
    return (
        111132.92
        - 559.82 * math.cos(2 * lat)
        + 1.175 * math.cos(4 * lat)
        - 0.0023 * math.cos(6 * lat)
    )


def meters_per_degree_lon(lat_deg: float) -> float:
    """Length of one degree of longitude. Goes to zero at the poles."""
    lat = lat_deg * DEG
    return (
        111412.84 * math.cos(lat)
        - 93.5 * math.cos(3 * lat)
        + 0.118 * math.cos(5 * lat)
    )


def wrap_lon(lon: float) -> float:
    """Normalise longitude to [-180, 180)."""
    return (lon + 180.0) % 360.0 - 180.0


def clamp_lat(lat: float) -> float:
    return -90.0 if lat < -90.0 else (90.0 if lat > 90.0 else lat)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance on a sphere of mean radius. Good to ~0.3% -- fine
    for travel times, wrong for surveying, and we never survey."""
    p1 = lat1 * DEG
    p2 = lat2 * DEG
    dp = (lat2 - lat1) * DEG
    dl = (lon2 - lon1) * DEG
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * MEAN_R * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing, degrees clockwise from north."""
    p1 = lat1 * DEG
    p2 = lat2 * DEG
    dl = (lon2 - lon1) * DEG
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.atan2(y, x) * RAD) % 360.0


def offset_deg(lat: float, lon: float, bearing: float, dist_m: float) -> tuple[float, float]:
    """Travel ``dist_m`` along a great circle from a point."""
    ang = dist_m / MEAN_R
    p1 = lat * DEG
    l1 = lon * DEG
    br = bearing * DEG
    p2 = math.asin(
        math.sin(p1) * math.cos(ang) + math.cos(p1) * math.sin(ang) * math.cos(br)
    )
    l2 = l1 + math.atan2(
        math.sin(br) * math.sin(ang) * math.cos(p1),
        math.cos(ang) - math.sin(p1) * math.sin(p2),
    )
    return clamp_lat(p2 * RAD), wrap_lon(l2 * RAD)


def cell_area_m2(lat: float, dlat_deg: float, dlon_deg: float) -> float:
    """Area of a lat/lon cell, accounting for meridian convergence. The
    strategic layer needs this or high-latitude claims look enormous."""
    lat_n = clamp_lat(lat + dlat_deg / 2) * DEG
    lat_s = clamp_lat(lat - dlat_deg / 2) * DEG
    return abs(
        (dlon_deg * DEG) * A * A * (1 - E2) *
        (_auth(lat_n) - _auth(lat_s))
    )


def _auth(lat_rad: float) -> float:
    """Antiderivative used by the authalic (equal-area) cell integral."""
    s = math.sin(lat_rad)
    d = 1 - E2 * s * s
    return s / d + (1 / (2 * math.sqrt(E2))) * math.log((1 + math.sqrt(E2) * s) / (1 - math.sqrt(E2) * s))


@dataclass(frozen=True)
class LocalFrame:
    """A tangent plane at ``(lat, lon)``, graduated in metres.

    ``x`` is east, ``y`` is north. Tile ``(0, 0)`` is the square whose
    south-west corner is the origin. Error against the true ellipsoid is under
    a centimetre within 5 km of the origin, which is larger than any site.
    """

    lat: float
    lon: float

    @property
    def m_per_deg_lat(self) -> float:
        return meters_per_degree_lat(self.lat)

    @property
    def m_per_deg_lon(self) -> float:
        # Guard the poles: a site at 89.999 would otherwise divide by ~0.
        return max(1.0, meters_per_degree_lon(self.lat))

    def to_geo(self, x_m: float, y_m: float) -> tuple[float, float]:
        lat = clamp_lat(self.lat + y_m / self.m_per_deg_lat)
        lon = wrap_lon(self.lon + x_m / self.m_per_deg_lon)
        return lat, lon

    def from_geo(self, lat: float, lon: float) -> tuple[float, float]:
        dlon = wrap_lon(lon - self.lon)
        return dlon * self.m_per_deg_lon, (lat - self.lat) * self.m_per_deg_lat


# --------------------------------------------------------------------------
# solar geometry -- drives temperature, daylight and solar power
# --------------------------------------------------------------------------

#: Tropical year. PEG runs a 365-day calendar and drops the quarter day; over
#: a century-long campaign the seasons drift by under a month, which nobody
#: will notice and which saves a leap-year branch in every date calculation.
DAYS_PER_YEAR = 365
AXIAL_TILT = 23.44


def solar_declination(day_of_year: int) -> float:
    """Sub-solar latitude, degrees. Peaks at +23.44 on the June solstice."""
    return -AXIAL_TILT * math.cos(2 * math.pi * (day_of_year + 10) / DAYS_PER_YEAR)


def day_length_h(lat: float, day_of_year: int) -> float:
    """Hours between sunrise and sunset, including polar day/night."""
    d = solar_declination(day_of_year) * DEG
    p = clamp_lat(lat) * DEG
    cos_h = -math.tan(p) * math.tan(d)
    if cos_h >= 1.0:
        return 0.0
    if cos_h <= -1.0:
        return 24.0
    return 24.0 * math.acos(cos_h) / math.pi


def solar_elevation(lat: float, lon: float, day_of_year: int, utc_hour: float) -> float:
    """Sun angle above the horizon, degrees. Negative is night."""
    d = solar_declination(day_of_year) * DEG
    p = clamp_lat(lat) * DEG
    # Hour angle: 15 deg per hour, zero at local solar noon.
    solar_hour = (utc_hour + lon / 15.0) % 24.0
    h = (solar_hour - 12.0) * 15.0 * DEG
    sin_e = math.sin(p) * math.sin(d) + math.cos(p) * math.cos(d) * math.cos(h)
    return math.asin(max(-1.0, min(1.0, sin_e))) * RAD


def daily_insolation(lat: float, day_of_year: int) -> float:
    """Top-of-atmosphere daily mean irradiance, W/m^2.

    This is the engine behind climate: everything else (temperature, growing
    season, solar yield) is a transformation of how much energy arrives here
    today.
    """
    d = solar_declination(day_of_year) * DEG
    p = clamp_lat(lat) * DEG
    cos_h0 = -math.tan(p) * math.tan(d)
    if cos_h0 >= 1.0:
        return 0.0
    h0 = math.pi if cos_h0 <= -1.0 else math.acos(cos_h0)
    # Orbital eccentricity: Earth is ~3.4% closer to the sun in January.
    dist_factor = 1.0 + 0.033 * math.cos(2 * math.pi * day_of_year / DAYS_PER_YEAR)
    s0 = 1361.0 * dist_factor
    return (s0 / math.pi) * (
        h0 * math.sin(p) * math.sin(d) + math.cos(p) * math.cos(d) * math.sin(h0)
    )


def local_solar_time(lon: float, utc_hour: float) -> float:
    """Mean solar time at a longitude. PEG has no time zones -- there is no
    one left to legislate them."""
    return (utc_hour + lon / 15.0) % 24.0


def timezone_offset_h(lon: float) -> float:
    """Nominal offset from UTC, used only for display."""
    return round(lon / 15.0)
