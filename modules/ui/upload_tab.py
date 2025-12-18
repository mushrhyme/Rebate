"""
업로드 & 분석 탭
"""
import os
import time
from pathlib import Path
from typing import Tuple, Dict, Any
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

import streamlit as st

from utils.session_manager import SessionManager
from modules.core.processor import PdfProcessor
from modules.utils.pdf_utils import find_pdf_path
from modules.core.app_processor import (
    check_pdf_in_db
)
from modules.utils.session_utils import ensure_session_state_defaults


def render_upload_tab():
    """업로드 & 분석 탭"""
    ensure_session_state_defaults()
    st.info(
        "**📌 使い方ガイド**:\n\n"
        "• 複数のファイルをアップロードした後、🔍 **解析実行**をクリックすると同時に分析できます",
        icon="ℹ️"
    )

    uploaded_files = st.file_uploader(
        "PDFファイルをアップロードしてください（複数ファイル選択可能）",
        type=['pdf'],
        accept_multiple_files=True
    )

    if uploaded_files:
        current_names = {Path(f.name).stem for f in uploaded_files}
        existing_names = {info["name"] for info in st.session_state.uploaded_files_info}
        new_files = current_names - existing_names
        for uploaded_file in uploaded_files:
            pdf_name = Path(uploaded_file.name).stem
            if pdf_name in new_files:
                st.session_state.uploaded_file_objects[pdf_name] = uploaded_file.getvalue()
                pdf_filename = f"{pdf_name}.pdf"
                is_in_db, db_page_count = check_pdf_in_db(pdf_filename)
                st.session_state.uploaded_files_info.append({
                    "name": pdf_name,
                    "original_name": uploaded_file.name,
                    "size": uploaded_file.size,
                    "is_in_db": is_in_db,
                    "db_page_count": db_page_count
                })
                if is_in_db and db_page_count > 0:
                    st.session_state.analysis_status[pdf_name] = {
                        "status": "completed",
                        "pages": db_page_count,
                        "error": None
                    }
                else:
                    # 새 파일은 항상 pending 상태로 설정
                    st.session_state.analysis_status[pdf_name] = {
                        "status": "pending",
                        "pages": 0,
                        "error": None
                    }
                    # PdfRegistry에서도 제거하고 pending 상태로 재설정 (이전 상태가 있을 수 있음)
                    try:
                        from modules.core.registry import PdfRegistry
                        # 완전히 제거 후 pending 상태로 재생성
                        PdfRegistry.remove(pdf_name)
                        PdfRegistry.ensure(pdf_name, status="pending", pages=0, error=None, source="session")
                    except Exception as e:
                        print(f"⚠️ PdfRegistry 초기화 실패: {e}")
        removed_names = existing_names - current_names
        if removed_names:
            st.session_state.uploaded_files_info = [
                info for info in st.session_state.uploaded_files_info
                if info["name"] not in removed_names
            ]
            for pdf_name in removed_names:
                st.session_state.analysis_status.pop(pdf_name, None)
                st.session_state.review_data.pop(pdf_name, None)
                st.session_state.uploaded_file_objects.pop(pdf_name, None)
    elif not uploaded_files and st.session_state.uploaded_files_info:
        st.session_state.uploaded_files_info = []
        st.session_state.analysis_status = {}
        st.session_state.uploaded_file_objects = {}

    processing_files = [
        pdf_name for pdf_name, status_info in st.session_state.analysis_status.items()
        if status_info.get("status") == "processing"
    ]

    if processing_files:
        st.warning(
            f"**分析中のファイルがあります**: {', '.join([f'{name}.pdf' for name in processing_files])}\n\n"
            "ページをリロードしても分析は継続されます。完了までお待ちください。",
            icon="⚠️"
        )

    if st.session_state.uploaded_files_info:
        st.subheader("📋 アップロードされたファイル一覧")
        for idx, file_info in enumerate(st.session_state.uploaded_files_info):
            col1, col2 = st.columns([4, 2])
            pdf_name = file_info['name']
            status_info = st.session_state.analysis_status.get(pdf_name, {})
            status = status_info.get("status", "pending")
            with col1:
                st.text(f"📄 {file_info['original_name']}")
            with col2:
                if status == "completed":
                    pages = status_info.get("pages", 0)
                    st.success(f"完了 ({pages}p)", icon="✅")
                elif status == "processing":
                    st.info("解析中...", icon="🔄")
                elif status == "error":
                    error = status_info.get("error", "不明なエラー")
                    st.error(f"エラー: {error[:30]}...", icon="❌")
                elif file_info.get("is_in_db") and file_info.get("db_page_count", 0) > 0:
                    st.info(f"解析済み ({file_info['db_page_count']}p)", icon="💾")
                else:
                    st.warning("待機中", icon="⏳")

        st.divider()

        pending_files = [
            info["name"] for info in st.session_state.uploaded_files_info
            if (st.session_state.analysis_status.get(info["name"], {}).get("status") == "pending" and
                not (info.get("is_in_db") and info.get("db_page_count", 0) > 0))
        ]

        processable_files = [
            name for name in pending_files 
            if PdfProcessor.can_process_pdf(name)
        ]

        # 디버깅 정보 (개발용)
        # if st.session_state.uploaded_files_info and not processable_files:
        #     with st.expander("🔍 디버깅 정보 (분석 버튼이 비활성화된 이유)", expanded=False):
        #         st.write(f"**업로드된 파일 수**: {len(st.session_state.uploaded_files_info)}")
        #         st.write(f"**pending_files**: {len(pending_files)}개 - {pending_files}")
        #         st.write(f"**processable_files**: {len(processable_files)}개 - {processable_files}")
                
        #         st.write("\n**각 파일 상태:**")
        #         for info in st.session_state.uploaded_files_info:
        #             pdf_name = info["name"]
        #             status_info = st.session_state.analysis_status.get(pdf_name, {})
        #             status = status_info.get("status", "unknown")
        #             is_in_db = info.get("is_in_db", False)
        #             db_page_count = info.get("db_page_count", 0)
        #             can_process = PdfProcessor.can_process_pdf(pdf_name)
                    
        #             st.write(f"- **{pdf_name}**:")
        #             st.write(f"  - status: {status}")
        #             st.write(f"  - is_in_db: {is_in_db}, db_page_count: {db_page_count}")
        #             st.write(f"  - can_process: {can_process}")
        #             st.write(f"  - pending 조건: status=='pending'={status=='pending'}, not_in_db={not (is_in_db and db_page_count > 0)}")
                    
        #             # PdfRegistry 상태 확인
        #             try:
        #                 from modules.core.registry import PdfRegistry
        #                 registry_metadata = PdfRegistry.get(pdf_name)
        #                 if registry_metadata:
        #                     st.write(f"  - PdfRegistry 상태: {registry_metadata.get('status', 'unknown')}")
        #                     st.write(f"  - PdfRegistry 메타데이터: {registry_metadata}")
        #                 else:
        #                     st.write(f"  - PdfRegistry: 없음 (새 파일)")
        #             except Exception as e:
        #                 st.write(f"  - PdfRegistry 확인 실패: {e}")

        if processable_files:
            st.info(f"{len(processable_files)}個のファイルが解析待機中です。", icon="💡")
        elif not pending_files and st.session_state.uploaded_files_info:
            st.success("すべてのファイルの解析が完了しました！", icon="✅")

        # RAG 기반 분석 정보 표시 (무조건 RAG 사용)
        st.divider()
        try:
            from modules.core.rag_manager import get_rag_manager
            rag_manager = get_rag_manager()
            example_count = rag_manager.count_examples()
            if example_count > 0:
                st.success(f"✅ RAG 기반 분석 활성화 (벡터 DB 예제: {example_count}개)")
            else:
                st.warning("⚠️ 벡터 DB에 예제가 없습니다. 정답지 편집 탭에서 예제를 추가하세요.")
        except Exception as e:
            st.error(f"❌ RAG Manager 초기화 실패: {e}")

        button_disabled = len(processable_files) == 0
        if st.button("🔍 解析実行", type="primary", width='stretch', disabled=button_disabled):
            files_to_analyze = []
            for pdf_name in processable_files:
                file_info = next(
                    (info for info in st.session_state.uploaded_files_info if info["name"] == pdf_name),
                    None
                )
                if not file_info:
                    continue
                file_bytes = st.session_state.uploaded_file_objects.get(pdf_name)
                if file_bytes:
                    uploaded_file = BytesIO(file_bytes)
                    uploaded_file.name = file_info["original_name"]
                    try:
                        SessionManager.save_pdf_file(uploaded_file, pdf_name)
                        uploaded_file = BytesIO(file_bytes)
                        uploaded_file.name = file_info["original_name"]
                    except Exception:
                        pass
                    files_to_analyze.append((file_info, uploaded_file, None))
                else:
                    pdf_path = find_pdf_path(pdf_name)
                    if pdf_path:
                        files_to_analyze.append((file_info, None, pdf_path))
                    else:
                        st.warning(f"⚠️ {pdf_name}.pdf ファイルが見つかりません。スキップします。", icon="⚠️")

            if files_to_analyze:
                # 파일 데이터 준비 (스레드 안전성을 위해 bytes 데이터도 포함)
                prepared_files = []
                for file_info, uploaded_file, pdf_path in files_to_analyze:
                    pdf_name = file_info["name"]
                    file_bytes_data = None
                    if uploaded_file is not None:
                        # BytesIO 객체의 데이터를 미리 추출 (스레드 안전성)
                        file_bytes_data = st.session_state.uploaded_file_objects.get(pdf_name)
                    prepared_files.append((file_info, uploaded_file, pdf_path, file_bytes_data))
                
                file_names = [f[0]['name'] for f in prepared_files]
                total_files = len(prepared_files)
                
                # Upstage API Rate limit 방지를 위해 파일 단위 병렬 처리 비활성화
                # (각 파일 내부의 OCR은 순차 처리, RAG+LLM은 병렬 처리)
                use_parallel = False  # 파일 단위 병렬 처리 비활성화
                max_workers = 1
                
                st.info(f"**分析対象**: {total_files}個のファイル - {', '.join(file_names)}", icon="ℹ️")
                if total_files > 1:
                    st.info(f"📝 **순차 처리 모드**: 파일을 하나씩 처리합니다 (Upstage API Rate limit 방지)", icon="📝")
                
                progress_placeholder = st.empty()
                start_time = time.time()
                
                def process_single_file_thread(file_data: Tuple) -> Dict[str, Any]:
                    """단일 파일 처리 함수 (스레드에서 실행) - UI 없이 처리"""
                    file_info, uploaded_file, pdf_path, file_bytes_data = file_data
                    pdf_name = file_info["name"]
                    file_display_name = file_info.get("original_name", f"{pdf_name}.pdf")
                    
                    try:
                        # UI 없이 직접 처리 (progress_callback=None)
                        if uploaded_file is not None or file_bytes_data is not None:
                            # 스레드 안전성을 위해 새로운 BytesIO 객체 생성
                            if file_bytes_data:
                                thread_uploaded_file = BytesIO(file_bytes_data)
                                thread_uploaded_file.name = file_display_name
                            else:
                                thread_uploaded_file = uploaded_file
                            
                            from modules.utils.config import get_rag_config
                            config = get_rag_config()
                            
                            success, pages, error, elapsed_time = PdfProcessor.process_uploaded_pdf(
                                uploaded_file=thread_uploaded_file,
                                pdf_name=pdf_name,
                                dpi=config.dpi,
                                progress_callback=None  # 스레드에서는 UI 업데이트 안 함
                            )
                        else:
                            from modules.utils.config import get_rag_config
                            config = get_rag_config()
                            
                            success, pages, error, elapsed_time = PdfProcessor.process_pdf(
                                pdf_name=pdf_name,
                                pdf_path=pdf_path,
                                dpi=config.dpi,
                                progress_callback=None  # 스레드에서는 UI 업데이트 안 함
                            )
                        
                        return {
                            "pdf_name": pdf_name,
                            "file_display_name": file_display_name,
                            "success": success,
                            "pages": pages,
                            "error": error,
                            "elapsed_time": elapsed_time,
                            "exception": None
                        }
                    except Exception as e:
                        return {
                            "pdf_name": pdf_name,
                            "file_display_name": file_display_name,
                            "success": False,
                            "pages": 0,
                            "error": str(e),
                            "elapsed_time": 0.0,
                            "exception": str(e)
                        }
                
                # 병렬 처리 또는 순차 처리
                results = []
                if use_parallel:
                    # ThreadPoolExecutor로 병렬 처리
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        # 모든 파일에 대해 Future 제출
                        future_to_file = {
                            executor.submit(process_single_file_thread, file_data): file_data
                            for file_data in prepared_files
                        }
                        
                        # 완료된 작업부터 처리
                        completed_count = 0
                        for future in as_completed(future_to_file):
                            result = future.result()
                            results.append(result)
                            completed_count += 1
                            
                            # 진행 상황 표시 (완료된 파일 수만 표시)
                            with progress_placeholder.container():
                                st.info(f"처리 중... ({completed_count}/{total_files}개 파일 완료)", icon="🔄")
                else:
                    # 순차 처리 (1개 파일)
                    for file_data in prepared_files:
                        result = process_single_file_thread(file_data)
                        results.append(result)
                        with progress_placeholder.container():
                            st.info(f"처리 중... (1/1)", icon="🔄")
                
                # 결과 수집 및 UI 업데이트 (메인 스레드에서)
                progress_placeholder.empty()
                total_pages = 0
                success_count = 0
                
                for result in results:
                    pdf_name = result["pdf_name"]
                    file_display_name = result["file_display_name"]
                    
                    if result["success"]:
                        total_pages += result["pages"]
                        success_count += 1
                        
                        # 세션 상태 업데이트
                        st.session_state.analysis_status[pdf_name] = {
                            "status": "completed",
                            "pages": result["pages"],
                            "error": None
                        }
                        
                        # 파일 정보 업데이트
                        file_info_idx = next(
                            (idx for idx, info in enumerate(st.session_state.uploaded_files_info) 
                             if info["name"] == pdf_name),
                            None
                        )
                        if file_info_idx is not None:
                            st.session_state.uploaded_files_info[file_info_idx]["is_in_db"] = True
                            st.session_state.uploaded_files_info[file_info_idx]["db_page_count"] = result["pages"]
                        
                        st.success(f"✅ **{file_display_name}** 解析完了 ({result['pages']}ページ)", icon="✅")
                    else:
                        error_msg = result.get("error") or result.get("exception") or "알 수 없는 오류"
                        st.error(f"❌ **{file_display_name}** 解析失敗: {error_msg}", icon="❌")
                        PdfProcessor.get_processing_status(pdf_name)
                
                # 최종 결과 표시
                if success_count > 0:
                    actual_elapsed_time = time.time() - start_time
                    minutes = int(actual_elapsed_time // 60)
                    seconds = int(actual_elapsed_time % 60)
                    if minutes > 0:
                        time_str = f"{minutes}分{seconds}秒"
                    else:
                        time_str = f"{seconds}秒"
                    
                    if use_parallel:
                        st.success(f"🎉 **{success_count}個のファイル解析完了！** (総 {total_pages}ページ、所要時間: {time_str}, 병렬 처리)", icon="✅")
                    else:
                        st.success(f"🎉 **{success_count}個のファイル解析完了！** (総 {total_pages}ページ、所要時間: {time_str})", icon="✅")
                    st.rerun()
            else:
                st.warning("分析対象のファイルがありません。", icon="⚠️")
    else:
        st.info("上でPDFファイルをアップロードしてください。", icon="👆")

