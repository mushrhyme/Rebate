"""
검토 탭
"""

import streamlit as st
import shutil
import os
from pathlib import Path
import json
from typing import Tuple
import fitz  # PyMuPDF
from PIL import Image
import io

from modules.utils.session_manager import SessionManager
from modules.ui.review_components import (
    load_page_data,
    render_navigation,
    render_page_image,
    render_editable_table
)
from modules.utils.session_utils import ensure_session_state_defaults
from modules.utils.config import get_project_root
from modules.utils.pdf_utils import find_pdf_path


def request_training(pdf_name: str) -> Tuple[bool, str]:
    """
    학습 요청: PDF와 분석 결과를 img 폴더에 저장
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        
    Returns:
        (성공 여부, 메시지)
    """
    try:
        # 1. 프로젝트 루트와 img 폴더 경로 설정
        project_root = get_project_root()
        img_dir = project_root / "img"
        pdf_img_dir = img_dir / pdf_name
        pdf_img_dir.mkdir(parents=True, exist_ok=True)
        
        # 2. PDF 파일 경로 찾기 및 복사 (여러 위치에서 찾기)
        pdf_path = None
        dest_pdf_path = pdf_img_dir / f"{pdf_name}.pdf"
        
        # 2-1. 세션에 저장된 파일 바이너리에서 복원 (가장 확실한 방법)
        if pdf_name in st.session_state.get("uploaded_file_objects", {}):
            file_bytes = st.session_state.uploaded_file_objects[pdf_name]
            with open(dest_pdf_path, 'wb') as f:
                f.write(file_bytes)
            pdf_path = dest_pdf_path
        
        # 2-2. 세션 디렉토리에서 찾기
        if not pdf_path or not pdf_path.exists():
            session_pdf_path = find_pdf_path(pdf_name)
            if session_pdf_path and os.path.exists(session_pdf_path):
                pdf_path = Path(session_pdf_path)
        
        # 2-3. img 폴더의 하위 폴더에서 찾기
        if not pdf_path or not pdf_path.exists():
            img_pdf_path = pdf_img_dir / f"{pdf_name}.pdf"
            if img_pdf_path.exists():
                pdf_path = img_pdf_path
        
        # 2-4. img 폴더 루트에서 찾기
        if not pdf_path or not pdf_path.exists():
            img_root_pdf_path = img_dir / f"{pdf_name}.pdf"
            if img_root_pdf_path.exists():
                pdf_path = img_root_pdf_path
        
        if not pdf_path or not pdf_path.exists():
            return False, f"PDF 파일을 찾을 수 없습니다: {pdf_name}\n(세션 파일, 세션 디렉토리, img/{pdf_name}/, img/ 폴더에서 확인했습니다)"
        
        # PDF 파일 복사 (이미 img 폴더에 있으면 복사하지 않음)
        if pdf_path != dest_pdf_path:
            shutil.copy2(str(pdf_path), str(dest_pdf_path))
        
        # 3. DB에서 각 페이지의 분석 결과 가져오기
        from database.registry import get_db
        db_manager = get_db()
        pdf_filename = f"{pdf_name}.pdf"
        
        # 세션 ID 찾기
        with db_manager.get_connection() as conn:
            from psycopg2.extras import RealDictCursor
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT session_id FROM parsing_sessions 
                WHERE pdf_filename = %s AND is_latest = TRUE
                ORDER BY parsing_timestamp DESC
                LIMIT 1
            """, (pdf_filename,))
            result = cursor.fetchone()
            if not result:
                return False, f"분석 결과를 찾을 수 없습니다: {pdf_name}"
            session_id = result['session_id']
        
        # 페이지별 데이터 조회 (page_number 포함)
        with db_manager.get_connection() as conn:
            from psycopg2.extras import RealDictCursor
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT 
                    pi.page_number,
                    MAX(i.page_index) as page_index,
                    COALESCE(MAX(i.page_role), 'detail') as page_role,
                    MAX(i.issuer) as issuer,
                    MAX(i.issue_date) as issue_date,
                    MAX(i.billing_period) as billing_period,
                    JSON_AGG(
                        JSON_BUILD_OBJECT(
                            'management_id', i.management_id,
                            'product_name', i.product_name,
                            'quantity', i.quantity,
                            'case_count', i.case_count,
                            'bara_count', i.bara_count,
                            'units_per_case', i.units_per_case,
                            'amount', i.amount,
                            'customer', i.customer
                        ) ORDER BY i.item_order
                    ) FILTER (WHERE i.management_id IS NOT NULL) AS items
                FROM page_images pi
                LEFT JOIN items i ON pi.session_id = i.session_id AND pi.page_number = i.page_number
                WHERE pi.session_id = %s
                GROUP BY pi.page_number
                ORDER BY pi.page_number
            """, (session_id,))
            
            page_results = []
            for row in cursor.fetchall():
                row_dict = dict(row)
                # JSON_AGG 결과를 파이썬 리스트로 변환
                items = row_dict.get('items', [])
                if items is None:
                    items = []
                elif isinstance(items, str):
                    items = json.loads(items)
                
                # 페이지 레벨 customer 추출
                page_customer = None
                if items and len(items) > 0:
                    page_customer = items[0].get('customer')
                
                # 페이지별 JSON 구조 생성
                page_json = {
                    'page_number': row_dict.get('page_number'),
                    'page_role': row_dict.get('page_role', 'detail'),
                    'issuer': row_dict.get('issuer'),
                    'issue_date': row_dict.get('issue_date'),
                    'billing_period': row_dict.get('billing_period'),
                    'customer': page_customer,
                    'items': items
                }
                page_results.append(page_json)
        
        if not page_results:
            return False, f"분석 결과를 찾을 수 없습니다: {pdf_name}"
        
        # 4. PDF를 이미지로 변환하여 Page{page_num}.png 형식으로 저장
        try:
            doc = fitz.open(str(dest_pdf_path))
            total_pages = doc.page_count
            
            for page_idx in range(total_pages):
                page = doc.load_page(page_idx)
                pix = page.get_pixmap(dpi=300)
                img_bytes = pix.tobytes("png")
                image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                page_num = page_idx + 1
                
                image_path = pdf_img_dir / f"Page{page_num}.png"
                image.save(image_path, "PNG", dpi=(300, 300), optimize=True)
            
            doc.close()
        except Exception as e:
            return False, f"PDF 이미지 변환 실패: {str(e)}"
        
        # 5. 각 페이지의 결과를 Page{page_num}_answer.json 형식으로 저장
        # 필요한 필드만 추출 (page_role과 items만)
        saved_count = 0
        for page_result in page_results:
            page_num = page_result.get('page_number')
            if not page_num:
                continue
            
            # answer.json 파일 경로
            answer_json_path = pdf_img_dir / f"Page{page_num}_answer.json"
            
            # 필요한 필드만 추출 (page_role과 items만)
            answer_data = {
                'page_role': page_result.get('page_role', 'detail'),
                'items': page_result.get('items', [])
            }
            
            # 페이지 데이터를 JSON으로 저장
            with open(answer_json_path, 'w', encoding='utf-8') as f:
                json.dump(answer_data, f, ensure_ascii=False, indent=2)
            
            saved_count += 1
        
        return True, f"✅ 학습 요청 완료! {saved_count}개 페이지 저장됨 (PDF, 이미지, JSON 모두 저장됨)"
        
    except Exception as e:
        return False, f"❌ 오류 발생: {str(e)}"


