"""
RAG 기반 페이지 추출 모듈

OCR 텍스트를 추출한 후 벡터 DB에서 유사한 예제를 검색하고,
RAG를 사용하여 JSON을 추출합니다.
"""

import os
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from PIL import Image

from src.upstage_extractor import UpstageExtractor
from src.rag_extractor import extract_json_with_rag


def extract_pages_with_rag(
    pdf_path: str,
    openai_api_key: Optional[str] = None,
    openai_model: str = "gpt-4o-2024-08-06",
    dpi: int = 300,
    save_images: bool = False,
    image_output_dir: Optional[str] = None,
    question: str = "이 청구서의 상품별 내역을 JSON으로 추출해라",
    top_k: int = 1,
    similarity_threshold: float = 0.7,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> tuple[List[Dict[str, Any]], List[str], Optional[List[Image.Image]]]:
    """
    PDF 파일을 RAG 기반으로 분석하여 페이지별 JSON 결과 반환
    
    Args:
        pdf_path: PDF 파일 경로
        openai_api_key: OpenAI API 키 (None이면 환경변수 사용)
        openai_model: OpenAI 모델 이름 (기본값: "gpt-4o-2024-08-06")
        dpi: PDF 변환 해상도 (기본값: 300)
        save_images: 이미지를 파일로 저장할지 여부 (기본값: False)
        image_output_dir: 이미지 저장 디렉토리 (사용 안 함)
        question: 질문 텍스트 (기본값: "이 청구서의 상품별 내역을 JSON으로 추출해라")
        top_k: 검색할 예제 수 (기본값: 1)
        similarity_threshold: 최소 유사도 임계값 (기본값: 0.7)
        
    Returns:
        (페이지별 JSON 결과 리스트, 이미지 파일 경로 리스트, PIL Image 객체 리스트) 튜플
    """
    pdf_name = Path(pdf_path).stem
    pdf_filename = f"{pdf_name}.pdf"
    
    # 1. DB에서 먼저 확인
    page_jsons = None
    try:
        from database.registry import get_db
        db_manager = get_db()
        page_jsons = db_manager.get_page_results(
            pdf_filename=pdf_filename,
            session_id=None,
            is_latest=True
        )
        if page_jsons and len(page_jsons) > 0:
            print(f"💾 DB에서 기존 파싱 결과 로드: {len(page_jsons)}개 페이지")
            image_paths = [None] * len(page_jsons)
            return page_jsons, image_paths, None
    except Exception as db_error:
        print(f"⚠️ DB 확인 실패: {db_error}. 새로 파싱합니다.")
    
    # 2. DB에 데이터가 없으면 RAG 기반 파싱
    # 디버깅 폴더 설정 (실제 분석을 수행할 때만 생성)
    # src/rag_pages_extractor.py -> src -> 프로젝트 루트
    project_root = Path(__file__).parent.parent
    debug_base_dir = project_root / "debug"
    debug_dir = debug_base_dir / pdf_name
    if debug_dir.exists():
        import shutil
        shutil.rmtree(debug_dir)
    debug_dir.mkdir(parents=True, exist_ok=True)
    print(f"🔍 디버깅 정보 저장 위치: {debug_dir}")
    # PDF를 이미지로 변환
    if progress_callback:
        progress_callback(0, 0, "🔄 PDF를 이미지로 변환 중...")
    
    from src.openai_extractor import PDFProcessor
    pdf_processor = PDFProcessor(dpi=dpi)
    images = pdf_processor.convert_pdf_to_images(pdf_path)
    pil_images = images
    print(f"PDF 변환 완료: {len(images)}개 페이지")
    
    # 이미지 경로 리스트 초기화
    image_paths = [None] * len(images)
    
    # Upstage로 OCR 텍스트 추출
    upstage_extractor = UpstageExtractor()
    page_jsons = []
    
    # 디버깅: 분석 통계
    analysis_stats = {
        "total": len(images),
        "success": 0,
        "failed": 0,
        "empty_items": 0,
        "with_items": 0,
        "page_details": []
    }
    
    # 각 페이지 처리
    for idx, image in enumerate(images):
        page_num = idx + 1
        total_pages = len(images)
        page_detail = {"page_num": page_num, "status": "unknown", "items_count": 0, "error": None}
        
        try:
            if progress_callback:
                progress_callback(page_num, total_pages, f"📄 페이지 {page_num}/{total_pages} 처리 중...")
            
            print(f"페이지 {page_num}/{total_pages} RAG 파싱 중...", end="", flush=True)
            
            # 임시 이미지 파일로 저장 (Upstage API 사용)
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                image.save(tmp_file.name, "PNG")
                tmp_path = tmp_file.name
            
            try:
                # Upstage로 OCR 텍스트 추출
                if progress_callback:
                    progress_callback(page_num, total_pages, f"🔍 페이지 {page_num}/{total_pages}: Upstage OCR 작업 중...")
                
                ocr_text = upstage_extractor.extract_text(tmp_path)
                if not ocr_text or len(ocr_text.strip()) == 0:
                    raise Exception("OCR 텍스트가 비어있습니다")
                
                # RAG 기반 JSON 추출
                if progress_callback:
                    progress_callback(page_num, total_pages, f"🔎 페이지 {page_num}/{total_pages}: RAG 검색 중...")
                
                # RAG 추출용 progress_callback 래퍼
                def rag_progress_wrapper(msg: str):
                    if progress_callback:
                        progress_callback(page_num, total_pages, f"🤖 페이지 {page_num}/{total_pages}: {msg}")
                
                page_json = extract_json_with_rag(
                    ocr_text=ocr_text,
                    question=question,
                    model_name=openai_model,
                    temperature=0.0,
                    top_k=top_k,
                    similarity_threshold=similarity_threshold,
                    progress_callback=rag_progress_wrapper if progress_callback else None,
                    debug_dir=str(debug_dir),
                    page_num=page_num
                )
                
                # items 개수 확인 (page_json이 딕셔너리인지 확인)
                if not isinstance(page_json, dict):
                    raise Exception(f"예상치 못한 응답 형식: {type(page_json)}. 딕셔너리가 아닙니다.")
                
                items = page_json.get("items", [])
                items_count = len(items) if items else 0
                page_detail["items_count"] = items_count
                
                if items_count > 0:
                    analysis_stats["with_items"] += 1
                    page_detail["status"] = "success_with_items"
                else:
                    analysis_stats["empty_items"] += 1
                    page_detail["status"] = "success_empty"
                
                analysis_stats["success"] += 1
                page_jsons.append(page_json)
                
                if progress_callback:
                    progress_callback(page_num, total_pages, f"✅ 페이지 {page_num}/{total_pages} 완료 ({items_count}개 items)")
                
                print(f" 완료 ({items_count}개 items)")
                
            finally:
                # 임시 파일 삭제
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                    
        except Exception as e:
            error_msg = str(e)
            print(f" 실패 - {error_msg}")
            if progress_callback:
                progress_callback(page_num, total_pages, f"❌ 페이지 {page_num}/{total_pages} 실패: {error_msg}")
            
            analysis_stats["failed"] += 1
            page_detail["status"] = "failed"
            page_detail["error"] = error_msg
            
            # 실패한 페이지는 빈 결과로 추가
            page_jsons.append({
                "items": [],
                "page_role": "detail",
                "error": error_msg
            })
            continue
        finally:
            analysis_stats["page_details"].append(page_detail)
    
    # 분석 통계 출력
    print(f"\n📊 RAG 분석 통계:")
    print(f"  - 전체 페이지: {analysis_stats['total']}개")
    print(f"  - 분석 성공: {analysis_stats['success']}개 (items 있음: {analysis_stats['with_items']}개, items 없음: {analysis_stats['empty_items']}개)")
    print(f"  - 분석 실패: {analysis_stats['failed']}개")
    print(f"\n📋 페이지별 상세:")
    for detail in analysis_stats["page_details"]:
        status_icon = "✅" if detail["status"].startswith("success") else "❌"
        items_info = f", {detail['items_count']}개 items" if detail["items_count"] > 0 else ""
        error_info = f", 오류: {detail['error']}" if detail.get("error") else ""
        print(f"  {status_icon} 페이지 {detail['page_num']}: {detail['status']}{items_info}{error_info}")
    
    return page_jsons, image_paths, pil_images

