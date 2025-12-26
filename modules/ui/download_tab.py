"""
다운로드 탭
"""

from io import BytesIO

import streamlit as st
import pandas as pd

from modules.utils.session_manager import SessionManager
from modules.utils.merge_utils import MergeUtils
from modules.utils.session_utils import ensure_session_state_defaults


def render_download_tab():
    """다운로드 탭"""
    ensure_session_state_defaults()
    uploaded_pdfs = [info["name"] for info in st.session_state.uploaded_files_info]
    if not uploaded_pdfs:
        st.warning("アップロードされたPDFファイルがありません。", icon="⚠️")
        return
    all_pdfs_option = "全ファイル（すべてのPDFをマージ）"
    pdf_options = [all_pdfs_option] + uploaded_pdfs
    selected_option = st.selectbox(
        "📁 ダウンロードするファイルを選択",
        options=pdf_options,
        key="export_pdf_selector"
    )
    if selected_option == all_pdfs_option:
        selected_pdfs = uploaded_pdfs
    else:
        selected_pdfs = [selected_option]
    total_page_count = 0
    pdf_page_counts = {}
    for pdf_name in selected_pdfs:
        page_count = SessionManager.get_pdf_page_count(pdf_name)
        pdf_page_counts[pdf_name] = page_count
        total_page_count += page_count
    st.subheader("📊 検証状態サマリー")
    total_reviewed = 0
    total_with_edits = 0
    for pdf_name in selected_pdfs:
        page_count = pdf_page_counts[pdf_name]
        review_data = st.session_state.review_data.get(pdf_name, {})
        reviewed_pages = sum(1 for page_num in range(1, page_count + 1) 
                           if review_data.get(page_num, {}).get("checked", False))
        pages_with_edits = sum(1 for page_num in range(1, page_count + 1) 
                              if review_data.get(page_num, {}).get("edited_items"))
        total_reviewed += reviewed_pages
        total_with_edits += pages_with_edits
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("総ページ数", total_page_count)
    with col2:
        st.metric("レビュー完了", f"{total_reviewed}/{total_page_count}")
    with col3:
        st.metric("修正されたページ", total_with_edits)
    all_page_results = []
    for pdf_name in selected_pdfs:
        page_numbers = SessionManager.get_all_pages_with_results(pdf_name)
        page_numbers = sorted(set(page_numbers))
        for page_num in page_numbers:
            page_data = SessionManager.load_ocr_result(pdf_name, page_num)
            if page_data:
                all_page_results.append(page_data)
    modified_results = []
    page_idx = 0
    for pdf_name in selected_pdfs:
        page_count = pdf_page_counts[pdf_name]
        pdf_page_results = all_page_results[page_idx:page_idx + page_count]
        review_data = st.session_state.review_data.get(pdf_name, {})
        for idx, page_json in enumerate(pdf_page_results):
            page_num = idx + 1
            edited_items = review_data.get(page_num, {}).get("edited_items")
            if edited_items:
                modified_page_json = page_json.copy()
                modified_page_json["items"] = edited_items
                modified_results.append(modified_page_json)
            else:
                modified_results.append(page_json)
        page_idx += page_count
    st.subheader("📊 データプレビュー")
    merged_df = MergeUtils.merge_all_pages(modified_results)
    if not merged_df.empty:
        st.dataframe(merged_df.head(20), width='stretch')
        st.caption(f"総 {len(merged_df)}行（上位20件のみ表示）")
    else:
        st.info("データがありません。")
    st.divider()
    st.subheader("📥 Excelダウンロード")
    if st.button("📥 Excelファイル生成及びダウンロード", type="primary", width='stretch'):
        try:
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                merged_df.to_excel(writer, index=False, sheet_name='Sheet1')
            output.seek(0)
            if len(selected_pdfs) == 1:
                filename = f"{selected_pdfs[0]}_parsed.xlsx"
            else:
                filename = f"merged_{len(selected_pdfs)}files_parsed.xlsx"
            st.download_button(
                label="📥 ダウンロード",
                data=output.getvalue(),
                file_name=filename,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width='stretch',
                key="download_excel"
            )
            st.success("Excelファイルが生成されました！", icon="✅")
        except Exception as e:
            st.error(f"Excelファイル生成失敗: {e}", icon="❌")

