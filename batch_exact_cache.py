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
    """Chuẩn hóa nhẹ text đầu vào nhưng không làm mất dấu tiếng Việt."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""

    text = str(value)
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = text.replace("，", ",").replace("؛", ";")
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
    Đọc cache. Mặc định KHÔNG trả lại cache lỗi định dạng.
    Lý do: sau khi sửa rule, các dòng từng lỗi cần được chạy lại.
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
    convert_address() xử lý tốt nhất dạng: street, ward, district, province.
    Nếu phần street có nhiều dấu phẩy, ví dụ:
      số 16, LKV 18B 0, Phường Trung Hưng, Sơn Tây, Hà Nội
    thì gom tất cả phần trước 3 cấp hành chính cuối vào street:
      số 16 LKV 18B 0, Phường Trung Hưng, Sơn Tây, Hà Nội
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

    add("raw", text)
    add("clean_commas", _clean_for_parser(text))
    add("merge_extra_street_commas", _legacy_four_part_candidate(text))

    without_placeholder = re.sub(
        r"\b(?:Khác|Không)\s*,",
        ",",
        _clean_for_parser(text),
        flags=re.IGNORECASE,
    )
    add("remove_placeholder_before_comma", without_placeholder)
    add("remove_placeholder_and_merge", _legacy_four_part_candidate(without_placeholder))

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


def _convert_one_address(normalized_address: str, *, short_name: bool, error_value: str) -> Dict[str, object]:
    from vietnamadminunits import ParseMode, convert_address, parse_address

    if not normalized_address:
        return _make_record(
            normalized_address=normalized_address,
            converted_address=error_value,
            status="invalid_format",
            error_message="empty_address",
        )

    errors: List[str] = []
    variants = _address_variants(normalized_address)

    # 1) Ưu tiên đúng luồng chuẩn hóa cũ -> mới.
    for method, candidate in variants:
        try:
            converted = convert_address(candidate)

            if converted:
                return _make_record(
                    normalized_address=normalized_address,
                    converted_address=_get_address_safely(converted, short_name=short_name),
                    status="computed",
                    error_message=None,
                    used_geocoder=int(getattr(converted, "_used_geocoder", False)),
                    method=f"convert_address:{method}",
                    matched_candidate=candidate,
                )

            errors.append(f"convert_address:{method}: returned_none")
        except Exception as exc:
            errors.append(f"convert_address:{method}: {type(exc).__name__}: {exc}")

    # 2) Nếu input là địa chỉ cũ nhưng bị bẩn, parse LEGACY trước rồi convert lại.
    for method, candidate in variants:
        try:
            parsed_legacy = parse_address(candidate, mode=ParseMode.LEGACY, keep_street=True, level=3)

            if not parsed_legacy:
                errors.append(f"parse_legacy:{method}: returned_none")
                continue

            legacy_address = _get_address_safely(parsed_legacy, short_name=False)
            legacy_candidates = [legacy_address, _legacy_four_part_candidate(legacy_address)]

            for legacy_candidate in legacy_candidates:
                if not legacy_candidate:
                    continue

                try:
                    converted = convert_address(legacy_candidate)

                    if converted:
                        return _make_record(
                            normalized_address=normalized_address,
                            converted_address=_get_address_safely(converted, short_name=short_name),
                            status="computed",
                            error_message=None,
                            used_geocoder=int(getattr(converted, "_used_geocoder", False)),
                            method=f"parse_legacy_then_convert:{method}",
                            matched_candidate=legacy_candidate,
                        )

                    errors.append("parse_legacy_then_convert: returned_none")
                except Exception as exc:
                    errors.append(f"parse_legacy_then_convert: {type(exc).__name__}: {exc}")

        except Exception as exc:
            errors.append(f"parse_legacy:{method}: {type(exc).__name__}: {exc}")

    # 3) Nếu input thực chất đã là địa chỉ 2025, chuẩn hóa bằng parse FROM_2025.
    for method, candidate in variants:
        try:
            parsed_new = parse_address(candidate, mode=ParseMode.FROM_2025, keep_street=True, level=2)

            if parsed_new:
                return _make_record(
                    normalized_address=normalized_address,
                    converted_address=_get_address_safely(parsed_new, short_name=short_name),
                    status="computed",
                    error_message=None,
                    used_geocoder=0,
                    method=f"parse_from_2025:{method}",
                    matched_candidate=candidate,
                )

            errors.append(f"parse_from_2025:{method}: returned_none")
        except Exception as exc:
            errors.append(f"parse_from_2025:{method}: {type(exc).__name__}: {exc}")

    return _make_record(
        normalized_address=normalized_address,
        converted_address=error_value,
        status="invalid_format",
        error_message=" | ".join(errors[-8:]) if errors else "unknown_error",
        used_geocoder=0,
        method="failed_all_variants",
        matched_candidate=None,
    )


def _convert_address_chunk(args: Tuple[List[str], bool, str]) -> List[Dict[str, object]]:
    addresses, short_name, error_value = args

    return [
        _convert_one_address(addr, short_name=short_name, error_value=error_value)
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
) -> Tuple[Dict[str, Dict[str, object]], Dict[str, int]]:
    started_at = time.time()
    unique_addresses = list(dict.fromkeys(addresses))

    # Chỉ lấy cache thành công. Cache invalid_format cũ sẽ được chạy lại sau khi sửa rule.
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

    if workers == 1 or len(chunks) == 1:
        for chunk in chunks:
            rows = _convert_address_chunk((chunk, short_name, error_value))
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
        futures = [
            executor.submit(_convert_address_chunk, (chunk, short_name, error_value))
            for chunk in chunks
        ]

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
