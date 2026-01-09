"""
RAG (Retrieval-Augmented Generation) 관리 모듈

FAISS를 사용하여 OCR 텍스트와 정답 JSON 쌍을 저장하고 검색합니다.
"""

import os
import json
import numpy as np
from typing import Dict, Any, List, Optional, Tuple
from threading import Lock
import faiss


class RAGManager:
    """
    RAG 벡터 DB 관리 클래스
    
    FAISS를 사용하여 OCR 텍스트를 임베딩하고 검색합니다.
    """
    
    # 클래스 레벨 락 (모델 로딩 동기화용)
    _model_lock = Lock()
    
    def __init__(self, persist_directory: Optional[str] = None, use_db: bool = True):
        """
        RAG Manager 초기화
        
        Args:
            persist_directory: 벡터 DB 저장 디렉토리 (None이면 프로젝트 루트/rag_db, use_db=True일 때는 사용 안 함)
            use_db: True면 DB에 저장, False면 로컬 파일에 저장 (기본값: True)
        """
        self.use_db = use_db
        
        if persist_directory is None:
            from modules.utils.config import get_project_root
            project_root = get_project_root()
            persist_directory = str(project_root / "rag_db")
        
        self.persist_directory = persist_directory
        
        # DB 연결 (use_db=True일 때만)
        if self.use_db:
            from database.registry import get_db
            import psycopg2
            self.db = get_db()
            self._ensure_vector_index_table_exists()  # 테이블이 없으면 자동 생성
        else:
            # 로컬 파일 모드: 디렉토리 생성 및 권한 설정
            os.makedirs(persist_directory, exist_ok=True, mode=0o755)
            
            # 파일 경로 (rag_db 구조)
            self.base_index_path = os.path.join(persist_directory, "base.faiss")
            self.base_metadata_path = os.path.join(persist_directory, "base_metadata.json")
            self.index_path = self.base_index_path
            self.metadata_path = self.base_metadata_path
        
        # shard 디렉토리 (파일 모드 fallback을 위해 항상 초기화)
        self.shards_dir = os.path.join(persist_directory, "shards")
        if not self.use_db:
            os.makedirs(self.shards_dir, exist_ok=True, mode=0o755)
        
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
        """임베딩 모델 가져오기 (지연 로딩, 스레드 안전)"""
        # 이중 체크 락킹 패턴 사용
        if self._embedding_model is None:
            with RAGManager._model_lock:  # 클래스 레벨 락 사용
                # 다시 확인 (다른 스레드가 이미 로드했을 수 있음)
                if self._embedding_model is None:
                    try:
                        # tokenizers 병렬 처리 경고 방지 (멀티프로세싱 환경에서 안전)
                        # 모델 로딩 전에 설정
                        os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
                        
                        from sentence_transformers import SentenceTransformer
                        # 다국어 모델 사용 (일본어/한국어/영어 지원)
                        # device 파라미터 제거 - sentence-transformers가 자동으로 디바이스 선택
                        # 명시적 device 설정은 메타 텐서 문제를 일으킬 수 있음
                        self._embedding_model = SentenceTransformer(
                            'paraphrase-multilingual-MiniLM-L12-v2'
                            # device 파라미터 제거 - 자동 디바이스 선택
                        )
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
        """FAISS 인덱스 및 메타데이터 로드 (DB 또는 파일)"""
        embedding_dim = self._get_embedding_dim()
        
        if self.use_db:
            # DB에서 로드
            self.index, self.metadata, self.id_to_index, self.index_to_id = self._load_index_from_db()
            
            # 인덱스가 없으면 새로 생성
            if self.index is None:
                self.index = faiss.IndexFlatL2(embedding_dim)
                self.metadata = {}
                self.id_to_index = {}
                self.index_to_id = {}
        else:
            # 파일에서 로드 (기존 방식)
            if os.path.exists(self.base_index_path):
                try:
                    self.index = faiss.read_index(self.base_index_path)
                except Exception as e:
                    print(f"⚠️ FAISS 인덱스 로드 실패, 새로 생성: {e}")
                    self.index = faiss.IndexFlatL2(embedding_dim)
            else:
                self.index = faiss.IndexFlatL2(embedding_dim)
            
            # 메타데이터 로드
            self.metadata, self.id_to_index, self.index_to_id = self._load_metadata_from_file(self.metadata_path)
        
        # index_to_id 매핑이 불완전하면 id_to_index로 재구축
        if len(self.index_to_id) < len(self.id_to_index):
            print(f"⚠️ index_to_id 매핑 불완전, 재구축 중... ({len(self.index_to_id)}/{len(self.id_to_index)})")
            self.index_to_id = {idx: doc_id for doc_id, idx in self.id_to_index.items()}
            self._save_index()  # 재구축된 매핑 저장
    
    def _ensure_vector_index_table_exists(self):
        """rag_vector_index 테이블이 존재하는지 확인하고 없으면 생성"""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                # 테이블 존재 여부 확인
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'rag_vector_index'
                    )
                """)
                table_exists = cursor.fetchone()[0]
                
                if not table_exists:
                    print("📋 RAG 벡터 인덱스 테이블이 없습니다. 생성 중...")
                    # rag_vector_index 테이블 생성
                    cursor.execute("""
                        CREATE TABLE rag_vector_index (
                            index_id SERIAL PRIMARY KEY,
                            index_name VARCHAR(100) NOT NULL DEFAULT 'base',
                            index_data BYTEA NOT NULL,
                            metadata_json JSONB NOT NULL,
                            index_size BIGINT,
                            vector_count INTEGER DEFAULT 0,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(index_name)
                        )
                    """)
                    # 인덱스 생성
                    cursor.execute("""
                        CREATE INDEX idx_rag_vector_index_name 
                        ON rag_vector_index(index_name)
                    """)
                    print("✅ RAG 벡터 인덱스 테이블 생성 완료")
        except Exception as e:
            print(f"⚠️ 테이블 생성 중 오류 발생: {e}")
            # 오류가 발생해도 계속 진행 (테이블이 이미 존재할 수 있음)
    
    def _load_index_from_db(self, exclude_shard_name: Optional[str] = None) -> Tuple[Optional[Any], Dict[str, Any], Dict[str, int], Dict[int, str]]:
        """
        DB에서 FAISS 인덱스 및 메타데이터 로드 (base 우선, 없으면 shard 병합)
        
        Args:
            exclude_shard_name: 병합에서 제외할 shard 이름 (merge_shard에서 사용)
        
        Returns:
            (index, metadata, id_to_index, index_to_id) 튜플
        """
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                # 1. base 인덱스 우선 시도
                cursor.execute("""
                    SELECT index_data, metadata_json, vector_count
                    FROM rag_vector_index
                    WHERE index_name = 'base'
                    ORDER BY updated_at DESC
                    LIMIT 1
                """)
                
                row = cursor.fetchone()
                if row:
                    index_data_bytes = row[0]  # BYTEA (memoryview일 수 있음)
                    metadata_json = row[1]  # JSONB
                    vector_count = row[2] or 0
                    
                    # BYTEA를 numpy 배열로 변환 (faiss.deserialize_index는 numpy 배열 필요)
                    if isinstance(index_data_bytes, memoryview):
                        index_data_bytes = np.frombuffer(index_data_bytes, dtype=np.uint8)
                    elif isinstance(index_data_bytes, bytes):
                        index_data_bytes = np.frombuffer(index_data_bytes, dtype=np.uint8)
                    else:
                        index_data_bytes = np.frombuffer(bytes(index_data_bytes), dtype=np.uint8)
                    
                    # FAISS 인덱스를 바이트에서 로드
                    index = faiss.deserialize_index(index_data_bytes)
                    
                    # 메타데이터 파싱
                    metadata = metadata_json.get('metadata', {})
                    id_to_index = metadata_json.get('id_to_index', {})
                    index_to_id_raw = metadata_json.get('index_to_id', {})
                    # JSON에서 로드하면 키가 문자열이므로 정수로 변환
                    index_to_id = {int(k): v for k, v in index_to_id_raw.items()}
                    
                    print(f"✅ DB에서 base 인덱스 로드 완료 ({vector_count}개 벡터)")
                    return index, metadata, id_to_index, index_to_id
                
                # 2. base가 없으면 모든 shard를 병합하여 로드 (제외할 shard 제외)
                if exclude_shard_name:
                    cursor.execute("""
                        SELECT index_data, metadata_json, vector_count, index_name
                        FROM rag_vector_index
                        WHERE index_name LIKE 'shard_%' AND index_name != %s
                        ORDER BY updated_at DESC
                    """, (exclude_shard_name,))
                else:
                    cursor.execute("""
                        SELECT index_data, metadata_json, vector_count, index_name
                        FROM rag_vector_index
                        WHERE index_name LIKE 'shard_%'
                        ORDER BY updated_at DESC
                    """)
                
                shard_rows = cursor.fetchall()
                if not shard_rows:
                    return None, {}, {}, {}
                
                # 첫 번째 shard를 base로 사용하고 나머지를 병합
                if not shard_rows:
                    return None, {}, {}, {}
                
                embedding_dim = self._get_embedding_dim()
                first_shard_data, first_metadata_json, first_vector_count, first_shard_name = shard_rows[0]
                
                # 첫 번째 shard를 base 인덱스로 로드
                # BYTEA를 numpy 배열로 변환
                if isinstance(first_shard_data, memoryview):
                    first_shard_data = np.frombuffer(first_shard_data, dtype=np.uint8)
                elif isinstance(first_shard_data, bytes):
                    first_shard_data = np.frombuffer(first_shard_data, dtype=np.uint8)
                else:
                    first_shard_data = np.frombuffer(bytes(first_shard_data), dtype=np.uint8)
                
                base_index = faiss.deserialize_index(first_shard_data)
                base_metadata = first_metadata_json.get('metadata', {})
                base_id_to_index = first_metadata_json.get('id_to_index', {})
                base_index_to_id_raw = first_metadata_json.get('index_to_id', {})
                base_index_to_id = {int(k): v for k, v in base_index_to_id_raw.items()}
                
                # 나머지 shard들을 병합
                for shard_data_bytes, shard_metadata_json, shard_vector_count, shard_name in shard_rows[1:]:
                    # BYTEA를 numpy 배열로 변환
                    if isinstance(shard_data_bytes, memoryview):
                        shard_data_bytes = np.frombuffer(shard_data_bytes, dtype=np.uint8)
                    elif isinstance(shard_data_bytes, bytes):
                        shard_data_bytes = np.frombuffer(shard_data_bytes, dtype=np.uint8)
                    else:
                        shard_data_bytes = np.frombuffer(bytes(shard_data_bytes), dtype=np.uint8)
                    
                    shard_index = faiss.deserialize_index(shard_data_bytes)
                    base_vector_count = base_index.ntotal
                    
                    # FAISS 인덱스 병합
                    base_index.merge_from(shard_index)
                    
                    # 메타데이터 병합 (인덱스 오프셋 조정)
                    shard_metadata = shard_metadata_json.get('metadata', {})
                    shard_id_to_index = shard_metadata_json.get('id_to_index', {})
                    shard_index_to_id_raw = shard_metadata_json.get('index_to_id', {})
                    shard_index_to_id = {int(k): v for k, v in shard_index_to_id_raw.items()}
                    
                    for doc_id, shard_faiss_idx in shard_id_to_index.items():
                        new_faiss_idx = base_vector_count + shard_faiss_idx
                        base_metadata[doc_id] = shard_metadata.get(doc_id, {})
                        base_id_to_index[doc_id] = new_faiss_idx
                        base_index_to_id[new_faiss_idx] = doc_id
                
                total_vectors = base_index.ntotal
                print(f"✅ DB에서 shard 병합하여 인덱스 로드 완료 ({len(shard_rows)}개 shard, {total_vectors}개 벡터)")
                
                # shard를 병합한 인덱스를 base로 저장 (다음 로드 시 빠르게 로드)
                try:
                    self._save_merged_index_to_db(base_index, base_metadata, base_id_to_index, base_index_to_id, total_vectors)
                    print(f"💾 병합된 인덱스를 base로 저장 완료 (다음 로드 시 빠른 로드)")
                except Exception as save_err:
                    print(f"⚠️ base 인덱스 저장 실패 (계속 사용 가능): {save_err}")
                
                return base_index, base_metadata, base_id_to_index, base_index_to_id
                
        except Exception as e:
            print(f"⚠️ DB에서 인덱스 로드 실패: {e}")
            import traceback
            traceback.print_exc()
            return None, {}, {}, {}
    
    def _load_metadata_from_file(self, metadata_path: str) -> Tuple[Dict[str, Any], Dict[str, int], Dict[int, str]]:
        """
        메타데이터 파일에서 로드 (헬퍼 메서드)
        
        Args:
            metadata_path: 메타데이터 파일 경로
            
        Returns:
            (metadata, id_to_index, index_to_id) 튜플
        """
        if not os.path.exists(metadata_path):
            return {}, {}, {}
        
        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                metadata = data.get("metadata", {})
                id_to_index = data.get("id_to_index", {})
                index_to_id_raw = data.get("index_to_id", {})
                # JSON에서 로드하면 키가 문자열이므로 정수로 변환
                index_to_id = {int(k): v for k, v in index_to_id_raw.items()}
                return metadata, id_to_index, index_to_id
        except Exception as e:
            print(f"⚠️ 메타데이터 로드 실패: {e}")
            return {}, {}, {}
    
    def _save_index(self):
        """FAISS 인덱스 및 메타데이터 저장 (DB 또는 파일)"""
        try:
            if self.use_db:
                # DB에 저장
                self._save_index_to_db()
            else:
                # 파일에 저장 (기존 방식)
                faiss.write_index(self.index, self.base_index_path)
                
                # base 메타데이터 저장
                data = {
                    "metadata": self.metadata,
                    "id_to_index": self.id_to_index,
                    "index_to_id": self.index_to_id
                }
                with open(self.base_metadata_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                # 경로 업데이트
                self.index_path = self.base_index_path
                self.metadata_path = self.base_metadata_path
        except Exception as e:
            print(f"⚠️ 인덱스 저장 실패: {e}")
    
    def _save_merged_index_to_db(
        self, 
        index: Any, 
        metadata: Dict[str, Any], 
        id_to_index: Dict[str, int], 
        index_to_id: Dict[int, str],
        vector_count: int
    ):
        """병합된 인덱스를 DB에 base로 저장 (내부 헬퍼 메서드)"""
        try:
            # FAISS 인덱스를 바이트로 직렬화
            serialized = faiss.serialize_index(index)
            if hasattr(serialized, 'tobytes'):
                index_data_bytes = serialized.tobytes()
            else:
                index_data_bytes = bytes(serialized)
            index_size = len(index_data_bytes)
            
            # NaN 값 처리
            def clean_for_json(obj):
                """NaN, Infinity 값을 null로 변환"""
                import math
                if isinstance(obj, dict):
                    return {k: clean_for_json(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [clean_for_json(item) for item in obj]
                elif isinstance(obj, float):
                    if math.isnan(obj) or math.isinf(obj):
                        return None
                    return obj
                return obj
            
            cleaned_metadata = clean_for_json(metadata)
            metadata_json = {
                "metadata": cleaned_metadata,
                "id_to_index": id_to_index,
                "index_to_id": {str(k): v for k, v in index_to_id.items()}
            }
            
            # DB에 저장
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO rag_vector_index (
                        index_name, index_data, metadata_json, index_size, vector_count
                    ) VALUES (%s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (index_name)
                    DO UPDATE SET
                        index_data = EXCLUDED.index_data,
                        metadata_json = EXCLUDED.metadata_json,
                        index_size = EXCLUDED.index_size,
                        vector_count = EXCLUDED.vector_count,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    'base',
                    index_data_bytes,
                    json.dumps(metadata_json, allow_nan=False),
                    index_size,
                    vector_count
                ))
        except Exception as e:
            print(f"⚠️ 병합 인덱스 저장 실패: {e}")
            raise
    
    def _save_index_to_db(self):
        """DB에 FAISS 인덱스 및 메타데이터 저장"""
        try:
            # FAISS 인덱스를 바이트로 직렬화
            # serialize_index는 bytes를 반환하지만, psycopg2 호환을 위해 명시적 변환
            serialized = faiss.serialize_index(self.index)
            # numpy 배열일 수 있으므로 bytes로 변환
            if hasattr(serialized, 'tobytes'):
                index_data_bytes = serialized.tobytes()
            else:
                index_data_bytes = bytes(serialized)
            index_size = len(index_data_bytes)
            vector_count = self.index.ntotal if self.index else 0
            
            # 메타데이터 준비 (NaN 값 처리)
            def clean_for_json(obj):
                """NaN, Infinity 값을 null로 변환"""
                import math
                if isinstance(obj, dict):
                    return {k: clean_for_json(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [clean_for_json(item) for item in obj]
                elif isinstance(obj, float):
                    if math.isnan(obj) or math.isinf(obj):
                        return None
                    return obj
                return obj
            
            cleaned_metadata = clean_for_json(self.metadata)
            metadata_json = {
                "metadata": cleaned_metadata,
                "id_to_index": self.id_to_index,
                "index_to_id": {str(k): v for k, v in self.index_to_id.items()}  # 키를 문자열로 변환
            }
            
            # DB에 저장 (UPSERT)
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO rag_vector_index (
                        index_name, index_data, metadata_json, index_size, vector_count
                    ) VALUES (%s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (index_name)
                    DO UPDATE SET
                        index_data = EXCLUDED.index_data,
                        metadata_json = EXCLUDED.metadata_json,
                        index_size = EXCLUDED.index_size,
                        vector_count = EXCLUDED.vector_count,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    'base',
                    index_data_bytes,
                    json.dumps(metadata_json, allow_nan=False),
                    index_size,
                    vector_count
                ))
            
            print(f"✅ DB에 벡터 인덱스 저장 완료 ({vector_count}개 벡터, {index_size:,} bytes)")
            
        except Exception as e:
            print(f"⚠️ DB 인덱스 저장 실패: {e}")
            import traceback
            traceback.print_exc()
    
    def add_example(
        self,
        ocr_text: str,
        answer_json: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        skip_duplicate: bool = True
    ) -> Optional[str]:
        """
        예제 추가 (OCR 텍스트만 임베딩)
        
        Args:
            ocr_text: OCR 추출 결과 텍스트 (임베딩 대상)
            answer_json: 정답 JSON 딕셔너리 (payload에 저장)
            metadata: 추가 메타데이터 (예: pdf_name, page_num 등)
            skip_duplicate: 중복 체크 여부 (True면 같은 pdf_name+page_num이 있으면 스킵)
            
        Returns:
            추가된 문서의 ID (중복이면 None)
        """
        import uuid
        
        metadata = metadata or {}
        
        # 중복 체크 (pdf_name과 page_num으로 확인)
        if skip_duplicate:
            pdf_name = metadata.get('pdf_name')
            page_num = metadata.get('page_num')
            
            if pdf_name is not None and page_num is not None:
                # 기존 예제 중 같은 pdf_name과 page_num이 있는지 확인
                for existing_id, existing_data in self.metadata.items():
                    existing_metadata = existing_data.get('metadata', {})
                    if (existing_metadata.get('pdf_name') == pdf_name and 
                        existing_metadata.get('page_num') == page_num):
                        # 중복 발견 - 기존 ID 반환
                        return None
        
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
            "metadata": metadata
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
    
    def count_examples(self) -> int:
        """
        벡터 DB에 저장된 예제 수 반환
        
        Returns:
            예제 수
        """
        if self.use_db:
            # DB 모드: DB에서 직접 확인 (base 우선, 없으면 모든 인덱스 합산)
            try:
                with self.db.get_connection() as conn:
                    cursor = conn.cursor()
                    # 먼저 base 인덱스 확인
                    cursor.execute("""
                        SELECT vector_count
                        FROM rag_vector_index
                        WHERE index_name = 'base'
                        ORDER BY updated_at DESC
                        LIMIT 1
                    """)
                    row = cursor.fetchone()
                    if row and row[0]:
                        return row[0]
                    
                    # base가 없으면 모든 인덱스의 벡터 수 합산 (shard 포함)
                    cursor.execute("""
                        SELECT COALESCE(SUM(vector_count), 0)
                        FROM rag_vector_index
                    """)
                    row = cursor.fetchone()
                    if row and row[0]:
                        return row[0]
                    
                    # DB에 데이터가 없으면 메타데이터 길이 반환
                    return len(self.metadata)
            except Exception as e:
                print(f"⚠️ DB에서 벡터 수 확인 실패: {e}")
                return len(self.metadata)
        else:
            # 파일 모드: 메타데이터 길이 반환
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
    
    def _create_search_result(
        self,
        doc_id: str,
        data: Dict[str, Any],
        similarity: float,
        distance: float,
        source: str
    ) -> Dict[str, Any]:
        """
        검색 결과 딕셔너리 생성 (헬퍼 메서드)
        
        Args:
            doc_id: 문서 ID
            data: 메타데이터 딕셔너리
            similarity: 유사도 점수
            distance: 거리 점수
            source: 출처 ("base" 또는 "shard")
            
        Returns:
            검색 결과 딕셔너리
        """
        return {
            "ocr_text": data.get("ocr_text", ""),
            "answer_json": data.get("answer_json", {}),
            "metadata": data.get("metadata", {}),
            "similarity": similarity,
            "distance": float(distance),
            "id": doc_id,
            "source": source
        }
    
    def _normalize_score(self, score: float, min_score: float, max_score: float) -> float:
        """
        점수 정규화 (헬퍼 메서드)
        
        Args:
            score: 정규화할 점수
            min_score: 최소 점수
            max_score: 최대 점수
            
        Returns:
            정규화된 점수 (0.0 ~ 1.0)
        """
        if max_score > min_score:
            return (score - min_score) / (max_score - min_score)
        elif max_score == min_score and max_score > 0:
            return 1.0
        else:
            return 0.0
    
    def search_vector_only(
        self,
        query_text: str,
        top_k: int = 3,
        similarity_threshold: float = 0.7
    ) -> List[Dict[str, Any]]:
        """
        기본 벡터 검색 (base 인덱스만 검색)
        
        Args:
            query_text: 검색 쿼리 텍스트 (OCR 텍스트)
            top_k: 반환할 최대 결과 수
            similarity_threshold: 최소 유사도 임계값 (0.0 ~ 1.0)
            
        Returns:
            검색 결과 리스트
        """
        # 전처리 적용
        processed_query = self.preprocess_ocr_text(query_text)
        
        # 임베딩 생성
        model = self._get_embedding_model()
        query_embedding = model.encode([processed_query], convert_to_numpy=True).astype('float32')
        
        all_results = []
        
        # base 인덱스 검색
        if self.index is None:
            print(f"⚠️ RAG 검색: 인덱스가 None입니다. 벡터 DB가 제대로 로드되지 않았을 수 있습니다.")
            return []
        
        if self.index.ntotal > 0:
            k = min(top_k * 2, self.index.ntotal)
            distances, indices = self.index.search(query_embedding, k)
            
            for i, (distance, idx) in enumerate(zip(distances[0], indices[0])):
                if idx == -1:
                    continue
                
                doc_id = self.index_to_id.get(idx)
                if not doc_id:
                    continue
                
                similarity = max(0.0, 1.0 - (distance / 100.0))
                if similarity < similarity_threshold:
                    continue
                
                data = self.metadata.get(doc_id, {})
                # deleted 상태 필터링
                page_metadata = data.get("metadata", {})
                if page_metadata.get("status") == "deleted":
                    continue
                
                all_results.append(self._create_search_result(doc_id, data, similarity, distance, "base"))
        else:
            print(f"⚠️ RAG 검색: 인덱스가 비어있습니다. (ntotal={self.index.ntotal}, 메타데이터={len(self.metadata)}개)")
        
        # 유사도로 정렬 및 중복 제거 (doc_id 기준)
        seen_doc_ids = set()
        unique_results = []
        for result in sorted(all_results, key=lambda x: x["similarity"], reverse=True):
            doc_id = result["id"]
            if doc_id not in seen_doc_ids:
                seen_doc_ids.add(doc_id)
                unique_results.append(result)
        
        return unique_results[:top_k]
    
    def search_hybrid(
        self,
        query_text: str,
        top_k: int = 3,
        similarity_threshold: float = 0.7,
        hybrid_alpha: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        하이브리드 검색: BM25 + 벡터 검색 결합
        
        Args:
            query_text: 검색 쿼리 텍스트 (OCR 텍스트)
            top_k: 반환할 최대 결과 수
            similarity_threshold: 최소 유사도 임계값 (0.0 ~ 1.0)
            hybrid_alpha: 하이브리드 가중치 (0.0~1.0, 0.5 = 벡터와 BM25 동일 가중치)
            
        Returns:
            검색 결과 리스트 (hybrid_score 포함)
        """
        # BM25 인덱스 구축
        self._build_bm25_index()
        
        if self._bm25_index is None:
            # BM25가 사용 불가능하면 벡터 검색만 사용
            return self.search_vector_only(query_text, top_k, similarity_threshold)
        
        # 벡터 검색 (더 많은 후보)
        vector_results = self.search_vector_only(
            query_text, top_k * 3, 0.0  # threshold 무시
        )
        
        # BM25 검색
        processed_query = self.preprocess_ocr_text(query_text)
        query_tokens = self._tokenize(processed_query)
        
        if not query_tokens:
            return self.search_vector_only(query_text, top_k, similarity_threshold)
        
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
            normalized_bm25 = self._normalize_score(bm25_score, min_bm25, max_bm25)
            
            # 하이브리드 점수
            hybrid_score = hybrid_alpha * vector_similarity + (1 - hybrid_alpha) * normalized_bm25
            
            # 벡터 유사도가 threshold를 통과하면 하이브리드 점수와 관계없이 포함
            # (BM25 점수가 낮아도 벡터 유사도가 높으면 유지)
            if hybrid_score < similarity_threshold and vector_similarity < similarity_threshold:
                continue
            
            result["bm25_score"] = normalized_bm25
            result["hybrid_score"] = hybrid_score
            hybrid_results.append(result)
        
        # 하이브리드 점수로 정렬
        hybrid_results.sort(key=lambda x: x["hybrid_score"], reverse=True)
        
        return hybrid_results[:top_k]
    
    # ============================================
    # Shard 관리 메서드
    # ============================================
    
    def build_shard(
        self,
        pages: List[Dict[str, Any]]
    ) -> Optional[Tuple[str, str]]:
        """
        새로운 shard FAISS 인덱스를 생성합니다.
        
        Args:
            pages: 페이지 데이터 리스트
                각 페이지는 {
                    'pdf_name': str,
                    'page_num': int,
                    'ocr_text': str,
                    'answer_json': Dict[str, Any],
                    'metadata': Dict[str, Any],
                    'page_key': str,
                    'page_hash': str
                } 형태
        
        Returns:
            (shard_path, shard_id) 튜플 (실패 시 None)
        """
        if not pages:
            return None
        
        import uuid
        from datetime import datetime
        
        # shard 파일명 생성 (타임스탬프 기반)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        shard_id = str(uuid.uuid4())[:8]
        shard_filename = f"shard_{timestamp}_{shard_id}.faiss"
        shard_path = os.path.join(self.shards_dir, shard_filename)
        shard_metadata_path = shard_path.replace(".faiss", "_metadata.json")
        
        try:
            # 임베딩 모델 가져오기
            model = self._get_embedding_model()
            embedding_dim = self._get_embedding_dim()
            
            # 새 FAISS 인덱스 생성
            shard_index = faiss.IndexFlatL2(embedding_dim)
            
            # shard 메타데이터
            shard_metadata = {}
            shard_id_to_index = {}
            shard_index_to_id = {}
            
            # 각 페이지 임베딩 및 추가
            for page_data in pages:
                ocr_text = page_data.get('ocr_text', '')
                answer_json = page_data.get('answer_json', {})
                base_metadata = page_data.get('metadata', {})
                page_key = page_data.get('page_key', '')
                page_hash = page_data.get('page_hash', '')
                
                if not ocr_text:
                    continue
                
                # 문서 ID 생성
                doc_id = str(uuid.uuid4())
                
                # 임베딩 생성
                processed_text = self.preprocess_ocr_text(ocr_text)
                embedding = model.encode([processed_text], convert_to_numpy=True).astype('float32')
                
                # FAISS 인덱스에 추가
                faiss_index = shard_index.ntotal
                shard_index.add(embedding)
                
                # 메타데이터 저장 (page 식별 정보 필수 포함)
                shard_metadata[doc_id] = {
                    "ocr_text": ocr_text,
                    "answer_json": answer_json,
                    "metadata": {
                        **base_metadata,
                        "page_key": page_key,
                        "page_hash": page_hash,
                        "shard_id": shard_id,
                        "status": "staged"
                    }
                }
                shard_id_to_index[doc_id] = faiss_index
                shard_index_to_id[faiss_index] = doc_id
            
            if shard_index.ntotal == 0:
                print("⚠️ shard에 추가할 데이터가 없습니다.")
                return None
            
            # shard 인덱스 저장
            if self.use_db:
                # DB에 저장
                shard_index_name = f"shard_{shard_id}"
                # serialize_index는 bytes를 반환하지만, psycopg2 호환을 위해 명시적 변환
                serialized = faiss.serialize_index(shard_index)
                if hasattr(serialized, 'tobytes'):
                    index_data_bytes = serialized.tobytes()
                else:
                    index_data_bytes = bytes(serialized)
                index_size = len(index_data_bytes)
                vector_count = shard_index.ntotal
                
                # NaN 값 처리
                def clean_for_json(obj):
                    """NaN, Infinity 값을 null로 변환"""
                    import math
                    if isinstance(obj, dict):
                        return {k: clean_for_json(v) for k, v in obj.items()}
                    elif isinstance(obj, list):
                        return [clean_for_json(item) for item in obj]
                    elif isinstance(obj, float):
                        if math.isnan(obj) or math.isinf(obj):
                            return None
                        return obj
                    return obj
                
                cleaned_shard_metadata = clean_for_json(shard_metadata)
                shard_data = {
                    "metadata": cleaned_shard_metadata,
                    "id_to_index": shard_id_to_index,
                    "index_to_id": {str(k): v for k, v in shard_index_to_id.items()},
                    "shard_id": shard_id
                }
                
                with self.db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO rag_vector_index (
                            index_name, index_data, metadata_json, index_size, vector_count
                        ) VALUES (%s, %s, %s::jsonb, %s, %s)
                    """, (
                        shard_index_name,
                        index_data_bytes,
                        json.dumps(shard_data, allow_nan=False),
                        index_size,
                        vector_count
                    ))
                
                print(f"✅ Shard 생성 완료 (DB 저장): {shard_index_name} ({vector_count}개 벡터)")
                return (shard_index_name, shard_id)  # DB 모드에서는 index_name 반환
            else:
                # 파일에 저장 (기존 방식)
                faiss.write_index(shard_index, shard_path)
                
                shard_data = {
                    "metadata": shard_metadata,
                    "id_to_index": shard_id_to_index,
                    "index_to_id": shard_index_to_id,
                    "shard_id": shard_id
                }
                with open(shard_metadata_path, 'w', encoding='utf-8') as f:
                    json.dump(shard_data, f, ensure_ascii=False, indent=2)
                
                print(f"✅ Shard 생성 완료: {shard_filename} ({shard_index.ntotal}개 벡터)")
                return (shard_path, shard_id)
            
        except Exception as e:
            print(f"❌ Shard 생성 실패: {e}")
            # 실패 시 생성된 파일 정리
            if os.path.exists(shard_path):
                try:
                    os.remove(shard_path)
                except:
                    pass
            if os.path.exists(shard_metadata_path):
                try:
                    os.remove(shard_metadata_path)
                except:
                    pass
            return None
    
    def merge_shard(self, shard_path: str) -> bool:
        """
        shard를 base 인덱스에 원자적으로 merge합니다.
        
        Args:
            shard_path: shard FAISS 인덱스 파일 경로 또는 DB index_name
            
        Returns:
            merge 성공 여부
        """
        if self.use_db:
            # DB 모드: shard_path는 index_name
            shard_index_name = shard_path
            try:
                with self.db.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT index_data, metadata_json
                        FROM rag_vector_index
                        WHERE index_name = %s
                    """, (shard_index_name,))
                    
                    row = cursor.fetchone()
                    if not row:
                        print(f"❌ DB에서 Shard를 찾을 수 없습니다: {shard_index_name}")
                        return False
                    
                    shard_index_data = row[0]
                    shard_metadata_json = row[1]
                    
                    # BYTEA를 numpy 배열로 변환 (faiss.deserialize_index는 numpy 배열 필요)
                    if isinstance(shard_index_data, memoryview):
                        shard_index_data = np.frombuffer(shard_index_data, dtype=np.uint8)
                    elif isinstance(shard_index_data, bytes):
                        shard_index_data = np.frombuffer(shard_index_data, dtype=np.uint8)
                    else:
                        shard_index_data = np.frombuffer(bytes(shard_index_data), dtype=np.uint8)
                    
                    # FAISS 인덱스 로드
                    shard_index = faiss.deserialize_index(shard_index_data)
                    
                    # 메타데이터 파싱
                    shard_data = shard_metadata_json
                    shard_metadata = shard_data.get('metadata', {})
                    shard_id_to_index = shard_data.get('id_to_index', {})
                    shard_index_to_id_raw = shard_data.get('index_to_id', {})
                    shard_index_to_id = {int(k): v for k, v in shard_index_to_id_raw.items()}
            except Exception as e:
                print(f"❌ DB에서 Shard 로드 실패: {e}")
                return False
        else:
            # 파일 모드
            if not os.path.exists(shard_path):
                print(f"❌ Shard 파일을 찾을 수 없습니다: {shard_path}")
                return False
            
            shard_metadata_path = shard_path.replace(".faiss", "_metadata.json")
            if not os.path.exists(shard_metadata_path):
                print(f"❌ Shard 메타데이터 파일을 찾을 수 없습니다: {shard_metadata_path}")
                return False
            
            # shard 인덱스 및 메타데이터 로드
            shard_index = faiss.read_index(shard_path)
            shard_metadata, shard_id_to_index, shard_index_to_id = self._load_metadata_from_file(shard_metadata_path)
        
        try:
            # base 인덱스 로드 (merge하려는 shard 제외)
            if self.use_db:
                # merge하려는 shard를 제외하고 base 로드
                base_index, base_metadata, base_id_to_index, base_index_to_id = self._load_index_from_db(exclude_shard_name=shard_index_name)
                if base_index is None:
                    embedding_dim = self._get_embedding_dim()
                    base_index = faiss.IndexFlatL2(embedding_dim)
                    base_metadata = {}
                    base_id_to_index = {}
                    base_index_to_id = {}
            else:
                if os.path.exists(self.base_index_path):
                    base_index = faiss.read_index(self.base_index_path)
                    base_metadata, base_id_to_index, base_index_to_id = self._load_metadata_from_file(self.base_metadata_path)
                else:
                    embedding_dim = self._get_embedding_dim()
                    base_index = faiss.IndexFlatL2(embedding_dim)
                    base_metadata = {}
                    base_id_to_index = {}
                    base_index_to_id = {}
            
            # base의 현재 벡터 수 (merge 전)
            base_vector_count = base_index.ntotal
            
            # FAISS 인덱스 merge
            base_index.merge_from(shard_index)
            
            # 메타데이터 merge (인덱스 오프셋 조정, status 업데이트)
            for doc_id, shard_faiss_idx in shard_id_to_index.items():
                new_faiss_idx = base_vector_count + shard_faiss_idx
                page_metadata = shard_metadata[doc_id].copy()
                # status를 merged로 업데이트
                if "metadata" in page_metadata:
                    page_metadata["metadata"]["status"] = "merged"
                
                base_metadata[doc_id] = page_metadata
                base_id_to_index[doc_id] = new_faiss_idx
                base_index_to_id[new_faiss_idx] = doc_id
            
            # 저장 (DB 또는 파일)
            if self.use_db:
                # DB에 저장
                self.index = base_index
                self.metadata = base_metadata
                self.id_to_index = base_id_to_index
                self.index_to_id = base_index_to_id
                self._save_index_to_db()  # DB에 저장
                
                # merge 완료 후 shard 삭제 (DB에서)
                try:
                    with self.db.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            DELETE FROM rag_vector_index
                            WHERE index_name = %s
                        """, (shard_index_name,))
                    print(f"✅ Shard 삭제 완료 (DB): {shard_index_name}")
                except Exception as e:
                    print(f"⚠️ Shard 삭제 실패: {e}")
            else:
                # 파일에 저장 (원자적 write)
                tmp_index_path = self.base_index_path + ".tmp"
                tmp_metadata_path = self.base_metadata_path + ".tmp"
                
                faiss.write_index(base_index, tmp_index_path)
                
                base_data = {
                    "metadata": base_metadata,
                    "id_to_index": base_id_to_index,
                    "index_to_id": base_index_to_id
                }
                with open(tmp_metadata_path, 'w', encoding='utf-8') as f:
                    json.dump(base_data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())  # 디스크 동기화
                
                # 원자적 rename (임시 파일 → 실제 파일)
                os.rename(tmp_index_path, self.base_index_path)
                os.rename(tmp_metadata_path, self.base_metadata_path)
                
                # 메모리 인덱스 갱신
                self.index = base_index
                self.metadata = base_metadata
                self.id_to_index = base_id_to_index
                self.index_to_id = base_index_to_id
                
                # merge 완료 후 shard 파일 삭제
                try:
                    if os.path.exists(shard_path):
                        os.remove(shard_path)
                    if os.path.exists(shard_metadata_path):
                        os.remove(shard_metadata_path)
                    print(f"✅ Shard 파일 삭제 완료: {shard_path}")
                except Exception as e:
                    print(f"⚠️ Shard 파일 삭제 실패: {e}")
            
            # BM25 인덱스 새로고침
            self._refresh_bm25_index()
            
            print(f"✅ Shard merge 완료: {shard_index.ntotal}개 벡터가 base에 추가됨")
            return True
            
        except Exception as e:
            print(f"❌ Shard merge 실패: {e}")
            import traceback
            traceback.print_exc()
            # 임시 파일 정리 (파일 모드일 때만)
            if not self.use_db:
                for tmp_path in [tmp_index_path, tmp_metadata_path]:
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except:
                            pass
            return False
    
    def get_shard_paths(self) -> List[str]:
        """
        모든 shard 파일 경로 반환
        
        Returns:
            shard 파일 경로 리스트
        """
        if not os.path.exists(self.shards_dir):
            return []
        
        shard_paths = []
        for filename in os.listdir(self.shards_dir):
            if filename.endswith(".faiss"):
                shard_paths.append(os.path.join(self.shards_dir, filename))
        
        return sorted(shard_paths)
    
    def search_similar_advanced(
        self,
        query_text: str,
        top_k: int = 3,
        similarity_threshold: float = 0.7,
        search_method: str = "vector",  # "vector", "hybrid"
        hybrid_alpha: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        통합 검색 함수 (검색 방식 선택 가능)
        
        Args:
            query_text: 검색 쿼리 텍스트 (OCR 텍스트)
            top_k: 반환할 최대 결과 수
            similarity_threshold: 최소 유사도 임계값 (0.0 ~ 1.0)
            search_method: 검색 방식 ("vector", "hybrid")
            hybrid_alpha: 하이브리드 가중치 (hybrid 방식 사용 시)
            
        Returns:
            검색 결과 리스트
        """
        if search_method == "hybrid":
            return self.search_hybrid(
                query_text, top_k, similarity_threshold, hybrid_alpha
            )
        else:  # "vector" 또는 기본값
            return self.search_vector_only(
                query_text, top_k, similarity_threshold
            )


# 전역 RAG Manager 인스턴스 (싱글톤 패턴)
_rag_manager: Optional[RAGManager] = None
_rag_manager_lock = Lock()  # 싱글톤 생성 락


def get_rag_manager(use_db: bool = True) -> RAGManager:
    """
    전역 RAG Manager 인스턴스 반환 (스레드 안전)
    
    Args:
        use_db: True면 DB에 저장, False면 로컬 파일에 저장 (기본값: True)
    
    Returns:
        RAGManager 인스턴스
    """
    global _rag_manager
    if _rag_manager is None:
        with _rag_manager_lock:
            # 이중 체크
            if _rag_manager is None:
                _rag_manager = RAGManager(use_db=use_db)
    return _rag_manager
