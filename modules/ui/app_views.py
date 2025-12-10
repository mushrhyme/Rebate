"""
Streamlit UI 탭 및 메인 엔트리 (app.py에서 분리됨)

이 파일은 업로드/검토/다운로드 탭과 페이지 설정, 세션 초기화를 포함합니다.
"""

import os
import time
from pathlib import Path
from typing import Optional
from io import BytesIO

import streamlit as st
import pandas as pd

from dotenv import load_dotenv

# .env 로드 (프로젝트 루트의 .env)
env_path = Path(__file__).parent.parent.parent / '.env'
load_dotenv(env_path)

# 이미지 로드 설정
from PIL import Image, ImageFile
Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True

from utils.session_manager import SessionManager
from parser.merge_utils import MergeUtils
from modules.ui.review_components import (
    load_page_data,
    load_page_image,
    render_navigation,
    render_page_image,
    render_editable_table
)
from modules.core.processor import PdfProcessor
from modules.utils.pdf_utils import find_pdf_path
from modules.core.app_processor import (
    process_pdf_with_progress,
    reprocess_pdf_from_storage,
    process_single_pdf,
    reparse_single_page,
    check_pdf_in_db
)

st.markdown("""
<style>
    /* 사이드바 숨기기 */
    [data-testid="stSidebar"] {
        display: none;
    }
    div.stButton button {
        border: none !important;
        font-weight: bold !important;
        transition: background-color 0.2s ease;
    }
    div.stButton button[data-testid="stBaseButton-primary"][kind="primary"] {
        background-color: #FF4B4B !important;
        color: white !important;
    }
    div.stButton button[data-testid="stBaseButton-primary"][kind="primary"]:not(:disabled):hover {
        background-color: #FF3030 !important;
    }
    div.stButton button[data-testid="stBaseButton-secondary"][kind="secondary"] {
        background-color: #F0F2F6 !important;
        color: #262730 !important;
    }
    div.stButton button[data-testid="stBaseButton-secondary"][kind="secondary"]:not(:disabled):hover {
        background-color: #E0E2E6 !important;
    }
    div.stButton button:disabled {
        background-color: #6c757d !important;
        opacity: 0.6 !important;
        cursor: not-allowed;
        color: white !important;
    }
</style>
""", unsafe_allow_html=True)

# 세션 상태 초기화
def _ensure_session_state_defaults() -> None:
    """Streamlit 세션 상태의 기본 키들을 안전하게 초기화합니다."""
    defaults = {
        "uploaded_files_info": [],
        "uploaded_file_objects": {},
        "analysis_status": {},
        "selected_pdf": None,
        "selected_page": 1,
        "review_data": {}
    }
    for key, default_value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default_value


# 모듈 import 시에도 기본값을 시도 설정 (Streamlit 세션이 아직 준비되지 않더라도 안전하게 동작)
try:
    _ensure_session_state_defaults()
except Exception:
    # Streamlit 런타임에서만 동작하므로 예외는 무시하고 런타임 시점에 다시 초기화할 예정
    pass


