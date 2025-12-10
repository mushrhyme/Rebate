"""
PostgreSQL 데이터베이스의 모든 테이블과 뷰를 삭제하는 스크립트

사용법:
    python database/drop_all_tables.py
"""

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from dotenv import load_dotenv
import os
from pathlib import Path

# .env 파일 로드
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

def drop_all_tables():
    """모든 테이블과 뷰를 삭제"""
    
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
        
        # 1. 기존 테이블 목록 확인
        print("\n📋 기존 테이블 목록 확인 중...")
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE'
            ORDER BY table_name;
        """)
        tables = [row[0] for row in cursor.fetchall()]
        print(f"   발견된 테이블: {tables if tables else '(없음)'}")
        
        # 2. 기존 뷰 목록 확인
        print("\n📋 기존 뷰 목록 확인 중...")
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.views 
            WHERE table_schema = 'public'
            ORDER BY table_name;
        """)
        views = [row[0] for row in cursor.fetchall()]
        print(f"   발견된 뷰: {views if views else '(없음)'}")
        
        if not tables and not views:
            print("\n✅ 삭제할 테이블이나 뷰가 없습니다.")
            return
        
        # 3. 뷰 삭제 (테이블보다 먼저 삭제해야 함)
        if views:
            print("\n🗑️  뷰 삭제 중...")
            for view in views:
                try:
                    cursor.execute(f'DROP VIEW IF EXISTS "{view}" CASCADE;')
                    print(f"   ✓ 뷰 삭제: {view}")
                except Exception as e:
                    print(f"   ✗ 뷰 삭제 실패 ({view}): {e}")
        
        # 4. 테이블 삭제
        if tables:
            print("\n🗑️  테이블 삭제 중...")
            for table in tables:
                try:
                    cursor.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE;')
                    print(f"   ✓ 테이블 삭제: {table}")
                except Exception as e:
                    print(f"   ✗ 테이블 삭제 실패 ({table}): {e}")
        
        # 5. 최종 확인
        print("\n📋 삭제 후 확인 중...")
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE';
        """)
        remaining_tables = cursor.fetchall()
        
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.views 
            WHERE table_schema = 'public';
        """)
        remaining_views = cursor.fetchall()
        
        if not remaining_tables and not remaining_views:
            print("✅ 모든 테이블과 뷰가 성공적으로 삭제되었습니다!")
        else:
            print(f"⚠️  일부 테이블/뷰가 남아있습니다:")
            if remaining_tables:
                print(f"   테이블: {[r[0] for r in remaining_tables]}")
            if remaining_views:
                print(f"   뷰: {[r[0] for r in remaining_views]}")
        
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
    print("PostgreSQL 테이블 및 뷰 삭제 스크립트")
    print("=" * 60)
    
    # 확인 메시지
    response = input("\n⚠️  모든 테이블과 뷰를 삭제하시겠습니까? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        print("❌ 취소되었습니다.")
        exit(0)
    
    drop_all_tables()
    
    print("\n" + "=" * 60)
    print("완료!")
    print("=" * 60)

