import streamlit as st
from vietnamadminunits import parse_address, convert_address
from vietnamadminunits.parser import ParseMode

st.set_page_config(page_title="Chuẩn hóa địa chỉ", layout="centered")
st.title("📍 Công cụ chuẩn hóa địa chỉ Việt Nam")

address_input = st.text_input("🔤 Nhập địa chỉ cần chuẩn hóa", "Số 1 Nguyễn Trãi, Thanh Xuân, Hà Nội")
mode_str = st.selectbox("🛠️ Chế độ chuẩn hóa", [m.value for m in ParseMode])
mode = mode_str

if st.button("✅ Phân tích"):
    try:
        parsed = parse_address(address_input, mode=mode)
        converted = convert_address(address_input)
    except Exception as e:
        st.error(f"❌ Lỗi: {e}")
        parsed = None
        converted = None

    if isinstance(parsed, dict):
        st.subheader("📌 Kết quả phân tích:")
        st.json(parsed)
    else:
        st.error("Kết quả phân tích không hợp lệ.")

    if isinstance(converted, dict):
        st.subheader("🔁 Kết quả chuẩn hóa 34 tỉnh:")
        st.json(converted)
    else:
        st.error("Kết quả chuẩn hóa không hợp lệ.")
