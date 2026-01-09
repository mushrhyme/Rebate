"""
조건청구서③ 검증 함수
"""

from typing import List, Dict, Tuple
import streamlit as st
import pandas as pd
import re

# answer_editor_tab.py에서 필요한 함수들 import
from modules.ui.answer_editor_tab import parse_amount


def extract_cover_totals_type03(cover_pages: List[Dict]) -> Dict[str, Dict]:
    """
    3번 양식지 cover 페이지에서 총액 정보 추출
    
    Args:
        cover_pages: cover 페이지 JSON 딕셔너리 리스트
        
    Returns:
        딕셔너리: {
            "販促_通常": {
                "8%": {"税抜": 금액, "消費税": 금액, "税込": 금액},
                "10%": {"税抜": 금액, "消費税": 금액, "税込": 금액}
            },
            "その他": {
                "10%": {"税抜": 금액, "消費税": 금액, "税込": 금액}
            },
            "合計": 금액
        }
    """
    totals = {
        "販促_通常": {
            "8%": {"税抜": 0, "消費税": 0, "税込": 0},
            "10%": {"税抜": 0, "消費税": 0, "税込": 0}
        },
        "その他": {
            "10%": {"税抜": 0, "消費税": 0, "税込": 0}
        },
        "合計": 0
    }
    
    for page_data in cover_pages:
        totals_section = page_data.get("totals", {})
        
        # 販促_通常 정보 추출
        if "販促_通常" in totals_section:
            promo_section = totals_section["販促_通常"]
            rate_breakdown = promo_section.get("税率別内訳", {})
            
            # 軽減税率8％ 정보
            if "軽減税率8％" in rate_breakdown:
                rate8 = rate_breakdown["軽減税率8％"]
                totals["販促_通常"]["8%"]["税抜"] += parse_amount(rate8.get("今回請求額税抜", "0"))
                totals["販促_通常"]["8%"]["消費税"] += parse_amount(rate8.get("今回請求消費税等", "0"))
                totals["販促_通常"]["8%"]["税込"] += parse_amount(rate8.get("今回ご請求額（税込）", "0"))
            
            # 税率10％ 정보
            if "税率10％" in rate_breakdown:
                rate10 = rate_breakdown["税率10％"]
                totals["販促_通常"]["10%"]["税抜"] += parse_amount(rate10.get("今回請求額税抜", "0"))
                totals["販促_通常"]["10%"]["消費税"] += parse_amount(rate10.get("今回請求消費税等", "0"))
                totals["販促_通常"]["10%"]["税込"] += parse_amount(rate10.get("今回ご請求額（税込）", "0"))
        
        # その他請求 정보 추출
        if "その他請求" in totals_section:
            service_section = totals_section["その他請求"]
            rate_breakdown = service_section.get("税率別内訳", {})
            
            # 税率10％ 정보 (その他는 일반적으로 10%)
            if "税率10％" in rate_breakdown:
                rate10 = rate_breakdown["税率10％"]
                totals["その他"]["10%"]["税抜"] += parse_amount(rate10.get("今回請求額税抜", "0"))
                totals["その他"]["10%"]["消費税"] += parse_amount(rate10.get("今回請求消費税等", "0"))
                totals["その他"]["10%"]["税込"] += parse_amount(rate10.get("今回ご請求額（税込）", "0"))
        
        # ご請求額合計 추출
        if "ご請求額合計" in totals_section:
            totals["合計"] = parse_amount(totals_section["ご請求額合計"])
    
    return totals


