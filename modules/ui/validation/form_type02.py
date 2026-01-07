"""
조건청구서② 검증 함수 - 請求No별 집계
"""

from typing import List, Dict, Tuple
import streamlit as st
import pandas as pd
import re

# answer_editor_tab.py에서 필요한 함수들 import
from modules.ui.answer_editor_tab import parse_amount


def extract_cover_by_request_no(cover_pages: List[Dict]) -> Dict[str, Dict]:
    """
    cover 페이지에서 請求No별로 집계
    
    Args:
        cover_pages: cover 페이지 JSON 딕셔너리 리스트
        
    Returns:
        딕셔너리: {請求No: {"税抜金額": 금액, "消費税金額": 금액, "税込金額": 금액}, ...}
    """
    request_no_totals = {}
    
    for page_data in cover_pages:
        totals = page_data.get("totals", {})
        detail_rows = totals.get("明細行", [])
        
        for row in detail_rows:
            if not isinstance(row, dict):
                continue
            
            request_no = row.get("請求No")
            if not request_no:  # 합계 행은 건너뛰기
                continue
            
            tax_rate_str = row.get("税率", "")
            tax_excluded_str = row.get("税抜金額", "0")
            tax_amount_str = row.get("消費税金額", "0")
            tax_included_str = row.get("税込金額", "0")
            
            if request_no not in request_no_totals:
                request_no_totals[request_no] = {
                    "税抜金額": 0,
                    "消費税金額": 0,
                    "税込金額": 0
                }
            
            request_no_totals[request_no]["税抜金額"] += parse_amount(tax_excluded_str)
            request_no_totals[request_no]["消費税金額"] += parse_amount(tax_amount_str)
            request_no_totals[request_no]["税込金額"] += parse_amount(tax_included_str)
    
    return request_no_totals


def extract_detail_by_request_no(detail_pages: List[Dict]) -> Dict[str, int]:
    """
    detail 페이지에서 請求No（契約No）별로 실제금액(税別)만 합산
    주의: リベート計算条件（適用人数）는 무시하고 관리번호 기준으로만 합산

    Args:
        detail_pages: detail 페이지 JSON 딕셔너리 리스트

    Returns:
        딕셔너리: {請求No: 합산금액, ...}
    """
    request_no_totals = {}

    for page_data in detail_pages:
        items = page_data.get("items", [])

        for item in items:
            if not isinstance(item, dict):
                continue

            # 관리번호(請求No（契約No）) 필드 확인 - 이것만 기준으로 합산
            request_no = item.get("請求No（契約No）") or item.get("請求No") or item.get("契約No")
            if not request_no:
                continue

            # 금액 필드 확인 (2번 양식지는 リベート金額（税別） 사용)
            # リベート計算条件（適用人数）는 무시하고 모든 항목 합산
            amount_str = item.get("リベート金額（税別）") or item.get("金額") or item.get("リベート金額")
            if not amount_str:
                continue

            amount = parse_amount(amount_str)

            if request_no not in request_no_totals:
                request_no_totals[request_no] = 0

            # 관리번호별로 실제금액만 합산 (세금 계산 없이, 조건 무시)
            request_no_totals[request_no] += amount
    
    return request_no_totals


def extract_tax_rates_from_cover(cover_pages: List[Dict]) -> Dict[str, float]:
    """
    cover 페이지에서 請求No별 세율 추출

    Args:
        cover_pages: cover 페이지 JSON 딕셔너리 리스트

    Returns:
        딕셔너리: {請求No: 세율(%), ...}
    """
    tax_rates = {}

    for page_data in cover_pages:
        totals = page_data.get("totals", {})
        detail_rows = totals.get("明細行", [])

        for row in detail_rows:
            if not isinstance(row, dict):
                continue

            request_no = row.get("請求No")
            if not request_no:  # 합계 행은 건너뛰기
                continue

            tax_rate_str = row.get("税率", "")
            if tax_rate_str:
                # "8.0%" 형태에서 숫자만 추출
                tax_rate_match = re.search(r'(\d+(?:\.\d+)?)', str(tax_rate_str))
                if tax_rate_match:
                    tax_rate = float(tax_rate_match.group(1))
                    tax_rates[request_no] = tax_rate

    return tax_rates


