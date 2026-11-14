from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "terrasentinel.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)

EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events?days=30&status=open"
USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"
CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=resource&FORMAT=json"
CELESTRAK_TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=resource&FORMAT=tle"

HTTP_TIMEOUT = 15.0

TRACKED_SATELLITE_PATTERNS = ("LANDSAT", "TERRA", "AQUA", "SENTINEL")
