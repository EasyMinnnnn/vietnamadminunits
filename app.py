# app.py
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional

import pandas as pd
import pydeck as pdk
import streamlit as st
import unicodedata
from pydeck.data_utils import viewport_helpers as vh

from vietnamadminunits import parse_address, convert_address, ParseMode
from batch_sqlite_cache import (
    DEFAULT_CACHE_DB,
    convert_dataframe_address_column,
    ensure_cache_schema,
)

# ===== Geocoder (OSM + ranh xã)
from geocode_tool import Geocoder

st.set_page_config(page_title="Chuẩn hóa địa chỉ Việt Nam", layout="wide")

CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap');
:root{
  --gold:#D4AF37; --gold-hi:#FFD700;
  --emerald-900:#083D3B; --emerald-800:#0A4D4A; --emerald-700:#0E6963; --emerald:#066E68;
  --panel:rgba(255,255,255,.045); --panel-bd:rgba(255,255,255,.09);
  --shadow:0 14px 36px rgba(0,0,0,.26);
  --r:14px; --r-lg:18px; --r-xl:22px;
}
html, body, [class*="css"]{ font-family:Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
.stApp{ background: radial-gradient(1200px 600px at 15% -10%, #0D5A56 0%, #0A4D4A 58%, #083D3B 100%); color:#F3FBFA; }
.block-container{ max-width:1180px; padding-top:.75rem; }
[data-testid="stSidebar"] > div:first-child{ background:var(--emerald-700); padding-top:8px; }
section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3{ color:var(--gold); }
.brand-box{ border:2px solid var(--gold); border-radius:12px; padding:10px 12px; text-align:center; color:var(--gold); font-weight:900; letter-spacing:.6px; margin:2px 8px 14px; }
.hero{ position:relative; padding:22px 26px; border-radius:var(--r-xl); background:linear-gradient(135deg, #0F7B74 0%, var(--emerald-700) 55%, var(--emerald-800) 100%); border:1px solid var(--panel-bd); box-shadow:var(--shadow); margin:8px 0 20px; overflow:hidden; }
.hero:before{ content:""; position:absolute; inset:0; background: linear-gradient(120deg, transparent 0 60%, rgba(255,255,255,.05) 62%, transparent 64%); pointer-events:none; }
.hero:after{ content:""; position:absolute; left:22px; right:22px; top:10px; height:8px; background:linear-gradient(90deg, var(--gold), var(--gold-hi)); border-radius:10px; }
.hero h1{ margin:.55rem 0 .3rem; font-weight:900; letter-spacing:.2px; color:var(--gold); }
.hero p{ margin:0; color:#CFE7E5; }
.card{ background:var(--panel); border:1px solid var(--panel-bd); border-radius:var(--r-lg); box-shadow:var(--shadow); padding:14px 16px; margin-bottom:14px; backdrop-filter:blur(6px); }
.card .card-title{ display:flex; gap:10px; align-items:center; font-weight:800; color:var(--gold); margin-bottom:8px; }
.badge{ display:inline-flex; align-items:center; justify-content:center; width:26px; height:26px; border-radius:999px; background:var(--emerald); }
.stTextInput input, .stSelectbox div[data-baseweb="select"]>div, .stTextArea textarea, .stNumberInput input{ background:#fff !important; color:#111 !important; height:44px; border-radius:12px !important; border:1px solid #E5E7EB !important; }
.stButton > button{ border:0; border-radius:12px; padding:10px 16px; font-weight:800; box-shadow:0 6px 16px rgba(0,0,0,.18); transition:transform .05s, filter .15s; }
.btn-primary > button{ background:linear-gradient(90deg, var(--gold), var(--gold-hi)) !important; color:#111 !important; }
.btn-ghost > button{ background:rgba(255,255,255,.10) !important; color:#fff !important; box-shadow:none; }
.stButton > button:hover{ filter:brightness(.97); } .stButton > button:active{ transform:translateY(1px); }
[data-testid="stTable"] thead tr th, .stDataFrame thead tr th{ background:var(--emerald) !important; color:var(--gold) !important; font-weight:800 !important; border-bottom:2px solid var(--gold) !important; }
.stDataFrame{ border:1.6px solid color-mix(in srgb, var(--gold) 58%, transparent); border-radius:12px; overflow:hidden; }
.stDataFrame tbody td{ border-bottom:1px solid rgba(255,255,255,.08) !important; }
.stAlert{ border-radius:12px; }
.stAlert.success{ background:rgba(212,175,55,.10) !important; border-left:5px solid var(--gold) !important; }
.pydeck_chart, .stDeckGlJsonChart{ border-radius:12px; overflow:hidden; border:1px solid color-mix(in srgb, var(--gold) 35%, transparent); }
h2, h3{ letter-spacing:.1px; }
</style>"""
st.markdown(CSS, unsafe_allow_html=True)
st.markdown("""
<div class="hero">
  <h1>📍Công cụ chuyển đổi địa giới hành chính</h1>
</div>
""", unsafe_allow_html=True)

if "last_points" not in st.session_state:
    st.session_state["last_points"] = None


def _score_vn(text: str) -> float:
    vn_chars = "ăâđêôơưĂÂĐÊÔƠƯàáảãạằắẳẵặầấẩẫẬèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ"
    has_vn = sum(ch in vn_chars for ch in text)
    combining = sum(unicodedata.combining(ch) != 0 for ch in text)
    qmarks = text.count("?")
    return has_vn * 3 + combining * 2 - qmarks * 2


def _read_csv_with_fallback(file, encoding_mode: str = "auto") -> pd.DataFrame:
    if encoding_mode and encoding_mode.lower() != "auto":
        file.seek(0)
        return pd.read_csv(file, encoding=encoding_mode)
    candidates = ["utf-8-sig", "utf-8", "cp1258", "cp1252", "latin1", "utf-16", "utf-16-le", "utf-16-be"]
    best_df, best_score, best_enc = None, -1e9, None
    errs = []
    for enc in candidates:
        try:
            file.seek(0)
            df = pd.read_csv(file, encoding=enc)
            sample = "\n".join(df.astype(str).head(5).apply(lambda r: " ".join(map(str, r.values)), axis=1).tolist())
            s = _score_vn(sample)
            if s > best_score:
                best_df, best_score, best_enc = df, s, enc
        except Exception as e:
            errs.append(f"{enc}: {e}")
    if best_df is None:
        raise UnicodeDecodeError("utf-8", b"", 0, 1, f"Không decode được CSV. Tried: {errs}")
    st.session_state["_detected_encoding"] = best_enc
    return best_df


def _read_excel(file, sheet_name: Optional[str] = None) -> pd.DataFrame:
    file.seek(0)
    if sheet_name:
        return pd.read_excel(file, sheet_name=sheet_name)
    xls = pd.ExcelFile(file)
    first = xls.sheet_names[0]
    file.seek(0)
    return pd.read_excel(file, sheet_name=first)


def load_table(uploaded, encoding_choice: str = "auto", excel_sheet: Optional[str] = None) -> pd.DataFrame:
    ext = Path(uploaded.name).suffix.lower()
    if ext == ".csv":
        return _read_csv_with_fallback(uploaded, encoding_choice)
    if ext in (".xls", ".xlsx"):
        return _read_excel(uploaded, sheet_name=excel_sheet)
    raise ValueError("Định dạng không hỗ trợ. Hỗ trợ: CSV, XLS, XLSX.")


def normalize_text_column(df: pd.DataFrame, column: str) -> pd.DataFrame:
    out = df.copy()
    out[column] = out[column].astype(str).str.strip().map(lambda s: unicodedata.normalize("NFC", s))
    return out


def to_clean_df(obj: Any) -> pd.DataFrame:
    if obj is None:
        return pd.DataFrame()
    data: Dict[str, Any] = {k: v for k, v in getattr(obj, "__dict__", {}).items() if not k.startswith("_") and v is not None}
    order = ["province","district","ward","street","short_province","short_district","short_ward","province_type","district_type","ward_type","latitude","longitude"]
    cols = [c for c in order if c in data] + [c for c in data if c not in order]
    return pd.DataFrame([{k: data.get(k) for k in cols}])


def _normalize_points(df: pd.DataFrame, lat_col="latitude", lon_col="longitude") -> pd.DataFrame:
    df = df.rename(columns={lat_col: "lat", lon_col: "lon"}).copy()
    for c in ["lat", "lon"]:
        s = df[c].astype(str).str.replace(",", ".", regex=False)
        s_num = s.str.extract(r"(-?\d+(?:\.\d+)?)", expand=False)
        df[c] = pd.to_numeric(s_num, errors="coerce")
    df = df[df["lat"].between(-90, 90) & df["lon"].between(-180, 180)]
    return df.dropna(subset=["lat", "lon"]).reset_index(drop=True)


def render_map(df: pd.DataFrame, lat_col="latitude", lon_col="longitude", label_col=None, point_radius=50):
    if df is None or df.empty:
        return
    pts = _normalize_points(df, lat_col, lon_col)
    if pts.empty:
        st.warning("Không có toạ độ hợp lệ để hiển thị.")
        return
    view = vh.compute_view(pts[["lon", "lat"]])
    view.zoom = max(min(view.zoom, 15) - 1, 5)
    layers = [pdk.Layer("ScatterplotLayer", data=pts, get_position='[lon, lat]', get_radius=point_radius, get_fill_color='[255, 77, 77, 200]', pickable=True)]
    if label_col and label_col in pts.columns:
        layers.append(pdk.Layer("TextLayer", data=pts, get_position='[lon, lat]', get_text=f'[{label_col}]', get_size=14, get_color='[0,0,0,255]', get_alignment_baseline='"top"'))
    deck = pdk.Deck(layers=layers, initial_view_state=view, map_provider="carto", map_style="light", tooltip={"text": "{lat}, {lon}"})
    st.pydeck_chart(deck, use_container_width=True)


@st.cache_resource(show_spinner=False)
def load_gc():
    CSV_PATH = "data/interim/legacy_63-province-10040-ward_with_location_and_key.csv"
    path = CSV_PATH
    try:
        return Geocoder(csv_path_or_url=path)
    except TypeError:
        try:
            return Geocoder(csv_path=path)
        except TypeError:
            return Geocoder(path)


st.sidebar.markdown('<div class="brand-box">TTĐGTS</div>', unsafe_allow_html=True)
st.sidebar.header("⚙️ Tùy chọn")
mode_str = st.sidebar.selectbox("Chế độ phân tích", ["LEGACY", "FROM_2025"])
mode = ParseMode[mode_str]
keep_street = st.sidebar.checkbox("Giữ tên đường", True)
short_name = st.sidebar.checkbox("Tên rút gọn", True)
level = st.sidebar.number_input("Level", 1, 3 if mode_str == "LEGACY" else 2, 3 if mode_str == "LEGACY" else 2, step=1)

st.sidebar.markdown("---")
st.sidebar.subheader("Batch (CSV/Excel)")
worker_default = max(1, min((os.cpu_count() or 2) - 1, 4))
max_workers = st.sidebar.number_input("Số worker batch", min_value=1, max_value=max(1, os.cpu_count() or 4), value=worker_default, step=1)
cache_db_path = st.sidebar.text_input("SQLite cache", value=str(DEFAULT_CACHE_DB))
ensure_cache_schema(cache_db_path)
st.sidebar.caption("Batch sẽ chỉ chuẩn hóa cột địa chỉ và dùng SQLite cache giữa nhiều lần chạy.")

uploaded = st.sidebar.file_uploader("Tải CSV/Excel", type=["csv", "xlsx", "xls"])
address_col = None
df_preview = None
excel_sheet = None

if uploaded is not None:
    ext = Path(uploaded.name).suffix.lower()
    if ext == ".csv":
        encoding_choice = st.sidebar.selectbox("Encoding (CSV)", ["auto", "utf-8-sig", "utf-8", "latin1", "cp1258", "cp1252"], index=0)
        try:
            df_preview = load_table(uploaded, encoding_choice)
        except Exception as e:
            st.sidebar.error(f"Lỗi đọc CSV: {e}")
    else:
        try:
            uploaded.seek(0)
            xls = pd.ExcelFile(uploaded)
            excel_sheet = st.sidebar.selectbox("Chọn sheet", xls.sheet_names, index=0)
            df_preview = load_table(uploaded, excel_sheet=excel_sheet)
        except Exception as e:
            st.sidebar.error(f"Lỗi đọc Excel: {e}")
    if df_preview is not None:
        enc_msg = st.session_state.get("_detected_encoding")
        if enc_msg:
            st.sidebar.caption(f"Detected: **{enc_msg}**")
        address_col = st.sidebar.selectbox("Chọn cột địa chỉ", list(df_preview.columns))

st.markdown('<div class="card"><div class="card-title"><span class="badge">🔎</span> Phân tích nhanh</div>', unsafe_allow_html=True)
st.caption("Ví dụ: 194 Trần Quang Khải, phường Lý Thái Tổ, quận Hoàn Kiếm, Hà Nội")
address_input = st.text_input("Nhập địa chỉ", "194 Trần Quang Khải, phường Lý Thái Tổ, quận Hoàn Kiếm, Hà Nội")
c1, c2 = st.columns([1, 1])
with c1:
    st.markdown('<div class="btn-primary">', unsafe_allow_html=True)
    parse_clicked = st.button("Phân tích địa chỉ")
    st.markdown('</div>', unsafe_allow_html=True)
with c2:
    st.markdown('<div class="btn-ghost">', unsafe_allow_html=True)
    convert_clicked = st.button("Chuẩn hóa (→ 2025)")
    st.markdown('</div>', unsafe_allow_html=True)

if parse_clicked:
    try:
        parsed = parse_address(address_input, mode=mode, keep_street=keep_street, level=int(level))
        if parsed:
            st.success("🎯 Phân tích thành công")
            df_parsed = to_clean_df(parsed)
            st.dataframe(df_parsed, use_container_width=True)
            st.session_state["last_points"] = df_parsed[["latitude", "longitude"]]
        else:
            st.warning("⚠️ Không phân tích được địa chỉ.")
    except Exception as e:
        st.error(f"❌ Lỗi phân tích: {type(e).__name__}: {e}")
        st.exception(e)

if convert_clicked:
    try:
        converted = convert_address(address_input)
        if converted:
            st.success("🔁 Kết quả sau chuẩn hóa (→ 2025)")
            df_converted = to_clean_df(converted)
            st.dataframe(df_converted, use_container_width=True)
        else:
            st.warning("⚠️ Không chuẩn hóa được địa chỉ.")
    except Exception as e:
        st.error(f"⚠️ Lỗi khi chuẩn hóa: {type(e).__name__}: {e}")
        st.exception(e)

with st.expander("🧭 Kiểm tra tọa độ (OSM + ranh xã)"):
    c3, c4, c5 = st.columns([1, 1, .6])
    with c3:
        lat_in = st.number_input("Latitude", value=21.028, format="%.8f")
    with c4:
        lon_in = st.number_input("Longitude", value=105.834, format="%.8f")
    with c5:
        st.markdown('<div class="btn-primary">', unsafe_allow_html=True)
        rev_clicked = st.button("Reverse")
        st.markdown('</div>', unsafe_allow_html=True)
    if rev_clicked:
        try:
            gc = load_gc()
            res = gc.geocode(float(lat_in), float(lon_in))
            if res:
                st.success("✅ Đã xác định địa chỉ")
                show = {"house_number": res.get("house_number"), "road": res.get("road"), "ward": res.get("ward"), "province": res.get("province"), "latitude": res.get("latitude"), "longitude": res.get("longitude"), "formatted": res.get("formatted")}
                st.dataframe(pd.DataFrame([show]), use_container_width=True)
                st.session_state["last_points"] = pd.DataFrame([{"latitude": show["latitude"], "longitude": show["longitude"]}])
            else:
                st.warning("⚠️ Không xác định được xã/phường hoặc OSM thiếu số nhà/đường.")
        except Exception as e:
            st.error(f"❌ Lỗi reverse: {type(e).__name__}: {e}")
            st.exception(e)

if st.session_state.get("last_points") is not None:
    render_map(st.session_state["last_points"])
st.markdown('</div>', unsafe_allow_html=True)

st.markdown('<div class="card"><div class="card-title"><span class="badge">📦</span> Xử lý hàng loạt</div>', unsafe_allow_html=True)
if uploaded is None or df_preview is None:
    st.caption("Tải file CSV/Excel ở sidebar để bắt đầu.")
else:
    st.write("**Xem nhanh dữ liệu đầu vào:**")
    st.dataframe(df_preview.head(20), use_container_width=True)
    st.markdown('<div class="btn-primary">', unsafe_allow_html=True)
    run_batch = st.button("⚙️ Chạy chuẩn hóa")
    st.markdown('</div>', unsafe_allow_html=True)

    cols_lower = {c.lower(): c for c in df_preview.columns}
    has_latlon = ("latitude" in cols_lower and "longitude" in cols_lower)
    if has_latlon:
        st.markdown('<div class="btn-ghost">', unsafe_allow_html=True)
        run_rev = st.button("🧭 Reverse geocode (lat, lon)")
        st.markdown('</div>', unsafe_allow_html=True)
    else:
        run_rev = False

    if run_batch and address_col:
        try:
            df_in = normalize_text_column(df_preview, address_col)
            with st.spinner("Đang chuẩn hóa..."):
                df_out, summary = convert_dataframe_address_column(
                    df_in,
                    address_col=address_col,
                    short_name=short_name,
                    max_workers=int(max_workers),
                    db_path=cache_db_path,
                    output_prefix="converted_",
                )
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Tổng dòng", f"{summary['total_rows']:,}")
            m2.metric("Địa chỉ unique", f"{summary['unique_rows']:,}")
            m3.metric("Cache hit", f"{summary['cache_hits']:,}")
            m4.metric("Thời gian", f"{summary['duration_seconds']}s")
            st.caption(f"Dòng lỗi: {summary['error_rows']:,} • Dòng có geocoder: {summary['used_geocoder']:,} • Worker: {int(max_workers)}")
            st.success("✅ Xong!")
            st.dataframe(df_out.head(50), use_container_width=True)
            st.download_button(
                "⬇️ Tải kết quả (CSV)",
                df_out.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig"),
                "converted_addresses.csv",
                "text/csv",
            )
        except Exception as e:
            st.error(f"❌ Lỗi batch: {type(e).__name__}: {e}")
            st.exception(e)

    if run_rev:
        try:
            gc = load_gc()
            with st.spinner("Đang reverse geocode (tối đa ~1 req/giây)…"):
                lat_col = cols_lower["latitude"]
                lon_col = cols_lower["longitude"]
                rows = []
                for _, r in df_preview.iterrows():
                    try:
                        lat = float(r[lat_col]); lon = float(r[lon_col])
                    except Exception:
                        rows.append({**r.to_dict(), "formatted": "", "ward": "", "province": "", "road": "", "house_number": ""})
                        continue
                    res = gc.geocode(lat, lon) or {}
                    rows.append({
                        **r.to_dict(),
                        "house_number": res.get("house_number", ""),
                        "road": res.get("road", ""),
                        "ward": res.get("ward", ""),
                        "province": res.get("province", ""),
                        "formatted": res.get("formatted", ""),
                    })
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
        except Exception as e:
            st.error(f"❌ Lỗi reverse batch: {type(e).__name__}: {e}")
            st.exception(e)

st.markdown('</div>', unsafe_allow_html=True)
