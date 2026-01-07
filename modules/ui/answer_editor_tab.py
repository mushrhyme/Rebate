"""
정답지 편집 탭 - PDF 텍스트 추출 (PyMuPDF 또는 엑셀 변환)
"""

import os
from pathlib import Path
import fitz
import streamlit as st
import json
import re
from PIL import Image
import io
import traceback
import time
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode, JsCode

from modules.utils.openai_utils import ask_openai_with_reference
from src.rag_extractor import extract_json_with_rag
# from src.gemini_extractor import GeminiVisionParser  # Gemini Extractor 기능 삭제됨
from modules.ui.aggrid_utils import AgGridUtils
import pandas as pd
from modules.core.rag_manager import get_rag_manager
from modules.utils.config import get_project_root, get_rag_config
from modules.utils.session_utils import ensure_session_state_defaults
from modules.utils.pdf_utils import find_pdf_path, extract_text_from_pdf_page

def flatten_dict(d, parent_key='', sep='_'):
    """
    딕셔너리를 평탄화하는 함수
    
    Args:
        d: 평탄화할 딕셔너리
        parent_key: 부모 키 (재귀 호출 시 사용)
        sep: 키 구분자
        
    Returns:
        평탄화된 딕셔너리
    """
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def unflatten_dict(flat_dict, sep='_'):
    """
    평탄화된 딕셔너리를 중첩 구조로 복원하는 함수
    
    Args:
        flat_dict: 평탄화된 딕셔너리
        sep: 키 구분자
        
    Returns:
        중첩된 딕셔너리
    """
    result = {}
    for key, value in flat_dict.items():
        parts = key.split(sep)
        d = result
        for part in parts[:-1]:
            if part not in d:
                d[part] = {}
            d = d[part]
        d[parts[-1]] = value
    return result


def filter_answer_json(answer_json: dict) -> dict:
    """
    정답 JSON에서 필요한 필드만 추출 (page_role과 items만)
    
    Args:
        answer_json: 원본 JSON 딕셔너리
        
    Returns:
        필터링된 JSON 딕셔너리 (page_role과 items만 포함)
    """
    filtered = {
        "page_role": answer_json.get("page_role", "detail"),
        "items": answer_json.get("items", [])
    }
    return filtered


def get_answer_json_path(pdf_img_dir: Path, page_num: int, version: str = "v2") -> Path:
    """
    정답지 JSON 파일 경로 생성
    
    Args:
        pdf_img_dir: PDF 이미지 디렉토리 경로
        page_num: 페이지 번호 (1부터 시작)
        version: 사용하지 않음 (호환성을 위해 유지)
        
    Returns:
        정답지 JSON 파일 경로 (Page{num}_answer.json)
    """
    return pdf_img_dir / f"Page{page_num}_answer.json"


def get_prompt_file_path(version: str = "v1", use_example: bool = True) -> Path:
    """
    프롬프트 파일 경로를 버전에 따라 생성
    
    Args:
        version: 정답지 버전 ("v1" 또는 "v2")
        use_example: 예제 사용 여부 (True: rag_with_example, False: rag_zero_shot)
        
    Returns:
        프롬프트 파일 경로
    """
    project_root = get_project_root()
    prompts_dir = project_root / "prompts"
    
    if use_example:
        return prompts_dir / f"rag_with_example_{version}.txt"
    else:
        return prompts_dir / f"rag_zero_shot_{version}.txt"




def prepare_dataframe_for_aggrid(items):
    """Items를 AgGrid용 DataFrame으로 변환"""
    # items가 비어있거나 None인 경우 빈 DataFrame 반환
    if not items:
        return pd.DataFrame(), None
    
    # DataFrame 생성 시도
    try:
        df = pd.DataFrame(items)
    except Exception as e:
        # DataFrame 생성 실패 시 빈 DataFrame 반환
        return pd.DataFrame(), None
    
    # DataFrame이 비어있으면 빈 DataFrame 반환
    if len(df) == 0:
        return df, None
    
    # No 컬럼 추가 (1부터 시작) - AgGrid 표시용
    df.insert(0, 'No', range(1, len(df) + 1))

    # 관리번호 컬럼 확인
    mgmt_col = 'management_id' if 'management_id' in df.columns else ('管理番号' if '管理番号' in df.columns else None)

    # 컬럼 순서는 JSON 그대로 유지 (재정렬하지 않음)
    # 모든 값이 null인 컬럼 제거 (단, No 컬럼은 유지)
    df = df.dropna(axis=1, how='all')
    
    # No 컬럼이 제거되었으면 다시 추가
    if 'No' not in df.columns and len(df) > 0:
        df.insert(0, 'No', range(1, len(df) + 1))

    # 관리번호 컬럼이 제거되었으면 None으로 설정
    if mgmt_col and mgmt_col not in df.columns:
        mgmt_col = None

    return df, mgmt_col


def create_management_color_style(mgmt_col, df):
    """관리번호별 색상 스타일 생성"""
    if not mgmt_col or mgmt_col not in df.columns or len(df) == 0:
        return None

    management_numbers = df[mgmt_col].dropna().unique()
    color_palette = ['#E3F2FD', '#F3E5F5', '#E8F5E9', '#FFF3E0', '#FCE4EC',
                     '#E0F2F1', '#FFF9C4', '#F1F8E9', '#E1BEE7', '#BBDEFB']
    color_map = {str(mgmt_id): color_palette[idx % len(color_palette)]
                 for idx, mgmt_id in enumerate(management_numbers) if pd.notna(mgmt_id)}

    get_row_style_js = f"""
    function(params) {{
        if (params.data && params.data[{json.dumps(mgmt_col)}]) {{
            var mgmtId = String(params.data[{json.dumps(mgmt_col)}]);
            var colorMap = {json.dumps(color_map)};
            if (colorMap[mgmtId]) {{
                return {{ backgroundColor: colorMap[mgmtId], color: '#000000' }};
            }}
        }}
        return null;
    }}
    """
    return JsCode(get_row_style_js)


def create_comparison_dataframe(openai_items, answer_items):
    """OpenAI 응답과 정답지를 비교하는 DataFrame 생성"""
    openai_df = pd.DataFrame(openai_items)
    answer_df = pd.DataFrame(answer_items)

    if len(openai_df) > 0:
        openai_df.insert(0, 'No', range(1, len(openai_df) + 1))
    if len(answer_df) > 0:
        answer_df.insert(0, 'No', range(1, len(answer_df) + 1))

    # JSON 파일 내 순서 유지 (정답지 컬럼 순서 우선, 없으면 OpenAI 응답 컬럼 순서)
    answer_cols = [col for col in answer_df.columns if col != 'No'] if len(answer_df) > 0 else []
    openai_cols = [col for col in openai_df.columns if col != 'No'] if len(openai_df) > 0 else []
    
    # 정답지 컬럼 순서를 기준으로 하되, 없는 컬럼은 OpenAI 응답에서 추가
    ordered_cols = answer_cols + [col for col in openai_cols if col not in answer_cols]

    comparison_data = []
    for i in range(max(len(openai_df), len(answer_df))):
        row_data = {"No": i + 1}
        for col in ordered_cols:
            row_data[f"응답_{col}"] = openai_df.iloc[i][col] if i < len(openai_df) and col in openai_df.columns else None
            row_data[f"정답_{col}"] = answer_df.iloc[i][col] if i < len(answer_df) and col in answer_df.columns else None

        if i < len(openai_df) and i < len(answer_df):
            matches = [openai_df.iloc[i][f] == answer_df.iloc[i][f] if f in openai_df.columns and f in answer_df.columns
                      and not (pd.isna(openai_df.iloc[i][f]) or pd.isna(answer_df.iloc[i][f]))
                      else (pd.isna(openai_df.iloc[i][f]) and pd.isna(answer_df.iloc[i][f]))
                      for f in ordered_cols if f in openai_df.columns and f in answer_df.columns]
            row_data["일치율"] = f"{sum(matches)}/{len(matches)}" if matches else "N/A"
            row_data["_match_rate"] = sum(matches) / len(matches) if matches else 0

        comparison_data.append(row_data)

    comparison_df = pd.DataFrame(comparison_data)
    final_order = ['No'] + [f"{prefix}_{col}" for col in ordered_cols for prefix in ["응답", "정답"]]
    final_order.extend([col for col in ["일치율", "_match_rate"] if col in comparison_df.columns])
    return comparison_df[[col for col in final_order if col in comparison_df.columns]]


