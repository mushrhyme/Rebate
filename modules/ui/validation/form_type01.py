"""
조건청구서① 검증 함수
"""

from typing import List, Dict, Tuple, Optional
import streamlit as st
import pandas as pd

# answer_editor_tab.py에서 필요한 함수들 import
from modules.ui.answer_editor_tab import (
    aggregate_detail_by_customer,
    extract_summary_by_customer,
    calculate_detail_tax_excluded_and_tax,
    calculate_detail_service_tax_excluded_and_tax,
    extract_cover_totals
)


def create_customer_comparison_table(
    detail_by_customer: Dict[Tuple[str, str], int],
    summary_by_customer: Dict[Tuple[str, str], int]
) -> pd.DataFrame:
    """
    거래처별 비교 테이블 생성
    
    Args:
        detail_by_customer: detail 페이지의 거래처별 집계 딕셔너리
        summary_by_customer: summary 페이지의 거래처별 집계 딕셔너리
        
    Returns:
        비교 데이터프레임
    """
    comparison_data = []
    all_customers = set(list(detail_by_customer.keys()) + list(summary_by_customer.keys()))
    
    for customer_key in sorted(all_customers):
        customer_name, customer_code = customer_key
        detail_amount = detail_by_customer.get(customer_key, 0)
        summary_amount = summary_by_customer.get(customer_key, 0)
        diff = detail_amount - summary_amount
        match = abs(diff) < 1  # 1원 이하 차이는 일치로 간주
        
        comparison_data.append({
            "得意先名": customer_name or "",
            "得意先コード": customer_code or "",
            "計算金額": f"{detail_amount:,}",
            "実際金額": f"{summary_amount:,}",
            "差額": f"{diff:,}",
            "状態": "✅ 一致" if match else "❌ 不一致"
        })
    
    return pd.DataFrame(comparison_data) if comparison_data else pd.DataFrame()


def create_tax_comparison_dataframe(
    comparison_items: List[Tuple[str, int, int]]
) -> pd.DataFrame:
    """
    세율별 비교 데이터프레임 생성
    
    Args:
        comparison_items: [(区分, 계산金額, 실제金額), ...] 형태의 리스트
        
    Returns:
        비교 데이터프레임
    """
    comparison_data = []
    
    for label, detail_value, cover_value in comparison_items:
        diff = detail_value - cover_value
        match = abs(diff) < 1  # 1원 이하 차이는 일치로 간주
        
        comparison_data.append({
            "区分": label,
            "計算金額": f"{detail_value:,}",
            "実際金額": f"{cover_value:,}",
            "差額": f"{diff:,}",
            "状態": "✅ 一致" if match else "❌ 不一致"
        })
    
    return pd.DataFrame(comparison_data)


def render_customer_comparison(
    detail_pages: List[Dict],
    summary_pages: List[Dict]
):
    """거래처별 비교 섹션 렌더링"""
    with st.expander("📊 得意先名/得意先コード別集計比較 (summary比較)", expanded=False):
        if detail_pages and summary_pages:
            st.caption("ℹ️ タイプの区分なく、得意先基準のみで合計した金額です。")
            
            # 販促_通常 검증
            st.write("**販促_通常:**")
            detail_promo_by_customer = aggregate_detail_by_customer(
                detail_pages, tax_rate=None, item_type="販促_通常"
            )
            summary_promo_by_customer = extract_summary_by_customer(
                summary_pages, tax_rate=None, item_type="販促_通常"
            )
            
            comparison_df_promo = create_customer_comparison_table(
                detail_promo_by_customer, summary_promo_by_customer
            )
            
            if not comparison_df_promo.empty:
                st.dataframe(comparison_df_promo, width='stretch', hide_index=True)
            else:
                st.info("販促_通常の比較データがありません。")
            
            # その他 검증
            detail_service_by_customer = aggregate_detail_by_customer(
                detail_pages, tax_rate=None, item_type="その他"
            )
            summary_service_by_customer = extract_summary_by_customer(
                summary_pages, tax_rate=None, item_type="その他"
            )
            
            if detail_service_by_customer or summary_service_by_customer:
                st.write("**その他:**")
                comparison_df_service = create_customer_comparison_table(
                    detail_service_by_customer, summary_service_by_customer
                )
                
                if not comparison_df_service.empty:
                    st.dataframe(comparison_df_service, width='stretch', hide_index=True)
                else:
                    st.info("その他の比較データがありません。")
        else:
            if not detail_pages:
                st.info("ℹ️ detailページがないため検証できません。")
            if not summary_pages:
                st.warning("⚠️ summaryページが見つかりません。")


