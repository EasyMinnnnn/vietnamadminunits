import streamlit as st
from vietnamadminunits import parse_address, convert_address
from vietnamadminunits.parser import ParseMode

st.set_page_config(page_title="Chuẩn hóa địa chỉ", layout="centered")
st.title("📍 Công cụ chuẩn hóa địa chỉ Việt Nam")

# Nhập địa chỉ
address_input = st.text_input("🔤 Nhập địa chỉ cần chuẩn hóa", "Số 1 Nguyễn Trãi, Thanh Xuân, Hà Nội")

# Lựa chọn chế độ
mode_str = st.selectbox("🛠️ Chế độ chuẩn hóa", [m.value for m in ParseMode])  # ['LEGACY', 'FROM_2025']
mode = mode_str  # hoặc ParseMode(mode_str) nếu bạn muốn dùng enum

# Khi nhấn nút
if st.button("✅ Phân tích"):
    try:
        # Gọi hàm phân tích địa chỉ
        parsed = parse_address(address_input, mode=mode)
        st.subheader("📌 Kết quả phân tích:")
        st.write("🧪 Debug (parsed):", parsed)

        if hasattr(parsed, "__dict__"):
            st.json(parsed.__dict__)  # in ra dạng JSON nếu là object
        elif isinstance(parsed, dict):
            st.json(parsed)
        else:
            st.warning("⚠️ Kết quả phân tích không hợp lệ.")

        # Gọi hàm chuẩn hóa sang 34 tỉnh
        converted = convert_address(address_input)  # ✅ sửa lỗi ở đây
        st.subheader("🔁 Kết quả chuẩn hóa 34 tỉnh:")
        st.write("🧪 Debug (converted):", converted)

        if isinstance(converted, dict):
            st.json(converted)
        else:
            st.warning("⚠️ Kết quả chuẩn hóa không hợp lệ.")

    except Exception as e:
        st.error("❌ Đã xảy ra lỗi trong quá trình xử lý.")
        st.exception(e)
