"""
RAG (Retrieval-Augmented Generation) 관리 모듈

벡터 DB를 사용하여 OCR 텍스트와 정답 JSON 쌍을 저장하고 검색합니다.
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import chromadb
from chromadb.config import Settings


class RAGManager:
    """
    RAG 벡터 DB 관리 클래스
    
    ChromaDB를 사용하여 OCR 텍스트를 임베딩하고 검색합니다.
    """
    
    def __init__(self, persist_directory: Optional[str] = None):
        """
        RAG Manager 초기화
        
        Args:
            persist_directory: 벡터 DB 저장 디렉토리 (None이면 프로젝트 루트/chroma_db)
        """
        if persist_directory is None:
            project_root = Path(__file__).parent.parent.parent
            persist_directory = str(project_root / "chroma_db")
        
        # 디렉토리 생성 및 권한 설정
        os.makedirs(persist_directory, exist_ok=True, mode=0o755)
        
        # 디렉토리 쓰기 권한 확인
        if not os.access(persist_directory, os.W_OK):
            raise PermissionError(
                f"벡터 DB 디렉토리에 쓰기 권한이 없습니다: {persist_directory}\n"
                f"다음 명령어로 권한을 수정하세요: chmod -R 755 {persist_directory}"
            )
        
        # ChromaDB 내부 디렉토리 및 파일 권한 확인
        # ChromaDB는 내부적으로 SQLite를 사용하므로 데이터베이스 파일에 쓰기 권한이 필요
        chromadb_data_dir = Path(persist_directory)
        if chromadb_data_dir.exists():
            # 디렉토리 내부 파일들의 쓰기 권한 확인
            try:
                test_file = chromadb_data_dir / ".write_test"
                try:
                    test_file.touch()
                    test_file.unlink()
                except (PermissionError, OSError):
                    raise PermissionError(
                        f"벡터 DB 디렉토리 내부 파일에 쓰기 권한이 없습니다: {persist_directory}\n"
                        f"다음 명령어로 권한을 수정하세요: chmod -R 755 {persist_directory}"
                    )
            except Exception:
                pass  # 테스트 파일 생성 실패는 무시
        
        # ChromaDB 클라이언트 초기화
        self.client = chromadb.PersistentClient(
            path=persist_directory,
            settings=Settings(anonymized_telemetry=False)
        )
        
        # 컬렉션 이름
        self.collection_name = "ocr_examples"
        
        # 컬렉션 가져오기 또는 생성
        try:
            self.collection = self.client.get_collection(name=self.collection_name)
        except Exception as e:
            # 컬렉션이 없으면 생성
            try:
                self.collection = self.client.create_collection(
                    name=self.collection_name,
                    metadata={"description": "OCR 텍스트와 정답 JSON 예제 저장소"}
                )
            except Exception as create_error:
                # 생성 실패 시 권한 문제일 수 있음
                error_msg = str(create_error)
                if "readonly" in error_msg.lower() or "permission" in error_msg.lower():
                    raise PermissionError(
                        f"벡터 DB 컬렉션 생성 실패 (권한 문제): {error_msg}\n"
                        f"디렉토리 경로: {persist_directory}\n"
                        f"해결 방법: 터미널에서 `chmod -R 755 {persist_directory}` 실행"
                    )
                raise
    
    def add_example(
        self,
        ocr_text: str,
        answer_json: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        예제 추가 (OCR 텍스트만 임베딩)
        
        Args:
            ocr_text: OCR 추출 결과 텍스트 (임베딩 대상)
            answer_json: 정답 JSON 딕셔너리 (payload에 저장)
            metadata: 추가 메타데이터 (예: pdf_name, page_num 등)
            
        Returns:
            추가된 문서의 ID
        """
        import uuid
        import time
        
        # 문서 ID 생성
        doc_id = str(uuid.uuid4())
        
        # 메타데이터 구성
        doc_metadata = metadata.copy() if metadata else {}
        doc_metadata["ocr_text"] = ocr_text  # payload에 포함
        doc_metadata["answer_json"] = json.dumps(answer_json, ensure_ascii=False)  # payload에 포함
        
        # OCR 텍스트만 임베딩 (answer_json은 임베딩하지 않음)
        # 재시도 로직 추가 (권한 문제나 잠금 문제 해결)
        max_retries = 3
        retry_delay = 0.5
        
        for attempt in range(max_retries):
            try:
                self.collection.add(
                    ids=[doc_id],
                    documents=[ocr_text],  # 임베딩 대상: OCR 텍스트만
                    metadatas=[doc_metadata]  # 메타데이터에 answer_json 포함
                )
                return doc_id
            except Exception as e:
                error_msg = str(e)
                if "readonly" in error_msg.lower() or "permission" in error_msg.lower():
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (attempt + 1))
                        continue
                    else:
                        # persist_directory를 사용하여 명확한 에러 메시지 제공
                        db_path = getattr(self, 'persist_directory', '알 수 없음')
                        
                        # 권한 자동 수정 시도 (선택적)
                        try:
                            import stat
                            import shutil
                            
                            # 1. 디렉토리 및 하위 파일들의 권한 수정 시도
                            for root, dirs, files in os.walk(db_path):
                                try:
                                    os.chmod(root, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
                                except Exception:
                                    pass
                                
                                for file in files:
                                    file_path = os.path.join(root, file)
                                    try:
                                        # SQLite 파일은 쓰기 권한이 필수
                                        os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
                                    except Exception:
                                        pass
                            
                            # 2. ChromaDB 내부 SQLite 파일 확인 및 권한 수정
                            # ChromaDB는 보통 chroma.sqlite3 같은 파일을 생성
                            sqlite_files = list(Path(db_path).rglob("*.sqlite3"))
                            sqlite_files.extend(list(Path(db_path).rglob("*.sqlite")))
                            sqlite_files.extend(list(Path(db_path).rglob("*.db")))
                            
                            for sqlite_file in sqlite_files:
                                try:
                                    os.chmod(str(sqlite_file), stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
                                except Exception:
                                    pass
                            
                            # 3. 권한 수정 후 재시도
                            self.collection.add(
                                ids=[doc_id],
                                documents=[ocr_text],
                                metadatas=[doc_metadata]
                            )
                            return doc_id
                        except Exception as retry_error:
                            # 컬렉션 재생성 시도 (마지막 시도)
                            try:
                                # 기존 컬렉션 삭제 시도
                                try:
                                    self.client.delete_collection(name=self.collection_name)
                                except Exception:
                                    pass
                                
                                # 새 컬렉션 생성
                                self.collection = self.client.create_collection(
                                    name=self.collection_name,
                                    metadata={"description": "OCR 텍스트와 정답 JSON 예제 저장소"}
                                )
                                
                                # 재시도
                                self.collection.add(
                                    ids=[doc_id],
                                    documents=[ocr_text],
                                    metadatas=[doc_metadata]
                                )
                                return doc_id
                            except Exception as final_error:
                                # 모든 시도 실패 시 명확한 에러 메시지 제공
                                # ChromaDB 내부 파일 목록 확인
                                file_list = []
                                try:
                                    for root, dirs, files in os.walk(db_path):
                                        for file in files:
                                            file_path = os.path.join(root, file)
                                            try:
                                                file_stat = os.stat(file_path)
                                                file_list.append(f"  {file_path} (권한: {oct(file_stat.st_mode)[-3:]})")
                                            except Exception:
                                                pass
                                except Exception:
                                    pass
                                
                                file_info = "\n".join(file_list[:10])  # 최대 10개만 표시
                                if len(file_list) > 10:
                                    file_info += f"\n  ... 외 {len(file_list) - 10}개 파일"
                                
                                raise PermissionError(
                                    f"벡터 DB 저장 실패 (권한 문제): {error_msg}\n"
                                    f"디렉토리 경로: {db_path}\n"
                                    f"\n디렉토리 내 파일:\n{file_info if file_list else '  (파일 없음)'}\n"
                                    f"\n해결 방법:\n"
                                    f"1. 터미널에서 다음 명령어를 실행하세요:\n"
                                    f"   chmod -R 755 {db_path}\n"
                                    f"   find {db_path} -type f -exec chmod 644 {{}} \\;\n"
                                    f"\n2. 또는 chroma_db 디렉토리를 삭제하고 다시 시도하세요:\n"
                                    f"   rm -rf {db_path}\n"
                                    f"   (다음 실행 시 자동으로 재생성됩니다)"
                                )
                else:
                    # 다른 종류의 오류는 즉시 재발생
                    raise
    
    def search_similar(
        self,
        query_text: str,
        top_k: int = 2,
        similarity_threshold: float = 0.7
    ) -> List[Dict[str, Any]]:
        """
        유사한 예제 검색
        
        Args:
            query_text: 검색 쿼리 텍스트 (OCR 텍스트)
            top_k: 반환할 최대 결과 수
            similarity_threshold: 최소 유사도 임계값 (0.0 ~ 1.0)
            
        Returns:
            검색 결과 리스트 (각 항목은 {"ocr_text": ..., "answer_json": ..., "distance": ...} 형태)
        """
        # 벡터 DB에서 검색
        results = self.collection.query(
            query_texts=[query_text],
            n_results=top_k
        )
        
        # 결과 파싱
        similar_examples = []
        
        if results["ids"] and len(results["ids"][0]) > 0:
            for i, doc_id in enumerate(results["ids"][0]):
                # 거리 계산 (ChromaDB는 distance를 반환, 0에 가까울수록 유사)
                distances = results.get("distances", [[]])
                distance = distances[0][i] if distances and len(distances[0]) > i else 1.0
                
                # 유사도 계산 (distance를 similarity로 변환: 1 - distance)
                similarity = 1.0 - distance
                
                # 임계값 체크
                if similarity < similarity_threshold:
                    continue
                
                # 메타데이터에서 OCR 텍스트와 정답 JSON 추출
                metadatas = results.get("metadatas", [[]])
                metadata = metadatas[0][i] if metadatas and len(metadatas[0]) > i else {}
                
                ocr_text = metadata.get("ocr_text", "")
                answer_json_str = metadata.get("answer_json", "{}")
                
                try:
                    answer_json = json.loads(answer_json_str)
                except json.JSONDecodeError:
                    answer_json = {}
                
                similar_examples.append({
                    "ocr_text": ocr_text,
                    "answer_json": answer_json,
                    "similarity": similarity,
                    "distance": distance,
                    "id": doc_id
                })
        
        return similar_examples
    
    def get_all_examples(self) -> List[Dict[str, Any]]:
        """
        모든 예제 조회
        
        Returns:
            예제 리스트
        """
        results = self.collection.get()
        
        examples = []
        for i, doc_id in enumerate(results["ids"]):
            metadata = results["metadatas"][i] if results["metadatas"] else {}
            ocr_text = metadata.get("ocr_text", "")
            answer_json_str = metadata.get("answer_json", "{}")
            
            try:
                answer_json = json.loads(answer_json_str)
            except json.JSONDecodeError:
                answer_json = {}
            
            examples.append({
                "id": doc_id,
                "ocr_text": ocr_text,
                "answer_json": answer_json,
                "metadata": {k: v for k, v in metadata.items() if k not in ["ocr_text", "answer_json"]}
            })
        
        return examples
    
    def delete_example(self, doc_id: str) -> bool:
        """
        예제 삭제
        
        Args:
            doc_id: 문서 ID
            
        Returns:
            삭제 성공 여부
        """
        try:
            self.collection.delete(ids=[doc_id])
            return True
        except Exception:
            return False
    
    def count_examples(self) -> int:
        """
        저장된 예제 수 반환
        
        Returns:
            예제 수
        """
        return self.collection.count()


# 전역 RAG Manager 인스턴스 (싱글톤 패턴)
_rag_manager: Optional[RAGManager] = None


def get_rag_manager() -> RAGManager:
    """
    전역 RAG Manager 인스턴스 반환
    
    Returns:
        RAGManager 인스턴스
    """
    global _rag_manager
    if _rag_manager is None:
        _rag_manager = RAGManager()
    return _rag_manager

