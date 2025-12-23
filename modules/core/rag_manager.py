"""
RAG (Retrieval-Augmented Generation) 관리 모듈

FAISS를 사용하여 OCR 텍스트와 정답 JSON 쌍을 저장하고 검색합니다.
"""

import os
import json
import pickle
import numpy as np
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import faiss


class RAGManager:
    """
    RAG 벡터 DB 관리 클래스
    
    FAISS를 사용하여 OCR 텍스트를 임베딩하고 검색합니다.
    """
    
    def __init__(self, persist_directory: Optional[str] = None):
        """
        RAG Manager 초기화
        
        Args:
            persist_directory: 벡터 DB 저장 디렉토리 (None이면 프로젝트 루트/faiss_db)
        """
        if persist_directory is None:
            from modules.utils.config import get_project_root
            project_root = get_project_root()
            persist_directory = str(project_root / "faiss_db")
        
        self.persist_directory = persist_directory
        
        # 디렉토리 생성 및 권한 설정
        os.makedirs(persist_directory, exist_ok=True, mode=0o755)
        
        # 파일 경로
        self.index_path = os.path.join(persist_directory, "faiss.index")
        self.metadata_path = os.path.join(persist_directory, "metadata.json")
        
        # 임베딩 모델 초기화 (지연 로딩)
        self._embedding_model = None
        
        # FAISS 인덱스 및 메타데이터 로드
        self.index = None
        self.metadata = {}  # {doc_id: {ocr_text, answer_json, metadata}}
        self.id_to_index = {}  # {doc_id: faiss_index}
        self.index_to_id = {}  # {faiss_index: doc_id}
        self._load_index()
        
        # BM25 인덱스 초기화 (지연 로딩)
        self._bm25_index = None
        self._bm25_texts = None
        self._bm25_example_map = None
    
    def _get_embedding_model(self):
        """임베딩 모델 가져오기 (지연 로딩)"""
        if self._embedding_model is None:
            try:
                from sentence_transformers import SentenceTransformer
                # 다국어 모델 사용 (일본어/한국어/영어 지원)
                self._embedding_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
            except ImportError:
                raise ImportError(
                    "sentence-transformers가 설치되지 않았습니다.\n"
                    "다음 명령어로 설치하세요: pip install sentence-transformers"
                )
        return self._embedding_model
    
    def _get_embedding_dim(self) -> int:
        """임베딩 차원 반환"""
        model = self._get_embedding_model()
        # 테스트 임베딩으로 차원 확인
        test_embedding = model.encode(["test"], convert_to_numpy=True)
        return test_embedding.shape[1]
    
    def _load_index(self):
        """FAISS 인덱스 및 메타데이터 로드"""
        embedding_dim = self._get_embedding_dim()
        
        # FAISS 인덱스 로드
        if os.path.exists(self.index_path):
            try:
                self.index = faiss.read_index(self.index_path)
            except Exception as e:
                print(f"⚠️ FAISS 인덱스 로드 실패, 새로 생성: {e}")
                self.index = faiss.IndexFlatL2(embedding_dim)
        else:
            self.index = faiss.IndexFlatL2(embedding_dim)
        
        # 메타데이터 로드
        if os.path.exists(self.metadata_path):
            try:
                with open(self.metadata_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.metadata = data.get("metadata", {})
                    self.id_to_index = data.get("id_to_index", {})
                    index_to_id_raw = data.get("index_to_id", {})
                    
                    # JSON에서 로드하면 키가 문자열이므로 정수로 변환
                    self.index_to_id = {int(k): v for k, v in index_to_id_raw.items()}
                    
                    # index_to_id 매핑이 불완전하면 id_to_index로 재구축
                    if len(self.index_to_id) < len(self.id_to_index):
                        print(f"⚠️ index_to_id 매핑 불완전, 재구축 중... ({len(self.index_to_id)}/{len(self.id_to_index)})")
                        self.index_to_id = {idx: doc_id for doc_id, idx in self.id_to_index.items()}
                        self._save_index()  # 재구축된 매핑 저장
            except Exception as e:
                print(f"⚠️ 메타데이터 로드 실패: {e}")
                self.metadata = {}
                self.id_to_index = {}
                self.index_to_id = {}
    
    def _save_index(self):
        """FAISS 인덱스 및 메타데이터 저장"""
        try:
            # FAISS 인덱스 저장
            faiss.write_index(self.index, self.index_path)
            
            # 메타데이터 저장
            data = {
                "metadata": self.metadata,
                "id_to_index": self.id_to_index,
                "index_to_id": self.index_to_id
            }
            with open(self.metadata_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ 인덱스 저장 실패: {e}")
    
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
        
        # 문서 ID 생성
        doc_id = str(uuid.uuid4())
        
        # 임베딩 생성
        model = self._get_embedding_model()
        processed_text = self.preprocess_ocr_text(ocr_text)
        embedding = model.encode([processed_text], convert_to_numpy=True).astype('float32')
        
        # FAISS 인덱스에 추가
        faiss_index = self.index.ntotal
        self.index.add(embedding)
        
        # 메타데이터 저장
        self.metadata[doc_id] = {
            "ocr_text": ocr_text,
            "answer_json": answer_json,
            "metadata": metadata or {}
        }
        self.id_to_index[doc_id] = faiss_index
        self.index_to_id[faiss_index] = doc_id
        
        # 저장
        self._save_index()
        
        # BM25 인덱스 새로고침
        self._refresh_bm25_index()
        
        return doc_id
    
    def get_all_examples(self) -> List[Dict[str, Any]]:
        """
        모든 예제 조회
        
        Returns:
            예제 리스트
        """
        examples = []
        for doc_id, data in self.metadata.items():
            examples.append({
                "id": doc_id,
                "ocr_text": data.get("ocr_text", ""),
                "answer_json": data.get("answer_json", {}),
                "metadata": data.get("metadata", {})
            })
        return examples
    
    def delete_example(self, doc_id: str) -> bool:
        """
        예제 삭제 (FAISS는 삭제를 지원하지 않으므로 메타데이터만 제거)
        
        Args:
            doc_id: 문서 ID
            
        Returns:
            삭제 성공 여부
        """
        if doc_id in self.metadata:
            faiss_index = self.id_to_index.get(doc_id)
            if faiss_index is not None:
                del self.index_to_id[faiss_index]
            del self.id_to_index[doc_id]
            del self.metadata[doc_id]
            self._save_index()
            self._refresh_bm25_index()
            return True
        return False
    
    def count_examples(self) -> int:
        """
        저장된 예제 수 반환
        
        Returns:
            예제 수
        """
        return len(self.metadata)
    
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
        tokens = re.findall(r'\b\w+\b|[가-힣]+|[ひらがなカタカナ]+|[一-龠]+', text)
        return tokens
    
    # ============================================
    # BM25 인덱스 관리
    # ============================================
    
    def _build_bm25_index(self):
        """BM25 인덱스 구축 (지연 로딩)"""
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
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
        
        for example in all_examples:
            ocr_text = example.get("ocr_text", "")
            doc_id = example.get("id", "")
            
            if not doc_id:
                continue
            
            # 전처리된 텍스트 토큰화
            preprocessed = self.preprocess_ocr_text(ocr_text)
            tokens = self._tokenize(preprocessed)
            
            if tokens:  # 토큰이 있는 경우만 추가
                self._bm25_texts.append(tokens)
                self._bm25_example_map[doc_id] = len(self._bm25_texts) - 1
        
        # BM25 인덱스 생성
        if self._bm25_texts:
            self._bm25_index = BM25Okapi(self._bm25_texts)
        else:
            self._bm25_index = None
    
    def _refresh_bm25_index(self):
        """BM25 인덱스 새로고침 (예제 추가/삭제 후 호출)"""
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
        기본 벡터 검색
        
        Args:
            query_text: 검색 쿼리 텍스트 (OCR 텍스트)
            top_k: 반환할 최대 결과 수
            similarity_threshold: 최소 유사도 임계값 (0.0 ~ 1.0)
            use_preprocessing: OCR 텍스트 전처리 사용 여부
            
        Returns:
            검색 결과 리스트
        """
        if self.index.ntotal == 0:
            return []
        
        # 전처리 적용
        processed_query = self.preprocess_ocr_text(query_text) if use_preprocessing else query_text
        
        # 임베딩 생성
        model = self._get_embedding_model()
        query_embedding = model.encode([processed_query], convert_to_numpy=True).astype('float32')
        
        # FAISS 검색
        k = min(top_k * 2, self.index.ntotal)  # 더 많이 검색 후 필터링
        distances, indices = self.index.search(query_embedding, k)
        
        # 결과 파싱
        results = []
        seen_doc_ids = set()
        
        for i, (distance, idx) in enumerate(zip(distances[0], indices[0])):
            if idx == -1:  # FAISS에서 유효하지 않은 인덱스
                continue
            
            doc_id = self.index_to_id.get(idx)
            if not doc_id or doc_id in seen_doc_ids:
                continue
            seen_doc_ids.add(doc_id)
            
            # 유사도 계산 (L2 거리를 유사도로 변환)
            # L2 거리는 보통 0~수백 범위이므로, 더 나은 변환 공식 사용
            # 거리가 작을수록 유사도가 높아야 하므로 역변환
            # 거리 0 -> 유사도 1.0, 거리 증가 -> 유사도 감소
            similarity = max(0.0, 1.0 - (distance / 100.0))  # 거리 100을 기준으로 정규화
            
            # 임계값 체크
            if similarity < similarity_threshold:
                continue
            
            # 메타데이터 가져오기
            data = self.metadata.get(doc_id, {})
            
            results.append({
                "ocr_text": data.get("ocr_text", ""),
                "answer_json": data.get("answer_json", {}),
                "similarity": similarity,
                "distance": float(distance),
                "id": doc_id
            })
        
        # 유사도로 정렬
        results.sort(key=lambda x: x["similarity"], reverse=True)
        
        return results[:top_k]
    
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
        
        # 벡터 검색 (더 많은 후보)
        vector_results = self.search_vector_only(
            query_text, top_k * 3, 0.0, use_preprocessing  # threshold 무시
        )
        
        # BM25 검색
        processed_query = self.preprocess_ocr_text(query_text) if use_preprocessing else query_text
        query_tokens = self._tokenize(processed_query)
        
        if not query_tokens:
            return self.search_vector_only(query_text, top_k, similarity_threshold, use_preprocessing)
        
        bm25_scores_list = self._bm25_index.get_scores(query_tokens)
        
        # doc_id -> BM25 점수 매핑
        bm25_scores = {}
        for doc_id, bm25_idx in self._bm25_example_map.items():
            if bm25_idx < len(bm25_scores_list):
                bm25_scores[doc_id] = bm25_scores_list[bm25_idx]
        
        # 하이브리드 점수 계산
        hybrid_results = []
        candidate_bm25_scores = [bm25_scores.get(r["id"], 0.0) for r in vector_results]
        
        if candidate_bm25_scores:
            max_bm25 = max(candidate_bm25_scores)
            min_bm25 = min(candidate_bm25_scores)
        else:
            max_bm25 = 1.0
            min_bm25 = 0.0
        
        for result in vector_results:
            doc_id = result["id"]
            vector_similarity = result["similarity"]
            
            # BM25 점수 정규화
            bm25_score = bm25_scores.get(doc_id, 0.0)
            if max_bm25 > min_bm25:
                normalized_bm25 = (bm25_score - min_bm25) / (max_bm25 - min_bm25)
            elif max_bm25 == min_bm25 and max_bm25 > 0:
                normalized_bm25 = 1.0
            else:
                normalized_bm25 = 0.0
            
            # 하이브리드 점수
            hybrid_score = hybrid_alpha * vector_similarity + (1 - hybrid_alpha) * normalized_bm25
            
            if hybrid_score < similarity_threshold:
                continue
            
            result["bm25_score"] = normalized_bm25
            result["hybrid_score"] = hybrid_score
            hybrid_results.append(result)
        
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
            return self.search_vector_only(query_text, top_k, similarity_threshold, use_preprocessing)
        
        # 벡터 검색
        candidates = self.search_vector_only(query_text, rerank_top_n, 0.0, use_preprocessing)
        
        if len(candidates) <= 1:
            return candidates[:top_k]
        
        # Cross-encoder로 재정렬
        if rerank_model is None:
            rerank_model = 'cross-encoder/ms-marco-MiniLM-L-6-v2'
        
        try:
            reranker = CrossEncoder(rerank_model)
        except Exception:
            return candidates[:top_k]
        
        processed_query = self.preprocess_ocr_text(query_text) if use_preprocessing else query_text
        pairs = [[processed_query, cand["ocr_text"]] for cand in candidates]
        rerank_scores = reranker.predict(pairs)
        
        if isinstance(rerank_scores, np.ndarray):
            rerank_scores = rerank_scores.tolist()
        
        rerank_scores_float = [float(score) for score in rerank_scores]
        
        if not rerank_scores_float:
            return candidates[:top_k]
        
        max_rerank = max(rerank_scores_float)
        min_rerank = min(rerank_scores_float)
        
        for i, cand in enumerate(candidates):
            if i >= len(rerank_scores_float):
                continue
            
            rerank_score_raw = rerank_scores_float[i]
            if max_rerank > min_rerank:
                normalized_rerank = (rerank_score_raw - min_rerank) / (max_rerank - min_rerank)
            elif max_rerank == min_rerank and max_rerank != 0:
                normalized_rerank = 1.0
            else:
                normalized_rerank = 0.0
            
            cand["rerank_score"] = normalized_rerank
            cand["rerank_score_raw"] = rerank_score_raw
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