def render_review_tab():
    """검토 탭 - 단순화된 클린 버전"""
    ensure_session_state_defaults()
    uploaded_pdfs = [info["name"] for info in st.session_state.uploaded_files_info]
    if not uploaded_pdfs:
        st.warning("アップロードされたPDFファイルがありません。", icon="⚠️")
        return
    if "selected_pdf" not in st.session_state:
        st.session_state.selected_pdf = uploaded_pdfs[0]
    if "selected_page" not in st.session_state:
        st.session_state.selected_page = 1
    
    # PDF 선택과 학습 요청 버튼을 같은 행에 배치
    col1, col2 = st.columns([3, 1])
    with col1:
        selected_pdf = st.selectbox(
            "PDFファイルを選択",
            uploaded_pdfs,
            index=uploaded_pdfs.index(st.session_state.selected_pdf)
            if st.session_state.selected_pdf in uploaded_pdfs else 0,
            key="pdf_selector"
        )
    with col2:
        if st.button("📚 学習リクエスト", type="primary", use_container_width=True):
            with st.spinner("学習データを保存中..."):
                success, message = request_training(selected_pdf)
                if success:
                    st.success(message)
                else:
                    st.error(message)
    if selected_pdf != st.session_state.selected_pdf:
        st.session_state.selected_pdf = selected_pdf
        st.session_state.selected_page = 1
        # 탭 상태 유지
        if "active_tab" not in st.session_state:
            st.session_state.active_tab = "📝 レビュー"
        st.rerun()
    page_count = SessionManager.get_pdf_page_count(selected_pdf)
    if page_count == 0:
        st.error("このファイルの解析結果がありません。", icon="⚠️")
        return
    current_page = st.session_state.selected_page
    current_page = max(1, min(current_page, page_count))
    st.session_state.selected_page = current_page
    render_navigation(selected_pdf, current_page, page_count)
    page_data = load_page_data(selected_pdf, current_page)
    col1, col2 = st.columns(2)
    with col1:
        render_page_image(selected_pdf, current_page)
    with col2:
        if page_data is None:
            st.error("このページの解析結果が見つかりません。", icon="⚠️")
        else:
            render_editable_table(selected_pdf, current_page)

