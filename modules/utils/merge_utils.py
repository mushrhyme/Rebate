"""
데이터 병합 유틸리티 모듈

여러 페이지의 파싱 결과를 하나의 DataFrame으로 병합합니다.
"""

import pandas as pd
from typing import List, Dict, Any


class MergeUtils:
    """데이터 병합 유틸리티 클래스"""
    
    # 컬럼명 일본어 매핑 (영어 → 일본어)
    COLUMN_NAME_MAPPING = {
        'management_id': '管理番号',
        'customer': '取引先',
        'product_name': '商品名',
        'units_per_case': 'ケース内入数',
        'case_count': 'ケース数',
        'bara_count': 'バラ数',
        'quantity': '数量',
        'amount': '金額',
        # 이미 일본어인 경우는 그대로 유지
        '管理番号': '管理番号',
        '取引先': '取引先',
        '商品名': '商品名',
        'ケース内入数': 'ケース内入数',
        'ケース数': 'ケース数',
        'バラ数': 'バラ数',
        '数量': '数量',
        '金額': '金額'
    }
    
    @staticmethod
    def merge_all_pages(page_results: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        모든 페이지 결과를 하나의 DataFrame으로 병합
        
        Args:
            page_results: 페이지별 파싱 결과 리스트 [{"items": [...], ...}, ...]
            
        Returns:
            병합된 DataFrame (컬럼명은 일본어로 변환됨)
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
                item_copy["ページ番号"] = page_idx  # 페이지番号 = 페이지 번호
                all_items.append(item_copy)
        
        if not all_items:
            return pd.DataFrame()
        
        # DataFrame으로 변환
        df = pd.DataFrame(all_items)
        
        # 컬럼명을 일본어로 변환
        df = MergeUtils._convert_columns_to_japanese(df)
        
        # 컬럼 순서 정렬 (일본어 컬럼명 기준)
        df = MergeUtils._reorder_columns(df)
        
        return df
    
    @staticmethod
    def _convert_columns_to_japanese(df: pd.DataFrame) -> pd.DataFrame:
        """
        DataFrame의 컬럼명을 일본어로 변환
        
        Args:
            df: 원본 DataFrame
            
        Returns:
            컬럼명이 일본어로 변환된 DataFrame
        """
        # 컬럼명 매핑 적용
        column_mapping = {}
        for col in df.columns:
            japanese_name = MergeUtils.COLUMN_NAME_MAPPING.get(col, col)
            if japanese_name != col:
                column_mapping[col] = japanese_name
        
        if column_mapping:
            df = df.rename(columns=column_mapping)
        
        return df
    
    @staticmethod
    def _reorder_columns(df: pd.DataFrame) -> pd.DataFrame:
        """
        컬럼 순서를 정렬 (일본어 컬럼명 기준)
        
        Args:
            df: DataFrame
            
        Returns:
            컬럼 순서가 정렬된 DataFrame
        """
        # 원하는 컬럼 순서 정의
        desired_order = [
            '管理番号',      # management_id
            '取引先',        # customer
            '商品名',        # product_name
            'ケース内入数',  # units_per_case
            'ケース数',      # case_count
            'バラ数',        # bara_count
            '数量',          # quantity
            '金額',          # amount
            'ページ番号'     # page_number (마지막)
        ]
        
        # 실제 존재하는 컬럼만 필터링하고 순서대로 재정렬
        existing_cols = [col for col in desired_order if col in df.columns]
        
        # 나머지 컬럼들도 추가 (원하는 순서에 없는 컬럼들)
        remaining_cols = [col for col in df.columns if col not in existing_cols]
        
        # 최종 컬럼 순서: 원하는 순서 + 나머지 컬럼들
        final_column_order = existing_cols + remaining_cols
        
        return df[final_column_order]
    
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

