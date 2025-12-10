"""
Streamlit 웹앱: 조건청구서 파싱 시스템 (통합 페이지)

PDF 청구서를 Gemini Vision으로 파싱하고,
페이지별로 결과를 확인하고 관리할 수 있는 기능을 제공합니다.

한 페이지에 모든 기능 통합:
1. 업로드 & 분석 탭
2. 검토 탭
3. 다운로드 탭
"""

import streamlit as st
import pandas as pd
import json
import os
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
from PIL import Image, ImageFile
from io import BytesIO
import sys
import time
from datetime import datetime

# .env 파일 로드 (프로젝트 루트에서)
from dotenv import load_dotenv
env_path = Path(__file__).parent / '.env'
load_dotenv(env_path)

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, str(Path(__file__).parent))

from utils.session_manager import SessionManager
from parser.vision_parser import VisionParser
from parser.merge_utils import MergeUtils
from src.gemini_extractor import GeminiVisionParser
from modules.ui.review_components import (
    load_page_data,
    load_page_image as load_page_image_from_module,
    render_navigation,
    render_page_image,
    render_editable_table
)
from modules.core.processor import PdfProcessor
from modules.core.registry import PdfRegistry
from modules.utils.pdf_utils import (
    find_pdf_path,
    get_all_pdf_list
)

# AG Grid import (선택적 - 없으면 기본 data_editor 사용)
try:
    from st_aggrid import AgGrid
    AGGrid_AVAILABLE = True
except ImportError:
    AGGrid_AVAILABLE = False

from modules.ui.aggrid_utils import AgGridUtils

# 이미지 처리 설정
Image.MAX_IMAGE_PIXELS = None
ImageFile.LOAD_TRUNCATED_IMAGES = True

