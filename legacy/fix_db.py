import sqlite3
import os

def update_database():
    # 현재 폴더 경로 설정
    basedir = os.path.abspath(os.path.dirname(__file__))
    db_path = os.path.join(basedir, 'db', 'reservations.db')
    
    if not os.path.exists(db_path):
        print(f"❌ 에러: DB 파일을 찾을 수 없습니다. 경로를 확인해주세요: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # 1. 기본값(DEFAULT) 없이 빈 컬럼을 먼저 추가합니다.
        cursor.execute("ALTER TABLE reservation ADD COLUMN created_at DATETIME")
        
        # 2. 추가된 빈 컬럼(NULL)을 찾아 일괄적으로 현재 시간을 채워넣습니다.
        cursor.execute("UPDATE reservation SET created_at = '2026-05-23 00:00:00'")
        
        conn.commit()
        print("✅ 성공: 'created_at' 빈 컬럼 추가 및 기존 데이터 시간 업데이트 완료!")
    except sqlite3.OperationalError as e:
        # 이미 컬럼이 존재하는 경우
        if "duplicate column name" in str(e).lower():
            print("⚠️ 안내: 이미 'created_at' 컬럼이 존재합니다.")
        else:
            print(f"❌ 에러 발생: {e}")
    finally:
        conn.close()

if __name__ == '__main__':
    update_database()