"""
answer_v2.json 파일들을 answer.json으로 이름 변경하는 스크립트
"""

import os
from pathlib import Path

def rename_answer_v2_files():
    """img와 test_img 폴더에서 answer_v2.json 파일들을 answer.json으로 변경"""
    project_root = Path(__file__).parent
    
    # 검색할 폴더 목록
    search_dirs = [
        project_root / "img",
        project_root / "test_img"
    ]
    
    renamed_count = 0
    renamed_files = []
    
    for search_dir in search_dirs:
        if not search_dir.exists():
            print(f"⚠️ 폴더가 존재하지 않습니다: {search_dir}")
            continue
        
        # 재귀적으로 answer_v2.json 파일 찾기
        for answer_v2_file in search_dir.rglob("*_answer_v2.json"):
            # answer.json으로 변경
            new_name = answer_v2_file.name.replace("_answer_v2.json", "_answer.json")
            new_path = answer_v2_file.parent / new_name
            
            # 이미 answer.json이 존재하는 경우 스킵
            if new_path.exists():
                print(f"⚠️ 이미 존재함 (스킵): {answer_v2_file} -> {new_path}")
                continue
            
            try:
                # 파일명 변경
                answer_v2_file.rename(new_path)
                renamed_count += 1
                renamed_files.append((str(answer_v2_file), str(new_path)))
                print(f"✅ 변경: {answer_v2_file.name} -> {new_name}")
            except Exception as e:
                print(f"❌ 변경 실패: {answer_v2_file} - {e}")
    
    print(f"\n📊 총 {renamed_count}개 파일 이름 변경 완료")
    
    if renamed_files:
        print("\n변경된 파일 목록:")
        for old_path, new_path in renamed_files[:10]:  # 처음 10개만 표시
            print(f"  - {old_path}")
            print(f"    -> {new_path}")
        if len(renamed_files) > 10:
            print(f"  ... 외 {len(renamed_files) - 10}개 파일")
    
    return renamed_count

if __name__ == "__main__":
    print("answer_v2.json 파일 이름 변경 시작...")
    print("=" * 60)
    rename_answer_v2_files()
    print("=" * 60)
    print("완료!")

