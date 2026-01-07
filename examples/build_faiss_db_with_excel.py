"""
엑셀 변환 텍스트를 사용하여 벡터 DB 구축하는 예시

이 스크립트는 PDF를 엑셀 형식으로 변환한 텍스트를 벡터 DB에 저장합니다.
"""

from pathlib import Path
from build_faiss_db import build_faiss_db


def build_with_excel_method():
    """엑셀 변환 방법으로 벡터 DB 구축"""
    print("="*60)
    print("엑셀 변환 방법으로 벡터 DB 구축")
    print("="*60)
    
    # 엑셀 변환 방법 사용
    build_faiss_db(
        form_folder=None,  # 모든 양식 폴더 처리
        auto_merge=True,   # 자동 merge
        version="v2",      # v2 버전 사용
        text_extraction_method="excel"  # 엑셀 변환 방법 사용
    )


def build_with_pymupdf_method():
    """PyMuPDF 방법으로 벡터 DB 구축 (기본)"""
    print("="*60)
    print("PyMuPDF 방법으로 벡터 DB 구축 (기본)")
    print("="*60)
    
    # 기본 PyMuPDF 방법 사용
    build_faiss_db(
        form_folder=None,
        auto_merge=True,
        version="v2",
        text_extraction_method="pymupdf"  # 기본 방법
    )


if __name__ == "__main__":
    import sys
    
    # 명령줄 인자로 방법 선택
    method = sys.argv[1] if len(sys.argv) > 1 else "excel"
    
    if method == "excel":
        build_with_excel_method()
    elif method == "pymupdf":
        build_with_pymupdf_method()
    else:
        print(f"❌ 알 수 없는 방법: {method}")
        print("사용법: python build_faiss_db_with_excel.py [excel|pymupdf]")
        sys.exit(1)

