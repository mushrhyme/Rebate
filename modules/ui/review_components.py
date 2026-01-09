"""
검토 탭 UI 컴포넌트 모듈
"""

import os
from typing import Dict, Any, Optional, List
from PIL import Image
from modules.utils.session_manager import SessionManager
from modules.utils.pdf_utils import extract_text_from_pdf_page, find_pdf_path
from pathlib import Path
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
        from database.registry import get_db
        import os
        from io import BytesIO

        # 전역 DB 인스턴스 사용
        db_manager = get_db()

        # PDF 파일명 (확장자 포함)
        pdf_filename = f"{pdf_name}.pdf"

        # DB에서 이미지 로드
        image_data = db_manager.get_page_image(
            pdf_filename=pdf_filename,
            page_number=page_num,
            session_id=None,
            is_latest=True
        )

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
    image_path = os.path.join(images_dir, pdf_name, f"page_{page_num}.jpg")  # JPEG 형식
    
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
        if st.button("◀", disabled=current_page <= 1, width='stretch', key="nav_prev", type="primary"):
            st.session_state.selected_page = current_page - 1
            # 탭 상태 유지
            if "active_tab" not in st.session_state:
                st.session_state.active_tab = "📝 レビュー"
            st.rerun()
    
    with col2:
        if st.button("▶", disabled=current_page >= total_pages, width='stretch', key="nav_next", type="primary"):
            st.session_state.selected_page = current_page + 1
            # 탭 상태 유지
            if "active_tab" not in st.session_state:
                st.session_state.active_tab = "📝 レビュー"
            st.rerun()
    
    with col3:
        st.button(f"ページ: {current_page} / {total_pages}", width='stretch', help=f"PDF: {pdf_name}", key="nav_page", type="secondary")
    
    with col4:
        st.button(f"ページ役割: {role_label}", width='stretch', key="nav_role", type="secondary")
    
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
    페이지 이미지 렌더링 (스크롤 가능)
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        page_num: 페이지 번호 (1부터 시작)
    """
    import streamlit as st
    from io import BytesIO
    import base64
    
    page_image = load_page_image(pdf_name, page_num)
    
    if page_image:
        try:
            # PIL Image를 BytesIO로 변환하여 안전하게 전달
            img_buffer = BytesIO()
            # RGB 모드로 변환 (JPEG는 RGB만 지원)
            if page_image.mode != 'RGB':
                page_image = page_image.convert('RGB')
            page_image.save(img_buffer, format='JPEG', quality=95)
            img_buffer.seek(0)
            
            # 이미지를 base64로 인코딩
            img_base64 = base64.b64encode(img_buffer.getvalue()).decode()
            
            # 스크롤 가능한 컨테이너로 이미지 표시
            st.markdown(
                f"""
                <div style="max-height: 600px; overflow-y: auto; overflow-x: auto; border: 1px solid #ddd; border-radius: 4px; padding: 10px;">
                    <img src="data:image/jpeg;base64,{img_base64}" style="width: 100%; height: auto; display: block;" />
                </div>
                """,
                unsafe_allow_html=True
            )
        except Exception as e:
            # Streamlit 메모리 스토리지 에러 등 예외 발생 시 재시도
            try:
                # 이미지를 다시 로드하여 시도
                page_image = load_page_image(pdf_name, page_num)
                if page_image:
                    img_buffer = BytesIO()
                    # RGB 모드로 변환 (JPEG는 RGB만 지원)
                    if page_image.mode != 'RGB':
                        page_image = page_image.convert('RGB')
                    page_image.save(img_buffer, format='JPEG', quality=95)
                    img_buffer.seek(0)
                    
                    # 이미지를 base64로 인코딩
                    img_base64 = base64.b64encode(img_buffer.getvalue()).decode()
                    
                    # 스크롤 가능한 컨테이너로 이미지 표시
                    st.markdown(
                        f"""
                        <div style="max-height: 600px; overflow-y: auto; overflow-x: auto; border: 1px solid #ddd; border-radius: 4px; padding: 10px;">
                            <img src="data:image/jpeg;base64,{img_base64}" style="width: 100%; height: auto; display: block;" />
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                else:
                    st.warning("画像の読み込みに失敗しました。")
            except Exception as ex:
                st.warning(f"画像の表示に失敗しました: {str(ex)[:50]}")
    else:
        st.warning("画像が見つかりません。")


