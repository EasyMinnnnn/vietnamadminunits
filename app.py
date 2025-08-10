# app.py
import os
from typing import Dict, Any

import pandas as pd
import pydeck as pdk
import streamlit as st

from vietnamadminunits import parse_address, convert_address, ParseMode
from vietnamadminunits.pandas import convert_address_column

# ---------------- Page ----------------
st.set_page_config(page_title="Chuẩn hóa địa chỉ Việt Nam", layout="wide")

# ---------------- CSS (inject once, hidden) ----------------
CSS = """
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
<style>
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

/* Sidebar */
[data-testid="stSidebar"] > div:first-child{ background:var(--emerald-700); }
section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3{ color:var(--gold); }

/* Hero */
.hero{
  position:relative; padding:22px 26px; border-radius:var(--r-xl);
  background:linear-gradient(135deg, #0F7B74 0%, var(--emerald-700) 55%, var(--emerald-800) 100%);
  border:1px solid var(--panel-bd); box-shadow:var(--shadow); margin:8px 0 20px; overflow:hidden;
}
.hero:before{
  content:""; position:absolute; inset:0;
  background: linear-gradient(120deg, transparent 0 60%, rgba(255,255,255,.05) 62%, transparent 64%);
  pointer-events:none;
}
.hero:after{
  content:""; position:absolute; left:22px; right:22px; top:10px; height:8px;
  background:linear-gradient(90deg, var(--gold), var(--gold-hi)); border-radius:10px;
}
.hero h1{ margin:.55rem 0 .3rem; font-weight:900; letter-spacing:.2px; color:var(--gold); }
.hero p{ margin:0; color:#CFE7E5; }

/* Cards */
.card{ background:var(--panel); border:1px solid var(--panel-bd); border-radius:var(--r-lg);
       box-shadow:var(--shadow); padding:14px 16px; margin-bottom:14px; backdrop-filter:blur(6px); }
.card .card-title{ display:flex; gap:10px; align-items:center; font-weight:800; color:var(--gold); margin-bottom:8px; }
.badge{ display:inline-flex; align-items:center; justify-content:center; width:26px; height:26px; border-radius:999px; background:var(--emerald); }

/* Inputs */
.stTextInput input, .stSelectbox div[data-baseweb="select"]>div, .stTextArea textarea, .stNumberInput input{
  background:#fff !important; color:#111 !important; height:44px; border-radius:12px !important; border:1px solid #E5E7EB !important;
}

/* Buttons */
.stButton > button{
  border:0; border-radius:12px; padding:10px 16px; font-weight:800;
  box-shadow:0 6px 16px rgba(0,0,0,.18); transition:transform .05s, filter .15s;
}
.btn-primary > button{ background:linear-gradient(90deg, var(--gold), var(--gold-hi)) !important; color:#111 !important; }
.btn-ghost   > button{ background:rgba(255,255,255,.10) !important; color:#fff !important; box-shadow:none; }
.stButton > button:hover{ filter:brightness(.97); } .stButton > button:active{ transform:translateY(1px); }

/* Table */
[data-testid="stTable"] thead tr th, .stDataFrame thead tr th{
  background:var(--emerald) !important; color:var(--gold) !important; font-weight:800 !important; border-bottom:2px solid var(--gold) !important;
}
.stDataFrame{ border:1.6px solid color-mix(in srgb, var(--gold) 58%, transparent); border-radius:12px; overflow:hidden; }
.stDataFrame tbody td{ border-bottom:1px solid rgba(255,255,255,.08) !important; }

/* Alerts */
.stAlert{ border-radius:12px; }
.stAlert.success{ background:rgba(212,175,55,.10) !important; border-left:5px solid var(--gold) !important; }

/* Map frame */
.pydeck_chart, .stDeckGlJsonChart{ border-radius:12px; overflow:hidden; border:1px solid color-mix(in srgb, var(--gold) 35%, transparent); }

/* Micro spacing */
h2, h3{ letter-spacing:.1px; }
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

# ---------------- HERO ----------------
st.markdown("""
<div class="hero">
  <h1>📍 Công cụ chuẩn hóa địa chỉ Việt Nam</h1>
  <p>Chuẩn hóa & chuyển đổi địa chỉ theo cấu trúc 63 ⇄ 34 tỉnh — emerald–gold, hiện đại & chuyên nghiệp.</p>
