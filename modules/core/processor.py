"""
PDF 처리 모듈

PDF 처리 로직을 중앙화하여 관리합니다.
"""

import os
import time
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, Callable
from PIL import Image

# PdfRegistry 제거됨 - DB와 st.session_state로 대체
from .storage import PageStorage


class PdfProcessor:
    """
    PDF 처리 클래스
    
    PDF 파일을 OCR 분석하고 결과를 저장하는 로직을 중앙화합니다.
    """
    
    DEFAULT_DPI = 300
    
    @staticmethod
    def process_pdf(
        pdf_name: str,
        pdf_path: Optional[str] = None,
        dpi: int = DEFAULT_DPI,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Tuple[bool, int, Optional[str], float]:
        """
        저장된 PDF 파일 처리
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            pdf_path: PDF 파일 경로 (None이면 자동으로 찾음)
            dpi: PDF 변환 해상도 (기본값: 300)
            progress_callback: 진행률 콜백 함수 (page_num, total_pages, message)
            
        Returns:
            (성공 여부, 페이지 수, 에러 메시지, 소요 시간) 튜플
        """
        start_time = time.time()
        
        try:
            # 순환 import 방지를 위해 함수 내부에서 import
            from modules.utils.session_manager import SessionManager
            from modules.utils.pdf_utils import find_pdf_path
            
            # 1. PDF 파일 경로 확인
            if pdf_path is None:
                pdf_path = find_pdf_path(pdf_name)
                if pdf_path is None:
                    return False, 0, f"PDF 파일을 찾을 수 없습니다: {pdf_name}", 0.0
            
            # 2. 상태는 st.session_state로 관리 (PdfRegistry 제거됨)
            
            # 3. PDF 파싱 (DB 우선 사용, 없으면 RAG 기반 분석)
            # RAG 기반 파싱만 사용 (무조건 RAG 사용)
            from src.rag_pages_extractor import extract_pages_with_rag
            from modules.utils.config import get_rag_config
            
            config = get_rag_config()
            print(f"\n🔄 PDF 파싱 시작: {pdf_name}")
            try:
                page_results, image_paths, pil_images = extract_pages_with_rag(
                    pdf_path=pdf_path,
                    openai_model=config.openai_model,
                    dpi=dpi if dpi else config.dpi,
                    save_images=False,
                    question=config.question,
                    top_k=config.top_k,
                    similarity_threshold=config.similarity_threshold,
                    progress_callback=progress_callback
                )
                print(f"✅ PDF 파싱 완료: {pdf_name} (결과: {len(page_results) if page_results else 0}개 페이지)")
            except Exception as parse_error:
                print(f"\n❌ PDF 파싱 실패: {pdf_name}")
                print(f"  - 오류: {parse_error}")
                import traceback
                print(f"  - 상세:\n{traceback.format_exc()}")
                raise RuntimeError(f"PDF 파싱 실패: {parse_error}") from parse_error
            
            # page_results가 None이거나 빈 리스트인지 확인
            if page_results is None or len(page_results) == 0:
                raise ValueError("파싱 결과가 없습니다")
            
            # 디버깅: 결과 확인
            print(f"\n📋 processor.py에서 받은 결과: {len(page_results)}개 페이지")
            for idx, result in enumerate(page_results[:3]):  # 처음 3개만 출력
                items_count = len(result.get("items", [])) if isinstance(result, dict) else 0
                print(f"  - 페이지 {idx+1}: {items_count}개 items")
            
            # 4. PIL Image 객체를 bytes로 변환하여 DB에 저장
            try:
                from database.registry import get_db
                import io

                # 전역 DB 인스턴스 사용
                db_manager = get_db()

                # PDF 파일명 (확장자 포함)
                pdf_filename = f"{pdf_name}.pdf"

                # PIL Image 객체를 bytes로 변환
                image_data_list = None
                if pil_images:
                    image_data_list = []
                    for img in pil_images:
                        if img:
                            # PIL Image를 JPEG bytes로 변환
                            img_bytes = io.BytesIO()
                            # RGB 모드로 변환 (JPEG는 RGB만 지원)
                            if img.mode != 'RGB':
                                img = img.convert('RGB')
                            img.save(img_bytes, format='JPEG', quality=95, optimize=True)
                            image_data_list.append(img_bytes.getvalue())
                        else:
                            image_data_list.append(None)
                
                # DB 저장 전 상태 확인
                print(f"\n💾 DB 저장 시작:")
                print(f"  - 저장할 페이지 수: {len(page_results)}개")
                print(f"  - 이미지 데이터 수: {len(image_data_list) if image_data_list else 0}개")
                
                # 각 페이지별 items 개수 확인
                pages_with_items = 0
                pages_without_items = 0
                for idx, page_result in enumerate(page_results, 1):
                    items = page_result.get("items", [])
                    items_count = len(items) if items else 0
                    if items_count > 0:
                        pages_with_items += 1
                        print(f"  - 페이지 {idx}: {items_count}개 items ✅")
                    else:
                        pages_without_items += 1
                        error = page_result.get("error")
                        error_info = f" (오류: {error})" if error else ""
                        print(f"  - 페이지 {idx}: items 없음{error_info} ⚠️")
                
                # DB에 저장 (이미지 데이터 직접 전달)
                session_name = f"RAGパース {pdf_name}"
                try:
                    session_id = db_manager.save_from_page_results(
                        page_results=page_results,
                        pdf_filename=pdf_filename,
                        session_name=session_name,
                        notes="RAG 기반 분석",
                        image_data_list=image_data_list  # 이미지 데이터(bytes) 직접 전달
                    )
                    print(f"\n✅ DB 저장 완료:")
                    print(f"  - session_id: {session_id}")
                    print(f"  - 저장된 페이지 수: {len(page_results)}개")
                    print(f"  - items 있는 페이지: {pages_with_items}개")
                    print(f"  - items 없는 페이지: {pages_without_items}개")
                    
                    # DB 저장 후 검증
                    saved_results = db_manager.get_page_results(
                        pdf_filename=pdf_filename,
                        session_id=session_id,
                        is_latest=False
                    )
                    print(f"  - DB 검증: 실제 저장된 페이지 수 {len(saved_results)}개")
                    if len(saved_results) != len(page_results):
                        print(f"  ⚠️ 경고: 저장 요청한 페이지 수({len(page_results)})와 실제 저장된 페이지 수({len(saved_results)})가 다릅니다!")
                except Exception as save_error:
                    print(f"\n❌ DB 저장 실패:")
                    print(f"  - 오류: {save_error}")
                    import traceback
                    print(f"  - 상세:\n{traceback.format_exc()}")
                    raise
            except Exception as db_error:
                # DB 저장 실패 시 에러 반환
                raise RuntimeError(f"DB 저장 실패: {db_error}")
            
            # 5. 진행률 업데이트 및 썸네일 생성
            for page_num, page_json in enumerate(page_results, 1):
                if page_json:
                    # 썸네일 생성 (선택적) - PIL Image에서 직접 생성
                    try:
                        if pil_images and page_num <= len(pil_images) and pil_images[page_num - 1]:
                            image = pil_images[page_num - 1]
                            # 썸네일 생성 (200x200)
                            thumbnail = image.copy()
                            thumbnail.thumbnail((200, 200), Image.Resampling.LANCZOS)
                            SessionManager.save_thumbnail(pdf_name, page_num, thumbnail)
                    except Exception:
                        pass  # 썸네일 생성 실패해도 계속 진행
                
                # 진행률 콜백 호출
                if progress_callback:
                    progress_callback(page_num, len(page_results), f"ページ {page_num}/{len(page_results)} 処理完了")
                
            # 7. 처리 완료
            elapsed_time = time.time() - start_time
            
            return True, len(page_results), None, elapsed_time
            
        except Exception as e:
            error_msg = str(e)
            elapsed_time = time.time() - start_time
            
            # 에러 상태는 st.session_state로 관리 (PdfRegistry 제거됨)
            
            return False, 0, error_msg, elapsed_time
    
    @staticmethod
    def process_uploaded_pdf(
        uploaded_file,
        pdf_name: str,
        dpi: int = DEFAULT_DPI,
        progress_callback: Optional[Callable[[int, int, str], None]] = None
    ) -> Tuple[bool, int, Optional[str], float]:
        """
        업로드된 PDF 파일 처리
        
        Args:
            uploaded_file: Streamlit UploadedFile 객체
            pdf_name: PDF 파일명 (확장자 제외)
            dpi: PDF 변환 해상도 (기본값: 300)
            progress_callback: 진행률 콜백 함수
            
        Returns:
            (성공 여부, 페이지 수, 에러 메시지, 소요 시간) 튜플
        """
        # 순환 import 방지를 위해 함수 내부에서 import
        from modules.utils.session_manager import SessionManager
        
        # 1. PDF 파일 저장
        pdf_path = SessionManager.save_pdf_file(uploaded_file, pdf_name)
        
        # 2. 상태는 st.session_state로 관리 (PdfRegistry 제거됨)
        
        # 3. 처리 실행
        return PdfProcessor.process_pdf(
            pdf_name=pdf_name,
            pdf_path=pdf_path,
            dpi=dpi,
            progress_callback=progress_callback
        )
    
    @staticmethod
    def can_process_pdf(pdf_name: str) -> bool:
        """
        PDF를 처리할 수 있는지 확인 (PdfRegistry 제거됨 - 항상 True 반환)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            처리 가능 여부 (항상 True)
        """
        # PdfRegistry 제거됨 - 항상 처리 가능
        return True
    
    @staticmethod
    def get_processing_status(pdf_name: str) -> Dict[str, Any]:
        """
        PDF 처리 상태 조회 (PdfRegistry 제거됨 - DB에서 조회)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            상태 딕셔너리
        """
        # DB에서 페이지 수 확인
        try:
            from database.registry import get_db
            db_manager = get_db()
            pdf_filename = f"{pdf_name}.pdf"
            page_results = db_manager.get_page_results(
                pdf_filename=pdf_filename,
                session_id=None,
                is_latest=True
            )
            pages = len(page_results) if page_results else 0
            status = "completed" if pages > 0 else "pending"
        except Exception:
            pages = 0
            status = "pending"
        
        return {
            "status": status,
            "pages": pages,
            "error": None,
            "last_updated": None,
            "pdf_name": pdf_name
        }

