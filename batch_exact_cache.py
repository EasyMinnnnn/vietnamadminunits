from __future__ import annotations

import math
import os
import re
import shutil
import sqlite3
import time
import unicodedata
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

DEFAULT_ERROR_VALUE = "Lỗi định dạng"
DEFAULT_CACHE_DIR = Path("/tmp/vietnamadminunits_cache")
DEFAULT_CACHE_DB = DEFAULT_CACHE_DIR / "address_conversion_cache.sqlite3"

ProgressCallback = Callable[[Dict[str, float]], None]


def normalize_address_value(value: object) -> str:
    """Chuẩn hóa text đầu vào nhưng vẫn giữ dấu tiếng Việt."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""

    text = str(value)
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = text.replace("，", ",").replace("；", ";")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _resolve_db_path(db_path: Optional[str] = None) -> Path:
    env_path = os.getenv("ADDRESS_CACHE_DB")
    path = Path(db_path or env_path or DEFAULT_CACHE_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _backup_corrupt_db(path: Path) -> None:
    if not path.exists():
        return

    ts = int(time.time())
    backup_path = path.with_suffix(path.suffix + f".corrupt.{ts}.bak")

    try:
        shutil.move(str(path), str(backup_path))
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass

    for suffix in ("-wal", "-shm"):
        extra = Path(str(path) + suffix)
        try:
            extra.unlink(missing_ok=True)
        except Exception:
            pass


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000;")
    conn.execute("PRAGMA journal_mode=DELETE;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _ensure_column(conn: sqlite3.Connection, table_name: str, column_name: str, ddl: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in cols:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def ensure_cache_schema(db_path: Optional[str] = None) -> Path:
    path = _resolve_db_path(db_path)

    for attempt in range(2):
        try:
            with _connect(path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS address_conversion_cache (
                        normalized_address TEXT PRIMARY KEY,
                        converted_address TEXT,
                        status TEXT NOT NULL,
                        error_message TEXT,
                        used_geocoder INTEGER NOT NULL DEFAULT 0,
                        method TEXT,
                        matched_candidate TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )

                _ensure_column(conn, "address_conversion_cache", "method", "method TEXT")
                _ensure_column(conn, "address_conversion_cache", "matched_candidate", "matched_candidate TEXT")

                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS geocode_cache (
                        query_address TEXT PRIMARY KEY,
                        latitude REAL,
                        longitude REAL,
                        provider TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_address_conversion_status "
                    "ON address_conversion_cache(status)"
                )
                conn.commit()
                return path
        except sqlite3.DatabaseError as exc:
            if attempt == 0 and "malformed" in str(exc).lower():
                _backup_corrupt_db(path)
                continue
            raise

    return path


def _chunked(items: Sequence[str], size: int = 900) -> Iterable[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def load_cached_results(
    addresses: Sequence[str],
    db_path: Optional[str] = None,
    *,
    include_invalid: bool = False,
) -> Dict[str, Dict[str, object]]:
    """
    Đọc cache.

    Mặc định KHÔNG lấy cache lỗi invalid_format.
    Lý do: sau khi sửa rule, các địa chỉ từng báo "Lỗi định dạng" phải được chạy lại.
    """
    if not addresses:
        return {}

    path = ensure_cache_schema(db_path)
    results: Dict[str, Dict[str, object]] = {}

    for _attempt in range(2):
        try:
            with _connect(path) as conn:
                conn.row_factory = sqlite3.Row

                for chunk in _chunked(list(addresses), size=900):
                    placeholders = ",".join(["?"] * len(chunk))
                    rows = conn.execute(
                        f"""
                        SELECT
                            normalized_address,
                            converted_address,
                            status,
                            error_message,
                            used_geocoder,
                            method,
                            matched_candidate
                        FROM address_conversion_cache
                        WHERE normalized_address IN ({placeholders})
                        """,
                        list(chunk),
                    ).fetchall()

                    for row in rows:
                        status = row["status"]
                        if not include_invalid and status != "computed":
                            continue

                        results[row["normalized_address"]] = {
                            "converted_address": row["converted_address"],
                            "status": status,
                            "error_message": row["error_message"],
                            "used_geocoder": int(row["used_geocoder"] or 0),
                            "method": row["method"],
                            "matched_candidate": row["matched_candidate"],
                            "cache_source": "sqlite_cache",
                        }

                return results

        except sqlite3.DatabaseError as exc:
            if "malformed" in str(exc).lower():
                _backup_corrupt_db(path)
                try:
                    path = ensure_cache_schema(str(path))
                except Exception:
                    pass
                results = {}
                continue
            return {}

    return {}


def upsert_cached_results(records: Sequence[Dict[str, object]], db_path: Optional[str] = None) -> None:
    if not records:
        return

    path = ensure_cache_schema(db_path)
    now = time.time()

    rows = [
        (
            str(r["normalized_address"]),
            r.get("converted_address"),
            str(r.get("status") or "computed"),
            r.get("error_message"),
            int(r.get("used_geocoder") or 0),
            r.get("method"),
            r.get("matched_candidate"),
            now,
            now,
        )
        for r in records
    ]

    for _attempt in range(2):
        try:
            with _connect(path) as conn:
                conn.executemany(
                    """
                    INSERT INTO address_conversion_cache (
                        normalized_address,
                        converted_address,
                        status,
                        error_message,
                        used_geocoder,
                        method,
                        matched_candidate,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(normalized_address) DO UPDATE SET
                        converted_address=excluded.converted_address,
                        status=excluded.status,
                        error_message=excluded.error_message,
                        used_geocoder=excluded.used_geocoder,
                        method=excluded.method,
                        matched_candidate=excluded.matched_candidate,
                        updated_at=excluded.updated_at
                    """,
                    rows,
                )
                conn.commit()
                return

        except sqlite3.DatabaseError as exc:
            if "malformed" in str(exc).lower():
                _backup_corrupt_db(path)
                try:
                    path = ensure_cache_schema(str(path))
                except Exception:
                    pass
                continue
            return


def _clean_for_parser(text: str) -> str:
    text = normalize_address_value(text)
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"(?:,\s*){2,}", ", ", text)
    text = re.sub(r"\s+", " ", text).strip(" ,;")
    return text


def _split_address_parts(text: str) -> List[str]:
    return [p.strip(" ,;") for p in _clean_for_parser(text).split(",") if p.strip(" ,;")]


def _legacy_four_part_candidate(text: str) -> Optional[str]:
    """
    convert_address() xử lý tốt nhất dạng:
        street, ward, district, province

    Nếu phần mô tả tài sản/street có nhiều dấu phẩy:
        Thửa đất số 482A, tờ bản đồ số 03, tổ dân phố..., Dương Nội, Hà Đông, Hà Nội

    thì gom mọi phần trước 3 cấp hành chính cuối vào street:
        Thửa đất số 482A tờ bản đồ số 03 tổ dân phố..., Dương Nội, Hà Đông, Hà Nội
    """
    parts = _split_address_parts(text)
    if len(parts) < 4:
        return None

    province = parts[-1]
    district = parts[-2]
    ward = parts[-3]
    street = " ".join(parts[:-3]).strip()

    if not street:
        return f"{ward}, {district}, {province}"

    return f"{street}, {ward}, {district}, {province}"


def _remove_placeholder_words(text: str) -> str:
    """
    Dữ liệu nguồn có nhiều giá trị đệm như 'Khác'/'Không' đặt trước phường/xã.
    Chỉ bỏ khi nó là token đứng trước dấu phẩy hoặc sát trước đơn vị hành chính.
    """
    text = _clean_for_parser(text)

    # "Tổ dân phố 10 Khác, Đồng Mai..." -> "Tổ dân phố 10, Đồng Mai..."
    text = re.sub(r"\b(Khác|Không)\s*,", ",", text, flags=re.IGNORECASE)

    # "Lô đất số 14 Không, Phùng Xá..." -> "Lô đất số 14, Phùng Xá..."
    text = re.sub(r"\s+\b(Khác|Không)\b(?=\s*,)", "", text, flags=re.IGNORECASE)

    return _clean_for_parser(text)


def _address_variants(text: str) -> List[Tuple[str, str]]:
    variants: List[Tuple[str, str]] = []
    seen = set()

    def add(method: str, candidate: Optional[str]) -> None:
        if not candidate:
            return

        candidate = _clean_for_parser(candidate)
        key = candidate.casefold()

        if candidate and key not in seen:
            seen.add(key)
            variants.append((method, candidate))

    cleaned = _clean_for_parser(text)
    no_placeholder = _remove_placeholder_words(cleaned)

    add("raw", text)
    add("clean_commas", cleaned)
    add("merge_extra_street_commas", _legacy_four_part_candidate(cleaned))
    add("remove_placeholder", no_placeholder)
    add("remove_placeholder_and_merge", _legacy_four_part_candidate(no_placeholder))

    return variants


def _get_address_safely(obj: object, *, short_name: bool) -> str:
    get_address = getattr(obj, "get_address", None)

    if callable(get_address):
        try:
            return str(get_address(short_name=short_name))
        except TypeError:
            return str(get_address())

    return str(obj)


def _make_record(
    *,
    normalized_address: str,
    converted_address: str,
    status: str,
    error_message: Optional[str],
    used_geocoder: int = 0,
    method: Optional[str] = None,
    matched_candidate: Optional[str] = None,
) -> Dict[str, object]:
    return {
        "normalized_address": normalized_address,
        "converted_address": converted_address,
        "status": status,
        "error_message": error_message,
        "used_geocoder": used_geocoder,
        "method": method,
        "matched_candidate": matched_candidate,
        "cache_source": "computed",
    }


def _mode_name(parse_mode: object, default: str = "LEGACY") -> str:
    if parse_mode is None:
        return default

    name = getattr(parse_mode, "name", None)
    if name:
        return str(name)

    value = getattr(parse_mode, "value", None)
    if value:
        return str(value)

    return str(parse_mode)


def _parse_mode_obj(parse_mode_name: str):
    from vietnamadminunits import ParseMode

    if not parse_mode_name:
        return ParseMode.LEGACY

    try:
        return ParseMode[parse_mode_name]
    except Exception:
        pass

    try:
        return getattr(ParseMode, parse_mode_name)
    except Exception:
        return parse_mode_name


def _try_convert_address(candidate: str, *, short_name: bool) -> Optional[str]:
    from vietnamadminunits import convert_address

    converted = convert_address(candidate)
    if not converted:
        return None

    return _get_address_safely(converted, short_name=short_name)


def _try_parse_address(
    candidate: str,
    *,
    parse_mode_name: str,
    keep_street: bool,
    level: int,
    short_name: bool,
) -> Optional[str]:
    from vietnamadminunits import parse_address

    parsed = parse_address(
        candidate,
        mode=_parse_mode_obj(parse_mode_name),
        keep_street=keep_street,
        level=int(level),
    )

    if not parsed:
        return None

    return _get_address_safely(parsed, short_name=short_name)


def _convert_one_address(
    normalized_address: str,
    *,
    short_name: bool,
    error_value: str,
    parse_mode_name: str = "LEGACY",
    keep_street: bool = True,
    level: int = 3,
) -> Dict[str, object]:
    if not normalized_address:
        return _make_record(
            normalized_address=normalized_address,
            converted_address=error_value,
            status="invalid_format",
            error_message="empty_address",
            method="empty_address",
        )

    errors: List[str] = []
    variants = _address_variants(normalized_address)

    # 1) Ưu tiên giống nút đơn lẻ "Chuẩn hóa (→ 2025)".
    for method, candidate in variants:
        try:
            converted_address = _try_convert_address(candidate, short_name=short_name)
            if converted_address:
                return _make_record(
                    normalized_address=normalized_address,
                    converted_address=converted_address,
                    status="computed",
                    error_message=None,
                    method=f"convert_address:{method}",
                    matched_candidate=candidate,
                )

            errors.append(f"convert_address:{method}: returned_none")
        except Exception as exc:
            errors.append(f"convert_address:{method}: {type(exc).__name__}: {exc}")

    # 2) Nếu convert không ăn, thử parse đúng mode trên sidebar.
    for method, candidate in variants:
        try:
            parsed_address = _try_parse_address(
                candidate,
                parse_mode_name=parse_mode_name,
                keep_street=keep_street,
                level=level,
                short_name=short_name,
            )

            if parsed_address:
                return _make_record(
                    normalized_address=normalized_address,
                    converted_address=parsed_address,
                    status="computed",
                    error_message=None,
                    method=f"parse_address:{parse_mode_name}:{method}",
                    matched_candidate=candidate,
                )

            errors.append(f"parse_address:{parse_mode_name}:{method}: returned_none")
        except Exception as exc:
            errors.append(f"parse_address:{parse_mode_name}:{method}: {type(exc).__name__}: {exc}")

    # 3) Fallback chéo: nhiều địa chỉ thực tế đang trộn cũ/mới.
    fallback_modes = ["LEGACY", "FROM_2025"]
    for fallback_mode in fallback_modes:
        if fallback_mode == parse_mode_name:
            continue

        fallback_level = 3 if fallback_mode == "LEGACY" else 2

        for method, candidate in variants:
            try:
                parsed_address = _try_parse_address(
                    candidate,
                    parse_mode_name=fallback_mode,
                    keep_street=keep_street,
                    level=fallback_level,
                    short_name=short_name,
                )

                if parsed_address:
                    return _make_record(
                        normalized_address=normalized_address,
                        converted_address=parsed_address,
                        status="computed",
                        error_message=None,
                        method=f"parse_address:{fallback_mode}:{method}",
                        matched_candidate=candidate,
                    )

                errors.append(f"parse_address:{fallback_mode}:{method}: returned_none")
            except Exception as exc:
                errors.append(f"parse_address:{fallback_mode}:{method}: {type(exc).__name__}: {exc}")

    return _make_record(
        normalized_address=normalized_address,
        converted_address=error_value,
        status="invalid_format",
        error_message=" | ".join(errors[-10:]) if errors else "unknown_error",
        method="failed_all_variants",
        matched_candidate=None,
    )


def _convert_address_chunk(args: Tuple[List[str], bool, str, str, bool, int]) -> List[Dict[str, object]]:
    addresses, short_name, error_value, parse_mode_name, keep_street, level = args

    return [
        _convert_one_address(
            addr,
            short_name=short_name,
            error_value=error_value,
            parse_mode_name=parse_mode_name,
            keep_street=keep_street,
            level=level,
        )
        for addr in addresses
    ]


def _emit_progress(
    *,
    progress_callback: Optional[ProgressCallback],
    stats: Dict[str, int],
    started_at: float,
) -> None:
    if progress_callback is None:
        return

    processed_unique = int(stats["cache_hits"] + stats["computed"])
    total_unique = int(stats["unique_total"])

    payload = {
        "unique_total": total_unique,
        "processed_unique": processed_unique,
        "remaining_unique": max(0, total_unique - processed_unique),
        "cache_hits": int(stats["cache_hits"]),
        "computed": int(stats["computed"]),
        "invalid": int(stats["invalid"]),
        "used_geocoder": int(stats["used_geocoder"]),
        "elapsed_seconds": round(time.time() - started_at, 2),
    }

    progress_callback(payload)


def convert_unique_addresses(
    addresses: Sequence[str],
    *,
    short_name: bool = True,
    error_value: str = DEFAULT_ERROR_VALUE,
    max_workers: Optional[int] = None,
    db_path: Optional[str] = None,
    chunk_size: int = 300,
    progress_callback: Optional[ProgressCallback] = None,
    parse_mode: object = "LEGACY",
    keep_street: bool = True,
    level: int = 3,
) -> Tuple[Dict[str, Dict[str, object]], Dict[str, int]]:
    started_at = time.time()
    unique_addresses = list(dict.fromkeys(addresses))
    parse_mode_name = _mode_name(parse_mode, default="LEGACY")

    # Chỉ lấy cache thành công; cache lỗi cũ phải chạy lại.
    cached = load_cached_results(unique_addresses, db_path=db_path, include_invalid=False)
    misses = [addr for addr in unique_addresses if addr not in cached]

    results: Dict[str, Dict[str, object]] = dict(cached)

    stats = {
        "unique_total": len(unique_addresses),
        "cache_hits": len(cached),
        "computed": 0,
        "invalid": 0,
        "used_geocoder": 0,
    }

    _emit_progress(progress_callback=progress_callback, stats=stats, started_at=started_at)

    if not misses:
        return results, stats

    workers = max(1, int(max_workers or max(1, min((os.cpu_count() or 2) - 1, 4))))
    chunk_size = max(50, int(chunk_size))
    chunks: List[List[str]] = [list(chunk) for chunk in _chunked(misses, size=chunk_size)]

    worker_args = [
        (chunk, short_name, error_value, parse_mode_name, bool(keep_street), int(level))
        for chunk in chunks
    ]

    if workers == 1 or len(chunks) == 1:
        for args in worker_args:
            rows = _convert_address_chunk(args)
            upsert_cached_results(rows, db_path=db_path)

            for row in rows:
                addr = str(row["normalized_address"])
                results[addr] = row
                stats["computed"] += 1
                stats["invalid"] += int(row.get("status") != "computed")
                stats["used_geocoder"] += int(row.get("used_geocoder") or 0)

            _emit_progress(progress_callback=progress_callback, stats=stats, started_at=started_at)

        return results, stats

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_convert_address_chunk, args) for args in worker_args]

        for future in as_completed(futures):
            rows = future.result()
            upsert_cached_results(rows, db_path=db_path)

            for row in rows:
                addr = str(row["normalized_address"])
                results[addr] = row
                stats["computed"] += 1
                stats["invalid"] += int(row.get("status") != "computed")
                stats["used_geocoder"] += int(row.get("used_geocoder") or 0)

            _emit_progress(progress_callback=progress_callback, stats=stats, started_at=started_at)

    return results, stats


def convert_dataframe_address_column(
    df: pd.DataFrame,
    *,
    address_col: str,
    short_name: bool = True,
    error_value: str = DEFAULT_ERROR_VALUE,
    max_workers: Optional[int] = None,
    db_path: Optional[str] = None,
    output_prefix: str = "converted_",
    chunk_size: int = 300,
    progress_callback: Optional[ProgressCallback] = None,
    parse_mode: object = "LEGACY",
    keep_street: bool = True,
    level: int = 3,
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    started = time.time()
    df_out = df.copy()

    normalized_col = f"{output_prefix}normalized_address"
    converted_col = f"{output_prefix}{address_col}"
    status_col = f"{output_prefix}status"
    source_col = f"{output_prefix}source"
    geocoder_col = f"{output_prefix}used_geocoder"
    error_col = f"{output_prefix}error_message"
    method_col = f"{output_prefix}method"
    candidate_col = f"{output_prefix}matched_candidate"

    df_out[normalized_col] = df_out[address_col].map(normalize_address_value)
    unique_addresses = df_out[normalized_col].drop_duplicates().tolist()

    result_map, stats = convert_unique_addresses(
        unique_addresses,
        short_name=short_name,
        error_value=error_value,
        max_workers=max_workers,
        db_path=db_path,
        chunk_size=chunk_size,
        progress_callback=progress_callback,
        parse_mode=parse_mode,
        keep_street=keep_street,
        level=level,
    )

    df_out[converted_col] = df_out[normalized_col].map(
        lambda x: result_map.get(x, {}).get("converted_address", error_value)
    )
    df_out[status_col] = df_out[normalized_col].map(
        lambda x: result_map.get(x, {}).get("status", "invalid_format")
    )
    df_out[source_col] = df_out[normalized_col].map(
        lambda x: result_map.get(x, {}).get("cache_source", "computed")
    )
    df_out[geocoder_col] = df_out[normalized_col].map(
        lambda x: int(result_map.get(x, {}).get("used_geocoder", 0))
    )
    df_out[error_col] = df_out[normalized_col].map(
        lambda x: result_map.get(x, {}).get("error_message", "") or ""
    )
    df_out[method_col] = df_out[normalized_col].map(
        lambda x: result_map.get(x, {}).get("method", "") or ""
    )
    df_out[candidate_col] = df_out[normalized_col].map(
        lambda x: result_map.get(x, {}).get("matched_candidate", "") or ""
    )

    summary = {
        "total_rows": int(len(df_out)),
        "unique_rows": int(len(unique_addresses)),
        "error_rows": int((df_out[status_col] != "computed").sum()),
        "cache_hits": int(stats["cache_hits"]),
        "computed": int(stats["computed"]),
        "used_geocoder": int(stats["used_geocoder"]),
        "duration_seconds": round(time.time() - started, 2),
    }

    return df_out, summary
