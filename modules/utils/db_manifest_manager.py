"""
DB 기반 Manifest 관리 모듈

PostgreSQL을 사용하여 처리된 PDF 페이지의 상태를 추적하고 관리합니다.
manifest.json 파일 대신 DB를 사용합니다.
"""

from typing import Dict, Set, Optional, List, Any
from database.registry import get_db
import psycopg2
import psycopg2.errors


class DBManifestManager:
    """
    DB 기반 Manifest 관리 클래스
    
    상태 기반 페이지 추적: staged(대기중) / merged(병합됨) / deleted(삭제됨)
    """
    
    def __init__(self):
        """DBManifestManager 초기화"""
        self.db = get_db()
        self._ensure_tables_exist()  # 테이블이 없으면 자동 생성
    
    def get_page_info(self, pdf_filename: str, page_number: int) -> Optional[Dict[str, Any]]:
        """
        페이지 정보 반환
        
        Args:
            pdf_filename: PDF 파일명 (확장자 포함)
            page_number: 페이지 번호 (1부터 시작)
            
        Returns:
            페이지 정보 딕셔너리 또는 None
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    learning_id, pdf_filename, page_number, status,
                    page_hash, fingerprint_mtime, fingerprint_size, shard_id,
                    created_at, updated_at
                FROM rag_learning_status
                WHERE pdf_filename = %s AND page_number = %s
            """, (pdf_filename, page_number))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            return {
                'learning_id': row[0],
                'pdf_filename': row[1],
                'page_number': row[2],
                'status': row[3],
                'page_hash': row[4],
                'fingerprint_mtime': row[5],
                'fingerprint_size': row[6],
                'shard_id': row[7],
                'created_at': row[8],
                'updated_at': row[9]
            }
    
    def get_page_status(self, pdf_filename: str, page_number: int) -> Optional[str]:
        """
        페이지 상태 반환 (staged/merged/deleted)
        
        Args:
            pdf_filename: PDF 파일명 (확장자 포함)
            page_number: 페이지 번호 (1부터 시작)
            
        Returns:
            상태 문자열 또는 None
        """
        info = self.get_page_info(pdf_filename, page_number)
        return info.get('status') if info else None
    
    def is_processed(self, pdf_filename: str, page_number: int, page_hash: str) -> bool:
        """
        페이지가 이미 처리되었는지 확인 (merged 상태이고 hash 동일)
        
        Args:
            pdf_filename: PDF 파일명 (확장자 포함)
            page_number: 페이지 번호 (1부터 시작)
            page_hash: 현재 페이지의 hash
            
        Returns:
            merged 상태이고 hash가 동일하면 True
        """
        info = self.get_page_info(pdf_filename, page_number)
        if not info:
            return False
        return info.get('status') == 'merged' and info.get('page_hash') == page_hash
    
    def is_staged(self, pdf_filename: str, page_number: int) -> bool:
        """
        페이지가 staged 상태인지 확인
        
        Args:
            pdf_filename: PDF 파일명 (확장자 포함)
            page_number: 페이지 번호 (1부터 시작)
            
        Returns:
            staged 상태이면 True
        """
        return self.get_page_status(pdf_filename, page_number) == 'staged'
    
    def is_file_changed_fast(
        self, 
        pdf_filename: str, 
        page_number: int, 
        fingerprint: Dict[str, Any]
    ) -> bool:
        """
        파일 fingerprint로 빠른 변경 감지 (1단계 체크 - answer.json 기준만)
        
        Args:
            pdf_filename: PDF 파일명 (확장자 포함)
            page_number: 페이지 번호 (1부터 시작)
            fingerprint: {'answer_mtime': float, 'answer_size': int}
            
        Returns:
            파일이 변경되었거나 처음 보는 경우 True
        """
        info = self.get_page_info(pdf_filename, page_number)
        if not info:
            return True  # 처음 보는 파일
        
        stored_mtime = info.get('fingerprint_mtime')
        stored_size = info.get('fingerprint_size')
        
        if stored_mtime is None or stored_size is None:
            return True
        
        # answer.json 기준만 체크
        return (
            stored_mtime != fingerprint.get('answer_mtime') or
            stored_size != fingerprint.get('answer_size')
        )
    
    def mark_pages_staged(
        self,
        pages: List[Dict[str, Any]],  # [{'pdf_filename': str, 'page_number': int}, ...]
        shard_id: str,
        page_hashes: Dict[str, str],  # {page_key: hash}
        fingerprints: Dict[str, Dict[str, Any]]  # {page_key: fingerprint}
    ) -> None:
        """
        페이지들을 staged 상태로 표시 (shard 생성 시 호출)
        
        Args:
            pages: 페이지 정보 리스트 [{'pdf_filename': str, 'page_number': int}, ...]
            shard_id: shard ID
            page_hashes: {page_key: hash} 딕셔너리
            fingerprints: {page_key: fingerprint} 딕셔너리
        """
        from modules.utils.hash_utils import get_page_key
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            for page_info in pages:
                pdf_filename = page_info['pdf_filename']
                page_number = page_info['page_number']
                page_key = get_page_key(
                    pdf_filename.replace('.pdf', ''),  # 확장자 제거
                    page_number
                )
                
                page_hash = page_hashes.get(page_key, '')
                fingerprint = fingerprints.get(page_key, {})
                
                # UPSERT (있으면 업데이트, 없으면 삽입)
                cursor.execute("""
                    INSERT INTO rag_learning_status (
                        pdf_filename, page_number, status, page_hash,
                        fingerprint_mtime, fingerprint_size, shard_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (pdf_filename, page_number)
                    DO UPDATE SET
                        status = EXCLUDED.status,
                        page_hash = EXCLUDED.page_hash,
                        fingerprint_mtime = EXCLUDED.fingerprint_mtime,
                        fingerprint_size = EXCLUDED.fingerprint_size,
                        shard_id = EXCLUDED.shard_id,
                        updated_at = CURRENT_TIMESTAMP
                """, (
                    pdf_filename,
                    page_number,
                    'staged',
                    page_hash,
                    fingerprint.get('answer_mtime'),
                    fingerprint.get('answer_size'),
                    shard_id
                ))
    
    def mark_pages_merged(self, pages: List[Dict[str, Any]]) -> None:
        """
        페이지들을 merged 상태로 전이 (merge 성공 시 호출)
        
        Args:
            pages: 페이지 정보 리스트 [{'pdf_filename': str, 'page_number': int}, ...]
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            for page_info in pages:
                pdf_filename = page_info['pdf_filename']
                page_number = page_info['page_number']
                
                cursor.execute("""
                    UPDATE rag_learning_status
                    SET status = 'merged', updated_at = CURRENT_TIMESTAMP
                    WHERE pdf_filename = %s AND page_number = %s
                """, (pdf_filename, page_number))
    
    def mark_pages_deleted(self, pages: List[Dict[str, Any]]) -> None:
        """
        페이지들을 deleted 상태로 표시 (파일 삭제 시 호출)
        
        Args:
            pages: 페이지 정보 리스트 [{'pdf_filename': str, 'page_number': int}, ...]
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            
            for page_info in pages:
                pdf_filename = page_info['pdf_filename']
                page_number = page_info['page_number']
                
                cursor.execute("""
                    UPDATE rag_learning_status
                    SET status = 'deleted', updated_at = CURRENT_TIMESTAMP
                    WHERE pdf_filename = %s AND page_number = %s
                """, (pdf_filename, page_number))
    
    def get_all_page_keys(self) -> Set[str]:
        """
        등록된 모든 페이지 키 반환
        
        Returns:
            페이지 키 집합 (예: {"docA.pdf:1", "docA.pdf:2", ...})
        """
        from modules.utils.hash_utils import get_page_key
        
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT pdf_filename, page_number
                    FROM rag_learning_status
                """)
                
                page_keys = set()
                for row in cursor.fetchall():
                    pdf_filename = row[0]
                    page_number = row[1]
                    pdf_name = pdf_filename.replace('.pdf', '')  # 확장자 제거
                    page_key = get_page_key(pdf_name, page_number)
                    page_keys.add(page_key)
                
                return page_keys
        except psycopg2.errors.UndefinedTable:
            # 테이블이 없으면 빈 집합 반환 (테이블은 _ensure_tables_exist에서 생성됨)
            return set()
    
    def get_staged_page_keys(self) -> Set[str]:
        """
        staged 상태인 페이지 키 반환
        
        Returns:
            페이지 키 집합
        """
        from modules.utils.hash_utils import get_page_key
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT pdf_filename, page_number
                FROM rag_learning_status
                WHERE status = 'staged'
            """)
            
            page_keys = set()
            for row in cursor.fetchall():
                pdf_filename = row[0]
                page_number = row[1]
                pdf_name = pdf_filename.replace('.pdf', '')
                page_key = get_page_key(pdf_name, page_number)
                page_keys.add(page_key)
            
            return page_keys
    
    def get_deleted_page_keys(self) -> Set[str]:
        """
        deleted 상태인 페이지 키 반환
        
        Returns:
            페이지 키 집합
        """
        from modules.utils.hash_utils import get_page_key
        
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT pdf_filename, page_number
                FROM rag_learning_status
                WHERE status = 'deleted'
            """)
            
            page_keys = set()
            for row in cursor.fetchall():
                pdf_filename = row[0]
                page_number = row[1]
                pdf_name = pdf_filename.replace('.pdf', '')
                page_key = get_page_key(pdf_name, page_number)
                page_keys.add(page_key)
            
            return page_keys
    
    def _ensure_tables_exist(self):
        """필요한 테이블이 존재하는지 확인하고 없으면 생성"""
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                # 테이블 존재 여부 확인
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = 'rag_learning_status'
                    )
                """)
                table_exists = cursor.fetchone()[0]
                
                if not table_exists:
                    print("📋 RAG 학습 상태 테이블이 없습니다. 생성 중...")
                    # rag_learning_status 테이블 생성
                    cursor.execute("""
                        CREATE TABLE rag_learning_status (
                            learning_id SERIAL PRIMARY KEY,
                            pdf_filename VARCHAR(500) NOT NULL,
                            page_number INTEGER NOT NULL,
                            status VARCHAR(20) NOT NULL DEFAULT 'pending',
                            page_hash VARCHAR(64),
                            fingerprint_mtime REAL,
                            fingerprint_size INTEGER,
                            shard_id VARCHAR(255),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            UNIQUE(pdf_filename, page_number)
                        )
                    """)
                    # 인덱스 생성
                    cursor.execute("""
                        CREATE INDEX idx_rag_learning_status_pdf_page 
                        ON rag_learning_status(pdf_filename, page_number)
                    """)
                    cursor.execute("""
                        CREATE INDEX idx_rag_learning_status_status 
                        ON rag_learning_status(status)
                    """)
                    cursor.execute("""
                        CREATE INDEX idx_rag_learning_status_hash 
                        ON rag_learning_status(page_hash)
                    """)
                    print("✅ RAG 학습 상태 테이블 생성 완료")
        except psycopg2.Error as e:
            print(f"⚠️ 테이블 생성 중 오류 발생: {e}")
            # 오류가 발생해도 계속 진행 (테이블이 이미 존재할 수 있음)




