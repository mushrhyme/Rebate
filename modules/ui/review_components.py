"""
검토 탭 UI 컴포넌트 모듈
"""

import os
from typing import Dict, Any, Optional
from PIL import Image
from utils.session_manager import SessionManager
import pandas as pd

def load_page_data(pdf_name: str, page_num: int) -> Optional[Dict[str, Any]]:
    """
    페이지 데이터 로드 (세션 디렉토리에서만)
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        page_num: 페이지 번호 (1부터 시작)
        
    Returns:
        페이지 데이터 딕셔너리 또는 None
    """
    # 세션 디렉토리에서만 로드
    page_data = SessionManager.load_ocr_result(pdf_name, page_num)
    return page_data


def load_page_image(pdf_name: str, page_num: int) -> Optional[Image.Image]:
    """
    페이지 이미지 로드 (DB 우선, 파일 시스템은 폴백)
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        page_num: 페이지 번호 (1부터 시작)
        
    Returns:
        PIL Image 객체 또는 None
    """
    # 1. DB에서 로드 시도
    try:
        from database.db_manager import DatabaseManager
        import os
        from io import BytesIO
        
        # DB 연결 정보 (환경 변수에서 가져오거나 기본값 사용)
        db_manager = DatabaseManager(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', '5432')),
            database=os.getenv('DB_NAME', 'rebate_db'),
            user=os.getenv('DB_USER', 'postgres'),
            password=os.getenv('DB_PASSWORD', '')
        )
        
        # PDF 파일명 (확장자 포함)
        pdf_filename = f"{pdf_name}.pdf"
        
        # DB에서 이미지 로드
        image_data = db_manager.get_page_image(
            pdf_filename=pdf_filename,
            page_number=page_num,
            session_id=None,
            is_latest=True
        )
        
        # DB 연결 종료
        db_manager.close()
        
        if image_data:
            # bytes를 PIL Image로 변환
            img = Image.open(BytesIO(image_data))
            img.load()
            return img
    except Exception as db_error:
        # DB 로드 실패 시 파일 시스템으로 폴백
        print(f"DB 이미지 로드 실패 (파일 시스템으로 폴백): {db_error}")
    
    # 2. 파일 시스템에서 로드 (하위 호환성)
    images_dir = SessionManager.get_images_dir()
    image_path = os.path.join(images_dir, pdf_name, f"page_{page_num}.png")
    
    if os.path.exists(image_path):
        try:
            img = Image.open(image_path)
            img.load()
            return img
        except Exception:
            pass
    
    return None


def render_navigation(pdf_name: str, current_page: int, total_pages: int):
    """
    페이지 네비게이션 렌더링
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        current_page: 현재 페이지 번호
        total_pages: 전체 페이지 수
    """
    import streamlit as st
    
    # 페이지 데이터 로드하여 page_role 정보 가져오기
    page_data = load_page_data(pdf_name, current_page)
    page_role = page_data.get('page_role', 'main') if page_data else 'main'
    
    # page_role 한글/일본어 매핑
    role_labels = {
        'cover': '表紙',
        'main': 'メイン',
        'detail': '詳細',
        'reply': '返信'
    }
    role_label = role_labels.get(page_role, page_role)
    
    
    col1, col2, col3, col4, col5 = st.columns(5)
    
    with col1:
        if st.button("◀", disabled=current_page <= 1, use_container_width=True, key="nav_prev", type="primary"):
            st.session_state.selected_page = current_page - 1
            st.rerun()
    
    with col2:
        if st.button("▶", disabled=current_page >= total_pages, use_container_width=True, key="nav_next", type="primary"):
            st.session_state.selected_page = current_page + 1
            st.rerun()
    
    with col3:
        st.button(f"ページ: {current_page} / {total_pages}", use_container_width=True, help=f"PDF: {pdf_name}", key="nav_page", type="secondary")
    
    with col4:
        st.button(f"ページ役割: {role_label}", use_container_width=True, key="nav_role", type="secondary")
    
    with col5:
        if 'review_data' not in st.session_state:
            st.session_state.review_data = {}
        if pdf_name not in st.session_state.review_data:
            st.session_state.review_data[pdf_name] = {}
        
        checked = st.session_state.review_data[pdf_name].get(current_page, {}).get("checked", False)
        review_checked = st.checkbox("✅ レビュー完了", value=checked, key=f"review_{pdf_name}_{current_page}")
        
        # 체크 상태 저장
        if review_checked != checked:
            if current_page not in st.session_state.review_data[pdf_name]:
                st.session_state.review_data[pdf_name][current_page] = {}
            st.session_state.review_data[pdf_name][current_page]["checked"] = review_checked
        


def render_page_image(pdf_name: str, page_num: int):
    """
    페이지 이미지 렌더링
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        page_num: 페이지 번호 (1부터 시작)
    """
    import streamlit as st
    
    page_image = load_page_image(pdf_name, page_num)
    
    if page_image:
        st.image(page_image, width='stretch')
    else:
        st.warning("画像が見つかりません。")


def render_editable_table(pdf_name: str, page_num: int):
    """
    편집 가능한 테이블 렌더링
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        page_num: 페이지 번호 (1부터 시작)
    """
    import streamlit as st
    from modules.ui.aggrid_utils import AgGridUtils
    
    page_data = load_page_data(pdf_name, page_num)
    
    if not page_data:
        st.warning("ページデータが見つかりません。")
        return
    
    # items 추출
    items = page_data.get("items", [])
    
    if not items:
        st.info("このページには項目がありません。")
        return
    
    # AgGrid로 표시
    if AgGridUtils.is_available():
        AgGridUtils.render_items(items, pdf_name, page_num)
    else:
        df = pd.DataFrame(items)
        edited_df = st.data_editor(df, width='stretch')
        
        if st.button("保存"):
            # 수정된 데이터 저장
            updated_items = edited_df.to_dict('records')
            page_data["items"] = updated_items
            
            # DB에 저장 (JSON 파일 저장은 제거)
            try:
                from database.db_manager import DatabaseManager
                import os
                
                db_manager = DatabaseManager(
                    host=os.getenv('DB_HOST', 'localhost'),
                    port=int(os.getenv('DB_PORT', '5432')),
                    database=os.getenv('DB_NAME', 'rebate_db'),
                    user=os.getenv('DB_USER', 'postgres'),
                    password=os.getenv('DB_PASSWORD', '')
                )
                
                pdf_filename = f"{pdf_name}.pdf"
                success = db_manager.update_page_items(
                    pdf_filename=pdf_filename,
                    page_num=page_num,
                    items=updated_items,
                    session_id=None,
                    is_latest=True
                )
                
                db_manager.close()
                
                if success:
                    st.success("保存完了！")
                else:
                    st.error("DB保存に失敗しました。")
            except Exception as db_error:
                st.error(f"DB保存失敗: {db_error}", icon="❌")

