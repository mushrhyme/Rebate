"""
PDF 관련 유틸리티 함수
"""

import os
from pathlib import Path
from typing import Optional, List, Dict
import fitz  # PyMuPDF
from modules.utils.session_manager import SessionManager


class PdfTextExtractor:
    """
    PDF 텍스트 추출 클래스 (캐싱 지원)
    
    여러 페이지를 처리할 때 성능 향상을 위해 문서를 캐싱합니다.
    """
    
    def __init__(self):
        """PDF 문서 캐시 초기화"""
        self._pdf_cache: Dict[Path, fitz.Document] = {}
    
    def extract_text(self, pdf_path: Path, page_num: int) -> str:
        """
        PDF에서 특정 페이지의 텍스트를 추출합니다.
        
        Args:
            pdf_path: PDF 파일 경로
            page_num: 페이지 번호 (1부터 시작)
            
        Returns:
            추출된 텍스트 (없으면 빈 문자열)
        """
        try:
            if not pdf_path.exists():
                return ""
            
            # 캐시에서 문서 가져오기 또는 로드
            if pdf_path not in self._pdf_cache:
                self._pdf_cache[pdf_path] = fitz.open(pdf_path)
            
            doc = self._pdf_cache[pdf_path]
            if page_num < 1 or page_num > doc.page_count:
                return ""
            
            page = doc.load_page(page_num - 1)
            text = page.get_text()
            return text.strip() if text else ""
        except Exception as e:
            print(f"⚠️ PDF 텍스트 추출 실패 ({pdf_path}, 페이지 {page_num}): {e}")
            return ""
    
    def close_all(self):
        """캐시된 모든 PDF 문서 닫기"""
        for doc in self._pdf_cache.values():
            try:
                doc.close()
            except:
                pass
        self._pdf_cache.clear()
    
    def __del__(self):
        """소멸자: 모든 문서 닫기"""
        self.close_all()


def extract_text_from_pdf_page(
    pdf_path: Path,
    page_num: int
) -> str:
    """
    PDF에서 특정 페이지의 텍스트를 추출합니다.
    
    Args:
        pdf_path: PDF 파일 경로 (Path 객체 또는 문자열)
        page_num: 페이지 번호 (1부터 시작)
        
    Returns:
        추출된 텍스트 (없으면 빈 문자열)
        
    Examples:
        # 단일 페이지
        text = extract_text_from_pdf_page(Path("doc.pdf"), 1)
        
        # 여러 페이지 (캐싱 사용)
        extractor = PdfTextExtractor()
        for page in range(1, 10):
            text = extractor.extract_text(Path("doc.pdf"), page)
        extractor.close_all()
    """
    # Path 객체로 변환
    if isinstance(pdf_path, str):
        pdf_path = Path(pdf_path)
    
    # 캐싱 없이 직접 처리 (단일 페이지 처리용)
    try:
        if not pdf_path.exists():
            return ""
        
        doc = fitz.open(pdf_path)
        try:
            if page_num < 1 or page_num > doc.page_count:
                return ""
            
            page = doc.load_page(page_num - 1)
            text = page.get_text()
            return text.strip() if text else ""
        finally:
            doc.close()
    except Exception as e:
        print(f"⚠️ PDF 텍스트 추출 실패 ({pdf_path}, 페이지 {page_num}): {e}")
        return ""


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



