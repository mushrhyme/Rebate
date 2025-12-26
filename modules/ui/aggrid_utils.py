"""
AG Grid 유틸리티 모듈
"""

import streamlit as st
import json
import pandas as pd
from typing import List, Dict, Any

try:
    from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode, JsCode
    AGGrid_AVAILABLE = True
except ImportError:
    AGGrid_AVAILABLE = False
    JsCode = None


class AgGridUtils:
    """AG Grid 유틸리티 클래스"""
    
    @staticmethod
    def is_available() -> bool:
        """AG Grid 사용 가능 여부"""
        return AGGrid_AVAILABLE
    
    @staticmethod
    def render_items(items: List[Dict[str, Any]], pdf_name: str, page_num: int):
        """
        items를 AG Grid로 렌더링
        
        Args:
            items: 항목 리스트
            pdf_name: PDF 파일명 (확장자 제외)
            page_num: 페이지 번호 (1부터 시작)
        """
        if not AgGridUtils.is_available():
            st.warning("AG Gridが利用できません。")
            return
        
        import pandas as pd
        from modules.utils.session_manager import SessionManager
        
        # DataFrame으로 변환
        df = pd.DataFrame(items)
        
        # 인덱스 번호 컬럼 추가 (1부터 시작)
        df.insert(0, 'No', range(1, len(df) + 1))
        
        # 관리번호 컬럼명 확인 (management_id 또는 管理番号)
        mgmt_col = None
        if 'management_id' in df.columns:
            mgmt_col = 'management_id'
        elif '管理番号' in df.columns:
            mgmt_col = '管理番号'
        
        # 컬럼 순서 정의 (원하는 순서대로)
        desired_order = [
            'No',  # 번호 (가장 앞에)
            'management_id', '管理番号',  # 관리번호 (둘 중 하나)
            'customer', '取引先',  # 고객
            'product_name', '商品名',  # 상품명
            'units_per_case', 'ケース内入数',  # 케이스당 단위
            'case_count', 'ケース数',  # 케이스 수
            'bara_count', 'バラ数',  # 바라 수
            'quantity', '数量',  # 수량
            'amount', '金額'  # 금액
        ]
        
        # 실제 존재하는 컬럼만 필터링하고 순서대로 재정렬
        existing_cols = []
        for col in desired_order:
            if col in df.columns:
                existing_cols.append(col)
        
        # 나머지 컬럼들도 추가 (원하는 순서에 없는 컬럼들)
        remaining_cols = [col for col in df.columns if col not in existing_cols]
        
        # 최종 컬럼 순서: 원하는 순서 + 나머지 컬럼들
        final_column_order = existing_cols + remaining_cols
        
        # DataFrame 컬럼 순서 재정렬
        df = df[final_column_order]
        
        # 모든 값이 null인 컬럼 제거
        df = df.dropna(axis=1, how='all')
        
        # dropna 후 mgmt_col이 여전히 존재하는지 확인
        if mgmt_col and mgmt_col not in df.columns:
            mgmt_col = None
        
        # 컬럼명 일본어 매핑 (영어 → 일본어)
        column_name_mapping = {
            'No': 'No',  # 번호는 그대로
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
        
        # GridOptionsBuilder 설정
        gb = GridOptionsBuilder.from_dataframe(df)
        gb.configure_default_column(editable=True, resizable=True)
        
        # 각 컬럼의 헤더명을 일본어로 설정
        for col in df.columns:
            japanese_name = column_name_mapping.get(col, col)  # 매핑이 없으면 원래 이름 사용
            if col == 'No':
                # 번호 컬럼은 편집 불가, 너비 고정
                gb.configure_column(col, header_name=japanese_name, editable=False, width=60, pinned='left')
            else:
                gb.configure_column(col, header_name=japanese_name)
        
        # 행 선택 기능 비활성화 (체크박스 제거)
        # 인덱스 번호 표시
        # 페이지네이션 비활성화 (스크롤 방식 사용)
        gb.configure_pagination(enabled=False)
        
        # 관리번호별 색상 지정 (있는 경우)
        if mgmt_col and mgmt_col in df.columns:
            # 관리번호별로 고유 색상 매핑 생성
            management_numbers = df[mgmt_col].dropna().unique()
            
            # 색상 팔레트 (밝은 색상들)
            color_palette = [
                '#E3F2FD',  # 연한 파란색
                '#F3E5F5',  # 연한 보라색
                '#E8F5E9',  # 연한 초록색
                '#FFF3E0',  # 연한 주황색
                '#FCE4EC',  # 연한 분홍색
                '#E0F2F1',  # 연한 청록색
                '#FFF9C4',  # 연한 노란색
                '#F1F8E9',  # 연한 라임색
                '#E1BEE7',  # 연한 자주색
                '#BBDEFB',  # 연한 하늘색
            ]
            
            # 관리번호별 색상 매핑 딕셔너리 생성
            color_map = {}
            for idx, mgmt_id in enumerate(management_numbers):
                if mgmt_id and pd.notna(mgmt_id):  # None이 아닌 경우만
                    color_map[str(mgmt_id)] = color_palette[idx % len(color_palette)]
            
            # getRowStyle을 JsCode 객체로 정의
            # st_aggrid는 JsCode 객체를 사용해야 함
            js_color_map = json.dumps(color_map)
            js_mgmt_col = json.dumps(mgmt_col)
            
            get_row_style_js = f"""
            function(params) {{
                if (params.data && params.data[{js_mgmt_col}]) {{
                    var mgmtId = String(params.data[{js_mgmt_col}]);
                    var colorMap = {js_color_map};
                    if (colorMap[mgmtId]) {{
                        return {{
                            backgroundColor: colorMap[mgmtId],
                            color: '#000000'
                        }};
                    }}
                }}
                return null;
            }}
            """
            
            # JsCode 객체로 변환
            if JsCode:
                get_row_style_code = JsCode(get_row_style_js)
            else:
                # JsCode가 없으면 문자열로 사용 (구버전 호환)
                get_row_style_code = get_row_style_js
            
            # grid_options에 getRowStyle 추가
            grid_options = gb.build()
            grid_options['getRowStyle'] = get_row_style_code
            # 페이지네이션 완전히 비활성화 (스크롤 방식)
            grid_options['pagination'] = False
            # 컬럼 자동 너비 조정 (내용에 맞게) - onGridReady 콜백 사용
            if JsCode:
                auto_size_js = JsCode("""
                function(params) {
                    params.api.sizeColumnsToFit();
                    // 모든 컬럼의 내용에 맞게 너비 자동 조정
                    var allColumnIds = [];
                    params.columnApi.getColumns().forEach(function(column) {
                        if (column.colId) {
                            allColumnIds.push(column.colId);
                        }
                    });
                    params.columnApi.autoSizeColumns(allColumnIds);
                }
                """)
            else:
                auto_size_js = """
                function(params) {
                    params.api.sizeColumnsToFit();
                    var allColumnIds = [];
                    params.columnApi.getColumns().forEach(function(column) {
                        if (column.colId) {
                            allColumnIds.push(column.colId);
                        }
                    });
                    params.columnApi.autoSizeColumns(allColumnIds);
                }
                """
            grid_options['onGridReady'] = auto_size_js
            # 행 선택 기능 완전히 제거
            if 'rowSelection' in grid_options:
                del grid_options['rowSelection']
            if 'suppressRowClickSelection' in grid_options:
                del grid_options['suppressRowClickSelection']
            if 'rowMultiSelectWithClick' in grid_options:
                del grid_options['rowMultiSelectWithClick']
        else:
            grid_options = gb.build()
            # 페이지네이션 완전히 비활성화 (스크롤 방식)
            grid_options['pagination'] = False
            # 컬럼 자동 너비 조정 (내용에 맞게) - onGridReady 콜백 사용
            if JsCode:
                auto_size_js = JsCode("""
                function(params) {
                    params.api.sizeColumnsToFit();
                    // 모든 컬럼의 내용에 맞게 너비 자동 조정
                    var allColumnIds = [];
                    params.columnApi.getColumns().forEach(function(column) {
                        if (column.colId) {
                            allColumnIds.push(column.colId);
                        }
                    });
                    params.columnApi.autoSizeColumns(allColumnIds);
                }
                """)
            else:
                auto_size_js = """
                function(params) {
                    params.api.sizeColumnsToFit();
                    var allColumnIds = [];
                    params.columnApi.getColumns().forEach(function(column) {
                        if (column.colId) {
                            allColumnIds.push(column.colId);
                        }
                    });
                    params.columnApi.autoSizeColumns(allColumnIds);
                }
                """
            grid_options['onGridReady'] = auto_size_js
            # 행 선택 기능 완전히 제거
            if 'rowSelection' in grid_options:
                del grid_options['rowSelection']
            if 'suppressRowClickSelection' in grid_options:
                del grid_options['suppressRowClickSelection']
            if 'rowMultiSelectWithClick' in grid_options:
                del grid_options['rowMultiSelectWithClick']
        
        # AG Grid 렌더링
        # allow_unsafe_jscode=True는 JavaScript 코드를 사용하기 위해 필요
        grid_response = AgGrid(
            df,
            gridOptions=grid_options,
            update_mode=GridUpdateMode.VALUE_CHANGED,
            data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
            fit_columns_on_grid_load=True,
            height=400,
            theme='streamlit',
            allow_unsafe_jscode=True,  # JavaScript 코드 사용을 위해 필요
            hide_index=False  # 인덱스 번호 표시
        )
        
        # 수정된 데이터 저장
        if grid_response['data'] is not None:
            updated_df = grid_response['data']
            
            # 'No' 컬럼 제거 (표시용이므로 저장 시 제거)
            if 'No' in updated_df.columns:
                updated_df = updated_df.drop(columns=['No'])
            
            updated_items = updated_df.to_dict('records')
            
            # 페이지 데이터 업데이트
            page_data = SessionManager.load_ocr_result(pdf_name, page_num)
            if page_data:
                page_data["items"] = updated_items
                
                # DB에 저장 (JSON 파일 저장은 제거)
                col1, col2 = st.columns([1, 4])
                with col1:
                    save_clicked = st.button("保存", width='stretch')
                with col2:
                    st.markdown(
                        '<p style="color: #ffc107; font-weight: bold; margin-top: 0.5rem;">⚠️ データ修正後は必ず保存してください</p>',
                        unsafe_allow_html=True
                    )
                
                if save_clicked:
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
                            # 탭 상태 유지
                            if "active_tab" not in st.session_state:
                                st.session_state.active_tab = "📝 レビュー"
                            st.rerun()
                        else:
                            st.error("DB保存に失敗しました。")
                    except Exception as db_error:
                        st.error(f"DB保存失敗: {db_error}", icon="❌")

