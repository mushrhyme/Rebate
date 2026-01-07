"""
detail 페이지 JSON 파일들의 items에 'タイプ' 키 추가 (기본값: '販促金請求')
"""
import json
import os
from pathlib import Path

def add_type_to_items(json_path: Path):
    """JSON 파일의 items에 'タイプ' 키 추가"""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # detail 페이지이고 items가 있는 경우만 처리
        if data.get("page_role") == "detail" and "items" in data:
            items = data.get("items", [])
            updated = False
            
            for item in items:
                if isinstance(item, dict) and "タイプ" not in item:
                    item["タイプ"] = "販促金請求"
                    updated = True
            
            if updated:
                # 백업 파일 생성
                backup_path = json_path.with_suffix('.json.bak')
                with open(backup_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                # 원본 파일 업데이트
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                
                return True, len([item for item in items if isinstance(item, dict)])
        
        return False, 0
    except Exception as e:
        print(f"Error processing {json_path}: {e}")
        return False, 0

def main():
    """메인 함수"""
    base_dir = Path("img/01/조건청구서① 20250206002380938001_46558204002_加藤産業株式?社(福岡支店)")
    
    if not base_dir.exists():
        print(f"Directory not found: {base_dir}")
        return
    
    json_files = list(base_dir.glob("*answer.json"))
    print(f"Found {len(json_files)} JSON files")
    
    total_updated = 0
    total_items = 0
    
    for json_path in sorted(json_files):
        updated, item_count = add_type_to_items(json_path)
        if updated:
            total_updated += 1
            total_items += item_count
            print(f"✓ Updated: {json_path.name} ({item_count} items)")
    
    print(f"\n완료: {total_updated}개 파일 업데이트, 총 {total_items}개 items에 'タイプ' 키 추가")

if __name__ == "__main__":
    main()

