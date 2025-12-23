"""
img 폴더의 PDF와 벡터 DB 상태를 비교 분석하는 스크립트
"""
import json
from pathlib import Path
from collections import defaultdict
import fitz  # PyMuPDF

from modules.core.rag_manager import get_rag_manager
from modules.utils.config import get_project_root


def analyze_img_folder(img_dir: Path) -> dict:
    """
    img 폴더의 PDF 파일들을 분석합니다.
    
    Returns:
        {
            'pdf_count': int,           # PDF 파일 개수
            'total_pages': int,         # 총 페이지 수
            'pdfs': [                   # PDF별 상세 정보
                {
                    'name': str,
                    'pages': int,
                    'answer_json_count': int
                }
            ]
        }
    """
    result = {
        'pdf_count': 0,
        'total_pages': 0,
        'pdfs': []
    }
    
    # img 폴더의 모든 하위 디렉토리 순회
    for pdf_folder in sorted(img_dir.iterdir()):
        if not pdf_folder.is_dir():
            continue
        
        pdf_name = pdf_folder.name
        
        # PDF 파일 찾기
        pdf_file = pdf_folder / f"{pdf_name}.pdf"
        if not pdf_file.exists():
            pdf_file = img_dir / f"{pdf_name}.pdf"
        
        if not pdf_file.exists():
            continue
        
        # PDF 페이지 수 확인
        try:
            doc = fitz.open(pdf_file)
            page_count = len(doc)
            doc.close()
        except Exception as e:
            print(f"⚠️ PDF 파일 열기 실패 ({pdf_name}): {e}")
            continue
        
        # answer.json 파일 개수 확인
        answer_files = list(pdf_folder.glob("Page*_answer.json"))
        answer_json_count = len(answer_files)
        
        result['pdfs'].append({
            'name': pdf_name,
            'pages': page_count,
            'answer_json_count': answer_json_count,
            'pdf_path': str(pdf_file)
        })
        
        result['pdf_count'] += 1
        result['total_pages'] += page_count
    
    return result


def analyze_faiss_db() -> dict:
    """
    벡터 DB의 상태를 분석합니다.
    
    Returns:
        {
            'total_examples': int,      # 총 예제 수
            'pdfs': {                   # PDF별 예제 수
                'pdf_name': count
            },
            'pages': {                 # (PDF, 페이지)별 저장 횟수
                ('pdf_name', page_num): count
            }
        }
    """
    try:
        rag_manager = get_rag_manager()
        all_examples = rag_manager.get_all_examples()
        
        result = {
            'total_examples': len(all_examples),
            'pdfs': defaultdict(int),
            'pages': defaultdict(int)
        }
        
        for example in all_examples:
            metadata = example.get('metadata', {})
            pdf_name = metadata.get('pdf_name', 'unknown')
            page_num = metadata.get('page_num', 0)
            
            result['pdfs'][pdf_name] += 1
            result['pages'][(pdf_name, page_num)] += 1
        
        return result
    except Exception as e:
        print(f"❌ 벡터 DB 분석 실패: {e}")
        return {
            'total_examples': 0,
            'pdfs': {},
            'pages': {}
        }


