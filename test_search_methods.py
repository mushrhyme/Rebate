"""
RAG 검색 방식 테스트 스크립트
"""
import os
import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from modules.core.rag_manager import get_rag_manager

def test_search_methods():
    """다양한 검색 방식 테스트"""
    
    # OCR 텍스트 읽기
    ocr_text_path = 'debug/旭食品?東支店25.03?理分②/page_2_ocr_text.txt'
    
    if not os.path.exists(ocr_text_path):
        print(f"❌ OCR 텍스트 파일을 찾을 수 없습니다: {ocr_text_path}")
        return
    
    with open(ocr_text_path, 'r', encoding='utf-8') as f:
        ocr_text = f.read()
    
    print(f"✅ OCR 텍스트 로드 완료: {len(ocr_text)} 문자")
    print(f"미리보기: {ocr_text[:200]}...\n")
    
    # RAG Manager 가져오기
    rag_manager = get_rag_manager()
    
    print(f"총 예제 수: {rag_manager.count_examples()}\n")
    
    # 1. Vector Only 검색
    print("=" * 80)
    print("1. Vector Only 검색")
    print("=" * 80)
    
    try:
        vector_results = rag_manager.search_vector_only(
            query_text=ocr_text,
            top_k=3,
            similarity_threshold=0.7,
            use_preprocessing=True
        )
        
        print(f"검색 결과: {len(vector_results)}개\n")
        for i, result in enumerate(vector_results, 1):
            print(f"[{i}]")
            print(f"  유사도: {result.get('similarity', 0):.6f}")
            print(f"  거리: {result.get('distance', 0):.6f}")
            print(f"  페이지 역할: {result['answer_json'].get('page_role', 'unknown')}")
            print(f"  Items 개수: {len(result['answer_json'].get('items', []))}")
            print(f"  ID: {result.get('id', 'unknown')[:8]}...")
            print()
    except Exception as e:
        print(f"❌ Vector Only 검색 실패: {e}")
        import traceback
        traceback.print_exc()
    
    # 2. Hybrid 검색
    print("=" * 80)
    print("2. Hybrid 검색 (BM25 + Vector)")
    print("=" * 80)
    
    try:
        # BM25 인덱스 상태 확인
        rag_manager._build_bm25_index()
        if rag_manager._bm25_index is None:
            print("⚠️ BM25 인덱스가 없습니다 (rank-bm25 미설치 또는 예제 없음)")
        else:
            print(f"✅ BM25 인덱스 구축 완료: {len(rag_manager._bm25_texts)}개 문서")
        
        # BM25 인덱스 디버깅
        print(f"BM25 텍스트 개수: {len(rag_manager._bm25_texts) if rag_manager._bm25_texts else 0}")
        print(f"BM25 예제 맵 크기: {len(rag_manager._bm25_example_map) if rag_manager._bm25_example_map else 0}")
        
        hybrid_results = rag_manager.search_hybrid(
            query_text=ocr_text,
            top_k=3,
            similarity_threshold=0.7,
            hybrid_alpha=0.5,
            use_preprocessing=True
        )
        
        print(f"검색 결과: {len(hybrid_results)}개\n")
        for i, result in enumerate(hybrid_results, 1):
            print(f"[{i}]")
            print(f"  하이브리드 점수: {result.get('hybrid_score', 0):.6f}")
            print(f"  벡터 유사도: {result.get('similarity', 0):.6f}")
            print(f"  BM25 점수 (정규화): {result.get('bm25_score', 0):.6f}")
            print(f"  페이지 역할: {result['answer_json'].get('page_role', 'unknown')}")
            print(f"  Items 개수: {len(result['answer_json'].get('items', []))}")
            print(f"  ID: {result.get('id', 'unknown')[:8]}...")
            print()
    except Exception as e:
        print(f"❌ Hybrid 검색 실패: {e}")
        import traceback
        traceback.print_exc()
    
    # 3. Rerank 검색
    print("=" * 80)
    print("3. Rerank 검색 (Vector + Cross-encoder)")
    print("=" * 80)
    
    try:
        rerank_results = rag_manager.search_with_rerank(
            query_text=ocr_text,
            top_k=3,
            similarity_threshold=0.7,
            rerank_top_n=10,
            rerank_model=None,
            use_preprocessing=True
        )
        
        print(f"검색 결과: {len(rerank_results)}개\n")
        for i, result in enumerate(rerank_results, 1):
            print(f"[{i}]")
            print(f"  최종 점수: {result.get('final_score', 0):.6f}")
            print(f"  벡터 유사도: {result.get('similarity', 0):.6f}")
            print(f"  Rerank 점수 (정규화): {result.get('rerank_score', 0):.6f}")
            if 'rerank_score_raw' in result:
                print(f"  Rerank 점수 (원본): {result.get('rerank_score_raw', 0):.6f}")
            print(f"  페이지 역할: {result['answer_json'].get('page_role', 'unknown')}")
            print(f"  Items 개수: {len(result['answer_json'].get('items', []))}")
            print(f"  ID: {result.get('id', 'unknown')[:8]}...")
            print()
            
            # 디버깅: 키 확인
            print(f"  사용 가능한 키: {list(result.keys())}")
            print()
    except Exception as e:
        print(f"❌ Rerank 검색 실패: {e}")
        import traceback
        traceback.print_exc()
    
    # 4. 비교 요약
    print("=" * 80)
    print("검색 방식 비교 요약")
    print("=" * 80)
    
    comparison = []
    
    if 'vector_results' in locals() and vector_results:
        for i, r in enumerate(vector_results, 1):
            comparison.append({
                "방식": "Vector Only",
                "순위": i,
                "점수": r.get('similarity', 0),
                "페이지역할": r['answer_json'].get('page_role', 'unknown'),
                "Items수": len(r['answer_json'].get('items', []))
            })
    
    if 'hybrid_results' in locals() and hybrid_results:
        for i, r in enumerate(hybrid_results, 1):
            comparison.append({
                "방식": "Hybrid",
                "순위": i,
                "점수": r.get('hybrid_score', 0),
                "페이지역할": r['answer_json'].get('page_role', 'unknown'),
                "Items수": len(r['answer_json'].get('items', []))
            })
    
    if 'rerank_results' in locals() and rerank_results:
        for i, r in enumerate(rerank_results, 1):
            comparison.append({
                "방식": "Rerank",
                "순위": i,
                "점수": r.get('final_score', 0),
                "페이지역할": r['answer_json'].get('page_role', 'unknown'),
                "Items수": len(r['answer_json'].get('items', []))
            })
    
    if comparison:
        import pandas as pd
        df = pd.DataFrame(comparison)
        print(df.to_string(index=False))
    else:
        print("비교 데이터가 없습니다.")

if __name__ == "__main__":
    test_search_methods()