# 페이지 설정
st.set_page_config(
    page_title="条件請求書パースシステム",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 전역 스타일 정의
st.markdown("""
<style>
    /* 사이드바 숨기기 */
    [data-testid="stSidebar"] {
        display: none;
    }
    
    /* 네비게이션 버튼 기본 스타일 */
    div.stButton button {
        border: none !important;
        font-weight: bold !important;
        transition: background-color 0.2s ease;
    }
    
    /* Primary 버튼 기본 색상 (type="primary") */
    div.stButton button[data-testid="stBaseButton-primary"][kind="primary"] {
        background-color: #FF4B4B !important; /* Streamlit 기본 primary 색상 */
        color: white !important;
    }
    
    div.stButton button[data-testid="stBaseButton-primary"][kind="primary"]:not(:disabled):hover {
        background-color: #FF3030 !important;
    }
    
    /* Secondary 버튼 기본 색상 (type="secondary") */
    div.stButton button[data-testid="stBaseButton-secondary"][kind="secondary"] {
        background-color: #F0F2F6 !important; /* Streamlit 기본 secondary 색상 */
        color: #262730 !important;
    }
    
    div.stButton button[data-testid="stBaseButton-secondary"][kind="secondary"]:not(:disabled):hover {
        background-color: #E0E2E6 !important;
    }
    
    /* 비활성화된 버튼 공통 스타일 */
    div.stButton button:disabled {
        background-color: #6c757d !important;
        opacity: 0.6 !important;
        cursor: not-allowed;
        color: white !important;
    }
</style>
<script>
(function() {
    // 페이지 로드 시 실행
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', styleNavigationButtons);
    } else {
        styleNavigationButtons();
    }
    
    // Streamlit의 동적 콘텐츠 업데이트를 감지
    const observer = new MutationObserver(styleNavigationButtons);
    observer.observe(document.body, { childList: true, subtree: true });
})();
</script>
<style>
</style>
""", unsafe_allow_html=True)

# 세션 상태 초기화
if 'uploaded_files_info' not in st.session_state:
    st.session_state.uploaded_files_info = []
    # ❗ DB에서 자동으로 복원하지 않음
    # uploaded_files_info는 오직 사용자가 직접 업로드한 파일만 포함
    # DB에 있는 파일은 자동으로 추가되지 않음
if 'uploaded_file_objects' not in st.session_state:
    st.session_state.uploaded_file_objects = {}  # 업로드된 파일 바이너리 저장
if 'analysis_status' not in st.session_state:
    st.session_state.analysis_status = {}
    # pdf_registry.json은 분석 대기열 관리용이므로 초기화 시 로드하지 않음
    # 분석 상태는 분석 시작 시 동적으로 업데이트됨
if 'selected_pdf' not in st.session_state:
    st.session_state.selected_pdf = None
if 'selected_page' not in st.session_state:
    st.session_state.selected_page = 1
if 'review_data' not in st.session_state:
    st.session_state.review_data = {}


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
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        progress_container: Streamlit progress container
        file_index: 현재 파일 인덱스 (0부터 시작)
        total_files: 전체 파일 수
        uploaded_file: 업로드된 파일 객체 (None이면 저장된 파일 처리)
        pdf_path: PDF 파일 경로 (None이면 자동으로 찾음)
        is_reprocess: 재분석 여부 (True면 "再解析中", False면 "解析中")
        
    Returns:
        (성공 여부, 페이지 수, 에러 메시지, 소요 시간) 튜플
    """
    # 진행률 콜백 함수 정의
    def progress_callback(page_num: int, total_pages: int, message: str):
        progress = page_num / total_pages
        progress_bar.progress(progress)
        status_text.text(message)
    
    # 진행률 UI 설정
    with progress_container.container():
        # 파일명 및 메시지 결정
        if uploaded_file:
            display_name = uploaded_file.name
        else:
            display_name = f"{pdf_name}.pdf"
        
        message = f"**{display_name}** {'再解析中' if is_reprocess else '解析中'}... ({file_index + 1}/{total_files})"
        st.info(message, icon="🔄")
        progress_bar = st.progress(0)
        status_text = st.empty()
    
    # PdfProcessor를 사용하여 처리
    if uploaded_file is not None:
        # 업로드된 파일 처리
        success, pages, error, elapsed_time = PdfProcessor.process_uploaded_pdf(
            uploaded_file=uploaded_file,
            pdf_name=pdf_name,
            dpi=300,
            progress_callback=progress_callback
        )
    else:
        # 저장된 파일 처리
        success, pages, error, elapsed_time = PdfProcessor.process_pdf(
            pdf_name=pdf_name,
            pdf_path=pdf_path,  # None이면 자동으로 찾음
            dpi=300,
            progress_callback=progress_callback
        )
    
    # 세션 상태 동기화
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


def load_page_image(pdf_name: str, page_num: int) -> Optional[Image.Image]:
    """페이지 이미지 로드 (DB 우선, 파일 시스템은 폴백)"""
    # review_components의 load_page_image 사용 (DB 우선 로드)
    from modules.ui.review_components import load_page_image as load_page_image_from_module
    return load_page_image_from_module(pdf_name, page_num)


# load_page_data_from_project_result 함수 제거됨 (사용자가 요청한 분석 목록에 한해서만 작동)




def reparse_single_page(pdf_name: str, page_num: int):
    """단일 페이지 재파싱"""
    page_image = load_page_image(pdf_name, page_num)
    if page_image is None:
        st.error("画像が見つかりません。")
        return
    
    try:
        parser = GeminiVisionParser()
        new_page_json = parser.parse_image(page_image)
        
        # DB에 저장 (JSON 파일 저장은 제거)
        try:
            from database.db_manager import DatabaseManager
            import os
            
            # DB 연결 정보 (환경 변수에서 가져오거나 기본값 사용)
            db_manager = DatabaseManager(
                host=os.getenv('DB_HOST', 'localhost'),
                port=int(os.getenv('DB_PORT', '5432')),
                database=os.getenv('DB_NAME', 'rebate_db'),
                user=os.getenv('DB_USER', 'postgres'),
                password=os.getenv('DB_PASSWORD', '')
            )
            
            # PDF 파일명 (확장자 포함)
            pdf_filename = f"{pdf_name}.pdf"
            
            # 단일 페이지 결과를 리스트로 변환하여 저장
            page_results = [new_page_json]
            
            # DB에 저장
            session_id = db_manager.save_from_page_results(
                page_results=page_results,
                pdf_filename=pdf_filename,
                session_name=f"再パース {pdf_name} ページ{page_num}",
                notes=f"ページ {page_num} 再パース"
            )
            
            # DB 연결 종료
            db_manager.close()
        except Exception as db_error:
            st.error(f"DB 저장 실패: {db_error}", icon="❌")
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


def render_upload_tab():
    """업로드 & 분석 탭"""
    
    # 안내 문구
    st.info(
        "**📌 使い方ガイド**:\n\n"
        "• 複数のファイルをアップロードした後、🔍 **解析実行**をクリックすると同時に分析できます\n\n"
        "• 既に分析を完了したPDFファイルは、🔄 **再解析**をクリックして個別ファイルに対して分析を再実行できます",
        icon="ℹ️"
    )
    
    # 파일 업로드
    uploaded_files = st.file_uploader(
        "PDFファイルをアップロードしてください（複数ファイル選択可能）",
        type=['pdf'],
        accept_multiple_files=True
    )
    
    # uploaded_files 기준으로 session_state 덮어쓰기 (단순화)
    if uploaded_files:
        current_names = {Path(f.name).stem for f in uploaded_files}
        existing_names = {info["name"] for info in st.session_state.uploaded_files_info}
        
        # 새로 추가된 파일만 DB 조회 및 추가
        new_files = current_names - existing_names
        for uploaded_file in uploaded_files:
            pdf_name = Path(uploaded_file.name).stem
            if pdf_name in new_files:
                # 파일 바이너리 저장 (rerun 시에도 유지)
                st.session_state.uploaded_file_objects[pdf_name] = uploaded_file.getvalue()
                
                # 업로드 시점에 DB 존재 여부 확인
                pdf_filename = f"{pdf_name}.pdf"
                is_in_db, db_page_count = check_pdf_in_db(pdf_filename)
                
                # 파일 정보 추가
                st.session_state.uploaded_files_info.append({
                    "name": pdf_name,
                    "original_name": uploaded_file.name,
                    "size": uploaded_file.size,
                    "is_in_db": is_in_db,
                    "db_page_count": db_page_count
                })
                
                # DB에 있으면 "解析済み" 상태로 설정
                if is_in_db and db_page_count > 0:
                    st.session_state.analysis_status[pdf_name] = {
                        "status": "completed",
                        "pages": db_page_count,
                        "error": None
                    }
                else:
                    # DB에 없으면 "待機中" 상태로 설정
                    st.session_state.analysis_status[pdf_name] = {
                        "status": "pending",
                        "pages": 0,
                        "error": None
                    }
        
        # 제거된 파일은 session_state에서도 제거
        removed_names = existing_names - current_names
        if removed_names:
            st.session_state.uploaded_files_info = [
                info for info in st.session_state.uploaded_files_info
                if info["name"] not in removed_names
            ]
            for pdf_name in removed_names:
                st.session_state.analysis_status.pop(pdf_name, None)
                st.session_state.review_data.pop(pdf_name, None)
                st.session_state.uploaded_file_objects.pop(pdf_name, None)  # 바이너리도 제거
    elif not uploaded_files and st.session_state.uploaded_files_info:
        # 파일 업로더가 비어있으면 session_state도 비우기
        st.session_state.uploaded_files_info = []
        st.session_state.analysis_status = {}
        st.session_state.uploaded_file_objects = {}  # 바이너리도 비우기
    
    # 진행 중인 파일 확인 및 알림
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
    
    # 업로드된 파일 목록 표시
    if st.session_state.uploaded_files_info:
        
        # 파일 목록 표시
        st.subheader("📋 アップロードされたファイル一覧")
        
        files_to_reprocess = []  # 재분석할 파일 인덱스 저장
        
        for idx, file_info in enumerate(st.session_state.uploaded_files_info):
            col1, col2, col3 = st.columns([4, 2, 1])
            pdf_name = file_info['name']
            status_info = st.session_state.analysis_status.get(pdf_name, {})
            status = status_info.get("status", "pending")
            
            with col1:
                # DB에 있는 파일은 "解析済み" 표시
                if file_info.get("is_in_db") and file_info.get("db_page_count", 0) > 0:
                    st.text(f"📄 {file_info['original_name']} 🔄 (解析済み: {file_info['db_page_count']}ページ)")
                else:
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
                    # DB에 있지만 상태가 아직 동기화되지 않은 경우
                    st.info(f"解析済み ({file_info['db_page_count']}p)", icon="💾")
                else:
                    st.warning("待機中", icon="⏳")
            
            with col3:
                # "解析済み" 파일에서만 재解析 버튼 표시
                if (status == "completed" or 
                    (file_info.get("is_in_db") and file_info.get("db_page_count", 0) > 0)):
                    if st.button("🔄 再解析", key=f"reprocess_{pdf_name}"):
                        files_to_reprocess.append(idx)
        
        # 재분석 처리
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
                    # 재분석 후 상태 업데이트
                    st.session_state.analysis_status[pdf_name] = {
                        "status": "completed",
                        "pages": pages,
                        "error": None
                    }
                    # DB 존재 여부도 업데이트
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
        
        # 분석 실행
        st.divider()
        
        # "待機中" 상태인 파일만 분석 대상으로 선택
        pending_files = [
            info["name"] for info in st.session_state.uploaded_files_info
            if (st.session_state.analysis_status.get(info["name"], {}).get("status") == "pending" and
                not (info.get("is_in_db") and info.get("db_page_count", 0) > 0))
        ]
        
        # 처리 가능한 파일만 필터링
        processable_files = [
            name for name in pending_files 
            if PdfProcessor.can_process_pdf(name)
        ]
        
        if processable_files:
            st.info(f"{len(processable_files)}個のファイルが解析待機中です。", icon="💡")
        elif not pending_files and st.session_state.uploaded_files_info:
            st.success("すべてのファイルの解析が完了しました！", icon="✅")
        
        # 분석 실행 버튼 ("待機中" 파일이 있을 때만 활성화)
        button_disabled = len(processable_files) == 0
        if st.button("🔍 解析実行", type="primary", width='stretch', disabled=button_disabled):
            # "待機中" 파일만 분석 대상으로 처리
            files_to_analyze = []
            
            # processable_files만 처리
            for pdf_name in processable_files:
                # 파일 정보 찾기
                file_info = next(
                    (info for info in st.session_state.uploaded_files_info if info["name"] == pdf_name),
                    None
                )
                if not file_info:
                    continue
                
                # session_state에서 파일 바이너리 복원
                file_bytes = st.session_state.uploaded_file_objects.get(pdf_name)
                
                if file_bytes:
                    # BytesIO로 파일 객체 복원
                    uploaded_file = BytesIO(file_bytes)
                    # 파일 이름 속성 설정 (PdfProcessor에서 필요할 수 있음)
                    uploaded_file.name = file_info["original_name"]
                    
                    # 새로 업로드된 파일은 즉시 저장
                    try:
                        SessionManager.save_pdf_file(uploaded_file, pdf_name)
                        # 저장 후 다시 BytesIO로 복원 (파일 포인터가 이동했을 수 있음)
                        uploaded_file = BytesIO(file_bytes)
                        uploaded_file.name = file_info["original_name"]
                    except Exception:
                        pass
                    files_to_analyze.append((file_info, uploaded_file, None))
                else:
                    # 저장된 파일 경로 확인
                    pdf_path = find_pdf_path(pdf_name)
                    if pdf_path:
                        files_to_analyze.append((file_info, None, pdf_path))
                    else:
                        st.warning(f"⚠️ {pdf_name}.pdf ファイルが見つかりません。スキップします。", icon="⚠️")
            
            if files_to_analyze:
                # 디버깅: 분석할 파일 목록 표시
                file_names = [f[0]['name'] for f in files_to_analyze]
                st.info(f"**分析対象**: {len(files_to_analyze)}個のファイル - {', '.join(file_names)}", icon="ℹ️")
                
                progress_placeholder = st.empty()
                total_files = len(files_to_analyze)
                total_pages = 0
                success_count = 0
                
                # 실제 경과 시간 측정 시작 (멀티쓰레딩이므로 합산이 아닌 실제 시간 측정)
                import time
                start_time = time.time()
                
                # 각 파일을 순차적으로 처리 (각 PDF는 독립적으로 처리됨)
                for file_idx, (file_info, uploaded_file, pdf_path) in enumerate(files_to_analyze):
                    pdf_name = file_info["name"]
                    
                    # 진행 상황 표시
                    with progress_placeholder.container():
                        st.info(f"📄 **{pdf_name}.pdf** を処理中... ({file_idx + 1}/{total_files})", icon="🔄")
                    
                    try:
                        if uploaded_file is not None:
                            # 새로 업로드된 파일 처리
                            success, pages, error, elapsed_time = process_single_pdf(
                                uploaded_file, pdf_name, progress_placeholder, file_idx, total_files
                            )
                        else:
                            # 저장된 파일 처리
                            success, pages, error, elapsed_time = reprocess_pdf_from_storage(
                                pdf_name, progress_placeholder, file_idx, total_files
                            )
                        
                        if success:
                            total_pages += pages
                            success_count += 1
                            # 분석 완료 후 상태 업데이트
                            st.session_state.analysis_status[pdf_name] = {
                                "status": "completed",
                                "pages": pages,
                                "error": None
                            }
                            # DB 존재 여부도 업데이트
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
                        # 예외 발생 시에도 다음 파일 처리 계속
                        file_display_name = uploaded_file.name if uploaded_file else f"{pdf_name}.pdf"
                        st.error(f"❌ **{file_display_name}** 解析中にエラーが発生しました: {str(e)}", icon="❌")
                        # 에러 상태 저장 (PdfProcessor 사용)
                        PdfProcessor.get_processing_status(pdf_name)
                        continue  # 다음 파일 계속 처리
                
                progress_placeholder.empty()
                
                if success_count > 0:
                    # 실제 경과 시간 계산 (멀티쓰레딩이므로 합산이 아닌 실제 시간)
                    actual_elapsed_time = time.time() - start_time
                    
                    # 시간 포맷팅 (초 단위, 분:초 형식으로 표시)
                    minutes = int(actual_elapsed_time // 60)
                    seconds = int(actual_elapsed_time % 60)
                    if minutes > 0:
                        time_str = f"{minutes}分{seconds}秒"
                    else:
                        time_str = f"{seconds}秒"
                    
                    st.success(f"🎉 **{success_count}個のファイル解析完了！** (総 {total_pages}ページ、所要時間: {time_str})", icon="✅")
                    # 분석 완료 후 상태 업데이트를 위해 rerun (최소화)
                    st.rerun()
            else:
                st.warning("分析対象のファイルがありません。", icon="⚠️")
    else:
        st.info("上でPDFファイルをアップロードしてください。", icon="👆")


def render_review_tab():
    """검토 탭 (모듈화된 컴포넌트 사용)"""
    
    col1, col2 = st.columns(2)
    
    with col1:
        # 사용자가 업로드한 파일만 조회 (uploaded_files_info 기준, 업로드 탭과 동일)
        uploaded_pdfs = [info["name"] for info in st.session_state.uploaded_files_info]
        
        # selected_pdf가 명시적으로 None으로 설정된 경우 (목록 초기화 후)
        if st.session_state.selected_pdf is None:
            if uploaded_pdfs:
                st.info("📋 PDFファイルを選択してください", icon="ℹ️")
                # 파일 선택
                selected_pdf = st.selectbox(
                    "PDFファイルを選択",
                    options=uploaded_pdfs,
                    index=0,
                    key="pdf_selector",
                    label_visibility="collapsed"
                )
                # 선택하면 상태 업데이트
                if selected_pdf:
                    st.session_state.selected_pdf = selected_pdf
                    st.session_state.selected_page = 1
                    st.rerun()
                return
            else:
                st.warning("アップロードされたPDFファイルがありません。まずアップロード & 解析タブでファイルをアップロードして解析してください。", icon="⚠️")
                return
        
        if not uploaded_pdfs:
            st.warning("アップロードされたPDFファイルがありません。まずアップロード & 解析タブでファイルをアップロードして解析してください。", icon="⚠️")
            # selected_pdf가 설정되어 있으면 해제
            if st.session_state.selected_pdf is not None:
                st.session_state.selected_pdf = None
                st.session_state.selected_page = 1
            return
        
        # selected_pdf가 목록에 없으면 첫 번째 파일 선택
        if st.session_state.selected_pdf not in uploaded_pdfs:
            if uploaded_pdfs:
                st.session_state.selected_pdf = uploaded_pdfs[0]
                st.session_state.selected_page = 1
        
        # 파일 선택
        selected_pdf = st.selectbox(
            "PDFファイルを選択",
            options=uploaded_pdfs,
            index=uploaded_pdfs.index(st.session_state.selected_pdf) if st.session_state.selected_pdf in uploaded_pdfs else 0,
            key="pdf_selector",
            label_visibility="collapsed"
        )
        
        # PDF가 실제로 변경된 경우에만 selected_page를 1로 초기화
        if selected_pdf != st.session_state.selected_pdf:
            # PDF 변경 시에만 페이지를 1로 초기화
            st.session_state.selected_pdf = selected_pdf
            st.session_state.selected_page = 1
            st.rerun()
        
        page_count = SessionManager.get_pdf_page_count(selected_pdf)
        
        if page_count == 0:
            st.error("このファイルの解析結果がありません。", icon="⚠️")
            return
        
        review_data = st.session_state.review_data.get(selected_pdf, {})
    
    with col2:
        # rerun 시에도 selected_page 유지 (PDF 변경 시에만 1로 초기화됨)
        if 'selected_page' not in st.session_state or st.session_state.selected_page < 1:
            st.session_state.selected_page = 1
        elif st.session_state.selected_page > page_count:
            st.session_state.selected_page = page_count
        
        current_page = st.session_state.selected_page
        
        # 페이지 네비게이션 렌더링
        render_navigation(selected_pdf, current_page, page_count)
    
    # 페이지 데이터 로드
    page_data = load_page_data(selected_pdf, current_page)
    
    # 이미지와 테이블 표시
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
   
    # 사용자가 업로드한 파일만 조회 (uploaded_files_info 기준, 업로드 탭과 동일)
    uploaded_pdfs = [info["name"] for info in st.session_state.uploaded_files_info]
    
    if not uploaded_pdfs:
        st.warning("アップロードされたPDFファイルがありません。", icon="⚠️")
        return
    
    # 파일 선택
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
    
    # 페이지 수 확인
    total_page_count = 0
    pdf_page_counts = {}
    
    for pdf_name in selected_pdfs:
        page_count = SessionManager.get_pdf_page_count(pdf_name)
        pdf_page_counts[pdf_name] = page_count
        total_page_count += page_count
        
    # 검수 상태 요약
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

    
    # 데이터 로드 및 병합
    all_page_results = []
    for pdf_name in selected_pdfs:
        # 세션 디렉토리에서 페이지 목록 가져오기 (사용자가 요청한 분석 목록에 한해서만)
        page_numbers = SessionManager.get_all_pages_with_results(pdf_name)
        
        # 페이지 번호 정렬
        page_numbers = sorted(set(page_numbers))
        
        for page_num in page_numbers:
            # 세션 디렉토리에서 로드 (PageStorage 사용)
            page_data = SessionManager.load_ocr_result(pdf_name, page_num)
            
            if page_data:
                all_page_results.append(page_data)
    
    # 검토 시 수정된 항목 반영
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
    
    # 데이터 미리보기
    st.subheader("📊 データプレビュー")
    
    merged_df = MergeUtils.merge_all_pages(modified_results)
    
    if not merged_df.empty:
        st.dataframe(merged_df.head(20), width='stretch')
        st.caption(f"総 {len(merged_df)}行（上位20件のみ表示）")
    else:
        st.info("データがありません。")
    
    st.divider()
    
    # 엑셀 다운로드
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
                mime="application/vnd.openpyxl-officedocument.spreadsheetml.sheet",
                width='stretch',
                key="download_excel"
            )
            
            st.success("Excelファイルが生成されました！", icon="✅")
        except Exception as e:
            st.error(f"Excelファイル生成失敗: {e}", icon="❌")


def main():
    """메인 함수"""
    st.title("Nongshim 条件請求書分析システム")
    
    # 탭 선택
    tab1, tab2, tab3 = st.tabs(["📤 アップロード & 解析", "📝 レビュー", "📥 ダウンロード"])
    
    with tab1:
        render_upload_tab()
    
    with tab2:
        render_review_tab()
    
    with tab3:
        render_download_tab()


if __name__ == "__main__":
    main()
