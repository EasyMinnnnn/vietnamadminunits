# app.py
import streamlit as st
import pandas as pd
from typing import Dict, Any

from vietnamadminunits import parse_address, convert_address, ParseMode
from vietnamadminunits.pandas import convert_address_column, standardize_admin_unit_columns

# ---------------- Theme & CSS ----------------
st.set_page_config(page_title="Chuẩn hóa địa chỉ Việt Nam", layout="wide")

PRIMARY = "#066E68"   # emerald
PRIMARY_DARK = "#0C5B57"
PRIMARY_MID  = "#0E6963"
GOLD    = "#D7C187"
WHITE   = "#FFFFFF"

st.markdown(f"""
<style>
/* NỀN CHUNG */
.stApp {{
  background: linear-gradient(180deg, {PRIMARY_MID} 0%, {PRIMARY_DARK} 100%);
  color: {WHITE};
  --shadow: 0 8px 24px rgba(0,0,0,0.25);
  --radius-xl: 16px;
  --radius-lg: 12px;
  --radius-md: 10px;
}}

/* HERO (đầu trang) */
.hero {{
  position: relative;
  background: linear-gradient(180deg, {PRIMARY} 0%, {PRIMARY_MID} 100%);
  padding: 28px 28px 24px 28px;
  border-bottom-left-radius: 18px;
  border-bottom-right-radius: 18px;
  box-shadow: var(--shadow);
  margin-bottom: 28px;
}}
.hero:before {{
  content: "";
  position: absolute;
  left: 24px;
  right: 24px;
  top: 8px;
  height: 6px;
  background: {GOLD};
  border-radius: 6px;
}}
.hero h1 {{
  color: {WHITE};
  font-weight: 800;
  letter-spacing: .3px;
  margin: 0 0 8px 0;
}}
.hero p {{
  margin: 0;
  color: #EAF7F6;
  opacity: .95;
}}

/* SIDEBAR */
[data-testid="stSidebar"] > div:first-child {{
  background: {PRIMARY_MID};
}}
section[data-testid="stSidebar"] h2, section[data-testid="stSidebar"] h3 {{
  color: {GOLD};
}}

/* CARD SECTION */
.card {{
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow);
  padding: 18px 18px 16px 18px;
  margin-bottom: 18px;
}}
.card .card-title {{
  color: {WHITE};
  font-weight: 700;
  margin-bottom: 12px;
}}

/* INPUT */
.stTextInput input, .stSelectbox div[data-baseweb="select"] > div,
.stTextArea textarea {{
  background: #ffffff !important;
  color: #000 !important;
  border-radius: var(--radius-lg) !important;
  border: 1px solid #E6E6E6 !important;
  height: 44px;
}}
.stNumberInput input {{
  background: #ffffff !important;
  color: #000 !important;
  border-radius: var(--radius-lg) !important;
  border: 1px solid #E6E6E6 !important;
}}

/* BUTTON */
.stButton > button {{
  background: {GOLD} !important;
  color: #000 !important;
  border: 0;
  border-radius: var(--radius-lg);
  font-weight: 800;
  padding: 10px 16px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.18);
}}
.stButton > button:hover {{ filter: brightness(0.96); }}
.stButton > button:active {{ transform: translateY(1px); }}

/* DATAFRAME header */
[data-testid="stTable"] thead tr th, .stDataFrame thead tr th {{
  background: {PRIMARY} !important;
  color: {WHITE} !important;
  font-weight: 700 !important;
}}
/* ALERT */
.stAlert.success {{ background: rgba(6,110,104,.18) !important; border-left: 4px solid {GOLD} !important; }}
.stAlert.warning {{ background: rgba(192,126,0,.18) !important; border-left: 4px solid #C07E00 !important; }}
.stAlert.error   {{ background: rgba(160,0,0,.18) !important;   border-left: 4px solid #A00000 !important; }}

/* GIÃN CÁCH */
.block-container {{ padding-top: 0.6rem; }}
</style>
""", unsafe_allow_html=True)

# ---------------- HERO ----------------
st.markdown(
    f"""
    <div class="hero">
      <h1>📍 Công cụ chuẩn hóa địa chỉ Việt Nam</h1>
      <p>Chuẩn hóa & chuyển đổi địa chỉ theo cấu trúc 63 ⇄ 34 tỉnh. Màu sắc đồng bộ emerald–gold.</p>
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

# ---------------- SINGLE ADDRESS ----------------
with st.container():
    st.markdown('<div class="card"><div class="card-title">🔎 Phân tích nhanh</div>', unsafe_allow_html=True)
    st.markdown("_Ví dụ: 70 nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM_")
    address_input = st.text_input("Nhập địa chỉ", "70 nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM")

    c1, c2 = st.columns([1,1])
    parse_clicked = c1.button("Phân tích địa chỉ")
    convert_clicked = c2.button("Chuẩn hóa (convert → 2025)")

    if parse_clicked:
        try:
            parsed = parse_address(address_input, mode=mode, keep_street=keep_street, level=int(level))
            if parsed:
                st.success("🎯 Phân tích thành công")
                df_parsed = to_clean_df(parsed)
                st.dataframe(df_parsed, use_container_width=True)
                if {"latitude","longitude"}.issubset(df_parsed.columns):
                    st.map(df_parsed.rename(columns={"latitude":"lat","longitude":"lon"})[["lat","lon"]])
            else:
                st.warning("⚠️ Không phân tích được địa chỉ.")
        except Exception as e:
            st.error(f"❌ Lỗi phân tích: {e}")
            st.info("Gợi ý: nếu bật keep_street, nên có ≥3 dấu phẩy (LEGACY) hoặc ≥2 (FROM_2025).")

    if convert_clicked:
        try:
            converted = convert_address(address_input)  # mặc định CONVERT_2025
            if converted:
                st.success("🔁 Kết quả sau chuẩn hóa (→ 2025)")
                df_converted = to_clean_df(converted)
                st.dataframe(df_converted, use_container_width=True)
                if {"latitude","longitude"}.issubset(df_converted.columns):
                    st.map(df_converted.rename(columns={"latitude":"lat","longitude":"lon"})[["lat","lon"]])
            else:
                st.warning("⚠️ Không chuẩn hóa được địa chỉ.")
        except Exception as e:
            st.error(f"⚠️ Lỗi khi chuẩn hóa: {e}")

    st.markdown('</div>', unsafe_allow_html=True)

# ---------------- BATCH CSV ----------------
with st.container():
    st.markdown('<div class="card"><div class="card-title">📦 Xử lý hàng loạt (CSV)</div>', unsafe_allow_html=True)

    if uploaded is None:
        st.caption("Tải file CSV ở sidebar để bắt đầu.")
    else:
        st.write("**Xem nhanh dữ liệu đầu vào:**")
        st.dataframe(df_preview.head(20), use_container_width=True)

        run_batch = st.button("Chạy chuẩn hóa CSV")
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
