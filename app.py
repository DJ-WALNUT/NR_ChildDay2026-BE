from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from sqlalchemy.exc import IntegrityError # 에러 처리를 위해 추가
import os

app = Flask(__name__)
# 허용할 도메인 리스트 정의
allowed_origins = [
    "http://localhost:5173",           # 로컬 개발 환경
    "https://2026child.team-cluster.kr", # 실제 배포 도메인 (HTTPS 확인 필요)
    "https://child-api.team-cluster.kr"
]

# 특정 도메인에 대해서만 모든 API 경로(/api/*) 허용
CORS(app, 
     resources={r"/api/*": {"origins": allowed_origins}}, 
     supports_credentials=True,
     expose_headers=["Content-Type", "Authorization"],
     allow_headers=["Content-Type", "Authorization", "Access-Control-Allow-Origin"])

basedir = os.path.abspath(os.path.dirname(__file__))
# DB 폴더가 없으면 자동 생성 (시놀로지 환경 대비)
if not os.path.exists(os.path.join(basedir, 'db')):
    os.makedirs(os.path.join(basedir, 'db'))

db_path = os.path.join(basedir, 'db', 'reservations.db')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

class Reservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    ageGroup = db.Column(db.String(20), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    time = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default='normal', server_default='normal')

with app.app_context():
    db.create_all()

@app.route('/api/reservations', methods=['POST'])
def add_reservation():
    try:
        data = request.json
        
        # 1. 필수 필드 검증 (데이터 무결성 보장)
        required_fields = ['name', 'gender', 'ageGroup', 'phone', 'time']
        for field in required_fields:
            if not data.get(field):
                return jsonify({"error": f"'{field}' 항목은 필수입니다."}), 400

        # 2. 중복 신청 방지 로직 (옵션: 이름+전화번호 조합)
        # 같은 사람이 여러 번 예약하는 것을 막으려면 아래 주석을 해제하세요.
        """
        exists = Reservation.query.filter_by(name=data['name'], phone=data['phone']).first()
        if exists:
            return jsonify({"error": "이미 동일한 정보로 등록된 예약이 있습니다."}), 409
        """

        new_res = Reservation(
            name=data['name'].strip(), # 공백 제거
            gender=data['gender'],
            ageGroup=data['ageGroup'],
            phone=data['phone'].replace("-", ""), # 하이픈 제거 후 저장 (데이터 통일)
            time=data['time']
        )
        
        db.session.add(new_res)
        db.session.commit()
        return jsonify({"message": "Success", "id": new_res.id}), 201

    except Exception as e:
        db.session.rollback() # 오류 시 세션 되돌리기
        return jsonify({"error": "서버 내부 오류가 발생했습니다.", "details": str(e)}), 500

@app.route('/api/reservations/clear', methods=['DELETE'])
def clear_reservations():
    """모든 데이터 삭제 API (관리자용)"""
    try:
        num_rows_deleted = db.session.query(Reservation).delete()
        db.session.commit()
        return jsonify({"message": f"{num_rows_deleted}개의 데이터가 삭제되었습니다."}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# API: 모든 예약 가져오기 (관리자용)
@app.route('/api/reservations', methods=['GET'])
def get_reservations():
    res_list = Reservation.query.all()
    output = []
    for r in res_list:
        output.append({
            "id": r.id, "name": r.name, "gender": r.gender,
            "ageGroup": r.ageGroup, "phone": r.phone,
            "time": r.time, "status": r.status
        })
    return jsonify(output)

# API: 노쇼 상태 변경
@app.route('/api/reservations/<int:id>/toggle', methods=['PATCH'])
def toggle_noshow(id):
    res = Reservation.query.get_or_404(id)
    res.status = 'noshow' if res.status == 'normal' else 'normal'
    db.session.commit()
    return jsonify({"message": "Updated"})

if __name__ == '__main__':
    app.run(debug=True, port=5005)