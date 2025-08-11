# geocode_tool.py
"""
Reverse geocoding Việt Nam: OSM (Nominatim) + ranh giới xã/phường (CSV).

- Khởi tạo với đường dẫn local hoặc URL raw tới CSV ranh giới.
- Với (lat, lon): tìm xã/phường bằng hộp bao trong CSV, gọi Nominatim lấy
  house_number/road, và trả về dict:
  {
    house_number, road, ward, province, latitude, longitude, formatted
  }
"""

from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass
from typing import Optional, List, Tuple, Dict

import requests


# ---------- Data model ----------
@dataclass
class AdminUnit:
    province: str
    ward: str
    ward_bounds: Tuple[float, float, float, float]  # (min_lat, min_lon, max_lat, max_lon)


# ---------- Helpers ----------
def _norm(s: str) -> str:
    """Chuẩn hóa tên cột: lower, thay non-alnum bằng '_'."""
    return re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")


def _first_col(header_map: Dict[str, int], candidates: List[str]) -> Optional[int]:
    """Tìm cột đầu tiên khớp trong danh sách ứng viên (đã normalize)."""
    for c in candidates:
        if c in header_map:
            return header_map[c]
    return None


def _parse_bounds(bounds_str: str) -> Tuple[float, float, float, float]:
    """
    Parse 'min_lat,min_lon – max_lat,max_lon' (chấp nhận '-', '–', '—').
    Tự sửa nếu min/max bị đảo.
    """
    s = str(bounds_str or "")
    parts = re.split(r"\s*[–—\-]\s*", s)
    if len(parts) != 2:
        raise ValueError(f"Invalid bounds string: {s}")
    def to_pair(t: str) -> Tuple[float, float]:
        a = [p.strip() for p in (t or "").split(",")]
        if len(a) != 2:
            raise ValueError(f"Invalid coord pair: {t}")
        return float(a[0]), float(a[1])
    (min_lat, min_lon) = to_pair(parts[0])
    (max_lat, max_lon) = to_pair(parts[1])
    if min_lat > max_lat:
        min_lat, max_lat = max_lat, min_lat
    if min_lon > max_lon:
        min_lon, max_lon = max_lon, min_lon
    return (min_lat, min_lon, max_lat, max_lon)


def _read_csv_anywhere(path_or_url: str) -> List[List[str]]:
    """Đọc CSV từ local hoặc URL; trả về list các dòng (list[str])."""
    if re.match(r"^https?://", path_or_url, re.I):
        ua_email = os.getenv("NOMINATIM_EMAIL") or "contact@example.com"
        headers = {"User-Agent": f"VietnamGeocoder/1.0 ({ua_email})"}
        r = requests.get(path_or_url, headers=headers, timeout=30)
        r.raise_for_status()
        text = r.text
        f = io.StringIO(text)
        reader = csv.reader(f)
        return [row for row in reader]
    else:
        with open(path_or_url, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            return [row for row in reader]


# ---------- Main class ----------
class Geocoder:
    """
    Geocoder kết hợp:
      - Tra ranh xã/phường bằng hộp bao từ CSV
      - Nominatim reverse để lấy số nhà/đường
    """

    def __init__(self, csv_path_or_url: str):
        if not re.match(r"^https?://", csv_path_or_url, re.I) and not os.path.exists(csv_path_or_url):
            raise FileNotFoundError(f"Boundary CSV not found: {csv_path_or_url}")
        self.wards: List[AdminUnit] = []
        self._load_wards(csv_path_or_url)

    # ---- Load wards ----
    def _load_wards(self, path_or_url: str) -> None:
        rows = _read_csv_anywhere(path_or_url)
        if not rows:
            raise RuntimeError("CSV empty or unreadable")

        header_raw = rows[0]
        header_norm = [_norm(h) for h in header_raw]
        header_map = {h: i for i, h in enumerate(header_norm)}

        # dò các cột cần thiết (linh hoạt tên cột)
        province_idx = _first_col(header_map, [
            "province", "newprovince", "new_province", "province_name", "short_province"
        ])
        ward_idx = _first_col(header_map, [
            "ward", "newward", "new_ward", "ward_name", "short_ward"
        ])
        bounds_idx = _first_col(header_map, [
            "wardbounds", "bounds", "ward_bounds"
        ])

        if province_idx is None or ward_idx is None or bounds_idx is None:
            raise RuntimeError(
                "CSV thiếu cột. Cần tối thiểu: province*, ward*, wardBounds*. "
                f"Header chuẩn hóa: {header_norm}"
            )

        for row in rows[1:]:
            try:
                province = (row[province_idx] or "").strip()
                ward = (row[ward_idx] or "").strip()
                bounds_str = (row[bounds_idx] or "").strip()
                if not province or not ward or not bounds_str:
                    continue
                bounds = _parse_bounds(bounds_str)
                self.wards.append(AdminUnit(province=province, ward=ward, ward_bounds=bounds))
            except Exception:
                # bỏ qua dòng lỗi
                continue

    # ---- Admin lookup ----
    def _lookup_admin_unit(self, lat: float, lon: float) -> Optional[AdminUnit]:
        candidates: List[Tuple[AdminUnit, float]] = []
        for u in self.wards:
            min_lat, min_lon, max_lat, max_lon = u.ward_bounds
            if (min_lat <= lat <= max_lat) and (min_lon <= lon <= max_lon):
                area = max(1e-12, (max_lat - min_lat) * (max_lon - min_lon))
                candidates.append((u, area))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1])  # chọn hộp bao nhỏ nhất
        return candidates[0][0]

    # ---- Nominatim ----
    def _reverse_geocode_osm(self, lat: float, lon: float) -> Tuple[Optional[str], Optional[str]]:
        params = {
            "format": "jsonv2",
            "lat": lat,
            "lon": lon,
            "addressdetails": 1,
            "accept-language": os.getenv("NOMINATIM_LANG", "vi"),
        }
        ua_email = os.getenv("NOMINATIM_EMAIL") or "contact@example.com"
        headers = {"User-Agent": f"VietnamGeocoder/1.0 ({ua_email})"}
        try:
            r = requests.get(
                "https://nominatim.openstreetmap.org/reverse",
                params=params, headers=headers, timeout=15
            )
            if r.status_code != 200:
                return None, None
            data = r.json()
            addr = data.get("address", {}) if isinstance(data, dict) else {}
            return addr.get("house_number"), addr.get("road")
        except Exception:
            return None, None

    # ---- Public API ----
    def geocode(self, lat: float, lon: float) -> Optional[Dict[str, Optional[str]]]:
        """
        Trả về dict:
        {
          house_number, road, ward, province, latitude, longitude, formatted
        }
        hoặc None nếu không tìm thấy xã/phường.
        """
        unit = self._lookup_admin_unit(lat, lon)
        if not unit:
            return None

        house_number, road = self._reverse_geocode_osm(lat, lon)

        parts: List[str] = []
        if house_number and road:
            parts.append(f"{house_number} {road}")
        elif road:
            parts.append(road)
        parts.append(unit.ward)
        parts.append(unit.province)

        return {
            "house_number": house_number or "",
            "road": road or "",
            "ward": unit.ward,
            "province": unit.province,
            "latitude": float(lat),
            "longitude": float(lon),
            "formatted": ", ".join([p for p in parts if p]),
        }
