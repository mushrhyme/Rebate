"""
PostgreSQL 데이터베이스 관리 모듈

JSON 파싱 결과를 PostgreSQL에 저장하고 조회하는 기능을 제공합니다.
2개 테이블 구조: parsing_sessions + items
"""

import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
from psycopg2.pool import SimpleConnectionPool
from typing import Dict, Any, List, Optional
import json
from contextlib import contextmanager
from pathlib import Path


class DatabaseManager:
    """PostgreSQL 데이터베이스 관리 클래스 (2개 테이블 구조)"""
    
    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        database: str = "rebate_db",
        user: str = "postgres",
        password: str = "",
        min_conn: int = 1,
        max_conn: int = 10
    ):
        """
        데이터베이스 연결 풀 초기화
        
        Args:
            host: 데이터베이스 호스트
            port: 데이터베이스 포트
            database: 데이터베이스 이름
            user: 사용자 이름
            password: 비밀번호
            min_conn: 최소 연결 수
            max_conn: 최대 연결 수
        """
        self.db_config = {
            'host': host,
            'port': port,
            'database': database,
            'user': user,
            'password': password
        }
        self.pool = SimpleConnectionPool(
            min_conn, max_conn, **self.db_config
        )
    
    @contextmanager
    def get_connection(self):
        """데이터베이스 연결 컨텍스트 매니저"""
        conn = self.pool.getconn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self.pool.putconn(conn)
    
    def create_session(
        self,
        pdf_filename: str,
        session_name: Optional[str] = None,
        notes: Optional[str] = None,
        is_latest: bool = True
    ) -> int:
        """
        파싱 세션 생성
        
        Args:
            pdf_filename: PDF 파일명
            session_name: 세션명 (선택)
            notes: 메모 (선택)
            is_latest: 최신 세션 여부
            
        Returns:
            생성된 session_id
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # 이전 최신 세션을 False로 업데이트
            if is_latest:
                cursor.execute("""
                    UPDATE parsing_sessions 
                    SET is_latest = FALSE 
                    WHERE pdf_filename = %s AND is_latest = TRUE
                """, (pdf_filename,))
            
            # 새 세션 생성
            if not session_name:
                session_name = f"パース {Path(pdf_filename).stem}"
            
            cursor.execute("""
                INSERT INTO parsing_sessions (
                    pdf_filename, session_name, is_latest, notes
                ) VALUES (%s, %s, %s, %s)
                RETURNING session_id
            """, (pdf_filename, session_name, is_latest, notes))
            
            session_id = cursor.fetchone()[0]
            return session_id
    
    def save_from_result_json(
        self,
        result_json: Dict[str, Any],
        pdf_filename: Optional[str] = None,
        session_name: Optional[str] = None,
        notes: Optional[str] = None
    ) -> int:
        """
        result.json 파일 형식의 데이터를 데이터베이스에 저장
        
        Args:
            result_json: result.json 형식의 파싱 결과
            pdf_filename: 원본 PDF 파일명 (없으면 JSON에서 추출 시도)
            session_name: 세션명 (선택)
            notes: 메모 (선택)
            
        Returns:
            생성된 session_id
        """
        doc = result_json.get('document', {})
        doc_info = doc.get('document_info', {})
        management_groups = doc.get('management_groups', [])
        pages = doc.get('pages', [])  # 페이지 인덱스 배열 [0, 1, ...]
        
        # PDF 파일명 추출
        if not pdf_filename:
            pdf_filename = doc.get('pdf_filename') or 'unknown.pdf'
        
        # 1. 파싱 세션 생성
        session_id = self.create_session(
            pdf_filename=pdf_filename,
            session_name=session_name,
            notes=notes,
            is_latest=True
        )
        
        # 2. 문서 메타데이터 추출
        issuer = doc_info.get('issuer')
        issue_date = doc_info.get('issue_date')
        billing_period = doc_info.get('billing_period')
        total_amount_document = self._parse_amount(doc_info.get('total_amount_document'))
        
        # 3. items 데이터 준비
        items_data = []
        item_order = 0
        
        for mg in management_groups:
            management_id = mg.get('management_id', 'UNKNOWN')
            customer = mg.get('customer')
            mg_pages = mg.get('pages', [])  # 관리번호 그룹이 속한 페이지 인덱스
            
            # 각 항목에 대해 items 데이터 생성
            for item in mg.get('items', []):
                item_order += 1
                
                # 항목별 관리번호 (있으면 사용, 없으면 그룹 관리번호 사용)
                item_management_id = item.get('management_id') or management_id
                
                # 페이지 정보 결정 (관리번호 그룹의 첫 번째 페이지 사용)
                page_index = mg_pages[0] if mg_pages else (pages[0] if pages else 0)
                page_number = page_index + 1  # 0-based → 1-based 변환
                page_role = 'main'  # result.json에는 page_role 정보가 없으므로 기본값
                
                # 수량 관련 필드 추출
                quantity_raw = self._parse_number(item.get('quantity'))
                case_count = self._parse_number(item.get('case_count'))
                bara_count = self._parse_number(item.get('bara_count'))
                units_per_case = self._parse_number(item.get('units_per_case'))
                
                # 수량 계산 (직접 수량이 있으면 사용, 없으면 케이스/바라로 계산)
                quantity = self._calculate_quantity(quantity_raw, case_count, bara_count, units_per_case)
                
                items_data.append((
                    session_id,
                    item_management_id,
                    customer,
                    item.get('product_name'),
                    quantity,  # 계산된 수량
                    case_count,
                    bara_count,
                    units_per_case,
                    self._parse_amount(item.get('amount')),
                    page_number,
                    page_role,
                    issuer,
                    issue_date,
                    billing_period,
                    total_amount_document,
                    pdf_filename,
                    page_index,
                    item_order
                ))
        
        # 4. items 일괄 삽입
        if items_data:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                execute_values(
                    cursor,
                    """
                    INSERT INTO items (
                        session_id, management_id, customer, product_name,
                        quantity, case_count, bara_count, units_per_case, amount,
                        page_number, page_role,
                        issuer, issue_date, billing_period, total_amount_document,
                        pdf_filename, page_index, item_order
                    ) VALUES %s
                    """,
                    items_data
                )
        
        return session_id
    
    def save_from_page_results(
        self,
        page_results: List[Dict[str, Any]],
        pdf_filename: str,
        session_name: Optional[str] = None,
        notes: Optional[str] = None,
        image_paths: Optional[List[str]] = None,
        image_data_list: Optional[List[bytes]] = None
    ) -> int:
        """
        페이지별 파싱 결과(page_results)를 데이터베이스에 저장
        
        Args:
            page_results: 페이지별 파싱 결과 리스트 (app.py의 page_results 형식)
            pdf_filename: 원본 PDF 파일명
            session_name: 세션명 (선택)
            notes: 메모 (선택)
            image_paths: 이미지 파일 경로 리스트 (선택, image_data_list가 없을 때 사용)
            image_data_list: 이미지 데이터(bytes) 리스트 (선택, image_paths보다 우선)
            
        Returns:
            생성된 session_id
        """
        if not page_results:
            raise ValueError("page_results가 비어있습니다.")
        
        # 1. 파싱 세션 생성
        session_id = self.create_session(
            pdf_filename=pdf_filename,
            session_name=session_name,
            notes=notes,
            is_latest=True
        )
        
        # 2. 문서 메타데이터 추출 (첫 번째 페이지에서)
        first_page = page_results[0]
        issuer = first_page.get('issuer')
        issue_date = first_page.get('issue_date')
        billing_period = first_page.get('billing_period')
        
        # 전체 총액 계산
        total_amount_document = 0
        for page in page_results:
            for item in page.get('items', []):
                amount = self._parse_amount(item.get('amount'))
                if amount:
                    total_amount_document += amount
        
        # 3. items 데이터 준비
        items_data = []
        item_order = 0
        
        for page_idx, page_json in enumerate(page_results):
            page_number = page_idx + 1  # 1부터 시작
            page_index = page_idx  # 0부터 시작
            page_role = page_json.get('page_role', 'main')
            
            # 페이지 레벨 거래처 (항목별 거래처가 없을 때 사용)
            page_customer = page_json.get('customer')
            
            # 문서 메타데이터 (페이지별로 다를 수 있으므로 첫 번째 비어있지 않은 값 사용)
            if not issuer:
                issuer = page_json.get('issuer')
            if not issue_date:
                issue_date = page_json.get('issue_date')
            if not billing_period:
                billing_period = page_json.get('billing_period')
            
            # 각 항목에 대해 items 데이터 생성
            for item in page_json.get('items', []):
                item_order += 1
                
                # 항목별 거래처가 있으면 사용, 없으면 페이지 레벨 거래처 사용
                customer = item.get('customer') or page_customer
                
                # 수량 관련 필드 추출
                quantity_raw = self._parse_number(item.get('quantity'))
                case_count = self._parse_number(item.get('case_count'))
                bara_count = self._parse_number(item.get('bara_count'))
                units_per_case = self._parse_number(item.get('units_per_case'))
                
                # 수량 계산 (직접 수량이 있으면 사용, 없으면 케이스/바라로 계산)
                quantity = self._calculate_quantity(quantity_raw, case_count, bara_count, units_per_case)
                
                items_data.append((
                    session_id,
                    item.get('management_id'),
                    customer,
                    item.get('product_name'),
                    quantity,  # 계산된 수량
                    case_count,
                    bara_count,
                    units_per_case,
                    self._parse_amount(item.get('amount')),
                    page_number,
                    page_role,
                    issuer,
                    issue_date,
                    billing_period,
                    total_amount_document,
                    pdf_filename,
                    page_index,
                    item_order
                ))
        
        # 4. items 일괄 삽입
        if items_data:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                execute_values(
                    cursor,
                    """
                    INSERT INTO items (
                        session_id, management_id, customer, product_name,
                        quantity, case_count, bara_count, units_per_case, amount,
                        page_number, page_role,
                        issuer, issue_date, billing_period, total_amount_document,
                        pdf_filename, page_index, item_order
                    ) VALUES %s
                    """,
                    items_data
                )
        
        # 5. 이미지 저장 (이미지 데이터 또는 이미지 경로가 제공된 경우)
        images_to_save = []
        
        # image_data_list가 있으면 우선 사용 (로컬 저장 없이 직접 전달)
        if image_data_list:
            for page_idx, image_data in enumerate(image_data_list):
                if image_data:
                    page_number = page_idx + 1
                    images_to_save.append((page_number, image_data))
        
        # image_data_list가 없고 image_paths가 있으면 파일에서 읽기
        elif image_paths:
            import os
            for page_idx, image_path in enumerate(image_paths):
                if image_path and os.path.exists(image_path):
                    try:
                        with open(image_path, 'rb') as f:
                            image_data = f.read()
                        page_number = page_idx + 1
                        images_to_save.append((page_number, image_data))
                    except Exception as e:
                        print(f"⚠️ 페이지 {page_idx + 1} 이미지 읽기 실패: {e}")
        
        # 이미지 저장
        if images_to_save:
            self.save_page_images(session_id, images_to_save)
        
        return session_id
    
    def get_items(
        self,
        pdf_filename: Optional[str] = None,
        session_id: Optional[int] = None,
        is_latest: bool = True
    ) -> List[Dict[str, Any]]:
        """
        항목 목록 조회
        
        Args:
            pdf_filename: PDF 파일명 (선택)
            session_id: 세션 ID (선택, 지정하면 해당 세션만 조회)
            is_latest: 최신 세션만 조회할지 여부
            
        Returns:
            항목 리스트
        """
        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            conditions = []
            params = []
            
            if session_id:
                conditions.append("i.session_id = %s")
                params.append(session_id)
            elif pdf_filename:
                if is_latest:
                    conditions.append("""
                        i.session_id IN (
                            SELECT session_id FROM parsing_sessions 
                            WHERE pdf_filename = %s AND is_latest = TRUE
                        )
                    """)
                else:
                    conditions.append("""
                        i.session_id IN (
                            SELECT session_id FROM parsing_sessions 
                            WHERE pdf_filename = %s
                        )
                    """)
                params.append(pdf_filename)
            
            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            
            cursor.execute(f"""
                SELECT 
                    i.*,
                    ps.session_name,
                    ps.parsing_timestamp,
                    ps.is_latest
                FROM items i
                JOIN parsing_sessions ps ON i.session_id = ps.session_id
                {where_clause}
                ORDER BY i.pdf_filename, i.page_number, i.item_order
            """, params)
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_sessions(self, pdf_filename: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        파싱 세션 목록 조회
        
        Args:
            pdf_filename: PDF 파일명 (선택, 없으면 모든 세션)
            
        Returns:
            세션 리스트
        """
        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            
            if pdf_filename:
                cursor.execute("""
                    SELECT 
                        ps.*,
                        COUNT(i.item_id) AS total_items,
                        SUM(i.amount) AS total_amount
                    FROM parsing_sessions ps
                    LEFT JOIN items i ON ps.session_id = i.session_id
                    WHERE ps.pdf_filename = %s
                    GROUP BY ps.session_id
                    ORDER BY ps.parsing_timestamp DESC
                """, (pdf_filename,))
            else:
                cursor.execute("""
                    SELECT 
                        ps.*,
                        COUNT(i.item_id) AS total_items,
                        SUM(i.amount) AS total_amount
                    FROM parsing_sessions ps
                    LEFT JOIN items i ON ps.session_id = i.session_id
                    GROUP BY ps.session_id
                    ORDER BY ps.parsing_timestamp DESC
                """)
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_all_pdf_filenames(self, is_latest_only: bool = True) -> List[str]:
        """
        DB에 있는 모든 고유한 PDF 파일명 목록 반환
        
        Args:
            is_latest_only: True면 최신 세션만, False면 모든 세션
            
        Returns:
            PDF 파일명 리스트 (확장자 포함)
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if is_latest_only:
                cursor.execute("""
                    SELECT DISTINCT pdf_filename
                    FROM parsing_sessions
                    WHERE is_latest = TRUE
                    ORDER BY pdf_filename
                """)
            else:
                cursor.execute("""
                    SELECT DISTINCT pdf_filename
                    FROM parsing_sessions
                    ORDER BY pdf_filename
                """)
            
            return [row[0] for row in cursor.fetchall()]
    
    def has_pdf_in_db(self, pdf_filename: str, is_latest_only: bool = True) -> bool:
        """
        DB에 해당 PDF 파일이 있는지 확인
        
        Args:
            pdf_filename: PDF 파일명 (확장자 포함)
            is_latest_only: True면 최신 세션만 확인
            
        Returns:
            존재 여부
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if is_latest_only:
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM parsing_sessions
                    WHERE pdf_filename = %s AND is_latest = TRUE
                """, (pdf_filename,))
            else:
                cursor.execute("""
                    SELECT COUNT(*) 
                    FROM parsing_sessions
                    WHERE pdf_filename = %s
                """, (pdf_filename,))
            
            count = cursor.fetchone()[0]
            return count > 0
    
    def get_page_results(
        self,
        pdf_filename: str,
        session_id: Optional[int] = None,
        is_latest: bool = True
    ) -> List[Dict[str, Any]]:
        """
        페이지별 파싱 결과를 JSON 형식으로 반환 (기존 page_N.json 형식과 호환)
        
        Args:
            pdf_filename: PDF 파일명
            session_id: 세션 ID (선택, 없으면 최신 세션 사용)
            is_latest: 최신 세션만 조회할지 여부
            
        Returns:
            페이지별 파싱 결과 리스트 (페이지 번호 순으로 정렬)
        """
        # 세션 ID 결정
        if session_id is None:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                if is_latest:
                    cursor.execute("""
                        SELECT session_id FROM parsing_sessions 
                        WHERE pdf_filename = %s AND is_latest = TRUE
                        ORDER BY parsing_timestamp DESC
                        LIMIT 1
                    """, (pdf_filename,))
                else:
                    cursor.execute("""
                        SELECT session_id FROM parsing_sessions 
                        WHERE pdf_filename = %s
                        ORDER BY parsing_timestamp DESC
                        LIMIT 1
                    """, (pdf_filename,))
                
                result = cursor.fetchone()
                if not result:
                    return []
                session_id = result[0]
        
        # 페이지별 데이터 조회
        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT 
                    page_number,
                    page_index,
                    page_role,
                    issuer,
                    issue_date,
                    billing_period,
                    pdf_filename,
                    JSON_AGG(
                        JSON_BUILD_OBJECT(
                            'management_id', management_id,
                            'product_name', product_name,
                            'quantity', quantity,
                            'case_count', case_count,
                            'bara_count', bara_count,
                            'units_per_case', units_per_case,
                            'amount', amount,
                            'customer', customer
                        ) ORDER BY item_order
                    ) FILTER (WHERE management_id IS NOT NULL) AS items
                FROM items
                WHERE session_id = %s
                GROUP BY page_number, page_index, page_role, issuer, issue_date, billing_period, pdf_filename
                ORDER BY page_number
            """, (session_id,))
            
            results = []
            for row in cursor.fetchall():
                row_dict = dict(row)
                # JSON_AGG 결과를 파이썬 리스트로 변환
                items = row_dict.get('items', [])
                if items is None:
                    items = []
                elif isinstance(items, str):
                    items = json.loads(items)
                
                # 페이지 레벨 customer 추출 (첫 번째 항목의 customer 또는 NULL)
                page_customer = None
                if items and len(items) > 0:
                    page_customer = items[0].get('customer')
                
                # 페이지별 JSON 구조 생성 (기존 형식과 호환)
                page_json = {
                    'page_role': row_dict.get('page_role', 'main'),
                    'issuer': row_dict.get('issuer'),
                    'issue_date': row_dict.get('issue_date'),
                    'billing_period': row_dict.get('billing_period'),
                    'customer': page_customer,  # 페이지 레벨 customer
                    'items': items
                }
                results.append(page_json)
            
            return results
    
    def get_page_result(
        self,
        pdf_filename: str,
        page_num: int,
        session_id: Optional[int] = None,
        is_latest: bool = True
    ) -> Optional[Dict[str, Any]]:
        """
        특정 페이지의 파싱 결과를 JSON 형식으로 반환
        
        Args:
            pdf_filename: PDF 파일명
            page_num: 페이지 번호 (1부터 시작)
            session_id: 세션 ID (선택, 없으면 최신 세션 사용)
            is_latest: 최신 세션만 조회할지 여부
            
        Returns:
            페이지 파싱 결과 딕셔너리 또는 None
        """
        # 세션 ID 결정
        if session_id is None:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                if is_latest:
                    cursor.execute("""
                        SELECT session_id FROM parsing_sessions 
                        WHERE pdf_filename = %s AND is_latest = TRUE
                        ORDER BY parsing_timestamp DESC
                        LIMIT 1
                    """, (pdf_filename,))
                else:
                    cursor.execute("""
                        SELECT session_id FROM parsing_sessions 
                        WHERE pdf_filename = %s
                        ORDER BY parsing_timestamp DESC
                        LIMIT 1
                    """, (pdf_filename,))
                
                result = cursor.fetchone()
                if not result:
                    return None
                session_id = result[0]
        
        # 페이지 데이터 조회
        with self.get_connection() as conn:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("""
                SELECT 
                    page_number,
                    page_index,
                    page_role,
                    issuer,
                    issue_date,
                    billing_period,
                    pdf_filename,
                    JSON_AGG(
                        JSON_BUILD_OBJECT(
                            'management_id', management_id,
                            'product_name', product_name,
                            'quantity', quantity,
                            'case_count', case_count,
                            'bara_count', bara_count,
                            'units_per_case', units_per_case,
                            'amount', amount,
                            'customer', customer
                        ) ORDER BY item_order
                    ) FILTER (WHERE management_id IS NOT NULL) AS items
                FROM items
                WHERE session_id = %s AND page_number = %s
                GROUP BY page_number, page_index, page_role, issuer, issue_date, billing_period, pdf_filename
            """, (session_id, page_num))
            
            row = cursor.fetchone()
            if not row:
                return None
            
            row_dict = dict(row)
            # JSON_AGG 결과를 파이썬 리스트로 변환
            items = row_dict.get('items', [])
            if items is None:
                items = []
            elif isinstance(items, str):
                items = json.loads(items)
            
            # 페이지 레벨 customer 추출 (첫 번째 항목의 customer 또는 NULL)
            page_customer = None
            if items and len(items) > 0:
                page_customer = items[0].get('customer')
            
            # 페이지별 JSON 구조 생성 (기존 형식과 호환)
            page_json = {
                'page_role': row_dict.get('page_role', 'main'),
                'issuer': row_dict.get('issuer'),
                'issue_date': row_dict.get('issue_date'),
                'billing_period': row_dict.get('billing_period'),
                'customer': page_customer,  # 페이지 레벨 customer
                'items': items
            }
            
            return page_json
    
    @staticmethod
    def _parse_amount(value: Any) -> Optional[int]:
        """
        금액 문자열을 정수로 변환 (예: "327115" -> 327115, "9,841" -> 9841)
        
        Args:
            value: 변환할 값
            
        Returns:
            변환된 정수 (변환 실패 시 None)
        """
        if value is None or value == "":
            return None
        
        if isinstance(value, (int, float)):
            return int(round(value))
        
        if isinstance(value, str):
            # 숫자가 아닌 문자 제거 (공백, 콤마, 통화 기호 등)
            cleaned = value.replace(',', '').replace('¥', '').replace('円', '').strip()
            try:
                if cleaned:
                    return int(round(float(cleaned)))
            except ValueError:
                return None
        
        return None
    
    @staticmethod
    def _parse_number(value: Any) -> Optional[int]:
        """
        수량 문자열을 정수로 변환
        
        Args:
            value: 변환할 값
            
        Returns:
            변환된 정수 (변환 실패 시 None)
        """
        if value is None or value == "":
            return None
        
        if isinstance(value, (int, float)):
            return int(round(value))
        
        if isinstance(value, str):
            cleaned = value.replace(',', '').strip()
            try:
                if cleaned:
                    return int(round(float(cleaned)))
            except ValueError:
                return None
        
        return None
    
    @staticmethod
    def _calculate_quantity(
        quantity: Optional[int],
        case_count: Optional[int],
        bara_count: Optional[int],
        units_per_case: Optional[int]
    ) -> Optional[int]:
        """
        수량 계산: 직접 수량이 있으면 사용, 없으면 case_count * units_per_case + bara_count 계산
        
        Args:
            quantity: 직접 수량 (있으면 우선 사용)
            case_count: 케이스 수
            bara_count: 바라 수
            units_per_case: 케이스당 개수
            
        Returns:
            계산된 수량 (계산 불가능하면 None)
        """
        # 직접 수량이 있으면 그대로 사용
        if quantity is not None:
            return quantity
        
        # 케이스/바라 정보로 계산
        if case_count is not None and units_per_case is not None:
            calculated = case_count * units_per_case
            if bara_count is not None:
                calculated += bara_count
            return calculated
        
        # 바라만 있는 경우
        if bara_count is not None and (case_count is None or case_count == 0):
            return bara_count
        
        return None
    
    def update_page_items(
        self,
        pdf_filename: str,
        page_num: int,
        items: List[Dict[str, Any]],
        session_id: Optional[int] = None,
        is_latest: bool = True
    ) -> bool:
        """
        특정 페이지의 items를 업데이트 (기존 items 삭제 후 새로 삽입)
        
        Args:
            pdf_filename: PDF 파일명
            page_num: 페이지 번호 (1부터 시작)
            items: 업데이트할 items 리스트
            session_id: 세션 ID (선택, 없으면 최신 세션 사용)
            is_latest: 최신 세션만 사용할지 여부
            
        Returns:
            업데이트 성공 여부
        """
        # 세션 ID 결정
        if session_id is None:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                if is_latest:
                    cursor.execute("""
                        SELECT session_id FROM parsing_sessions 
                        WHERE pdf_filename = %s AND is_latest = TRUE
                        ORDER BY parsing_timestamp DESC
                        LIMIT 1
                    """, (pdf_filename,))
                else:
                    cursor.execute("""
                        SELECT session_id FROM parsing_sessions 
                        WHERE pdf_filename = %s
                        ORDER BY parsing_timestamp DESC
                        LIMIT 1
                    """, (pdf_filename,))
                
                result = cursor.fetchone()
                if not result:
                    return False
                session_id = result[0]
        
        # 기존 페이지 데이터에서 메타데이터 가져오기
        page_data = self.get_page_result(pdf_filename, page_num, session_id, False)
        if not page_data:
            return False
        
        # 기존 items 삭제
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                DELETE FROM items 
                WHERE session_id = %s AND page_number = %s
            """, (session_id, page_num))
        
        # 새로운 items 삽입
        items_data = []
        item_order = 0
        
        for item in items:
            item_order += 1
            
            # 메타데이터 추출
            customer = item.get('customer') or page_data.get('customer')
            quantity_raw = self._parse_number(item.get('quantity'))
            case_count = self._parse_number(item.get('case_count'))
            bara_count = self._parse_number(item.get('bara_count'))
            units_per_case = self._parse_number(item.get('units_per_case'))
            amount = self._parse_amount(item.get('amount'))
            
            # 수량 계산
            quantity = self._calculate_quantity(
                quantity_raw, case_count, bara_count, units_per_case
            )
            
            items_data.append((
                session_id,
                item.get('management_id'),
                customer,
                item.get('product_name'),
                quantity,
                case_count,
                bara_count,
                units_per_case,
                amount,
                page_num,
                page_data.get('page_role', 'main'),
                page_data.get('issuer'),
                page_data.get('issue_date'),
                page_data.get('billing_period'),
                None,  # total_amount_document (페이지 단위 업데이트에서는 계산하지 않음)
                pdf_filename,
                page_num - 1,  # page_index (0부터 시작)
                item_order
            ))
        
        # items 삽입
        if items_data:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                execute_values(
                    cursor,
                    """
                    INSERT INTO items (
                        session_id, management_id, customer, product_name,
                        quantity, case_count, bara_count, units_per_case, amount,
                        page_number, page_role,
                        issuer, issue_date, billing_period, total_amount_document,
                        pdf_filename, page_index, item_order
                    ) VALUES %s
                    """,
                    items_data
                )
        
        return True
    
    def save_page_image(
        self,
        session_id: int,
        page_number: int,
        image_data: bytes,
        image_format: str = 'PNG'
    ) -> bool:
        """
        페이지 이미지를 데이터베이스에 저장
        
        Args:
            session_id: 세션 ID
            page_number: 페이지 번호 (1부터 시작)
            image_data: 이미지 바이너리 데이터 (bytes)
            image_format: 이미지 형식 (PNG, JPEG 등)
            
        Returns:
            저장 성공 여부
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO page_images (
                        session_id, page_number, image_data, image_format, image_size
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (session_id, page_number) 
                    DO UPDATE SET 
                        image_data = EXCLUDED.image_data,
                        image_format = EXCLUDED.image_format,
                        image_size = EXCLUDED.image_size,
                        created_at = CURRENT_TIMESTAMP
                """, (session_id, page_number, psycopg2.Binary(image_data), image_format, len(image_data)))
                return True
        except Exception as e:
            print(f"⚠️ 이미지 저장 실패: {e}")
            return False
    
    def get_page_image(
        self,
        pdf_filename: str,
        page_number: int,
        session_id: Optional[int] = None,
        is_latest: bool = True
    ) -> Optional[bytes]:
        """
        페이지 이미지를 데이터베이스에서 로드
        
        Args:
            pdf_filename: PDF 파일명
            page_number: 페이지 번호 (1부터 시작)
            session_id: 세션 ID (선택, 없으면 최신 세션 사용)
            is_latest: 최신 세션만 조회할지 여부
            
        Returns:
            이미지 바이너리 데이터 (bytes) 또는 None
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # 세션 ID 결정
                if session_id is None:
                    if is_latest:
                        cursor.execute("""
                            SELECT session_id FROM parsing_sessions 
                            WHERE pdf_filename = %s AND is_latest = TRUE
                            ORDER BY parsing_timestamp DESC
                            LIMIT 1
                        """, (pdf_filename,))
                    else:
                        cursor.execute("""
                            SELECT session_id FROM parsing_sessions 
                            WHERE pdf_filename = %s
                            ORDER BY parsing_timestamp DESC
                            LIMIT 1
                        """, (pdf_filename,))
                    
                    result = cursor.fetchone()
                    if not result:
                        return None
                    session_id = result[0]
                
                # 이미지 조회
                cursor.execute("""
                    SELECT image_data FROM page_images
                    WHERE session_id = %s AND page_number = %s
                """, (session_id, page_number))
                
                result = cursor.fetchone()
                if result:
                    return bytes(result[0])  # BYTEA를 bytes로 변환
                
                return None
        except Exception as e:
            print(f"⚠️ 이미지 로드 실패: {e}")
            return None
    
    def save_page_images(
        self,
        session_id: int,
        images: List[tuple[int, bytes]],
        image_format: str = 'PNG'
    ) -> int:
        """
        여러 페이지 이미지를 일괄 저장
        
        Args:
            session_id: 세션 ID
            images: (page_number, image_data) 튜플 리스트
            image_format: 이미지 형식 (PNG, JPEG 등)
            
        Returns:
            저장된 이미지 수
        """
        if not images:
            return 0
        
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                saved_count = 0
                for page_number, image_data in images:
                    try:
                        cursor.execute("""
                            INSERT INTO page_images (
                                session_id, page_number, image_data, image_format, image_size
                            ) VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (session_id, page_number) 
                            DO UPDATE SET 
                                image_data = EXCLUDED.image_data,
                                image_format = EXCLUDED.image_format,
                                image_size = EXCLUDED.image_size,
                                created_at = CURRENT_TIMESTAMP
                        """, (session_id, page_number, psycopg2.Binary(image_data), image_format, len(image_data)))
                        saved_count += 1
                    except Exception as e:
                        print(f"⚠️ 페이지 {page_number} 이미지 저장 실패: {e}")
                
                return saved_count
        except Exception as e:
            print(f"⚠️ 이미지 일괄 저장 실패: {e}")
            return 0
    
    def close(self):
        """연결 풀 종료"""
        if self.pool:
            self.pool.closeall()
