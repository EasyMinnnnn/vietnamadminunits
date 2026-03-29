import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence, Tuple

MODULE_DIR = Path(__file__).parent.parent

if __name__ == '__main__':
    sys.path.append(MODULE_DIR.as_posix())
    from parser import parse_address, ParseMode
    from parser.utils import get_geo_location, check_point_in_polygon, find_nearest_point
else:
    from ..parser import parse_address, ParseMode
    from ..parser.utils import get_geo_location, check_point_in_polygon, find_nearest_point


with open(MODULE_DIR / 'data/converter_2025.json', 'r', encoding='utf-8') as f:
    converter_data = json.load(f)


DICT_PROVINCE = converter_data['DICT_PROVINCE']
DICT_PROVINCE_WARD_NO_DIVIDED = converter_data['DICT_PROVINCE_WARD_NO_DIVIDED']
DICT_PROVINCE_WARD_DIVIDED = converter_data['DICT_PROVINCE_WARD_DIVIDED']

SPECIAL_ZONE_KEYS = {'huyenbachlongvi', 'huyenconco', 'huyenhoangsa', 'huyenlyson', 'huyencondao'}
ENABLE_STREET_GEOLOCATION = os.getenv('VIETNAMADMINUNITS_ENABLE_STREET_GEOLOCATION', '0').strip().lower() in {
    '1', 'true', 'yes', 'on'
}
GEOLOCATION_CACHE_SIZE = int(os.getenv('VIETNAMADMINUNITS_GEOLOCATION_CACHE_SIZE', '50000'))
CONVERT_CACHE_SIZE = int(os.getenv('VIETNAMADMINUNITS_CONVERT_CACHE_SIZE', '200000'))


@lru_cache(maxsize=GEOLOCATION_CACHE_SIZE)
def _cached_geo_point(address: str) -> Optional[Tuple[float, float]]:
    try:
        location = get_geo_location(address)
    except Exception:
        return None

    if not location:
        return None

    lat = getattr(location, 'latitude', None)
    lon = getattr(location, 'longitude', None)
    if lat is None or lon is None:
        return None

    try:
        return float(lat), float(lon)
    except Exception:
        return None


def _pick_default_new_ward(new_wards: Sequence[dict]) -> Optional[str]:
    return next((ward['newWardKey'] for ward in new_wards if ward.get('isDefaultNewWard')), None)


def _get_reference_point(old_unit) -> Optional[Tuple[float, float]]:
    lat = getattr(old_unit, 'latitude', None)
    lon = getattr(old_unit, 'longitude', None)
    if lat is None or lon is None:
        return None

    try:
        return float(lat), float(lon)
    except Exception:
        return None


def _select_new_ward_from_point(reference_point: Tuple[float, float], new_wards: Sequence[dict]) -> Optional[str]:
    if not new_wards:
        return None

    containing_points = []
    new_ward_points = []

    for ward in new_wards:
        new_point = (ward['newWardLat'], ward['newWardLon'])
        new_ward_points.append(new_point)
        try:
            is_contain = check_point_in_polygon(
                point=reference_point,
                polygon_center=new_point,
                polygon_area_km2=ward['newWardAreaKm2'],
            )
        except Exception:
            is_contain = False

        if is_contain:
            containing_points.append(new_point)

    try:
        nearest_point = find_nearest_point(a_point=reference_point, list_of_b_points=new_ward_points)
    except Exception:
        nearest_point = None

    if len(containing_points) == 1:
        target_point = containing_points[0]
    else:
        target_point = nearest_point

    if target_point is None:
        return None

    return next(
        (
            ward['newWardKey']
            for ward in new_wards
            if (ward['newWardLat'], ward['newWardLon']) == target_point
        ),
        None,
    )


def _resolve_divided_ward(old_unit, new_wards: Sequence[dict]) -> Optional[str]:
    if not new_wards:
        return None

    default_new_ward = _pick_default_new_ward(new_wards)

    reference_point = None
    if ENABLE_STREET_GEOLOCATION and getattr(old_unit, 'street', None):
        reference_point = _cached_geo_point(old_unit.get_address())

    if reference_point is None:
        reference_point = _get_reference_point(old_unit)

    if reference_point is None:
        return default_new_ward

    selected_new_ward = _select_new_ward_from_point(reference_point, new_wards)
    return selected_new_ward or default_new_ward


@lru_cache(maxsize=CONVERT_CACHE_SIZE)
def convert_address_2025(address: str):
    address = (address or '').strip()
    if not address:
        raise ValueError('Address is empty')

    new_ward_key = None

    old_unit = parse_address(address, mode=ParseMode.LEGACY, keep_street=True, level=3)

    new_province_key = next(
        (k for k, v in DICT_PROVINCE.items() if old_unit.province_key and old_unit.province_key in v),
        None,
    )

    if old_unit.ward_key or old_unit.district_key in SPECIAL_ZONE_KEYS:
        old_province_district_ward_key = (
            f"{old_unit.province_key}_{old_unit.district_key}_{old_unit.ward_key if old_unit.ward_key else ''}"
        )

        dict_ward_no_divided = DICT_PROVINCE_WARD_NO_DIVIDED.get(new_province_key, {})
        new_ward_key = next(
            (
                k
                for k, v in dict_ward_no_divided.items()
                if old_province_district_ward_key and old_province_district_ward_key in v
            ),
            None,
        )

        if not new_ward_key:
            new_wards = DICT_PROVINCE_WARD_DIVIDED.get(new_province_key, {}).get(old_province_district_ward_key, [])

            if not getattr(old_unit, 'street', None):
                new_ward_key = _pick_default_new_ward(new_wards) or _resolve_divided_ward(old_unit, new_wards)
            else:
                new_ward_key = _resolve_divided_ward(old_unit, new_wards)

    new_address_components = [value for value in (old_unit.street, new_ward_key, new_province_key) if value]
    new_address = ','.join(new_address_components)

    level = 2 if new_ward_key else 1
    new_unit = parse_address(new_address, mode=ParseMode.FROM_2025, keep_street=True, level=level)
    return new_unit


if __name__ == '__main__':
    print(convert_address_2025('194 Trần Quang Khải, phường Lý Thái Tổ, quận Hoàn Kiếm, Hà Nội'))
