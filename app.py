import streamlit as st
import pandas as pd
from vietnamadminunits import parse_address, convert_address, ParseMode, ConvertMode
from vietnamadminunits.pandas import convert_address_column, standardize_admin_unit_columns

st.set_page_config(page_title="Chuẩn hóa địa chỉ hàng loạt", layout="wide")
st.title("📍 Công cụ chuẩn hóa và chuyển đổi địa chỉ Việt Nam")

# Sidebar navigation
page = st.sidebar.radio("Chọn chế độ xử lý:", ["🔍 Phân tích từng địa chỉ", "📂 Xử lý hàng loạt từ file CSV"])

if page == "🔍 Phân tích từng địa chỉ":
    st.subheader("🔤 Nhập địa chỉ cần chuẩn hóa")
    address = st.text_input("Ví dụ: 70 nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM")
    
    mode = st.selectbox("🛠️ Chế độ phân tích", ["LEGACY", "FROM_2025"])

    if st.button("✅ Phân tích") and address:
        try:
            parsed = parse_address(address, mode=ParseMode(mode))
            st.success("🎯 Phân tích thành công:")
            st.json(parsed.to_dict())

            if st.checkbox("🔁 Chuyển sang 34 tỉnh"):
                converted = convert_address(address)
                st.markdown("### ✅ Kết quả sau chuyển đổi:")
                st.json(converted.to_dict())

        except Exception as e:
            st.error(f"❌ Đã xảy ra lỗi: {e}")

elif page == "📂 Xử lý hàng loạt từ file CSV":
    st.subheader("📤 Tải lên file CSV")
    uploaded_file = st.file_uploader("Chọn file chứa địa chỉ", type="csv")

    if uploaded_file:
        df = pd.read_csv(uploaded_file)
        st.markdown("### 🧾 Xem trước dữ liệu:")
        st.dataframe(df.head())

        method = st.selectbox("🔧 Chọn kiểu xử lý", ["Chuẩn hóa cột tỉnh/xã/huyện", "Chuyển đổi địa chỉ 63→34 tỉnh"])

        if method == "Chuẩn hóa cột tỉnh/xã/huyện":
            province_col = st.selectbox("Cột tỉnh", df.columns)
            district_col = st.selectbox("Cột huyện (tùy chọn)", [None] + list(df.columns))
            ward_col = st.selectbox("Cột xã/phường (tùy chọn)", [None] + list(df.columns))

            if st.button("🚀 Tiến hành chuẩn hóa"):
                result = standardize_admin_unit_columns(
                    df,
                    province=province_col,
                    district=district_col if district_col else None,
                    ward=ward_col if ward_col else None,
                    convert_mode=ConvertMode.CONVERT_2025,
                    show_progress=True
                )
                st.success("✅ Đã chuẩn hóa thành công")
                st.dataframe(result.head())
                st.download_button("📥 Tải xuống kết quả", result.to_csv(index=False), "ket_qua_chuanhoa.csv", "text/csv")

        elif method == "Chuyển đổi địa chỉ 63→34 tỉnh":
            address_col = st.selectbox("Chọn cột địa chỉ", df.columns)

            if st.button("🚀 Tiến hành chuyển đổi"):
                result = convert_address_column(df, address=address_col)
                st.success("✅ Đã chuyển đổi thành công")
                st.dataframe(result.head())
                st.download_button("📥 Tải xuống kết quả", result.to_csv(index=False), "ket_qua_chuyendoi.csv", "text/csv")
