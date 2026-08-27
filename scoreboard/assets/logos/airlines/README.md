# Airline logos

Logos are downloaded automatically (see `extras/flights/logos.py`) and cached under
`$SCOREBOARD_CACHE_DIR/airline-logos/` (default `~/.scoreboard/cache`). Drop a PNG here, named by ICAO or IATA operator
code, to override a fetched logo or to add one the sets don't carry:

```
UAL.png   # United (ICAO — checked first)
UA.png    # United (IATA — used if UAL.png is absent)
ACA.png   # Air Canada
```

~40x40 px RGBA with a transparent background; larger art is scaled down, but small
LED-friendly source art looks best. Aircraft with no logo get a monogram tile instead,
so this directory is entirely optional.
