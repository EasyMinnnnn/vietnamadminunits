"""
Geocode tool combining OpenStreetMap reverse‑geocoding with
Vietnamese administrative boundaries.

This module demonstrates how to convert a pair of geographic
coordinates (latitude, longitude) into a human‑readable address of
the form “<house number> <street>, <commune/ward>, <province>”.  It
uses the following data sources:

* **OpenStreetMap Nominatim API** – the reverse API translates a
  coordinate into address components.  According to the official
  documentation, the reverse API finds the nearest suitable OSM
  object and returns its address details【577374665323068†L87-L103】.  The API
  is queried via HTTP with parameters such as `lat`, `lon` and
  `format=jsonv2`.  When `addressdetails=1`, the response includes
  structured address elements like house number and road【577374665323068†L140-L151】.

* **Vietnamese administrative units** – a CSV dataset (for example
  `legacy_63-province-10040-ward_with_location.csv` from the
  ``vietnamadminunits`` project) containing latitude/longitude bounds
  for every ward/commune.  Each row has fields such as
  `wardBounds` and `provinceBounds` which store a south‑west and
  north‑east coordinate pair as a string (e.g., "20.995099,105.797482 – 21.05038,105.876446")【581511831359102†L4-L11】.  By
  comparing the input coordinate with these bounds we can determine
  which ward/province contains it.

To use this module you will need to download the boundary CSV file
separately (for example from the ``vietnamadminunits`` GitHub
repository) and point ``WARD_CSV_PATH`` to its location.  You also
need an active internet connection to query Nominatim.

Example:

```
from geocode_tool import Geocoder

# Create the geocoder (first call may take a moment to load the CSV)
geocoder = Geocoder('path/to/legacy_63-province-10040-ward_with_location.csv')

# Geocode a point in Hanoi
address = geocoder.geocode(21.0468, 105.8481)
print(address)
# → '135 Pilkington Avenue, Phúc Xá, Hà Nội' (example)
```

Note: Nominatim has usage limits and a fair‑use policy.  Always set
a valid ``User‑Agent`` header and refrain from sending large volumes
of requests.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from typing import Optional, List, Tuple

import requests


@dataclass
class AdminUnit:
    """Represents a Vietnamese ward/commune with its bounding box."""
    province: str
    district: Optional[str]
    ward: str
    province_short: Optional[str]
    ward_type: Optional[str]
    ward_bounds: Tuple[float, float, float, float]  # (min_lat, min_lon, max_lat, max_lon)


class Geocoder:
    """Geocoder combining OSM reverse geocoding with ward/province lookup."""

    def __init__(self, ward_csv_path: str):
        if not os.path.exists(ward_csv_path):
            raise FileNotFoundError(f"Boundary CSV not found: {ward_csv_path}")
        self.wards: List[AdminUnit] = []
        self._load_wards(ward_csv_path)

    def _parse_bounds(self, bounds_str: str) -> Tuple[float, float, float, float]:
        """Parse a bounds string of the form 'min_lat,min_lon – max_lat,max_lon'."""
        if '–' in bounds_str:
            # long dash used in the dataset
            parts = bounds_str.split('–')
        elif '-' in bounds_str:
            parts = bounds_str.split('-')
        else:
            raise ValueError(f"Invalid bounds string: {bounds_str}")
        sw = parts[0].strip().split(',')
        ne = parts[1].strip().split(',')
        min_lat, min_lon = float(sw[0]), float(sw[1])
        max_lat, max_lon = float(ne[0]), float(ne[1])
        return (min_lat, min_lon, max_lat, max_lon)

    def _load_wards(self, csv_path: str) -> None:
        """Load wards and their bounds from a CSV file."""
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            # Read header to find column indices
            header = next(reader)
            # Normalise header names by stripping whitespace
            header = [h.strip() for h in header]
            # Find relevant columns – fallback to known names
            try:
                province_idx = header.index('province')
                district_idx = header.index('district') if 'district' in header else None
                ward_idx = header.index('ward')
                province_short_idx = header.index('provinceShort') if 'provinceShort' in header else None
                ward_type_idx = header.index('wardType') if 'wardType' in header else None
                bounds_idx = header.index('wardBounds')
            except ValueError as exc:
                raise RuntimeError("CSV does not contain expected columns: " + str(exc))

            for row in reader:
                try:
                    bounds_str = row[bounds_idx].strip()
                    bounds = self._parse_bounds(bounds_str)
                    province = row[province_idx].strip()
                    district = row[district_idx].strip() if district_idx is not None else None
                    ward = row[ward_idx].strip()
                    province_short = row[province_short_idx].strip() if province_short_idx is not None else None
                    ward_type = row[ward_type_idx].strip() if ward_type_idx is not None else None
                    self.wards.append(AdminUnit(
                        province=province,
                        district=district,
                        ward=ward,
                        province_short=province_short,
                        ward_type=ward_type,
                        ward_bounds=bounds,
                    ))
                except Exception:
                    # Skip malformed lines silently
                    continue

    def _lookup_admin_unit(self, lat: float, lon: float) -> Optional[AdminUnit]:
        """Find the ward whose bounding box contains the point (lat, lon)."""
        matches: List[Tuple[AdminUnit, float]] = []
        for unit in self.wards:
            min_lat, min_lon, max_lat, max_lon = unit.ward_bounds
            if min_lat <= lat <= max_lat and min_lon <= lon <= max_lon:
                # compute area for tie‑breaking (smaller area preferred)
                area = (max_lat - min_lat) * (max_lon - min_lon)
                matches.append((unit, area))
        if not matches:
            return None
        # Return the smallest bounding box match
        matches.sort(key=lambda x: x[1])
        return matches[0][0]

    def _reverse_geocode(self, lat: float, lon: float) -> Tuple[Optional[str], Optional[str]]:
        """Call Nominatim reverse API and extract house number and street.

        Returns a tuple (house_number, road) where each component may be None
        if not provided by OSM.  See Nominatim docs for details【577374665323068†L274-L317】.
        """
        params = {
            'format': 'jsonv2',
            'lat': lat,
            'lon': lon,
            'addressdetails': 1,
        }
        headers = {
            'User-Agent': 'VietnamGeocoder/1.0 (contact: example@example.com)'
        }
        try:
            resp = requests.get('https://nominatim.openstreetmap.org/reverse', params=params, headers=headers, timeout=10)
            if resp.status_code != 200:
                return None, None
            data = resp.json()
            address = data.get('address', {})
            house_number = address.get('house_number')
            road = address.get('road')
            return house_number, road
        except Exception:
            return None, None

    def geocode(self, lat: float, lon: float) -> Optional[str]:
        """Return a human‑readable address string for the given coordinate.

        The returned string is assembled as `<house number> <road>, <ward>, <province>`.
        If the house number or road are missing, they will be omitted accordingly.
        If no matching ward/province can be found, returns None.
        """
        unit = self._lookup_admin_unit(lat, lon)
        if not unit:
            return None
        house_number, road = self._reverse_geocode(lat, lon)
        parts: List[str] = []
        if house_number and road:
            parts.append(f"{house_number} {road}")
        elif road:
            parts.append(road)
        # Append ward and province.  We prefer the short form if available.
        parts.append(unit.ward)
        parts.append(unit.province)
        return ', '.join(parts)
