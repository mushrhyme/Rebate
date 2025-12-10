"""
PDF 결과 저장 모듈

단순화된 저장 구조:
- result/{pdf_name}/page_1.json
- result/{pdf_name}/page_2.json
- ...

기존 타임스탬프 파일 형식도 읽을 수 있도록 하위 호환성 유지
"""

import os
import json
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime


class PageStorage:
    """
    페이지별 결과 저장 및 로드 클래스
    
    단순화된 파일명 형식 사용: page_N.json
    원자적 파일 I/O를 통해 Streamlit rerun 환경에서도 안전하게 동작합니다.
    """
    
    @staticmethod
    def _get_project_root() -> str:
        """
        프로젝트 루트 디렉토리 경로 반환
        
        Returns:
            프로젝트 루트 디렉토리 경로
        """
        # 현재 파일의 위치에서 프로젝트 루트 찾기
        current_file = Path(__file__).resolve()
        # modules/core/storage.py -> modules/core -> modules -> 프로젝트 루트
        project_root = current_file.parent.parent.parent
        return str(project_root)
    
    @staticmethod
    def _get_result_dir(pdf_name: str) -> str:
        """
        PDF 결과 디렉토리 경로 반환
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            result/{pdf_name}/ 경로
        """
        project_root = PageStorage._get_project_root()
        result_dir = os.path.join(project_root, "result", pdf_name)
        os.makedirs(result_dir, exist_ok=True)
        return result_dir
    
    @staticmethod
    def _get_page_path(pdf_name: str, page_num: int) -> str:
        """
        페이지 결과 파일 경로 반환
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            page_num: 페이지 번호 (1부터 시작)
            
        Returns:
            result/{pdf_name}/page_{page_num}.json 경로
        """
        result_dir = PageStorage._get_result_dir(pdf_name)
        return os.path.join(result_dir, f"page_{page_num}.json")
    
    @staticmethod
    def save_page(
        pdf_name: str,
        page_num: int,
        page_data: Dict[str, Any]
    ) -> str:
        """
        페이지 결과를 page_N.json 형식으로 저장 (원자적 쓰기)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            page_num: 페이지 번호 (1부터 시작)
            page_data: 페이지 데이터 딕셔너리
            
        Returns:
            저장된 파일 경로
        """
        page_path = PageStorage._get_page_path(pdf_name, page_num)
        
        # 페이지 번호를 메타데이터에 포함
        page_data_with_meta = {
            "page_number": page_num,
            "saved_at": datetime.now().isoformat(),
            **page_data
        }
        
        try:
            # 임시 파일에 먼저 쓰기 (원자적 I/O)
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                dir=os.path.dirname(page_path),
                delete=False,
                suffix='.tmp'
            ) as tmp_file:
                json.dump(page_data_with_meta, tmp_file, ensure_ascii=False, indent=2)
                tmp_path = tmp_file.name
            
            # 원자적 이동 (rename은 원자적 연산)
            os.replace(tmp_path, page_path)
            return page_path
            
        except (IOError, OSError) as e:
            # 임시 파일이 남아있을 수 있으므로 정리 시도
            try:
                if 'tmp_path' in locals() and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except:
                pass
            raise RuntimeError(f"Failed to save page {page_num} for {pdf_name}: {e}")
    
    @staticmethod
    def load_page(pdf_name: str, page_num: int) -> Optional[Dict[str, Any]]:
        """
        페이지 결과 로드
        
        먼저 page_N.json 형식을 시도하고, 없으면 기존 타임스탬프 형식도 확인합니다.
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            page_num: 페이지 번호 (1부터 시작)
            
        Returns:
            페이지 데이터 딕셔너리 또는 None
        """
        # 1. 새로운 형식 시도: page_N.json
        page_path = PageStorage._get_page_path(pdf_name, page_num)
        if os.path.exists(page_path):
            try:
                with open(page_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError, OSError):
                pass
        
        # 2. 기존 타임스탬프 형식 시도 (하위 호환성)
        legacy_path = PageStorage._load_legacy_page(pdf_name, page_num)
        if legacy_path:
            try:
                with open(legacy_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 새로운 형식으로 마이그레이션 (선택적)
                    # PageStorage.save_page(pdf_name, page_num, data)
                    return data
            except (json.JSONDecodeError, IOError, OSError):
                pass
        
        return None
    
    @staticmethod
    def _load_legacy_page(pdf_name: str, page_num: int) -> Optional[str]:
        """
        기존 타임스탬프 형식의 페이지 파일 찾기 (하위 호환성)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            page_num: 페이지 번호 (1부터 시작)
            
        Returns:
            파일 경로 또는 None
        """
        project_root = PageStorage._get_project_root()
        legacy_dir = os.path.join(project_root, "result", pdf_name, f"page_{page_num}")
        
        if not os.path.exists(legacy_dir):
            return None
        
        # JSON 파일 목록 가져오기 (타임스탬프 순으로 정렬)
        json_files = [f for f in os.listdir(legacy_dir) if f.endswith('.json')]
        if not json_files:
            return None
        
        # 최신 파일 로드 (파일명이 타임스탬프 형식이므로 정렬하면 최신이 마지막)
        json_files.sort()
        latest_file = json_files[-1]
        return os.path.join(legacy_dir, latest_file)
    
    @staticmethod
    def list_pages(pdf_name: str) -> List[int]:
        """
        PDF의 모든 페이지 번호 리스트 반환 (결과가 있는 페이지만)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            페이지 번호 리스트 (1부터 시작, 정렬됨)
        """
        result_dir = PageStorage._get_result_dir(pdf_name)
        
        if not os.path.exists(result_dir):
            return []
        
        page_numbers = []
        
        # 새로운 형식: page_N.json 파일 확인
        for filename in os.listdir(result_dir):
            if filename.startswith("page_") and filename.endswith(".json"):
                try:
                    # "page_1.json" -> 1
                    page_num = int(filename.replace("page_", "").replace(".json", ""))
                    page_numbers.append(page_num)
                except ValueError:
                    continue
        
        # 기존 형식: page_N/ 디렉토리 확인 (하위 호환성)
        for item in os.listdir(result_dir):
            item_path = os.path.join(result_dir, item)
            if os.path.isdir(item_path) and item.startswith("page_"):
                try:
                    page_num = int(item.replace("page_", ""))
                    if page_num not in page_numbers:
                        # 해당 디렉토리에 JSON 파일이 있는지 확인
                        json_files = [f for f in os.listdir(item_path) if f.endswith('.json')]
                        if json_files:
                            page_numbers.append(page_num)
                except ValueError:
                    continue
        
        return sorted(set(page_numbers))
    
    @staticmethod
    def load_all_pages(pdf_name: str) -> List[Dict[str, Any]]:
        """
        PDF의 모든 페이지 결과 로드
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            페이지 데이터 리스트 (페이지 번호 순으로 정렬)
        """
        page_numbers = PageStorage.list_pages(pdf_name)
        
        if not page_numbers:
            return []
        
        pages = []
        for page_num in page_numbers:
            page_data = PageStorage.load_page(pdf_name, page_num)
            if page_data:
                pages.append(page_data)
        
        return pages
    
    @staticmethod
    def delete_page(pdf_name: str, page_num: int) -> bool:
        """
        페이지 결과 삭제
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            page_num: 페이지 번호 (1부터 시작)
            
        Returns:
            삭제 성공 여부
        """
        page_path = PageStorage._get_page_path(pdf_name, page_num)
        
        if os.path.exists(page_path):
            try:
                os.remove(page_path)
                return True
            except (IOError, OSError):
                return False
        
        # 기존 형식도 삭제 시도
        legacy_dir = os.path.join(PageStorage._get_result_dir(pdf_name), f"page_{page_num}")
        if os.path.exists(legacy_dir):
            try:
                import shutil
                shutil.rmtree(legacy_dir)
                return True
            except (IOError, OSError):
                return False
        
        return True  # 파일이 없어도 성공으로 간주
    
    @staticmethod
    def delete_all_pages(pdf_name: str) -> bool:
        """
        PDF의 모든 페이지 결과 삭제
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            삭제 성공 여부
        """
        result_dir = PageStorage._get_result_dir(pdf_name)
        
        if not os.path.exists(result_dir):
            return True
        
        try:
            import shutil
            shutil.rmtree(result_dir)
            return True
        except (IOError, OSError):
            return False
    
    @staticmethod
    def extract_items_from_page(page_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        페이지 데이터에서 items 리스트 추출
        
        Args:
            page_data: 페이지 데이터 딕셔너리
            
        Returns:
            items 리스트
        """
        # 다양한 형식 지원
        if "items" in page_data:
            return page_data["items"]
        elif "data" in page_data and "items" in page_data["data"]:
            return page_data["data"]["items"]
        else:
            return []
    
    @staticmethod
    def get_page_count(pdf_name: str) -> int:
        """
        PDF의 페이지 수 반환 (결과가 있는 페이지만)
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            페이지 수
        """
        page_numbers = PageStorage.list_pages(pdf_name)
        return max(page_numbers) if page_numbers else 0

