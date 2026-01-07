"""
PDF를 엑셀로 변환하는 사용 예시

이 예시는 PDF를 엑셀로 변환하거나 LLM에 전달하기 적합한 형식으로 변환하는 방법을 보여줍니다.
"""

from pathlib import Path
from modules.utils.pdf_to_excel import (
    PdfToExcelConverter,
    convert_pdf_page_to_excel,
    convert_pdf_page_to_text_for_llm
)


def example_1_convert_to_excel():
    """예시 1: PDF 페이지를 엑셀 파일로 변환"""
    pdf_path = Path("img/02/조건청구서② M0059065511500-農心ジャパン202502/조건청구서② M0059065511500-農心ジャパン202502.pdf")
    page_num = 1
    
    # pdfplumber 사용 (권장)
    excel_path = convert_pdf_page_to_excel(
        pdf_path=pdf_path,
        page_num=page_num,
        method="pdfplumber"
    )
    
    if excel_path:
        print(f"✅ 엑셀 파일 생성: {excel_path}")
    else:
        print("❌ 엑셀 파일 생성 실패")


def example_2_convert_to_text_for_llm():
    """예시 2: PDF를 LLM에 전달하기 적합한 텍스트 형식으로 변환"""
    pdf_path = Path("img/02/조건청구서② M0059065511500-農心ジャパン202502/조건청구서② M0059065511500-農心ジャパン202502.pdf")
    page_num = 1
    
    # 테이블 형태의 텍스트로 변환
    text = convert_pdf_page_to_text_for_llm(
        pdf_path=pdf_path,
        page_num=page_num,
        method="pdfplumber"
    )
    
    print("=== LLM에 전달할 텍스트 ===")
    print(text)
    
    # 이 텍스트를 LLM 프롬프트에 사용할 수 있습니다
    # 예: ocr_text = text


def example_3_use_in_rag_extractor():
    """예시 3: RAG 추출기에서 엑셀 변환 텍스트 사용"""
    from src.rag_extractor import extract_json_with_rag
    
    pdf_path = Path("img/02/조건청구서② M0059065511500-農心ジャパン202502/조건청구서② M0059065511500-農心ジャパン202502.pdf")
    page_num = 1
    
    # PDF를 테이블 형태의 텍스트로 변환
    ocr_text = convert_pdf_page_to_text_for_llm(
        pdf_path=pdf_path,
        page_num=page_num,
        method="pdfplumber"
    )
    
    # RAG 추출기에 전달
    result = extract_json_with_rag(
        ocr_text=ocr_text,
        page_num=page_num
    )
    
    print("=== 추출 결과 ===")
    print(result)


def example_4_compare_methods():
    """예시 4: 여러 방법 비교"""
    pdf_path = Path("img/02/조건청구서② M0059065511500-農心ジャパン202502/조건청구서② M0059065511500-農心ジャパン202502.pdf")
    page_num = 1
    
    methods = ["pdfplumber", "pymupdf"]  # tabula는 Java 필요
    
    for method in methods:
        print(f"\n=== {method} 방법 ===")
        try:
            converter = PdfToExcelConverter(method=method)
            tables = converter.extract_tables(pdf_path, page_num)
            
            print(f"추출된 테이블 수: {len(tables)}")
            for idx, df in enumerate(tables):
                print(f"\n테이블 {idx+1}:")
                print(df.head())
        except Exception as e:
            print(f"❌ 오류: {e}")


if __name__ == "__main__":
    print("PDF를 엑셀로 변환하는 예시\n")
    
    # 예시 실행 (원하는 것만 주석 해제)
    # example_1_convert_to_excel()
    # example_2_convert_to_text_for_llm()
    # example_3_use_in_rag_extractor()
    # example_4_compare_methods()

