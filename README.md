# 条件請求書パースシステム (Rebate System)

일본어 조건청구서(条件請求書) PDF를 자동으로 분석하여 구조화된 데이터를 추출하는 시스템입니다.

## 📋 목차

- [프로젝트 개요](#프로젝트-개요)
- [주요 기능](#주요-기능)
- [프로젝트 구조](#프로젝트-구조)
- [기술 스택](#기술-스택)
- [데이터베이스 구조](#데이터베이스-구조)
- [주요 모듈 설명](#주요-모듈-설명)
- [환경 설정](#환경-설정)
- [사용 방법](#사용-방법)

---

## 프로젝트 개요

이 시스템은 일본어 조건청구서 PDF 파일을 업로드하면:
1. **OCR 텍스트 추출** (Upstage API)
2. **RAG 기반 구조화** (ChromaDB + OpenAI GPT-4)
3. **데이터베이스 저장** (PostgreSQL)
4. **Excel 다운로드** (수정 및 검토 후)

의 과정을 통해 상품별 내역을 자동으로 추출합니다.

---

## 주요 기능

### ✅ 현재 활성화된 기능

#### 1. **PDF 업로드 & 분석** (`modules/ui/upload_tab.py`)
- 여러 PDF 파일 동시 업로드
- RAG 기반 자동 파싱
- 진행률 표시
- 재분석 기능

#### 2. **리뷰 & 수정** (`modules/ui/review_tab.py`)
- 페이지별 이미지 및 파싱 결과 확인
- 테이블 편집 (AG Grid)
- 단일 페이지 재파싱 (Gemini Two-Stage Parser)
- 수정 내용 DB 저장

#### 3. **다운로드** (`modules/ui/download_tab.py`)
- Excel 파일 생성 및 다운로드
- 단일/다중 PDF 병합 지원
- 검증 상태 요약

#### 4. **정답지 편집** (`modules/ui/answer_editor_tab.py`)
- Upstage OCR 텍스트 확인
- OpenAI를 통한 JSON 추출
- 기준 페이지 참조 기능

### 🔄 향후 사용 가능한 기능 (현재 비활성화)

#### Gemini 기반 파싱 (`src/gemini_extractor.py`)
- `GeminiTwoStageParser`: 2단계 파이프라인 (Vision → Text)
- `extract_pages_with_gemini()`: 전체 PDF Gemini 파싱
- **상태**: 리뷰 탭에서 단일 페이지 재파싱에만 사용 중

---

## 프로젝트 구조

```
Rebate/
├── app.py                          # 메인 엔트리 포인트
├── modules/                        # 핵심 모듈
│   ├── core/                       # 비즈니스 로직
│   │   ├── processor.py           # PDF 처리 중앙화
│   │   ├── registry.py            # PDF 메타데이터 관리
│   │   ├── storage.py             # 페이지 결과 파일 저장
│   │   ├── rag_manager.py         # RAG 벡터 DB 관리
│   │   └── app_processor.py       # UI 레벨 PDF 처리 헬퍼
│   ├── ui/                         # Streamlit UI
│   │   ├── app_views.py           # 메인 UI (탭 라우팅)
│   │   ├── upload_tab.py          # 업로드 & 분석 탭
│   │   ├── review_tab.py          # 리뷰 탭
│   │   ├── download_tab.py        # 다운로드 탭
│   │   ├── answer_editor_tab.py   # 정답지 편집 탭
│   │   ├── review_components.py   # 리뷰 컴포넌트
│   │   └── aggrid_utils.py        # AG Grid 유틸리티
│   └── utils/                      # 유틸리티
│       ├── config.py               # 환경 설정
│       ├── pdf_utils.py            # PDF 경로 찾기
│       ├── merge_utils.py          # 데이터 병합
│       ├── openai_utils.py         # OpenAI API 헬퍼
│       └── session_utils.py       # 세션 상태 관리
├── src/                            # 추출기 모듈
│   ├── pdf_processor.py           # PDF → 이미지 변환 (PdfImageConverter)
│   ├── upstage_extractor.py       # Upstage OCR API
│   ├── rag_extractor.py           # RAG 기반 JSON 추출
│   ├── rag_pages_extractor.py     # RAG 기반 페이지 추출
│   ├── openai_extractor.py        # OpenAI 텍스트 파서
│   └── gemini_extractor.py        # Gemini 파서 (향후 사용)
├── database/                       # 데이터베이스
│   ├── db_manager.py              # PostgreSQL 관리
│   ├── registry.py                # DB 싱글톤
│   └── schema.sql                 # DB 스키마
├── utils/                          # 세션 관리
│   └── session_manager.py         # 세션별 파일 관리
├── prompts/                        # 프롬프트 파일
│   ├── prompt_v1.txt
│   └── prompt_v2.txt
├── chroma_db/                      # RAG 벡터 DB (ChromaDB)
├── debug/                          # 디버깅 정보 저장
└── img/                            # PDF 이미지 저장
```

---

## 기술 스택

### 프론트엔드
- **Streamlit**: 웹 UI 프레임워크
- **AG Grid**: 데이터 테이블 편집

### 백엔드
- **Python 3.x**

### AI/ML
- **OpenAI GPT-4**: JSON 구조화 (메인 파싱)
- **Upstage OCR**: 텍스트 추출
- **Google Gemini**: 단일 페이지 재파싱 (향후 확장 가능)
- **ChromaDB**: RAG 벡터 데이터베이스

### 데이터베이스
- **PostgreSQL**: 파싱 결과 저장
  - `parsing_sessions`: 파싱 세션 관리
  - `items`: 상품 데이터
  - `page_images`: 페이지 이미지

### 이미지 처리
- **pdf2image**: PDF → 이미지 변환
- **Pillow (PIL)**: 이미지 처리

### 데이터 처리
- **pandas**: 데이터프레임 처리
- **openpyxl**: Excel 파일 생성

---

## 데이터베이스 구조

### 테이블 구조

#### 1. `parsing_sessions` (파싱 세션)
- `session_id`: 세션 고유 ID
- `pdf_filename`: PDF 파일명
- `session_name`: 세션명
- `is_latest`: 최신 세션 여부
- `parsing_timestamp`: 파싱 실행 일시
- `notes`: 메모

#### 2. `items` (상품 데이터)
- `item_id`: 항목 고유 ID
- `session_id`: 세션 ID (FK)
- `management_id`: 관리번호
- `customer`: 거래처
- `product_name`: 상품명
- `quantity`: 수량
- `case_count`: 케이스 수
- `bara_count`: 바라 수
- `units_per_case`: 케이스당 개수
- `amount`: 금액
- `page_number`: 페이지 번호
- `page_role`: 페이지 역할

#### 3. `page_images` (페이지 이미지)
- `image_id`: 이미지 고유 ID
- `session_id`: 세션 ID (FK)
- `page_number`: 페이지 번호
- `image_data`: 이미지 데이터 (BYTEA)
- `image_format`: 이미지 형식
- `image_size`: 이미지 크기

### 뷰 (View)

#### `user_excel_view`
- 최신 세션의 데이터만 조회
- Excel 다운로드용

#### `developer_session_comparison`
- 여러 세션 비교용
- 개발자용 통계

---

## 주요 모듈 설명

### 1. PDF 처리 파이프라인

```
PDF 업로드
  ↓
PdfImageConverter (PDF → 이미지)
  ↓
UpstageExtractor (OCR 텍스트 추출)
  ↓
RAGManager (벡터 DB에서 유사 예제 검색)
  ↓
OpenAI GPT-4 (JSON 구조화)
  ↓
DatabaseManager (PostgreSQL 저장)
```

### 2. 핵심 클래스

#### `PdfProcessor` (`modules/core/processor.py`)
- PDF 처리 로직 중앙화
- RAG 기반 파싱 실행
- DB 저장 관리

#### `DatabaseManager` (`database/db_manager.py`)
- PostgreSQL 연결 풀 관리
- 파싱 결과 CRUD
- 세션 관리

#### `RAGManager` (`modules/core/rag_manager.py`)
- ChromaDB 벡터 검색
- 유사 예제 검색
- RAG 컨텍스트 구성

#### `PdfImageConverter` (`src/pdf_processor.py`)
- PDF → PIL Image 변환
- 이미지 저장 (선택적)

### 3. Extractor 모듈

#### 활성화된 Extractor
- **`extract_pages_with_rag()`**: 메인 파싱 (RAG + OpenAI)
- **`UpstageExtractor`**: OCR 텍스트 추출
- **`OpenAITextParser`**: 텍스트 → JSON 변환

#### 향후 사용 가능한 Extractor
- **`GeminiTwoStageParser`**: Gemini 2단계 파이프라인
  - 현재: 사용되지 않음 (향후 옵션으로 추가 가능)
  - 향후: 전체 PDF 파싱 옵션으로 확장 가능

---

## 환경 설정

### 1. 환경변수 (`.env` 파일)

```env
# OpenAI
OPENAI_API_KEY=your_openai_api_key

# Upstage
UPSTAGE_API_KEY=your_upstage_api_key

# Gemini (선택적)
GEMINI_API_KEY=your_gemini_api_key

# PostgreSQL (선택적)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=rebate_db
DB_USER=postgres
DB_PASSWORD=your_password
DB_MIN_CONN=1
DB_MAX_CONN=10
```

### 2. 의존성 설치

```bash
pip install -r requirements.txt
```

### 3. 데이터베이스 설정

```bash
# PostgreSQL 데이터베이스 생성
createdb rebate_db

# 스키마 적용
psql -d rebate_db -f database/schema.sql
```

---

## 사용 방법

### 1. 애플리케이션 실행

```bash
streamlit run app.py
```

### 2. 주요 워크플로우

#### PDF 분석
1. **업로드 탭**: PDF 파일 업로드
2. **🔍 解析実行** 클릭 → 자동 파싱
3. **리뷰 탭**: 결과 확인 및 수정
4. **다운로드 탭**: Excel 다운로드

#### 단일 페이지 재파싱
1. **리뷰 탭**: 특정 페이지 선택
2. **🔄 再パース** 클릭 → RAG 기반 재파싱 (업로드 탭과 동일한 방식)
3. 결과 확인 및 저장

---

## 기능 상태 정리

### ✅ 활성화된 기능
- [x] PDF 업로드 & 분석 (RAG 기반)
- [x] 리뷰 & 수정 (AG Grid)
- [x] Excel 다운로드
- [x] 정답지 편집
- [x] 단일 페이지 재파싱 (RAG 기반)
- [x] PostgreSQL 데이터베이스 저장
- [x] RAG 벡터 검색

### 🔄 향후 확장 가능한 기능
- [ ] 전체 PDF Gemini 파싱 옵션
- [ ] Gemini Vision 파서 (현재 레거시)
- [ ] 캐시 시스템 (현재 DB 사용)

### 🗑️ 제거된 기능
- [x] 중복 래퍼 함수 (`extract_json_from_text`)
- [x] 사용되지 않는 standalone 함수들
- [x] 레거시 검토 데이터 저장 (`save_review_data`)

---

## 개발 노트

### 최근 정리 작업
1. **클래스명 개선**: `PDFProcessor` → `PdfImageConverter`
2. **중복 코드 제거**: 래퍼 함수 제거
3. **레거시 코드 정리**: 사용되지 않는 함수 제거
4. **구조 개선**: `merge_utils` 이동 (`parser/` → `modules/utils/`)

### 향후 개선 방향
- Gemini 파서 통합 옵션 추가
- 캐시 시스템 개선
- 에러 핸들링 강화
- 테스트 코드 추가

---

## 라이선스

내부 사용 전용

