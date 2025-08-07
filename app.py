import streamlit as st
from vietnamadminunits import parse_address, convert_address
from vietnamadminunits.parser import ParseMode  # Nhập enum ParseMode

st.set_page_config(page_title="Chuẩn hóa địa chỉ", layout="centered")
st.title("📍 Công cụ chuẩn hóa địa chỉ Việt Nam")

st.markdown("#### 🔤 Nhập địa chỉ cần chuẩn hóa")
address_input = st.text_input("", "Số 1 Nguyễn Trãi, Thanh Xuân, Hà Nội")

mode_str = st.selectbox("🛠️ Chế độ chuẩn hóa", ["legacy", "from_2025"])
mode = ParseMode(mode_str)  # Ép kiểu sang enum

if st.button("✅ Phân tích"):
    try:
        # Gọi 2 hàm chính
        parsed = parse_address(address_input, mode=mode)
        converted = convert_address(address_input)

        st.subheader("📌 Kết quả phân tích:")
        st.json(parsed)

        st.subheader("🔁 Kết quả sau chuyển đổi (chuẩn hóa 34 tỉnh):")
        st.json(converted)
    except Exception as e:
        st.error(f"❌ Lỗi: {e}")