def aggregate_detail_totals_type03(detail_pages: List[Dict]) -> Dict[str, Dict]:
    """
    detail 페이지에서 세율별, 타입별 총액 집계
    관리번호별로 그룹화하여 세전 금액을 합산한 후 세율을 곱해서 세액 계산
    
    Args:
        detail_pages: detail 페이지 JSON 딕셔너리 리스트
        
    Returns:
        딕셔너리: {
            "販促_通常": {
                "8%": {"税抜": 금액, "消費税": 금액, "税込": 금액},
                "10%": {"税抜": 금액, "消費税": 금액, "税込": 금액}
            },
            "その他": {
                "10%": {"税抜": 금액, "消費税": 금액, "税込": 금액}
            },
            "合計": 금액
        }
    """
    # 관리번호별, 타입별, 세율별로 그룹화하여 세전 금액 수집
    # 구조: {타입: {세율: {관리번호: 세전금액}}}
    grouped_data = {
        "販促_通常": {
            "8%": {},  # {관리번호: 세전금액}
            "10%": {}  # {관리번호: 세전금액}
        },
        "その他": {
            "10%": {}  # {관리번호: 세전금액}
        }
    }
    
    for page_data in detail_pages:
        items = page_data.get("items", [])
        
        for item in items:
            if not isinstance(item, dict):
                continue
            
            # 금액 추출
            amount_str = item.get("請求金額") or item.get("金額")
            if not amount_str:
                continue
            
            amount = parse_amount(amount_str)
            
            # 세율 추출
            tax_rate_str = item.get("税額") or item.get("税率") or item.get("消費税率")
            tax_rate = None
            if tax_rate_str:
                tax_rate_match = re.search(r'(\d+)', str(tax_rate_str))
                if tax_rate_match:
                    tax_rate = int(tax_rate_match.group(1))
            
            # 타입 추출
            item_type = item.get("タイプ") or item.get("type")
            
            # 관리번호 추출
            request_no = item.get("請求No") or item.get("請求番号") or item.get("management_id") or item.get("管理番号")
            
            # 타입별, 세율별로 관리번호별 그룹화
            if item_type == "販促_通常":
                if tax_rate == 8:
                    if request_no not in grouped_data["販促_通常"]["8%"]:
                        grouped_data["販促_通常"]["8%"][request_no] = 0
                    grouped_data["販促_通常"]["8%"][request_no] += amount
                elif tax_rate == 10:
                    if request_no not in grouped_data["販促_通常"]["10%"]:
                        grouped_data["販促_通常"]["10%"][request_no] = 0
                    grouped_data["販促_通常"]["10%"][request_no] += amount
            elif item_type == "その他":
                # その他는 일반적으로 10% 세율
                if tax_rate is None or tax_rate == 10:
                    if request_no not in grouped_data["その他"]["10%"]:
                        grouped_data["その他"]["10%"][request_no] = 0
                    grouped_data["その他"]["10%"][request_no] += amount
    
    # 관리번호별로 합산한 세전 금액에 세율을 곱해서 최종 집계
    totals = {
        "販促_通常": {
            "8%": {"税抜": 0, "消費税": 0, "税込": 0},
            "10%": {"税抜": 0, "消費税": 0, "税込": 0}
        },
        "その他": {
            "10%": {"税抜": 0, "消費税": 0, "税込": 0}
        },
        "合計": 0
    }
    
    # 販促_通常 - 8% 집계
    for request_no, tax_excluded in grouped_data["販促_通常"]["8%"].items():
        totals["販促_通常"]["8%"]["税抜"] += tax_excluded
        tax_amount = round(tax_excluded * 0.08)  # 관리번호별 합계에 세율 곱하기
        totals["販促_通常"]["8%"]["消費税"] += tax_amount
        totals["販促_通常"]["8%"]["税込"] += (tax_excluded + tax_amount)
    
    # 販促_通常 - 10% 집계
    for request_no, tax_excluded in grouped_data["販促_通常"]["10%"].items():
        totals["販促_通常"]["10%"]["税抜"] += tax_excluded
        tax_amount = round(tax_excluded * 0.10)  # 관리번호별 합계에 세율 곱하기
        totals["販促_通常"]["10%"]["消費税"] += tax_amount
        totals["販促_通常"]["10%"]["税込"] += (tax_excluded + tax_amount)
    
    # その他 - 10% 집계
    for request_no, tax_excluded in grouped_data["その他"]["10%"].items():
        totals["その他"]["10%"]["税抜"] += tax_excluded
        tax_amount = round(tax_excluded * 0.10)  # 관리번호별 합계에 세율 곱하기
        totals["その他"]["10%"]["消費税"] += tax_amount
        totals["その他"]["10%"]["税込"] += (tax_excluded + tax_amount)
    
    # 전체 합계 계산
    total_promo_8 = totals["販促_通常"]["8%"]["税込"]
    total_promo_10 = totals["販促_通常"]["10%"]["税込"]
    total_service_10 = totals["その他"]["10%"]["税込"]
    totals["合計"] = total_promo_8 + total_promo_10 + total_service_10
    
    return totals


