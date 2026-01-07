"""
answer.json 파일들을 tmp 폴더로 이동하는 스크립트
(answer_v2.json이 아닌 answer.json 파일만 이동)
"""

import os
import shutil
from pathlib import Path

def move_answer_json_files():
    """img와 test_img 폴더에서 answer.json 파일들을 tmp 폴더로 이동"""
    project_root = Path(__file__).parent
    tmp_dir = project_root / "tmp"
    
    # tmp 폴더 생성
    tmp_dir.mkdir(exist_ok=True)
    
    # 검색할 폴더 목록
    search_dirs = [
        project_root / "img",
        project_root / "test_img"
    ]
    
    moved_count = 0
    moved_files = []
    
    for search_dir in search_dirs:
        if not search_dir.exists():
            print(f"⚠️ 폴더가 존재하지 않습니다: {search_dir}")
            continue
        
        # 재귀적으로 answer.json 파일 찾기 (answer_v2.json 제외)
        for answer_file in search_dir.rglob("*_answer.json"):
            # answer_v2.json은 제외
            if "_answer_v2.json" in str(answer_file):
                continue
            
            # 파일명만 가져와서 tmp 폴더로 복사
            filename = answer_file.name
            
            # 중복 파일명 처리 (원본 경로 정보를 파일명에 포함)
            relative_path = answer_file.relative_to(search_dir)
            parent_dirs = relative_path.parent.parts
            
            # 파일명 생성: 원본 경로 정보 포함
            if parent_dirs:
                safe_filename = "_".join(parent_dirs) + "_" + filename
                # 파일명에 사용할 수 없는 문자 제거
                safe_filename = safe_filename.replace("/", "_").replace("\\", "_")
            else:
                safe_filename = filename
            
            dest_path = tmp_dir / safe_filename
            
            # 중복 파일명이 있으면 번호 추가
            counter = 1
            original_dest_path = dest_path
            while dest_path.exists():
                stem = original_dest_path.stem
                suffix = original_dest_path.suffix
                dest_path = tmp_dir / f"{stem}_{counter}{suffix}"
                counter += 1
            
            try:
                # 파일 이동
                shutil.move(str(answer_file), str(dest_path))
                moved_count += 1
                moved_files.append((str(answer_file), str(dest_path)))
                print(f"✅ 이동: {answer_file} -> {dest_path}")
            except Exception as e:
                print(f"❌ 이동 실패: {answer_file} - {e}")
    
    print(f"\n📊 총 {moved_count}개 파일 이동 완료")
    print(f"📁 이동 위치: {tmp_dir}")
    
    if moved_files:
        print("\n이동된 파일 목록:")
        for src, dst in moved_files:
            print(f"  - {src}")
            print(f"    -> {dst}")
    
    return moved_count

if __name__ == "__main__":
    print("answer.json 파일 이동 시작...")
    print("=" * 60)
    move_answer_json_files()
    print("=" * 60)
    print("완료!")

