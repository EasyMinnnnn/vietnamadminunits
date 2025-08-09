# app.py
import os
from string import Template
from typing import Dict, Any

import pandas as pd
import pydeck as pdk
import streamlit as st

from vietnamadminunits import parse_address, convert_address, ParseMode
from vietnamadminunits.pandas import convert_address_column, standardize_admin_unit_columns  # noqa

# ---------------- THEME / SETUP ----------------
st.set_page_config(page_title="Chuẩn hóa địa chỉ Việt Nam", layout="wide")

PRIMARY = "#066E68"       # emerald
PRIMARY_DARK = "#0C5B57"
PRIMARY_MID  = "#0E6963"
GOLD    = "#D7C187"
WHITE   = "#FFFFFF"

# ---------------- CSS / FONT (Template để tránh lỗi %) ----------------
css_tpl = Template(r"""
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
html, body, [class*="css"] { font-family: 'Inter', system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }

/* Background tổng thể */
.stApp{
  background: radial-gradient(1200px 600px at 15% 0%, #107973 0%, #0D5F5B 40%, #0C5B57 70%);
  color:#fff;
  --shadow: 0 16px 40px rgba(0,0,0,.35);
  --glass: rgba(255,255,255,.05);
  --border: rgba(255,255,255,.10);
  --radius-xl: 18px; --radius-lg: 12px;
}

/* HERO */
.hero{
  position:relative; padding:30px 28px 26px 28px;
  background: linear-gradient(180deg, #0F7B74 0%, #0E6963 100%);
  border-bottom-left-radius:22px; border-bottom-right-radius:22px;
  box-shadow: var(--shadow); margin-bottom:26px;
}
.hero:before{
  content:""; position:absolute; left:28px; right:28px; top:10px; height:7px;
  background: $GOLD; border-radius:9px;
}
.hero h1{ margin:0 0 6px 0; font-weight:800; letter-spacing:.2px; }
.hero p{ margin:0; color:#EAF7F6; opacity:.95; }

/* Sidebar */
[data-testid="stSidebar"] > div:first-child{
  background: $PRIMARY_MID;
}
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3{
  color: $GOLD;
}

/* Cards (glassmorphism) */
.card{
  background: var(--glass);
  border: 1px solid var(--border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow);
  padding: 18px;
  margin: 10px 0 22px 0;
  backdrop-filter: blur(6px);
}
.card .card-title{ font-weight:800; margin-bottom:10px; }

/* Inputs */
.stTextInput input, .stSelectbox div[data-baseweb="select"] > div,
.stTextArea textarea, .stNumberInput input{
  background:#fff !important; color:#000 !important;
  border-radius:12px !important; border:1px solid #E6E6E6 !important; height:46px;
}

/* Buttons */
.stButton > button{
  background: $GOLD !important; color:#000 !important; border:0;
  border-radius: var(--radius-lg); font-weight:800; padding:12px 18px;
  box-shadow: 0 8px 18px rgba(0,0,0,.22); transition: transform .05s ease, filter .15s ease;
}
.stButton > button:hover{ filter:brightness(.97); }
.stButton > button:active{ transform: translateY(1px); }

/* Table header */
[data-testid="stTable"] thead tr th, .stDataFrame thead tr th{
  background: $PRIMARY !important; color:#fff !important; font-weight:700 !important;
}

/* Alerts */
.stAlert.success{ background: rgba(6,110,104,.18) !important; border-left: 4px solid $GOLD !important; }
.stAlert.warning{ background: rgba(192,126,0,.18) !important; border-left: 4px solid #C07E00 !important; }
.stAlert.error  { background: rgba(160,0,0,.18) !important;   border-left: 4px solid #A00000 !important; }

/* Skeleton shimmer */
.skel{
  background: linear-gradient(90deg, rgba(255,255,255,.08) 25%, rgba(255,255,255,.15) 37%, rgba(255,255,255,.08) 63%);
  border-radius: 10px; height: 42px; animation: shimmer 1.2s infinite;
}
@keyframes shimmer { 0%{background-position:-300px 0} 100%{background-position:300px 0} }

/* Bố cục */
.block-container{ padding-top: .6rem; max-width: 1200px; margin: 0 auto; }
</style>
""")
st.markdown(css_tpl.substitute(GOLD=GOLD, PRIMARY_MID=PRIMARY_MID, PRIMARY=PRIMARY), unsafe_allow_html=True)

# ---------------- HERO ----------------
st.markdown(
    """
    <div class="hero">
      <h1>📍 Công cụ chuẩn hóa địa chỉ Việt Nam</h1>
      <p>Chuẩn hóa & chuyển đổi địa chỉ theo cấu trúc 63 ⇄ 34 tỉnh — emerald–gold UI.</p>
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
    "Level",
    min_value=1, max_value=3 if mode_str == "LEGACY" else 2,
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
    if obj is None:
        return pd.DataFrame()
    data: Dict[str, Any] = {
        k: v for k, v in getattr(obj, "__dict__", {}).items()
        if not k.startswith("_") and v is not None
    }
    default_order = [
        "province", "district", "ward", "street",
        "short_province", "short_district", "short_ward",
        "province_type", "district_type", "ward_type",
        "latitude", "longitude",
    ]
    if order_hint:
        default_order = order_hint + [c for c in default_order if c not in order_hint]
    ordered = [c for c in default_order if c in data] + [c for c in data if c not in default_order]
    return pd.DataFrame([{k: data.get(k) for k in ordered}])

def render_map(df: pd.DataFrame, lat_col="latitude", lon_col="longitude"):
    """Hiển thị pydeck map; nếu có MAPBOX_API_KEY sẽ dùng dark style."""
    if {lat_col, lon_col}.issubset(df.columns) and df[lat_col].notna().any():
        lat = float(df[lat_col].iloc[0]); lon = float(df[lon_col].iloc[0])
        view = pdk.ViewState(latitude=lat, longitude=lon, zoom=10, pitch=0)
        map_style = "mapbox://styles/mapbox/dark-v11" if os.getenv("MAPBOX_API_KEY") else None
        layer = pdk.Layer(
            "ScatterplotLayer",
            data=df.rename(columns={lat_col: "lat", lon_col: "lon"}),
            get_position="[lon, lat]",
            get_radius=200, pickable=True, opacity=0.9,
        )
        r = pdk.Deck(layers=[layer], initial_view_state=view, map_style=map_style)
        st.pydeck_chart(r, use_container_width=True)

# ---------------- SINGLE ADDRESS ----------------
st.markdown('<div class="card"><div class="card-title">🔎 Phân tích nhanh</div>', unsafe_allow_html=True)
st.markdown("_Ví dụ: 70 nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM_")
address_input = st.text_input("Nhập địa chỉ", "70 nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM")

c1, c2 = st.columns([1, 1])
parse_clicked = c1.button("🔍 Phân tích địa chỉ")
convert_clicked = c2.button("🔁 Chuẩn hóa (→ 2025)")

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
        converted = convert_address(address_input)  # mặc định CONVERT_2025
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
st.markdown('<div class="card"><div class="card-title">📦 Xử lý hàng loạt (CSV)</div>', unsafe_allow_html=True)
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
            st.download_button(
                "⬇️ Tải kết quả (CSV)",
                df_out.to_csv(index=False).encode("utf-8"),
                file_name="converted_addresses.csv",
                mime="text/csv",
            )
        except Exception as e:
            st.error(f"❌ Lỗi batch: {e}")
            st.info("Kiểm tra encoding UTF-8 và cột địa chỉ được chọn đúng.")
st.markdown('</div>', unsafe_allow_html=True)
