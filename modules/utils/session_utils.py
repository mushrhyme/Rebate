"""
Streamlit 세션 상태 유틸리티 모듈

세션 상태 초기화 등 UI 관련 공통 유틸리티 함수를 제공합니다.
"""

import streamlit as st


def ensure_session_state_defaults() -> None:
    """
    Streamlit 세션 상태의 기본 키들을 안전하게 초기화합니다.
    
    모든 UI 탭에서 공통으로 사용하는 세션 상태 키들을 초기화합니다.
    """
    defaults = {
        "uploaded_files_info": [],
        "uploaded_file_objects": {},
        "analysis_status": {},
        "selected_pdf": None,
        "selected_page": 1,
        "review_data": {}
    }
    for key, default_value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = default_value