def extract_summary_by_request_no(summary_pages: List[Dict]) -> Dict[str, int]:
    """
    summary 페이지에서 請求No별 집계
    
    Args:
        summary_pages: summary 페이지 JSON 딕셔너리 리스트
        
    Returns:
        딕셔너리: {請求No: 請求金額, ...}
    """
    request_no_totals = {}
    
    for page_data in summary_pages:
        items = page_data.get("items", [])
        
        for item in items:
            if not isinstance(item, dict):
                continue
            
            request_no = item.get("請求No")
            amount_str = item.get("請求金額") or item.get("金額")
            
            if request_no and amount_str:
                amount = parse_amount(amount_str)
                if request_no not in request_no_totals:
                    request_no_totals[request_no] = 0
                request_no_totals[request_no] += amount
    
    return request_no_totals


def aggregate_detail_by_request_no(detail_pages: List[Dict]) -> Dict[str, Dict[str, int]]:
    """
    detail 페이지에서 請求No별 집계 (세전/세액/세액포함)
    세액은 전체 합계에 세율을 곱한 후 반올림 (각 항목별 계산 후 합산이 아님)
    
    Args:
        detail_pages: detail 페이지 JSON 딕셔너리 리스트
        
    Returns:
        딕셔너리: {
            請求No: {
                "税抜": 세전 금액,
                "消費税": 세액,
                "税込": 세액 포함 금액
            }, ...
        }
    """
    request_no_totals = {}  # {請求No: {"税抜": 금액, "税率": 세율}}
    
    # 1단계: 請求No별로 세전 금액과 세율 수집
    for page_data in detail_pages:
        items = page_data.get("items", [])
        
        for item in items:
            if not isinstance(item, dict):
                continue
            
            request_no = item.get("請求No")
            amount_str = item.get("請求金額") or item.get("金額")
            
            if request_no and amount_str:
                # 세전 금액 추출
                amount = parse_amount(amount_str)
                
                # 세율 추출
                tax_rate_str = item.get("税額") or item.get("税率") or item.get("消費税率")
                tax_rate = None
                if tax_rate_str:
                    tax_rate_match = re.search(r'(\d+)', str(tax_rate_str))
                    if tax_rate_match:
                        tax_rate = int(tax_rate_match.group(1))
                
                if request_no not in request_no_totals:
                    request_no_totals[request_no] = {
                        "税抜": 0,
                        "税率": tax_rate  # 첫 번째 항목의 세율 사용 (같은 請求No는 같은 세율 가정)
                    }
                
                request_no_totals[request_no]["税抜"] += amount
    
    # 2단계: 각 請求No별로 전체 합계에 세율을 곱해서 세액 계산
    result = {}
    for request_no, data in request_no_totals.items():
        tax_excluded = data["税抜"]
        tax_rate = data.get("税率")
        
        if tax_rate:
            # 전체 합계에 세율을 곱한 후 반올림
            tax_amount = round(tax_excluded * (tax_rate / 100))
            tax_included = tax_excluded + tax_amount
        else:
            # 세율이 없으면 세전 금액만 사용
            tax_amount = 0
            tax_included = tax_excluded
        
        result[request_no] = {
            "税抜": tax_excluded,
            "消費税": tax_amount,
            "税込": tax_included
        }
    
    return result