def main():
    """메인 함수"""
    project_root = get_project_root()
    img_dir = project_root / "img"
    
    if not img_dir.exists():
        print(f"❌ img 폴더를 찾을 수 없습니다: {img_dir}")
        return
    
    print("="*70)
    print("img 폴더 vs 벡터 DB 비교 분석")
    print("="*70)
    
    # img 폴더 분석
    print("\n[1] img 폴더 분석 중...")
    img_data = analyze_img_folder(img_dir)
    
    print(f"\n📊 img 폴더 통계:")
    print(f"  - PDF 파일 개수: {img_data['pdf_count']}개")
    print(f"  - 총 페이지 수: {img_data['total_pages']}장")
    
    total_answer_json = sum(pdf['answer_json_count'] for pdf in img_data['pdfs'])
    print(f"  - answer.json 파일 개수: {total_answer_json}개")
    
    print(f"\n📄 PDF별 상세 정보:")
    for pdf in img_data['pdfs']:
        print(f"  - {pdf['name']}:")
        print(f"      페이지: {pdf['pages']}장")
        print(f"      answer.json: {pdf['answer_json_count']}개")
    
    # 벡터 DB 분석
    print("\n[2] 벡터 DB 분석 중...")
    faiss_data = analyze_faiss_db()
    
    print(f"\n📊 벡터 DB 통계:")
    print(f"  - 총 예제 수: {faiss_data['total_examples']}개")
    print(f"  - PDF 종류: {len(faiss_data['pdfs'])}개")
    
    # PDF별 비교
    print(f"\n[3] PDF별 비교:")
    print("-"*70)
    
    # img 폴더의 PDF 목록
    img_pdf_names = {pdf['name'] for pdf in img_data['pdfs']}
    faiss_pdf_names = set(faiss_data['pdfs'].keys())
    
    # 모든 PDF 이름 수집
    all_pdf_names = sorted(img_pdf_names | faiss_pdf_names)
    
    for pdf_name in all_pdf_names:
        img_pdf = next((p for p in img_data['pdfs'] if p['name'] == pdf_name), None)
        faiss_count = faiss_data['pdfs'].get(pdf_name, 0)
        
        if img_pdf:
            img_pages = img_pdf['pages']
            img_answers = img_pdf['answer_json_count']
            print(f"\n📄 {pdf_name}:")
            print(f"  img 폴더: {img_pages}페이지, {img_answers}개 answer.json")
            print(f"  벡터 DB: {faiss_count}개 예제")
            
            if faiss_count > 0:
                # 고유 페이지 수 계산
                unique_pages = {page_num for (name, page_num) in faiss_data['pages'].keys() if name == pdf_name}
                print(f"  벡터 DB 고유 페이지: {len(unique_pages)}개")
                
                # 중복 저장 확인
                page_counts = {k: v for k, v in faiss_data['pages'].items() if k[0] == pdf_name}
                duplicates = {k: v for k, v in page_counts.items() if v > 1}
                if duplicates:
                    avg_duplicates = sum(v for v in duplicates.values()) / len(duplicates)
                    print(f"  ⚠️ 중복 저장: {len(duplicates)}개 페이지가 평균 {avg_duplicates:.1f}회 저장됨")
        else:
            print(f"\n📄 {pdf_name}:")
            print(f"  img 폴더: 없음")
            print(f"  벡터 DB: {faiss_count}개 예제 (⚠️ img 폴더에 없는 PDF)")
    
    # 전체 요약
    print("\n" + "="*70)
    print("[4] 전체 요약")
    print("="*70)
    
    # 고유 페이지 수 계산
    unique_pages_in_faiss = len(set(faiss_data['pages'].keys()))
    
    print(f"img 폴더:")
    print(f"  - PDF 파일: {img_data['pdf_count']}개")
    print(f"  - 총 페이지: {img_data['total_pages']}장")
    print(f"  - answer.json: {total_answer_json}개")
    
    print(f"\n벡터 DB:")
    print(f"  - 총 예제 수: {faiss_data['total_examples']}개")
    print(f"  - 고유 페이지 수: {unique_pages_in_faiss}개")
    print(f"  - PDF 종류: {len(faiss_pdf_names)}개")
    
    print(f"\n비교:")
    print(f"  - answer.json vs 벡터 DB 예제: {total_answer_json}개 vs {faiss_data['total_examples']}개")
    if total_answer_json > 0:
        ratio = faiss_data['total_examples'] / total_answer_json
        print(f"  - 저장 비율: {ratio:.2f}배 (벡터 DB 예제 수 / answer.json 개수)")
    
    if unique_pages_in_faiss > 0:
        ratio = faiss_data['total_examples'] / unique_pages_in_faiss
        print(f"  - 평균 저장 횟수: {ratio:.2f}회/페이지")
    
    print("="*70)


if __name__ == "__main__":
    main()

