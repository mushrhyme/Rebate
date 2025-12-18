"""
검토 탭
"""

import streamlit as st

from utils.session_manager import SessionManager
from modules.ui.review_components import (
    load_page_data,
    render_navigation,
    render_page_image,
    render_editable_table
)
from modules.utils.session_utils import ensure_session_state_defaults


def render_review_tab():
    """검토 탭 - 단순화된 클린 버전"""
    ensure_session_state_defaults()
    uploaded_pdfs = [info["name"] for info in st.session_state.uploaded_files_info]
    if not uploaded_pdfs:
        st.warning("アップロードされたPDFファイルがありません。", icon="⚠️")
        return
    if "selected_pdf" not in st.session_state:
        st.session_state.selected_pdf = uploaded_pdfs[0]
    if "selected_page" not in st.session_state:
        st.session_state.selected_page = 1
    selected_pdf = st.selectbox(
        "PDFファイルを選択",
        uploaded_pdfs,
        index=uploaded_pdfs.index(st.session_state.selected_pdf)
        if st.session_state.selected_pdf in uploaded_pdfs else 0,
        key="pdf_selector"
    )
    if selected_pdf != st.session_state.selected_pdf:
        st.session_state.selected_pdf = selected_pdf
        st.session_state.selected_page = 1
        st.rerun()
    page_count = SessionManager.get_pdf_page_count(selected_pdf)
    if page_count == 0:
        st.error("このファイルの解析結果がありません。", icon="⚠️")
        return
    current_page = st.session_state.selected_page
    current_page = max(1, min(current_page, page_count))
    st.session_state.selected_page = current_page
    render_navigation(selected_pdf, current_page, page_count)
    page_data = load_page_data(selected_pdf, current_page)
    col1, col2 = st.columns(2)
    with col1:
        render_page_image(selected_pdf, current_page)
    with col2:
        if page_data is None:
            st.error("このページの解析結果が見つかりません。", icon="⚠️")
        else:
            render_editable_table(selected_pdf, current_page)

