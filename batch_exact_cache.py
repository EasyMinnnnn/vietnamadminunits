from __future__ import annotations

import os
import re
import shutil
import sqlite3
import time
import unicodedata
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

DEFAULT_ERROR_VALUE = "Lỗi định dạng"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "vietnamadminunits"
DEFAULT_CACHE_DB = DEFAULT_CACHE_DIR / "address_conversion_cache.sqlite3"


def normalize_address_value(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
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
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
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


def load_cached_results(addresses: Sequence[str], db_path: Optional[str] = None) -> Dict[str, Dict[str, object]]:
    if not addresses:
        return {}
    path = ensure_cache_schema(db_path)
    results: Dict[str, Dict[str, object]] = {}
    for attempt in range(2):
        try:
            with _connect(path) as conn:
                conn.row_factory = sqlite3.Row
                for chunk in _chunked(list(addresses), size=900):
                    placeholders = ",".join(["?"] * len(chunk))
                    rows = conn.execute(
                        f"""
                        SELECT normalized_address, converted_address, status, error_message, used_geocoder
                        FROM address_conversion_cache
                        WHERE normalized_address IN ({placeholders})
                        """,
                        list(chunk),
                    ).fetchall()
                    for row in rows:
                        results[row["normalized_address"]] = {
                            "converted_address": row["converted_address"],
                            "status": row["status"],
                            "error_message": row["error_message"],
                            "used_geocoder": int(row["used_geocoder"] or 0),
                            "cache_source": "sqlite_cache",
                        }
            return results
        except sqlite3.DatabaseError as exc:
            if attempt == 0 and "malformed" in str(exc).lower():
                _backup_corrupt_db(path)
                path = ensure_cache_schema(str(path))
                results = {}
                continue
            raise
    return results


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
            now,
            now,
        )
        for r in records
    ]
    for attempt in range(2):
        try:
            with _connect(path) as conn:
                conn.executemany(
                    """
                    INSERT INTO address_conversion_cache (
                        normalized_address, converted_address, status, error_message,
                        used_geocoder, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(normalized_address) DO UPDATE SET
                        converted_address=excluded.converted_address,
                        status=excluded.status,
                        error_message=excluded.error_message,
                        used_geocoder=excluded.used_geocoder,
                        updated_at=excluded.updated_at
                    """,
                    rows,
                )
                conn.commit()
            return
        except sqlite3.DatabaseError as exc:
            if attempt == 0 and "malformed" in str(exc).lower():
                _backup_corrupt_db(path)
                path = ensure_cache_schema(str(path))
                continue
            raise


def _convert_address_chunk(args: Tuple[List[str], bool, str]) -> List[Dict[str, object]]:
    addresses, short_name, error_value = args
    from vietnamadminunits import convert_address

    output: List[Dict[str, object]] = []
    for normalized_address in addresses:
        try:
            if not normalized_address:
                output.append({
                    "normalized_address": normalized_address,
                    "converted_address": error_value,
                    "status": "invalid_format",
                    "error_message": "empty_address",
                    "used_geocoder": 0,
                    "cache_source": "computed",
                })
                continue

            converted = convert_address(normalized_address)
            used_geocoder = int(getattr(converted, "_used_geocoder", False))
            converted_address = converted.get_address(short_name=short_name) if converted else error_value
            status = "computed" if converted else "invalid_format"
            output.append({
                "normalized_address": normalized_address,
                "converted_address": converted_address,
                "status": status,
                "error_message": None if converted else "convert_returned_none",
                "used_geocoder": used_geocoder,
                "cache_source": "computed",
            })
        except Exception as exc:
            output.append({
                "normalized_address": normalized_address,
                "converted_address": error_value,
                "status": "invalid_format",
                "error_message": f"{type(exc).__name__}: {exc}",
                "used_geocoder": 0,
                "cache_source": "computed",
            })
    return output


def convert_unique_addresses(
    addresses: Sequence[str],
    *,
    short_name: bool = True,
    error_value: str = DEFAULT_ERROR_VALUE,
    max_workers: Optional[int] = None,
    db_path: Optional[str] = None,
    chunk_size: int = 300,
) -> Tuple[Dict[str, Dict[str, object]], Dict[str, int]]:
    unique_addresses = list(dict.fromkeys(addresses))
    cached = load_cached_results(unique_addresses, db_path=db_path)
    misses = [addr for addr in unique_addresses if addr not in cached]

    results: Dict[str, Dict[str, object]] = dict(cached)
    stats = {
        "unique_total": len(unique_addresses),
        "cache_hits": len(cached),
        "computed": 0,
        "invalid": 0,
        "used_geocoder": 0,
    }

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
        return results, stats

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_convert_address_chunk, (chunk, short_name, error_value)) for chunk in chunks]
        for future in as_completed(futures):
            rows = future.result()
            upsert_cached_results(rows, db_path=db_path)
            for row in rows:
                addr = str(row["normalized_address"])
                results[addr] = row
                stats["computed"] += 1
                stats["invalid"] += int(row.get("status") != "computed")
                stats["used_geocoder"] += int(row.get("used_geocoder") or 0)

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
) -> Tuple[pd.DataFrame, Dict[str, float]]:
    started = time.time()
    df_out = df.copy()

    normalized_col = f"{output_prefix}normalized_address"
    converted_col = f"{output_prefix}{address_col}"
    status_col = f"{output_prefix}status"
    source_col = f"{output_prefix}source"
    geocoder_col = f"{output_prefix}used_geocoder"

    df_out[normalized_col] = df_out[address_col].map(normalize_address_value)
    unique_addresses = df_out[normalized_col].drop_duplicates().tolist()
    result_map, stats = convert_unique_addresses(
        unique_addresses,
        short_name=short_name,
        error_value=error_value,
        max_workers=max_workers,
        db_path=db_path,
        chunk_size=chunk_size,
    )

    df_out[converted_col] = df_out[normalized_col].map(lambda x: result_map.get(x, {}).get("converted_address", error_value))
    df_out[status_col] = df_out[normalized_col].map(lambda x: result_map.get(x, {}).get("status", "invalid_format"))
    df_out[source_col] = df_out[normalized_col].map(lambda x: result_map.get(x, {}).get("cache_source", "computed"))
    df_out[geocoder_col] = df_out[normalized_col].map(lambda x: int(result_map.get(x, {}).get("used_geocoder", 0)))

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
