import json
import sys
from functools import lru_cache
from pathlib import Path

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
SPECIAL_ZONE = {'huyenbachlongvi', 'huyenconco', 'huyenhoangsa', 'huyenlyson', 'huyencondao'}

OLD_PROVINCE_KEY_TO_NEW_PROVINCE_KEY = {}
for new_province_key, old_province_keys in DICT_PROVINCE.items():
    for old_key in old_province_keys:
        OLD_PROVINCE_KEY_TO_NEW_PROVINCE_KEY.setdefault(old_key, new_province_key)

NO_DIVIDED_REVERSE_MAP = {}
for new_province_key, ward_map in DICT_PROVINCE_WARD_NO_DIVIDED.items():
    reverse_map = {}
    for new_ward_key, old_keys in ward_map.items():
        for old_key in old_keys:
            reverse_map.setdefault(old_key, new_ward_key)
    NO_DIVIDED_REVERSE_MAP[new_province_key] = reverse_map

DIVIDED_REVERSE_MAP = {
    new_province_key: dict(ward_map)
    for new_province_key, ward_map in DICT_PROVINCE_WARD_DIVIDED.items()
}


@lru_cache(maxsize=50000)
def _get_geo_location_cached(address: str):
    return get_geo_location(address)


def _resolve_new_ward_key(old_unit, new_province_key: str):
    if not new_province_key:
        return None, False

    if not old_unit.ward_key and old_unit.district_key not in SPECIAL_ZONE:
        return None, False

    old_key = f"{old_unit.province_key}_{old_unit.district_key}_{old_unit.ward_key if old_unit.ward_key else ''}"

    no_divided_map = NO_DIVIDED_REVERSE_MAP.get(new_province_key, {})
    new_ward_key = no_divided_map.get(old_key)
    if new_ward_key:
        return new_ward_key, False

    new_wards = DIVIDED_REVERSE_MAP.get(new_province_key, {}).get(old_key, [])
    if not new_wards:
        return None, False

    if not old_unit.street:
        return next((ward['newWardKey'] for ward in new_wards if ward['isDefaultNewWard']), None), False

    old_location = _get_geo_location_cached(old_unit.get_address())
    old_point = (old_location.latitude, old_location.longitude)

    containing_points = []
    new_ward_points = []
    for ward in new_wards:
        new_point = (ward['newWardLat'], ward['newWardLon'])
        new_ward_points.append(new_point)
        is_contain = check_point_in_polygon(
            point=old_point,
            polygon_center=new_point,
            polygon_area_km2=ward['newWardAreaKm2'],
        )
        if is_contain:
            containing_points.append(new_point)

    nearest_point = find_nearest_point(a_point=old_point, list_of_b_points=new_ward_points)
    if len(containing_points) == 1:
        default_ward_point = containing_points[0]
    else:
        default_ward_point = nearest_point

    chosen = next(
        (
            ward['newWardKey']
            for ward in new_wards
            if (ward['newWardLat'], ward['newWardLon']) == default_ward_point
        ),
        None,
    )
    return chosen, True


def convert_address_2025(address: str):
    old_unit = parse_address(address, mode=ParseMode.LEGACY, keep_street=True, level=3)
    new_province_key = OLD_PROVINCE_KEY_TO_NEW_PROVINCE_KEY.get(old_unit.province_key)
    new_ward_key, used_geocoder = _resolve_new_ward_key(old_unit, new_province_key)

    new_address_components = [part for part in (old_unit.street, new_ward_key, new_province_key) if part]
    new_address = ','.join(new_address_components)

    level = 2 if new_ward_key else 1
    new_unit = parse_address(new_address, mode=ParseMode.FROM_2025, keep_street=True, level=level)
    setattr(new_unit, '_used_geocoder', used_geocoder)
    return new_unit


if __name__ == '__main__':
    print(convert_address_2025(''))
