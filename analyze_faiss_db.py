"""
벡터 DB의 중복 저장 여부를 분석하는 스크립트
"""
import json
from collections import Counter
from pathlib import Path


def analyze_faiss_db():
    """벡터 DB의 중복 저장 여부 분석"""
    metadata_path = Path("faiss_db/metadata.json")
    
    if not metadata_path.exists():
        print("❌ metadata.json 파일을 찾을 수 없습니다.")
        return
    
    # 메타데이터 로드
    with open(metadata_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # (PDF명, 페이지번호)별 저장 횟수 계산
    page_counts = Counter(
        (m['metadata'].get('pdf_name', 'unknown'), m['metadata'].get('page_num', 0))
        for m in data['metadata'].values()
    )
    
    print("="*70)
    print("벡터 DB 중복 저장 분석")
    print("="*70)
    
    # PDF별 통계
    pdf_stats = {}
    for (pdf, page), count in page_counts.items():
        if pdf not in pdf_stats:
            pdf_stats[pdf] = {'pages': set(), 'total_items': 0}
        pdf_stats[pdf]['pages'].add(page)
        pdf_stats[pdf]['total_items'] += count
    
    print("\n[PDF별 통계]")
    for pdf, stats in sorted(pdf_stats.items()):
        unique_pages = len(stats['pages'])
        total_items = stats['total_items']
        ratio = total_items / unique_pages if unique_pages > 0 else 0
        print(f"\n{pdf}:")
        print(f"  고유 페이지 수: {unique_pages}개")
        print(f"  벡터 DB 항목 수: {total_items}개")
        print(f"  평균 저장 횟수: {ratio:.1f}회/페이지")
    
    # 중복 저장된 페이지 확인
    duplicates = {k: v for k, v in page_counts.items() if v > 1}
    
    print("\n" + "="*70)
    print(f"[중복 저장된 페이지] (총 {len(duplicates)}개)")
    print("="*70)
    
    if duplicates:
        for (pdf, page), count in sorted(duplicates.items()):
            print(f"  {pdf} - Page{page}: {count}회 저장")
    else:
        print("  중복 저장된 페이지 없음")
    
    # 전체 요약
    total_unique_pages = sum(len(stats['pages']) for stats in pdf_stats.values())
    total_items = sum(stats['total_items'] for stats in pdf_stats.values())
    
    print("\n" + "="*70)
    print("[전체 요약]")
    print("="*70)
    print(f"총 고유 페이지 수: {total_unique_pages}개")
    print(f"총 벡터 DB 항목 수: {total_items}개")
    print(f"평균 저장 횟수: {total_items / total_unique_pages:.1f}회/페이지")
    print("="*70)


if __name__ == "__main__":
    analyze_faiss_db()

