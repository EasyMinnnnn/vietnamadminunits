import streamlit as st
import pandas as pd
from vietnamadminunits import parse_address, convert_address, ParseMode

st.set_page_config(page_title="Chuẩn hóa địa chỉ Việt Nam", layout="centered")
st.title("📍 Công cụ chuẩn hóa địa chỉ Việt Nam")

st.header("📌 Nhập địa chỉ để chuẩn hóa")
st.markdown("_Ví dụ: 70 nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM_")

address_input = st.text_input("", "nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM")

mode_str = st.selectbox("🛠️ Chế độ phân tích", ["LEGACY", "FROM_2025"])
mode = ParseMode[mode_str]

if st.button("Phân tích địa chỉ"):
    try:
        parsed = parse_address(address_input, mode=mode, keep_street=True, level=3 if mode_str == "LEGACY" else 2)
        if parsed:
            st.success("🎯 Phân tích thành công:")
            parsed_dict = parsed.__dict__
            st.dataframe(pd.DataFrame([parsed_dict]))

            try:
                converted = convert_address(address_input)
                st.success("🔁 Kết quả sau chuẩn hóa:")
                converted_dict = converted.__dict__
                st.dataframe(pd.DataFrame([converted_dict]))
            except Exception as e:
                st.error(f"⚠️ Lỗi khi chuẩn hóa: {e}")
        else:
            st.warning("⚠️ Không phân tích được địa chỉ.")
    except Exception as e:
        st.error(f"❌ Đã xảy ra lỗi: {e}")
