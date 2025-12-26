"""
Hash 계산 유틸리티 모듈

PDF 페이지의 텍스트와 answer.json을 기반으로 hash를 계산합니다.
manifest에서 변경 감지에 사용됩니다.
"""

import hashlib
import json
from typing import Dict, Any
from pathlib import Path


def compute_page_hash(pdf_text: str, answer_json: Dict[str, Any]) -> str:
    """
    PDF 페이지의 텍스트와 answer.json을 기반으로 hash를 계산합니다.
    
    Args:
        pdf_text: PDF 페이지에서 추출한 텍스트
        answer_json: answer.json 딕셔너리
        
    Returns:
        SHA256 hash 문자열 (hex)
    """
    # JSON을 정렬된 문자열로 변환 (순서 무관하게 동일한 hash 생성)
    answer_str = json.dumps(answer_json, sort_keys=True, ensure_ascii=False)
    
    # 텍스트와 JSON을 결합
    combined = f"{pdf_text}\n{answer_str}"
    
    # SHA256 hash 계산
    hash_obj = hashlib.sha256(combined.encode('utf-8'))
    return hash_obj.hexdigest()


def compute_file_fingerprint(pdf_path: Path, answer_path: Path) -> Dict[str, Any]:
    """
    파일의 빠른 변경 감지를 위한 fingerprint 계산 (answer.json 기준만)
    
    Args:
        pdf_path: PDF 파일 경로 (사용 안 함, 호환성 유지)
        answer_path: answer.json 파일 경로
        
    Returns:
        {'answer_mtime': float, 'answer_size': int}
    """
    answer_stat = answer_path.stat() if answer_path.exists() else None
    
    return {
        'answer_mtime': answer_stat.st_mtime if answer_stat else 0.0,
        'answer_size': answer_stat.st_size if answer_stat else 0
    }


def get_page_key(pdf_name: str, page_num: int) -> str:
    """
    manifest에서 사용할 페이지 키를 생성합니다.
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        page_num: 페이지 번호 (1부터 시작)
        
    Returns:
        페이지 키 문자열 (예: "docA.pdf:1")
    """
    return f"{pdf_name}:{page_num}"