def render_comparison_grid(comparison_df, current_page):
    """비교 데이터프레임을 AgGrid로 렌더링"""
    if not AgGridUtils.is_available():
        st.dataframe(comparison_df, height=400)
        return
    
    gb = GridOptionsBuilder.from_dataframe(comparison_df)
    gb.configure_default_column(editable=False, resizable=True)
    gb.configure_pagination(enabled=False)

    # 컬럼 헤더 설정
    for col in comparison_df.columns:
        if col == 'No':
            gb.configure_column(col, header_name='No', editable=False, width=60, pinned='left')
        elif col == "일치율":
            gb.configure_column(col, header_name="일치율", pinned='right', width=100)
        elif col == "_match_rate":
            gb.configure_column(col, hide=True)
        elif col.startswith("응답_"):
            original_col = col.replace("응답_", "")
            gb.configure_column(col, header_name=f"응답: {original_col}")
        elif col.startswith("정답_"):
            original_col = col.replace("정답_", "")
            gb.configure_column(col, header_name=f"정답: {original_col}")
        else:
            gb.configure_column(col, header_name=col)

    # 개별 셀 색상 지정
    for col in comparison_df.columns:
        if col.startswith("응답_"):
            original_col = col.replace("응답_", "")
            answer_col = f"정답_{original_col}"
            if answer_col in comparison_df.columns:
                cell_style_js = f"""
                function(params) {{
                    if (params.data) {{
                        var r = params.data['{col}'];
                        var a = params.data['{answer_col}'];

                        // null, undefined, NaN을 null로 통일
                        if (r === null || r === undefined || (typeof r === 'number' && isNaN(r))) r = null;
                        if (a === null || a === undefined || (typeof a === 'number' && isNaN(a))) a = null;

                        // 둘 다 null이면 일치 (빨간색 표시 안 함)
                        if (r === null && a === null) return null;

                        // 하나만 null이면 불일치
                        if (r === null || a === null) {{
                            return {{ color: '#DC143C', fontWeight: 'bold' }};
                        }}

                        // 값 비교: 먼저 엄격한 비교, 그 다음 문자열 비교
                        if (r === a) return null;  // 완전히 일치하면 빨간색 표시 안 함
                        if (String(r).trim() === String(a).trim()) return null;  // 문자열로 변환 후 공백 제거하여 비교

                        // 불일치 시 빨간색 표시
                        return {{ color: '#DC143C', fontWeight: 'bold' }};
                    }}
                    return null;
                }}
                """
                gb.configure_column(col, cellStyle=JsCode(cell_style_js))

    # 행 배경색 지정
    if "_match_rate" in comparison_df.columns:
        get_row_style_js = """
        function(params) {
            if (params.data && params.data._match_rate !== undefined) {
                var m = params.data._match_rate;
                if (m === 1.0) return { backgroundColor: '#E8F5E9', color: '#000000' };
                if (m >= 0.8) return { backgroundColor: '#FFF9C4', color: '#000000' };
                if (m >= 0.5) return { backgroundColor: '#FFF3E0', color: '#000000' };
                return { backgroundColor: '#FFEBEE', color: '#000000' };
            }
            return null;
        }
        """
        grid_options = gb.build()
        grid_options['getRowStyle'] = JsCode(get_row_style_js)
    else:
        grid_options = gb.build()
    grid_options['pagination'] = False

    AgGrid(comparison_df, gridOptions=grid_options, update_mode=GridUpdateMode.NO_UPDATE,
           data_return_mode=DataReturnMode.FILTERED_AND_SORTED, fit_columns_on_grid_load=True,
           height=400, theme='streamlit', allow_unsafe_jscode=True, hide_index=False,
           key=f"comparison_grid_{current_page}")

    st.caption("**일치율 색상 범례**: 🟢 초록색 (100% 일치) | 🟡 노란색 (80% 이상) | 🟠 주황색 (50% 이상) | 🔴 빨간색 (50% 미만)")


def process_single_page(
    page_info: dict,
    pdf_path: Path,
    reference_json: dict = None,
    reference_page_num: int = None,
    total_pages: int = 0,
    version: str = "v1"
) -> tuple[int, bool, str]:
    """
    단일 페이지를 처리하여 JSON을 생성합니다.
    
    Args:
        page_info: 페이지 정보 딕셔너리 (page_num, ocr_text, answer_json_path 포함)
        pdf_path: PDF 파일 경로
        reference_json: 기준 페이지 JSON (None이면 RAG 사용)
        reference_page_num: 기준 페이지 번호
        total_pages: 전체 페이지 수
        
    Returns:
        (page_num, success, message) 튜플
    """
    page_num = page_info["page_num"]
    
    # 기준 페이지는 건너뛰기
    if reference_page_num and page_num == reference_page_num:
        return (page_num, True, f"페이지 {page_num}/{total_pages} 건너뜀 (기준 페이지)")
    
    try:
        # OCR 텍스트 추출
        ocr_text = page_info.get("ocr_text", "")
        if not ocr_text and pdf_path.exists():
            ocr_text = extract_text_from_pdf_page(pdf_path, page_num)
        
        if not ocr_text:
            return (page_num, False, f"페이지 {page_num}: 텍스트 추출 실패")
        
        # 기준 페이지가 있으면 RAG 없이 직접 사용, 없으면 RAG로 유사 예제 찾기
        if reference_json:
            # 기준 페이지 JSON을 직접 사용 (RAG 없이)
            result_json = ask_openai_with_reference(
                ocr_text=ocr_text,
                answer_json=reference_json,
                question=ocr_text,
                model_name="gpt-4o-2024-08-06",
                use_langchain=False,
                temperature=0.0
            )
        else:
            # RAG로 유사 예제 찾아서 LLM 호출 (progress_callback은 None으로 설정)
            result_json = extract_json_with_rag(
                ocr_text=ocr_text,
                question=None,  # config에서 가져옴
                model_name=None,  # config에서 가져옴
                temperature=0.0,
                top_k=None,  # config에서 가져옴
                similarity_threshold=None,  # config에서 가져옴
                progress_callback=None,  # 병렬 처리에서는 콜백 미사용
                page_num=page_num,
                prompt_version=version  # 정답지 버전에 따라 프롬프트 파일 선택
            )
        
        # 결과 저장
        with open(page_info["answer_json_path"], "w", encoding="utf-8") as f:
            json.dump(result_json, f, ensure_ascii=False, indent=2)
        
        return (page_num, True, f"페이지 {page_num}/{total_pages} 처리 완료")
        
    except Exception as e:
        return (page_num, False, f"페이지 {page_num}: 오류 발생 - {str(e)}")


def find_pdf_path_with_form(img_dir: Path, pdf_name: str, form_folder: str = None) -> Path:
    """
    양식 폴더를 고려하여 PDF 경로를 찾습니다.
    
    Args:
        img_dir: img 폴더 경로
        pdf_name: PDF 파일명 (확장자 제외)
        form_folder: 양식 폴더명 (예: "01", "02"). None이면 모든 양식 폴더에서 찾기
        
    Returns:
        PDF 파일 경로 (없으면 None)
    """
    if form_folder and form_folder != "전체":
        # 선택된 양식 폴더에서 찾기
        pdf_path = img_dir / form_folder / pdf_name / f"{pdf_name}.pdf"
        if pdf_path.exists():
            return pdf_path
    else:
        # 모든 양식 폴더에서 찾기
        for form_folder_name in sorted([d.name for d in img_dir.iterdir() if d.is_dir() and d.name.isdigit()]):
            pdf_path = img_dir / form_folder_name / pdf_name / f"{pdf_name}.pdf"
            if pdf_path.exists():
                return pdf_path
    
    # 세션 디렉토리에서도 찾기
    session_pdf_path = find_pdf_path(pdf_name)
    if session_pdf_path and Path(session_pdf_path).exists():
        return Path(session_pdf_path)
    
    return None


