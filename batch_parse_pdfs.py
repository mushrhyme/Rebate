"""
raw_data 폴더의 모든 PDF 파일을 일괄 파싱하는 스크립트

각 PDF 파일에 대해 Gemini Vision API로 파싱을 수행하고
새로운 저장 구조(img/, result/)에 결과를 저장합니다.
"""

import sys
from pathlib import Path
from typing import List

# 프로젝트 루트를 경로에 추가
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root / 'src'))

from gemini_extractor import extract_pages_with_gemini
from storage_utils import save_page_result, get_img_dir


def batch_parse_pdfs(raw_data_dir: str = "raw_data", dpi: int = 300, force_reparse: bool = False):
    """
    raw_data 폴더의 모든 PDF 파일을 일괄 파싱
    
    Args:
        raw_data_dir: PDF 파일이 있는 디렉토리 (기본값: "raw_data")
        dpi: PDF 변환 해상도 (기본값: 300)
        force_reparse: 기존 캐시를 무시하고 강제로 재파싱 (기본값: False)
    """
    raw_data_path = Path(raw_data_dir)
    
    if not raw_data_path.exists():
        print(f"❌ 디렉토리를 찾을 수 없습니다: {raw_data_path}")
        return
    
    # PDF 파일 찾기
    pdf_files = list(raw_data_path.glob("*.pdf"))
    
    if not pdf_files:
        print(f"❌ {raw_data_path}에 PDF 파일이 없습니다.")
        return
    
    print(f"📁 발견된 PDF 파일: {len(pdf_files)}개")
    print("=" * 60)
    
    # 각 PDF 파일 처리
    for idx, pdf_file in enumerate(pdf_files, 1):
        pdf_name = pdf_file.stem  # 확장자 제외한 파일명
        print(f"\n[{idx}/{len(pdf_files)}] 처리 중: {pdf_file.name}")
        print("-" * 60)
        
        try:
            # 이미지 저장 디렉토리 (새 구조: img/{pdf_name}/)
            image_output_dir = get_img_dir(pdf_name)
            
            # Gemini 파싱 수행 (새로운 저장 구조 사용)
            page_results, image_paths = extract_pages_with_gemini(
                pdf_path=str(pdf_file),
                dpi=dpi,
                use_gemini_cache=not force_reparse,  # 캐시 사용 (force_reparse가 False일 때만)
                save_images=True,  # 이미지 저장 활성화
                image_output_dir=image_output_dir,  # 새 구조로 이미지 저장
                use_history=False  # 배치 파싱에서는 히스토리 사용 안 함
            )
            
            # 전체 파싱 결과 저장 (새 구조: result/{pdf_name}/page_{page_num}/)
            saved_count = 0
            if page_results:
                try:
                    # 각 페이지별로 결과 저장
                    for page_idx, page_json in enumerate(page_results):
                        page_num = page_idx + 1
                        saved_path = save_page_result(pdf_name, page_num, page_json)
                        if saved_path:
                            saved_count += 1
                except Exception as e:
                    print(f"   ⚠️ 결과 저장 중 오류: {e}")
            
            print(f"✅ 완료: {pdf_file.name} ({len(page_results)}개 페이지)")
            print(f"   💾 결과 저장: {saved_count}개 페이지")
            print(f"   🖼️ 이미지 저장: {len(image_paths)}개 파일")
            
        except Exception as e:
            print(f"❌ 실패: {pdf_file.name}")
            print(f"   에러: {e}")
            import traceback
            traceback.print_exc()
            continue
    
    print("\n" + "=" * 60)
    print(f"✅ 일괄 파싱 완료! 총 {len(pdf_files)}개 파일 처리")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="raw_data 폴더의 모든 PDF 파일을 일괄 파싱")
    parser.add_argument(
        "--dir",
        type=str,
        default="raw_data",
        help="PDF 파일이 있는 디렉토리 (기본값: raw_data)"
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="PDF 변환 해상도 (기본값: 300)"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="기존 캐시를 무시하고 강제로 재파싱"
    )
    
    args = parser.parse_args()
    
    batch_parse_pdfs(
        raw_data_dir=args.dir, 
        dpi=args.dpi,
        force_reparse=args.force
    )

