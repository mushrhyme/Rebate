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

    from modules.utils.config import get_rag_config
    config = get_rag_config()
    
    if uploaded_file is not None:
        success, pages, error, elapsed_time = PdfProcessor.process_uploaded_pdf(
            uploaded_file=uploaded_file,
            pdf_name=pdf_name,
            dpi=config.dpi,
            progress_callback=progress_callback
        )
    else:
        success, pages, error, elapsed_time = PdfProcessor.process_pdf(
            pdf_name=pdf_name,
            pdf_path=pdf_path,
            dpi=config.dpi,
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
    단일 페이지 재파싱 (PyMuPDF + RAG 기반)
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        page_num: 페이지 번호 (1부터 시작)
        timeout: API 호출 타임아웃 (초, 기본값: 120초 = 2분)
    """
    from pathlib import Path
    import fitz  # PyMuPDF
    from src.rag_extractor import extract_json_with_rag
    from modules.utils.config import get_rag_config
    from modules.utils.pdf_utils import find_pdf_path

    # 설정 로드 (한 번만 호출)
    config = get_rag_config()

    # 진행 상황 표시를 위한 placeholder
    progress_placeholder = st.empty()
    
    with progress_placeholder.container():
        st.info("🔄 PDFファイルを検索中...", icon="⏳")
    
    # PDF 파일 경로 찾기
    pdf_path = find_pdf_path(pdf_name)
    if not pdf_path:
        progress_placeholder.empty()
        st.error("PDFファイルが見つかりません。")
        return
    
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        progress_placeholder.empty()
        st.error("PDFファイルが見つかりません。")
        return

    try:
        # 파싱 시간 측정 시작
        parse_start_time = time.time()
        
        # PyMuPDF로 텍스트 추출
        with progress_placeholder.container():
            st.info("🔍 PyMuPDFでテキスト抽出中...", icon="⏳")
        
        # PyMuPDF로 PDF에서 텍스트 추출
        doc = fitz.open(pdf_path)
        if page_num < 1 or page_num > doc.page_count:
            doc.close()
            raise Exception(f"페이지 번호가 범위를 벗어났습니다 (1-{doc.page_count})")
        
        page = doc.load_page(page_num - 1)  # fitz는 0부터 시작
        ocr_text = page.get_text()
        doc.close()
        
        if not ocr_text or len(ocr_text.strip()) == 0:
            raise Exception("PDF에서 텍스트를 추출할 수 없습니다")
        
        # RAG 기반 JSON 추출
        with progress_placeholder.container():
            st.info("🔎 RAG検索中...", icon="⏳")
        
        def rag_progress_wrapper(msg: str):
            with progress_placeholder.container():
                st.info(f"🤖 {msg}", icon="⏳")
        
        new_page_json = extract_json_with_rag(
            ocr_text=ocr_text,
            question=config.question,
            model_name=config.openai_model,
            temperature=0.0,
            top_k=config.top_k,
            similarity_threshold=config.similarity_threshold,
            progress_callback=rag_progress_wrapper,
            page_num=page_num
        )
        
        # items 개수 확인
        if not isinstance(new_page_json, dict):
            raise Exception(f"예상치 못한 응답 형식: {type(new_page_json)}. 딕셔너리가 아닙니다.")
        
        items = new_page_json.get("items", [])
        items_count = len(items) if items else 0
        
        parse_end_time = time.time()
        parse_duration = parse_end_time - parse_start_time
        
        print(f"페이지 {page_num} 재파싱 완료: {parse_duration:.1f}초 ({items_count}개 items)")

        with progress_placeholder.container():
            st.info("💾 結果を保存中...", icon="⏳")
        
        # 파일 시스템에 저장
        SessionManager.save_ocr_result(pdf_name, page_num, new_page_json)
        
        # DB에도 저장 (items 업데이트)
        try:
            from database.registry import get_db
            db_manager = get_db()
            pdf_filename = f"{pdf_name}.pdf"
            
            if items:
                # DB의 해당 페이지 items 업데이트
                success = db_manager.update_page_items(
                    pdf_filename=pdf_filename,
                    page_num=page_num,
                    items=items,
                    session_id=None,
                    is_latest=True
                )
                if success:
                    print(f"✅ DB 업데이트 완료: {len(items)}개 items 저장")
                else:
                    print(f"⚠️ DB 업데이트 실패 (세션이 없을 수 있음)")
            else:
                print(f"⚠️ items가 비어있어 DB 업데이트를 건너뜁니다")
        except Exception as db_err:
            # DB 업데이트 실패해도 파일 저장은 성공했으므로 계속 진행
            print(f"⚠️ DB 업데이트 실패 (파일 저장은 완료): {db_err}")

        progress_placeholder.empty()
        st.success(f"ページ {page_num} 再パース完了！ (소요 시간: {parse_duration:.2f}초, {items_count}개 items)", icon="✅")
        st.rerun()
    except Exception as e:
        parse_end_time = time.time()
        parse_duration = parse_end_time - parse_start_time if 'parse_start_time' in locals() else 0.0
        
        # 실패 시 소요 시간만 출력
        print(f"페이지 {page_num} 재파싱 실패: {parse_duration:.1f}초 - {e}")
        
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


