import streamlit as st
from vietnamadminunits import parse_address, convert_address
from vietnamadminunits.parser import ParseMode

st.set_page_config(page_title="Chuẩn hóa địa chỉ", layout="centered")
st.title("📍 Công cụ chuẩn hóa địa chỉ Việt Nam")

address_input = st.text_input("🔤 Nhập địa chỉ cần chuẩn hóa", "Số 1 Nguyễn Trãi, Thanh Xuân, Hà Nội")

# dùng tên enum cho selectbox
mode_options = [m.value for m in ParseMode]  # ['LEGACY', 'FROM_2025']
mode_str = st.selectbox("🛠️ Chế độ chuẩn hóa", mode_options)
mode = mode_str  # hoặc ParseMode(mode_str) nếu bạn muốn dùng enum

if st.button("✅ Phân tích"):
    try:
        parsed = parse_address(address_input, mode=mode)
        converted = convert_address(address_input)
        st.subheader("📌 Kết quả phân tích:")
        st.json(parsed)
        st.subheader("🔁 Kết quả sau chuyển đổi (chuẩn hóa 34 tỉnh):")
        st.json(converted)
    except Exception as e:
        st.error(f"❌ Lỗi: {e}")
