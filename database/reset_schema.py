"""
PostgreSQL 데이터베이스 스키마 완전 초기화 스크립트

모든 테이블과 뷰를 삭제하고 schema.sql을 사용하여 새로 생성합니다.

사용법:
    python database/reset_schema.py
"""

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv
import os
from pathlib import Path

# .env 파일 로드
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

def reset_schema():
    """모든 테이블과 뷰를 삭제하고 새로 생성"""
    
    # 데이터베이스 연결 정보
    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 5432)),
        'database': os.getenv('DB_NAME', 'rebate_db'),
        'user': os.getenv('DB_USER', 'postgres'),
        'password': os.getenv('DB_PASSWORD', '')
    }
    
    print(f"🔌 데이터베이스 연결 중: {db_config['database']}@{db_config['host']}:{db_config['port']}")
    
    try:
        # 데이터베이스 연결
        conn = psycopg2.connect(**db_config)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)  # 자동 커밋 모드
        cursor = conn.cursor()
        
        # 1. 기존 테이블 및 뷰 목록 확인
        print("\n📋 기존 스키마 확인 중...")
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """)
        tables = [row[0] for row in cursor.fetchall()]
        
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.views 
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)
        views = [row[0] for row in cursor.fetchall()]
        
        if tables or views:
            print(f"   발견된 테이블: {tables if tables else '(없음)'}")
            print(f"   발견된 뷰: {views if views else '(없음)'}")
        else:
            print("   기존 테이블/뷰 없음")
        
        # 2. 뷰 삭제 (테이블보다 먼저 삭제해야 함)
        if views:
            print("\n🗑️  뷰 삭제 중...")
            for view in views:
                try:
                    cursor.execute(f'DROP VIEW IF EXISTS "{view}" CASCADE;')
                    print(f"   ✓ 뷰 삭제: {view}")
                except Exception as e:
                    print(f"   ✗ 뷰 삭제 실패 ({view}): {e}")
        
        # 3. 테이블 삭제
        if tables:
            print("\n🗑️  테이블 삭제 중...")
            for table in tables:
                try:
                    cursor.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE;')
                    print(f"   ✓ 테이블 삭제: {table}")
                except Exception as e:
                    print(f"   ✗ 테이블 삭제 실패 ({table}): {e}")
        
        # 4. schema.sql 파일 읽기
        schema_file = Path(__file__).parent / 'schema.sql'
        if not schema_file.exists():
            print(f"❌ schema.sql 파일을 찾을 수 없습니다: {schema_file}")
            return
        
        print(f"\n📖 스키마 파일 읽기: {schema_file}")
        with open(schema_file, 'r', encoding='utf-8') as f:
            schema_sql = f.read()
        
        # 5. 스키마 생성
        print("\n🔨 새 스키마 생성 중...")
        try:
            # SQL 문을 세미콜론으로 분리하여 실행
            # 주석 처리된 부분은 건너뛰기
            statements = []
            current_statement = []
            
            for line in schema_sql.split('\n'):
                # 주석 제거 (-- 로 시작하는 줄)
                if line.strip().startswith('--'):
                    continue
                
                # 빈 줄은 무시
                if not line.strip():
                    continue
                
                current_statement.append(line)
                
                # 세미콜론으로 문장 종료
                if line.strip().endswith(';'):
                    statement = '\n'.join(current_statement).strip()
                    if statement:
                        statements.append(statement)
                    current_statement = []
            
            # 남은 문장 처리
            if current_statement:
                statement = '\n'.join(current_statement).strip()
                if statement:
                    statements.append(statement)
            
            # 각 SQL 문 실행
            for i, statement in enumerate(statements, 1):
                try:
                    cursor.execute(statement)
                    # CREATE 문인 경우 어떤 객체가 생성되었는지 확인
                    if 'CREATE TABLE' in statement.upper():
                        # 테이블명 추출
                        import re
                        match = re.search(r'CREATE TABLE\s+(\w+)', statement, re.IGNORECASE)
                        if match:
                            print(f"   ✓ 테이블 생성: {match.group(1)}")
                    elif 'CREATE VIEW' in statement.upper():
                        # 뷰명 추출
                        match = re.search(r'CREATE VIEW\s+(\w+)', statement, re.IGNORECASE)
                        if match:
                            print(f"   ✓ 뷰 생성: {match.group(1)}")
                    elif 'CREATE INDEX' in statement.upper():
                        # 인덱스명 추출
                        match = re.search(r'CREATE INDEX\s+(\w+)', statement, re.IGNORECASE)
                        if match:
                            print(f"   ✓ 인덱스 생성: {match.group(1)}")
                except Exception as e:
                    print(f"   ✗ SQL 실행 실패 (문장 {i}): {e}")
                    print(f"      문장: {statement[:100]}...")
            
        except Exception as e:
            print(f"❌ 스키마 생성 실패: {e}")
            raise
        
        # 6. 최종 확인
        print("\n📋 생성된 스키마 확인 중...")
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """)
        created_tables = [row[0] for row in cursor.fetchall()]
        
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.views 
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)
        created_views = [row[0] for row in cursor.fetchall()]
        
        print(f"\n✅ 스키마 초기화 완료!")
        print(f"   생성된 테이블: {created_tables if created_tables else '(없음)'}")
        print(f"   생성된 뷰: {created_views if created_views else '(없음)'}")
        
        cursor.close()
        conn.close()
        
    except psycopg2.Error as e:
        print(f"❌ 데이터베이스 오류: {e}")
        raise
    except Exception as e:
        print(f"❌ 오류 발생: {e}")
        raise

if __name__ == "__main__":
    print("=" * 60)
    print("PostgreSQL 스키마 완전 초기화")
    print("=" * 60)
    print("\n⚠️  경고: 이 작업은 모든 테이블과 뷰를 삭제하고")
    print("   schema.sql을 사용하여 새로 생성합니다.")
    print("   모든 데이터가 삭제됩니다!")
    print("=" * 60)
    
    # 확인 메시지
    response = input("\n⚠️  계속하시겠습니까? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        print("❌ 취소되었습니다.")
        exit(0)
    
    reset_schema()
    
    print("\n" + "=" * 60)
    print("완료!")
    print("=" * 60)

