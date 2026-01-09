"""
Upstage OCR 유틸리티 모듈

Upstage OCR API를 사용하여 이미지에서 텍스트를 추출하고,
결과를 파일로 캐싱하여 API 호출을 최소화합니다.
"""

import os
import json
import time
import requests
from pathlib import Path
from typing import Optional

# .env 파일 로드 (config.py와 동일한 방식)
from modules.utils.config import load_env
load_env()


def get_upstage_api_key() -> Optional[str]:
    """
    환경 변수에서 Upstage API 키를 가져옵니다.
    .env 파일에서 자동으로 로드됩니다.
    
    Returns:
        API 키 또는 None
    """
    return os.getenv("UPSTAGE_API_KEY")


def get_upstage_cache_path(pdf_path: Path, page_num: int) -> Path:
    """
    Upstage OCR 결과 캐시 파일 경로를 반환합니다.
    
    Args:
        pdf_path: PDF 파일 경로
        page_num: 페이지 번호 (1부터 시작)
    
    Returns:
        캐시 파일 경로
    """
    # PDF 파일과 같은 디렉토리에 캐시 파일 저장
    cache_dir = pdf_path.parent
    cache_filename = f"{pdf_path.stem}_Page{page_num}_upstage_ocr.json"
    return cache_dir / cache_filename


def load_upstage_cache(cache_path: Path) -> Optional[str]:
    """
    저장된 Upstage OCR 결과를 로드합니다.
    
    Args:
        cache_path: 캐시 파일 경로
    
    Returns:
        OCR 텍스트 또는 None
    """
    if not cache_path.exists():
        return None
    
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache_data = json.load(f)
            return cache_data.get("text", None)
    except Exception as e:
        print(f"⚠️ 캐시 파일 로드 실패 ({cache_path}): {e}")
        return None


def save_upstage_cache(cache_path: Path, text: str):
    """
    Upstage OCR 결과를 캐시 파일로 저장합니다.
    
    Args:
        cache_path: 캐시 파일 경로
        text: OCR 텍스트
    """
    try:
        cache_data = {
            "text": text,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 캐시 파일 저장 실패 ({cache_path}): {e}")


def extract_text_with_upstage(image_path: Path, cache_path: Optional[Path] = None) -> Optional[str]:
    """
    Upstage OCR API를 사용하여 이미지에서 텍스트를 추출합니다.
    캐시 파일이 있으면 API 호출 없이 캐시를 사용합니다.
    
    Args:
        image_path: 이미지 파일 경로
        cache_path: 캐시 파일 경로 (None이면 자동 생성)
    
    Returns:
        추출된 텍스트 또는 None
    """
    # 캐시 파일 경로가 없으면 자동 생성
    if cache_path is None:
        cache_path = image_path.parent / f"{image_path.stem}_upstage_ocr.json"
    
    # 캐시 확인
    cached_text = load_upstage_cache(cache_path)
    if cached_text:
        print(f"✅ Upstage OCR 캐시 사용: {cache_path}")
        return cached_text
    
    # Upstage API 키 확인
    api_key = get_upstage_api_key()
    if not api_key:
        print("⚠️ UPSTAGE_API_KEY 환경 변수가 설정되지 않았습니다.")
        return None
    
    # 이미지 파일 확인
    if not image_path.exists():
        print(f"⚠️ 이미지 파일을 찾을 수 없습니다: {image_path}")
        return None
    
    try:
        # Upstage OCR API 호출
        print(f"🔍 Upstage OCR API 호출 중: {image_path}")
        url = "https://api.upstage.ai/v1/document-digitization"
        headers = {"Authorization": f"Bearer {api_key}"}
        
        # 파일 열기 (requests가 파일을 닫아주므로 with 문 밖에서 열기)
        files = {"document": open(image_path, "rb")}
        data = {"model": "ocr"}
        response = requests.post(url, headers=headers, files=files, data=data)
        
        # 파일 닫기
        files["document"].close()
        
        # 응답 확인
        response.raise_for_status()
        result = response.json()
        
        # 텍스트 추출 (응답 구조에 따라 조정 필요)
        text = None
        if isinstance(result, dict):
            # 응답 구조에 따라 텍스트 추출
            # 일반적으로 "text" 또는 "result" 필드에 텍스트가 있음
            text = result.get("text") or result.get("result") or result.get("content")
            # 만약 다른 구조라면 전체 응답을 문자열로 변환
            if not text:
                # pages나 다른 구조일 수 있음
                if "pages" in result:
                    # 여러 페이지가 있는 경우 모든 텍스트 합치기
                    pages = result.get("pages", [])
                    texts = []
                    for page in pages:
                        if isinstance(page, dict):
                            page_text = page.get("text") or page.get("content")
                            if page_text:
                                texts.append(page_text)
                    text = "\n".join(texts) if texts else None
                else:
                    # 전체 JSON을 문자열로 변환 (디버깅용)
                    text = json.dumps(result, ensure_ascii=False)
        
        if text:
            # 캐시에 저장
            save_upstage_cache(cache_path, text)
            print(f"✅ Upstage OCR 완료 및 캐시 저장: {cache_path}")
            return text
        else:
            print(f"⚠️ Upstage OCR 결과가 비어있습니다: {image_path}")
            print(f"   응답: {result}")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Upstage OCR API 호출 실패 ({image_path}): {e}")
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_detail = e.response.json()
                print(f"   오류 상세: {error_detail}")
            except:
                print(f"   응답 상태 코드: {e.response.status_code}")
        return None
    except Exception as e:
        print(f"⚠️ Upstage OCR 오류 ({image_path}): {e}")
        import traceback
        traceback.print_exc()
        return None


def extract_text_from_pdf_page_with_upstage(pdf_path: Path, page_num: int) -> Optional[str]:
    """
    PDF 페이지를 이미지로 변환한 후 Upstage OCR로 텍스트를 추출합니다.
    
    Args:
        pdf_path: PDF 파일 경로
        page_num: 페이지 번호 (1부터 시작)
    
    Returns:
        추출된 텍스트 또는 None
    """
    import fitz  # PyMuPDF
    
    try:
        # PDF에서 페이지를 이미지로 변환
        doc = fitz.open(pdf_path)
        if page_num < 1 or page_num > doc.page_count:
            doc.close()
            return None
        
        page = doc.load_page(page_num - 1)
        pix = page.get_pixmap(dpi=300)
        img_bytes = pix.tobytes("png")
        doc.close()
        
        # 임시 이미지 파일 생성
        temp_image_path = pdf_path.parent / f"{pdf_path.stem}_Page{page_num}_temp.png"
        with open(temp_image_path, "wb") as f:
            f.write(img_bytes)
        
        # 캐시 파일 경로
        cache_path = get_upstage_cache_path(pdf_path, page_num)
        
        # Upstage OCR 호출
        text = extract_text_with_upstage(temp_image_path, cache_path)
        
        # 임시 이미지 파일 삭제
        try:
            if temp_image_path.exists():
                temp_image_path.unlink()
        except:
            pass
        
        return text
        
    except Exception as e:
        print(f"⚠️ PDF 페이지 이미지 변환 실패 ({pdf_path}, 페이지 {page_num}): {e}")
        return None

