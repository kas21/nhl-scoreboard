# Weather fixtures

- `open_meteo.json` — Open-Meteo `v1/forecast` capture (current + daily).
- `nws_alerts.json` — `api.weather.gov/alerts/active` capture from 2026-09-08, trimmed to six
  features of different kinds (warning, watch, advisory, statement, and a marine advisory the
  default ignore list drops). `affectedZones`/`geocode` removed and geometry nulled to keep it small;
  the properties are untouched. The title pretends it was a point query.
- `eccc_alerts.json` — Environment Canada GeoMet `collections/weather-alerts/items` capture from the
  same day: a continued air quality warning, an issued frost advisory and an ended rainfall warning
  (which must be dropped). Geometry nulled.
