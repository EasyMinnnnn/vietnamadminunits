# app.py
import os
from string import Template
from typing import Dict, Any

import pandas as pd
import pydeck as pdk
import streamlit as st
from streamlit.components.v1 import html

from vietnamadminunits import parse_address, convert_address, ParseMode
from vietnamadminunits.pandas import convert_address_column, standardize_admin_unit_columns  # noqa

# ---------------- BASIC SETUP ----------------
st.set_page_config(page_title="Chuẩn hóa địa chỉ Việt Nam", layout="wide")

# Brand palette (BIDV-like)
GOLD          = "#D4AF37"  # gold đậm kiểu BIDV
EMERALD_900   = "#0B4F4B"
EMERALD_800   = "#0E6963"
EMERALD_700   = "#066E68"
EMERALD_600   = "#0F7B74"
TEXT_LIGHT    = "#F3FBFA"
TEXT_MUTED    = "#CEEDEA"

# ---------------- CSS (inject via components.html) ----------------
css_tpl = Template("""
<style>
:root{
  --bg: linear-gradient(180deg, ${EMERALD_800} 0%, ${EMERALD_900} 100%);
  --panel: rgba(255,255,255,.045);
  --panel-border: rgba(255,255,255,.08);
  --shadow: 0 12px 32px rgba(0,0,0,.28);
  --r-lg: 14px; --r-xl: 18px;
}

html, body, [class*="css"]{ font-family: 'Inter', system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
.stApp{ background: var(--bg); color: ${TEXT_LIGHT}; }

/* Sidebar */
[data-testid="stSidebar"] > div:first-child{ background: ${EMERALD_800}; }
section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3{ color: ${GOLD}; }

/* HERO (có thanh vàng) */
.hero{
  position:relative; padding:20px 22px 18px 22px;
  background: linear-gradient(180deg, ${EMERALD_600} 0%, ${EMERALD_800} 100%);
  border-radius: var(--r-xl); box-shadow: var(--shadow); margin: 8px 0 18px 0;
  border: 1px solid var(--panel-border);
}
.hero:before{
  content:""; position:absolute; left:18px; right:18px; top:8px; height:6px;
  background:${GOLD}; border-radius:8px;
}
.hero h1{ margin:6px 0 4px 0; color:${GOLD}; font-weight:900; letter-spacing:.3px; }
.hero p{ margin:0; color:${TEXT_MUTED}; }

/* Layout */
.block-container{ max-width: 1100px; margin: 0 auto; padding-top: .6rem; }

/* Cards (glass) */
.card{
  background: var(--panel); border: 1px solid var(--panel-border);
  border-radius: var(--r-xl); box-shadow: var(--shadow);
  padding: 16px; margin-bottom: 16px; backdrop-filter: blur(6px);
}
.card .card-title{ display:flex; align-items:center; gap:10px; font-weight:800; margin-bottom:10px; color:${GOLD}; }
.badge{ display:inline-block; padding:4px 10px; border-radius:999px; background:${EMERALD_700}; color:#fff; font-size:12px; font-weight:800; }

/* Inputs */
.stTextInput input, .stSelectbox div[data-baseweb="select"] > div,
.stTextArea textarea, .stNumberInput input{
  background:#fff !important; color:#000 !important; height:44px;
  border-radius:12px !important; border:1px solid #E6E6E6 !important;
}

/* Buttons (gold primary) */
.stButton > button{
  background: ${GOLD} !important; color:#000 !important; border:0;
  border-radius:12px; font-weight:900; padding:10px 16px;
  box-shadow: 0 6px 16px rgba(0,0,0,.18); transition: transform .05s, filter .15s;
}
.stButton > button:hover{ filter:brightness(.97); }
.stButton > button:active{ transform: translateY(1px); }

/* Dataframe styling: header emerald, viền nhấn gold */
[data-testid="stTable"] thead tr th, .stDataFrame thead tr th{
  background:${EMERALD_700} !important; color:${GOLD} !important; font-weight:800 !important;
  border-bottom: 2px solid ${GOLD} !important;
}
.stDataFrame tbody td{ border-bottom: 1px solid rgba(255,255,255,.06) !important; }
.stDataFrame{ border: 1px solid ${GOLD}22; border-radius: 12px; overflow: hidden; }

/* Alerts với viền vàng */
.stAlert{ border-radius:12px; }
.stAlert.success{ background: rgba(212,175,55,.10) !important; border-left: 4px solid ${GOLD} !important; }
.stAlert.warning{ background: rgba(192,126,0,.12) !important; border-left: 4px solid #C07E00 !important; }
.stAlert.error  { background: rgba(160,0,0,.12) !important;   border-left: 4px solid #A00000 !important; }

/* Skeleton shimmer */
.skel{
  background: linear-gradient(90deg, rgba(255,255,255,.08) 25%, rgba(255,255,255,.16) 40%, rgba(255,255,255,.08) 65%);
  border-radius: 10px; height: 40px; animation: shimmer 1.1s infinite;
}
@keyframes shimmer { 0%{background-position:-280px 0} 100%{background-position:280px 0} }

/* Map */
.pydeck_chart, .stDeckGlJsonChart{ border-radius: 12px; overflow:hidden; border:1px solid ${GOLD}22; }
</style>
""")

