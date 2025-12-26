"""
세션 관리 유틸리티 모듈
다중 사용자 환경에서 session_id 기반으로 파일을 분리하여 저장

PdfRegistry와 PageStorage를 사용하여 메타데이터와 결과를 관리합니다.
"""

import os
import json
import sys
import uuid
import streamlit as st
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

# Core 모듈 import (같은 프로젝트 내부이므로 상대 import 사용)
# PdfRegistry 제거됨 - DB와 st.session_state로 대체
from modules.core.storage import PageStorage


class SessionManager:
    """세션별 파일 관리 클래스"""
    
    BASE_TMP_DIR = "/tmp"  
    
    @staticmethod
    def get_project_root() -> str:
        """프로젝트 루트 디렉토리 경로 반환"""
        # app.py가 있는 디렉토리를 프로젝트 루트로 사용
        if hasattr(sys, '_getframe'):
            frame = sys._getframe(1)
            while frame:
                filename = frame.f_globals.get('__file__', '')
                if filename and 'app.py' in filename:
                    return str(Path(filename).parent)
                frame = frame.f_back
        # fallback: 현재 작업 디렉토리
        return os.getcwd()
    
    @staticmethod
    def get_session_id() -> str:
        """
        현재 Streamlit 세션 ID 반환
        
        Returns:
            세션 ID 문자열
        """
        # Streamlit의 내부 세션 ID 사용
        if hasattr(st, 'session_id'):
            return str(st.session_id)
        # fallback: session_state에 저장된 ID 사용
        if 'session_id' not in st.session_state:
            st.session_state.session_id = str(uuid.uuid4())
        return st.session_state.session_id
    
    @staticmethod
    def get_session_dir() -> str:
        """
        현재 세션의 작업 디렉토리 경로 반환
        
        Returns:
            /tmp/{session_id}/ 경로
        """
        session_id = SessionManager.get_session_id()
        session_dir = os.path.join(SessionManager.BASE_TMP_DIR, session_id)
        os.makedirs(session_dir, exist_ok=True)
        return session_dir
    
    @staticmethod
    def get_pdfs_dir() -> str:
        """
        세션별 PDF 저장 디렉토리
        
        Returns:
            /tmp/{session_id}/pdfs/ 경로
        """
        session_dir = SessionManager.get_session_dir()
        pdfs_dir = os.path.join(session_dir, "pdfs")
        os.makedirs(pdfs_dir, exist_ok=True)
        return pdfs_dir
    
    @staticmethod
    def get_images_dir() -> str:
        """
        이미지 저장 디렉토리 (프로젝트 루트)
        
        Returns:
            img/ 경로
        """
        project_root = SessionManager.get_project_root()
        images_dir = os.path.join(project_root, "img")
        os.makedirs(images_dir, exist_ok=True)
        return images_dir
    
    @staticmethod
    def get_results_dir() -> str:
        """
        OCR 결과 저장 디렉토리 (프로젝트 루트)
        
        Returns:
            result/ 경로
        """
        project_root = SessionManager.get_project_root()
        results_dir = os.path.join(project_root, "result")
        os.makedirs(results_dir, exist_ok=True)
        return results_dir
    
    @staticmethod
    def save_pdf_file(uploaded_file, pdf_name: str) -> str:
        """
        업로드된 PDF 파일을 세션 디렉토리에 저장
        
        Args:
            uploaded_file: Streamlit UploadedFile 객체
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            저장된 PDF 파일 경로
        """
        pdfs_dir = SessionManager.get_pdfs_dir()
        pdf_path = os.path.join(pdfs_dir, f"{pdf_name}.pdf")
        
        with open(pdf_path, 'wb') as f:
            f.write(uploaded_file.getvalue())
        
        return pdf_path
    
    @staticmethod
    def save_page_image(image, pdf_name: str, page_num: int) -> str:
        """
        페이지 이미지를 img 폴더에 저장
        
        Args:
            image: PIL Image 객체
            pdf_name: PDF 파일명 (확장자 제외)
            page_num: 페이지 번호 (1부터 시작)
            
        Returns:
            저장된 이미지 파일 경로
        """
        images_dir = SessionManager.get_images_dir()
        pdf_images_dir = os.path.join(images_dir, pdf_name)
        os.makedirs(pdf_images_dir, exist_ok=True)
        
        image_path = os.path.join(pdf_images_dir, f"page_{page_num}.jpg")  # JPEG 형식
        # RGB 모드로 변환 (JPEG는 RGB만 지원)
        if image.mode != 'RGB':
            image = image.convert('RGB')
        image.save(image_path, 'JPEG', quality=95)
        
        return image_path
    
    @staticmethod
    def save_ocr_result(pdf_name: str, page_num: int, page_json: Dict[str, Any]) -> str:
        """
        OCR 결과를 page_N.json 형식으로 저장 (PageStorage 사용)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            page_num: 페이지 번호 (1부터 시작)
            page_json: 페이지 OCR 결과 JSON
            
        Returns:
            저장된 JSON 파일 경로
        """
        return PageStorage.save_page(pdf_name, page_num, page_json)
    
    @staticmethod
    def load_ocr_result(pdf_name: str, page_num: int) -> Optional[Dict[str, Any]]:
        """
        저장된 OCR 결과 로드 (DB 우선, 파일 시스템은 폴백)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            page_num: 페이지 번호 (1부터 시작)
            
        Returns:
            페이지 OCR 결과 JSON 또는 None
        """
        # 1. DB에서 로드 시도 (애플리케이션 전역 인스턴스 사용)
        try:
            from database.registry import get_db

            # 전역 DB 인스턴스 사용
            db_manager = get_db()

            # PDF 파일명 (확장자 포함)
            pdf_filename = f"{pdf_name}.pdf"

            # DB에서 페이지 데이터 로드
            page_data = db_manager.get_page_result(
                pdf_filename=pdf_filename,
                page_num=page_num,
                session_id=None,
                is_latest=True
            )

            if page_data:
                return page_data
        except Exception as db_error:
            # DB 로드 실패 시 파일 시스템으로 폴백
            print(f"DB 로드 실패 (파일 시스템으로 폴백): {db_error}")
        
        # 2. 파일 시스템에서 로드 (하위 호환성)
        return PageStorage.load_page(pdf_name, page_num)
    
    @staticmethod
    def get_pdf_list() -> List[str]:
        """
        DB에서 모든 PDF 파일 목록 반환 (최신 세션만)
        
        pdf_registry.json과 무관하게 DB에서 직접 조회합니다.
        DB에 실제로 최신 세션이 있고 페이지 데이터가 있는 경우에만 포함합니다.
        
        주의: list_cleared 플래그가 True이면 빈 리스트를 반환합니다.
        
        Returns:
            PDF 파일명 리스트 (확장자 제외)
        """
        # Streamlit 세션 상태 확인 (list_cleared 플래그)
        try:
            import streamlit as st
            if st.session_state.get("list_cleared", False):
                return []
        except:
            pass  # Streamlit이 없는 환경에서는 무시
        
        try:
            from database.registry import get_db

            db_manager = get_db()

            # DB에서 모든 고유한 PDF 파일명 가져오기 (최신 세션만)
            pdf_filenames = db_manager.get_all_pdf_filenames(is_latest_only=True)

            # 확장자 제거하여 PDF 이름만 추출
            valid_pdfs = []
            for pdf_filename in pdf_filenames:
                # 확장자 제거
                pdf_name = pdf_filename.replace('.pdf', '')

                # 페이지 데이터가 실제로 있는지 확인
                page_results = db_manager.get_page_results(
                    pdf_filename=pdf_filename,
                    session_id=None,
                    is_latest=True
                )

                page_count = len(page_results) if page_results else 0

                if page_count > 0:
                    valid_pdfs.append(pdf_name)
                    print(f"✅ PDF '{pdf_name}' 목록에 추가 (DB에 {page_count}페이지 존재)")
                else:
                    print(f"⚠️ PDF '{pdf_name}'는 DB에 페이지 데이터가 없습니다. (목록에서 제외)")

            return sorted(valid_pdfs)

        except Exception as db_error:
            # DB 조회 실패 시 빈 리스트 반환
            print(f"⚠️ DB 조회 실패: {db_error}")
            return []
    
    @staticmethod
    def get_pdf_page_count(pdf_name: str) -> int:
        """
        PDF의 페이지 수 반환 (DB에서 조회)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            페이지 수
        """
        try:
            from database.registry import get_db

            db_manager = get_db()

            # PDF 파일명 (확장자 포함)
            pdf_filename = f"{pdf_name}.pdf"

            # DB에서 페이지 결과 조회
            page_results = db_manager.get_page_results(
                pdf_filename=pdf_filename,
                session_id=None,
                is_latest=True
            )

            # 페이지 수 반환
            page_count = len(page_results) if page_results else 0

            # 디버깅: 페이지 수가 0인 경우 상세 정보 출력
            if page_count == 0:
                print(f"⚠️ DB 조회 결과: '{pdf_filename}'의 페이지 수가 0입니다.")
                # 세션이 있는지 확인
                try:
                    db_manager2 = get_db()
                    sessions = db_manager2.get_sessions(pdf_filename=pdf_filename)
                    if sessions:
                        print(f"   발견된 세션 수: {len(sessions)}")
                        for sess in sessions:
                            print(f"   - session_id: {sess.get('session_id')}, is_latest: {sess.get('is_latest')}, timestamp: {sess.get('parsing_timestamp')}")
                    else:
                        print(f"   ⚠️ '{pdf_filename}'에 대한 세션이 DB에 없습니다.")
                except Exception as e:
                    print(f"   세션 확인 실패: {e}")

            return page_count

        except Exception as db_error:
            # DB 조회 실패 시 파일 시스템으로 폴백
            print(f"DB 페이지 수 조회 실패 (파일 시스템으로 폴백): {db_error}")
            import traceback
            traceback.print_exc()
            return PageStorage.get_page_count(pdf_name)
    
    @staticmethod
    def get_thumbnails_dir() -> str:
        """
        세션별 썸네일 저장 디렉토리
        
        Returns:
            /tmp/{session_id}/thumbnails/ 경로
        """
        session_dir = SessionManager.get_session_dir()
        thumbnails_dir = os.path.join(session_dir, "thumbnails")
        os.makedirs(thumbnails_dir, exist_ok=True)
        return thumbnails_dir
    
    @staticmethod
    def save_thumbnail(pdf_name: str, page_num: int, thumbnail_image) -> str:
        """
        썸네일 이미지 저장
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            page_num: 페이지 번호 (1부터 시작)
            thumbnail_image: PIL Image 객체 (썸네일)
            
        Returns:
            저장된 썸네일 파일 경로
        """
        thumbnails_dir = SessionManager.get_thumbnails_dir()
        pdf_thumbnails_dir = os.path.join(thumbnails_dir, pdf_name)
        os.makedirs(pdf_thumbnails_dir, exist_ok=True)
        
        thumbnail_path = os.path.join(pdf_thumbnails_dir, f"page_{page_num}_thumb.jpg")  # JPEG 형식
        # RGB 모드로 변환 (JPEG는 RGB만 지원)
        if thumbnail_image.mode != 'RGB':
            thumbnail_image = thumbnail_image.convert('RGB')
        thumbnail_image.save(thumbnail_path, 'JPEG', quality=95)
        
        return thumbnail_path
    
    @staticmethod
    def load_thumbnail(pdf_name: str, page_num: int) -> Optional[str]:
        """
        썸네일 이미지 경로 반환
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            page_num: 페이지 번호 (1부터 시작)
            
        Returns:
            썸네일 파일 경로 또는 None
        """
        thumbnails_dir = SessionManager.get_thumbnails_dir()
        thumbnail_path = os.path.join(thumbnails_dir, pdf_name, f"page_{page_num}_thumb.jpg")  # JPEG 형식
        
        if os.path.exists(thumbnail_path):
            return thumbnail_path
        return None
    
    @staticmethod
    def get_all_pages_with_results(pdf_name: str) -> List[int]:
        """
        PDF의 모든 페이지 번호 리스트 반환 (DB에서 조회)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            페이지 번호 리스트 (1부터 시작)
        """
        try:
            from database.registry import get_db

            db_manager = get_db()

            # PDF 파일명 (확장자 포함)
            pdf_filename = f"{pdf_name}.pdf"

            # DB에서 페이지 번호 직접 조회
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT DISTINCT i.page_number
                    FROM items i
                    JOIN parsing_sessions ps ON i.session_id = ps.session_id
                    WHERE ps.pdf_filename = %s AND ps.is_latest = TRUE
                    ORDER BY i.page_number
                """, (pdf_filename,))
                page_numbers = [row[0] for row in cursor.fetchall()]

            return page_numbers

        except Exception as db_error:
            # DB 조회 실패 시 파일 시스템으로 폴백
            print(f"DB 페이지 목록 조회 실패 (파일 시스템으로 폴백): {db_error}")
            return PageStorage.list_pages(pdf_name)
    
    @staticmethod
    def get_status_dir() -> str:
        """
        분석 상태 저장 디렉토리 (프로젝트 루트)
        
        Returns:
            status/ 경로
        """
        project_root = SessionManager.get_project_root()
        status_dir = os.path.join(project_root, "status")
        os.makedirs(status_dir, exist_ok=True)
        return status_dir
    
    @staticmethod
    def save_analysis_status(pdf_name: str, status: str, pages: int = 0, error: Optional[str] = None) -> bool:
        """
        분석 상태 저장 (PdfRegistry 제거됨 - st.session_state로 관리)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            status: 상태 ("processing", "completed", "error", "pending")
            pages: 페이지 수 (기본값: 0)
            error: 에러 메시지 (있는 경우)
            
        Returns:
            저장 성공 여부 (항상 True)
        """
        # st.session_state로 관리 (PdfRegistry 제거됨)
        import streamlit as st
        if "analysis_status" not in st.session_state:
            st.session_state.analysis_status = {}
        
        st.session_state.analysis_status[pdf_name] = {
            "status": status,
            "pages": pages,
            "error": error
        }
        return True
    
    @staticmethod
    def load_analysis_status(pdf_name: str) -> Optional[Dict[str, Any]]:
        """
        저장된 분석 상태 로드 (PdfRegistry 제거됨 - st.session_state에서 조회)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            분석 상태 딕셔너리 또는 None
        """
        # st.session_state에서 조회 (PdfRegistry 제거됨)
        import streamlit as st
        if "analysis_status" in st.session_state:
            status = st.session_state.analysis_status.get(pdf_name)
            if status:
                return {
                    "status": status.get("status", "pending"),
                    "pages": status.get("pages", 0),
                    "error": status.get("error"),
                    "pdf_name": pdf_name
                }
        
        # DB에서 페이지 수 확인
        try:
            from database.registry import get_db
            db_manager = get_db()
            pdf_filename = f"{pdf_name}.pdf"
            page_results = db_manager.get_page_results(
                pdf_filename=pdf_filename,
                session_id=None,
                is_latest=True
            )
            pages = len(page_results) if page_results else 0
            if pages > 0:
                return {
                    "status": "completed",
                    "pages": pages,
                    "error": None,
                    "pdf_name": pdf_name
                }
        except Exception:
            pass
        
        return None
    
    @staticmethod
    def _migrate_status_to_registry(pdf_name: str, status_data: Dict[str, Any]) -> None:
        """
        기존 status 파일 데이터 마이그레이션 (PdfRegistry 제거됨 - 더 이상 사용 안 함)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            status_data: 기존 상태 데이터
        """
        # PdfRegistry 제거됨 - 더 이상 마이그레이션 불필요
        pass
    
    @staticmethod
    def update_analysis_heartbeat(pdf_name: str) -> bool:
        """
        분석 중인 경우 heartbeat 업데이트 (PdfRegistry 제거됨 - 더 이상 사용 안 함)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            업데이트 성공 여부 (항상 True)
        """
        # PdfRegistry 제거됨 - 더 이상 heartbeat 불필요
        return True
    
    @staticmethod
    def is_analysis_active(pdf_name: str, timeout_minutes: int = 10) -> bool:
        """
        분석이 현재 활성 상태인지 확인 (PdfRegistry 제거됨 - 항상 False 반환)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            timeout_minutes: 타임아웃 시간 (분, 기본값: 10분)
            
        Returns:
            분석이 활성 상태이면 True, 아니면 False (항상 False)
        """
        # PdfRegistry 제거됨 - 항상 비활성으로 간주
        return False
    
    @staticmethod
    def get_all_analysis_statuses() -> Dict[str, Dict[str, Any]]:
        """
        사용자가 요청한 분석 목록의 상태만 로드 (PdfRegistry 제거됨 - st.session_state에서 조회)
        
        Returns:
            {pdf_name: status_dict} 형태의 딕셔너리
        """
        # st.session_state에서 조회 (PdfRegistry 제거됨)
        import streamlit as st
        if "analysis_status" not in st.session_state:
            return {}
        
        return st.session_state.analysis_status.copy()
    
    @staticmethod
    def save_analysis_requests(requests: List[Dict[str, Any]]) -> str:
        """
        분석 요청 목록을 파일에 저장 - 비활성화됨
        
        Args:
            requests: 분석 요청 목록 [{"pdf_name": "...", "status": "...", ...}, ...]
            
        Returns:
            저장된 경로 (더미 값)
        
        Note:
            로컬 파일 저장을 최소화하기 위해 비활성화됨.
            분석 요청은 pdf_registry.json으로 관리됩니다.
        """
        # 로컬 파일 저장 비활성화 (pdf_registry.json 사용)
        return ""
    
    @staticmethod
    def load_analysis_requests() -> List[Dict[str, Any]]:
        """
        저장된 분석 요청 목록 로드
        
        Returns:
            분석 요청 목록 리스트
        """
        status_dir = SessionManager.get_status_dir()
        requests_path = os.path.join(status_dir, "analysis_requests.json")
        
        if not os.path.exists(requests_path):
            return []
        
        try:
            with open(requests_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("requests", [])
        except Exception:
            return []
    
    @staticmethod
    def migrate_legacy_status_files() -> int:
        """
        기존 status 파일들을 마이그레이션 (PdfRegistry 제거됨 - 더 이상 사용 안 함)
        
        Returns:
            마이그레이션된 파일 수 (항상 0)
        """
        # PdfRegistry 제거됨 - 더 이상 마이그레이션 불필요
        return 0
    
    @staticmethod
    def cleanup_session():
        """
        현재 세션의 임시 파일 정리 (선택적)
        """
        session_dir = SessionManager.get_session_dir()
        if os.path.exists(session_dir):
            import shutil
            try:
                shutil.rmtree(session_dir)
            except Exception:
                pass  # 정리 실패해도 무시

