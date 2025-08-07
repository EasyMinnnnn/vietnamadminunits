import streamlit as st
from vietnamadminunits import parse_address, convert_address

st.set_page_config(page_title="Chuẩn hóa địa chỉ", layout="centered")
st.title("📍 Công cụ chuẩn hóa địa chỉ Việt Nam")

address_input = st.text_input("🔤 Nhập địa chỉ cần chuẩn hóa", "Số 1 Nguyễn Trãi, Thanh Xuân, Hà Nội")
mode = st.selectbox("Chế độ chuẩn hóa", ["legacy", "from_2025"])

if st.button("✅ Phân tích"):
    parsed = parse_address(address_input, mode=mode)
    converted = convert_address(address_input)
    
    st.subheader("📌 Kết quả phân tích:")
    st.json(parsed)
    
    st.subheader("🔁 Kết quả sau chuyển đổi (chuẩn hóa 34 tỉnh):")
    st.json(converted)