def create_request_no_comparison_dataframe(
    detail_totals: Dict[str, int],
    cover_totals: Dict[str, Dict],
    tax_rates: Dict[str, float]
) -> pd.DataFrame:
    """
    請求No별 비교 데이터프레임 생성 (관리번호별 합산금액 검증)

    Args:
        detail_totals: detail 페이지의 請求No별 합산금액
        cover_totals: cover 페이지의 請求No별 집계
        tax_rates: 請求No별 세율

    Returns:
        비교 데이터프레임
    """
    comparison_data = []
    all_request_nos = set(list(detail_totals.keys()) + list(cover_totals.keys()))

    for request_no in sorted(all_request_nos):
        detail_amount = detail_totals.get(request_no, 0)
        cover_data = cover_totals.get(request_no, {"税抜金額": 0, "消費税金額": 0, "税込金額": 0})
        tax_rate = tax_rates.get(request_no, 8.0)  # 기본 세율 8%

        # 계산된 금액들
        calculated_tax_excluded = detail_amount
        calculated_tax_amount = int(detail_amount * (tax_rate / 100))  # 소수점 날림
        calculated_tax_included = calculated_tax_excluded + calculated_tax_amount

        # Cover의 금액들
        cover_tax_excluded = cover_data.get("税抜金額", 0)
        cover_tax_amount = cover_data.get("消費税金額", 0)
        cover_tax_included = cover_data.get("税込金額", 0)

        # 각 항목별 비교
        comparisons = [
            ("税抜金額", calculated_tax_excluded, cover_tax_excluded),
            ("消費税金額", calculated_tax_amount, cover_tax_amount),
            ("税込金額", calculated_tax_included, cover_tax_included)
        ]

        for item_type, calc_value, cover_value in comparisons:
            diff = calc_value - cover_value
            match = abs(diff) < 1  # 1원 이하 차이는 일치로 간주

            comparison_data.append({
                "請求No": request_no,
                "区分": item_type,
                "計算金額": f"{calc_value:,}",
                "実際金額": f"{cover_value:,}",
                "差額": f"{diff:,}",
                "状態": "✅ 一致" if match else "❌ 不一致"
            })
    
    return pd.DataFrame(comparison_data) if comparison_data else pd.DataFrame()


def validate_form_type02(
    detail_pages: List[Dict],
    summary_pages: List[Dict],
    cover_pages: List[Dict]
):
    """
    조건청구서② 검증 함수 - 請求No별 집계
    
    Args:
        detail_pages: detail 페이지 JSON 딕셔너리 리스트
        summary_pages: summary 페이지 JSON 딕셔너리 리스트 (사용 안 함)
        cover_pages: cover 페이지 JSON 딕셔너리 리스트
    """
    with st.expander("💰 請求No別集計比較 (cover比較)", expanded=False):
        if detail_pages and cover_pages:
            # 請求No별 집계 및 세율 추출
            detail_totals = extract_detail_by_request_no(detail_pages)
            cover_totals = extract_cover_by_request_no(cover_pages)
            tax_rates = extract_tax_rates_from_cover(cover_pages)

            if detail_totals or cover_totals:
                # 請求No별 비교 테이블
                comparison_df = create_request_no_comparison_dataframe(
                    detail_totals, cover_totals, tax_rates
                )
                
                if not comparison_df.empty:
                    st.dataframe(comparison_df, use_container_width=True, hide_index=True)
                    
                    # 총 금액 합산 및 비교
                    st.divider()
                    st.write("**総合計:**")

                    # Detail의 모든 관리번호 합산금액을 더함
                    total_detail_amount = sum(detail_totals.values())

                    # 각 관리번호별 세율 적용해서 세금 계산 및 합산
                    total_calculated_tax = 0
                    for request_no, amount in detail_totals.items():
                        tax_rate = tax_rates.get(request_no, 8.0)
                        tax_amount = int(amount * (tax_rate / 100))  # 소수점 날림
                        total_calculated_tax += tax_amount

                    total_calculated_tax_included = total_detail_amount + total_calculated_tax

                    # 合計 금액 추출 (cover의 마지막 행)
                    total_amount = 0
                    for page_data in cover_pages:
                        totals = page_data.get("totals", {})
                        detail_rows = totals.get("明細行", [])
                        for row in detail_rows:
                            if isinstance(row, dict) and row.get("件名") == "合計":
                                total_amount_str = row.get("税込金額")
                                if total_amount_str:
                                    total_amount = parse_amount(total_amount_str)
                                break

                    # 총계 금액만 단일 행으로 비교
                    diff = total_calculated_tax_included - total_amount
                    match = abs(diff) < 1

                    total_comparison_data = [{
                        "区分": "合計",
                        "計算金額": f"{total_calculated_tax_included:,}",
                        "実際金額": f"{total_amount:,}",
                        "差額": f"{diff:,}",
                        "状態": "✅ 一致" if match else "❌ 不一致"
                    }]

                    total_comparison_df = pd.DataFrame(total_comparison_data)
                    st.dataframe(total_comparison_df, use_container_width=True, hide_index=True)
                else:
                    st.info("請求No別の比較データがありません。")
            else:
                st.info("請求No別のデータがありません。")
        else:
            if not detail_pages:
                st.info("ℹ️ detailページがないため検証できません。")
            if not cover_pages:
                st.warning("⚠️ coverページが見つかりません。")

