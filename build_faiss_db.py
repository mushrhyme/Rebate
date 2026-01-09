"""
img 폴더의 PDF 데이터를 FAISS 벡터 DB로 변환하는 스크립트 (증분 shard + merge 구조)

img 폴더의 모든 하위 폴더에서:
- PDF 파일 (PyMuPDF로 텍스트 추출)
- Page*_answer.json (정답 JSON)

파일을 찾아서 변경분만 shard로 생성하고 base DB에 merge합니다.
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, List, Optional
import fitz  # PyMuPDF

from modules.core.rag_manager import get_rag_manager
from modules.utils.config import get_project_root
from modules.utils.hash_utils import compute_page_hash, get_page_key, compute_file_fingerprint
from modules.utils.db_manifest_manager import DBManifestManager
from modules.utils.pdf_utils import PdfTextExtractor
from modules.utils.pdf_to_excel import PdfToExcelConverter


def find_pdf_pages(
    img_dir: Path,
    form_folder: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    img 폴더의 양식별 폴더(01, 02 등) 안의 하위 폴더에서 모든 PDF 페이지 데이터를 찾습니다.

    Args:
        img_dir: img 폴더 경로
        form_folder: 양식 폴더명 (예: "01", "02"). None이면 모든 양식 폴더를 순회

    Returns:
        [page_data, ...] 리스트
        page_data = {
            'pdf_name': str,
            'page_num': int,
            'pdf_path': Path,
            'answer_json_path': Optional[Path]
        }
    """
    pages = []

    # 양식별 폴더 목록 (01, 02, 03, 04, 05 등)
    if form_folder:
        form_folders = [img_dir / form_folder]
    else:
        # 모든 양식 폴더 순회
        form_folders = [d for d in img_dir.iterdir() if d.is_dir() and d.name.isdigit()]
        form_folders.sort()  # 숫자 순서로 정렬

    for form_dir in form_folders:
        if not form_dir.exists():
            continue

        print(f"📁 양식 폴더: {form_dir.name}")

        # 양식 폴더 안의 하위 폴더 순회
        for pdf_folder in form_dir.iterdir():
            if not pdf_folder.is_dir():
                continue

            pdf_name = pdf_folder.name
            pdf_file = pdf_folder / f"{pdf_name}.pdf"
            if not pdf_file.exists():
                pdf_file = form_dir / f"{pdf_name}.pdf"

            if not pdf_file.exists():
                print(f"  ⚠️ PDF 파일 없음: {pdf_name}")
                continue

            # 버전 구분 없이 모든 Page*_answer*.json 대상으로 처리
            answer_files = sorted(pdf_folder.glob("Page*_answer*.json"))

            if not answer_files:
                print(f"  ⚠️ {pdf_name}: answer.json 파일이 없습니다")
                continue

            try:
                doc = fitz.open(pdf_file)
                page_count = len(doc)
                doc.close()
            except Exception as e:
                print(f"  ⚠️ PDF 파일 열기 실패 ({pdf_name}): {e}")
                continue

            print(f"  - {pdf_name}: {len(answer_files)}개 answer.json 파일, {page_count}페이지")

            for answer_file in answer_files:
                try:
                    # Page{num}_answer.json 또는 Page{num}_answer_xx.json 형식 대응
                    stem = answer_file.stem
                    # 가장 왼쪽의 숫자만 뽑는다. 예시: Page3_answer_v2 -> '3'
                    import re
                    match = re.match(r'Page(\d+)_answer', stem)
                    if not match:
                        print(f"  ⚠️ 페이지 번호 파싱 실패: {answer_file}")
                        continue
                    page_num = int(match.group(1))

                    if page_num < 1 or page_num > page_count:
                        print(f"  ⚠️ 페이지 번호 범위 초과: {pdf_name} Page{page_num} (최대: {page_count})")
                        continue

                    pages.append({
                        'pdf_name': pdf_name,
                        'page_num': page_num,
                        'pdf_path': pdf_file,
                        'answer_json_path': answer_file
                    })
                except ValueError:
                    print(f"  ⚠️ 페이지 번호 파싱 실패: {answer_file}")
                    continue

    return pages




