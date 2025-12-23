"""
test_img 폴더와 img 폴더의 PDF 파일을 비교 분석하는 스크립트
"""
from pathlib import Path
from collections import defaultdict
import fitz  # PyMuPDF

from modules.utils.config import get_project_root


def analyze_test_img_folder(test_img_dir: Path) -> dict:
    """
    test_img 폴더의 PDF 파일들을 분석합니다.
    
    Returns:
        {
            'pdf_count': int,
            'total_pages': int,
            'pdfs': [
                {
                    'name': str,
                    'path': str,
                    'pages': int
                }
            ]
        }
    """
    result = {
        'pdf_count': 0,
        'total_pages': 0,
        'pdfs': []
    }
    
    # test_img 폴더의 모든 PDF 파일 찾기 (재귀적으로)
    pdf_files = sorted(test_img_dir.rglob("*.pdf"))
    
    for pdf_file in pdf_files:
        try:
            doc = fitz.open(pdf_file)
            page_count = len(doc)
            doc.close()
            
            # 상대 경로로 표시
            relative_path = pdf_file.relative_to(test_img_dir)
            
            result['pdfs'].append({
                'name': pdf_file.stem,  # 확장자 제외한 파일명
                'path': str(relative_path),
                'full_path': str(pdf_file),
                'pages': page_count
            })
            
            result['pdf_count'] += 1
            result['total_pages'] += page_count
            
        except Exception as e:
            print(f"⚠️ PDF 파일 처리 실패 ({pdf_file}): {e}")
            continue
    
    return result


def analyze_img_folder(img_dir: Path) -> dict:
    """
    img 폴더의 학습용 PDF 파일들을 분석합니다.
    
    Returns:
        {
            'pdf_count': int,
            'total_pages': int,
            'pdf_names': set  # PDF 파일명 (확장자 제외)
        }
    """
    result = {
        'pdf_count': 0,
        'total_pages': 0,
        'pdf_names': set()
    }
    
    # img 폴더의 하위 디렉토리 순회
    for pdf_folder in img_dir.iterdir():
        if not pdf_folder.is_dir():
            continue
        
        pdf_name = pdf_folder.name
        
        # PDF 파일 찾기
        pdf_file = pdf_folder / f"{pdf_name}.pdf"
        if not pdf_file.exists():
            pdf_file = img_dir / f"{pdf_name}.pdf"
        
        if not pdf_file.exists():
            continue
        
        try:
            doc = fitz.open(pdf_file)
            page_count = len(doc)
            doc.close()
            
            result['pdf_names'].add(pdf_name)
            result['pdf_count'] += 1
            result['total_pages'] += page_count
            
        except Exception as e:
            print(f"⚠️ PDF 파일 처리 실패 ({pdf_name}): {e}")
            continue
    
    return result


def main():
    """메인 함수"""
    project_root = get_project_root()
    test_img_dir = project_root / "test_img"
    img_dir = project_root / "img"
    
    if not test_img_dir.exists():
        print(f"❌ test_img 폴더를 찾을 수 없습니다: {test_img_dir}")
        return
    
    if not img_dir.exists():
        print(f"❌ img 폴더를 찾을 수 없습니다: {img_dir}")
        return
    
    print("="*70)
    print("test_img 폴더 vs img 폴더 비교 분석")
    print("="*70)
    
    # test_img 폴더 분석
    print("\n[1] test_img 폴더 분석 중...")
    test_img_data = analyze_test_img_folder(test_img_dir)
    
    print(f"\n📊 test_img 폴더 통계:")
    print(f"  - PDF 파일 개수: {test_img_data['pdf_count']}개")
    print(f"  - 총 페이지 수: {test_img_data['total_pages']}장")
    
    # img 폴더 분석
    print("\n[2] img 폴더 (학습용) 분석 중...")
    img_data = analyze_img_folder(img_dir)
    
    print(f"\n📊 img 폴더 통계:")
    print(f"  - PDF 파일 개수: {img_data['pdf_count']}개")
    print(f"  - 총 페이지 수: {img_data['total_pages']}장")
    
    # 중복 확인
    print("\n[3] 중복 파일 확인:")
    print("-"*70)
    
    duplicates = []
    unique_test = []
    
    for pdf_info in test_img_data['pdfs']:
        test_name = pdf_info['name']
        
        # 파일명이 img 폴더에 있는지 확인
        is_duplicate = False
        for img_name in img_data['pdf_names']:
            # 파일명 비교 (대소문자 무시, 공백 무시)
            if test_name.lower().strip() == img_name.lower().strip():
                duplicates.append({
                    'test_name': test_name,
                    'test_path': pdf_info['path'],
                    'img_name': img_name,
                    'test_pages': pdf_info['pages']
                })
                is_duplicate = True
                break
        
        if not is_duplicate:
            unique_test.append(pdf_info)
    
    if duplicates:
        print(f"\n⚠️ 중복 발견: {len(duplicates)}개 파일")
        for dup in duplicates:
            print(f"\n  📄 {dup['test_name']}:")
            print(f"     test_img 경로: {dup['test_path']}")
            print(f"     img 폴더 이름: {dup['img_name']}")
            print(f"     test_img 페이지: {dup['test_pages']}장")
    else:
        print("\n✅ 중복 파일 없음")
    
    print(f"\n📊 test_img 폴더 고유 파일: {len(unique_test)}개")
    
    # test_img 폴더의 PDF 목록 (중복 제외)
    if unique_test:
        print(f"\n[4] test_img 폴더 고유 PDF 목록:")
        print("-"*70)
        for pdf_info in unique_test:
            print(f"  - {pdf_info['name']}: {pdf_info['pages']}장 ({pdf_info['path']})")
    
    # 전체 요약
    print("\n" + "="*70)
    print("[5] 전체 요약")
    print("="*70)
    print(f"test_img 폴더:")
    print(f"  - PDF 파일: {test_img_data['pdf_count']}개")
    print(f"  - 총 페이지: {test_img_data['total_pages']}장")
    print(f"  - 중복 파일: {len(duplicates)}개")
    print(f"  - 고유 파일: {len(unique_test)}개")
    
    print(f"\nimg 폴더 (학습용):")
    print(f"  - PDF 파일: {img_data['pdf_count']}개")
    print(f"  - 총 페이지: {img_data['total_pages']}장")
    
    print("="*70)


if __name__ == "__main__":
    main()