def render_upload_tab():
    """업로드 & 분석 탭"""
    _ensure_session_state_defaults()
    st.info(
        "**📌 使い方ガイド**:\n\n"
        "• 複数のファイルをアップロードした後、🔍 **解析実行**をクリックすると同時に分析できます\n\n"
        "• 既に分析を完了したPDFファイルは、🔄 **再解析**をクリックして個別ファイルに対して分析を再実行できます",
        icon="ℹ️"
    )

    uploaded_files = st.file_uploader(
        "PDFファイルをアップロードしてください（複数ファイル選択可能）",
        type=['pdf'],
        accept_multiple_files=True
    )

    if uploaded_files:
        current_names = {Path(f.name).stem for f in uploaded_files}
        existing_names = {info["name"] for info in st.session_state.uploaded_files_info}
        new_files = current_names - existing_names
        for uploaded_file in uploaded_files:
            pdf_name = Path(uploaded_file.name).stem
            if pdf_name in new_files:
                st.session_state.uploaded_file_objects[pdf_name] = uploaded_file.getvalue()
                pdf_filename = f"{pdf_name}.pdf"
                is_in_db, db_page_count = check_pdf_in_db(pdf_filename)
                st.session_state.uploaded_files_info.append({
                    "name": pdf_name,
                    "original_name": uploaded_file.name,
                    "size": uploaded_file.size,
                    "is_in_db": is_in_db,
                    "db_page_count": db_page_count
                })
                if is_in_db and db_page_count > 0:
                    st.session_state.analysis_status[pdf_name] = {
                        "status": "completed",
                        "pages": db_page_count,
                        "error": None
                    }
                else:
                    st.session_state.analysis_status[pdf_name] = {
                        "status": "pending",
                        "pages": 0,
                        "error": None
                    }
        removed_names = existing_names - current_names
        if removed_names:
            st.session_state.uploaded_files_info = [
                info for info in st.session_state.uploaded_files_info
                if info["name"] not in removed_names
            ]
            for pdf_name in removed_names:
                st.session_state.analysis_status.pop(pdf_name, None)
                st.session_state.review_data.pop(pdf_name, None)
                st.session_state.uploaded_file_objects.pop(pdf_name, None)
    elif not uploaded_files and st.session_state.uploaded_files_info:
        st.session_state.uploaded_files_info = []
        st.session_state.analysis_status = {}
        st.session_state.uploaded_file_objects = {}

    processing_files = [
        pdf_name for pdf_name, status_info in st.session_state.analysis_status.items()
        if status_info.get("status") == "processing"
    ]

    if processing_files:
        st.warning(
            f"**分析中のファイルがあります**: {', '.join([f'{name}.pdf' for name in processing_files])}\n\n"
            "ページをリロードしても分析は継続されます。完了までお待ちください。",
            icon="⚠️"
        )

    if st.session_state.uploaded_files_info:
        st.subheader("📋 アップロードされたファイル一覧")
        files_to_reprocess = []
        for idx, file_info in enumerate(st.session_state.uploaded_files_info):
            col1, col2, col3 = st.columns([4, 2, 1])
            pdf_name = file_info['name']
            status_info = st.session_state.analysis_status.get(pdf_name, {})
            status = status_info.get("status", "pending")
            with col1:
                st.text(f"📄 {file_info['original_name']}")
            with col2:
                if status == "completed":
                    pages = status_info.get("pages", 0)
                    st.success(f"完了 ({pages}p)", icon="✅")
                elif status == "processing":
                    st.info("解析中...", icon="🔄")
                elif status == "error":
                    error = status_info.get("error", "不明なエラー")
                    st.error(f"エラー: {error[:30]}...", icon="❌")
                elif file_info.get("is_in_db") and file_info.get("db_page_count", 0) > 0:
                    st.info(f"解析済み ({file_info['db_page_count']}p)", icon="💾")
                else:
                    st.warning("待機中", icon="⏳")
            with col3:
                if (status == "completed" or 
                    (file_info.get("is_in_db") and file_info.get("db_page_count", 0) > 0)):
                    if st.button("🔄 再解析", key=f"reprocess_{pdf_name}"):
                        files_to_reprocess.append(idx)

        if files_to_reprocess:
            progress_placeholder = st.empty()
            total_files = len(files_to_reprocess)
            total_pages = 0
            success_count = 0
            start_time = time.time()
            for file_idx, original_idx in enumerate(files_to_reprocess):
                file_info = st.session_state.uploaded_files_info[original_idx]
                pdf_name = file_info["name"]
                success, pages, error, elapsed_time = reprocess_pdf_from_storage(
                    pdf_name, progress_placeholder, file_idx, total_files
                )
                if success:
                    total_pages += pages
                    success_count += 1
                    st.session_state.analysis_status[pdf_name] = {
                        "status": "completed",
                        "pages": pages,
                        "error": None
                    }
                    st.session_state.uploaded_files_info[original_idx]["is_in_db"] = True
                    st.session_state.uploaded_files_info[original_idx]["db_page_count"] = pages
                else:
                    st.error(f"{pdf_name}.pdf 再解析失敗: {error}", icon="❌")
            progress_placeholder.empty()
            if success_count > 0:
                actual_elapsed_time = time.time() - start_time
                minutes = int(actual_elapsed_time // 60)
                seconds = int(actual_elapsed_time % 60)
                time_str = f"{minutes}分{seconds}秒" if minutes > 0 else f"{seconds}秒"
                st.success(f"{success_count}個のファイル再解析完了！ (総 {total_pages}ページ、所要時間: {time_str})", icon="✅")
                st.rerun()

        st.divider()

        pending_files = [
            info["name"] for info in st.session_state.uploaded_files_info
            if (st.session_state.analysis_status.get(info["name"], {}).get("status") == "pending" and
                not (info.get("is_in_db") and info.get("db_page_count", 0) > 0))
        ]

        processable_files = [
            name for name in pending_files 
            if PdfProcessor.can_process_pdf(name)
        ]

        if processable_files:
            st.info(f"{len(processable_files)}個のファイルが解析待機中です。", icon="💡")
        elif not pending_files and st.session_state.uploaded_files_info:
            st.success("すべてのファイルの解析が完了しました！", icon="✅")

        button_disabled = len(processable_files) == 0
        if st.button("🔍 解析実行", type="primary", width='stretch', disabled=button_disabled):
            files_to_analyze = []
            for pdf_name in processable_files:
                file_info = next(
                    (info for info in st.session_state.uploaded_files_info if info["name"] == pdf_name),
                    None
                )
                if not file_info:
                    continue
                file_bytes = st.session_state.uploaded_file_objects.get(pdf_name)
                if file_bytes:
                    uploaded_file = BytesIO(file_bytes)
                    uploaded_file.name = file_info["original_name"]
                    try:
                        SessionManager.save_pdf_file(uploaded_file, pdf_name)
                        uploaded_file = BytesIO(file_bytes)
                        uploaded_file.name = file_info["original_name"]
                    except Exception:
                        pass
                    files_to_analyze.append((file_info, uploaded_file, None))
                else:
                    pdf_path = find_pdf_path(pdf_name)
                    if pdf_path:
                        files_to_analyze.append((file_info, None, pdf_path))
                    else:
                        st.warning(f"⚠️ {pdf_name}.pdf ファイルが見つかりません。スキップします。", icon="⚠️")

            if files_to_analyze:
                file_names = [f[0]['name'] for f in files_to_analyze]
                st.info(f"**分析対象**: {len(files_to_analyze)}個のファイル - {', '.join(file_names)}", icon="ℹ️")
                progress_placeholder = st.empty()
                total_files = len(files_to_analyze)
                total_pages = 0
                success_count = 0
                start_time = time.time()
                for file_idx, (file_info, uploaded_file, pdf_path) in enumerate(files_to_analyze):
                    pdf_name = file_info["name"]
                    # process_single_pdf/reprocess_pdf_from_storage 내부에서 진행 상황을 표시하므로
                    # 여기서는 중복으로 표시하지 않음
                    try:
                        if uploaded_file is not None:
                            success, pages, error, elapsed_time = process_single_pdf(
                                uploaded_file, pdf_name, progress_placeholder, file_idx, total_files
                            )
                        else:
                            success, pages, error, elapsed_time = reprocess_pdf_from_storage(
                                pdf_name, progress_placeholder, file_idx, total_files
                            )
                        if success:
                            total_pages += pages
                            success_count += 1
                            st.session_state.analysis_status[pdf_name] = {
                                "status": "completed",
                                "pages": pages,
                                "error": None
                            }
                            file_info_idx = next(
                                (idx for idx, info in enumerate(st.session_state.uploaded_files_info) 
                                 if info["name"] == pdf_name),
                                None
                            )
                            if file_info_idx is not None:
                                st.session_state.uploaded_files_info[file_info_idx]["is_in_db"] = True
                                st.session_state.uploaded_files_info[file_info_idx]["db_page_count"] = pages
                            st.success(f"✅ **{pdf_name}.pdf** 解析完了 ({pages}ページ)", icon="✅")
                        else:
                            file_display_name = uploaded_file.name if uploaded_file else f"{pdf_name}.pdf"
                            st.error(f"❌ **{file_display_name}** 解析失敗: {error}", icon="❌")
                    except Exception as e:
                        file_display_name = uploaded_file.name if uploaded_file else f"{pdf_name}.pdf"
                        st.error(f"❌ **{file_display_name}** 解析中にエラーが発生しました: {str(e)}", icon="❌")
                        PdfProcessor.get_processing_status(pdf_name)
                        continue
                progress_placeholder.empty()
                if success_count > 0:
                    actual_elapsed_time = time.time() - start_time
                    minutes = int(actual_elapsed_time // 60)
                    seconds = int(actual_elapsed_time % 60)
                    if minutes > 0:
                        time_str = f"{minutes}分{seconds}秒"
                    else:
                        time_str = f"{seconds}秒"
                    st.success(f"🎉 **{success_count}個のファイル解析完了！** (総 {total_pages}ページ、所要時間: {time_str})", icon="✅")
                    st.rerun()
            else:
                st.warning("分析対象のファイルがありません。", icon="⚠️")
    else:
        st.info("上でPDFファイルをアップロードしてください。", icon="👆")


def render_review_tab():
    """검토 탭 - 단순화된 클린 버전"""
    _ensure_session_state_defaults()
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


def render_download_tab():
    """다운로드 탭"""
    _ensure_session_state_defaults()
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


def main():
    """메인 함수"""
    # Streamlit 페이지 설정은 반드시 가장 먼저 호출되어야 함
    st.set_page_config(
        page_title="条件請求書パースシステム",
        page_icon="📄",
        layout="wide",
        initial_sidebar_state="collapsed"
    )
    _ensure_session_state_defaults()
    st.title("Nongshim 条件請求書分析システム")
    tab1, tab2, tab3 = st.tabs(["📤 アップロード & 解析", "📝 レビュー", "📥 ダウンロード"])
    with tab1:
        render_upload_tab()
    with tab2:
        render_review_tab()
    with tab3:
        render_download_tab()
