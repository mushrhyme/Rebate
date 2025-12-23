"""
img 폴더의 PDF 데이터를 FAISS 벡터 DB로 변환하는 스크립트

img 폴더의 모든 하위 폴더에서:
- PDF 파일 (fitz로 텍스트 추출)
- Page*_answer.json (정답 JSON)

파일을 찾아서 RAG Manager에 추가합니다.
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, List, Tuple
import fitz  # PyMuPDF

from modules.core.rag_manager import get_rag_manager
from modules.utils.config import get_project_root


def find_pdf_pages(img_dir: Path) -> List[Dict[str, Any]]:
    """
    img 폴더에서 모든 PDF 페이지 데이터를 찾습니다.
    
    Args:
        img_dir: img 폴더 경로
        
    Returns:
        [page_data, ...] 리스트
        page_data = {
            'pdf_name': str,
            'page_num': int,
            'pdf_path': Path,
            'answer_json_path': Path
        }
    """
    pages = []
    
    # img 폴더의 모든 하위 디렉토리 순회
    for pdf_folder in img_dir.iterdir():
        if not pdf_folder.is_dir():
            continue
        
        pdf_name = pdf_folder.name
        
        # PDF 파일 찾기 (폴더 내부 또는 상위 폴더)
        pdf_file = pdf_folder / f"{pdf_name}.pdf"
        if not pdf_file.exists():
            # 상위 폴더에서도 찾기
            pdf_file = img_dir / f"{pdf_name}.pdf"
        
        if not pdf_file.exists():
            print(f"⚠️ PDF 파일 없음: {pdf_name}")
            continue
        
        # 해당 폴더의 모든 answer.json 파일 찾기
        answer_files = sorted(pdf_folder.glob("Page*_answer.json"))
        
        for answer_file in answer_files:
            try:
                # 페이지 번호 추출 (예: "Page1_answer.json" -> 1)
                page_num_str = answer_file.stem.replace("Page", "").replace("_answer", "")
                page_num = int(page_num_str)
                
                pages.append({
                    'pdf_name': pdf_name,
                    'page_num': page_num,
                    'pdf_folder': pdf_folder,
                    'pdf_path': pdf_file,
                    'answer_json_path': answer_file
                })
                
            except ValueError:
                print(f"⚠️ 페이지 번호 파싱 실패: {answer_file}")
                continue
    
    return pages


def extract_text_from_pdf_page(pdf_path: Path, page_num: int) -> str:
    """
    fitz를 사용하여 PDF에서 특정 페이지의 텍스트를 추출합니다.
    
    Args:
        pdf_path: PDF 파일 경로
        page_num: 페이지 번호 (1부터 시작)
        
    Returns:
        추출된 텍스트 (없으면 빈 문자열)
    """
    try:
        if not pdf_path.exists():
            return ""
        
        doc = fitz.open(pdf_path)
        if page_num < 1 or page_num > doc.page_count:
            doc.close()
            return ""
        
        page = doc.load_page(page_num - 1)  # fitz는 0부터 시작
        text = page.get_text()
        doc.close()
        
        return text.strip() if text else ""
    except Exception as e:
        print(f"⚠️ PDF 텍스트 추출 실패 ({pdf_path}, 페이지 {page_num}): {e}")
        return ""


def load_answer_json(answer_path: Path) -> Dict[str, Any]:
    """
    정답 JSON 파일을 읽습니다.
    
    Args:
        answer_path: 정답 JSON 파일 경로
        
    Returns:
        정답 JSON 딕셔너리 (없으면 빈 딕셔너리)
    """
    try:
        with open(answer_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 정답 JSON 읽기 실패 ({answer_path}): {e}")
        return {}


def build_faiss_db(img_dir: Path = None) -> None:
    """
    img 폴더의 데이터를 FAISS 벡터 DB로 변환합니다.
    
    Args:
        img_dir: img 폴더 경로 (None이면 프로젝트 루트/img)
    """
    if img_dir is None:
        project_root = get_project_root()
        img_dir = project_root / "img"
    
    if not img_dir.exists():
        print(f"❌ img 폴더를 찾을 수 없습니다: {img_dir}")
        return
    
    print(f"📂 img 폴더 스캔 중: {img_dir}")
    
    # 모든 PDF 페이지 데이터 찾기
    pages = find_pdf_pages(img_dir)
    
    if not pages:
        print("❌ 처리할 페이지를 찾을 수 없습니다.")
        return
    
    print(f"✅ {len(pages)}개 페이지 발견\n")
    
    # RAG Manager 초기화
    print("🔄 RAG Manager 초기화 중...")
    try:
        rag_manager = get_rag_manager()
        print("✅ RAG Manager 초기화 완료\n")
    except Exception as e:
        print(f"❌ RAG Manager 초기화 실패: {e}")
        return
    
    # 기존 예제 수 확인
    existing_count = rag_manager.count_examples()
    print(f"📊 기존 벡터 DB 예제 수: {existing_count}개\n")
    
    # 각 페이지를 벡터 DB에 추가
    success_count = 0
    skip_count = 0
    error_count = 0
    
    for i, page_data in enumerate(pages, 1):
        pdf_name = page_data['pdf_name']
        page_num = page_data['page_num']
        pdf_path = page_data['pdf_path']
        answer_path = page_data['answer_json_path']
        
        print(f"[{i}/{len(pages)}] 처리 중: {pdf_name} - Page{page_num}")
        
        # fitz를 사용하여 PDF에서 텍스트 추출
        ocr_text = extract_text_from_pdf_page(pdf_path, page_num)
        if not ocr_text:
            print(f"  ⚠️ PDF에서 텍스트를 추출할 수 없어 건너뜁니다.")
            skip_count += 1
            continue
        
        # 정답 JSON 읽기
        answer_json = load_answer_json(answer_path)
        if not answer_json:
            print(f"  ⚠️ 정답 JSON이 비어있어 건너뜁니다.")
            skip_count += 1
            continue
        
        # 메타데이터 구성
        metadata = {
            'pdf_name': pdf_name,
            'page_num': page_num,
            'source': 'img_folder'
        }
        
        # 벡터 DB에 추가
        try:
            doc_id = rag_manager.add_example(
                ocr_text=ocr_text,
                answer_json=answer_json,
                metadata=metadata
            )
            print(f"  ✅ 추가 완료 (ID: {doc_id[:8]}...)")
            success_count += 1
        except Exception as e:
            print(f"  ❌ 추가 실패: {e}")
            error_count += 1
    
    # 결과 요약
    print("\n" + "="*60)
    print("📊 벡터 DB 구축 결과")
    print("="*60)
    print(f"✅ 성공: {success_count}개")
    print(f"⚠️ 건너뜀: {skip_count}개")
    print(f"❌ 실패: {error_count}개")
    print(f"📈 총 처리: {len(pages)}개")
    print(f"💾 최종 벡터 DB 예제 수: {rag_manager.count_examples()}개")
    print("="*60)


if __name__ == "__main__":
    print("🚀 FAISS 벡터 DB 구축 시작\n")
    build_faiss_db()
    print("\n✅ 완료!")