def create_cover_comparison_dataframe(
    detail_totals: Dict[str, Dict],
    cover_totals: Dict[str, Dict]
) -> pd.DataFrame:
    """
    cover 페이지와 detail 합산 금액 비교 데이터프레임 생성
    
    Args:
        detail_totals: detail 페이지의 집계 결과
        cover_totals: cover 페이지의 집계 결과
        
    Returns:
        비교 데이터프레임
    """
    comparison_data = []
    
    # 販促_通常 - 8% 비교
    detail_promo_8 = detail_totals["販促_通常"]["8%"]
    cover_promo_8 = cover_totals["販促_通常"]["8%"]
    
    comparisons_8 = [
        ("税抜", detail_promo_8["税抜"], cover_promo_8["税抜"]),
        ("消費税", detail_promo_8["消費税"], cover_promo_8["消費税"]),
        ("税込", detail_promo_8["税込"], cover_promo_8["税込"])
    ]
    
    for label, detail_val, cover_val in comparisons_8:
        diff = detail_val - cover_val
        match = abs(diff) < 1  # 1원 이하 차이는 일치로 간주
        comparison_data.append({
            "区分": f"販促_通常 - 8% - {label}",
            "計算金額": f"{detail_val:,}",
            "実際金額": f"{cover_val:,}",
            "差額": f"{diff:,}",
            "状態": "✅ 一致" if match else "❌ 不一致"
        })
    
    # 販促_通常 - 10% 비교
    detail_promo_10 = detail_totals["販促_通常"]["10%"]
    cover_promo_10 = cover_totals["販促_通常"]["10%"]
    
    if detail_promo_10["税抜"] > 0 or cover_promo_10["税抜"] > 0:
        comparisons_10 = [
            ("税抜", detail_promo_10["税抜"], cover_promo_10["税抜"]),
            ("消費税", detail_promo_10["消費税"], cover_promo_10["消費税"]),
            ("税込", detail_promo_10["税込"], cover_promo_10["税込"])
        ]
        
        for label, detail_val, cover_val in comparisons_10:
            diff = detail_val - cover_val
            match = abs(diff) < 1
            comparison_data.append({
                "区分": f"販促_通常 - 10% - {label}",
                "計算金額": f"{detail_val:,}",
                "実際金額": f"{cover_val:,}",
                "差額": f"{diff:,}",
                "状態": "✅ 一致" if match else "❌ 不一致"
            })
    
    # その他 - 10% 비교
    detail_service_10 = detail_totals["その他"]["10%"]
    cover_service_10 = cover_totals["その他"]["10%"]
    
    if detail_service_10["税抜"] > 0 or cover_service_10["税抜"] > 0:
        comparisons_service = [
            ("税抜", detail_service_10["税抜"], cover_service_10["税抜"]),
            ("消費税", detail_service_10["消費税"], cover_service_10["消費税"]),
            ("税込", detail_service_10["税込"], cover_service_10["税込"])
        ]
        
        for label, detail_val, cover_val in comparisons_service:
            diff = detail_val - cover_val
            match = abs(diff) < 1
            comparison_data.append({
                "区分": f"その他 - 10% - {label}",
                "計算金額": f"{detail_val:,}",
                "実際金額": f"{cover_val:,}",
                "差額": f"{diff:,}",
                "状態": "✅ 一致" if match else "❌ 不一致"
            })
    
    # 전체 합계 비교
    detail_total = detail_totals["合計"]
    cover_total = cover_totals["合計"]
    diff_total = detail_total - cover_total
    match_total = abs(diff_total) < 1
    
    comparison_data.append({
        "区分": "合計",
        "計算金額": f"{detail_total:,}",
        "実際金額": f"{cover_total:,}",
        "差額": f"{diff_total:,}",
        "状態": "✅ 一致" if match_total else "❌ 不一致"
    })
    
    return pd.DataFrame(comparison_data) if comparison_data else pd.DataFrame()