def render_answer_editor_tab():
    """정답지 편집 탭"""
    ensure_session_state_defaults()

    # 세션 상태 초기화
    if "answer_editor_pdfs" not in st.session_state:
        st.session_state.answer_editor_pdfs = {}
    if "answer_editor_selected_pdf" not in st.session_state:
        st.session_state.answer_editor_selected_pdf = None
    if "answer_editor_selected_page" not in st.session_state:
        st.session_state.answer_editor_selected_page = 1
    # 항상 v2 사용 (버전 선택 옵션 제거)
    if "answer_editor_version" not in st.session_state:
        st.session_state.answer_editor_version = "v2"

    # 설정에 따라 텍스트 추출 방법 표시
    from modules.utils.config import get_rag_config
    import os
    config = get_rag_config()
    extraction_method = getattr(config, 'text_extraction_method', 'pymupdf')
    method_name = "엑셀 변환" if extraction_method == "excel" else "PyMuPDF"
    keep_excel = os.getenv("KEEP_EXCEL_FILES", "false").lower() == "true"
    
    info_text = f"**📌 정답지 편집 가이드**:\n\n"
    info_text += f"• PDF 파일을 업로드하면 자동으로 이미지로 변환되고 **{method_name}**으로 텍스트를 추출합니다\n\n"
    
    if extraction_method == "excel":
        info_text += f"• **엑셀 변환 방식**: PDF → 엑셀 파일(.xlsx) 생성 → 엑셀 파일 읽기 → 텍스트 변환\n\n"
        if keep_excel:
            info_text += "• 엑셀 파일이 PDF와 같은 폴더에 저장됩니다 (KEEP_EXCEL_FILES=true)\n\n"
        else:
            info_text += "• 엑셀 파일은 임시로 생성 후 자동 삭제됩니다 (KEEP_EXCEL_FILES=false)\n\n"
    
    info_text += "• 각 페이지별로 원문 텍스트, 텍스트 추출 결과, 정답 JSON을 편집할 수 있습니다\n\n"
    info_text += "• 정답 JSON은 RAG 학습용 정답지로 사용됩니다"
    
    st.info(info_text, icon="ℹ️")

    # 양식 폴더 목록 확인 (01, 02, 03, 04, 05 등)
    project_root = get_project_root()
    img_dir = project_root / "img"
    form_folders = []
    if img_dir.exists():
        for item in img_dir.iterdir():
            if item.is_dir() and item.name.isdigit():
                form_folders.append(item.name)
        form_folders.sort()  # 숫자 순서로 정렬
    
    # 양식 선택 UI
    if form_folders:
        st.subheader("📁 양식 선택")
        selected_form = st.selectbox(
            "양식 폴더 선택",
            options=["전체"] + form_folders,
            key="answer_editor_form_selector",
            help="양식별 폴더를 선택하면 해당 양식의 PDF만 표시됩니다"
        )
    else:
        selected_form = "전체"
    
    # 정답지 버전 선택 UI 제거 (항상 v2 사용)
    st.session_state.answer_editor_version = "v2"
    
    # 기존 처리된 PDF 목록 확인 (선택된 양식 폴더 기준)
    existing_pdfs = []
    if img_dir.exists():
        if selected_form == "전체":
            # 모든 양식 폴더 순회
            for form_folder_name in form_folders:
                form_folder = img_dir / form_folder_name
                if form_folder.exists():
                    for item in form_folder.iterdir():
                        if item.is_dir():
                            if (item / "Page1.png").exists():
                                existing_pdfs.append(f"{form_folder_name}/{item.name}")
        else:
            # 선택된 양식 폴더만 순회
            form_folder = img_dir / selected_form
            if form_folder.exists():
                for item in form_folder.iterdir():
                    if item.is_dir():
                        if (item / "Page1.png").exists():
                            existing_pdfs.append(item.name)
    
    # 여러 PDF 일괄 벡터 DB 저장 섹션
    with st.expander("🔍 벡터 DB 구축", expanded=False):
        st.info("img 폴더의 하위 폴더에 있는 PDF 파일들을 벡터 DB에 저장합니다.")
        st.caption("• img 폴더의 모든 하위 폴더를 순회합니다")
        st.caption("• 각 하위 폴더의 PDF 파일에서 PyMuPDF로 텍스트를 추출합니다")
        st.caption("• 하위 폴더의 Page*_answer.json 파일을 정답지로 사용합니다")
        
        # 기존 벡터 DB 상태 확인
        try:
            rag_manager = get_rag_manager()
            existing_count = rag_manager.count_examples()
            if existing_count > 0:
                st.caption(f"📊 현재 벡터 DB 예제 수: {existing_count}개")
        except Exception:
            pass
        
        # 벡터 DB 구축 버튼
        if st.button("🚀 벡터 DB 구축 실행", type="primary", key="build_faiss_db"):
            try:
                from build_faiss_db import build_faiss_db
                
                with st.spinner("벡터 DB 구축 중..."):
                    # 기존 예제 수 저장
                    rag_manager = get_rag_manager()
                    before_count = rag_manager.count_examples()
                    
                    # build_faiss_db 실행 (선택된 양식 폴더 기준)
                    project_root = get_project_root()
                    img_dir = project_root / "img"
                    form_folder = selected_form if selected_form != "전체" else None
                    build_faiss_db(img_dir, form_folder=form_folder, auto_merge=True)
                    
                    # 결과 확인
                    after_count = rag_manager.count_examples()
                    added_count = after_count - before_count
                    
                    if added_count > 0:
                        st.success(f"✅ 벡터 DB 구축 완료!")
                        st.caption(f"**구축 결과:**")
                        st.caption(f"- 새로 추가된 예제: {added_count}개")
                        st.caption(f"- **총 예제 수: {after_count}개**")
                    else:
                        st.warning("⚠️ 새로 추가된 예제가 없습니다. img 폴더에 PDF 파일이 있는지 확인하세요.")
                    
            except PermissionError as e:
                st.error(f"❌ 벡터 DB 구축 실패 (권한 문제): {e}")
                st.info("💡 해결 방법: 터미널에서 다음 명령어를 실행하세요:\n"
                       f"`chmod -R 755 faiss_db` 또는 `sudo chmod -R 755 faiss_db`")
            except Exception as e:
                error_msg = str(e)
                if "readonly" in error_msg.lower():
                    st.error(f"❌ 벡터 DB 구축 실패 (읽기 전용 오류): {error_msg}")
                    st.info("💡 해결 방법:\n"
                           "1. `chmod -R 755 faiss_db` 명령어로 권한 수정\n"
                           "2. 또는 `faiss_db` 디렉토리를 삭제하고 다시 시도")
                else:
                    st.error(f"❌ 벡터 DB 구축 실패: {error_msg}")
                    with st.expander("상세 오류 정보"):
                        st.code(traceback.format_exc())
            else:
                st.info("💡 위에서 저장할 PDF를 선택하세요.")

    # PDF 선택 (기존 또는 새 업로드)
    if existing_pdfs:
        st.subheader("📁 기존 처리된 PDF 선택")
        selected_existing = st.selectbox(
            "처리된 PDF 선택",
            options=["새로 업로드"] + existing_pdfs,
            key="answer_editor_existing_pdf"
        )

        if selected_existing != "새로 업로드":
            # 양식 폴더 경로 처리
            if "/" in selected_existing:
                form_folder_name, pdf_name = selected_existing.split("/", 1)
                pdf_img_dir = img_dir / form_folder_name / pdf_name
            else:
                pdf_name = selected_existing
                if selected_form != "전체":
                    pdf_img_dir = img_dir / selected_form / pdf_name
                else:
                    # 전체 모드에서는 첫 번째 양식 폴더에서 찾기
                    pdf_img_dir = None
                    for form_folder_name in form_folders:
                        candidate_dir = img_dir / form_folder_name / pdf_name
                        if candidate_dir.exists() and (candidate_dir / "Page1.png").exists():
                            pdf_img_dir = candidate_dir
                            break
                    if pdf_img_dir is None:
                        pdf_img_dir = img_dir / pdf_name
            
            if pdf_name not in st.session_state.answer_editor_pdfs:
                st.session_state.answer_editor_pdfs[pdf_name] = {
                    "pages": [],
                    "processed": False
                }

            pdf_info = st.session_state.answer_editor_pdfs[pdf_name]
            if not pdf_info["processed"]:
                page_info_list = []
                page_num = 1
                while True:
                    image_path = pdf_img_dir / f"Page{page_num}.png"
                    if not image_path.exists():
                        break
                    answer_json_path = get_answer_json_path(pdf_img_dir, page_num, st.session_state.answer_editor_version)
                    # fitz를 사용하여 PDF에서 텍스트 추출
                    pdf_path = pdf_img_dir / f"{pdf_name}.pdf"
                    if not pdf_path.exists():
                        # 양식 폴더를 고려하여 PDF 경로 찾기
                        found_path = find_pdf_path_with_form(img_dir, pdf_name, selected_form)
                        if found_path:
                            pdf_path = found_path
                    
                    ocr_text = ""
                    if pdf_path.exists():
                        ocr_text = extract_text_from_pdf_page(pdf_path, page_num)
                    page_info_list.append({
                        "page_num": page_num,
                        "image_path": str(image_path),
                        "answer_json_path": str(answer_json_path),
                        "ocr_text": ocr_text
                    })
                    page_num += 1
                if page_info_list:
                    pdf_info["pages"] = page_info_list
                    pdf_info["processed"] = True
                    st.session_state.answer_editor_selected_pdf = pdf_name
                    st.session_state.answer_editor_selected_page = 1
                    # 탭 상태 유지
                    if "active_tab" not in st.session_state:
                        st.session_state.active_tab = "✏️ 정답지 편집"
                    st.rerun()

    # PDF 업로드
    st.subheader("📤 새 PDF 업로드")
    uploaded_file = st.file_uploader(
        "PDFファイルをアップロードしてください（정답지 편집용）",
        type=['pdf'],
        accept_multiple_files=False,
        key="answer_editor_uploader"
    )

    if uploaded_file:
        pdf_name = Path(uploaded_file.name).stem

        if pdf_name not in st.session_state.answer_editor_pdfs:
            st.session_state.answer_editor_pdfs[pdf_name] = {
                "pages": [],
                "processed": False
            }

        pdf_info = st.session_state.answer_editor_pdfs[pdf_name]

        if not pdf_info["processed"]:
            if st.button("🔄 PDF 처리 시작 (이미지 변환 + PyMuPDF 텍스트 추출)", type="primary"):
                with st.spinner("PDF를 처리하는 중... (fitz 기반 이미지 추출)"):
                    try:
                        # 저장 경로 준비
                        project_root = get_project_root()
                        img_dir = project_root / "img" / pdf_name
                        img_dir.mkdir(parents=True, exist_ok=True)
                        temp_pdf_path = img_dir / f"{pdf_name}.pdf"
                        with open(temp_pdf_path, "wb") as f:
                            f.write(uploaded_file.getvalue())

                        # PDF to image (fitz) - PIL Image로 변환 및 저장
                        doc = fitz.open(temp_pdf_path)
                        total_pages = doc.page_count

                        page_info_list = []
                        progress_bar = st.progress(0)
                        status_text = st.empty()

                        for page_idx in range(total_pages):
                            page = doc.load_page(page_idx)
                            pix = page.get_pixmap(dpi=300)
                            img_bytes = pix.tobytes("png")
                            image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
                            page_num = page_idx + 1

                            image_path = img_dir / f"Page{page_num}.png"
                            image.save(image_path, "PNG", dpi=(300, 300), optimize=True)

                            answer_json_path = get_answer_json_path(img_dir, page_num, st.session_state.answer_editor_version)

                            status_text.text(f"페이지 {page_num}/{total_pages} 처리 중...")
                            
                            # PyMuPDF로 텍스트 추출
                            ocr_text = extract_text_from_pdf_page(temp_pdf_path, page_num)
                            
                            page_info_list.append({
                                "page_num": page_num,
                                "image_path": str(image_path),
                                "answer_json_path": str(answer_json_path),
                                "ocr_text": ocr_text  # upstage_text 대신 ocr_text 사용
                            })
                            progress_bar.progress((page_idx + 1) / total_pages)
                        
                        doc.close()
                        progress_bar.empty()
                        status_text.empty()

                        pdf_info["pages"] = page_info_list
                        pdf_info["processed"] = True
                        st.session_state.answer_editor_selected_pdf = pdf_name
                        st.session_state.answer_editor_selected_page = 1

                        st.success(f"✅ PDF 처리 완료! {len(page_info_list)}개 페이지")
                        # 탭 상태 유지
                        if "active_tab" not in st.session_state:
                            st.session_state.active_tab = "✏️ 정답지 편집"
                        st.rerun()
                    except Exception as e:
                        st.error(f"PDF 처리 실패: {e}", icon="❌")


    processed_pdfs = [name for name, info in st.session_state.answer_editor_pdfs.items()
                      if info.get("processed") and info.get("pages")]

    if processed_pdfs:
        # PDF 선택
        if st.session_state.answer_editor_selected_pdf not in processed_pdfs:
            st.session_state.answer_editor_selected_pdf = processed_pdfs[0]
            st.session_state.answer_editor_selected_page = 1

        if len(processed_pdfs) > 1:
            selected_pdf = st.selectbox(
                "편집할 PDF 선택",
                options=processed_pdfs,
                index=processed_pdfs.index(st.session_state.answer_editor_selected_pdf),
                key="answer_editor_pdf_selector"
            )
            if selected_pdf != st.session_state.answer_editor_selected_pdf:
                st.session_state.answer_editor_selected_pdf = selected_pdf
                st.session_state.answer_editor_selected_page = 1
                # 탭 상태 유지
                if "active_tab" not in st.session_state:
                    st.session_state.active_tab = "✏️ 정답지 편집"
                st.rerun()
        else:
            selected_pdf = processed_pdfs[0]
            st.session_state.answer_editor_selected_pdf = selected_pdf

        pdf_info = st.session_state.answer_editor_pdfs[selected_pdf]

        if pdf_info["processed"] and pdf_info["pages"]:
            st.divider()
            st.subheader("📝 정답지 편집")
            total_pages = len(pdf_info["pages"])
            
            # 기존 데이터 호환성: upstage_text가 있으면 ocr_text로 변환
            for page_info in pdf_info["pages"]:
                if "ocr_text" not in page_info and "upstage_text" in page_info:
                    page_info["ocr_text"] = page_info["upstage_text"]
                # ocr_text가 없으면 PDF에서 추출 시도
                if not page_info.get("ocr_text"):
                    pdf_path = find_pdf_path_with_form(img_dir, selected_pdf, selected_form)
                    if pdf_path and pdf_path.exists():
                        page_info["ocr_text"] = extract_text_from_pdf_page(pdf_path, page_info["page_num"])
            
            pages_with_ocr = [p for p in pdf_info["pages"] if p.get("ocr_text")]

            if pages_with_ocr:
                # 기준 페이지 선택 UI
                st.caption("**기준 페이지 설정** (선택사항): 기준 페이지의 JSON 정보를 참조하여 다른 페이지를 추출합니다")
                col_ref1, col_ref2 = st.columns([1, 3])
                with col_ref1:
                    reference_page_options = ["없음"] + [f"페이지 {p['page_num']}" for p in pdf_info["pages"] if os.path.exists(p.get("answer_json_path", ""))]
                    reference_page_idx = 0
                    if "answer_editor_reference_page" in st.session_state:
                        try:
                            ref_page_num = st.session_state.answer_editor_reference_page
                            ref_page_str = f"페이지 {ref_page_num}"
                            if ref_page_str in reference_page_options:
                                reference_page_idx = reference_page_options.index(ref_page_str)
                        except:
                            pass

                    selected_reference = st.selectbox(
                        "기준 페이지",
                        options=reference_page_options,
                        index=reference_page_idx,
                        key="answer_editor_reference_page_selector",
                        help="기준 페이지를 선택하면 해당 페이지의 JSON 정보를 참조하여 다른 페이지를 추출합니다"
                    )

                    # 기준 페이지 번호 추출
                    reference_page_num = None
                    if selected_reference != "없음":
                        try:
                            reference_page_num = int(selected_reference.replace("페이지 ", ""))
                            st.session_state.answer_editor_reference_page = reference_page_num
                        except:
                            pass
                    else:
                        if "answer_editor_reference_page" in st.session_state:
                            del st.session_state.answer_editor_reference_page

                with col_ref2:
                    if reference_page_num:
                        reference_page_info = next((p for p in pdf_info["pages"] if p["page_num"] == reference_page_num), None)
                        if reference_page_info and os.path.exists(reference_page_info["answer_json_path"]):
                            with open(reference_page_info["answer_json_path"], "r", encoding="utf-8") as f:
                                ref_json = json.load(f)
                            st.success(f"✅ 기준 페이지 {reference_page_num}의 JSON 정보를 참조합니다 ({len(ref_json.get('items', []))}개 items)")
                        else:
                            st.warning(f"⚠️ 기준 페이지 {reference_page_num}의 JSON 파일이 없습니다")
                    else:
                        st.info("기준 페이지를 선택하지 않으면 각 페이지를 독립적으로 추출합니다")

                st.divider()

                col_btn1, col_btn2, col_btn3, col_btn4 = st.columns([2, 1, 2, 1])
                with col_btn1:
                    if st.button("🤖 RAG 기반 전체 페이지 정답 생성", type="primary", key="rag_batch_extract"):
                        # 분석 시작 시간 기록
                        start_time = time.time()
                        
                        st.session_state["_answer_editor_page_backup"] = st.session_state.get("answer_editor_selected_page", 1)
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        success_count = 0
                        error_count = 0

                        # 기준 페이지 JSON 로드
                        reference_json = None
                        if reference_page_num:
                            reference_page_info = next((p for p in pdf_info["pages"] if p["page_num"] == reference_page_num), None)
                            if reference_page_info and os.path.exists(reference_page_info["answer_json_path"]):
                                with open(reference_page_info["answer_json_path"], "r", encoding="utf-8") as f:
                                    reference_json = json.load(f)
                                status_text.text(f"기준 페이지 {reference_page_num}의 JSON 정보를 로드했습니다")

                        # PDF 경로 찾기 (양식 폴더 고려)
                        pdf_path = find_pdf_path_with_form(img_dir, selected_pdf, selected_form)
                        if not pdf_path or not pdf_path.exists():
                            st.error(f"❌ PDF 파일을 찾을 수 없습니다: {selected_pdf}")
                        else:
                            # 병렬 처리할 페이지 목록 준비 (기준 페이지 제외)
                            pages_to_process = [
                                p for p in pages_with_ocr 
                                if not (reference_page_num and p["page_num"] == reference_page_num)
                            ]
                            
                            # 기준 페이지는 건너뛰기 처리
                            if reference_page_num:
                                skipped_page = next((p for p in pages_with_ocr if p["page_num"] == reference_page_num), None)
                                if skipped_page:
                                    success_count += 1
                            
                            # 병렬 처리 실행
                            max_workers = min(10, len(pages_to_process))  # 최대 10개 스레드
                            completed_count = 0
                            
                            status_text.text(f"🚀 병렬 처리 시작 (최대 {max_workers}개 동시 실행)...")
                            
                            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                                # 모든 작업 제출
                                future_to_page = {
                                    executor.submit(
                                        process_single_page,
                                        page_info,
                                        pdf_path,
                                        reference_json,
                                        reference_page_num,
                                        total_pages,
                                        st.session_state.answer_editor_version  # 정답지 버전 전달
                                    ): page_info
                                    for page_info in pages_to_process
                                }
                                
                                # 완료된 작업 처리
                                for future in as_completed(future_to_page):
                                    page_info = future_to_page[future]
                                    completed_count += 1
                                    
                                    try:
                                        page_num, success, message = future.result()
                                        if success:
                                            success_count += 1
                                        else:
                                            error_count += 1
                                        
                                        # 진행 상황 업데이트 (경과 시간 포함)
                                        elapsed_time = time.time() - start_time
                                        status_text.text(f"진행 중... ({completed_count}/{len(pages_to_process)}) - {message} [경과: {elapsed_time:.1f}초]")
                                        progress_bar.progress(completed_count / len(pages_to_process))
                                        
                                    except Exception as e:
                                        error_count += 1
                                        page_num = page_info["page_num"]
                                        elapsed_time = time.time() - start_time
                                        status_text.text(f"페이지 {page_num}: 예외 발생 - {str(e)} [경과: {elapsed_time:.1f}초]")
                            
                            # 분석 종료 시간 기록 및 총 소요 시간 계산
                            end_time = time.time()
                            total_duration = end_time - start_time
                            
                            progress_bar.empty()
                            status_text.empty()
                            ref_msg = f" (기준 페이지 {reference_page_num} 참조)" if reference_json else " (RAG 기반)"
                            
                            # 소요 시간 포맷팅 (초 단위, 분:초 형식으로도 표시)
                            if total_duration < 60:
                                duration_msg = f"{total_duration:.1f}초"
                            else:
                                minutes = int(total_duration // 60)
                                seconds = total_duration % 60
                                duration_msg = f"{minutes}분 {seconds:.1f}초 ({total_duration:.1f}초)"
                            
                            st.success(f"✅ 전체 {success_count}개 페이지 정답 JSON 생성 완료!{ref_msg} ⏱️ 소요 시간: {duration_msg}")
                            if error_count > 0:
                                st.warning(f"⚠️ {error_count}개 페이지 처리 실패")
                            # 탭 상태 유지
                            if "active_tab" not in st.session_state:
                                st.session_state.active_tab = "✏️ 정답지 편집"
                            st.rerun()
                with col_btn2:
                    st.caption(f"총 {len(pages_with_ocr)}개 페이지")
                with col_btn3:
                    if reference_page_num:
                        st.caption(f"기준 페이지 {reference_page_num}의 JSON 정보를 참조하여 추출합니다")
                    else:
                        st.caption("RAG로 유사 예제를 찾아서 LLM으로 JSON 변환합니다")
                
                with col_btn4:
                    if st.button("🔍 전체 벡터 DB 저장", key="save_all_rag", 
                               help="모든 페이지의 OCR 텍스트와 정답 JSON을 벡터 DB에 저장"):
                        try:
                            rag_manager = get_rag_manager()
                            saved_count = 0
                            skipped_count = 0
                            
                            with st.spinner("벡터 DB에 저장 중..."):
                                # PDF 경로 찾기
                                pdf_path = img_dir / selected_pdf / f"{selected_pdf}.pdf"
                                if not pdf_path.exists():
                                    # 세션 디렉토리에서도 찾기
                                    session_pdf_path = find_pdf_path(selected_pdf)
                                    if session_pdf_path:
                                        pdf_path = Path(session_pdf_path)
                                
                                for page_info in pdf_info["pages"]:
                                    page_num = page_info["page_num"]
                                    answer_json_path = page_info.get("answer_json_path", "")
                                    
                                    if not os.path.exists(answer_json_path):
                                        skipped_count += 1
                                        continue
                                    
                                    # fitz를 사용하여 PDF에서 텍스트 추출
                                    ocr_text = extract_text_from_pdf_page(pdf_path, page_num) if pdf_path.exists() else ""
                                    
                                    if not ocr_text.strip():
                                        skipped_count += 1
                                        continue
                                    
                                    try:
                                        with open(answer_json_path, "r", encoding="utf-8") as f:
                                            loaded_json = json.load(f)
                                            # 전체 JSON 저장 (필터링하지 않음)
                                            answer_json = loaded_json
                                        
                                        rag_manager.add_example(
                                            ocr_text=ocr_text,
                                            answer_json=answer_json,
                                            metadata={
                                                "pdf_name": selected_pdf,
                                                "page_num": page_num,
                                                "page_role": answer_json.get("page_role", "detail")
                                            }
                                        )
                                        saved_count += 1
                                    except PermissionError as e:
                                        skipped_count += 1
                                        st.warning(f"⚠️ 페이지 {page_num} 저장 실패 (권한 문제): {e}")
                                    except Exception as e:
                                        skipped_count += 1
                                        error_msg = str(e)
                                        if "readonly" in error_msg.lower():
                                            st.warning(f"⚠️ 페이지 {page_num} 저장 실패 (읽기 전용): {error_msg}")
                                        else:
                                            st.warning(f"⚠️ 페이지 {page_num} 저장 실패: {error_msg}")
                            
                            if saved_count > 0:
                                st.success(f"✅ 벡터 DB 저장 완료! (저장: {saved_count}개, 건너뜀: {skipped_count}개)")
                                st.caption(f"총 예제 수: {rag_manager.count_examples()}개")
                            else:
                                st.error(f"❌ 저장 실패: 모든 페이지 저장에 실패했습니다 (건너뜀: {skipped_count}개)")
                                st.info("💡 해결 방법:\n"
                                       "1. `chmod -R 755 chroma_db` 명령어로 권한 수정\n"
                                       "2. 또는 `chroma_db` 디렉토리를 삭제하고 다시 시도")
                        except PermissionError as e:
                            st.error(f"❌ 벡터 DB 저장 실패 (권한 문제): {e}")
                            st.info("💡 해결 방법: 터미널에서 다음 명령어를 실행하세요:\n"
                                   f"`chmod -R 755 chroma_db` 또는 `sudo chmod -R 755 chroma_db`")
                        except Exception as e:
                            error_msg = str(e)
                            if "readonly" in error_msg.lower():
                                st.error(f"❌ 벡터 DB 저장 실패 (읽기 전용 오류): {error_msg}")
                                st.info("💡 해결 방법:\n"
                                       "1. `chmod -R 755 chroma_db` 명령어로 권한 수정\n"
                                       "2. 또는 `chroma_db` 디렉토리를 삭제하고 다시 시도")
                            else:
                                st.error(f"❌ 벡터 DB 저장 실패: {error_msg}")
                                with st.expander("상세 오류 정보"):
                                    st.code(traceback.format_exc())

            st.divider()

            if "_answer_editor_page_backup" in st.session_state:
                st.session_state.answer_editor_selected_page = st.session_state["_answer_editor_page_backup"]
                del st.session_state["_answer_editor_page_backup"]

            current_page = st.session_state.get("answer_editor_selected_page", 1)
            current_page = max(1, min(current_page, total_pages))
            st.session_state.answer_editor_selected_page = current_page

            col1, col2, col3, col4 = st.columns([1, 1, 1, 2])
            with col1:
                if st.button("◀ 이전", disabled=(current_page <= 1)):
                    st.session_state.answer_editor_selected_page -= 1
                    # 탭 상태 유지
                    if "active_tab" not in st.session_state:
                        st.session_state.active_tab = "✏️ 정답지 편집"
                    st.rerun()
            with col2:
                if st.button("다음 ▶", disabled=(current_page >= total_pages)):
                    st.session_state.answer_editor_selected_page += 1
                    # 탭 상태 유지
                    if "active_tab" not in st.session_state:
                        st.session_state.active_tab = "✏️ 정답지 편집"
                    st.rerun()
            with col3:
                st.text(f"페이지 {current_page}/{total_pages}")
            with col4:
                page_selector = st.selectbox(
                    "페이지 선택",
                    options=list(range(1, total_pages + 1)),
                    index=current_page - 1,
                    key="answer_editor_selected_page"
                )

            col1, col2 = st.columns([1, 1])

            page_info = pdf_info["pages"][current_page - 1]
            # with col1:
                # PDF 원본 이미지 표시 주석처리
                # with st.expander("..."):
                #     if os.path.exists(page_info["image_path"]):
                #         st.image(page_info["image_path"], caption=f"Page {current_page}", width='stretch')

                # Gemini Extractor 기능 삭제됨
                # OpenAI 질문 기능 및 RAG 기반 정답 생성은 JSON 편집창 아래로 이동됨
                # pass

        # with col2:
            # 설정에 따라 텍스트 추출 방법 표시
            from modules.utils.config import get_rag_config
            config = get_rag_config()
            extraction_method = getattr(config, 'text_extraction_method', 'pymupdf')
            
            if extraction_method == "excel":
                method_label = "엑셀 변환"
                method_icon = "📊"
            else:
                method_label = "PyMuPDF"
                method_icon = "📄"
            

            # JSON 파일 로드 (Gemini 결과 > RAG 결과 > 파일 순으로 우선 사용)
            answer_json_path = page_info["answer_json_path"]
            default_answer_json = {
                "page_role": "detail",
                "items": []
            }
            
            # RAG 결과가 있으면 우선 사용, 없으면 파일에서 로드
            if f"rag_result_{current_page}" in st.session_state:
                default_answer_json = st.session_state[f"rag_result_{current_page}"]
            elif os.path.exists(answer_json_path):
                try:
                    with open(answer_json_path, "r", encoding="utf-8") as f:
                        loaded_json = json.load(f)
                        # 전체 JSON 로드 (필터링하지 않음)
                        default_answer_json = loaded_json
                except Exception as e:
                    st.warning(f"기존 정답 JSON 로드 실패: {e}")

            # JSON 편집 expander
            with st.expander("📝 JSON 편집", expanded=True):
                # 전체 JSON 로드 (필터링하지 않음)
                full_answer_json = {}
                if f"answer_json_{current_page}" in st.session_state:
                    try:
                        full_answer_json = json.loads(st.session_state[f"answer_json_{current_page}"])
                    except json.JSONDecodeError:
                        full_answer_json = default_answer_json
                else:
                    # 파일에서 전체 JSON 로드 시도
                    if os.path.exists(answer_json_path):
                        try:
                            with open(answer_json_path, "r", encoding="utf-8") as f:
                                full_answer_json = json.load(f)
                        except Exception:
                            full_answer_json = default_answer_json
                    else:
                        full_answer_json = default_answer_json
                
                # 상위 키 목록 추출 (page_role 제외)
                top_level_keys = [k for k in full_answer_json.keys() if k != "page_role"]
                
                if not top_level_keys:
                    st.info("JSON에 편집 가능한 키가 없습니다.")
                else:
                    # 탭 생성
                    tabs = st.tabs([f"📋 {key}" for key in top_level_keys])
                    
                    # 각 탭에 대해 데이터프레임 표시
                    for idx, key in enumerate(top_level_keys):
                        with tabs[idx]:
                            value = full_answer_json.get(key)
                            
                            if isinstance(value, dict):
                                # 딕셔너리인 경우 평탄화하여 데이터프레임으로 표시
                                flattened = flatten_dict(value)
                                if flattened:
                                    df = pd.DataFrame([flattened]).T
                                    edited_df = st.data_editor(
                                        df,
                                        height=400,
                                        key=f"json_editor_{current_page}_{key}",
                                        use_container_width=True
                                    )
                                    # 수정된 데이터를 다시 딕셔너리로 변환 (간단한 역변환은 어려우므로 전체 JSON 업데이트는 저장 시 수행)
                                    st.session_state[f"json_data_{current_page}_{key}"] = edited_df.to_dict('records')[0] if len(edited_df) > 0 else {}
                                else:
                                    st.info(f"'{key}' 키의 값이 비어있습니다.")
                            elif isinstance(value, list):
                                # 리스트인 경우 (items 등)
                                if not value:
                                    # 빈 리스트인 경우
                                    st.info(f"'{key}' 키의 값이 비어있습니다.")
                                    st.session_state[f"json_data_{current_page}_{key}"] = []
                                elif isinstance(value[0], dict):
                                    # 딕셔너리 리스트인 경우
                                    if key == "items":
                                        # items인 경우 AgGrid 사용 (색상 구분 포함)
                                        if not AgGridUtils.is_available():
                                            st.warning("⚠️ AgGrid가 설치되어 있지 않습니다. `pip install streamlit-aggrid`로 설치하세요.")
                                            df = pd.DataFrame(value)
                                            edited_df = st.data_editor(
                                                df,
                                                height=400,
                                                key=f"json_editor_{current_page}_{key}",
                                                use_container_width=True
                                            )
                                            st.session_state[f"json_data_{current_page}_{key}"] = edited_df.to_dict('records')
                                        else:
                                            df, mgmt_col = prepare_dataframe_for_aggrid(value)
                                            
                                            # GridOptionsBuilder 설정
                                            if len(df) == 0 or len(df.columns) == 0:
                                                st.warning(f"⚠️ DataFrame을 생성할 수 없습니다. (items 개수: {len(value)})")
                                                st.session_state[f"json_data_{current_page}_{key}"] = value
                                            else:
                                                # 색상 그룹핑 기준 컬럼 선택
                                                color_grouping_key = f"color_grouping_col_{current_page}_{key}"
                                                available_cols = [col for col in df.columns if col != 'No']
                                                
                                                # 기본값 설정 (기존 mgmt_col 또는 첫 번째 컬럼)
                                                default_col = mgmt_col if mgmt_col and mgmt_col in available_cols else (available_cols[0] if available_cols else None)
                                                
                                                if default_col:
                                                    selected_col = st.selectbox(
                                                        "색상 그룹핑 기준 컬럼",
                                                        options=["없음"] + available_cols,
                                                        index=available_cols.index(default_col) + 1 if default_col in available_cols else 0,
                                                        key=color_grouping_key,
                                                        help="선택한 컬럼의 값이 같은 행들은 같은 색상으로 표시됩니다."
                                                    )
                                                    
                                                    # 선택한 컬럼으로 색상 그룹핑 (없음 선택 시 None)
                                                    grouping_col = None if selected_col == "없음" else selected_col
                                                else:
                                                    grouping_col = None
                                                
                                                gb = GridOptionsBuilder.from_dataframe(df)
                                                gb.configure_default_column(editable=True, resizable=True)
                                                
                                                # 각 컬럼 설정
                                                for col in df.columns:
                                                    if col == 'No':
                                                        gb.configure_column(col, header_name=col, editable=False, width=60, pinned='left')
                                                    else:
                                                        gb.configure_column(col, header_name=col)
                                                
                                                gb.configure_pagination(enabled=False)
                                                
                                                # 선택한 컬럼 기준으로 색상 지정
                                                get_row_style_code = create_management_color_style(grouping_col, df)
                                                grid_options = gb.build()
                                                if get_row_style_code:
                                                    grid_options['getRowStyle'] = get_row_style_code
                                                grid_options['pagination'] = False
                                                
                                                auto_size_js = JsCode("""
                                                function(params) {
                                                    params.api.sizeColumnsToFit();
                                                    var allColumnIds = [];
                                                    params.columnApi.getColumns().forEach(function(column) {
                                                        if (column.colId) allColumnIds.push(column.colId);
                                                    });
                                                    params.columnApi.autoSizeColumns(allColumnIds);
                                                }
                                                """)
                                                grid_options['onGridReady'] = auto_size_js
                                                
                                                # AG Grid 렌더링
                                                grid_response = AgGrid(
                                                    df,
                                                    gridOptions=grid_options,
                                                    update_mode=GridUpdateMode.VALUE_CHANGED,
                                                    data_return_mode=DataReturnMode.FILTERED_AND_SORTED,
                                                    fit_columns_on_grid_load=True,
                                                    height=400,
                                                    theme='streamlit',
                                                    allow_unsafe_jscode=True,
                                                    hide_index=False,
                                                    key=f"aggrid_json_{current_page}_{key}"
                                                )
                                                
                                                # 수정된 데이터 가져오기
                                                updated_df = grid_response['data'] if grid_response.get('data') is not None else pd.DataFrame()
                                                if len(updated_df) > 0 and 'No' in updated_df.columns:
                                                    updated_df = updated_df.drop(columns=['No'])
                                                st.session_state[f"json_data_{current_page}_{key}"] = updated_df.to_dict('records') if len(updated_df) > 0 else value
                                    else:
                                        # items가 아닌 다른 리스트는 일반 데이터프레임 편집기 사용
                                        df = pd.DataFrame(value)
                                        edited_df = st.data_editor(
                                            df,
                                            height=400,
                                            key=f"json_editor_{current_page}_{key}",
                                            use_container_width=True
                                        )
                                        st.session_state[f"json_data_{current_page}_{key}"] = edited_df.to_dict('records')
                                else:
                                    # 단순 리스트인 경우
                                    st.json(value)
                                    st.info("리스트는 JSON 형식으로만 표시됩니다. 편집은 JSON 텍스트 영역에서 가능합니다.")
                            else:
                                # 단순 값인 경우
                                st.text_input(
                                    f"{key} 값",
                                    value=str(value) if value is not None else "",
                                    key=f"json_editor_{current_page}_{key}"
                                )
                                st.session_state[f"json_data_{current_page}_{key}"] = st.session_state.get(f"json_editor_{current_page}_{key}", str(value) if value is not None else "")
                    
                    st.divider()
                    
                    # 전체 JSON 텍스트 편집 영역 (참고용)
                    st.caption("💡 전체 JSON 텍스트 (참고용)")
                    answer_json_str_default = json.dumps(full_answer_json, ensure_ascii=False, indent=2)
                    answer_json_str = st.text_area(
                        "정답 JSON (전체)",
                        value=answer_json_str_default,
                        height=200,
                        key=f"answer_json_{current_page}"
                    )
                    
                    # JSON 파싱 오류 처리
                    try:
                        parsed_json = json.loads(answer_json_str)
                        # 파싱 성공 (위젯이 이미 세션 상태를 관리하므로 별도 업데이트 불필요)
                    except json.JSONDecodeError as e:
                        st.error(f"❌ JSON 파싱 오류: {e}")
                        
                    if page_info.get("ocr_text"):
                        st.text_area(
                            f"{method_label} OCR 결과",
                            value=page_info["ocr_text"],
                            height=200,
                            key=f"ocr_text_{current_page}",
                            disabled=True
                        )
                    else:
                        st.warning(f"{method_label} 추출 결과가 없습니다.")

            # OpenAI 질문 기능 및 RAG 기반 정답 생성 (JSON 편집창 아래로 이동)
            with st.expander("🤖 OpenAI 질문 기능 및 RAG 기반 정답 생성", expanded=False):
                    # JSON 파일 업로더
                    uploaded_json_file = st.file_uploader(
                        "참조용 정답 JSON 파일 업로드",
                        type=['json'],
                        key=f"reference_json_uploader_{current_page}",
                        help="참조용 정답 JSON 파일을 업로드하세요. 이 파일과 현재 페이지의 TXT 파일을 사용하여 OpenAI에 질문합니다."
                    )

                    # 업로드된 JSON 파일 로드
                    reference_json = None
                    if uploaded_json_file:
                        try:
                            reference_json = json.load(uploaded_json_file)
                            st.success(f"✅ JSON 파일 로드 완료: {uploaded_json_file.name}")
                        except Exception as e:
                            st.error(f"❌ JSON 파일 로드 실패: {e}")

                    # RAG 검색 및 모델 설정 섹션
                    question_disabled = not page_info.get("ocr_text")
                    
                    # 모델 선택 옵션
                    config = get_rag_config()
                    available_models = [
                        "gpt-4o-2024-11-20",
                        "gpt-4.1-2025-04-14",
                        "gpt-5-nano-2025-08-07",
                        "gpt-5-mini-2025-08-07",
                        "gpt-5.2-2025-12-11"
                    ]
                    selected_model = st.selectbox(
                        "🤖 사용할 모델 선택",
                        options=available_models,
                        index=0 if config.openai_model in available_models else 0,
                        key=f"model_selector_{current_page}",
                        help="RAG 기반 정답 생성에 사용할 OpenAI 모델을 선택하세요."
                    )
                    
                    # RAG 검색 버튼 (검색 결과 미리보기)
                    if st.button(
                        "🔍 RAG 검색 (참고 문서 확인)",
                        disabled=question_disabled,
                        key=f"search_rag_{current_page}"
                    ):
                        if not page_info.get("ocr_text"):
                            st.error("❌ 현재 페이지의 OCR 텍스트가 없습니다.")
                        else:
                            with st.spinner("RAG 검색 중..."):
                                try:
                                    # PDF 경로 찾기
                                    pdf_path = img_dir / selected_pdf / f"{selected_pdf}.pdf"
                                    if not pdf_path.exists():
                                        session_pdf_path = find_pdf_path(selected_pdf)
                                        if session_pdf_path:
                                            pdf_path = Path(session_pdf_path)
                                    
                                    # PyMuPDF로 텍스트 추출
                                    ocr_text = page_info.get("ocr_text", "")
                                    if not ocr_text and pdf_path.exists():
                                        ocr_text = extract_text_from_pdf_page(pdf_path, current_page)
                                    
                                    if not ocr_text:
                                        st.error("❌ OCR 텍스트를 추출할 수 없습니다.")
                                    else:
                                        # RAG Manager로 검색만 수행
                                        rag_manager = get_rag_manager()
                                        similar_examples = rag_manager.search_similar_advanced(
                                            query_text=ocr_text,
                                            top_k=config.top_k,
                                            similarity_threshold=config.similarity_threshold,
                                            search_method=config.search_method,
                                            hybrid_alpha=config.hybrid_alpha
                                        )
                                        
                                        # 검색 결과가 없으면 threshold를 낮춰서 재검색
                                        if not similar_examples:
                                            similar_examples = rag_manager.search_similar_advanced(
                                                query_text=ocr_text,
                                                top_k=1,
                                                similarity_threshold=0.0,
                                                search_method=config.search_method,
                                                hybrid_alpha=config.hybrid_alpha
                                            )
                                        
                                        # 검색 결과를 세션 상태에 저장
                                        st.session_state[f"rag_search_results_{current_page}"] = {
                                            "similar_examples": similar_examples,
                                            "ocr_text": ocr_text
                                        }
                                        st.success(f"✅ RAG 검색 완료: {len(similar_examples)}개 예제 발견")
                                        
                                except Exception as e:
                                    st.error(f"❌ RAG 검색 실패: {e}")
                                    st.code(traceback.format_exc())
                
                    # 검색 결과 표시 및 예제 선택
                    if f"rag_search_results_{current_page}" in st.session_state:
                        search_results = st.session_state[f"rag_search_results_{current_page}"]
                        similar_examples = search_results["similar_examples"]
                        
                        if similar_examples:
                            st.subheader("📚 검색된 참고 문서")
                            
                            # 예제 선택 옵션 생성
                            example_options = []
                            rag_manager = get_rag_manager()
                            for idx, ex in enumerate(similar_examples):
                                # 점수 정보 수집
                                score_info = []
                                if 'hybrid_score' in ex:
                                    score_info.append(f"Hybrid: {ex['hybrid_score']:.4f}")
                                if 'bm25_score' in ex:
                                    score_info.append(f"BM25: {ex['bm25_score']:.4f}")
                                score_info.append(f"Similarity: {ex['similarity']:.4f}")
                            
                            # 메타데이터에서 PDF 정보 추출
                            pdf_name = "Unknown"
                            page_num = "Unknown"
                            if 'id' in ex:
                                doc_id = ex['id']
                                all_examples = rag_manager.get_all_examples()
                                for example in all_examples:
                                    if example['id'] == doc_id:
                                        metadata = example.get('metadata', {})
                                        pdf_name = metadata.get('pdf_name', 'Unknown')
                                        page_num = metadata.get('page_num', 'Unknown')
                                        break
                            
                            example_label = f"[{idx+1}] {pdf_name} - Page{page_num} ({', '.join(score_info)})"
                            example_options.append((idx, example_label, ex))
                        
                            # 예제 선택 드롭다운
                            selected_example_idx = st.selectbox(
                            "📌 사용할 참고 예제 선택",
                            options=[opt[0] for opt in example_options],
                            format_func=lambda x: example_options[x][1],
                            key=f"example_selector_{current_page}",
                            help="검색된 예제 중 하나를 선택하여 RAG 정답 생성에 사용합니다."
                        )
                        
                            selected_example = example_options[selected_example_idx][2]
                            
                            # 선택된 예제 상세 정보 표시
                            with st.expander("📖 선택된 예제 상세 정보", expanded=True):
                                col_info1, col_info2 = st.columns(2)
                            with col_info1:
                                st.write("**점수 정보:**")
                                if 'hybrid_score' in selected_example:
                                    st.write(f"- Hybrid Score: {selected_example['hybrid_score']:.4f}")
                                if 'bm25_score' in selected_example:
                                    st.write(f"- BM25 Score: {selected_example['bm25_score']:.4f}")
                                st.write(f"- Similarity: {selected_example['similarity']:.4f}")
                            
                            with col_info2:
                                st.write("**문서 정보:**")
                                if 'id' in selected_example:
                                    doc_id = selected_example['id']
                                    all_examples = rag_manager.get_all_examples()
                                    for example in all_examples:
                                        if example['id'] == doc_id:
                                            metadata = example.get('metadata', {})
                                            st.write(f"- PDF: {metadata.get('pdf_name', 'Unknown')}")
                                            st.write(f"- Page: {metadata.get('page_num', 'Unknown')}")
                                            st.write(f"- Role: {selected_example['answer_json'].get('page_role', 'N/A')}")
                                            break
                            
                            st.write("**OCR 텍스트 미리보기:**")
                            ocr_preview = selected_example['ocr_text'][:500] + "..." if len(selected_example['ocr_text']) > 500 else selected_example['ocr_text']
                            st.text_area(
                                "참고 예제 OCR 텍스트",
                                value=ocr_preview,
                                height=150,
                                key=f"example_ocr_preview_{current_page}",
                                disabled=True
                            )
                            
                            st.write("**정답 JSON 미리보기:**")
                            example_answer_str = json.dumps(selected_example['answer_json'], ensure_ascii=False, indent=2)
                            st.code(example_answer_str[:1000] + "..." if len(example_answer_str) > 1000 else example_answer_str, language='json')
                        
                            # 정답 생성 버튼
                            if st.button(
                                "🚀 선택한 예제로 정답 생성",
                                type="primary",
                                key=f"generate_with_selected_{current_page}"
                            ):
                                with st.spinner("LLM 호출 중..."):
                                    try:
                                        ocr_text = search_results["ocr_text"]
                                        
                                        # 선택된 예제를 사용하여 RAG 추출
                                        def progress_wrapper(msg: str):
                                            st.info(f"🤖 {msg}")
                                        
                                        # 선택된 예제를 직접 사용하여 프롬프트 생성
                                        project_root = get_project_root()
                                        prompts_dir = project_root / "prompts"
                                        
                                        example_ocr = selected_example["ocr_text"]
                                        example_answer = selected_example["answer_json"]
                                        example_answer_str = json.dumps(example_answer, ensure_ascii=False, indent=2)
                                        
                                        # 프롬프트 템플릿 로드 (필수)
                                        prompt_template_path = prompts_dir / "rag_with_example.txt"
                                        if not prompt_template_path.exists():
                                            raise FileNotFoundError(
                                                f"프롬프트 파일이 없습니다: {prompt_template_path}\n"
                                                f"prompts/rag_with_example.txt 파일이 필요합니다."
                                            )
                                        
                                        with open(prompt_template_path, 'r', encoding='utf-8') as f:
                                            prompt_template = f.read()
                                        prompt = prompt_template.format(
                                            example_ocr=example_ocr,
                                            example_answer_str=example_answer_str,
                                            ocr_text=ocr_text
                                        )
                                        
                                        # 프롬프트를 세션 상태에 저장 (확인용)
                                        st.session_state[f"last_prompt_{current_page}"] = prompt
                                        
                                        # 프롬프트 미리보기 표시
                                        with st.expander("📝 사용된 프롬프트 확인", expanded=True):
                                            st.text_area(
                                                "최종 프롬프트",
                                                value=prompt,
                                                height=400,
                                                key=f"prompt_preview_{current_page}",
                                                disabled=True,
                                                help="OpenAI API에 전송되는 최종 프롬프트입니다."
                                            )
                                        
                                        # OpenAI API 호출
                                        api_key = os.getenv("OPENAI_API_KEY")
                                        if not api_key:
                                            raise ValueError("OPENAI_API_KEY가 필요합니다.")
                                        
                                        client = OpenAI(api_key=api_key)
                                        response = client.chat.completions.create(
                                            model=selected_model,
                                            messages=[{"role": "user", "content": prompt}],
                                            temperature=0.0,
                                            timeout=120
                                        )
                                        result_text = response.choices[0].message.content
                                        
                                        # JSON 파싱
                                        result_text = result_text.strip()
                                        if result_text.startswith('```'):
                                            result_text = result_text.split('```', 1)[1]
                                            if result_text.startswith('json'):
                                                result_text = result_text[4:].strip()
                                            if result_text.endswith('```'):
                                                result_text = result_text.rsplit('```', 1)[0].strip()
                                        
                                        result_text = re.sub(r':\s*None\s*([,}])', r': null\1', result_text)
                                        result_text = re.sub(r':\s*True\s*([,}])', r': true\1', result_text)
                                        result_text = re.sub(r':\s*False\s*([,}])', r': false\1', result_text)
                                        
                                        result_json = json.loads(result_text)
                                        
                                        # null 값 정규화
                                        if result_json.get("items") is None:
                                            result_json["items"] = []
                                        if result_json.get("page_role") is None:
                                            result_json["page_role"] = "detail"
                                        if not isinstance(result_json.get("items"), list):
                                            result_json["items"] = []
                                        
                                        # 세션 상태에 저장 (정답 JSON 편집 영역에 바로 반영)
                                        st.session_state[f"rag_result_{current_page}"] = result_json
                                        st.session_state[f"answer_json_{current_page}"] = json.dumps(result_json, ensure_ascii=False, indent=2)
                                        st.session_state[f"page_role_{current_page}"] = result_json.get("page_role", "detail")
                                        st.success("✅ RAG 기반 정답 생성 완료! 아래 정답 JSON 편집 영역에서 확인하세요.")
                                        # 탭 상태 유지
                                        if "active_tab" not in st.session_state:
                                            st.session_state.active_tab = "✏️ 정답지 편집"
                                        st.rerun()
                                        
                                    except Exception as e:
                                        st.error(f"❌ 정답 생성 실패: {e}")
                                        st.code(traceback.format_exc())
                        else:
                            st.info("⚠️ 검색된 예제가 없습니다.")
                
                    # 기존 RAG 기반 정답 생성 버튼 (하위 호환성 유지)
                    if st.button(
                        "🔍 RAG 기반 정답 생성 (자동)",
                        disabled=question_disabled,
                        key=f"ask_rag_auto_{current_page}",
                        help="자동으로 최상위 검색 결과를 사용하여 정답을 생성합니다."
                    ):
                        if not page_info.get("ocr_text"):
                            st.error("❌ 현재 페이지의 OCR 텍스트가 없습니다.")
                        else:
                            with st.spinner("RAG 검색 및 LLM 호출 중..."):
                                try:
                                    # PDF 경로 찾기
                                    pdf_path = img_dir / selected_pdf / f"{selected_pdf}.pdf"
                                    if not pdf_path.exists():
                                        session_pdf_path = find_pdf_path(selected_pdf)
                                        if session_pdf_path:
                                            pdf_path = Path(session_pdf_path)
                                    
                                    # PyMuPDF로 텍스트 추출
                                    ocr_text = page_info.get("ocr_text", "")
                                    if not ocr_text and pdf_path.exists():
                                        ocr_text = extract_text_from_pdf_page(pdf_path, current_page)
                                    
                                    if not ocr_text:
                                        st.error("❌ OCR 텍스트를 추출할 수 없습니다.")
                                    else:
                                        # RAG 기반 JSON 추출
                                        def progress_wrapper(msg: str):
                                            st.info(f"🤖 {msg}")
                                        
                                        result_json = extract_json_with_rag(
                                            ocr_text=ocr_text,
                                            question=None,
                                            model_name=selected_model,  # 선택된 모델 사용
                                            temperature=0.0,
                                            top_k=None,
                                            prompt_version=st.session_state.answer_editor_version,  # 정답지 버전에 따라 프롬프트 파일 선택
                                            similarity_threshold=None,
                                            progress_callback=progress_wrapper,
                                            page_num=current_page
                                        )
                                        
                                        # 프롬프트 파일에서 읽기 (디버깅 폴더에 저장된 경우)
                                        project_root = get_project_root()
                                        debug_dir = project_root / "debug"
                                        prompt_file = debug_dir / f"page_{current_page}_prompt.txt"
                                        if prompt_file.exists():
                                            with open(prompt_file, 'r', encoding='utf-8') as f:
                                                saved_prompt = f.read()
                                            st.session_state[f"last_prompt_{current_page}"] = saved_prompt
                                            
                                            # 프롬프트 미리보기 표시
                                            with st.expander("📝 사용된 프롬프트 확인", expanded=True):
                                                st.text_area(
                                                    "최종 프롬프트",
                                                    value=saved_prompt,
                                                    height=400,
                                                    key=f"prompt_preview_auto_{current_page}",
                                                    disabled=True,
                                                    help="OpenAI API에 전송되는 최종 프롬프트입니다."
                                                )

                                        # null 값 정규화
                                        if result_json.get("items") is None:
                                            result_json["items"] = []
                                        if result_json.get("page_role") is None:
                                            result_json["page_role"] = "detail"
                                        if not isinstance(result_json.get("items"), list):
                                            result_json["items"] = []
                                        
                                        # 세션 상태에 저장 (정답 JSON 편집 영역에 바로 반영)
                                        st.session_state[f"rag_result_{current_page}"] = result_json
                                        st.session_state[f"answer_json_{current_page}"] = json.dumps(result_json, ensure_ascii=False, indent=2)
                                        st.session_state[f"page_role_{current_page}"] = result_json.get("page_role", "detail")
                                        st.success("✅ RAG 기반 정답 생성 완료! 아래 정답 JSON 편집 영역에서 확인하세요.")
                                        # 탭 상태 유지
                                        if "active_tab" not in st.session_state:
                                            st.session_state.active_tab = "✏️ 정답지 편집"
                                        st.rerun()

                                except Exception as e:
                                    st.error(f"❌ OpenAI API 호출 실패: {e}")
                                    st.code(traceback.format_exc())

            # 저장 버튼 (expander 밖)
            col_save1, col_save2, col_save3 = st.columns([1, 1, 3])
            with col_save1:
                if st.button("💾 저장", type="primary", key=f"save_answer_{current_page}"):
                    # JSON 파싱 및 저장
                    try:
                        # 텍스트 영역의 JSON을 우선 사용
                        answer_json_str_for_save = st.session_state.get(f"answer_json_{current_page}", "{}")
                        
                        # JSON 파싱
                        try:
                            answer_json = json.loads(answer_json_str_for_save)
                        except json.JSONDecodeError:
                            # 파싱 실패 시 기본값 사용
                            answer_json = default_answer_json.copy()
                        
                        # page_role은 JSON에서 직접 읽어옴 (별도 UI 없음)
                        
                        # 탭에서 수정한 데이터 반영 (각 키별로, items 포함)
                        top_level_keys = [k for k in answer_json.keys() if k != "page_role"]
                        for key in top_level_keys:
                            if f"json_data_{current_page}_{key}" in st.session_state:
                                updated_data = st.session_state[f"json_data_{current_page}_{key}"]
                                
                                if isinstance(updated_data, dict):
                                    # 평탄화된 딕셔너리인 경우 복원
                                    if any(sep in k for k in updated_data.keys() for sep in ['_']):
                                        # 평탄화된 키가 있는 경우 복원 시도
                                        try:
                                            answer_json[key] = unflatten_dict(updated_data)
                                        except Exception:
                                            # 복원 실패 시 그대로 저장
                                            answer_json[key] = updated_data
                                    else:
                                        answer_json[key] = updated_data
                                elif isinstance(updated_data, list):
                                    answer_json[key] = updated_data
                                elif isinstance(updated_data, str):
                                    # 단순 문자열 값인 경우 타입 변환 시도
                                    try:
                                        # 숫자로 변환 가능한지 확인
                                        if updated_data.isdigit():
                                            answer_json[key] = int(updated_data)
                                        elif updated_data.replace('.', '', 1).isdigit():
                                            answer_json[key] = float(updated_data)
                                        else:
                                            answer_json[key] = updated_data
                                    except Exception:
                                        answer_json[key] = updated_data
                                else:
                                    answer_json[key] = updated_data

                        # 파일 저장 (전체 JSON 저장)
                        if not answer_json_path:
                            st.error(f"❌ 저장 경로가 없습니다. answer_json_path를 확인하세요.")
                        else:
                            os.makedirs(os.path.dirname(answer_json_path), exist_ok=True)
                            with open(answer_json_path, "w", encoding="utf-8") as f:
                                json.dump(answer_json, f, ensure_ascii=False, indent=2)

                            st.success(f"✅ 정답 JSON 저장 완료! (파일 크기: {os.path.getsize(answer_json_path)} bytes)")
                            st.caption(f"저장 경로: `{answer_json_path}`")
                            # 탭 상태 유지
                            if "active_tab" not in st.session_state:
                                st.session_state.active_tab = "✏️ 정답지 편집"
                            st.rerun()
                    except json.JSONDecodeError as e:
                        st.error(f"❌ JSON 파싱 오류: {e}")
                        st.code(traceback.format_exc())
                    except Exception as e:
                        st.error(f"❌ 저장 실패: {e}")
                        st.code(traceback.format_exc())
                
            with col_save2:
                # 벡터 DB 저장 버튼
                ocr_text = page_info.get("ocr_text", "")
                has_ocr = bool(ocr_text)
                try:
                    answer_json_str_for_check = st.session_state.get(f"answer_json_{current_page}", json.dumps(default_answer_json, ensure_ascii=False, indent=2))
                    parsed_json = json.loads(answer_json_str_for_check)
                    has_answer = bool(parsed_json)
                except (json.JSONDecodeError, NameError, KeyError):
                    has_answer = False
                
                if st.button("🔍 벡터 DB 저장", key=f"save_rag_{current_page}", 
                            disabled=not (has_ocr and has_answer),
                            help="OCR 텍스트와 정답 JSON을 벡터 DB에 저장합니다 (RAG 학습용)"):
                    try:
                        # JSON 파싱 (전체 JSON 사용)
                        answer_json_str_for_rag = st.session_state.get(f"answer_json_{current_page}", json.dumps(default_answer_json, ensure_ascii=False, indent=2))
                        parsed_json = json.loads(answer_json_str_for_rag)
                        
                        # page_role은 JSON에서 직접 읽어옴
                        page_role_for_rag = parsed_json.get("page_role", "detail")
                        
                        # 탭에서 수정한 데이터 반영 (각 키별로, items 포함)
                        top_level_keys = [k for k in parsed_json.keys() if k != "page_role"]
                        for key in top_level_keys:
                            if f"json_data_{current_page}_{key}" in st.session_state:
                                updated_data = st.session_state[f"json_data_{current_page}_{key}"]
                                
                                if isinstance(updated_data, dict):
                                    # 평탄화된 딕셔너리인 경우 복원
                                    if any('_' in k for k in updated_data.keys()):
                                        try:
                                            parsed_json[key] = unflatten_dict(updated_data)
                                        except Exception:
                                            parsed_json[key] = updated_data
                                    else:
                                        parsed_json[key] = updated_data
                                elif isinstance(updated_data, list):
                                    parsed_json[key] = updated_data
                                elif isinstance(updated_data, str):
                                    # 단순 문자열 값인 경우 타입 변환 시도
                                    try:
                                        if updated_data.isdigit():
                                            parsed_json[key] = int(updated_data)
                                        elif updated_data.replace('.', '', 1).isdigit():
                                            parsed_json[key] = float(updated_data)
                                        else:
                                            parsed_json[key] = updated_data
                                    except Exception:
                                        parsed_json[key] = updated_data
                                else:
                                    parsed_json[key] = updated_data
                        
                        # 전체 JSON 저장 (필터링하지 않음)
                        answer_json = parsed_json
                        
                        # RAG Manager로 저장
                        rag_manager = get_rag_manager()
                        doc_id = rag_manager.add_example(
                            ocr_text=ocr_text,
                            answer_json=answer_json,
                            metadata={
                                "pdf_name": selected_pdf,
                                "page_num": current_page,
                                "page_role": page_role_for_rag
                            }
                        )
                        
                        st.success(f"✅ 벡터 DB 저장 완료! (ID: {doc_id[:8]}...)")
                        st.caption(f"총 예제 수: {rag_manager.count_examples()}개")
                    except PermissionError as e:
                        st.error(f"❌ 벡터 DB 저장 실패 (권한 문제): {e}")
                        st.info("💡 해결 방법: 터미널에서 다음 명령어를 실행하세요:\n"
                                f"`chmod -R 755 chroma_db` 또는 `sudo chmod -R 755 chroma_db`")
                    except Exception as e:
                        error_msg = str(e)
                        if "readonly" in error_msg.lower():
                            st.error(f"❌ 벡터 DB 저장 실패 (읽기 전용 오류): {error_msg}")
                            st.info("💡 해결 방법:\n"
                                    "1. `chmod -R 755 chroma_db` 명령어로 권한 수정\n"
                                    "2. 또는 `chroma_db` 디렉토리를 삭제하고 다시 시도")
                        else:
                            st.error(f"❌ 벡터 DB 저장 실패: {error_msg}")
                            with st.expander("상세 오류 정보"):
                                st.code(traceback.format_exc())
            
            with col_save3:
                # 벡터 DB 통계 표시
                try:
                    rag_manager = get_rag_manager()
                    example_count = rag_manager.count_examples()
                    st.caption(f"벡터 DB 예제 수: {example_count}개")
                except Exception:
                    pass

    # 정답지와 비교 기능
        if f"openai_result_{current_page}" in st.session_state:
            st.divider()
            st.subheader("🔍 OpenAI 응답 vs 정답지 비교")
            st.caption("**비교 기준**: 각 행(항목)별로 동일한 인덱스의 OpenAI 응답과 정답지를 비교합니다. 주요 필드(관리번호, 상품명, 수량, 금액 등)의 일치 여부를 확인합니다.")

            openai_result = st.session_state[f"openai_result_{current_page}"]
            openai_items = openai_result.get("items", [])

            # 정답지 JSON 다시 로드
            answer_json_path = page_info["answer_json_path"]
            answer_items = []
            if os.path.exists(answer_json_path):
                with open(answer_json_path, "r", encoding="utf-8") as f:
                    loaded_json = json.load(f)
                    # 불필요한 필드 제거 후 items만 추출
                    filtered_json = filter_answer_json(loaded_json)
                    answer_items = filtered_json.get("items", [])

            if openai_items and answer_items:
                # 비교용 데이터프레임 생성 (함수 사용)
                comparison_df = create_comparison_dataframe(openai_items, answer_items)

                # AgGrid로 표시 (함수 사용)
                render_comparison_grid(comparison_df, current_page)
            elif not openai_items:
                st.info("OpenAI 응답 결과가 없습니다. 먼저 OpenAI에 질문하기를 실행하세요.")
            elif not answer_items:
                st.info("정답지 items가 없습니다.")
        else:
            st.caption("💡 OpenAI 응답 결과가 있으면 정답지와 자동으로 비교됩니다.")
    else:
        st.info("위에서 PDF 파일을 업로드하세요.", icon="👆")