def render_tax_rate_comparison(
    detail_pages: List[Dict],
    cover_pages: List[Dict]
):
    """소비세율별 비교 섹션 렌더링"""
    with st.expander("💰 消費税率別総額比較 (cover比較)", expanded=False):
        if detail_pages and cover_pages:
            cover_totals = extract_cover_totals(cover_pages)
            promo_totals = cover_totals.get("販促_通常", {})
            service_totals = cover_totals.get("その他", {})
            
            # detail의 세금 제외 금액 계산
            detail_tax_breakdown = calculate_detail_tax_excluded_and_tax(detail_pages)
            
            # detail의 세금 제외 금액 추출
            detail_8_tax_excluded = detail_tax_breakdown["8%"].get("税抜", 0)
            detail_10_tax_excluded = detail_tax_breakdown["10%"].get("税抜", 0)
            
            # detail의 その他 금액 계산
            detail_service_breakdown = calculate_detail_service_tax_excluded_and_tax(detail_pages)
            detail_service_tax_excluded = detail_service_breakdown.get("税抜", 0)
            detail_service_tax = detail_service_breakdown.get("消費税", 0)
            detail_service_total = detail_service_breakdown.get("合計", 0)
            
            # cover 판촉금 정보
            cover_promo_8_tax_excluded = promo_totals.get("8%", {}).get("税抜", 0)
            cover_promo_8_tax = promo_totals.get("8%", {}).get("消費税", 0)
            cover_promo_8_total = cover_promo_8_tax_excluded + cover_promo_8_tax
            
            cover_promo_10_tax_excluded = promo_totals.get("10%", {}).get("税抜", 0)
            cover_promo_10_tax = promo_totals.get("10%", {}).get("消費税", 0)
            cover_promo_10_total = cover_promo_10_tax_excluded + cover_promo_10_tax
            
            # cover その他 정보
            cover_service_tax_excluded = service_totals.get("税抜金額", 0)
            cover_service_tax = service_totals.get("消費税", 0)
            cover_service_total = service_totals.get("合計", 0)
            
            # 판촉금 검증: 8% 대상
            st.write("**販促_通常 - 8% 対象金額:**")
            detail_8_tax_calculated = round(detail_8_tax_excluded * 0.08)
            detail_8_total_calculated = detail_8_tax_excluded + detail_8_tax_calculated
            
            comparison_items_8 = [
                ("税抜", detail_8_tax_excluded, cover_promo_8_tax_excluded),
                ("消費税", detail_8_tax_calculated, cover_promo_8_tax),
                ("合計 (税抜+消費税)", detail_8_total_calculated, cover_promo_8_total)
            ]
            
            comparison_df_8 = create_tax_comparison_dataframe(comparison_items_8)
            st.dataframe(comparison_df_8, width='stretch', hide_index=True)
            
            # 판촉금 검증: 10% 대상
            if detail_10_tax_excluded > 0 or cover_promo_10_tax_excluded > 0:
                detail_10_tax_calculated = round(detail_10_tax_excluded * 0.10)
                detail_10_total_calculated = detail_10_tax_excluded + detail_10_tax_calculated
                
                st.write("**販促_通常 - 10% 対象金額:**")
                comparison_items_10 = [
                    ("税抜", detail_10_tax_excluded, cover_promo_10_tax_excluded),
                    ("消費税", detail_10_tax_calculated, cover_promo_10_tax),
                    ("合計 (税抜+消費税)", detail_10_total_calculated, cover_promo_10_total)
                ]
                
                comparison_df_10 = create_tax_comparison_dataframe(comparison_items_10)
                st.dataframe(comparison_df_10, width='stretch', hide_index=True)
            
            # その他 검증
            if detail_service_tax_excluded > 0 or cover_service_tax_excluded > 0:
                st.write("**その他:**")
                detail_service_tax_calculated = round(detail_service_tax_excluded * 0.10)  # その他은 일반적으로 10% 세율
                detail_service_total_calculated = detail_service_tax_excluded + detail_service_tax_calculated
                
                comparison_items_service = [
                    ("税抜金額", detail_service_tax_excluded, cover_service_tax_excluded),
                    ("消費税", detail_service_tax_calculated, cover_service_tax),
                    ("合計（税込）", detail_service_total_calculated, cover_service_total)
                ]
                
                comparison_df_service = create_tax_comparison_dataframe(comparison_items_service)
                st.dataframe(comparison_df_service, width='stretch', hide_index=True)
        else:
            if not detail_pages:
                st.info("ℹ️ detailページがないため検証できません。")
            if not cover_pages:
                st.warning("⚠️ coverページが見つかりません。")


def validate_form_type01(
    detail_pages: List[Dict],
    summary_pages: List[Dict],
    cover_pages: List[Dict]
):
    """
    조건청구서① 검증 함수
    
    Args:
        detail_pages: detail 페이지 JSON 딕셔너리 리스트
        summary_pages: summary 페이지 JSON 딕셔너리 리스트
        cover_pages: cover 페이지 JSON 딕셔너리 리스트
    """
    # 거래처별 검증
    render_customer_comparison(detail_pages, summary_pages)
    
    # 소비세율별 검증
    render_tax_rate_comparison(detail_pages, cover_pages)

