"""Ground truth for climate validation.

Forty-eight real places, chosen to cover every Koppen group and every awkward
case the model has to get right: coastal deserts driven by cold currents
(Lima, Walvis Bay), rain shadows (Las Vegas), monsoons (Mumbai, Kolkata),
Mediterranean summer drought (Athens, Perth), extreme continentality
(Yakutsk), maritime damping (Reykjavik, Valentia) and tropical highland
(Quito, Addis Ababa).

Values are long-term normals rounded from public climate records. They are
reference data for a test, not part of the simulation -- the model never reads
this file.

Fields: name, lon, lat, elevation m, Koppen code, mean annual temp C,
annual precipitation mm.
"""

CITIES = [
    # name                 lon      lat    elev  koppen   Tmean  Pann
    ("Singapore",         103.82,   1.35,    15, "Af",    27.5, 2340),
    ("Manaus",            -60.02,  -3.10,    92, "Af",    27.0, 2300),
    ("Kinshasa",           15.31,  -4.32,   240, "Aw",    25.3, 1500),
    ("Mumbai",             72.83,  19.08,    14, "Am",    27.0, 2200),
    ("Kolkata",            88.36,  22.57,     9, "Aw",    26.8, 1580),
    ("Lagos",               3.38,   6.52,    41, "Aw",    26.9, 1500),
    ("Darwin",            130.84, -12.46,    30, "Aw",    27.7, 1730),
    ("Bangkok",           100.50,  13.76,     2, "Aw",    28.4, 1500),
    ("Cairo",              31.24,  30.04,    23, "BWh",   21.9,   25),
    ("Phoenix",          -112.07,  33.45,   331, "BWh",   23.9,  200),
    ("Riyadh",             46.72,  24.69,   612, "BWh",   26.0,  110),
    ("Lima",              -77.03, -12.05,   154, "BWh",   19.0,   13),
    ("Walvis Bay",         14.51, -22.96,     7, "BWh",   17.5,   15),
    ("Alice Springs",     133.88, -23.70,   545, "BWh",   21.0,  280),
    ("Las Vegas",        -115.14,  36.17,   610, "BWh",   20.3,  110),
    ("Ulaanbaatar",       106.92,  47.89,  1350, "BWk",   -0.4,  267),
    ("Tashkent",           69.24,  41.30,   455, "BSk",   14.6,  440),
    ("Denver",           -104.99,  39.74,  1609, "BSk",   10.4,  390),
    ("Nairobi",            36.82,  -1.29,  1795, "BSh",   19.0,  870),
    ("Athens",             23.73,  37.98,    70, "Csa",   18.8,  400),
    ("Los Angeles",      -118.24,  34.05,    71, "Csb",   18.6,  380),
    ("Perth",             115.86, -31.95,    15, "Csa",   18.6,  730),
    ("Santiago",          -70.65, -33.45,   520, "Csb",   14.5,  310),
    ("Lisbon",             -9.14,  38.72,    56, "Csa",   17.4,  730),
    ("San Francisco",    -122.42,  37.77,    16, "Csb",   14.1,  600),
    ("Tokyo",             139.69,  35.69,    40, "Cfa",   16.0, 1530),
    ("Buenos Aires",      -58.38, -34.60,    25, "Cfa",   17.9, 1250),
    ("Atlanta",           -84.39,  33.75,   320, "Cfa",   17.0, 1290),
    ("Sydney",            151.21, -33.87,    20, "Cfa",   18.3, 1210),
    ("Shanghai",          121.47,  31.23,     4, "Cfa",   16.6, 1170),
    ("London",             -0.13,  51.51,    25, "Cfb",   11.6,  620),
    ("Paris",               2.35,  48.86,    35, "Cfb",   11.7,  640),
    ("Amsterdam",           4.90,  52.37,     0, "Cfb",   10.5,  840),
    ("Auckland",          174.76, -36.85,    26, "Cfb",   15.2, 1210),
    ("Valentia",          -10.25,  51.93,     9, "Cfb",   10.9, 1430),
    ("Reykjavik",         -21.94,  64.13,    30, "Cfc",    5.0,  800),
    ("Chicago",           -87.63,  41.88,   180, "Dfa",   10.3,  940),
    ("Toronto",           -79.38,  43.65,    76, "Dfb",    9.4,  830),
    ("Moscow",             37.62,  55.75,   156, "Dfb",    5.8,  700),
    ("Warsaw",             21.01,  52.23,   100, "Dfb",    8.5,  530),
    ("Stockholm",          18.07,  59.33,    28, "Dfb",    7.4,  540),
    ("Anchorage",        -149.90,  61.22,    31, "Dfc",    2.8,  410),
    ("Yakutsk",           129.73,  62.03,   100, "Dfd",  -10.0,  240),
    ("Harbin",            126.53,  45.80,   150, "Dwa",    4.9,  520),
    ("Beijing",           116.41,  39.90,    44, "Dwa",   12.9,  570),
    ("Barrow",           -156.79,  71.29,     3, "ET",   -11.0,  110),
    ("Vostok",            106.80, -78.46,  3488, "EF",   -55.0,   20),
    ("Quito",             -78.47,  -0.18,  2850, "Cfb",   13.5, 1100),
]
