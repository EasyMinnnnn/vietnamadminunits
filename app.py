import streamlit as st
import pandas as pd
from vietnamadminunits import parse_address, convert_address
from vietnamadminunits.parser import ParseMode
from vietnamadminunits.converter import ConvertMode

st.set_page_config(page_title="Chuẩn hóa địa chỉ", layout="wide")
st.title("📍 Công cụ chuẩn hóa địa chỉ Việt Nam")

with st.sidebar:
    st.header("⚙️ Tùy chọn")
    mode_str = st.selectbox("🛠️ Chế độ phân tích", [m.value for m in ParseMode])
    convert_mode = st.checkbox("🔁 Chuyển đổi sang 34 tỉnh", value=False)

mode = ParseMode(mode_str)

# Một địa chỉ
st.subheader("📌 Nhập địa chỉ để chuẩn hóa")
address_input = st.text_input("Ví dụ: 70 nguyễn sỹ sách, p.15, Tân Bình, Tp.HCM")

if st.button("Phân tích địa chỉ"):
    try:
        parsed = parse_address(address_input, mode=mode)
        st.success("🎯 Phân tích thành công:")

        if hasattr(parsed, '__dict__'):
            st.json(parsed.__dict__)
        else:
            st.write(parsed)

        if convert_mode:
            converted = convert_address(address_input)
            st.subheader("🔁 Sau chuyển đổi (chuẩn hóa 34 tỉnh):")
            if hasattr(converted, '__dict__'):
                st.json(converted.__dict__)
            else:
                st.write(converted)
    except Exception as e:
        st.error(f"❌ Đã xảy ra lỗi: {e}")

# Upload file CSV
st.subheader("📂 Hoặc tải lên file CSV để xử lý hàng loạt")
uploaded_file = st.file_uploader("Tải lên file chứa cột địa chỉ", type="csv")

if uploaded_file is not None:
    df = pd.read_csv(uploaded_file)
    st.write("📄 Xem trước dữ liệu:")
    st.dataframe(df.head())

    address_column = st.selectbox("🧩 Chọn cột chứa địa chỉ", df.columns)
    process = st.radio("🔎 Thao tác", ["Phân tích (parse)", "Chuyển đổi (convert)"])

    results = []
    for addr in df[address_column]:
        try:
            if process == "Phân tích (parse)":
                res = parse_address(addr, mode=mode)
                results.append(res.__dict__ if hasattr(res, '__dict__') else str(res))
            else:
                res = convert_address(addr)
                results.append(res.__dict__ if hasattr(res, '__dict__') else str(res))
        except Exception as e:
            results.append({"error": str(e)})

    result_df = pd.DataFrame(results)
    st.subheader("✅ Kết quả xử lý:")
    st.dataframe(result_df)

    csv = result_df.to_csv(index=False).encode('utf-8')
    st.download_button("⬇️ Tải kết quả về", csv, "ket_qua.csv", "text/csv")
