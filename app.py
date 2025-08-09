# app.py
import streamlit as st
import pandas as pd
from typing import Dict, Any

from vietnamadminunits import parse_address, convert_address, ParseMode
from vietnamadminunits.pandas import (
    convert_address_column,
    standardize_admin_unit_columns,
)

# ---------------- Theme & CSS injection ----------------
st.set_page_config(page_title="Chuẩn hóa địa chỉ Việt Nam", layout="wide")

PRIMARY = "#066E68"   # emerald (xanh ngọc)
GOLD    = "#D7C187"   # viền vàng nhạt
BG      = "#0C5B57"   # nền chính
BG2     = "#0E6963"   # nền khối/box

st.markdown(f"""
<style>
/* Toàn app */
.stApp {{
  background: {BG};
  color: #fff;
}}

/* Thanh top-bar (viền vàng bo tròn góc phải) */
.topbar {{
  height: 52px;
  background: {GOLD};
  border-bottom-left-radius: 14px;
}}

/* Container chính: giảm khoảng trống top để ô topbar sát nội dung */
.block-container {{
  padding-top: 0rem;
}}

/* Sidebar */
[data-testid="stSidebar"] > div:first-child {{
  background: {BG2};
}}

/* Tiêu đề */
h1, h2, h3, h4 {{
  color: {GOLD};
  font-weight: 700;
}}

/* Nút bấm */
.stButton > button {{
  background: {GOLD} !important;
  color: #000 !important;
  border: 0;
  border-radius: 10px;
  font-weight: 700;
}}
.stButton > button:hover {{
  filter: brightness(0.95);
}}

/* Input / select / text area */
.stTextInput input, .stSelectbox div[data-baseweb="select"] > div,
.stTextArea textarea {{
  background: #ffffff !important;
  color: #000 !important;
  border-radius: 10px !important;
}}

/* Bảng dữ liệu: header tone theo chủ đạo */
[data-testid="stTable"] thead tr th, .stDataFrame thead tr th {{
  background: {PRIMARY}22 !important;
  color: #fff !important;
}}

/* Alert thẩm mỹ */
.stAlert.success {{
  background: {PRIMARY}33 !important;
  border-left: 4px solid {GOLD} !important;
}}
.stAlert.warning {{
  background: #C07E0026 !important;
  border-left: 4px solid #C07E00 !important;
}}
.stAlert.error {{
  background: #A0000026 !important;
  border-left: 4px solid #A00000 !important;
}}
</style>
""", unsafe_allow_html=True)

# Vẽ thanh topbar vàng
st.markdown('<div class="topbar"></div>', unsafe_allow_html=True)

st.title("📍 Công cụ chuẩn hóa địa chỉ Việt Nam")

# -------- Sidebar controls --------
st.sidebar.header("⚙️ Tùy chọn")
mode_str = st.sidebar.selectbox("Chế độ phân tích", ["LEGACY", "FROM_2025"])
mode = ParseMode[mode_str]
keep_street = st.sidebar.checkbox("Giữ tên đường (keep_street)", True)
short_name = st.sidebar.checkbox("Tên rút gọn (short_name)", True)

# level hợp lệ theo mode
level = st.sidebar.number_input(
    "Level",
    min_value=1,
    max_value=3 if mode_str == "LEGACY" else 2,
    value=3 if mode_str == "LEGACY" else 2,
    step=1,
)

st.sidebar.markdown("---")
st.sidebar.subheader("Batch CSV")
uploaded = st.sidebar.file_uploader("Tải CSV (UTF-8)", type=["csv"])
address_col = None
if uploaded is not None:
    df_preview = pd.read_csv(uploaded)
    cols = list(df_preview.columns)
    address_col = st.sidebar.selectbox("Chọn cột địa chỉ", cols)

# -------- Single address --------
st.header("🔎 Phân tích nhanh")
st.markdown("_Ví dụ: 70 nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM_")
address_input = st.text_input(
    "Nhập địa chỉ", "nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM"
)

col_btn1, col_btn2 = st.columns([1,1])
parse_clicked = col_btn1.button("Phân tích địa chỉ")
convert_clicked = col_btn2.button("Chuẩn hóa (convert → 2025)")

def to_clean_df(obj: Any, order_hint: list[str] | None = None) -> pd.DataFrame:
    if obj is None:
        return pd.DataFrame()
    # Lấy dict công khai
    data: Dict[str, Any] = {
        k: v for k, v in getattr(obj, "__dict__", {}).items()
        if not k.startswith("_") and v is not None
    }
    # Sắp xếp cột đẹp mắt
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

# Kết quả một địa chỉ
if parse_clicked:
    try:
        parsed = parse_address(
            address_input,
            mode=mode,
            keep_street=keep_street,
            level=int(level),
        )
        if parsed:
            st.success("🎯 Phân tích thành công")
            df_parsed = to_clean_df(parsed)
            st.dataframe(df_parsed, use_container_width=True)

            # Map nếu có lat/lon
            if {"latitude", "longitude"}.issubset(df_parsed.columns):
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
            if {"latitude", "longitude"}.issubset(df_converted.columns):
                st.map(df_converted.rename(columns={"latitude":"lat","longitude":"lon"})[["lat","lon"]])
        else:
            st.warning("⚠️ Không chuẩn hóa được địa chỉ.")
    except Exception as e:
        st.error(f"⚠️ Lỗi khi chuẩn hóa: {e}")

# -------- Batch CSV --------
st.header("📦 Xử lý hàng loạt (CSV)")
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
