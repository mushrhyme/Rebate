#!/bin/bash
# DB 삭제 및 재생성 스크립트 (벡터 DB 포함)

# 스크립트 위치 기준으로 프로젝트 루트 찾기
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RAG_DB_DIR="$PROJECT_ROOT/rag_db"

# 환경변수에서 DB 정보 가져오기 (없으면 기본값 사용)
DB_NAME=${DB_NAME:-rebate_db}
DB_USER=${DB_USER:-postgres}
DB_HOST=${DB_HOST:-localhost}
DB_PORT=${DB_PORT:-5432}

# 기본 데이터베이스 찾기 (template1은 항상 존재)
SYSTEM_DB=""
for db in template1 postgres template0; do
    if psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $db -c "SELECT 1;" >/dev/null 2>&1; then
        SYSTEM_DB=$db
        break
    fi
done

if [ -z "$SYSTEM_DB" ]; then
    echo "❌ 기본 데이터베이스에 연결할 수 없습니다."
    exit 1
fi

echo "🗑️  벡터 DB 폴더 삭제 중..."
# rag_db 폴더 삭제 (FAISS 벡터 DB 파일)
if [ -d "$RAG_DB_DIR" ]; then
    echo "  📁 rag_db 폴더 삭제 중: $RAG_DB_DIR"
    rm -rf "$RAG_DB_DIR"
    echo "  ✅ rag_db 폴더 삭제 완료"
else
    echo "  ℹ️  rag_db 폴더가 없습니다 (이미 삭제됨)"
fi

echo ""
echo "🔌 기존 데이터베이스 연결 종료 중..."
# 모든 활성 연결을 강제로 종료
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $SYSTEM_DB -c "
SELECT pg_terminate_backend(pg_stat_activity.pid)
FROM pg_stat_activity
WHERE pg_stat_activity.datname = '$DB_NAME'
  AND pid <> pg_backend_pid();
" 2>/dev/null || true

echo "🗑️  기존 데이터베이스 삭제 중..."
# 데이터베이스가 존재하는지 확인 후 삭제
if psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $SYSTEM_DB -lqt 2>/dev/null | cut -d \| -f 1 | grep -qw $DB_NAME; then
    # 강제로 삭제 시도 (연결이 있어도)
    dropdb -h $DB_HOST -p $DB_PORT -U $DB_USER --if-exists $DB_NAME 2>/dev/null || \
    psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $SYSTEM_DB -c "DROP DATABASE IF EXISTS $DB_NAME;" 2>/dev/null
    echo "  ✅ 데이터베이스 삭제 완료"
else
    echo "  ℹ️  데이터베이스가 존재하지 않음"
fi

echo "✅ 새 데이터베이스 생성 중..."
createdb -h $DB_HOST -p $DB_PORT -U $DB_USER $DB_NAME 2>/dev/null || \
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $SYSTEM_DB -c "CREATE DATABASE $DB_NAME;" >/dev/null 2>&1

echo "📋 스키마 적용 중..."
# 기존 객체들을 먼저 삭제
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME <<EOF 2>/dev/null || true
DROP VIEW IF EXISTS developer_session_comparison CASCADE;
DROP VIEW IF EXISTS user_excel_view CASCADE;
DROP TABLE IF EXISTS rag_learning_status CASCADE;
DROP TABLE IF EXISTS page_images CASCADE;
DROP TABLE IF EXISTS items CASCADE;
DROP TABLE IF EXISTS parsing_sessions CASCADE;
EOF

# 스키마 적용
psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d $DB_NAME -f schema.sql

# rag_db 폴더 재생성 (빈 폴더)
echo ""
echo "📁 rag_db 폴더 재생성 중..."
mkdir -p "$RAG_DB_DIR/shards"
chmod 755 "$RAG_DB_DIR" 2>/dev/null || true
chmod 755 "$RAG_DB_DIR/shards" 2>/dev/null || true
echo "  ✅ rag_db 폴더 재생성 완료"

echo ""
echo "✅ 데이터베이스 및 벡터 DB 재생성 완료!"

