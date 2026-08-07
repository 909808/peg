"""Climate: monthly normals derived from physics, not painted on a map.

Nothing about "this is a desert" is stored anywhere. The Sahara, the Atacama
and the Gobi exist in PEG because of where the Hadley cells subside, where the
Humboldt current chills the air, and what the Himalayas do to a westerly. That
matters for play: it means a rain shadow behind a range you can see on the map
is *actually* dry, and a colony sited in one will actually starve.

The model, per point:

  temperature  zonal sea-level curve, moved by elevation (lapse rate), damped
               or amplified seasonally by continentality, offset by ocean
               currents
  precipitation zonal cell structure (ITCZ, subtropical high, storm track),
               modulated by distance from a moisture source, orographic lift
               and rain shadow, monsoon, and current temperature anomalies

Output is twelve monthly means, which is exactly what the Koppen
classification wants and what crop growth models want.

Accuracy is checked against 48 real cities in tests/test_climate.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .. import geo
from .. import rng
from . import raster

#: Free-air environmental lapse rate, degrees C per metre. Applies to the
#: first kilometre of altitude.
LAPSE = 0.0065

#: Above about a kilometre, high ground stops behaving like free air at that
#: height and starts behaving like a heated surface: the plateau absorbs
#: sunlight and warms the air over it. Quito at 2850 m is 13 C, not the 8 C a
#: uniform lapse rate predicts. This is the "elevated heat source" effect and
#: without it every plateau in the game freezes.
LAPSE_HIGH = 0.0042
LAPSE_KNEE = 1000.0


def lapse_cooling(elev_m: float) -> float:
    """Temperature drop, degrees C, for a surface at this altitude."""
    if elev_m <= LAPSE_KNEE:
        return max(0.0, elev_m) * LAPSE
    return LAPSE_KNEE * LAPSE + (elev_m - LAPSE_KNEE) * LAPSE_HIGH

MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
MONTH_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
#: Day-of-year at the middle of each month.
MONTH_MID = (15, 45, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349)


# --------------------------------------------------------------------------
# ocean currents
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Current:
    """A boundary current, as a temperature anomaly on the air above it.

    These are hand-placed because they are the one part of Earth's climate that
    is genuinely a historical accident of basin shape rather than something a
    zonal model derives. Leaving them out costs you the Atacama, the Namib and
    an ice-free Norway -- three of the most legible facts on the map.
    """

    name: str
    lon: float
    lat: float
    reach_km: float
    delta_t: float      # air temperature anomaly at the core, degrees C
    wet_factor: float   # multiplier on precipitation for onshore air


CURRENTS = (
    # Warm western-boundary currents and their extensions.
    Current("North Atlantic Drift", -8.0, 57.0, 2200.0, 9.0, 1.30),
    Current("North Atlantic Drift", 5.0, 65.0, 1200.0, 11.0, 1.20),
    Current("Gulf Stream", -74.0, 33.0, 900.0, 3.0, 1.15),
    Current("Kuroshio", 138.0, 33.0, 900.0, 3.5, 1.15),
    Current("Brazil Current", -44.0, -26.0, 900.0, 2.5, 1.15),
    Current("Agulhas", 32.0, -32.0, 800.0, 2.5, 1.15),
    Current("East Australian", 154.0, -32.0, 800.0, 2.0, 1.10),
    # Cold eastern-boundary upwelling currents. These make the great coastal
    # deserts: cool air over a warm land cannot rise, so it does not rain.
    Current("Humboldt", -73.0, -20.0, 1100.0, -6.0, 0.16),
    Current("Benguela", 12.0, -24.0, 1000.0, -5.5, 0.16),
    Current("Canary", -16.0, 24.0, 900.0, -3.5, 0.55),
    Current("California", -122.0, 34.0, 800.0, -3.5, 0.62),
    Current("West Australian", 112.0, -28.0, 700.0, -1.5, 0.90),
    Current("Labrador", -55.0, 52.0, 900.0, -6.0, 0.85),
    Current("Oyashio", 150.0, 47.0, 800.0, -4.5, 0.90),
    Current("Somali upwelling", 52.0, 8.0, 700.0, -2.0, 0.30),
    Current("East African coastal", 42.0, -3.0, 900.0, -1.0, 0.40),
    Current("Irminger", -20.0, 63.0, 900.0, 7.0, 1.20),
    Current("Alaska Current", -148.0, 58.0, 900.0, 5.0, 1.25),
    Current("Norwegian Current", 15.0, 69.0, 1100.0, 10.0, 1.10),
)


def _current_effect(lon: float, lat: float, coast_km: float) -> tuple[float, float]:
    """Combined temperature offset and precipitation multiplier at a point.

    Influence decays with distance from the current's core *and* with distance
    inland -- a maritime air mass is spent a few hundred km from the coast.
    """
    if coast_km > 1600.0:
        return 0.0, 1.0
    # A maritime air mass carries its temperature much further inland than its
    # moisture: western Europe is mild 800 km from the sea but not especially
    # wet there. Two decay scales, not one.
    inland_t = math.exp(-max(0.0, coast_km) / 750.0)
    inland_p = math.exp(-max(0.0, coast_km) / 330.0)
    dt = 0.0
    wet_num = 0.0
    wet_den = 0.0
    for cur in CURRENTS:
        d = geo.haversine_m(lat, lon, cur.lat, cur.lon) / 1000.0
        if d > cur.reach_km:
            continue
        w = (1.0 - d / cur.reach_km) ** 1.5
        dt += cur.delta_t * w * inland_t
        wet_num += cur.wet_factor * w
        wet_den += w
    wet = (wet_num / wet_den) if wet_den > 0 else 1.0
    wet = 1.0 + (wet - 1.0) * inland_p
    return dt, wet


# --------------------------------------------------------------------------
# winds and orography
# --------------------------------------------------------------------------


def prevailing_wind(lat: float, day: int = 196) -> tuple[float, float]:
    """Unit vector the wind blows *towards*, in (east, north) components.

    The three-cell circulation: easterly trades to 30 degrees, westerlies to
    60, polar easterlies beyond. The cell boundaries migrate a few degrees
    with the season, which is what gives Mediterranean climates their dry
    summer -- the storm track walks off the poleward edge and comes back.
    """
    shift = 6.0 * math.sin(2 * math.pi * (day - 15) / 365.0)
    a = abs(lat) - (shift if lat >= 0 else -shift)
    hemi = 1.0 if lat >= 0 else -1.0
    if a < 30.0:
        # Trades: from the north-east in the northern hemisphere.
        return -0.94, -0.34 * hemi
    if a < 62.0:
        # Westerlies: from the south-west in the northern hemisphere.
        return 0.94, 0.34 * hemi
    return -0.90, 0.44 * hemi


def _orographic(r: raster.WorldRaster, lon: float, lat: float, elev: float,
                wx: float, wy: float) -> tuple[float, float]:
    """Return ``(lift_factor, shadow_factor)`` for precipitation.

    Walks upwind in steps, comparing the terrain there with the terrain here.
    Air climbing a slope rains; air that has already crossed a barrier has
    nothing left to give.

    ``(wx, wy)`` is the direction the *rain-bearing* air travels, which is not
    always the prevailing wind. Getting this wrong is expensive: computed
    against the westerlies, Tokyo sits in the lee of the Japanese Alps and
    comes out a desert, when in reality its rain arrives off the Pacific to
    the south-east.
    """
    # Step *against* the flow direction to find where the air came from.
    step_km = 70.0
    max_up = elev
    rise = 0.0
    prev = elev
    # Eight steps reaches 560 km. Six was not enough: the Peninsular Ranges
    # that dry out Phoenix sit about 450 km upwind of it.
    for k in range(1, 9):
        d_km = step_km * k
        dlat = -wy * d_km / 110.574
        dlon = -wx * d_km / max(1.0, 111.320 * math.cos(math.radians(lat)))
        h = r.elevation(geo.wrap_lon(lon + dlon), geo.clamp_lat(lat + dlat))
        h = max(0.0, h)
        if k <= 2:
            rise += (prev - h)
        prev = h
        if h > max_up:
            max_up = h
    # Lift: how much the air must climb over the last ~150 km. Capped well
    # below what the raw slope would suggest -- a windward face gets perhaps
    # twice the regional rainfall, not ten times.
    lift = 1.0 + rng.clamp(rise / 450.0, -0.40, 1.25) * 0.80
    # Shadow: how much higher the upwind barrier is than we are. Floored at
    # 0.30 -- no rain shadow on Earth is total. Death Valley sits behind four
    # ranges and still gets 60 mm.
    barrier = max(0.0, max_up - elev)
    shadow = 0.30 + 0.70 * math.exp(-barrier / 850.0)
    return lift, shadow


# --------------------------------------------------------------------------
# the model
# --------------------------------------------------------------------------


def _sea_level_temp(lat: float) -> float:
    """Zonal mean sea-level air temperature, degrees C.

    Fitted to observed zonal means: 26 at the equator, 20 at 30 degrees, 12 at
    45, 2 at 60, -18 at the pole.
    """
    c = math.cos(math.radians(geo.clamp_lat(lat)))
    return -19.0 + 45.5 * (max(0.0, c) ** 1.15)


def _seasonal_amplitude(lat: float, continentality: float) -> float:
    """Half the annual temperature range, degrees C.

    Continental interiors swing hugely (Yakutsk: -38 to +19); maritime places
    barely move (Reykjavik: -0.5 to +11).
    """
    s = abs(math.sin(math.radians(geo.clamp_lat(lat))))
    base = 25.0 * (s ** 1.35)
    return base * (0.25 + 1.30 * continentality)


def _itcz_lat(day: int) -> float:
    """Latitude of the intertropical convergence zone.

    Follows the sub-solar point but lags it and overshoots north, because the
    northern hemisphere has more land to heat. This asymmetry is why the Sahel
    and the Indian monsoon behave the way they do.
    """
    return 3.0 + 11.0 * math.sin(2 * math.pi * (day - 100) / 365.0)


def _zonal_precip(lat: float) -> float:
    """Baseline annual precipitation from the general circulation, mm.

    Three features, in order of importance: the ITCZ maximum at the equator,
    the subtropical subsidence minimum near 27 degrees (this is what makes the
    world's desert belt), and the mid-latitude storm-track maximum near 50.
    """
    a = abs(lat)
    equatorial = 1850.0 * math.exp(-((a / 13.0) ** 2))
    midlat = 880.0 * math.exp(-(((a - 50.0) / 17.0) ** 2))
    # Descending limb of the Hadley cell: air that rained out at the equator
    # returns warm and dry.
    subsidence = 1.0 - 0.38 * math.exp(-(((a - 27.0) / 9.0) ** 2))
    polar_dry = math.exp(-max(0.0, a - 68.0) / 16.0)
    return (equatorial + midlat + 300.0) * subsidence * polar_dry


def _warm_sea_supply(r: raster.WorldRaster, lon: float, lat: float,
                     hemi: float) -> float:
    """Moisture reaching an inland site from a warm sea on its equatorward
    side, 0-1.

    The Great Plains low-level jet is the archetype: the Gulf of Mexico feeds
    the whole North American interior in summer, which is why Iowa gets
    900 mm a year while Ulaanbaatar, no further from the sea, gets 270. The
    difference is not distance, it is that Mongolia's equatorward fetch is
    blocked by 2000 km of Asia.

    So the probe walks towards the equator looking for open water, and gives
    up if it has to cross a mountain barrier to get there -- which correctly
    denies central Asia the Arabian Sea on the far side of the Hindu Kush.
    """
    coslat = max(0.1, math.cos(math.radians(lat)))
    barrier = 0.0
    for d_km in (300.0, 600.0, 900.0, 1300.0, 1700.0):
        la2 = geo.clamp_lat(lat - hemi * d_km / 110.574)
        found = False
        for dl in (-7.0, -3.5, 0.0, 3.5, 7.0):
            lo2 = geo.wrap_lon(lon + dl / coslat)
            if not r.is_land(lo2, la2):
                found = True
            else:
                barrier = max(barrier, r.elevation(lo2, la2))
        if found:
            # Fetch is spent both by distance and by anything it climbed over.
            reach = math.exp(-d_km / 1400.0)
            block = math.exp(-max(0.0, barrier - 700.0) / 700.0)
            return reach * block
    return 0.0


def _moisture_supply(lat: float, coast_km: float) -> float:
    """Fraction of maritime moisture that survives to this distance inland.

    Never reaches zero, because continents recycle their own evaporation --
    but how much they recycle depends on how much they evaporate. The Amazon
    and the Congo stay wet 1500 km from the sea; the Gobi, equally far inland
    at 47 degrees, does not.
    """
    recycling = 0.10 + 0.32 * math.exp(-((lat / 18.0) ** 2))
    return recycling + (1.0 - recycling) * math.exp(-max(0.0, coast_km) / 1250.0)


def _ocean_to_the_east(r: raster.WorldRaster, lon: float, lat: float) -> float:
    """How much open ocean lies east of here, 0 to 1.

    Distinguishes an eastern continental margin from a western one, which is
    the single most important thing to know about a subtropical coast: eastern
    margins at 20-40 degrees sit on the moist western flank of the subtropical
    high and are humid (Atlanta, Shanghai, Sydney, Buenos Aires); western
    margins at the same latitude sit under its dry eastern flank and are
    Mediterranean or desert (Los Angeles, Perth, Casablanca).
    """
    hits = 0
    probes = (150.0, 350.0, 550.0, 800.0, 1100.0, 1450.0)
    coslat = max(0.1, math.cos(math.radians(lat)))
    for d_km in probes:
        dlon = d_km / (111.32 * coslat)
        if not r.is_land(geo.wrap_lon(lon + dlon), lat):
            hits += 1
    return hits / len(probes)


def _east_margin_boost(ocean_east: float, lat: float,
                       coast_km: float, day: int, hemi: float) -> float:
    """Warm moist advection up an eastern continental margin, 0 to ~1.8.

    Peaks in summer, when the subtropical high is strongest and the land is
    hot. This term is why humid subtropical climates exist at all; a purely
    zonal model puts deserts at 30 degrees on every coast.
    """
    a = abs(lat)
    if a < 8.0 or a > 48.0 or coast_km > 1600.0:
        return 0.0
    season = math.cos(2 * math.pi * (day - 196.0) / 365.0) * hemi
    # Even in winter these margins keep some maritime supply.
    season = 0.60 + 0.40 * max(0.0, season)
    band = math.exp(-(((a - 28.0) / 16.0) ** 2))
    reach = math.exp(-max(0.0, coast_km - 250.0) / 900.0)
    return 2.0 * ocean_east * band * reach * season


@dataclass
class Climate:
    """Twelve monthly normals plus the derived summary numbers everything
    else in the game actually consumes."""

    lat: float
    lon: float
    elev_m: float
    temp_c: tuple[float, ...]        # monthly mean, degrees C
    precip_mm: tuple[float, ...]     # monthly total, mm
    continentality: float
    koppen: str

    # ---- summary properties ----

    @property
    def mean_temp(self) -> float:
        return sum(self.temp_c) / 12.0

    @property
    def annual_precip(self) -> float:
        return sum(self.precip_mm)

    @property
    def coldest(self) -> float:
        return min(self.temp_c)

    @property
    def warmest(self) -> float:
        return max(self.temp_c)

    @property
    def temp_range(self) -> float:
        return self.warmest - self.coldest

    def temp_on_day(self, day: int) -> float:
        """Interpolate the monthly curve to a specific day of year."""
        pos = (day % 365) / 365.0 * 12.0 - 0.5
        i = int(math.floor(pos))
        t = pos - i
        return self.temp_c[i % 12] * (1 - t) + self.temp_c[(i + 1) % 12] * t

    def precip_on_day(self, day: int) -> float:
        """Expected precipitation for a single day, mm."""
        m = _month_of_day(day)
        return self.precip_mm[m] / MONTH_DAYS[m]

    @property
    def growing_days(self) -> int:
        """Days per year above 5 degrees C -- the usual threshold for
        temperate crop growth."""
        n = 0
        for d in range(0, 365, 5):
            if self.temp_on_day(d) > 5.0:
                n += 5
        return n

    def growing_degree_days(self, base: float = 10.0, cap: float = 30.0) -> float:
        """Accumulated heat above a base temperature. This is the number that
        decides whether maize will actually ripen before frost."""
        total = 0.0
        for d in range(365):
            t = min(cap, self.temp_on_day(d))
            if t > base:
                total += t - base
        return total

    @property
    def frost_free_days(self) -> int:
        return sum(1 for d in range(0, 365, 3) if self.temp_on_day(d) > 0.0) * 3

    @property
    def aridity_index(self) -> float:
        """Precipitation over potential evapotranspiration. Below 0.2 is
        arid, below 0.5 semi-arid, above 1 humid."""
        pet = self.annual_pet
        return self.annual_precip / pet if pet > 0 else 9.9

    @property
    def annual_pet(self) -> float:
        """Potential evapotranspiration by Thornthwaite, mm/yr. Crude, but it
        needs only monthly temperature, which is all we have."""
        heat = 0.0
        for t in self.temp_c:
            if t > 0:
                heat += (t / 5.0) ** 1.514
        if heat <= 0:
            return 1.0
        a = 6.75e-7 * heat ** 3 - 7.71e-5 * heat ** 2 + 1.792e-2 * heat + 0.49239
        total = 0.0
        for m, t in enumerate(self.temp_c):
            if t <= 0:
                continue
            # Daylight correction by month and latitude.
            dl = geo.day_length_h(self.lat, MONTH_MID[m])
            total += 16.0 * ((10.0 * t / heat) ** a) * (dl / 12.0) * (MONTH_DAYS[m] / 30.0)
        return max(1.0, total)


def _month_of_day(day: int) -> int:
    d = day % 365
    acc = 0
    for m, n in enumerate(MONTH_DAYS):
        acc += n
        if d < acc:
            return m
    return 11


def at(lon: float, lat: float, res: float = raster.DEFAULT_RES,
       seed: int = 0) -> Climate:
    """Compute the climate normals at a point."""
    r = raster.get(res, seed)
    coast_km = r.coast_distance_km(lon, lat)
    return _compute(r, lon, lat, settlement_elevation(r, lon, lat), coast_km)


def settlement_elevation(r: raster.WorldRaster, lon: float, lat: float) -> float:
    """Elevation of the ground a colony would actually occupy.

    A 55 km raster cell in the Basin and Range averages ridges and valley
    floors together, and reports something neither of them is. People do not
    settle on the mean: they settle in the valley, by the water. Biasing
    towards the low end of the cell's relief is both more accurate for the
    places that matter and the right input for climate, which is measured at
    inhabited altitudes.
    """
    cell = r.elevation(lon, lat)
    # Never drop below a quarter of the cell mean: the Scottish Highlands are
    # rugged, but their glens are still at 200 m, not at sea level.
    return max(0.0, max(cell * 0.25, cell - 0.40 * r.local_relief(lon, lat)))


def _compute(r: raster.WorldRaster, lon: float, lat: float,
             elev: float, coast_km: float) -> Climate:
    continentality = 1.0 - math.exp(-max(0.0, coast_km) / 800.0)
    dt_current, wet_current = _current_effect(lon, lat, coast_km)

    mean_sl = _sea_level_temp(lat) + dt_current
    amp = _seasonal_amplitude(lat, continentality)
    hemi = 1.0 if lat >= 0 else -1.0

    # --- temperature ------------------------------------------------------
    temps = []
    for m in range(12):
        day = MONTH_MID[m]
        # Peak heat lags the solstice by about a month over land, more over
        # sea. Continentality shortens the lag.
        lag = 30.0 - 12.0 * continentality
        phase = 2 * math.pi * (day - 172.0 - lag) / 365.0
        t = mean_sl + amp * math.cos(phase) * hemi
        t -= lapse_cooling(elev)
        temps.append(t)

    # --- precipitation ----------------------------------------------------
    ann_zonal = _zonal_precip(lat)

    supply = _moisture_supply(lat, coast_km)
    supply = max(supply, supply + 0.75 * _warm_sea_supply(r, lon, lat, hemi)
                 * (1.0 - supply))

    # Seasonal weights. These need the monthly temperatures computed above,
    # because summer convection scales with how hot the ground gets.
    # Seasonal weights first. These say *when* it rains; they are normalised
    # to a mean of one so that redistributing the year does not also invent or
    # destroy rainfall. Terms that genuinely change the annual total -- relief
    # and monsoon -- are applied afterwards.
    shares = []
    for m in range(12):
        day = MONTH_MID[m]
        # ITCZ: convective rain where the convergence zone sits this month.
        d_itcz = abs(lat - _itcz_lat(day))
        conv = math.exp(-((d_itcz / 12.0) ** 2))
        # Storm track: mid-latitude cyclones, migrating with the season. The
        # migration is what dries out Mediterranean summers -- the track walks
        # off polewards in June and comes back in October.
        track = 51.0 - 9.0 * math.cos(2 * math.pi * (day - 15) / 365.0) * hemi
        d_track = abs(abs(lat) - track)
        storm = math.exp(-((d_track / 14.0) ** 2))

        # The subtropical high migrates poleward in summer and sits on top of
        # the Mediterranean latitudes exactly when they would otherwise be
        # rained on. This is the whole mechanism of the dry-summer climate,
        # and without it Los Angeles and Perth come out as steppe.
        season = math.cos(2 * math.pi * (day - 196.0) / 365.0) * hemi
        high_lat = 30.0 + 6.0 * season
        subs = 1.0 - 0.55 * math.exp(-(((abs(lat) - high_lat) / 8.0) ** 2))

        # Summer convection: a heated continental surface builds thunderstorms
        # in the afternoon. This is the dominant warm-season rainfall mechanism
        # over continental interiors, and without it the American Midwest comes
        # out with a Mediterranean rainfall regime.  Gated hard on
        # continentality, because a coastal site has a stable marine layer
        # instead -- which is exactly why Los Angeles stays dry in July while
        # Iowa, at a similar latitude, does not.
        warmth = rng.clamp01((temps[m] - 8.0) / 20.0)
        convective = warmth * (continentality ** 1.5)

        shares.append((0.12 + 1.6 * conv + 1.0 * storm + 2.2 * convective) * subs)
    mean_share = sum(shares) / 12.0
    if mean_share > 0:
        shares = [s / mean_share for s in shares]

    ocean_east = _ocean_to_the_east(r, lon, lat)

    monthly = []
    for m in range(12):
        day = MONTH_MID[m]
        # Monsoon: a sea breeze on a continental scale. Needs a hot land mass,
        # a nearby ocean and a tropical-to-subtropical latitude.
        mon, mon_flow = _monsoon(lon, lat, coast_km, day, continentality, hemi)
        east = _east_margin_boost(ocean_east, lat, coast_km, day, hemi)

        # Blend the three moisture pathways into one flow direction, weighted
        # by how much rain each is delivering this month, and test the terrain
        # against that.
        wx, wy = prevailing_wind(lat, day)
        fx = wx + mon_flow[0] * mon * 1.5 + (-1.0) * east * 1.5
        fy = wy + mon_flow[1] * mon * 1.5 + (0.15 * hemi) * east * 1.5
        norm = math.hypot(fx, fy)
        if norm > 1e-6:
            fx /= norm
            fy /= norm
        else:
            fx, fy = wx, wy
        lift, shadow = _orographic(r, lon, lat, elev, fx, fy)

        base = ann_zonal / 12.0 * shares[m] * (1.0 + mon + east)
        monthly.append(max(0.0, base * supply * lift * shadow * wet_current))

    monthly_t = tuple(monthly)
    temps_t = tuple(temps)

    kop = koppen(temps_t, monthly_t, lat)
    return Climate(lat=lat, lon=lon, elev_m=elev, temp_c=temps_t,
                   precip_mm=monthly_t, continentality=continentality,
                   koppen=kop)


#: Monsoon systems: longitude/latitude box, peak strength, and the direction
#: the moist summer flow travels (east, north). The direction matters as much
#: as the strength -- it is what decides which side of a range gets the rain.
MONSOONS = (
    #  lon0   lon1   lat0   lat1  strength  flow
    ( 60.0, 100.0,   5.0,  38.0,     4.0, ( 0.75,  0.66)),  # South Asian, SW
    (100.0, 148.0,   5.0,  40.0,     3.2, (-0.60,  0.80)),  # East Asian, SE
    (-18.0,  40.0,   4.0,  18.0,     2.0, ( 0.80,  0.60)),  # West African, SW
    (110.0, 150.0, -22.0,  -8.0,     1.8, ( 0.70, -0.71)),  # N Australian, NW
    (-80.0, -40.0, -25.0,   2.0,     0.8, ( 0.30, -0.95)),  # South American
)


def _monsoon(lon: float, lat: float, coast_km: float, day: int,
             continentality: float, hemi: float) -> tuple[float, tuple[float, float]]:
    """Monsoon enhancement and its flow direction.

    A monsoon is a thermally driven seasonal wind reversal: a large land mass
    heats faster than the adjacent ocean, pressure falls over land, and moist
    air is drawn in. So the conditions are a hot summer, a big continent, and
    not too far from the sea.
    """
    a = abs(lat)
    if a > 40.0 or coast_km > 2400.0:
        return 0.0, (0.0, 0.0)
    season = math.cos(2 * math.pi * (day - 196.0) / 365.0) * hemi
    if season <= 0:
        return 0.0, (0.0, 0.0)
    for lon0, lon1, lat0, lat1, strength, flow in MONSOONS:
        if lon0 <= lon <= lon1 and lat0 <= lat <= lat1:
            reach = math.exp(-max(0.0, coast_km - 250.0) / 1200.0)
            # Monsoon rain is heaviest where the flow first makes landfall --
            # Mumbai gets 2200 mm, Delhi 800. Continentality must not be a
            # bonus here; if anything it is a mild penalty.
            return strength * season * reach * (1.0 - 0.25 * continentality), flow
    return 0.0, (0.0, 0.0)


# --------------------------------------------------------------------------
# Koppen-Geiger classification
# --------------------------------------------------------------------------


def koppen(temps: tuple[float, ...], precip: tuple[float, ...], lat: float) -> str:
    """Classify monthly normals into a Koppen-Geiger code.

    Implements the modern (0 degree C) C/D boundary. Returns codes like
    ``Cfb``, ``BWh``, ``Dfc``, ``ET``.
    """
    t_ann = sum(temps) / 12.0
    p_ann = sum(precip)
    t_cold = min(temps)
    t_hot = max(temps)
    p_min = min(precip)

    # Summer/winter half-years, hemisphere-aware.
    if lat >= 0:
        summer = (3, 4, 5, 6, 7, 8)      # Apr-Sep
        winter = (9, 10, 11, 0, 1, 2)
    else:
        summer = (9, 10, 11, 0, 1, 2)
        winter = (3, 4, 5, 6, 7, 8)
    p_summer = sum(precip[m] for m in summer)
    p_winter = sum(precip[m] for m in winter)

    # --- B: arid ---------------------------------------------------------
    if p_ann > 0:
        if p_summer >= 0.7 * p_ann:
            k = 280.0
        elif p_winter >= 0.7 * p_ann:
            k = 0.0
        else:
            k = 140.0
    else:
        k = 140.0
    threshold = 20.0 * t_ann + k
    if p_ann < threshold:
        letter2 = "W" if p_ann < threshold / 2.0 else "S"
        letter3 = "h" if t_ann >= 18.0 else "k"
        return "B" + letter2 + letter3

    # --- E: polar --------------------------------------------------------
    if t_hot < 10.0:
        return "EF" if t_hot < 0.0 else "ET"

    # --- A: tropical -----------------------------------------------------
    if t_cold >= 18.0:
        if p_min >= 60.0:
            return "Af"
        if p_min >= 100.0 - p_ann / 25.0:
            return "Am"
        return "Aw"

    # --- C / D -----------------------------------------------------------
    group = "C" if t_cold >= 0.0 else "D"

    p_summer_min = min(precip[m] for m in summer)
    p_summer_max = max(precip[m] for m in summer)
    p_winter_min = min(precip[m] for m in winter)
    p_winter_max = max(precip[m] for m in winter)

    if p_summer_min < 40.0 and p_winter_max >= 3.0 * p_summer_min:
        sub = "s"
    elif p_summer_max >= 10.0 * p_winter_min:
        sub = "w"
    else:
        sub = "f"

    months_over_10 = sum(1 for t in temps if t >= 10.0)
    if t_hot >= 22.0:
        third = "a"
    elif months_over_10 >= 4:
        third = "b"
    elif t_cold < -38.0 and group == "D":
        third = "d"
    else:
        third = "c"
    return group + sub + third


KOPPEN_NAMES = {
    "Af": "tropical rainforest", "Am": "tropical monsoon", "Aw": "tropical savanna",
    "BWh": "hot desert", "BWk": "cold desert",
    "BSh": "hot steppe", "BSk": "cold steppe",
    "Csa": "hot-summer Mediterranean", "Csb": "warm-summer Mediterranean",
    "Csc": "cold-summer Mediterranean",
    "Cwa": "monsoon humid subtropical", "Cwb": "subtropical highland",
    "Cwc": "cold subtropical highland",
    "Cfa": "humid subtropical", "Cfb": "oceanic", "Cfc": "subpolar oceanic",
    "Dsa": "hot-summer continental, dry summer",
    "Dsb": "warm-summer continental, dry summer",
    "Dsc": "subarctic, dry summer", "Dsd": "extreme subarctic, dry summer",
    "Dwa": "hot-summer continental, dry winter",
    "Dwb": "warm-summer continental, dry winter",
    "Dwc": "subarctic, dry winter", "Dwd": "extreme subarctic, dry winter",
    "Dfa": "hot-summer continental", "Dfb": "warm-summer continental",
    "Dfc": "subarctic", "Dfd": "extreme subarctic",
    "ET": "tundra", "EF": "ice cap",
}


def describe(code: str) -> str:
    return KOPPEN_NAMES.get(code, code)
