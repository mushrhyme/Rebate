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


def reparse_single_page(pdf_name: str, page_num: int, timeout: int = 120):
    """
    단일 페이지 재파싱
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        page_num: 페이지 번호 (1부터 시작)
        timeout: API 호출 타임아웃 (초, 기본값: 120초 = 2분)
    """
    from modules.ui.review_components import load_page_image as load_page_image_from_module

    # 진행 상황 표시를 위한 placeholder
    progress_placeholder = st.empty()
    
    with progress_placeholder.container():
        st.info("🔄 画像を読み込み中...", icon="⏳")
    
    page_image = load_page_image_from_module(pdf_name, page_num)
    if page_image is None:
        progress_placeholder.empty()
        st.error("画像が見つかりません。")
        return

    try:
        with progress_placeholder.container():
            st.info("🤖 Gemini APIで解析中... (최대 2분 소요)", icon="⏳")
        
        parser = GeminiVisionParser()
        new_page_json = parser.parse_image(page_image, timeout=timeout)  # 타임아웃 전달

        with progress_placeholder.container():
            st.info("💾 結果を保存中...", icon="⏳")
        
        try:
            SessionManager.save_ocr_result(pdf_name, page_num, new_page_json)
        except Exception as save_err:
            progress_placeholder.empty()
            st.error(f"セッションへの保存に失敗しました: {save_err}", icon="❌")
            return

        progress_placeholder.empty()
        st.success(f"ページ {page_num} 再パース完了！", icon="✅")
        st.rerun()
    except Exception as e:
        progress_placeholder.empty()
        error_msg = str(e)
        if "타임아웃" in error_msg or "timeout" in error_msg.lower():
            st.error(f"再パースタイムアウト: {timeout}秒以内に完了しませんでした。", icon="⏱️")
        else:
            st.error(f"再パース失敗: {e}", icon="❌")


def check_pdf_in_db(pdf_filename: str) -> Tuple[bool, int]:
    """DB에서 PDF 존재 여부 및 페이지 수 확인"""
    try:
        from database.registry import get_db
        db_manager = get_db()

        is_in_db = db_manager.has_pdf_in_db(pdf_filename, is_latest_only=True)
        page_count = 0

        if is_in_db:
            page_results = db_manager.get_page_results(
                pdf_filename=pdf_filename,
                session_id=None,
                is_latest=True
            )
            page_count = len(page_results) if page_results else 0

        return is_in_db, page_count
    except Exception:
        return False, 0