def get_reference_document(pdf_name: str, page_num: int) -> Optional[Dict[str, Any]]:
    """
    현재 페이지의 참고 문서를 RAG 검색으로 가져오기
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        page_num: 페이지 번호 (1부터 시작)
        
    Returns:
        참고 문서 정보 (answer_json 포함) 또는 None
    """
    try:
        # 1. OCR 텍스트 가져오기
        page_data = load_page_data(pdf_name, page_num)
        ocr_text = None
        
        if page_data:
            # page_data에 ocr_text가 있는지 확인 (일부 구현에서는 저장되지 않을 수 있음)
            ocr_text = page_data.get("ocr_text", "")
        
        # OCR 텍스트가 없으면 PDF에서 직접 추출
        if not ocr_text:
            # PDF 경로 찾기
            pdf_path_str = find_pdf_path(pdf_name)
            if not pdf_path_str:
                # img 폴더에서도 찾기
                from modules.utils.config import get_project_root
                project_root = get_project_root()
                img_dir = project_root / "img"
                pdf_path = img_dir / pdf_name / f"{pdf_name}.pdf"
                if not pdf_path.exists():
                    pdf_path = img_dir / f"{pdf_name}.pdf"
            else:
                pdf_path = Path(pdf_path_str)
            
            if pdf_path.exists():
                ocr_text = extract_text_from_pdf_page(pdf_path, page_num)
        
        if not ocr_text or len(ocr_text.strip()) == 0:
            return None
        
        # 2. RAG 검색 수행
        from modules.core.rag_manager import get_rag_manager
        from modules.utils.config import get_rag_config
        
        rag_manager = get_rag_manager()
        config = get_rag_config()
        
        # 벡터 DB 상태 확인 (디버깅용)
        example_count = rag_manager.count_examples()
        if example_count == 0:
            print(f"⚠️ 벡터 DB에 예제가 없습니다. (총 {example_count}개)")
            return None
        
        # 유사한 예제 검색 (최상위 1개만)
        similar_examples = rag_manager.search_similar_advanced(
            query_text=ocr_text,
            top_k=1,
            similarity_threshold=0.0,  # threshold 무시하고 최상위 결과 사용
            search_method=getattr(config, 'search_method', 'hybrid'),
            hybrid_alpha=getattr(config, 'hybrid_alpha', 0.5)
        )
        
        if not similar_examples or len(similar_examples) == 0:
            print(f"⚠️ RAG 검색 결과 없음 (벡터 DB에 {example_count}개 예제 있음)")
            return None
        
        # 가장 유사한 예제 반환
        example = similar_examples[0]
        return {
            "answer_json": example.get("answer_json", {}),
            "metadata": example.get("metadata", {}),
            "similarity": example.get("similarity", 0),
            "hybrid_score": example.get("hybrid_score", example.get("final_score", 0))
        }
    except Exception as e:
        print(f"⚠️ 참고 문서 가져오기 실패: {e}")
        return None


