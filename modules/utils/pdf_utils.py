"""
PDF 관련 유틸리티 함수
"""

import os
from typing import Optional, List
from utils.session_manager import SessionManager


def find_pdf_path(pdf_name: str) -> Optional[str]:
    """
    PDF 파일 경로 찾기 (세션 디렉토리만 확인)
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        
    Returns:
        PDF 파일 경로 또는 None
    """
    # 세션 디렉토리 확인
    pdfs_dir = SessionManager.get_pdfs_dir()
    pdf_path = os.path.join(pdfs_dir, f"{pdf_name}.pdf")
    
    if os.path.exists(pdf_path):
        return pdf_path
    
    return None


def get_pdf_page_count_from_all_sources(pdf_name: str) -> int:
    """
    PDF의 페이지 수 반환 (PageStorage 사용)
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        
    Returns:
        페이지 수
    """
    return SessionManager.get_pdf_page_count(pdf_name)


def get_all_pdf_list() -> List[str]:
    """
    모든 PDF 목록 반환 (사용자가 요청한 분석 목록만)
    
    Returns:
        PDF 파일명 리스트 (확장자 제외)
    """
    return SessionManager.get_pdf_list()