def load_answer_json(answer_path: Optional[Path]) -> Dict[str, Any]:
    """정답 JSON 파일을 읽습니다."""
    if answer_path is None or not answer_path.exists():
        return {}

    try:
        with open(answer_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 정답 JSON 읽기 실패 ({answer_path}): {e}")
        return {}


def diff_pages_with_manifest(
    pages: List[Dict[str, Any]],
    manifest: DBManifestManager,
    text_extractor: PdfTextExtractor,
    text_extraction_method: str = "pymupdf"  # "pymupdf" 또는 "excel"
) -> List[Dict[str, Any]]:
    """
    manifest와 비교하여 새로운 페이지 또는 변경된 페이지만 필터링합니다.
    2단계 체크: 1단계(answer.json fingerprint) → 2단계(실제 텍스트 hash)
    staged 상태는 재처리하지 않음.

    Args:
        pages: 페이지 데이터 리스트
        manifest: DBManifestManager 인스턴스
        text_extractor: PDF 텍스트 추출기 (캐싱 지원)

    Returns:
        새로운 페이지 또는 변경된 페이지 리스트
    """
    new_pages = []

    for page_data in pages:
        pdf_name = page_data['pdf_name']
        page_num = page_data['page_num']
        pdf_path = page_data['pdf_path']
        answer_path = page_data.get('answer_json_path')

        if not answer_path or not answer_path.exists():
            continue

        pdf_filename = f"{pdf_name}.pdf"  # DB는 확장자 포함
        page_key = get_page_key(pdf_name, page_num)

        # staged 상태는 재처리하지 않음
        if manifest.is_staged(pdf_filename, page_num):
            continue

        # 1단계: answer.json fingerprint 체크
        fingerprint = compute_file_fingerprint(pdf_path, answer_path)
        if not manifest.is_file_changed_fast(pdf_filename, page_num, fingerprint):
            continue

        # 2단계: 실제 텍스트 추출 및 hash 계산
        if text_extraction_method == "excel":
            # 엑셀 변환 방법 사용
            try:
                excel_converter = PdfToExcelConverter(method="pdfplumber")
                ocr_text = excel_converter.convert_to_text_for_llm(pdf_path, page_num)
            except Exception as e:
                print(f"⚠️ 엑셀 변환 실패, PyMuPDF로 폴백 ({pdf_name}, 페이지 {page_num}): {e}")
                ocr_text = text_extractor.extract_text(pdf_path, page_num)
        else:
            # 기본 PyMuPDF 방법 사용
            ocr_text = text_extractor.extract_text(pdf_path, page_num)

        if not ocr_text:
            continue

        answer_json = load_answer_json(answer_path)
        if not answer_json:
            continue

        page_hash = compute_page_hash(ocr_text, answer_json)

        # merged 상태이고 hash 동일하면 스킵
        if manifest.is_processed(pdf_filename, page_num, page_hash):
            continue

        # 새로운 페이지이거나 변경됨
        new_pages.append({
            **page_data,
            'ocr_text': ocr_text,
            'answer_json': answer_json,
            'page_hash': page_hash,
            'page_key': page_key,
            'fingerprint': fingerprint,
            'pdf_filename': pdf_filename  # DB용 파일명 추가
        })

    return new_pages


def detect_deleted_pages(
    scanned_pages: List[Dict[str, Any]],  # [{'pdf_name': str, 'page_num': int}, ...]
    manifest: DBManifestManager
) -> List[Dict[str, Any]]:
    """
    삭제된 페이지 감지 (manifest에 있지만 스캔 결과에 없음)

    Args:
        scanned_pages: 현재 스캔된 페이지 리스트
        manifest: DBManifestManager 인스턴스

    Returns:
        삭제된 페이지 정보 리스트 [{'pdf_filename': str, 'page_number': int}, ...]
    """
    # 스캔된 페이지를 (pdf_filename, page_number) 집합으로 변환
    scanned_set = {
        (f"{p['pdf_name']}.pdf", p['page_num'])
        for p in scanned_pages
    }

    # DB에서 모든 페이지 조회
    try:
        with manifest.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT pdf_filename, page_number, status
                FROM rag_learning_status
                WHERE status IN ('merged', 'staged')
            """)

            deleted_pages = []
            for row in cursor.fetchall():
                pdf_filename = row[0]
                page_number = row[1]
                status = row[2]

                # 스캔 결과에 없으면 삭제된 것으로 간주
                if (pdf_filename, page_number) not in scanned_set:
                    deleted_pages.append({
                        'pdf_filename': pdf_filename,
                        'page_number': page_number
                    })

        return deleted_pages
    except Exception as e:
        # 테이블이 없거나 오류가 발생하면 빈 리스트 반환
        print(f"⚠️ 삭제된 페이지 감지 중 오류 (무시): {e}")
        return []


def build_faiss_db(
    img_dir: Path = None,
    form_folder: Optional[str] = None,
    auto_merge: bool = False,
    text_extraction_method: str = "pymupdf"  # "pymupdf" 또는 "excel"
) -> None:
    """
    img 폴더의 데이터를 FAISS 벡터 DB로 변환합니다 (증분 shard + merge 구조).

    Args:
        img_dir: img 폴더 경로 (None이면 프로젝트 루트/img)
        form_folder: 양식 폴더명 (예: "01", "02"). None이면 모든 양식 폴더를 순회
        auto_merge: shard 생성 후 자동으로 merge할지 여부
        text_extraction_method: 텍스트 추출 방법 ("pymupdf" 또는 "excel")
            - "pymupdf": PyMuPDF로 텍스트 추출 (기본값)
            - "excel": pdfplumber로 테이블 추출 후 엑셀 형식 텍스트로 변환
    """
    if img_dir is None:
        project_root = get_project_root()
        img_dir = project_root / "img"

    if not img_dir.exists():
        print(f"❌ img 폴더를 찾을 수 없습니다: {img_dir}")
        return

    if form_folder:
        print(f"📂 양식 폴더 '{form_folder}' 스캔 중: {img_dir / form_folder}")
    else:
        print(f"📂 img 폴더의 모든 양식 폴더 스캔 중: {img_dir}")

    pages = find_pdf_pages(img_dir, form_folder)
    if not pages:
        print("❌ 처리할 페이지를 찾을 수 없습니다.")
        return

    print(f"✅ {len(pages)}개 페이지 발견\n")

    # RAG Manager 초기화
    print("🔄 RAG Manager 초기화 중...")
    try:
        rag_manager = get_rag_manager()
        print("✅ RAG Manager 초기화 완료\n")
    except Exception as e:
        print(f"❌ RAG Manager 초기화 실패: {e}")
        return

    # DB Manifest Manager 초기화
    manifest = DBManifestManager()

    print(f"📋 DB Manifest 로드: {len(manifest.get_all_page_keys())}개 페이지 등록됨\n")

    # PDF 텍스트 추출기 생성 (캐싱 지원)
    text_extractor = PdfTextExtractor()

    try:
        # 삭제된 페이지 감지
        deleted_pages = detect_deleted_pages(pages, manifest)
        if deleted_pages:
            print(f"🗑️ 삭제된 페이지 감지: {len(deleted_pages)}개")
            for deleted in deleted_pages[:10]:  # 최대 10개만 출력
                print(f"   - {deleted['pdf_filename']} 페이지 {deleted['page_number']}")
            if len(deleted_pages) > 10:
                print(f"   ... 외 {len(deleted_pages) - 10}개")
            manifest.mark_pages_deleted(deleted_pages)

        # manifest와 비교하여 변경분만 필터링
        print(f"🔍 Manifest와 비교하여 변경분 확인 중... (텍스트 추출 방법: {text_extraction_method})")
        new_pages = diff_pages_with_manifest(pages, manifest, text_extractor, text_extraction_method)

        if not new_pages:
            print("✅ 변경된 페이지가 없습니다. 모든 페이지가 최신 상태입니다.\n")
            return

        print(f"📝 변경된 페이지: {len(new_pages)}개 발견\n")

        # 기존 예제 수 확인
        existing_count = rag_manager.count_examples()
        print(f"📊 기존 벡터 DB 예제 수: {existing_count}개\n")

        # shard 생성을 위한 페이지 데이터 준비
        shard_pages = []
        for page_data in new_pages:
            pdf_name = page_data['pdf_name']
            page_num = page_data['page_num']

            metadata = {
                'pdf_name': pdf_name,
                'page_num': page_num,
                'source': 'img_folder'
            }

            shard_pages.append({
                'pdf_name': pdf_name,
                'page_num': page_num,
                'ocr_text': page_data['ocr_text'],
                'answer_json': page_data['answer_json'],
                'metadata': metadata,
                'page_key': page_data['page_key'],
                'page_hash': page_data['page_hash']
            })

        # shard FAISS DB 생성
        print("🔨 Shard 생성 중...")
        result = rag_manager.build_shard(shard_pages)

        if not result:
            print("❌ Shard 생성 실패")
            return

        # result는 (shard_path 또는 shard_index_name, shard_id) 튜플
        shard_identifier, shard_id = result

        # shard 생성 시 manifest 즉시 업데이트 (staged 상태)
        print("\n📋 DB Manifest에 staged 상태 기록 중...")
        page_hashes = {p['page_key']: p['page_hash'] for p in new_pages}
        fingerprints = {p['page_key']: p['fingerprint'] for p in new_pages}

        # DB용 페이지 정보 리스트 생성
        db_pages = [
            {
                'pdf_filename': p['pdf_filename'],
                'page_number': p['page_num']
            }
            for p in new_pages
        ]

        manifest.mark_pages_staged(db_pages, shard_id, page_hashes, fingerprints)
        print(f"✅ DB Manifest 업데이트 완료: {len(db_pages)}개 페이지 staged 상태로 기록\n")

        # shard → base merge
        if auto_merge:
            print("🔄 Shard를 base에 merge 중...")
            # shard_identifier는 DB 모드에서는 index_name, 파일 모드에서는 파일 경로
            merge_success = rag_manager.merge_shard(shard_identifier)

            if merge_success:
                # merge 성공 시 상태 전이 (staged → merged)
                print("\n📋 DB Manifest 상태 전이 중 (staged → merged)...")
                manifest.mark_pages_merged(db_pages)
                print(f"✅ DB Manifest 상태 전이 완료: {len(db_pages)}개 페이지 merged 상태로 변경\n")
            else:
                print("❌ Shard merge 실패 (staged 상태 유지)\n")
                return
        else:
            print(f"\n⚠️ 자동 merge가 비활성화되어 있습니다.")
            print(f"   수동으로 merge하려면: rag_manager.merge_shard('{shard_identifier}')\n")
            print(f"   merge 후 manifest.mark_pages_merged(db_pages)를 호출하세요.\n")

        # 결과 요약
        print("="*60)
        print("📊 벡터 DB 구축 결과")
        print("="*60)
        print(f"✅ 처리된 페이지: {len(new_pages)}개")
        print(f"📈 기존 벡터 DB 예제 수: {existing_count}개")
        print(f"💾 최종 벡터 DB 예제 수: {rag_manager.count_examples()}개")
        print(f"📋 DB Manifest 등록 페이지 수: {len(manifest.get_all_page_keys())}개")
        if deleted_pages:
            print(f"🗑️ 삭제된 페이지: {len(deleted_pages)}개")
        print("="*60)
    finally:
        # PDF 캐시 정리
        text_extractor.close_all()


if __name__ == "__main__":
    import sys
    print("🚀 FAISS 벡터 DB 구축 시작\n")

    # 명령줄 인자로 양식 폴더 지정 가능
    form_folder = None
    if len(sys.argv) > 1:
        form_folder = sys.argv[1]
        print(f"📁 지정된 양식 폴더: {form_folder}\n")

    build_faiss_db(
        form_folder=form_folder,
        auto_merge=True,
        text_extraction_method="excel"  # 기본 PyMuPDF 방법 (엑셀 변환은 "excel")
    )
    print("\n✅ 완료!")