def render_reference_document(pdf_name: str, page_num: int):
    """
    참고 문서를 AgGrid로 렌더링 (읽기 전용)
    
    Args:
        pdf_name: PDF 파일명 (확장자 제외)
        page_num: 페이지 번호 (1부터 시작)
    """
    import streamlit as st
    from modules.ui.aggrid_utils import AgGridUtils
    
    # 참고 문서 가져오기
    reference_doc = get_reference_document(pdf_name, page_num)
    
    if not reference_doc:
        st.info("📚 참고 문서가 없습니다. (RAG 검색 결과 없음)")
        return
    
    answer_json = reference_doc.get("answer_json", {})
    items = answer_json.get("items", [])
    metadata = reference_doc.get("metadata", {})
    similarity = reference_doc.get("similarity", 0)
    hybrid_score = reference_doc.get("hybrid_score", 0)
    
    # 메타데이터 정보 표시
    ref_pdf_name = metadata.get("pdf_name", "알 수 없음")
    ref_page_num = metadata.get("page_num", "알 수 없음")
    
    # 점수 표시
    score_text = ""
    if hybrid_score > 0:
        score_text = f"유사도: {hybrid_score:.4f}"
    elif similarity > 0:
        score_text = f"유사도: {similarity:.4f}"
    
    st.caption(f"📚 참고 문서: {ref_pdf_name} (페이지 {ref_page_num}) | {score_text}")
    
    if not items or len(items) == 0:
        st.info("참고 문서에 항목이 없습니다.")
        return
    
    # AgGrid로 표시 (읽기 전용)
    if AgGridUtils.is_available():
        # 읽기 전용으로 표시하기 위해 임시로 render_items를 사용하되 저장 버튼은 표시하지 않음
        # AgGridUtils.render_items는 편집 가능하므로, 별도로 읽기 전용 버전을 만들어야 함
        df = pd.DataFrame(items)
        
        # 인덱스 번호 컬럼 추가
        df.insert(0, 'No', range(1, len(df) + 1))
        
        # 컬럼 순서 정의
        desired_order = [
            'No',
            'management_id', '管理番号',
            'customer', '取引先',
            'product_name', '商品名',
            'units_per_case', 'ケース内入数',
            'case_count', 'ケース数',
            'bara_count', 'バラ数',
            'quantity', '数量',
            'amount', '金額'
        ]
        
        existing_cols = [col for col in desired_order if col in df.columns]
        remaining_cols = [col for col in df.columns if col not in existing_cols]
        final_column_order = existing_cols + remaining_cols
        df = df[final_column_order]
        df = df.dropna(axis=1, how='all')
        
        # 컬럼명 매핑
        column_name_mapping = {
            'No': 'No',
            'management_id': '管理番号',
            'customer': '取引先',
            'product_name': '商品名',
            'units_per_case': 'ケース内入数',
            'case_count': 'ケース数',
            'bara_count': 'バラ数',
            'quantity': '数量',
            'amount': '金額',
            '管理番号': '管理番号',
            '取引先': '取引先',
            '商品名': '商品名',
            'ケース内入数': 'ケース内入数',
            'ケース数': 'ケース数',
            'バラ数': 'バラ数',
            '数量': '数量',
            '金額': '金額'
        }
        
        from st_aggrid import AgGrid, GridOptionsBuilder
        
        gb = GridOptionsBuilder.from_dataframe(df)
        gb.configure_default_column(editable=False, resizable=True)  # 읽기 전용
        
        for col in df.columns:
            japanese_name = column_name_mapping.get(col, col)
            if col == 'No':
                gb.configure_column(col, header_name=japanese_name, editable=False, width=60, pinned='left')
            else:
                gb.configure_column(col, header_name=japanese_name, editable=False)
        
        gb.configure_pagination(enabled=False)
        
        grid_options = gb.build()
        grid_options['pagination'] = False
        
        # AgGrid 렌더링 (읽기 전용)
        AgGrid(
            df,
            gridOptions=grid_options,
            fit_columns_on_grid_load=True,
            height=400,
            theme='streamlit',
            allow_unsafe_jscode=False
        )
    else:
        # AgGrid가 없으면 일반 테이블로 표시
        df = pd.DataFrame(items)
        st.dataframe(df, width='stretch')


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
                from database.registry import get_db
                import os

                db_manager = get_db()

                pdf_filename = f"{pdf_name}.pdf"
                success = db_manager.update_page_items(
                    pdf_filename=pdf_filename,
                    page_num=page_num,
                    items=updated_items,
                    session_id=None,
                    is_latest=True
                )

                if success:
                    st.success("保存完了！")
                else:
                    st.error("DB保存に失敗しました。")
            except Exception as db_error:
                st.error(f"DB保存失敗: {db_error}", icon="❌")

