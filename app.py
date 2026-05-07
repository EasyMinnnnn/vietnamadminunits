# app.py
from __future__ import annotations

import os
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import pydeck as pdk
import streamlit as st
from pydeck.data_utils import viewport_helpers as vh
from vietnamadminunits import ParseMode, convert_address, parse_address

from batch_exact_cache import (
    DEFAULT_CACHE_DB,
    convert_dataframe_address_column,
    ensure_cache_schema,
    normalize_address_value,
)
from geocode_tool import Geocoder


st.set_page_config(page_title="Chuẩn hóa địa chỉ Việt Nam", layout="wide")

CSS = """
<style>
.block-container {
    padding-top: 2rem;
    max-width: 1180px;
}
[data-testid="stSidebar"] {
    min-width: 310px;
}
.main-title {
    background: linear-gradient(135deg, #087c73 0%, #06433f 100%);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 22px;
    padding: 28px 34px;
    margin-bottom: 22px;
    box-shadow: 0 14px 40px rgba(0, 0, 0, 0.18);
}
.main-title h1 {
    margin: 0;
    color: #f5c443;
    font-size: 2.5rem;
    font-weight: 800;
}
.section-card {
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 18px;
    padding: 14px 18px;
    margin: 18px 0;
    background: rgba(255, 255, 255, 0.04);
}
.section-card b {
    color: #f5c443;
}
.small-gap { height: 12px; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

st.markdown(
    """
    <div class="main-title">
        <h1>📍 Công cụ chuyển đổi địa giới hành chính</h1>
    </div>
    """,
    unsafe_allow_html=True,
)

if "last_points" not in st.session_state:
    st.session_state["last_points"] = None


# =========================
# Helpers
# =========================

def _score_vn(text: str) -> float:
    vn_chars = (
        "ăâđêôơưĂÂĐÊÔƠƯ"
        "àáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩị"
        "òóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"
    )
    has_vn = sum(ch in vn_chars for ch in text)
    combining = sum(unicodedata.combining(ch) != 0 for ch in text)
    qmarks = text.count("?")
    return has_vn * 3 + combining * 2 - qmarks * 2


def _read_csv_with_fallback(file, encoding_mode: str = "auto") -> pd.DataFrame:
    if encoding_mode and encoding_mode.lower() != "auto":
        file.seek(0)
        return pd.read_csv(file, encoding=encoding_mode)

    candidates = [
        "utf-8-sig",
        "utf-8",
        "cp1258",
        "cp1252",
        "latin1",
        "utf-16",
        "utf-16-le",
        "utf-16-be",
    ]

    best_df: Optional[pd.DataFrame] = None
    best_score = -1e9
    best_enc: Optional[str] = None
    errs = []

    for enc in candidates:
        try:
            file.seek(0)
            df = pd.read_csv(file, encoding=enc)
            sample = "\n".join(
                df.astype(str)
                .head(5)
                .apply(lambda r: " ".join(map(str, r.values)), axis=1)
                .tolist()
            )
            score = _score_vn(sample)
            if score > best_score:
                best_df = df
                best_score = score
                best_enc = enc
        except Exception as exc:
            errs.append(f"{enc}: {exc}")

    if best_df is None:
        raise UnicodeDecodeError(
            "utf-8",
            b"",
            0,
            1,
            f"Không decode được CSV. Tried: {errs}",
        )

    st.session_state["_detected_encoding"] = best_enc
    return best_df


def _read_excel(file, sheet_name: Optional[str] = None) -> pd.DataFrame:
    file.seek(0)
    if sheet_name:
        return pd.read_excel(file, sheet_name=sheet_name)

    xls = pd.ExcelFile(file)
    first_sheet = xls.sheet_names[0]
    file.seek(0)
    return pd.read_excel(file, sheet_name=first_sheet)


def load_table(uploaded, encoding_choice: str = "auto", excel_sheet: Optional[str] = None) -> pd.DataFrame:
    ext = Path(uploaded.name).suffix.lower()

    if ext == ".csv":
        return _read_csv_with_fallback(uploaded, encoding_choice)

    if ext in (".xls", ".xlsx"):
        return _read_excel(uploaded, sheet_name=excel_sheet)

    raise ValueError("Định dạng không hỗ trợ. Hỗ trợ: CSV, XLS, XLSX.")


def normalize_text_column(df: pd.DataFrame, address_col: str) -> pd.DataFrame:
    df = df.copy()
    df[address_col] = df[address_col].map(normalize_address_value)
    return df


def to_clean_df(obj: Any) -> pd.DataFrame:
    if obj is None:
        return pd.DataFrame()

    data: Dict[str, Any] = {
        k: v
        for k, v in getattr(obj, "__dict__", {}).items()
        if not k.startswith("_") and v is not None
    }

    order = [
        "province",
        "district",
        "ward",
        "street",
        "short_province",
        "short_district",
        "short_ward",
        "province_type",
        "district_type",
        "ward_type",
        "latitude",
        "longitude",
        "province_code",
        "district_code",
        "ward_code",
        "province_key",
        "district_key",
        "ward_key",
    ]
    cols = [c for c in order if c in data] + [c for c in data if c not in order]
    return pd.DataFrame([{k: data.get(k) for k in cols}])


def _normalize_points(
    df: pd.DataFrame,
    lat_col: str = "latitude",
    lon_col: str = "longitude",
) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    if lat_col not in df.columns or lon_col not in df.columns:
        return pd.DataFrame()

    df = df.rename(columns={lat_col: "lat", lon_col: "lon"}).copy()
    for col in ["lat", "lon"]:
        s = df[col].astype(str).str.replace(",", ".", regex=False)
        s_num = s.str.extract(r"(-?\d+(?:\.\d+)?)", expand=False)
        df[col] = pd.to_numeric(s_num, errors="coerce")

    df = df[df["lat"].between(-90, 90) & df["lon"].between(-180, 180)]
    return df.dropna(subset=["lat", "lon"]).reset_index(drop=True)


def set_last_points_from_df(df: pd.DataFrame) -> None:
    pts = _normalize_points(df)
    st.session_state["last_points"] = pts if not pts.empty else None


def render_map(
    df: pd.DataFrame,
    lat_col: str = "latitude",
    lon_col: str = "longitude",
    label_col: Optional[str] = None,
    point_radius: int = 50,
) -> None:
    pts = _normalize_points(df, lat_col, lon_col)
    if pts.empty:
        st.warning("Không có toạ độ hợp lệ để hiển thị.")
        return

    view = vh.compute_view(pts[["lon", "lat"]])
    view.zoom = max(min(view.zoom, 15) - 1, 5)

    layers = [
        pdk.Layer(
            "ScatterplotLayer",
            data=pts,
            get_position="[lon, lat]",
            get_radius=point_radius,
            get_fill_color="[255, 77, 77, 200]",
            pickable=True,
        )
    ]

    if label_col and label_col in pts.columns:
        layers.append(
            pdk.Layer(
                "TextLayer",
                data=pts,
                get_position="[lon, lat]",
                get_text=f"[{label_col}]",
                get_size=14,
                get_color="[0,0,0,255]",
                get_alignment_baseline='"top"',
            )
        )

    deck = pdk.Deck(
        layers=layers,
        initial_view_state=view,
        map_provider="carto",
        map_style="light",
        tooltip={"text": "{lat}, {lon}"},
    )
    st.pydeck_chart(deck, use_container_width=True)


CSV_PATH = "data/interim/legacy_63-province-10040-ward_with_location_and_key.csv"


@st.cache_resource(show_spinner=False)
def load_gc():
    path = CSV_PATH
    try:
        return Geocoder(csv_path_or_url=path)
    except TypeError:
        try:
            return Geocoder(csv_path=path)
        except TypeError:
            return Geocoder(path)


# =========================
# Sidebar
# =========================

st.sidebar.markdown("### TTĐGTS")
st.sidebar.header("⚙️ Tùy chọn")

mode_str = st.sidebar.selectbox("Chế độ phân tích", ["LEGACY", "FROM_2025"])
mode = ParseMode[mode_str]
keep_street = st.sidebar.checkbox("Giữ tên đường", True)
short_name = st.sidebar.checkbox("Tên rút gọn", True)
level = st.sidebar.number_input(
    "Level",
    min_value=1,
    max_value=3 if mode_str == "LEGACY" else 2,
    value=3 if mode_str == "LEGACY" else 2,
    step=1,
)

st.sidebar.markdown("---")
st.sidebar.subheader("Batch (CSV/Excel)")

max_workers_default = max(1, min(4, (os.cpu_count() or 2) - 1))
max_workers = st.sidebar.number_input(
    "Số worker batch",
    min_value=1,
    max_value=max(1, os.cpu_count() or 1),
    value=max_workers_default,
    step=1,
)
chunk_size = st.sidebar.number_input(
    "Kích thước chunk",
    min_value=50,
    max_value=2000,
    value=300,
    step=50,
)
cache_db_path = st.sidebar.text_input("SQLite cache path", str(DEFAULT_CACHE_DB))
uploaded = st.sidebar.file_uploader("Tải CSV/Excel", type=["csv", "xlsx", "xls"])

address_col = None
excel_sheet = None
df_preview: Optional[pd.DataFrame] = None

if uploaded is not None:
    ext = Path(uploaded.name).suffix.lower()

    if ext == ".csv":
        encoding_choice = st.sidebar.selectbox(
            "Encoding (CSV)",
            ["auto", "utf-8-sig", "utf-8", "latin1", "cp1258", "cp1252"],
            index=0,
        )
        try:
            df_preview = load_table(uploaded, encoding_choice)
        except Exception as exc:
            st.sidebar.error(f"Lỗi đọc CSV: {exc}")
    else:
        try:
            uploaded.seek(0)
            xls = pd.ExcelFile(uploaded)
            excel_sheet = st.sidebar.selectbox("Chọn sheet", xls.sheet_names, index=0)
            df_preview = load_table(uploaded, excel_sheet=excel_sheet)
        except Exception as exc:
            st.sidebar.error(f"Lỗi đọc Excel: {exc}")

    if df_preview is not None:
        enc_msg = st.session_state.get("_detected_encoding")
        if enc_msg:
            st.sidebar.caption(f"Detected: **{enc_msg}**")
        address_col = st.sidebar.selectbox("Chọn cột địa chỉ", list(df_preview.columns))


# =========================
# Quick parse / convert
# =========================

st.markdown(
    """
    <div class="section-card"><b>🔎 Phân tích nhanh</b></div>
    """,
    unsafe_allow_html=True,
)
st.caption("Ví dụ: 194 Trần Quang Khải, phường Lý Thái Tổ, quận Hoàn Kiếm, Hà Nội")

address_input = st.text_input(
    "Nhập địa chỉ",
    "194 Trần Quang Khải, phường Lý Thái Tổ, quận Hoàn Kiếm, Hà Nội",
)

c1, c2 = st.columns([1, 1])
with c1:
    parse_clicked = st.button("Phân tích địa chỉ")
with c2:
    convert_clicked = st.button("Chuẩn hóa (→ 2025)")

if parse_clicked:
    try:
        parsed = parse_address(
            address_input,
            mode=mode,
            keep_street=keep_street,
            level=int(level),
        )
        if parsed:
            st.success("Phân tích thành công")
            df_parsed = to_clean_df(parsed)
            st.dataframe(df_parsed, use_container_width=True)
            set_last_points_from_df(df_parsed)
        else:
            st.warning("⚠️ Không phân tích được địa chỉ.")
    except Exception as exc:
        st.error(f"❌ Lỗi phân tích: {type(exc).__name__}: {exc}")
        st.exception(exc)

if convert_clicked:
    try:
        converted = convert_address(address_input)
        if converted:
            st.success("Kết quả sau chuẩn hóa (→ 2025)")
            df_converted = to_clean_df(converted)
            st.dataframe(df_converted, use_container_width=True)
            set_last_points_from_df(df_converted)
        else:
            st.warning("⚠️ Không chuẩn hóa được địa chỉ.")
    except Exception as exc:
        st.error(f"⚠️ Lỗi khi chuẩn hóa: {type(exc).__name__}: {exc}")
        st.exception(exc)

with st.expander("🧭 Kiểm tra tọa độ (OSM + ranh xã)"):
    c3, c4, c5 = st.columns([1, 1, 0.6])
    with c3:
        lat_in = st.number_input("Latitude", value=21.028, format="%.8f")
    with c4:
        lon_in = st.number_input("Longitude", value=105.834, format="%.8f")
    with c5:
        st.markdown("<div class='small-gap'></div>", unsafe_allow_html=True)
        rev_clicked = st.button("Reverse")

    if rev_clicked:
        try:
            gc = load_gc()
            res = gc.geocode(float(lat_in), float(lon_in))
            if res:
                st.success("✅ Đã xác định địa chỉ")
                show = {
                    "house_number": res.get("house_number"),
                    "road": res.get("road"),
                    "ward": res.get("ward"),
                    "province": res.get("province"),
                    "latitude": res.get("latitude"),
                    "longitude": res.get("longitude"),
                    "formatted": res.get("formatted"),
                }
                df_show = pd.DataFrame([show])
                st.dataframe(df_show, use_container_width=True)
                set_last_points_from_df(df_show)
            else:
                st.warning("⚠️ Không xác định được xã/phường hoặc OSM thiếu số nhà/đường.")
        except Exception as exc:
            st.error(f"❌ Lỗi reverse: {type(exc).__name__}: {exc}")
            st.exception(exc)

if st.session_state.get("last_points") is not None:
    render_map(st.session_state["last_points"])


# =========================
# Batch
# =========================

st.markdown(
    """
    <div class="section-card"><b>📦 Xử lý hàng loạt</b></div>
    """,
    unsafe_allow_html=True,
)

if uploaded is None or df_preview is None:
    st.caption("Tải file CSV/Excel ở sidebar để bắt đầu.")
else:
    st.write("**Xem nhanh dữ liệu đầu vào:**")
    st.dataframe(df_preview.head(20), use_container_width=True)

    run_batch = st.button("⚙️ Chạy chuẩn hóa")

    cols_lower = {str(c).lower(): c for c in df_preview.columns}
    has_latlon = "latitude" in cols_lower and "longitude" in cols_lower

    if has_latlon:
        run_rev = st.button("Reverse geocode (lat, lon)")
    else:
        run_rev = False

    if run_batch and address_col:
        try:
            ensure_cache_schema(cache_db_path)
            df_in = normalize_text_column(df_preview, address_col)

            progress_bar = st.progress(0.0, text="Chuẩn bị chạy batch...")
            live_cols = st.columns(3)
            live_elapsed = live_cols[0].empty()
            live_processed = live_cols[1].empty()
            live_cache = live_cols[2].empty()
            live_caption = st.empty()

            def on_progress(info: Dict[str, float]) -> None:
                total_unique = max(1, int(info["unique_total"]))
                processed_unique = int(info["processed_unique"])
                progress = min(1.0, processed_unique / total_unique)

                progress_bar.progress(
                    progress,
                    text=(
                        f"Đang xử lý {processed_unique:,}/"
                        f"{int(info['unique_total']):,} địa chỉ unique"
                    ),
                )
                live_elapsed.metric("Đang chạy", f"{float(info['elapsed_seconds']):.1f}s")
                live_processed.metric("Đã xử lý unique", f"{processed_unique:,}")
                live_cache.metric("Cache hit", f"{int(info['cache_hits']):,}")
                live_caption.caption(
                    f"Còn lại: {int(info['remaining_unique']):,} • "
                    f"Compute mới: {int(info['computed']):,} • "
                    f"Lỗi: {int(info['invalid']):,} • "
                    f"Có geocoder: {int(info['used_geocoder']):,}"
                )

            with st.spinner("Đang chuẩn hóa..."):
                df_out, summary = convert_dataframe_address_column(
                    df_in,
                    address_col=address_col,
                    short_name=short_name,
                    max_workers=int(max_workers),
                    db_path=cache_db_path,
                    output_prefix="converted_",
                    chunk_size=int(chunk_size),
                    progress_callback=on_progress,
                    parse_mode=mode,
                    keep_street=keep_street,
                    level=int(level),
                )

            progress_bar.progress(1.0, text="Hoàn tất batch")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Tổng dòng", f"{summary['total_rows']:,}")
            m2.metric("Địa chỉ unique", f"{summary['unique_rows']:,}")
            m3.metric("Cache hit", f"{summary['cache_hits']:,}")
            m4.metric("Thời gian", f"{summary['duration_seconds']}s")

            st.caption(
                f"Dòng lỗi: {summary['error_rows']:,} • "
                f"Dòng có geocoder: {summary['used_geocoder']:,} • "
                f"Worker: {int(max_workers)} • Chunk: {int(chunk_size)}"
            )

            st.success("✅ Xong!")
            st.dataframe(df_out.head(50), use_container_width=True)
            st.download_button(
                "⬇️ Tải kết quả (CSV)",
                df_out.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                "converted_addresses.csv",
                "text/csv",
            )

        except Exception as exc:
            st.error(f"❌ Lỗi batch: {type(exc).__name__}: {exc}")
            st.exception(exc)

    if run_rev:
        try:
            gc = load_gc()
            with st.spinner("Đang reverse geocode (tối đa ~1 req/giây)…"):
                lat_col = cols_lower["latitude"]
                lon_col = cols_lower["longitude"]
                rows = []

                for _, r in df_preview.iterrows():
                    try:
                        lat = float(str(r[lat_col]).replace(",", "."))
                        lon = float(str(r[lon_col]).replace(",", "."))
                    except Exception:
                        rows.append(
                            {
                                **r.to_dict(),
                                "formatted": "",
                                "ward": "",
                                "province": "",
                                "road": "",
                                "house_number": "",
                            }
                        )
                        continue

                    res = gc.geocode(lat, lon) or {}
                    rows.append(
                        {
                            **r.to_dict(),
                            "house_number": res.get("house_number", ""),
                            "road": res.get("road", ""),
                            "ward": res.get("ward", ""),
                            "province": res.get("province", ""),
                            "formatted": res.get("formatted", ""),
                        }
                    )
                    time.sleep(1.1)

            df_rev = pd.DataFrame(rows)
            st.success("✅ Xong!")
            st.dataframe(df_rev.head(50), use_container_width=True)
            st.download_button(
                "⬇️ Tải kết quả Reverse (CSV)",
                df_rev.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                "reverse_geocoded.csv",
                "text/csv",
            )
        except Exception as exc:
            st.error(f"❌ Lỗi reverse batch: {type(exc).__name__}: {exc}")
            st.exception(exc)
