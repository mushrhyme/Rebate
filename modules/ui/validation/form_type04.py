"""
조건청구서④ 검증 함수 - 入出荷支店별 집계
"""

from typing import List, Dict
import streamlit as st
import pandas as pd

# answer_editor_tab.py에서 필요한 함수들 import
from modules.ui.answer_editor_tab import parse_amount


def aggregate_detail_by_branch(detail_pages: List[Dict]) -> Dict[str, int]:
    """
    detail 페이지에서 入出荷支店별로 집계
    
    Args:
        detail_pages: detail 페이지 JSON 딕셔너리 리스트
        
    Returns:
        딕셔너리: {入出荷支店: 합산금액, ...}
    """
    branch_totals = {}  # {入出荷支店: 금액}
    
    for page_data in detail_pages:
        items = page_data.get("items", [])
        if not items:
            continue
        
        for item in items:
            if not isinstance(item, dict):
                continue
            
            # 入出荷支店 필드 확인
            branch = item.get("入出荷支店")
            if not branch:
                continue
            
            # 금액 필드 확인
            amount_str = item.get("金額") or item.get("リベート金額") or item.get("請求金額")
            if not amount_str:
                continue
            
            amount = parse_amount(amount_str)
            
            # 入出荷支店별로 합산
            if branch not in branch_totals:
                branch_totals[branch] = 0
            branch_totals[branch] += amount
    
    return branch_totals


def extract_cover_by_branch(cover_pages: List[Dict]) -> Dict[str, int]:
    """
    cover 페이지에서 入出荷支店별 집계 추출
    
    Args:
        cover_pages: cover 페이지 JSON 딕셔너리 리스트
        
    Returns:
        딕셔너리: {入出荷支店: 금액, ...}
    """
    branch_totals = {}  # {入出荷支店: 금액}
    
    for page_data in cover_pages:
        items = page_data.get("items", [])
        if not items:
            continue
        
        for item in items:
            if not isinstance(item, dict):
                continue
            
            # 入出荷支店 필드 확인
            branch = item.get("入出荷支店")
            if not branch:
                continue
            
            # 금액 필드 확인
            amount_str = item.get("金額") or item.get("リベート金額") or item.get("請求金額")
            if not amount_str:
                continue
            
            amount = parse_amount(amount_str)
            
            # 入出荷支店별로 합산
            if branch not in branch_totals:
                branch_totals[branch] = 0
            branch_totals[branch] += amount
    
    return branch_totals


def create_branch_comparison_dataframe(
    detail_totals: Dict[str, int],
    cover_totals: Dict[str, int]
) -> pd.DataFrame:
    """
    入出荷支店별 비교 데이터프레임 생성
    
    Args:
        detail_totals: detail 페이지의 入出荷支店별 합산금액
        cover_totals: cover 페이지의 入出荷支店별 집계
        
    Returns:
        비교 데이터프레임
    """
    comparison_data = []
    all_branches = set(list(detail_totals.keys()) + list(cover_totals.keys()))
    
    for branch in sorted(all_branches):
        calculated_amount = detail_totals.get(branch, 0)  # 계산금액
        actual_amount = cover_totals.get(branch, 0)       # 실제금액
        diff = calculated_amount - actual_amount           # 차이
        match = abs(diff) < 1                              # 1원 이하 차이는 일치로 간주
        
        comparison_data.append({
            "入出荷支店": branch,
            "計算金額": f"{calculated_amount:,}",
            "実際金額": f"{actual_amount:,}",
            "差額": f"{diff:,}",
            "状態": "✅ 一致" if match else "❌ 不一致"
        })
    
    return pd.DataFrame(comparison_data) if comparison_data else pd.DataFrame()


def validate_form_type04(
    detail_pages: List[Dict],
    summary_pages: List[Dict],
    cover_pages: List[Dict]
):
    """
    조건청구서④ 검증 함수 - 入出荷支店별 집계
    
    Args:
        detail_pages: detail 페이지 JSON 딕셔너리 리스트
        summary_pages: summary 페이지 JSON 딕셔너리 리스트 (사용 안 함)
        cover_pages: cover 페이지 JSON 딕셔너리 리스트
    """
    with st.expander("💰 入出荷支店別集計比較 (cover比較)", expanded=False):
        if detail_pages and cover_pages:
            # 入出荷支店별 집계
            detail_totals = aggregate_detail_by_branch(detail_pages)
            cover_totals = extract_cover_by_branch(cover_pages)
            
            if detail_totals or cover_totals:
                # 비교 테이블 표시
                comparison_df = create_branch_comparison_dataframe(detail_totals, cover_totals)
                
                if not comparison_df.empty:
                    st.dataframe(comparison_df, width='stretch', hide_index=True)
                else:
                    st.info("入出荷支店別の比較データがありません。")
            else:
                st.info("入出荷支店別のデータがありません。")
        else:
            if not detail_pages:
                st.info("ℹ️ detailページがないため検証できません。")
            if not cover_pages:
                st.warning("⚠️ coverページが見つかりません。")

