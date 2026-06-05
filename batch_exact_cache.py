from __future__ import annotations

"""
batch_exact_cache.py
====================

Drop-in replacement for the Streamlit app batch converter.

Mục tiêu của bản này:
1. Kết quả batch phải ưu tiên cùng logic với nút đơn lẻ "Chuẩn hóa (→ 2025)".
2. Không dùng cache cũ sai logic, ví dụ các dòng đã cache bằng parse_address:LEGACY.
3. Vẫn giữ hiệu năng khi chạy lô lớn bằng cách:
   - chuẩn hóa và xử lý theo địa chỉ unique;
   - cache SQLite kết quả đã tính đúng theo logic version;
   - chạy song song theo chunk.

File này tương thích với app.py hiện tại, đang import:
    DEFAULT_CACHE_DB
    convert_dataframe_address_column
    ensure_cache_schema
    normalize_address_value
"""

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

# Tăng version khi thay logic convert để bỏ qua cache cũ.
# Các dòng cache cũ chưa có logic_version hoặc logic_version khác sẽ được tính lại.
BATCH_LOGIC_VERSION = "convert_2025_first_v2"

ProgressCallback = Callable[[Dict[str, float]], None]


# ============================================================
# Text normalization / input variants
# ============================================================

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
    text = re.sub(r"\s*,\s*", ", ", text)
    text = re.sub(r"(?:,\s*){2,}", ", ", text)
    return text.strip(" ,;")


