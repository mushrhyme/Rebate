"""
PDF 메타데이터 레지스트리 모듈

단일 소스 원칙: 모든 PDF 메타데이터를 pdf_registry.json에 중앙 집중 관리
"""

import os
import json
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime


class PdfRegistry:
    """
    PDF 메타데이터 레지스트리 클래스
    
    pdf_registry.json 파일을 통해 모든 PDF의 메타데이터를 관리합니다.
    원자적 파일 I/O를 통해 Streamlit rerun 환경에서도 안전하게 동작합니다.
    """
    
    REGISTRY_FILE = Path("pdf_registry.json")
    
    @staticmethod
    def _get_registry_path() -> Path:
        """
        레지스트리 파일 경로 반환
        
        Returns:
            pdf_registry.json 파일 경로
        """
        from modules.utils.config import get_project_root
        project_root = get_project_root()
        return project_root / PdfRegistry.REGISTRY_FILE
    
    @staticmethod
    def load() -> Dict[str, Any]:
        """
        레지스트리 데이터 로드
        
        Returns:
            {pdf_name: metadata} 형태의 딕셔너리
        """
        registry_path = PdfRegistry._get_registry_path()
        
        if not registry_path.exists():
            return {}
        
        try:
            with open(registry_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError, OSError):
            # 파일이 손상된 경우 빈 딕셔너리 반환
            return {}
    
    @staticmethod
    def save(registry_data: Dict[str, Any]):
        """
        레지스트리 데이터 저장 (원자적 쓰기)
        
        Args:
            registry_data: {pdf_name: metadata} 형태의 딕셔너리
        """
        registry_path = PdfRegistry._get_registry_path()
        
        try:
            # 임시 파일에 먼저 쓰기 (원자적 I/O)
            with tempfile.NamedTemporaryFile(
                mode='w',
                encoding='utf-8',
                dir=registry_path.parent,
                delete=False,
                suffix='.tmp'
            ) as tmp_file:
                json.dump(registry_data, tmp_file, ensure_ascii=False, indent=2)
                tmp_path = tmp_file.name
            
            # 원자적 이동 (rename은 원자적 연산)
            os.replace(tmp_path, str(registry_path))
        except (IOError, OSError) as e:
            # 임시 파일이 남아있을 수 있으므로 정리 시도
            try:
                if 'tmp_path' in locals() and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except:
                pass
            raise RuntimeError(f"Failed to save registry: {e}")
    
    @staticmethod
    def list_pdfs() -> List[str]:
        """
        등록된 모든 PDF 목록 반환
        
        Returns:
            PDF 파일명 리스트 (확장자 제외)
        """
        registry_data = PdfRegistry.load()
        return list(registry_data.keys())
    
    @staticmethod
    def get(pdf_name: str) -> Optional[Dict[str, Any]]:
        """
        PDF 메타데이터 조회
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            메타데이터 딕셔너리 또는 None
        """
        registry_data = PdfRegistry.load()
        return registry_data.get(pdf_name)
    
    @staticmethod
    def update(pdf_name: str, **fields) -> bool:
        """
        PDF 메타데이터 업데이트
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            **fields: 업데이트할 필드들 (status, pages, error, source 등)
            
        Returns:
            업데이트 성공 여부
        """
        registry_data = PdfRegistry.load()
        
        if pdf_name not in registry_data:
            registry_data[pdf_name] = {}
        
        # 필드 업데이트
        registry_data[pdf_name].update(fields)
        # last_updated 자동 갱신
        registry_data[pdf_name]["last_updated"] = datetime.now().isoformat()
        
        try:
            PdfRegistry.save(registry_data)
            return True
        except Exception:
            return False
    
    @staticmethod
    def ensure(pdf_name: str, **default_fields) -> Dict[str, Any]:
        """
        PDF 메타데이터가 없으면 생성하고 반환
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            **default_fields: 기본 필드 값들
            
        Returns:
            메타데이터 딕셔너리
        """
        registry_data = PdfRegistry.load()
        
        if pdf_name not in registry_data:
            registry_data[pdf_name] = {
                "status": "pending",
                "pages": 0,
                "error": None,
                "source": "session",
                "last_updated": datetime.now().isoformat(),
                **default_fields
            }
            PdfRegistry.save(registry_data)
        
        return registry_data[pdf_name]
    
    @staticmethod
    def remove(pdf_name: str) -> bool:
        """
        PDF 메타데이터 삭제
        
        Args:
            pdf_name: PDF 파일명 (확장자 제외)
            
        Returns:
            삭제 성공 여부
        """
        registry_data = PdfRegistry.load()
        
        if pdf_name in registry_data:
            del registry_data[pdf_name]
            try:
                PdfRegistry.save(registry_data)
                return True
            except Exception:
                return False
        
        return True  # 이미 없어도 성공으로 간주
    
    @staticmethod
    def get_by_status(status: str) -> Dict[str, Any]:
        """
        특정 상태의 PDF 목록 반환
        
        Args:
            status: 상태 ("processing", "completed", "error", "pending")
            
        Returns:
            {pdf_name: metadata} 형태의 딕셔너리
        """
        registry_data = PdfRegistry.load()
        return {
            pdf_name: metadata
            for pdf_name, metadata in registry_data.items()
            if metadata.get("status") == status
        }
    
    @staticmethod
    def get_by_source(source: str) -> Dict[str, Any]:
        """
        특정 소스의 PDF 목록 반환
        
        Args:
            source: 소스 ("session", "raw_data" 등)
            
        Returns:
            {pdf_name: metadata} 형태의 딕셔너리
        """
        registry_data = PdfRegistry.load()
        return {
            pdf_name: metadata
            for pdf_name, metadata in registry_data.items()
            if metadata.get("source") == source
        }

