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
            from modules.utils.config import get_project_root
            project_root = get_project_root()
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
        
        # BM25 인덱스 초기화 (지연 로딩)
        self._bm25_index = None
        self._bm25_texts = None
        self._bm25_example_map = None  # doc_id -> example 인덱스 매핑
    
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
                # BM25 인덱스 새로고침
                self._refresh_bm25_index()
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
            # BM25 인덱스 새로고침
            self._refresh_bm25_index()
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
    
    # ============================================
    # OCR 텍스트 전처리 함수
    # ============================================
    
    @staticmethod
    def preprocess_ocr_text(ocr_text: str) -> str:
        """
        검색 품질 향상을 위한 OCR 텍스트 전처리
        
        Args:
            ocr_text: 원본 OCR 텍스트
            
        Returns:
            전처리된 OCR 텍스트
        """
        import re
        
        # 1. 불필요한 공백 정리
        text = re.sub(r'\s+', ' ', ocr_text)
        
        # 2. 숫자 정규화 (예: "1,234" -> "1234")
        text = re.sub(r'(\d+),(\d+)', r'\1\2', text)
        
        # 3. 줄바꿈 정리
        text = re.sub(r'\n+', ' ', text)
        
        return text.strip()
    
    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """
        텍스트를 토큰화 (일본어/한국어/영어 혼합 문서용)
        
        Args:
            text: 토큰화할 텍스트
            
        Returns:
            토큰 리스트
        """
        import re
        # 숫자, 일본어, 한국어, 영어를 모두 포함하는 토큰화
        # 간단한 방법: 공백, 줄바꿈, 특수문자 기준으로 분리
        tokens = re.findall(r'\b\w+\b|[가-힣]+|[ひらがなカタカナ]+|[一-龠]+', text)
        return tokens
    
    # ============================================
    # BM25 인덱스 관리
    # ============================================
    
    def _build_bm25_index(self):
        """
        BM25 인덱스 구축 (지연 로딩)
        """
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            # rank-bm25가 설치되지 않은 경우 None으로 설정
            self._bm25_index = None
            return
        
        if self._bm25_index is not None:
            return  # 이미 구축됨
        
        all_examples = self.get_all_examples()
        
        if not all_examples:
            self._bm25_index = None
            return
        
        # OCR 텍스트를 토큰화
        self._bm25_texts = []
        self._bm25_example_map = {}
        
        for idx, example in enumerate(all_examples):
            ocr_text = example.get("ocr_text", "")
            doc_id = example.get("id", "")
            
            if not doc_id:
                continue
            
            # 전처리된 텍스트 토큰화
            preprocessed = self.preprocess_ocr_text(ocr_text)
            tokens = self._tokenize(preprocessed)
            
            if tokens:  # 토큰이 있는 경우만 추가
                self._bm25_texts.append(tokens)
                self._bm25_example_map[doc_id] = len(self._bm25_texts) - 1  # 실제 인덱스 사용
        
        # BM25 인덱스 생성
        if self._bm25_texts:
            self._bm25_index = BM25Okapi(self._bm25_texts)
        else:
            self._bm25_index = None
    
    def _refresh_bm25_index(self):
        """
        BM25 인덱스 새로고침 (예제 추가/삭제 후 호출)
        """
        self._bm25_index = None
        self._bm25_texts = None
        self._bm25_example_map = None
        self._build_bm25_index()
    
    # ============================================
    # 다양한 검색 방식
    # ============================================
    
    def search_vector_only(
        self,
        query_text: str,
        top_k: int = 3,
        similarity_threshold: float = 0.7,
        use_preprocessing: bool = True
    ) -> List[Dict[str, Any]]:
        """
        기본 벡터 검색 (기존 방식)
        
        Args:
            query_text: 검색 쿼리 텍스트 (OCR 텍스트)
            top_k: 반환할 최대 결과 수
            similarity_threshold: 최소 유사도 임계값 (0.0 ~ 1.0)
            use_preprocessing: OCR 텍스트 전처리 사용 여부
            
        Returns:
            검색 결과 리스트
        """
        # 전처리 적용
        processed_query = self.preprocess_ocr_text(query_text) if use_preprocessing else query_text
        
        # 벡터 DB에서 검색
        results = self.collection.query(
            query_texts=[processed_query],
            n_results=top_k
        )
        
        return self._parse_search_results(results, similarity_threshold)
    
    def search_hybrid(
        self,
        query_text: str,
        top_k: int = 3,
        similarity_threshold: float = 0.7,
        hybrid_alpha: float = 0.5,
        use_preprocessing: bool = True
    ) -> List[Dict[str, Any]]:
        """
        하이브리드 검색: BM25 + 벡터 검색 결합
        
        Args:
            query_text: 검색 쿼리 텍스트 (OCR 텍스트)
            top_k: 반환할 최대 결과 수
            similarity_threshold: 최소 유사도 임계값 (0.0 ~ 1.0)
            hybrid_alpha: 하이브리드 가중치 (0.0~1.0, 0.5 = 벡터와 BM25 동일 가중치)
            use_preprocessing: OCR 텍스트 전처리 사용 여부
            
        Returns:
            검색 결과 리스트 (hybrid_score 포함)
        """
        # BM25 인덱스 구축
        self._build_bm25_index()
        
        if self._bm25_index is None:
            # BM25가 사용 불가능하면 벡터 검색만 사용
            return self.search_vector_only(query_text, top_k, similarity_threshold, use_preprocessing)
        
        # 전처리 적용
        processed_query = self.preprocess_ocr_text(query_text) if use_preprocessing else query_text
        
        # 1. 벡터 검색 (더 많은 후보 검색)
        vector_results = self.collection.query(
            query_texts=[processed_query],
            n_results=top_k * 3  # 하이브리드 결합을 위해 더 많이 검색
        )
        
        # 2. BM25 검색
        query_tokens = self._tokenize(processed_query)
        if not query_tokens:
            # 토큰이 없으면 벡터 검색만 사용
            return self.search_vector_only(query_text, top_k, similarity_threshold, use_preprocessing)
        
        bm25_scores_list = self._bm25_index.get_scores(query_tokens)
        
        # doc_id -> BM25 점수 매핑
        # _bm25_example_map을 사용하여 올바른 인덱스로 매핑
        bm25_scores = {}
        all_examples = self.get_all_examples()
        for example in all_examples:
            doc_id = example.get("id", "")
            if not doc_id:
                continue
            # _bm25_example_map에서 실제 BM25 인덱스 가져오기
            bm25_idx = self._bm25_example_map.get(doc_id)
            if bm25_idx is not None and bm25_idx < len(bm25_scores_list):
                bm25_scores[doc_id] = bm25_scores_list[bm25_idx]
        
        # 3. 하이브리드 점수 계산
        hybrid_results = []
        
        if vector_results["ids"] and len(vector_results["ids"][0]) > 0:
            # 먼저 벡터 검색 결과에 포함된 doc_id들의 BM25 점수만 수집
            candidate_bm25_scores = []
            for i, doc_id in enumerate(vector_results["ids"][0]):
                bm25_score = bm25_scores.get(doc_id, 0.0)
                candidate_bm25_scores.append(bm25_score)
            
            # BM25 점수 정규화를 위한 최대값/최소값 계산 (후보들 중에서만)
            if candidate_bm25_scores:
                max_bm25 = max(candidate_bm25_scores)
                min_bm25 = min(candidate_bm25_scores)
            else:
                max_bm25 = 1.0
                min_bm25 = 0.0
            
            for i, doc_id in enumerate(vector_results["ids"][0]):
                # 벡터 거리
                distances = vector_results.get("distances", [[]])
                distance = distances[0][i] if distances and len(distances[0]) > i else 1.0
                vector_similarity = 1.0 - distance
                
                # BM25 점수 (정규화: 0-1 범위)
                bm25_score = bm25_scores.get(doc_id, 0.0)
                if max_bm25 > min_bm25:
                    normalized_bm25 = (bm25_score - min_bm25) / (max_bm25 - min_bm25)
                elif max_bm25 == min_bm25 and max_bm25 > 0:
                    # 모든 점수가 같고 0이 아닌 경우
                    normalized_bm25 = 1.0
                else:
                    # 모든 점수가 0인 경우
                    normalized_bm25 = 0.0
                
                # 하이브리드 점수 (가중 평균)
                hybrid_score = (
                    hybrid_alpha * vector_similarity + 
                    (1 - hybrid_alpha) * normalized_bm25
                )
                
                if hybrid_score < similarity_threshold:
                    continue
                
                # 메타데이터 추출
                metadatas = vector_results.get("metadatas", [[]])
                metadata = metadatas[0][i] if metadatas and len(metadatas[0]) > i else {}
                
                answer_json = json.loads(metadata.get("answer_json", "{}"))
                
                hybrid_results.append({
                    "ocr_text": metadata.get("ocr_text", ""),
                    "answer_json": answer_json,
                    "similarity": vector_similarity,
                    "bm25_score": normalized_bm25,
                    "hybrid_score": hybrid_score,  # 최종 점수
                    "distance": distance,
                    "id": doc_id
                })
        
        # 하이브리드 점수로 정렬
        hybrid_results.sort(key=lambda x: x["hybrid_score"], reverse=True)
        
        return hybrid_results[:top_k]
    
    def search_with_rerank(
        self,
        query_text: str,
        top_k: int = 3,
        similarity_threshold: float = 0.7,
        rerank_top_n: int = 10,
        rerank_model: Optional[str] = None,
        use_preprocessing: bool = True
    ) -> List[Dict[str, Any]]:
        """
        벡터 검색 후 Cross-encoder로 재정렬
        
        Args:
            query_text: 검색 쿼리 텍스트 (OCR 텍스트)
            top_k: 반환할 최대 결과 수
            similarity_threshold: 최소 유사도 임계값 (0.0 ~ 1.0)
            rerank_top_n: 재정렬할 후보 수
            rerank_model: Re-ranking 모델명 (None이면 기본 모델 사용)
            use_preprocessing: OCR 텍스트 전처리 사용 여부
            
        Returns:
            검색 결과 리스트 (rerank_score 포함)
        """
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            # sentence-transformers가 설치되지 않은 경우 벡터 검색만 사용
            return self.search_vector_only(query_text, top_k, similarity_threshold, use_preprocessing)
        
        # 전처리 적용
        processed_query = self.preprocess_ocr_text(query_text) if use_preprocessing else query_text
        
        # 1. 벡터 검색 (더 많은 후보)
        vector_results = self.collection.query(
            query_texts=[processed_query],
            n_results=rerank_top_n
        )
        
        candidates = self._parse_search_results(vector_results, similarity_threshold)
        
        if len(candidates) <= 1:
            return candidates[:top_k]
        
        # 2. Cross-encoder로 재정렬
        if rerank_model is None:
            rerank_model = 'cross-encoder/ms-marco-MiniLM-L-6-v2'
        
        try:
            reranker = CrossEncoder(rerank_model)
        except Exception:
            # 모델 로드 실패 시 벡터 검색 결과 반환
            return candidates[:top_k]
        
        # 쿼리-문서 쌍 생성
        pairs = [[processed_query, cand["ocr_text"]] for cand in candidates]
        
        # 재정렬 점수 계산
        rerank_scores = reranker.predict(pairs)
        
        # rerank_scores를 numpy 배열이나 리스트로 변환
        import numpy as np
        if isinstance(rerank_scores, np.ndarray):
            rerank_scores = rerank_scores.tolist()
        elif not isinstance(rerank_scores, list):
            rerank_scores = list(rerank_scores)
        
        # rerank 점수 정규화 (Cross-encoder 점수는 보통 -10 ~ 10 범위)
        rerank_scores_float = [float(score) for score in rerank_scores]
        
        if not rerank_scores_float:
            # 점수가 없으면 벡터 검색 결과 반환
            return candidates[:top_k]
        
        max_rerank = max(rerank_scores_float)
        min_rerank = min(rerank_scores_float)
        
        # 점수 추가 및 정렬
        for i, cand in enumerate(candidates):
            if i >= len(rerank_scores_float):
                continue
                
            rerank_score_raw = rerank_scores_float[i]
            # rerank_score를 0-1 범위로 정규화
            if max_rerank > min_rerank:
                normalized_rerank = (rerank_score_raw - min_rerank) / (max_rerank - min_rerank)
            elif max_rerank == min_rerank and max_rerank != 0:
                # 모든 점수가 같고 0이 아닌 경우
                normalized_rerank = 1.0
            else:
                # 모든 점수가 0인 경우
                normalized_rerank = 0.0
            
            cand["rerank_score"] = normalized_rerank
            cand["rerank_score_raw"] = rerank_score_raw  # 원본 점수도 저장
            
            # similarity와 rerank_score를 결합한 최종 점수 (둘 다 0-1 범위)
            cand["final_score"] = (cand["similarity"] * 0.3 + normalized_rerank * 0.7)
        
        candidates.sort(key=lambda x: x["final_score"], reverse=True)
        
        return candidates[:top_k]
    
    def search_similar_advanced(
        self,
        query_text: str,
        top_k: int = 3,
        similarity_threshold: float = 0.7,
        search_method: str = "vector",  # "vector", "hybrid", "rerank"
        hybrid_alpha: float = 0.5,
        rerank_top_n: int = 10,
        rerank_model: Optional[str] = None,
        use_preprocessing: bool = True
    ) -> List[Dict[str, Any]]:
        """
        통합 검색 함수 (검색 방식 선택 가능)
        
        Args:
            query_text: 검색 쿼리 텍스트 (OCR 텍스트)
            top_k: 반환할 최대 결과 수
            similarity_threshold: 최소 유사도 임계값 (0.0 ~ 1.0)
            search_method: 검색 방식 ("vector", "hybrid", "rerank")
            hybrid_alpha: 하이브리드 가중치 (hybrid 방식 사용 시)
            rerank_top_n: 재정렬할 후보 수 (rerank 방식 사용 시)
            rerank_model: Re-ranking 모델명 (rerank 방식 사용 시)
            use_preprocessing: OCR 텍스트 전처리 사용 여부
            
        Returns:
            검색 결과 리스트
        """
        if search_method == "hybrid":
            return self.search_hybrid(
                query_text, top_k, similarity_threshold, hybrid_alpha, use_preprocessing
            )
        elif search_method == "rerank":
            return self.search_with_rerank(
                query_text, top_k, similarity_threshold, rerank_top_n, rerank_model, use_preprocessing
            )
        else:  # "vector" 또는 기본값
            return self.search_vector_only(
                query_text, top_k, similarity_threshold, use_preprocessing
            )
    
    # ============================================
    # 내부 헬퍼 함수
    # ============================================
    
    def _parse_search_results(
        self,
        results: Dict[str, Any],
        similarity_threshold: float
    ) -> List[Dict[str, Any]]:
        """
        검색 결과 파싱 (공통 로직)
        
        Args:
            results: ChromaDB 검색 결과
            similarity_threshold: 최소 유사도 임계값
            
        Returns:
            파싱된 검색 결과 리스트
        """
        similar_examples = []
        
        if results["ids"] and len(results["ids"][0]) > 0:
            for i, doc_id in enumerate(results["ids"][0]):
                # 거리 계산
                distances = results.get("distances", [[]])
                distance = distances[0][i] if distances and len(distances[0]) > i else 1.0
                
                # 유사도 계산
                similarity = 1.0 - distance
                
                # 임계값 체크
                if similarity < similarity_threshold:
                    continue
                
                # 메타데이터 추출
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