def _strip_vn_accents(text: str) -> str:
    text = unicodedata.normalize("NFD", str(text or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d").replace("Đ", "D").lower().strip()


def _split_address_parts(text: str) -> List[str]:
    text = normalize_address_value(text)
    return [p.strip(" ,;") for p in text.split(",") if p.strip(" ,;")]


def _is_bare_number_admin(part: str) -> bool:
    return bool(re.fullmatch(r"\d{1,3}[A-Za-z]?", str(part or "").strip()))


def _normalize_numeric_ward(part: str) -> str:
    raw = str(part or "").strip()
    norm = _strip_vn_accents(raw)

    # 4 / 04 / 4A -> Phường 4 / Phường 04 / Phường 4A
    if _is_bare_number_admin(raw):
        return f"Phường {raw}"

    # P4 / P.4 / phuong 4 / phường 4 -> Phường 4
    m = re.fullmatch(r"(?:p|phuong)\.?\s*(\d{1,3}[a-z]?)", norm, flags=re.IGNORECASE)
    if m:
        code = m.group(1)
        if code[-1:].isalpha():
            code = code[:-1] + code[-1:].upper()
        return f"Phường {code}"

    return raw


def _normalize_numeric_district(part: str) -> str:
    raw = str(part or "").strip()
    norm = _strip_vn_accents(raw)

    # 10 / 01 / 12 -> Quận 10 / Quận 01 / Quận 12
    if _is_bare_number_admin(raw):
        return f"Quận {raw}"

    # Q10 / Q.10 / quan 10 / quận 10 -> Quận 10
    m = re.fullmatch(r"(?:q|quan)\.?\s*(\d{1,3}[a-z]?)", norm, flags=re.IGNORECASE)
    if m:
        code = m.group(1)
        if code[-1:].isalpha():
            code = code[:-1] + code[-1:].upper()
        return f"Quận {code}"

    return raw


def expand_numeric_legacy_admin_parts(address: str) -> Optional[str]:
    """
    Xử lý dữ liệu cũ bị rút gọn cấp hành chính bằng số:
      322/6 Vĩnh Viễn, 4, 10, Hồ Chí Minh
    thành:
      322/6 Vĩnh Viễn, Phường 4, Quận 10, Hồ Chí Minh

    Chỉ sinh biến thể mới, không sửa phá dữ liệu gốc.
    """
    parts = _split_address_parts(address)
    if len(parts) < 4:
        return None

    province = parts[-1]
    district = parts[-2]
    ward = parts[-3]
    street = ", ".join(parts[:-3]).strip()

    ward2 = _normalize_numeric_ward(ward)
    district2 = _normalize_numeric_district(district)

    if ward2 == ward and district2 == district:
        return None

    if not street:
        return f"{ward2}, {district2}, {province}"
    return f"{street}, {ward2}, {district2}, {province}"


def _remove_placeholder_prefix(address: str) -> Optional[str]:
    """
    Nhiều dữ liệu đầu vào dùng 'Không ...' ở đầu chuỗi để biểu diễn không có số nhà.
    Biến thể này chỉ bỏ placeholder ở đầu nếu có, không tác động chuỗi gốc.
    """
    text = normalize_address_value(address)
    if not text:
        return None

    norm = _strip_vn_accents(text)
    for prefix in ("khong ", "không ", "ko ", "khg "):
        if norm.startswith(_strip_vn_accents(prefix)):
            # Bỏ đúng độ dài token đầu theo whitespace, tránh lệch dấu tiếng Việt.
            parts = text.split(" ", 1)
            return parts[1].strip() if len(parts) > 1 else None
    return None


def _legacy_four_part_candidate(address: str) -> Optional[str]:
    """
    Nếu địa chỉ có quá nhiều dấu phẩy trong phần mô tả đường/số nhà,
    thử gom phần trước ward/district/province lại thành street.

    Ví dụ:
      A, B, C, Phường X, Quận Y, Tỉnh Z
    -> A B C, Phường X, Quận Y, Tỉnh Z
    """
    parts = _split_address_parts(address)
    if len(parts) <= 4:
        return None

    province = parts[-1]
    district = parts[-2]
    ward = parts[-3]
    street = " ".join(parts[:-3]).strip()
    if not street:
        return None
    return f"{street}, {ward}, {district}, {province}"


def _address_variants(address: str) -> List[Tuple[str, str]]:
    """
    Sinh biến thể đầu vào giống app.py nhưng batch sẽ ưu tiên convert_address trước.

    Thứ tự quan trọng:
    - expand_numeric_admin_parts trước để xử lý case 4, 10, HCM;
    - raw tiếp theo để giống tra cứu đơn lẻ cho địa chỉ đầy đủ;
    - các biến thể dọn dấu phẩy/placeholder chỉ là hỗ trợ fallback.
    """
    text = normalize_address_value(address)
    variants: List[Tuple[str, str]] = []
    seen = set()

    def add(method: str, candidate: Optional[str]) -> None:
        if not candidate:
            return
        candidate = normalize_address_value(candidate)
        key = candidate.casefold()
        if candidate and key not in seen:
            seen.add(key)
            variants.append((method, candidate))

    expanded_numeric = expand_numeric_legacy_admin_parts(text)
    no_placeholder = _remove_placeholder_prefix(text)
    cleaned = normalize_address_value(text)

    add("expand_numeric_admin_parts", expanded_numeric)
    if expanded_numeric:
        add("expand_numeric_admin_parts_and_merge", _legacy_four_part_candidate(expanded_numeric))
    add("raw", text)
    add("clean_commas", cleaned)
    add("merge_extra_street_commas", _legacy_four_part_candidate(cleaned))
    add("remove_placeholder", no_placeholder)
    add("remove_placeholder_and_merge", _legacy_four_part_candidate(no_placeholder or ""))

    return variants


# ============================================================
# SQLite cache
# ============================================================

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
                        logic_version TEXT,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                    """
                )
                _ensure_column(conn, "address_conversion_cache", "method", "method TEXT")
                _ensure_column(conn, "address_conversion_cache", "matched_candidate", "matched_candidate TEXT")
                _ensure_column(conn, "address_conversion_cache", "logic_version", "logic_version TEXT")

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
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_address_conversion_logic_version "
                    "ON address_conversion_cache(logic_version)"
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
    Đọc cache đúng version hiện tại.

    Quan trọng:
    - Không lấy cache cũ chưa có logic_version, vì đây là nguồn làm batch trả về
      parse_address:LEGACY thay vì convert_address giống tra cứu đơn lẻ.
    - Mặc định không lấy cache lỗi invalid_format để sau khi sửa rule có thể chạy lại.
    """
    if not addresses:
        return {}

    path = ensure_cache_schema(db_path)
    result: Dict[str, Dict[str, object]] = {}

    with _connect(path) as conn:
        for chunk in _chunked(list(addresses), size=900):
            placeholders = ",".join("?" for _ in chunk)
            status_clause = "" if include_invalid else "AND status = 'computed'"
            sql = f"""
                SELECT
                    normalized_address,
                    converted_address,
                    status,
                    error_message,
                    used_geocoder,
                    method,
                    matched_candidate,
                    logic_version
                FROM address_conversion_cache
                WHERE normalized_address IN ({placeholders})
                  AND logic_version = ?
                  {status_clause}
            """
            rows = conn.execute(sql, [*chunk, BATCH_LOGIC_VERSION]).fetchall()
            for row in rows:
                (
                    normalized_address,
                    converted_address,
                    status,
                    error_message,
                    used_geocoder,
                    method,
                    matched_candidate,
                    logic_version,
                ) = row
                result[str(normalized_address)] = {
                    "normalized_address": normalized_address,
                    "converted_address": converted_address,
                    "status": status,
                    "error_message": error_message,
                    "used_geocoder": int(used_geocoder or 0),
                    "method": method,
                    "matched_candidate": matched_candidate,
                    "logic_version": logic_version,
                    "cache_source": "sqlite_cache",
                }

    return result


def upsert_cached_results(rows: Sequence[Dict[str, object]], db_path: Optional[str] = None) -> None:
    if not rows:
        return

    path = ensure_cache_schema(db_path)
    now = time.time()

    payload = []
    for row in rows:
        payload.append(
            (
                row.get("normalized_address", ""),
                row.get("converted_address", ""),
                row.get("status", "invalid_format"),
                row.get("error_message"),
                int(row.get("used_geocoder") or 0),
                row.get("method"),
                row.get("matched_candidate"),
                BATCH_LOGIC_VERSION,
                now,
                now,
            )
        )

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
                logic_version,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_address) DO UPDATE SET
                converted_address = excluded.converted_address,
                status = excluded.status,
                error_message = excluded.error_message,
                used_geocoder = excluded.used_geocoder,
                method = excluded.method,
                matched_candidate = excluded.matched_candidate,
                logic_version = excluded.logic_version,
                updated_at = excluded.updated_at
            """,
            payload,
        )
        conn.commit()


# ============================================================
# Conversion logic
# ============================================================

def _get_address_safely(obj: object, *, short_name: bool) -> str:
    get_address = getattr(obj, "get_address", None)
    if callable(get_address):
        try:
            return str(get_address(short_name=short_name))
        except TypeError:
            return str(get_address())
    return str(obj)


def _admin_result_score(obj: object) -> float:
    data = getattr(obj, "__dict__", {}) or {}
    score = 0.0
    for key in ["province", "district", "ward"]:
        if data.get(key):
            score += 1.0
    if data.get("street"):
        score += 0.25
    if data.get("latitude") is not None and data.get("longitude") is not None:
        score += 0.25
    return score


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
        "used_geocoder": int(used_geocoder or 0),
        "method": method,
        "matched_candidate": matched_candidate,
        "logic_version": BATCH_LOGIC_VERSION,
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


def _try_convert_address_obj(candidate: str):
    from vietnamadminunits import convert_address

    return convert_address(candidate)


def _try_parse_address_obj(
    candidate: str,
    *,
    parse_mode_name: str,
    keep_street: bool,
    level: int,
):
    from vietnamadminunits import parse_address

    return parse_address(
        candidate,
        mode=_parse_mode_obj(parse_mode_name),
        keep_street=keep_street,
        level=int(level),
    )


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

    variants = _address_variants(normalized_address)
    errors: List[str] = []

    # 1) Luôn ưu tiên convert_address giống nút đơn lẻ "Chuẩn hóa (→ 2025)".
    # Không được trả kết quả parse_address nếu convert_address đã thành công.
    best_obj = None
    best_candidate = None
    best_method = None
    best_score = -1.0

    for method, candidate in variants:
        try:
            obj = _try_convert_address_obj(candidate)
            if not obj:
                errors.append(f"convert_address:{method}: returned_none")
                continue
            score = _admin_result_score(obj)
            if score > best_score:
                best_obj = obj
                best_candidate = candidate
                best_method = method
                best_score = score
        except Exception as exc:
            errors.append(f"convert_address:{method}: {type(exc).__name__}: {exc}")

    if best_obj is not None:
        return _make_record(
            normalized_address=normalized_address,
            converted_address=_get_address_safely(best_obj, short_name=short_name),
            status="computed",
            error_message=None,
            used_geocoder=int(bool(getattr(best_obj, "_used_geocoder", False))),
            method=f"convert_address:{best_method}",
            matched_candidate=best_candidate,
        )

    # 2) Fallback parse chỉ dùng khi convert_address thật sự không xử lý được.
    # Điều này giúp batch không mất dữ liệu đầu ra nhưng vẫn tránh case sai như
    # Phường Trung Văn -> phải convert sang Phường Đại Mỗ.
    fallback_modes = []
    if parse_mode_name:
        fallback_modes.append(parse_mode_name)
    for item in ["LEGACY", "FROM_2025"]:
        if item not in fallback_modes:
            fallback_modes.append(item)

    for fallback_mode in fallback_modes:
        fallback_level = int(level) if fallback_mode == parse_mode_name else (3 if fallback_mode == "LEGACY" else 2)
        for method, candidate in variants:
            try:
                obj = _try_parse_address_obj(
                    candidate,
                    parse_mode_name=fallback_mode,
                    keep_street=keep_street,
                    level=fallback_level,
                )
                if not obj:
                    errors.append(f"parse_address:{fallback_mode}:{method}: returned_none")
                    continue
                return _make_record(
                    normalized_address=normalized_address,
                    converted_address=_get_address_safely(obj, short_name=short_name),
                    status="computed",
                    error_message=None,
                    used_geocoder=0,
                    method=f"parse_address:{fallback_mode}:{method}",
                    matched_candidate=candidate,
                )
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
    unique_addresses = [addr for addr in list(dict.fromkeys(addresses)) if addr is not None]
    parse_mode_name = _mode_name(parse_mode, default="LEGACY")

    # Chỉ lấy cache thành công và đúng logic_version hiện tại.
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

    def consume_rows(rows: List[Dict[str, object]]) -> None:
        upsert_cached_results(rows, db_path=db_path)
        for row in rows:
            addr = str(row["normalized_address"])
            results[addr] = row
            stats["computed"] += 1
            stats["invalid"] += int(row.get("status") != "computed")
            stats["used_geocoder"] += int(row.get("used_geocoder") or 0)
        _emit_progress(progress_callback=progress_callback, stats=stats, started_at=started_at)

    if workers == 1 or len(chunks) == 1:
        for args in worker_args:
            consume_rows(_convert_address_chunk(args))
        return results, stats

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_convert_address_chunk, args) for args in worker_args]
        for future in as_completed(futures):
            consume_rows(future.result())

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
