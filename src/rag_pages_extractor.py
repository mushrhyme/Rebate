"""
RAG 기반 페이지 추출 모듈

OCR 텍스트를 추출한 후 벡터 DB에서 유사한 예제를 검색하고,
RAG를 사용하여 JSON을 추출합니다.
"""

import os
import time
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from src.upstage_extractor import UpstageExtractor
from src.rag_extractor import extract_json_with_rag


def extract_pages_with_rag(
    pdf_path: str,
    openai_api_key: Optional[str] = None,
    openai_model: Optional[str] = None,
    dpi: Optional[int] = None,
    save_images: bool = False,
    image_output_dir: Optional[str] = None,
    question: Optional[str] = None,
    top_k: Optional[int] = None,
    similarity_threshold: Optional[float] = None,
    progress_callback: Optional[Callable[[int, int, str], None]] = None
) -> tuple[List[Dict[str, Any]], List[str], Optional[List[Image.Image]]]:
    """
    PDF 파일을 RAG 기반으로 분석하여 페이지별 JSON 결과 반환
    
    Args:
        pdf_path: PDF 파일 경로
        openai_api_key: OpenAI API 키 (None이면 환경변수 사용)
        openai_model: OpenAI 모델 이름 (None이면 config에서 가져옴)
        dpi: PDF 변환 해상도 (None이면 config에서 가져옴)
        save_images: 이미지를 파일로 저장할지 여부 (기본값: False)
        image_output_dir: 이미지 저장 디렉토리 (사용 안 함)
        question: 질문 텍스트 (None이면 config에서 가져옴)
        top_k: 검색할 예제 수 (None이면 config에서 가져옴)
        similarity_threshold: 최소 유사도 임계값 (None이면 config에서 가져옴)
        
    Returns:
        (페이지별 JSON 결과 리스트, 이미지 파일 경로 리스트, PIL Image 객체 리스트) 튜플
    """
    # 설정값 가져오기 (파라미터가 None이면 config에서 가져옴)
    from modules.utils.config import get_rag_config
    config = get_rag_config()
    
    openai_model = openai_model or config.openai_model
    dpi = dpi or config.dpi
    question = question or config.question
    top_k = top_k if top_k is not None else config.top_k
    similarity_threshold = similarity_threshold if similarity_threshold is not None else config.similarity_threshold
    rag_llm_workers = config.rag_llm_parallel_workers  # RAG+LLM 병렬 워커 수
    ocr_delay = config.ocr_request_delay  # OCR 요청 간 딜레이
    
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
    from modules.utils.config import get_project_root
    project_root = get_project_root()
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
    
    from src.pdf_processor import PdfImageConverter
    pdf_processor = PdfImageConverter(dpi=dpi)
    images = pdf_processor.convert_pdf_to_images(pdf_path)
    pil_images = images
    print(f"PDF 변환 완료: {len(images)}개 페이지")
    
    # 이미지 경로 리스트 초기화
    image_paths = [None] * len(images)
    
    # 디버깅: 분석 통계
    analysis_stats = {
        "total": len(images),
        "success": 0,
        "failed": 0,
        "empty_items": 0,
        "with_items": 0,
        "page_details": []
    }
    
    # 1단계: Upstage OCR 순차 처리 (Rate limit 방지)
    print(f"📝 1단계: Upstage OCR 순차 처리 시작 ({len(images)}개 페이지, 요청 간 딜레이: {ocr_delay}초)")
    upstage_extractor = UpstageExtractor()
    ocr_texts = []  # OCR 텍스트 저장
    
    for idx, image in enumerate(images):
        page_num = idx + 1
        total_pages = len(images)
        
        # 첫 번째 페이지가 아닌 경우 요청 간 딜레이 (Rate limit 방지)
        if idx > 0 and ocr_delay > 0:
            print(f"\n⏳ {ocr_delay}초 대기 중... (Rate limit 방지)", end="", flush=True)
            time.sleep(ocr_delay)
            print(" 완료")
        
        if progress_callback:
            progress_callback(page_num, total_pages, f"🔍 페이지 {page_num}/{total_pages}: Upstage OCR 작업 중...")
        
        print(f"페이지 {page_num}/{total_pages} OCR 중...", end="", flush=True)
        
        tmp_path = None
        try:
            # 임시 이미지 파일로 저장
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp_file:
                image.save(tmp_file.name, "PNG")
                tmp_path = tmp_file.name
            
            # 디버깅: 원본 이미지 저장
            try:
                os.makedirs(debug_dir, exist_ok=True)
                debug_image_path = os.path.join(debug_dir, f"page_{page_num}_original_image.png")
                image.save(debug_image_path, "PNG")
                print(f"  💾 디버깅: 원본 이미지 저장 완료 - {debug_image_path}")
            except Exception as debug_error:
                print(f"  ⚠️ 원본 이미지 저장 실패: {debug_error}")
            
            try:
                ocr_text = upstage_extractor.extract_text(tmp_path)
                if not ocr_text or len(ocr_text.strip()) == 0:
                    raise Exception("OCR 텍스트가 비어있습니다")
                
                ocr_texts.append(ocr_text)
                print(f" 완료")
                
            except Exception as e:
                error_msg = str(e)
                print(f" 실패 - {error_msg}")
                ocr_texts.append(None)  # 실패한 페이지는 None으로 표시
                
        finally:
            # 임시 파일 삭제
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
    
    print(f"✅ OCR 완료: {len([t for t in ocr_texts if t is not None])}/{len(images)}개 페이지 성공\n")
    
    # 2단계: RAG+LLM 병렬 처리 (OCR 텍스트가 있는 페이지만)
    stats_lock = Lock()
    
    def process_rag_llm(idx: int, ocr_text: str) -> tuple[int, Dict[str, Any], Optional[str]]:
        """
        RAG+LLM 처리 함수 (스레드에서 실행)
        
        Args:
            idx: 페이지 인덱스 (0부터 시작)
            ocr_text: OCR 추출된 텍스트
        
        Returns:
            (페이지 인덱스, 페이지 JSON 결과, 에러 메시지) 튜플
        """
        page_num = idx + 1
        total_pages = len(images)
        page_detail = {"page_num": page_num, "status": "unknown", "items_count": 0, "error": None}
        
        try:
            if progress_callback:
                progress_callback(page_num, total_pages, f"🔎 페이지 {page_num}/{total_pages}: RAG 검색 중...")
            
            print(f"페이지 {page_num}/{total_pages} RAG+LLM 처리 중...", end="", flush=True)
            
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
                page_detail["status"] = "success_with_items"
            else:
                page_detail["status"] = "success_empty"
            
            if progress_callback:
                progress_callback(page_num, total_pages, f"✅ 페이지 {page_num}/{total_pages} 완료 ({items_count}개 items)")
            
            print(f" 완료 ({items_count}개 items)")
            
            return (idx, page_json, None)
            
        except Exception as e:
            error_msg = str(e)
            print(f" 실패 - {error_msg}")
            if progress_callback:
                progress_callback(page_num, total_pages, f"❌ 페이지 {page_num}/{total_pages} 실패: {error_msg}")
            
            page_detail["status"] = "failed"
            page_detail["error"] = error_msg
            
            # 실패한 페이지는 빈 결과로 반환
            error_result = {
                "items": [],
                "page_role": "detail",
                "error": error_msg
            }
            return (idx, error_result, error_msg)
        finally:
            # 통계 업데이트 (스레드 안전)
            with stats_lock:
                analysis_stats["page_details"].append(page_detail)
                if page_detail["status"] == "failed":
                    analysis_stats["failed"] += 1
                else:
                    analysis_stats["success"] += 1
                    if page_detail["items_count"] > 0:
                        analysis_stats["with_items"] += 1
                    else:
                        analysis_stats["empty_items"] += 1
    
    # RAG+LLM 병렬 처리
    page_results = {}
    valid_ocr_indices = [(idx, ocr_text) for idx, ocr_text in enumerate(ocr_texts) if ocr_text is not None]
    
    if len(valid_ocr_indices) == 0:
        # OCR이 모두 실패한 경우
        print("⚠️ 모든 페이지 OCR 실패")
        page_jsons = [{
            "items": [],
            "page_role": "detail",
            "error": "OCR 실패"
        } for _ in range(len(images))]
        return page_jsons, image_paths, pil_images
    
    # 병렬 처리 여부 결정 (유효한 OCR 텍스트가 2개 이상일 때만 병렬 처리)
    use_parallel_rag = len(valid_ocr_indices) > 1
    
    if use_parallel_rag:
        # 병렬 처리: ThreadPoolExecutor 사용
        max_workers = min(rag_llm_workers, len(valid_ocr_indices))
        print(f"🚀 2단계: RAG+LLM 병렬 처리 시작 (최대 {max_workers}개 스레드)")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 유효한 OCR 텍스트에 대해 Future 제출
            future_to_idx = {
                executor.submit(process_rag_llm, idx, ocr_text): idx
                for idx, ocr_text in valid_ocr_indices
            }
            
            # 완료된 작업부터 처리
            completed_count = 0
            for future in as_completed(future_to_idx):
                idx, page_json, error = future.result()
                page_results[idx] = page_json
                completed_count += 1
                
                # 진행 상황 출력
                if error:
                    print(f"페이지 {idx+1}/{len(images)} RAG+LLM 처리 실패: {error}")
                
                if progress_callback:
                    progress_callback(completed_count, len(valid_ocr_indices), f"진행 중... ({completed_count}/{len(valid_ocr_indices)}개 페이지 완료)")
    else:
        # 순차 처리 (OCR 텍스트가 1개일 때)
        idx, ocr_text = valid_ocr_indices[0]
        idx, page_json, error = process_rag_llm(idx, ocr_text)
        page_results[idx] = page_json
    
    # OCR 실패한 페이지는 빈 결과로 추가
    for idx, ocr_text in enumerate(ocr_texts):
        if ocr_text is None:
            page_results[idx] = {
                "items": [],
                "page_role": "detail",
                "error": "OCR 실패"
            }
    
    # 모든 페이지 인덱스가 page_results에 있는지 확인 (누락된 경우 빈 결과로 추가)
    for idx in range(len(images)):
        if idx not in page_results:
            page_results[idx] = {
                "items": [],
                "page_role": "detail",
                "error": "처리되지 않음"
            }
    
    # 모든 페이지 인덱스가 page_results에 있는지 확인 (누락된 경우 빈 결과로 추가)
    for idx in range(len(images)):
        if idx not in page_results:
            print(f"⚠️ 페이지 {idx+1} 결과가 없어 빈 결과로 추가합니다.")
            page_results[idx] = {
                "items": [],
                "page_role": "detail",
                "error": "처리되지 않음"
            }
    
    # 인덱스 순서대로 결과 리스트 생성
    page_jsons = [page_results[i] for i in range(len(images))]
    
    # 디버깅: 결과 확인
    try:
        print(f"\n📋 최종 결과 확인: {len(page_jsons)}개 페이지 결과 생성됨")
        for idx, result in enumerate(page_jsons):
            items_count = len(result.get("items", []))
            error = result.get("error")
            status = f"{items_count}개 items" if items_count > 0 else (f"오류: {error}" if error else "빈 결과")
            print(f"  - 페이지 {idx+1}: {status}")
        
        # 분석 통계 출력
        print(f"\n📊 RAG 분석 통계:")
        print(f"  - 전체 페이지: {analysis_stats['total']}개")
        print(f"  - 분석 성공: {analysis_stats['success']}개 (items 있음: {analysis_stats['with_items']}개, items 없음: {analysis_stats['empty_items']}개)")
        print(f"  - 분석 실패: {analysis_stats['failed']}개")
        print(f"\n📋 페이지별 상세:")
        for detail in analysis_stats.get("page_details", []):
            status_icon = "✅" if detail["status"].startswith("success") else "❌"
            items_info = f", {detail['items_count']}개 items" if detail["items_count"] > 0 else ""
            error_info = f", 오류: {detail['error']}" if detail.get("error") else ""
            print(f"  {status_icon} 페이지 {detail['page_num']}: {detail['status']}{items_info}{error_info}")
    except Exception as stats_error:
        print(f"\n⚠️ 통계 출력 중 오류 발생 (결과는 정상 반환): {stats_error}")
        import traceback
        print(f"  - 상세:\n{traceback.format_exc()}")
    
    # 반환값 검증
    if page_jsons is None:
        raise ValueError("page_jsons가 None입니다")
    if not isinstance(page_jsons, list):
        raise ValueError(f"page_jsons가 리스트가 아닙니다: {type(page_jsons)}")
    if len(page_jsons) == 0:
        raise ValueError("page_jsons가 비어있습니다")
    
    print(f"\n✅ extract_pages_with_rag 반환 준비 완료: {len(page_jsons)}개 페이지, {len(image_paths) if image_paths else 0}개 이미지 경로, {len(pil_images) if pil_images else 0}개 PIL 이미지")
    
    return page_jsons, image_paths, pil_images

