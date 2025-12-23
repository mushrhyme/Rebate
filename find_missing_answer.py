"""
img 폴더에서 누락된 answer.json 파일을 찾는 스크립트
"""
from pathlib import Path
import fitz  # PyMuPDF

from modules.utils.config import get_project_root


def find_missing_answers(img_dir: Path):
    """
    img 폴더에서 누락된 answer.json 파일을 찾습니다.
    """
    print("="*70)
    print("누락된 answer.json 파일 찾기")
    print("="*70)
    
    missing_count = 0
    
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
            print(f"\n⚠️ PDF 파일 없음: {pdf_name}")
            continue
        
        # PDF 페이지 수 확인
        try:
            doc = fitz.open(pdf_file)
            page_count = len(doc)
            doc.close()
        except Exception as e:
            print(f"\n⚠️ PDF 파일 열기 실패 ({pdf_name}): {e}")
            continue
        
        # answer.json 파일 찾기
        answer_files = sorted(pdf_folder.glob("Page*_answer.json"))
        answer_pages = set()
        
        for answer_file in answer_files:
            try:
                # 페이지 번호 추출
                page_num_str = answer_file.stem.replace("Page", "").replace("_answer", "")
                page_num = int(page_num_str)
                answer_pages.add(page_num)
            except ValueError:
                continue
        
        # 누락된 페이지 찾기
        all_pages = set(range(1, page_count + 1))
        missing_pages = sorted(all_pages - answer_pages)
        
        if missing_pages:
            missing_count += len(missing_pages)
            print(f"\n📄 {pdf_name}:")
            print(f"   총 페이지: {page_count}장")
            print(f"   answer.json: {len(answer_pages)}개")
            print(f"   ⚠️ 누락된 페이지: {missing_pages}")
            print(f"   누락된 answer.json 파일:")
            for page_num in missing_pages:
                expected_file = pdf_folder / f"Page{page_num}_answer.json"
                print(f"      - {expected_file.name}")
        else:
            print(f"\n✅ {pdf_name}: {page_count}페이지, {len(answer_pages)}개 answer.json (완벽)")
    
    print("\n" + "="*70)
    print(f"총 누락된 answer.json 파일: {missing_count}개")
    print("="*70)


if __name__ == "__main__":
    project_root = get_project_root()
    img_dir = project_root / "img"
    
    if not img_dir.exists():
        print(f"❌ img 폴더를 찾을 수 없습니다: {img_dir}")
    else:
        find_missing_answers(img_dir)

