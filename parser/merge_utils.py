"""
데이터 병합 유틸리티 모듈

여러 페이지의 파싱 결과를 하나의 DataFrame으로 병합합니다.
"""

import pandas as pd
from typing import List, Dict, Any


class MergeUtils:
    """데이터 병합 유틸리티 클래스"""
    
    @staticmethod
    def merge_all_pages(page_results: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        모든 페이지 결과를 하나의 DataFrame으로 병합
        
        Args:
            page_results: 페이지별 파싱 결과 리스트 [{"items": [...], ...}, ...]
            
        Returns:
            병합된 DataFrame
        """
        all_items = []
        
        for page_idx, page_json in enumerate(page_results, 1):
            if not page_json:
                continue
            
            # items 추출
            items = page_json.get("items", [])
            
            if not items:
                continue
            
            # 각 item에 페이지 번호 추가
            for item in items:
                item_copy = item.copy()
                item_copy["ページ番号"] = page_idx
                all_items.append(item_copy)
        
        if not all_items:
            return pd.DataFrame()
        
        # DataFrame으로 변환
        df = pd.DataFrame(all_items)
        
        return df
    
    @staticmethod
    def extract_items_from_page(page_json: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        페이지 JSON에서 items 추출
        
        Args:
            page_json: 페이지 JSON 딕셔너리
            
        Returns:
            items 리스트
        """
        # 다양한 형식 지원
        if "items" in page_json:
            return page_json["items"]
        elif "data" in page_json and "items" in page_json["data"]:
            return page_json["data"]["items"]
        else:
            return []