def create_summary_comparison_dataframe(
    detail_by_request_no: Dict[str, Dict[str, int]],
    summary_by_request_no: Dict[str, int]
) -> pd.DataFrame:
    """
    summary 페이지와 detail의 請求No별 집계 비교 데이터프레임 생성 (디버깅용: 세전/세액/세액포함 표시)
    
    Args:
        detail_by_request_no: detail 페이지의 請求No별 집계 (세전/세액/세액포함 포함)
        summary_by_request_no: summary 페이지의 請求No별 집계
        
    Returns:
        비교 데이터프레임
    """
    comparison_data = []
    all_request_nos = set(list(detail_by_request_no.keys()) + list(summary_by_request_no.keys()))
    
    for request_no in sorted(all_request_nos):
        detail_data = detail_by_request_no.get(request_no, {"税抜": 0, "消費税": 0, "税込": 0})
        detail_tax_excluded = detail_data.get("税抜", 0)
        detail_tax = detail_data.get("消費税", 0)
        detail_tax_included = detail_data.get("税込", 0)
        
        summary_amount = summary_by_request_no.get(request_no, 0)
        diff = detail_tax_included - summary_amount
        match = abs(diff) < 1  # 1원 이하 차이는 일치로 간주
        
        comparison_data.append({
            "請求No": request_no or "",
            "計算金額(税抜)": f"{detail_tax_excluded:,}",
            "計算金額(消費税)": f"{detail_tax:,}",
            "計算金額(税込)": f"{detail_tax_included:,}",
            "実際金額": f"{summary_amount:,}",
            "差額": f"{diff:,}",
            "状態": "✅ 一致" if match else "❌ 不一致"
        })
    
    return pd.DataFrame(comparison_data) if comparison_data else pd.DataFrame()


def validate_form_type03(
    detail_pages: List[Dict],
    summary_pages: List[Dict],
    cover_pages: List[Dict]
):
    """
    조건청구서③ 검증 함수
    
    Args:
        detail_pages: detail 페이지 JSON 딕셔너리 리스트
        summary_pages: summary 페이지 JSON 딕셔너리 리스트
        cover_pages: cover 페이지 JSON 딕셔너리 리스트
    """
    # 1. cover 페이지의 정보와 detail의 합산 금액 비교
    with st.expander("💰 coverページとdetail合計金額比較", expanded=False):
        if detail_pages and cover_pages:
            detail_totals = aggregate_detail_totals_type03(detail_pages)
            cover_totals = extract_cover_totals_type03(cover_pages)
            
            comparison_df = create_cover_comparison_dataframe(detail_totals, cover_totals)
            
            if not comparison_df.empty:
                st.dataframe(comparison_df, width='stretch', hide_index=True)
            else:
                st.info("比較データがありません。")
        else:
            if not detail_pages:
                st.info("ℹ️ detailページがないため検証できません。")
            if not cover_pages:
                st.warning("⚠️ coverページが見つかりません。")
    
    # 2. summary 페이지의 정보와 관리번호별 detail의 합산 금액 비교
    with st.expander("📊 請求No別集計比較 (summary比較)", expanded=False):
        if detail_pages and summary_pages:
            detail_by_request_no = aggregate_detail_by_request_no(detail_pages)
            summary_by_request_no = extract_summary_by_request_no(summary_pages)
            
            comparison_df = create_summary_comparison_dataframe(
                detail_by_request_no, summary_by_request_no
            )
            
            if not comparison_df.empty:
                st.dataframe(comparison_df, width='stretch', hide_index=True)
            else:
                st.info("請求No別の比較データがありません。")
        else:
            if not detail_pages:
                st.info("ℹ️ detailページがないため検証できません。")
            if not summary_pages:
                st.warning("⚠️ summaryページが見つかりません。")