if not st.session_state.get("css_loaded"):
    html(css_tpl.substitute(
        GOLD=GOLD, EMERALD_700=EMERALD_700, EMERALD_800=EMERALD_800,
        EMERALD_900=EMERALD_900, EMERALD_600=EMERALD_600,
        TEXT_LIGHT=TEXT_LIGHT, TEXT_MUTED=TEXT_MUTED
    ), height=0, width=0)
    st.session_state["css_loaded"] = True

# ---------------- HERO ----------------
st.markdown(
    """
    <div class="hero">
      <h1>📍 Công cụ chuẩn hóa địa chỉ Việt Nam</h1>
      <p>Chuẩn hóa & chuyển đổi địa chỉ theo cấu trúc 63 ⇄ 34 tỉnh — emerald–gold UI chuẩn BIDV.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------- SIDEBAR ----------------
st.sidebar.header("⚙️ Tùy chọn")
mode_str = st.sidebar.selectbox("Chế độ phân tích", ["LEGACY", "FROM_2025"])
mode = ParseMode[mode_str]
keep_street = st.sidebar.checkbox("Giữ tên đường (keep_street)", True)
short_name = st.sidebar.checkbox("Tên rút gọn (short_name)", True)
level = st.sidebar.number_input(
    "Level", min_value=1, max_value=3 if mode_str == "LEGACY" else 2,
    value=3 if mode_str == "LEGACY" else 2, step=1,
)
st.sidebar.markdown("---")
st.sidebar.subheader("Batch CSV")
uploaded = st.sidebar.file_uploader("Tải CSV (UTF-8)", type=["csv"])
address_col = None
if uploaded is not None:
    df_preview = pd.read_csv(uploaded)
    cols = list(df_preview.columns)
    address_col = st.sidebar.selectbox("Chọn cột địa chỉ", cols)

# ---------------- HELPERS ----------------
def to_clean_df(obj: Any, order_hint: list[str] | None = None) -> pd.DataFrame:
    if obj is None: return pd.DataFrame()
    data: Dict[str, Any] = {k: v for k, v in getattr(obj, "__dict__", {}).items()
                            if not k.startswith("_") and v is not None}
    default_order = ["province","district","ward","street",
                     "short_province","short_district","short_ward",
                     "province_type","district_type","ward_type",
                     "latitude","longitude"]
    if order_hint:
        default_order = order_hint + [c for c in default_order if c not in order_hint]
    ordered = [c for c in default_order if c in data] + [c for c in data if c not in default_order]
    return pd.DataFrame([{k: data.get(k) for k in ordered}])

def render_map(df: pd.DataFrame, lat_col="latitude", lon_col="longitude"):
    if {lat_col, lon_col}.issubset(df.columns) and df[lat_col].notna().any():
        lat = float(df[lat_col].iloc[0]); lon = float(df[lon_col].iloc[0])
        view = pdk.ViewState(latitude=lat, longitude=lon, zoom=10)
        style = "mapbox://styles/mapbox/dark-v11" if os.getenv("MAPBOX_API_KEY") else None
        layer = pdk.Layer(
            "ScatterplotLayer",
            data=df.rename(columns={lat_col:"lat", lon_col:"lon"}),
            get_position="[lon, lat]",
            get_radius=220, pickable=True, opacity=0.9,
        )
        st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view, map_style=style), use_container_width=True)

# ---------------- SINGLE ADDRESS ----------------
st.markdown('<div class="card"><div class="card-title"><span class="badge">🔎</span> Phân tích nhanh</div>', unsafe_allow_html=True)
st.caption("Ví dụ: 70 nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM")
address_input = st.text_input("Nhập địa chỉ", "70 nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM")

c1, c2 = st.columns([1, 1])
parse_clicked   = c1.button("Phân tích địa chỉ")
convert_clicked = c2.button("Chuẩn hóa (→ 2025)")

if parse_clicked:
    try:
        st.markdown('<div class="skel"></div>', unsafe_allow_html=True)
        parsed = parse_address(address_input, mode=mode, keep_street=keep_street, level=int(level))
        st.empty()
        if parsed:
            st.success("🎯 Phân tích thành công")
            df_parsed = to_clean_df(parsed)
            st.dataframe(df_parsed, use_container_width=True)
            render_map(df_parsed)
        else:
            st.warning("⚠️ Không phân tích được địa chỉ.")
    except Exception as e:
        st.error(f"❌ Lỗi phân tích: {e}")
        st.info("Gợi ý: nếu bật keep_street, nên có ≥3 dấu phẩy (LEGACY) hoặc ≥2 (FROM_2025).")

if convert_clicked:
    try:
        st.markdown('<div class="skel"></div>', unsafe_allow_html=True)
        converted = convert_address(address_input)  # default CONVERT_2025
        st.empty()
        if converted:
            st.success("🔁 Kết quả sau chuẩn hóa (→ 2025)")
            df_converted = to_clean_df(converted)
            st.dataframe(df_converted, use_container_width=True)
            render_map(df_converted)
        else:
            st.warning("⚠️ Không chuẩn hóa được địa chỉ.")
    except Exception as e:
        st.error(f"⚠️ Lỗi khi chuẩn hóa: {e}")

st.markdown('</div>', unsafe_allow_html=True)

# ---------------- BATCH CSV ----------------
st.markdown('<div class="card"><div class="card-title"><span class="badge">📦</span> Xử lý hàng loạt (CSV)</div>', unsafe_allow_html=True)
if uploaded is None:
    st.caption("Tải file CSV ở sidebar để bắt đầu.")
else:
    st.write("**Xem nhanh dữ liệu đầu vào:**")
    st.dataframe(df_preview.head(20), use_container_width=True)

    run_batch = st.button("⚙️ Chạy chuẩn hóa CSV")
    if run_batch and address_col:
        try:
            with st.spinner("Đang chuẩn hóa..."):
                df_out = convert_address_column(
                    df_preview.copy(),
                    address=address_col,
                    convert_mode="CONVERT_2025",
                    inplace=False,
                    prefix="converted_",
                    suffix="",
                    short_name=short_name,
                    show_progress=True,
                )
            st.success("✅ Xong!")
            st.dataframe(df_out.head(50), use_container_width=True)
            st.download_button("⬇️ Tải kết quả (CSV)",
                               df_out.to_csv(index=False).encode("utf-8"),
                               "converted_addresses.csv", "text/csv")
        except Exception as e:
            st.error(f"❌ Lỗi batch: {e}")
            st.info("Kiểm tra encoding UTF-8 và cột địa chỉ được chọn đúng.")
st.markdown('</div>', unsafe_allow_html=True)
