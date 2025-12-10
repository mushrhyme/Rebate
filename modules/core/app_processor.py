"""
App-level PDF processing helpers (moved out of app.py)

이 파일은 Streamlit UI에서 사용되는 PDF 처리 관련 헬퍼들을 모아둡니다.
"""

import os
import time
from typing import Any, Optional, Tuple, Callable
from io import BytesIO

import streamlit as st

from modules.core.processor import PdfProcessor
from utils.session_manager import SessionManager
from src.gemini_extractor import GeminiVisionParser


def process_pdf_with_progress(
    pdf_name: str,
    progress_container,
    file_index: int,
    total_files: int,
    uploaded_file: Optional[Any] = None,
    pdf_path: Optional[str] = None,
    is_reprocess: bool = False
) -> Tuple[bool, int, Optional[str], float]:
    """
    PDF 파일 처리 공통 핸들러 (재분석 및 신규 분석 통합)
    """
    def progress_callback(page_num: int, total_pages: int, message: str):
        progress = page_num / total_pages
        progress_bar.progress(progress)
        status_text.text(message)

    with progress_container.container():
        if uploaded_file:
            display_name = uploaded_file.name
        else:
            display_name = f"{pdf_name}.pdf"

        message = f"**{display_name}** {'再解析中' if is_reprocess else '解析中'}... ({file_index + 1}/{total_files})"
        st.info(message, icon="🔄")
        progress_bar = st.progress(0)
        status_text = st.empty()

    if uploaded_file is not None:
        success, pages, error, elapsed_time = PdfProcessor.process_uploaded_pdf(
            uploaded_file=uploaded_file,
            pdf_name=pdf_name,
            dpi=300,
            progress_callback=progress_callback
        )
    else:
        success, pages, error, elapsed_time = PdfProcessor.process_pdf(
            pdf_name=pdf_name,
            pdf_path=pdf_path,
            dpi=300,
            progress_callback=progress_callback
        )

    status = PdfProcessor.get_processing_status(pdf_name)
    st.session_state.analysis_status[pdf_name] = status

    progress_container.empty()
    return success, pages, error, elapsed_time


def reprocess_pdf_from_storage(pdf_name: str, progress_container, file_index: int, total_files: int) -> Tuple[bool, int, Optional[str], float]:
    """저장된 PDF 파일 재분석 (공통 핸들러 사용)"""
    return process_pdf_with_progress(
        pdf_name=pdf_name,
        progress_container=progress_container,
        file_index=file_index,
        total_files=total_files,
        uploaded_file=None,
        pdf_path=None,
        is_reprocess=True
    )


def process_single_pdf(uploaded_file, pdf_name: str, progress_container, file_index: int, total_files: int) -> Tuple[bool, int, Optional[str], float]:
    """단일 PDF 파일 처리 (공통 핸들러 사용)"""
    return process_pdf_with_progress(
        pdf_name=pdf_name,
        progress_container=progress_container,
        file_index=file_index,
        total_files=total_files,
        uploaded_file=uploaded_file,
        pdf_path=None,
        is_reprocess=False
    )


def reparse_single_page(pdf_name: str, page_num: int):
    """단일 페이지 재파싱"""
    from modules.ui.review_components import load_page_image as load_page_image_from_module

    page_image = load_page_image_from_module(pdf_name, page_num)
    if page_image is None:
        st.error("画像が見つかりません。")
        return

    try:
        parser = GeminiVisionParser()
        new_page_json = parser.parse_image(page_image)

        try:
            SessionManager.save_ocr_result(pdf_name, page_num, new_page_json)
        except Exception as save_err:
            st.error(f"セッションへの保存に失敗しました: {save_err}", icon="❌")
            return

        st.success(f"ページ {page_num} 再パース完了！", icon="✅")
        st.rerun()
    except Exception as e:
        st.error(f"再パース失敗: {e}", icon="❌")


def check_pdf_in_db(pdf_filename: str) -> Tuple[bool, int]:
    """DB에서 PDF 존재 여부 및 페이지 수 확인"""
    try:
        from database.db_manager import DatabaseManager
        db_manager = DatabaseManager(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', '5432')),
            database=os.getenv('DB_NAME', 'rebate_db'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', '')
        )

        is_in_db = db_manager.has_pdf_in_db(pdf_filename, is_latest_only=True)
        page_count = 0

        if is_in_db:
            page_results = db_manager.get_page_results(
                pdf_filename=pdf_filename,
                session_id=None,
                is_latest=True
            )
            page_count = len(page_results) if page_results else 0

        db_manager.close()
        return is_in_db, page_count
    except Exception:
        return False, 0


