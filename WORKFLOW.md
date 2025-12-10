# PDF 파일 업로드 및 분석 완료까지의 작동 로직

## 목차
1. [전체 흐름 개요](#전체-흐름-개요)
2. [파일 업로드 단계](#파일-업로드-단계)
3. [분석 실행 단계](#분석-실행-단계)
4. [데이터베이스 저장 단계](#데이터베이스-저장-단계)
5. [상태 관리 및 동기화](#상태-관리-및-동기화)
6. [데이터베이스 구조](#데이터베이스-구조)
7. [주요 컴포넌트 설명](#주요-컴포넌트-설명)

---

## 전체 흐름 개요

```
사용자 파일 업로드
    ↓
파일 저장 (SessionManager)
    ↓
파일 정보 등록 (st.session_state.uploaded_files_info)
    ↓
DB 존재 여부 확인 (DatabaseManager.has_pdf_in_db)
    ↓
[분석 실행 버튼 클릭]
    ↓
pdf_registry.json에 등록 (PdfRegistry.ensure)
    ↓
PDF 파싱 시작 (PdfProcessor.process_pdf)
    ↓
VisionParser로 PDF → 이미지 변환 및 OCR
    ↓
페이지별 파싱 결과 생성 (page_results)
    ↓
DB에 저장 (DatabaseManager.save_from_page_results)
    ↓
이미지 DB 저장 (DatabaseManager.save_page_images)
    ↓
pdf_registry.json에서 제거 (PdfRegistry.remove)
    ↓
분석 완료
```

---

## 파일 업로드 단계

### 1. 파일 업로더 처리 (`app.py` - `render_upload_tab()`)

**위치**: `app.py` 라인 456-586

```python
# Streamlit 파일 업로더
uploaded_files = st.file_uploader(
    "PDFファイルをアップロードしてください（複数ファイル選択可能）",
    type=['pdf'],
    accept_multiple_files=True
)
```

**처리 로직**:

1. **파일 업로더 변경 감지**
   - `current_uploaded_names`: 현재 업로더에 선택된 파일명 집합
   - `prev_uploaded_files`: 이전에 업로드된 파일명 집합
   - 두 집합을 비교하여 추가/제거된 파일 감지

2. **제거된 파일 처리** (라인 494-507)
   ```python
   removed_from_uploader = st.session_state.prev_uploaded_files - current_uploaded_names
   for pdf_name in removed_from_uploader:
       # uploaded_files_info에서 제거
       st.session_state.uploaded_files_info = [
           info for info in st.session_state.uploaded_files_info
           if info['name'] != pdf_name
       ]
       # 세션 상태에서도 제거
       st.session_state.analysis_status.pop(pdf_name, None)
       st.session_state.review_data.pop(pdf_name, None)
       # pdf_registry.json에서도 제거
       PdfRegistry.remove(pdf_name)
   ```

3. **새 파일 추가** (라인 530-578)
   - `list_cleared` 플래그가 `False`일 때만 처리
   - 각 파일에 대해:
     - **DB 존재 여부 확인**: `DatabaseManager.has_pdf_in_db()`
     - **페이지 수 확인**: `DatabaseManager.get_page_results()`
     - `uploaded_files_info`에 파일 정보 추가:
       ```python
       {
           "name": pdf_name,              # 확장자 제외 파일명
           "original_name": uploaded_file.name,  # 원본 파일명
           "size": uploaded_file.size,    # 파일 크기
           "is_in_db": is_in_db,          # DB 존재 여부
           "db_page_count": db_page_count # DB에 저장된 페이지 수
       }
       ```

4. **파일 저장** (`SessionManager.save_pdf_file()`)
   - **위치**: `utils/session_manager.py` 라인 125-142
   - **저장 경로**: `/tmp/{session_id}/pdfs/{pdf_name}.pdf`
   - **동작**: Streamlit `UploadedFile` 객체의 바이너리 데이터를 파일 시스템에 저장

---

## 분석 실행 단계

### 1. 분석 버튼 클릭 처리 (`app.py` - `render_upload_tab()`)

**위치**: `app.py` 라인 650-700

**처리 로직**:

1. **분석 대상 파일 선택**
   - `files_to_reprocess`: 재분석할 파일 인덱스 리스트
   - `files_to_analyze`: 새로 분석할 파일 인덱스 리스트

2. **멀티스레딩 분석 시작** (라인 680-700)
   ```python
   start_time = time.time()  # 전체 분석 시작 시간 기록
   
   with ThreadPoolExecutor(max_workers=3) as executor:
       futures = {}
       for idx in files_to_analyze:
           file_info = st.session_state.uploaded_files_info[idx]
           pdf_name = file_info['name']
           
           # 비동기 분석 시작
           future = executor.submit(
               analyze_single_pdf,
               pdf_name=pdf_name,
               progress_callback=create_progress_callback(pdf_name)
           )
           futures[future] = pdf_name
   ```

3. **개별 파일 분석 함수** (`analyze_single_pdf()`)
   - **위치**: `app.py` 라인 200-273
   - **처리 순서**:
     1. PDF 파일 경로 찾기: `find_pdf_path(pdf_name)`
     2. `PdfProcessor.process_pdf()` 호출
     3. 결과 처리 및 상태 업데이트

### 2. PDF 처리 (`PdfProcessor.process_pdf()`)

**위치**: `modules/core/processor.py` 라인 26-150

**처리 단계**:

#### 2.1. 초기화 및 등록
```python
# PDF 파일 경로 확인
if pdf_path is None:
    pdf_path = find_pdf_path(pdf_name)

# pdf_registry.json에 등록 (분석 대기열에 추가)
PdfRegistry.ensure(pdf_name, source="session")
PdfRegistry.update(pdf_name, status="processing", pages=0, error=None)
```

#### 2.2. PDF 파싱 (`VisionParser.parse_pdf()`)
- **입력**: PDF 파일 경로, DPI 설정 (기본 300)
- **출력**: 
  - `page_results`: 페이지별 파싱 결과 리스트
  - `image_paths`: 각 페이지의 이미지 파일 경로 리스트
- **처리 내용**:
  - PDF → 이미지 변환 (PIL/Pillow 사용)
  - 각 페이지 이미지를 Gemini Vision API로 OCR 분석
  - JSON 형식의 파싱 결과 생성

#### 2.3. 데이터베이스 저장
```python
db_manager = DatabaseManager(
    host=os.getenv('DB_HOST', 'localhost'),
    port=int(os.getenv('DB_PORT', '5432')),
    database=os.getenv('DB_NAME', 'rebate_db'),
    user=os.getenv('DB_USER', 'postgres'),
    password=os.getenv('DB_PASSWORD', '')
)

session_id = db_manager.save_from_page_results(
    page_results=page_results,
    pdf_filename=f"{pdf_name}.pdf",
    session_name=f"自動パース {pdf_name}",
    notes=None,
    image_paths=image_paths  # 이미지 경로 전달
)
```

#### 2.4. 완료 처리
```python
# 분석 완료 시 pdf_registry.json에서 제거
PdfRegistry.remove(pdf_name)

return True, len(page_results), None, elapsed_time
```

---

## 데이터베이스 저장 단계

### 1. `DatabaseManager.save_from_page_results()`

**위치**: `database/db_manager.py` 라인 223-360

**처리 순서**:

#### 1.1. 파싱 세션 생성 (`create_session()`)
```python
session_id = self.create_session(
    pdf_filename=pdf_filename,
    session_name=session_name,
    notes=notes,
    is_latest=True  # 최신 세션으로 표시
)
```

**SQL 쿼리**:
```sql
INSERT INTO parsing_sessions (
    pdf_filename, session_name, is_latest, notes
) VALUES (%s, %s, %s, %s)
RETURNING session_id
```

**테이블 구조** (`parsing_sessions`):
- `session_id` (SERIAL PRIMARY KEY)
- `pdf_filename` (TEXT)
- `session_name` (TEXT)
- `is_latest` (BOOLEAN) - 해당 PDF의 최신 파싱 결과 여부
- `parsing_timestamp` (TIMESTAMP) - 자동 생성
- `notes` (TEXT, NULL 가능)

#### 1.2. 문서 메타데이터 추출
```python
first_page = page_results[0]
issuer = first_page.get('issuer')           # 발행자
issue_date = first_page.get('issue_date')   # 발행일
billing_period = first_page.get('billing_period')  # 청구 기간

# 전체 총액 계산
total_amount_document = 0
for page in page_results:
    for item in page.get('items', []):
        amount = self._parse_amount(item.get('amount'))
        if amount:
            total_amount_document += amount
```

#### 1.3. Items 데이터 준비 및 일괄 삽입
```python
items_data = []
item_order = 0

for page_idx, page_json in enumerate(page_results):
    page_number = page_idx + 1  # 1부터 시작
    page_index = page_idx        # 0부터 시작
    page_role = page_json.get('page_role', 'main')
    page_customer = page_json.get('customer')
    
    for item in page_json.get('items', []):
        item_order += 1
        customer = item.get('customer') or page_customer
        
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

# 일괄 삽입 (execute_values 사용)
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
```

**테이블 구조** (`items`):
- `item_id` (SERIAL PRIMARY KEY)
- `session_id` (INTEGER, FOREIGN KEY → parsing_sessions.session_id)
- `management_id` (TEXT) - 관리 ID
- `customer` (TEXT) - 거래처
- `product_name` (TEXT) - 상품명
- `quantity` (NUMERIC) - 수량
- `case_count` (NUMERIC) - 케이스 수
- `bara_count` (NUMERIC) - 바라 수
- `units_per_case` (NUMERIC) - 케이스당 단위 수
- `amount` (NUMERIC) - 금액
- `page_number` (INTEGER) - 페이지 번호 (1부터 시작)
- `page_role` (TEXT) - 페이지 역할 ('main', 'continuation' 등)
- `issuer` (TEXT) - 발행자
- `issue_date` (DATE) - 발행일
- `billing_period` (TEXT) - 청구 기간
- `total_amount_document` (NUMERIC) - 문서 전체 총액
- `pdf_filename` (TEXT) - PDF 파일명
- `page_index` (INTEGER) - 페이지 인덱스 (0부터 시작)
- `item_order` (INTEGER) - 항목 순서

#### 1.4. 이미지 저장 (`save_page_images()`)
```python
if image_paths:
    images_to_save = []
    for page_idx, image_path in enumerate(image_paths):
        if image_path and os.path.exists(image_path):
            with open(image_path, 'rb') as f:
                image_data = f.read()
            page_number = page_idx + 1
            images_to_save.append((page_number, image_data))
    
    if images_to_save:
        self.save_page_images(session_id, images_to_save)
```

**이미지 저장 테이블** (`page_images`):
- `image_id` (SERIAL PRIMARY KEY)
- `session_id` (INTEGER, FOREIGN KEY)
- `page_number` (INTEGER)
- `image_data` (BYTEA) - PNG 이미지 바이너리 데이터

**SQL 쿼리**:
```sql
INSERT INTO page_images (session_id, page_number, image_data)
VALUES (%s, %s, %s)
```

---

## 상태 관리 및 동기화

### 1. `pdf_registry.json` 관리

**역할**: 분석 대기열 관리 (현재 분석 중인 파일만 추적)

**위치**: `modules/core/registry.py`

**생명주기**:

1. **등록 시점**: 분석 시작 시 (`PdfProcessor.process_pdf()`)
   ```python
   PdfRegistry.ensure(pdf_name, source="session")
   PdfRegistry.update(pdf_name, status="processing", pages=0, error=None)
   ```

2. **업데이트 시점**: 분석 진행 중 (Heartbeat)
   ```python
   # 각 페이지 처리 후
   PdfRegistry.update(pdf_name)  # last_updated 자동 갱신
   ```

3. **제거 시점**:
   - 분석 완료 시: `PdfRegistry.remove(pdf_name)`
   - 분석 에러 시: `PdfRegistry.remove(pdf_name)`
   - 파일 업로더에서 제거 시: `PdfRegistry.remove(pdf_name)`

**데이터 구조**:
```json
{
  "pdf_name": {
    "status": "processing" | "completed" | "error" | "pending",
    "pages": 0,
    "error": null,
    "source": "session",
    "last_updated": "2025-01-20T10:30:00"
  }
}
```

### 2. Streamlit 세션 상태 관리

**주요 상태 변수** (`st.session_state`):

1. **`uploaded_files_info`**: 업로드된 파일 정보 리스트
   ```python
   [
       {
           "name": "pdf_name",
           "original_name": "original.pdf",
           "size": 12345,
           "is_in_db": True,
           "db_page_count": 10
       },
       ...
   ]
   ```

2. **`analysis_status`**: 분석 상태 딕셔너리
   ```python
   {
       "pdf_name": {
           "status": "processing" | "completed" | "error",
           "pages": 10,
           "error": None
       },
       ...
   }
   ```

3. **`prev_uploaded_files`**: 이전 업로드 파일명 집합
   ```python
   set(["pdf_name1", "pdf_name2", ...])
   ```

4. **`list_cleared`**: 목록 초기화 플래그
   ```python
   False  # 목록이 초기화되지 않음
   True   # 목록이 초기화됨 (DB 조회 및 파일 추가 차단)
   ```

5. **`review_data`**: 리뷰 탭에서 수정된 데이터
   ```python
   {
       "pdf_name": {
           "items": [...],  # 수정된 항목 리스트
           ...
       }
   }
   ```

### 3. 상태 동기화 로직

**위치**: `app.py` 라인 360-430

**처리 순서**:

1. **`pdf_registry.json` 로드 및 동기화**
   ```python
   registry_statuses = SessionManager.get_all_analysis_statuses()
   for pdf_name, status_data in registry_statuses.items():
       # 세션 상태와 동기화
       if status_data.get("status") == "completed":
           # DB 확인
           page_count = SessionManager.get_pdf_page_count(pdf_name)
           if page_count == 0:
               PdfRegistry.remove(pdf_name)  # DB에 없으면 제거
   ```

2. **분석 중 파일 경고 표시**
   ```python
   processing_files = [
       pdf_name for pdf_name, status_info in st.session_state.analysis_status.items()
       if status_info.get("status") == "processing"
   ]
   
   if processing_files:
       st.warning(f"分析中のファイルがあります: {', '.join(processing_files)}")
   ```

---

## 데이터베이스 구조

### 테이블 관계도

```
parsing_sessions (1) ──< (N) items
parsing_sessions (1) ──< (N) page_images
```

### 주요 테이블

#### 1. `parsing_sessions`
- **역할**: PDF 파싱 세션 메타데이터
- **주요 컬럼**:
  - `session_id`: 세션 고유 ID
  - `pdf_filename`: PDF 파일명
  - `session_name`: 세션 이름
  - `is_latest`: 최신 파싱 결과 여부
  - `parsing_timestamp`: 파싱 시각

#### 2. `items`
- **역할**: 파싱된 항목 데이터
- **주요 컬럼**:
  - `item_id`: 항목 고유 ID
  - `session_id`: 세션 ID (FK)
  - `management_id`: 관리 ID
  - `customer`: 거래처
  - `product_name`: 상품명
  - `quantity`: 수량
  - `amount`: 금액
  - `page_number`: 페이지 번호
  - `item_order`: 항목 순서

#### 3. `page_images`
- **역할**: 페이지 이미지 저장
- **주요 컬럼**:
  - `image_id`: 이미지 고유 ID
  - `session_id`: 세션 ID (FK)
  - `page_number`: 페이지 번호
  - `image_data`: 이미지 바이너리 데이터 (BYTEA)

### 주요 쿼리 패턴

#### 1. PDF 존재 여부 확인
```python
db_manager.has_pdf_in_db(pdf_filename, is_latest_only=True)
```
**SQL**:
```sql
SELECT EXISTS(
    SELECT 1 FROM parsing_sessions
    WHERE pdf_filename = %s AND is_latest = TRUE
)
```

#### 2. 페이지 결과 조회
```python
db_manager.get_page_results(
    pdf_filename=pdf_filename,
    session_id=None,
    is_latest=True
)
```
**SQL**:
```sql
SELECT 
    i.*,
    ps.session_name,
    ps.parsing_timestamp
FROM items i
JOIN parsing_sessions ps ON i.session_id = ps.session_id
WHERE ps.pdf_filename = %s AND ps.is_latest = TRUE
ORDER BY i.page_number, i.item_order
```

#### 3. 최신 세션 업데이트
```python
# 새 세션 생성 시 기존 세션의 is_latest를 False로 변경
UPDATE parsing_sessions
SET is_latest = FALSE
WHERE pdf_filename = %s AND is_latest = TRUE
```

---

## 주요 컴포넌트 설명

### 1. `SessionManager` (`utils/session_manager.py`)

**역할**: 세션별 파일 관리

**주요 메서드**:

- `save_pdf_file(uploaded_file, pdf_name)`: PDF 파일 저장
- `save_page_image(image, pdf_name, page_num)`: 페이지 이미지 저장
- `get_pdfs_dir()`: PDF 저장 디렉토리 경로 반환
- `get_images_dir()`: 이미지 저장 디렉토리 경로 반환
- `get_pdf_page_count(pdf_name)`: DB에서 페이지 수 조회
- `get_all_analysis_statuses()`: `pdf_registry.json`에서 분석 상태 조회

### 2. `PdfRegistry` (`modules/core/registry.py`)

**역할**: PDF 메타데이터 레지스트리 관리

**주요 메서드**:

- `ensure(pdf_name, **default_fields)`: PDF 등록 (없으면 생성)
- `update(pdf_name, **fields)`: 메타데이터 업데이트
- `remove(pdf_name)`: PDF 제거
- `get(pdf_name)`: 메타데이터 조회
- `get_by_status(status)`: 상태별 PDF 목록 조회
- `get_by_source(source)`: 소스별 PDF 목록 조회

**특징**: 원자적 파일 I/O 사용 (임시 파일 → 원자적 이동)

### 3. `PdfProcessor` (`modules/core/processor.py`)

**역할**: PDF 처리 로직 중앙화

**주요 메서드**:

- `process_pdf(pdf_name, pdf_path, dpi, progress_callback)`: PDF 처리 메인 함수
  - PDF 파싱
  - DB 저장
  - 상태 관리

### 4. `DatabaseManager` (`database/db_manager.py`)

**역할**: PostgreSQL 데이터베이스 관리

**주요 메서드**:

- `create_session(pdf_filename, session_name, notes, is_latest)`: 파싱 세션 생성
- `save_from_page_results(page_results, pdf_filename, ...)`: 페이지 결과 저장
- `save_page_images(session_id, images_to_save)`: 페이지 이미지 저장
- `get_page_results(pdf_filename, session_id, is_latest)`: 페이지 결과 조회
- `has_pdf_in_db(pdf_filename, is_latest_only)`: PDF 존재 여부 확인
- `get_items(pdf_filename, session_id, is_latest)`: 항목 목록 조회

**특징**: 연결 풀 사용 (`SimpleConnectionPool`)

### 5. `VisionParser` (`parser/vision_parser.py`)

**역할**: PDF → 이미지 변환 및 OCR 분석

**주요 메서드**:

- `parse_pdf(pdf_path, dpi, use_cache, save_images, ...)`: PDF 파싱
  - PDF → 이미지 변환
  - Gemini Vision API 호출
  - JSON 결과 생성

---

## 데이터 흐름 다이어그램

### 파일 업로드 → 분석 완료

```
[사용자]
    ↓ 파일 선택
[Streamlit file_uploader]
    ↓
[app.py: render_upload_tab()]
    ├─→ SessionManager.save_pdf_file()
    │   └─→ /tmp/{session_id}/pdfs/{pdf_name}.pdf 저장
    │
    ├─→ DatabaseManager.has_pdf_in_db()
    │   └─→ DB 조회 (parsing_sessions 테이블)
    │
    └─→ st.session_state.uploaded_files_info에 추가
        └─→ {"name", "original_name", "size", "is_in_db", "db_page_count"}

[분석 실행 버튼 클릭]
    ↓
[app.py: analyze_single_pdf()]
    ↓
[PdfProcessor.process_pdf()]
    ├─→ PdfRegistry.ensure()  # pdf_registry.json에 등록
    │
    ├─→ VisionParser.parse_pdf()
    │   ├─→ PDF → 이미지 변환
    │   ├─→ Gemini Vision API 호출 (각 페이지)
    │   └─→ page_results 생성
    │
    ├─→ DatabaseManager.save_from_page_results()
    │   ├─→ create_session() → parsing_sessions 테이블에 INSERT
    │   ├─→ items 테이블에 일괄 INSERT (execute_values)
    │   └─→ save_page_images() → page_images 테이블에 INSERT
    │
    └─→ PdfRegistry.remove()  # pdf_registry.json에서 제거

[분석 완료]
    ↓
[app.py: 상태 업데이트]
    └─→ st.session_state.analysis_status[pdf_name] = {
            "status": "completed",
            "pages": page_count,
            "error": None
        }
```

### DB 조회 흐름 (Review/Download 탭)

```
[Review/Download 탭]
    ↓
[app.py: render_review_tab() / render_download_tab()]
    ↓
[st.session_state.uploaded_files_info에서 파일 목록 가져오기]
    ↓
[DatabaseManager.get_page_results()]
    ├─→ parsing_sessions JOIN items
    └─→ WHERE pdf_filename = %s AND is_latest = TRUE
    ↓
[페이지별 데이터 그룹화]
    ↓
[AG Grid 테이블 표시 / JSON 다운로드]
```

---

## 주요 설계 원칙

### 1. 단일 소스 원칙
- **파일 목록**: `st.session_state.uploaded_files_info`가 단일 소스
- **분석 상태**: `pdf_registry.json`은 분석 대기열만 관리 (완료 시 제거)
- **데이터 저장**: PostgreSQL이 단일 소스 (파일 시스템은 임시 저장용)

### 2. 원자적 연산
- `PdfRegistry`: 임시 파일 → 원자적 이동으로 파일 손상 방지
- `PageStorage`: 원자적 파일 I/O 사용
- DB 트랜잭션: `with conn.cursor() as cursor` 사용

### 3. 상태 동기화
- `pdf_registry.json` ↔ `st.session_state.analysis_status` 동기화
- DB 존재 여부 확인 후 `uploaded_files_info` 업데이트
- 파일 업로더 변경 감지 및 자동 동기화

### 4. 에러 처리
- DB 연결 실패 시 예외 처리
- 파일 I/O 실패 시 예외 처리
- 분석 실패 시 `pdf_registry.json`에서 제거

---

## 환경 변수

다음 환경 변수를 설정해야 합니다:

```bash
DB_HOST=localhost          # PostgreSQL 호스트
DB_PORT=5432              # PostgreSQL 포트
DB_NAME=rebate_db         # 데이터베이스 이름
DB_USER=postgres          # 사용자 이름
DB_PASSWORD=              # 비밀번호
```

---

## 참고 사항

1. **임시 파일**: PDF 및 이미지는 `/tmp/{session_id}/` 디렉토리에 저장되며, DB 저장 후 삭제 가능
2. **멀티스레딩**: 최대 3개의 파일을 동시에 분석 (`ThreadPoolExecutor(max_workers=3)`)
3. **분석 속도**: 실제 경과 시간 측정 (`time.time()` 사용)
4. **최신 세션**: `is_latest=True`로 최신 파싱 결과만 조회
5. **페이지 번호**: 1부터 시작 (DB 저장 시 `page_number = page_idx + 1`)