</div>
""", unsafe_allow_html=True)

# ---------------- Sidebar ----------------
st.sidebar.header("⚙️ Tùy chọn")
mode_str = st.sidebar.selectbox("Chế độ phân tích", ["LEGACY", "FROM_2025"])
mode = ParseMode[mode_str]
keep_street = st.sidebar.checkbox("Giữ tên đường", True)
short_name = st.sidebar.checkbox("Tên rút gọn", True)
level = st.sidebar.number_input("Level", 1, 3 if mode_str == "LEGACY" else 2, 3 if mode_str == "LEGACY" else 2, step=1)
st.sidebar.markdown("---")
st.sidebar.subheader("Batch CSV")
uploaded = st.sidebar.file_uploader("Tải CSV (UTF-8)", type=["csv"])
address_col = None
if uploaded is not None:
    df_preview = pd.read_csv(uploaded)
    address_col = st.sidebar.selectbox("Chọn cột địa chỉ", list(df_preview.columns))

# ---------------- Helpers ----------------
def to_clean_df(obj: Any) -> pd.DataFrame:
    if obj is None:
        return pd.DataFrame()
    data: Dict[str, Any] = {
        k: v for k, v in getattr(obj, "__dict__", {}).items()
        if not k.startswith("_") and v is not None
    }
    order = ["province","district","ward","street","short_province","short_district","short_ward",
             "province_type","district_type","ward_type","latitude","longitude"]
    cols = [c for c in order if c in data] + [c for c in data if c not in order]
    return pd.DataFrame([{k: data.get(k) for k in cols}])

def render_map(df: pd.DataFrame):
    if {"latitude","longitude"}.issubset(df.columns) and df["latitude"].notna().any():
        lat = float(df["latitude"].iloc[0]); lon = float(df["longitude"].iloc[0])
        view = pdk.ViewState(latitude=lat, longitude=lon, zoom=10)
        style = "mapbox://styles/mapbox/dark-v11" if os.getenv("MAPBOX_API_KEY") else None
        layer = pdk.Layer("ScatterplotLayer",
                          data=df.rename(columns={"latitude":"lat","longitude":"lon"}),
                          get_position="[lon, lat]", get_radius=220, pickable=True, opacity=0.9)
        st.pydeck_chart(pdk.Deck(layers=[layer], initial_view_state=view, map_style=style), use_container_width=True)

# ---------------- Single address ----------------
st.markdown('<div class="card"><div class="card-title"><span class="badge">🔎</span> Phân tích nhanh</div>', unsafe_allow_html=True)
st.caption("Ví dụ: 70 nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM")
address_input = st.text_input("Nhập địa chỉ", "70 nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM")

c1, c2 = st.columns([1,1])
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
            render_map(df_parsed)
        else:
            st.warning("⚠️ Không phân tích được địa chỉ.")
    except Exception as e:
        st.error(f"❌ Lỗi phân tích: {e}")

if convert_clicked:
    try:
        converted = convert_address(address_input)
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

# ---------------- Batch CSV ----------------
st.markdown('<div class="card"><div class="card-title"><span class="badge">📦</span> Xử lý hàng loạt (CSV)</div>', unsafe_allow_html=True)
if uploaded is None:
    st.caption("Tải file CSV ở sidebar để bắt đầu.")
else:
    st.write("**Xem nhanh dữ liệu đầu vào:**")
    st.dataframe(df_preview.head(20), use_container_width=True)

    st.markdown('<div class="btn-primary">', unsafe_allow_html=True)
    run_batch = st.button("⚙️ Chạy chuẩn hóa CSV")
    st.markdown('</div>', unsafe_allow_html=True)

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
                "converted_addresses.csv",
                "text/csv",
            )
        except Exception as e:
            st.error(f"❌ Lỗi batch: {e}")
st.markdown('</div>', unsafe_allow_html=True)
