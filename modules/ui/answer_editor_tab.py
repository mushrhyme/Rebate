"""
정답지 편집 탭 - fitz (PyMuPDF) 중심 구조
"""

import os
from pathlib import Path
import fitz
import streamlit as st
import json
from PIL import Image
import io

from src.upstage_extractor import UpstageExtractor
from modules.utils.openai_utils import ask_openai_with_reference
from src.openai_extractor import OpenAITextParser
from modules.ui.aggrid_utils import AgGridUtils
import pandas as pd
from modules.core.rag_manager import get_rag_manager
from modules.utils.config import get_project_root
from modules.utils.session_utils import ensure_session_state_defaults

# 컬럼명 일본어 매핑 (공통 상수)
COLUMN_NAME_MAPPING = {
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

# 컬럼 순서 정의 (공통 상수)
DESIRED_COLUMN_ORDER = [
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
    
    # No 컬럼 추가 (1부터 시작)
    df.insert(0, 'No', range(1, len(df) + 1))

    # 관리번호 컬럼 확인
    mgmt_col = 'management_id' if 'management_id' in df.columns else ('管理番号' if '管理番号' in df.columns else None)

    # 컬럼 순서 재정렬
    existing_cols = [col for col in DESIRED_COLUMN_ORDER if col in df.columns]
    remaining_cols = [col for col in df.columns if col not in existing_cols]
    df = df[existing_cols + remaining_cols]
    
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

    from st_aggrid import JsCode
    import json

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

    key_fields = [f for f in DESIRED_COLUMN_ORDER if f != 'No']
    all_cols = set([col for col in openai_df.columns if col != 'No'] + [col for col in answer_df.columns if col != 'No'])
    ordered_cols = [f for f in key_fields if f in all_cols] + sorted(all_cols - set(key_fields))

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
                      for f in key_fields if f in openai_df.columns and f in answer_df.columns]
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

    from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode, JsCode

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
            japanese_name = COLUMN_NAME_MAPPING.get(original_col, original_col)
            gb.configure_column(col, header_name=f"응답: {japanese_name}")
        elif col.startswith("정답_"):
            original_col = col.replace("정답_", "")
            japanese_name = COLUMN_NAME_MAPPING.get(original_col, original_col)
            gb.configure_column(col, header_name=f"정답: {japanese_name}")
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

    st.info(
        "**📌 정답지 편집 가이드**:\n\n"
        "• PDF 파일을 업로드하면 자동으로 이미지로 변환되고 Upstage로 텍스트를 추출합니다\n\n"
        "• 각 페이지별로 원문 텍스트, Upstage 추출 결과, 정답 JSON을 편집할 수 있습니다\n\n"
        "• 정답 JSON은 RAG 학습용 정답지로 사용됩니다",
        icon="ℹ️"
    )

    # 기존 처리된 PDF 목록 확인
    project_root = get_project_root()
    img_dir = project_root / "img"
    existing_pdfs = []
    if img_dir.exists():
        for item in img_dir.iterdir():
            if item.is_dir():
                if (item / "Page1.png").exists():
                    existing_pdfs.append(item.name)
    
    # 여러 PDF 일괄 벡터 DB 저장 섹션
    with st.expander("🔍 여러 PDF 일괄 벡터 DB 저장", expanded=False):
        st.info("여러 PDF 파일의 모든 페이지를 한 번에 벡터 DB에 저장할 수 있습니다.")
        
        if not existing_pdfs:
            st.warning("⚠️ 저장된 PDF가 없습니다. 먼저 PDF를 업로드하고 정답 JSON을 생성하세요.")
        else:
            # PDF 다중 선택
            selected_pdfs_for_batch = st.multiselect(
                "벡터 DB에 저장할 PDF 선택 (여러 개 선택 가능)",
                options=existing_pdfs,
                default=[],
                key="batch_rag_pdf_selector",
                help="여러 PDF를 선택하면 모든 페이지가 일괄로 벡터 DB에 저장됩니다"
            )
            
            if selected_pdfs_for_batch:
                # 선택된 PDF들의 페이지 수 확인
                total_pages = 0
                pdf_page_counts = {}
                for pdf_name in selected_pdfs_for_batch:
                    pdf_img_dir = img_dir / pdf_name
                    page_count = 0
                    if pdf_img_dir.exists():
                        for page_file in sorted(pdf_img_dir.glob("Page*_answer.json")):
                            page_count += 1
                    pdf_page_counts[pdf_name] = page_count
                    total_pages += page_count
                
                st.caption(f"선택된 PDF: {len(selected_pdfs_for_batch)}개, 총 페이지: {total_pages}개")
                for pdf_name, count in pdf_page_counts.items():
                    st.caption(f"  - {pdf_name}: {count}개 페이지")
                
                # 일괄 저장 버튼
                if st.button("🚀 선택한 PDF 모두 벡터 DB에 저장", type="primary", key="batch_save_all_rag"):
                    try:
                        rag_manager = get_rag_manager()
                        total_saved = 0
                        total_skipped = 0
                        pdf_results = {}
                        
                        progress_bar = st.progress(0)
                        status_text = st.empty()
                        
                        total_items = sum(pdf_page_counts.values())
                        processed_items = 0
                        
                        for pdf_idx, pdf_name in enumerate(selected_pdfs_for_batch):
                            pdf_img_dir = img_dir / pdf_name
                            pdf_saved = 0
                            pdf_skipped = 0
                            
                            status_text.text(f"처리 중: {pdf_name} ({pdf_idx + 1}/{len(selected_pdfs_for_batch)})")
                            
                            # 해당 PDF의 모든 페이지 찾기
                            page_files = sorted(pdf_img_dir.glob("Page*_answer.json"))
                            
                            for page_file in page_files:
                                try:
                                    # 페이지 번호 추출
                                    page_num_str = page_file.stem.replace("Page", "").replace("_answer", "")
                                    try:
                                        page_num = int(page_num_str)
                                    except ValueError:
                                        continue
                                    
                                    # OCR 텍스트 파일 경로
                                    ocr_text_path = pdf_img_dir / f"Page{page_num}_upstage.txt"
                                    if not ocr_text_path.exists():
                                        pdf_skipped += 1
                                        total_skipped += 1
                                        continue
                                    
                                    # OCR 텍스트 읽기
                                    with open(ocr_text_path, "r", encoding="utf-8") as f:
                                        ocr_text = f.read()
                                    
                                    if not ocr_text.strip():
                                        pdf_skipped += 1
                                        total_skipped += 1
                                        continue
                                    
                                    # 정답 JSON 읽기
                                    with open(page_file, "r", encoding="utf-8") as f:
                                        answer_json = json.load(f)
                                    
                                    # 벡터 DB에 저장
                                    rag_manager.add_example(
                                        ocr_text=ocr_text,
                                        answer_json=answer_json,
                                        metadata={
                                            "pdf_name": pdf_name,
                                            "page_num": page_num,
                                            "page_role": answer_json.get("page_role", "detail")
                                        }
                                    )
                                    
                                    pdf_saved += 1
                                    total_saved += 1
                                    
                                except PermissionError as e:
                                    pdf_skipped += 1
                                    total_skipped += 1
                                    st.warning(f"⚠️ {pdf_name} 페이지 {page_num_str} 저장 실패 (권한 문제): {e}")
                                except Exception as e:
                                    pdf_skipped += 1
                                    total_skipped += 1
                                    error_msg = str(e)
                                    if "readonly" in error_msg.lower():
                                        st.warning(f"⚠️ {pdf_name} 페이지 {page_num_str} 저장 실패 (읽기 전용): {error_msg}")
                                    else:
                                        st.warning(f"⚠️ {pdf_name} 페이지 {page_num_str} 저장 실패: {error_msg}")
                                
                                processed_items += 1
                                progress_bar.progress(processed_items / total_items if total_items > 0 else 1.0)
                            
                            pdf_results[pdf_name] = {"saved": pdf_saved, "skipped": pdf_skipped}
                        
                        progress_bar.progress(1.0)
                        status_text.empty()
                        
                        # 결과 표시
                        if total_saved > 0:
                            st.success(f"✅ 벡터 DB 저장 완료!")
                            st.caption(f"**저장 통계:**")
                            st.caption(f"- 총 저장: {total_saved}개 페이지")
                            st.caption(f"- 건너뜀: {total_skipped}개 페이지")
                            st.caption(f"- **총 예제 수: {rag_manager.count_examples()}개**")
                            
                            with st.expander("📊 PDF별 상세 결과"):
                                for pdf_name, result in pdf_results.items():
                                    st.text(f"**{pdf_name}**: 저장 {result['saved']}개, 건너뜀 {result['skipped']}개")
                        else:
                            st.error(f"❌ 저장 실패: 모든 페이지 저장에 실패했습니다 (건너뜀: {total_skipped}개)")
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
                            import traceback
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
            pdf_name = selected_existing
            if pdf_name not in st.session_state.answer_editor_pdfs:
                st.session_state.answer_editor_pdfs[pdf_name] = {
                    "pages": [],
                    "processed": False
                }

            pdf_info = st.session_state.answer_editor_pdfs[pdf_name]
            if not pdf_info["processed"]:
                page_info_list = []
                pdf_img_dir = img_dir / pdf_name
                page_num = 1
                while True:
                    image_path = pdf_img_dir / f"Page{page_num}.png"
                    if not image_path.exists():
                        break
                    upstage_text_path = pdf_img_dir / f"Page{page_num}_upstage.txt"
                    answer_json_path = pdf_img_dir / f"Page{page_num}_answer.json"
                    upstage_text = ""
                    if upstage_text_path.exists():
                        with open(upstage_text_path, "r", encoding="utf-8") as f:
                            upstage_text = f.read()
                    page_info_list.append({
                        "page_num": page_num,
                        "image_path": str(image_path),
                        "upstage_text_path": str(upstage_text_path),
                        "answer_json_path": str(answer_json_path),
                        "upstage_text": upstage_text
                    })
                    page_num += 1
                if page_info_list:
                    pdf_info["pages"] = page_info_list
                    pdf_info["processed"] = True
                    st.session_state.answer_editor_selected_pdf = pdf_name
                    st.session_state.answer_editor_selected_page = 1
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
            if st.button("🔄 PDF 처리 시작 (이미지 변환 + Upstage 텍스트 추출)", type="primary"):
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
                        upstage_extractor = UpstageExtractor()
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

                            upstage_text_path = img_dir / f"Page{page_num}_upstage.txt"
                            answer_json_path = img_dir / f"Page{page_num}_answer.json"

                            status_text.text(f"페이지 {page_num}/{total_pages} 처리 중...")
                            upstage_text = ""
                            if upstage_text_path.exists():
                                with open(upstage_text_path, "r", encoding="utf-8") as f:
                                    upstage_text = f.read()
                            if not upstage_text:
                                upstage_text = upstage_extractor.extract_text(str(image_path))
                                with open(upstage_text_path, "w", encoding="utf-8") as f:
                                    f.write(upstage_text)
                            page_info_list.append({
                                "page_num": page_num,
                                "image_path": str(image_path),
                                "upstage_text_path": str(upstage_text_path),
                                "answer_json_path": str(answer_json_path),
                                "upstage_text": upstage_text
                            })
                            progress_bar.progress((page_idx + 1) / total_pages)
                        progress_bar.empty()
                        status_text.empty()

                        pdf_info["pages"] = page_info_list
                        pdf_info["processed"] = True
                        st.session_state.answer_editor_selected_pdf = pdf_name
                        st.session_state.answer_editor_selected_page = 1

                        st.success(f"✅ PDF 처리 완료! {len(page_info_list)}개 페이지")
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
                st.rerun()
        else:
            selected_pdf = processed_pdfs[0]
            st.session_state.answer_editor_selected_pdf = selected_pdf

        pdf_info = st.session_state.answer_editor_pdfs[selected_pdf]

        if pdf_info["processed"] and pdf_info["pages"]:
            st.divider()
            st.subheader("📝 정답지 편집")
            total_pages = len(pdf_info["pages"])
            pages_with_upstage = [p for p in pdf_info["pages"] if p.get("upstage_text")]

            if pages_with_upstage:
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
                    if st.button("🤖 OpenAI로 전체 페이지 정답 생성", type="primary", key="openai_batch_extract"):
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

                        for idx, page_info in enumerate(pages_with_upstage):
                            page_num = page_info["page_num"]

                            # 기준 페이지는 건너뛰기 (이미 JSON이 있으므로)
                            if reference_page_num and page_num == reference_page_num:
                                status_text.text(f"페이지 {page_num}/{total_pages} 건너뜀 (기준 페이지)... ({idx + 1}/{len(pages_with_upstage)})")
                                success_count += 1
                                progress_bar.progress((idx + 1) / len(pages_with_upstage))
                                continue

                            status_text.text(f"페이지 {page_num}/{total_pages} 처리 중... ({idx + 1}/{len(pages_with_upstage)})")
                            parser = OpenAITextParser(
                                api_key=None,
                                model_name="gpt-5-mini-2025-08-07",
                                prompt_version="v2"
                            )
                            result_json = parser.parse_text(
                                text=page_info["upstage_text"],
                                reference_json=reference_json
                            )
                            with open(page_info["answer_json_path"], "w", encoding="utf-8") as f:
                                json.dump(result_json, f, ensure_ascii=False, indent=2)
                            success_count += 1
                            progress_bar.progress((idx + 1) / len(pages_with_upstage))
                        progress_bar.empty()
                        status_text.empty()
                        ref_msg = f" (기준 페이지 {reference_page_num} 참조)" if reference_json else ""
                        st.success(f"✅ 전체 {success_count}개 페이지 정답 JSON 생성 완료!{ref_msg}")
                        st.rerun()
                with col_btn2:
                    st.caption(f"총 {len(pages_with_upstage)}개 페이지")
                with col_btn3:
                    if reference_page_num:
                        st.caption(f"기준 페이지 {reference_page_num}의 JSON 정보를 참조하여 추출합니다")
                    else:
                        st.caption("모든 페이지의 Upstage 추출 결과를 OpenAI로 JSON 변환합니다")
                
                with col_btn4:
                    if st.button("🔍 전체 벡터 DB 저장", key="save_all_rag", 
                               help="모든 페이지의 OCR 텍스트와 정답 JSON을 벡터 DB에 저장"):
                        try:
                            rag_manager = get_rag_manager()
                            saved_count = 0
                            skipped_count = 0
                            
                            with st.spinner("벡터 DB에 저장 중..."):
                                for page_info in pdf_info["pages"]:
                                    page_num = page_info["page_num"]
                                    ocr_text = page_info.get("upstage_text", "")
                                    answer_json_path = page_info.get("answer_json_path", "")
                                    
                                    if not ocr_text or not os.path.exists(answer_json_path):
                                        skipped_count += 1
                                        continue
                                    
                                    try:
                                        with open(answer_json_path, "r", encoding="utf-8") as f:
                                            answer_json = json.load(f)
                                        
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
                                import traceback
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
                    st.rerun()
            with col2:
                if st.button("다음 ▶", disabled=(current_page >= total_pages)):
                    st.session_state.answer_editor_selected_page += 1
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
            with col1:
                with st.expander("..."):
                    if os.path.exists(page_info["image_path"]):
                        st.image(page_info["image_path"], caption=f"Page {current_page}", width='stretch')

                    # OpenAI 질문 기능 섹션
                    st.divider()
                    st.subheader("🤖 OpenAI 질문 기능")

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

                    # 질문 버튼
                    question_disabled = not (page_info.get("upstage_text") and reference_json)
                    if st.button(
                        "🔍 OpenAI에 질문하기",
                        type="primary",
                        disabled=question_disabled,
                        key=f"ask_openai_{current_page}"
                    ):
                        if not page_info.get("upstage_text"):
                            st.error("❌ 현재 페이지의 Upstage 텍스트가 없습니다.")
                        elif not reference_json:
                            st.error("❌ 참조용 JSON 파일을 업로드해주세요.")
                        else:
                            with st.spinner("OpenAI API 호출 중..."):
                                try:
                                    use_langchain_flag = False
                                    temperature = 0.0
                                    # OpenAI API 호출
                                    result_json = ask_openai_with_reference(
                                        ocr_text=page_info["upstage_text"],  # 현재 페이지의 TXT 사용
                                        answer_json=reference_json,  # 업로드한 JSON 사용
                                        question=page_info["upstage_text"],  # 현재 페이지의 TXT를 질문으로 사용
                                        model_name="gpt-4o-2024-08-06",
                                        use_langchain=use_langchain_flag,  # 라이브러리 선택
                                        temperature=temperature  # Temperature 설정
                                    )

                                    # 세션 상태에 저장
                                    st.session_state[f"openai_result_{current_page}"] = result_json
                                    st.success("✅ OpenAI 응답 완료!")

                                except Exception as e:
                                    st.error(f"❌ OpenAI API 호출 실패: {e}")
                                    import traceback
                                    st.code(traceback.format_exc())

                    # 응답 결과 표시
                    if f"openai_result_{current_page}" in st.session_state:
                        result_json = st.session_state[f"openai_result_{current_page}"]

                        # 결과를 데이터프레임으로 변환
                        if result_json.get("items"):
                            result_df, mgmt_col = prepare_dataframe_for_aggrid(result_json["items"])

                            if AgGridUtils.is_available() and len(result_df) > 0:
                                from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode, JsCode

                                gb = GridOptionsBuilder.from_dataframe(result_df)
                                gb.configure_default_column(editable=False, resizable=True)

                                for col in result_df.columns:
                                    japanese_name = COLUMN_NAME_MAPPING.get(col, col)
                                    if col == 'No':
                                        gb.configure_column(col, header_name=japanese_name, editable=False, width=60, pinned='left')
                                    else:
                                        gb.configure_column(col, header_name=japanese_name)

                                gb.configure_pagination(enabled=False)
                                get_row_style_code = create_management_color_style(mgmt_col, result_df)
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

                                st.subheader("📊 OpenAI 응답 결과")
                                AgGrid(result_df, gridOptions=grid_options, update_mode=GridUpdateMode.NO_UPDATE,
                                       data_return_mode=DataReturnMode.FILTERED_AND_SORTED, fit_columns_on_grid_load=True,
                                       height=400, theme='streamlit', allow_unsafe_jscode=True, hide_index=False,
                                       key=f"openai_result_grid_{current_page}")
                            elif len(result_df) > 0:
                                st.subheader("📊 OpenAI 응답 결과")
                                st.dataframe(result_df, height=400)
                            else:
                                st.info("응답에 items가 없습니다.")

            with col2:
                st.subheader("📄 Upstage 추출 결과 (원문 텍스트)")
                if page_info["upstage_text"]:
                    st.text_area(
                        "Upstage OCR 결과",
                        value=page_info["upstage_text"],
                        height=200,
                        key=f"upstage_text_{current_page}",
                        disabled=True
                    )
                else:
                    st.warning("Upstage 추출 결과가 없습니다.")

                # JSON 파일 로드
                answer_json_path = page_info["answer_json_path"]
                default_answer_json = {
                    "page_role": "detail",
                    "items": []
                }
                if os.path.exists(answer_json_path):
                    try:
                        with open(answer_json_path, "r", encoding="utf-8") as f:
                            default_answer_json = json.load(f)
                    except Exception as e:
                        st.warning(f"기존 정답 JSON 로드 실패: {e}")

                # JSON 편집 expander
                with st.expander("📝 JSON 편집", expanded=False):
                    page_role = st.selectbox(
                        "페이지 역할 (page_role)",
                        options=["cover", "detail", "summary"],
                        index=["cover", "detail", "summary"].index(default_answer_json.get("page_role", "detail")) if default_answer_json.get("page_role", "detail") in ["cover", "detail", "summary"] else 1,
                        key=f"page_role_{current_page}"
                    )

                    st.divider()

                    # JSON 편집 창
                    answer_json_str_default = json.dumps(default_answer_json, ensure_ascii=False, indent=2)
                    answer_json_str = st.text_area(
                        "정답 JSON (편집 가능)",
                        value=answer_json_str_default,
                        height=300,
                        key=f"answer_json_{current_page}"
                    )

                    # JSON 파싱 오류 처리
                    try:
                        parsed_json = json.loads(answer_json_str)
                    except json.JSONDecodeError as e:
                        st.error(f"❌ JSON 파싱 오류: {e}")

                # Items 편집 expander
                with st.expander("📊 Items 편집 (AgGrid)", expanded=False):
                    # JSON에서 items 추출 (세션 상태에서 가져오기)
                    items = []
                    try:
                        answer_json_str_for_items = st.session_state.get(f"answer_json_{current_page}", answer_json_str_default)
                        parsed_json = json.loads(answer_json_str_for_items)
                        items = parsed_json.get("items", [])
                    except (json.JSONDecodeError, NameError, KeyError):
                        items = []

                    if not AgGridUtils.is_available():
                        st.warning("⚠️ AgGrid가 설치되어 있지 않습니다. `pip install streamlit-aggrid`로 설치하세요.")
                        if items:
                            df = pd.DataFrame(items)
                            edited_df = st.data_editor(df, height=400, key=f"items_editor_{current_page}")
                            st.session_state[f"updated_items_{current_page}"] = edited_df.to_dict('records')
                        else:
                            st.info("Items가 없습니다.")
                            st.session_state[f"updated_items_{current_page}"] = []
                    elif not items:
                        st.info("Items가 없습니다. JSON 편집 창에서 items를 추가하세요.")
                        st.session_state[f"updated_items_{current_page}"] = []
                    else:
                        df, mgmt_col = prepare_dataframe_for_aggrid(items)

                        # GridOptionsBuilder 설정
                        from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, DataReturnMode, JsCode
                        
                        if len(df) == 0 or len(df.columns) == 0:
                            st.warning(f"⚠️ DataFrame을 생성할 수 없습니다. (items 개수: {len(items)})")
                            st.session_state[f"updated_items_{current_page}"] = items
                        else:
                            gb = GridOptionsBuilder.from_dataframe(df)
                            gb.configure_default_column(editable=True, resizable=True)

                            # 각 컬럼의 헤더명을 일본어로 설정
                            for col in df.columns:
                                japanese_name = COLUMN_NAME_MAPPING.get(col, col)
                                if col == 'No':
                                    gb.configure_column(col, header_name=japanese_name, editable=False, width=60, pinned='left')
                                else:
                                    gb.configure_column(col, header_name=japanese_name)

                            gb.configure_pagination(enabled=False)

                            # 관리번호별 색상 지정 (함수 사용)
                            get_row_style_code = create_management_color_style(mgmt_col, df)
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
                                key=f"aggrid_items_{current_page}"
                            )

                            # 수정된 데이터 가져오기
                            updated_df = grid_response['data'] if grid_response.get('data') is not None else pd.DataFrame()
                            if len(updated_df) > 0 and 'No' in updated_df.columns:
                                updated_df = updated_df.drop(columns=['No'])
                            st.session_state[f"updated_items_{current_page}"] = updated_df.to_dict('records') if len(updated_df) > 0 else items

                            # AgGrid 바로 아래에 저장 버튼 추가
                            st.caption("⚠️ AgGrid에서 수정한 내용은 아래 저장 버튼을 클릭해야 파일에 저장됩니다.")
                            col_save_aggrid1, col_save_aggrid2 = st.columns([1, 4])
                            with col_save_aggrid1:
                                if st.button("💾 AgGrid 변경사항 저장", type="primary", key=f"save_aggrid_{current_page}"):
                                    # 현재 JSON 로드
                                    answer_json = json.load(open(answer_json_path, "r", encoding="utf-8")) if os.path.exists(answer_json_path) else default_answer_json.copy()

                                    # AgGrid에서 수정된 items 반영
                                    answer_json["items"] = st.session_state.get(f"updated_items_{current_page}", items)
                                    answer_json["page_role"] = st.session_state.get(f"page_role_{current_page}", default_answer_json.get("page_role", "detail"))

                                    # 파일 저장
                                    os.makedirs(os.path.dirname(answer_json_path), exist_ok=True)
                                    with open(answer_json_path, "w", encoding="utf-8") as f:
                                        json.dump(answer_json, f, ensure_ascii=False, indent=2)

                                    st.success(f"✅ AgGrid 변경사항 저장 완료! (파일 크기: {os.path.getsize(answer_json_path)} bytes)")
                                    st.rerun()

                            with col_save_aggrid2:
                                st.caption(f"저장 경로: `{answer_json_path}`")

                # 저장 버튼 (expander 밖)
                col_save1, col_save2, col_save3 = st.columns([1, 1, 3])
                with col_save1:
                    if st.button("💾 저장", type="primary", key=f"save_answer_{current_page}"):
                        # JSON 파싱 및 page_role 업데이트
                        try:
                            # answer_json_str_default가 정의되어 있는지 확인
                            if 'answer_json_str_default' not in locals():
                                answer_json_str_default = json.dumps(default_answer_json, ensure_ascii=False, indent=2)
                            
                            answer_json_str_for_save = st.session_state.get(f"answer_json_{current_page}", answer_json_str_default)
                            page_role_for_save = st.session_state.get(f"page_role_{current_page}", default_answer_json.get("page_role", "detail"))
                            
                            # JSON 파싱
                            answer_json = json.loads(answer_json_str_for_save)
                            answer_json["page_role"] = page_role_for_save
                            
                            # items 업데이트 (AgGrid에서 수정한 경우)
                            updated_items = st.session_state.get(f"updated_items_{current_page}")
                            if updated_items is not None:
                                answer_json["items"] = updated_items
                            elif "items" not in answer_json:
                                answer_json["items"] = []

                            # 파일 저장
                            if not answer_json_path:
                                st.error(f"❌ 저장 경로가 없습니다. answer_json_path를 확인하세요.")
                            else:
                                os.makedirs(os.path.dirname(answer_json_path), exist_ok=True)
                                with open(answer_json_path, "w", encoding="utf-8") as f:
                                    json.dump(answer_json, f, ensure_ascii=False, indent=2)

                                st.success(f"✅ 정답 JSON 저장 완료! (파일 크기: {os.path.getsize(answer_json_path)} bytes)")
                                st.caption(f"저장 경로: `{answer_json_path}`")
                                st.rerun()
                        except json.JSONDecodeError as e:
                            st.error(f"❌ JSON 파싱 오류: {e}")
                            import traceback
                            st.code(traceback.format_exc())
                        except Exception as e:
                            st.error(f"❌ 저장 실패: {e}")
                            import traceback
                            st.code(traceback.format_exc())
                    
                with col_save2:
                    # 벡터 DB 저장 버튼
                    ocr_text = page_info.get("upstage_text", "")
                    has_ocr = bool(ocr_text)
                    try:
                        answer_json_str_for_check = st.session_state.get(f"answer_json_{current_page}", answer_json_str_default)
                        parsed_json = json.loads(answer_json_str_for_check)
                        has_answer = bool(parsed_json)
                    except (json.JSONDecodeError, NameError, KeyError):
                        has_answer = False
                    
                    if st.button("🔍 벡터 DB 저장", key=f"save_rag_{current_page}", 
                               disabled=not (has_ocr and has_answer),
                               help="OCR 텍스트와 정답 JSON을 벡터 DB에 저장합니다 (RAG 학습용)"):
                        try:
                            # JSON 파싱
                            answer_json_str_for_rag = st.session_state.get(f"answer_json_{current_page}", answer_json_str_default)
                            page_role_for_rag = st.session_state.get(f"page_role_{current_page}", default_answer_json.get("page_role", "detail"))
                            answer_json = json.loads(answer_json_str_for_rag)
                            answer_json["page_role"] = page_role_for_rag
                            answer_json["items"] = st.session_state.get(f"updated_items_{current_page}", answer_json.get("items", []))
                            
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
                                import traceback
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
                    answer_json = json.load(f)
                    answer_items = answer_json.get("items", [])

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

