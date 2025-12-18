"""
Streamlit UI 메인 엔트리 포인트

이 파일은 페이지 설정, 스타일, 세션 초기화 및 탭 라우팅을 담당합니다.
각 탭의 실제 구현은 별도 파일로 분리되어 있습니다.
"""

import streamlit as st

# 공통 설정 로드 (PIL 설정, .env 로드 등)
from modules.utils.config import load_env
load_env()  # 명시적으로 .env 로드

# 탭 모듈 import
from modules.ui.upload_tab import render_upload_tab
from modules.ui.review_tab import render_review_tab
from modules.ui.download_tab import render_download_tab
from modules.ui.answer_editor_tab import render_answer_editor_tab

st.markdown("""
<style>
    /* 사이드바 숨기기 */
    [data-testid="stSidebar"] {
        display: none;
    }
    div.stButton button {
        border: none !important;
        font-weight: bold !important;
        transition: background-color 0.2s ease;
    }
    div.stButton button[data-testid="stBaseButton-primary"][kind="primary"] {
        background-color: #FF4B4B !important;
        color: white !important;
    }
    div.stButton button[data-testid="stBaseButton-primary"][kind="primary"]:not(:disabled):hover {
        background-color: #FF3030 !important;
    }
    div.stButton button[data-testid="stBaseButton-secondary"][kind="secondary"] {
        background-color: #F0F2F6 !important;
        color: #262730 !important;
    }
    div.stButton button[data-testid="stBaseButton-secondary"][kind="secondary"]:not(:disabled):hover {
        background-color: #E0E2E6 !important;
    }
    div.stButton button:disabled {
        background-color: #6c757d !important;
        opacity: 0.6 !important;
        cursor: not-allowed;
        color: white !important;
    }
</style>
""", unsafe_allow_html=True)



def main():
    """메인 함수"""
    # Streamlit 페이지 설정은 반드시 가장 먼저 호출되어야 함
    st.set_page_config(
        page_title="条件請求書パースシステム",
        
        page_icon="📄",
        layout="wide",
        initial_sidebar_state="collapsed"
    )
    st.title("Nongshim 条件請求書分析システム")
    tab1, tab2, tab3, tab4 = st.tabs(["📤 アップロード & 解析", "📝 レビュー", "📥 ダウンロード", "✏️ 정답지 편집"])
    with tab1:
        render_upload_tab()
    with tab2:
        render_review_tab()
    with tab3:
        render_download_tab()
    with tab4:
        render_answer_editor_tab()
