"""
PDF 파일들의 총 페이지 수를 계산하는 스크립트
"""
import fitz  # PyMuPDF
from pathlib import Path
from collections import defaultdict


class PDFPageCounter:
    """PDF 파일들의 페이지 수를 세는 클래스"""
    
    def __init__(self, folders: list[str]):
        """
        Args:
            folders: PDF 파일을 찾을 폴더 경로 리스트
        """
        self.folders = folders
        self.results = defaultdict(int)  # 폴더별 결과 저장
        
    def count_pages(self, pdf_path: Path) -> int:
        """
        단일 PDF 파일의 페이지 수를 반환
        
        Args:
            pdf_path: PDF 파일 경로
            
        Returns:
            페이지 수 (에러 시 0)
        """
        try:
            doc = fitz.open(pdf_path)
            page_count = len(doc)
            doc.close()
            return page_count
        except Exception as e:
            print(f"오류 발생 ({pdf_path.name}): {e}")
            return 0
    
    def count_all_pdfs(self) -> dict:
        """
        모든 폴더의 PDF 파일 페이지 수를 계산
        
        Returns:
            폴더별 통계 정보 딕셔너리
        """
        total_pages = 0
        total_files = 0
        
        for folder_path in self.folders:
            folder = Path(folder_path)
            if not folder.exists():
                print(f"경고: 폴더가 존재하지 않습니다 - {folder_path}")
                continue
                
            folder_pages = 0
            folder_files = 0
            
            # 재귀적으로 모든 PDF 파일 찾기
            pdf_files = list(folder.rglob("*.pdf"))
            
            for pdf_file in pdf_files:
                pages = self.count_pages(pdf_file)
                folder_pages += pages
                folder_files += 1
                total_pages += pages
                total_files += 1
                
            self.results[folder_path] = {
                'files': folder_files,
                'pages': folder_pages
            }
        
        self.results['전체'] = {
            'files': total_files,
            'pages': total_pages
        }
        
        return self.results
    
    def print_results(self):
        """결과를 출력"""
        print("\n" + "="*60)
        print("PDF 페이지 수 통계")
        print("="*60)
        
        for folder, stats in self.results.items():
            if folder != '전체':
                print(f"\n[{folder}]")
                print(f"  파일 수: {stats['files']}개")
                print(f"  총 페이지: {stats['pages']}장")
        
        print("\n" + "-"*60)
        print(f"[전체 합계]")
        print(f"  총 파일 수: {self.results['전체']['files']}개")
        print(f"  총 페이지 수: {self.results['전체']['pages']}장")
        print("="*60 + "\n")


def main():
    """메인 함수"""
    # 검색할 폴더 경로
    base_path = Path(__file__).parent
    folders = [
        str(base_path / "test_img"),
        str(base_path / "img")
    ]
    
    # 페이지 수 계산
    counter = PDFPageCounter(folders)
    counter.count_all_pdfs()
    counter.print_results()


if __name__ == "__main__":
    main()

