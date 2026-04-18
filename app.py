from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from sqlalchemy.exc import IntegrityError
import os

app = Flask(__name__)

# CORS 설정
allowed_origins = [
    "http://localhost:5173",
    "https://2026child.team-cluster.kr",
    "https://child-api.team-cluster.kr"
]
CORS(app, 
     resources={r"/api/*": {"origins": allowed_origins}}, 
     supports_credentials=True,
     expose_headers=["Content-Type", "Authorization"],
     allow_headers=["Content-Type", "Authorization", "Access-Control-Allow-Origin"])

basedir = os.path.abspath(os.path.dirname(__file__))
if not os.path.exists(os.path.join(basedir, 'db')):
    os.makedirs(os.path.join(basedir, 'db'))

db_path = os.path.join(basedir, 'db', 'reservations.db')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- Models ---

class Booth(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, default=True)
    # 부스 삭제 시 관련 예약 데이터도 연쇄 삭제
    reservations = db.relationship('Reservation', backref='booth', cascade="all, delete-orphan")

class Reservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booth_id = db.Column(db.Integer, db.ForeignKey('booth.id'), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    ageGroup = db.Column(db.String(20), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    time = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), default='normal') # 'normal', 'noshow', 'completed'

    # [경쟁 상태 보완] 동일 부스 내 이름+번호 중복 방지 제약 조건
    __table_args__ = (
        db.UniqueConstraint('booth_id', 'name', 'phone', name='_booth_user_uc'),
    )

with app.app_context():
    db.create_all()

# --- Booth Management API ---

@app.route('/api/booths', methods=['GET'])
def get_booths():
    booths = Booth.query.all()
    return jsonify([{
        "id": b.id, 
        "name": b.name, 
        "description": b.description, 
        "is_active": b.is_active,
        "count": len(b.reservations)
    } for b in booths])

@app.route('/api/booths', methods=['POST'])
def add_booth():
    data = request.json
    new_booth = Booth(name=data['name'], description=data.get('description', ''))
    db.session.add(new_booth)
    db.session.commit()
    return jsonify({"message": "부스가 생성되었습니다.", "id": new_booth.id}), 201

@app.route('/api/booths/<int:id>/toggle', methods=['PATCH'])
def toggle_booth_active(id):
    booth = Booth.query.get_or_404(id)
    booth.is_active = not booth.is_active
    db.session.commit()
    return jsonify({"message": "부스 상태 변경", "is_active": booth.is_active})

@app.route('/api/booths/<int:id>', methods=['DELETE'])
def delete_booth(id):
    booth = Booth.query.get_or_404(id)
    db.session.delete(booth)
    db.session.commit()
    return jsonify({"message": "부스가 삭제되었습니다."})

# --- Reservation API (Business Logic) ---

@app.route('/api/reservations', methods=['POST'])
def add_reservation():
    try:
        data = request.json
        booth_id = data.get('booth_id')
        
        # [락 적용] 부스 상태를 확인하는 동안 다른 요청이 개입하지 못하도록 락을 겁니다.
        booth = Booth.query.with_for_update().get(booth_id)
        
        if not booth:
            return jsonify({"error": "존재하지 않는 부스입니다."}), 404
        if not booth.is_active:
            return jsonify({"error": "현재 이 부스는 신청을 받지 않습니다."}), 403

        new_res = Reservation(
            booth_id=booth_id,
            name=data['name'].strip(),
            gender=data['gender'],
            ageGroup=data['ageGroup'],
            phone=data['phone'].replace("-", ""),
            time=data['time']
        )
        db.session.add(new_res)
        db.session.commit()
        return jsonify({"message": "Success", "id": new_res.id}), 201

    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "이미 해당 부스에 동일한 정보로 신청된 내역이 있습니다."}), 409
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500

# 부스별 신청자 목록 가져오기 (관리자 대시보드용)
@app.route('/api/booths/<int:booth_id>/reservations', methods=['GET'])
def get_booth_reservations(booth_id):
    booth = Booth.query.get_or_404(booth_id)
    output = []
    for r in booth.reservations:
        output.append({
            "id": r.id, "name": r.name, "gender": r.gender,
            "ageGroup": r.ageGroup, "phone": r.phone,
            "time": r.time, "status": r.status or 'normal'
        })
    return jsonify({"boothName": booth.name, "reservations": output})

# 전체 데이터 가져오기 (엑셀 추출 등 통합 관리용)
@app.route('/api/reservations', methods=['GET'])
def get_all_reservations():
    res_list = Reservation.query.all()
    output = [{
        "id": r.id, "booth_name": r.booth.name, "name": r.name, "gender": r.gender,
        "ageGroup": r.ageGroup, "phone": r.phone, "time": r.time, "status": r.status
    } for r in res_list]
    return jsonify(output)

# 노쇼 상태 토글
@app.route('/api/reservations/<int:id>/toggle', methods=['PATCH'])
def toggle_noshow(id):
    res = Reservation.query.get_or_404(id)
    res.status = 'noshow' if res.status == 'normal' else 'normal'
    db.session.commit()
    return jsonify({"message": "Status updated", "status": res.status})

# 체험 완료 처리
@app.route('/api/reservations/<int:id>/complete', methods=['PATCH'])
def complete_reservation(id):
    res = Reservation.query.get_or_404(id)
    res.status = 'completed' if res.status != 'completed' else 'normal'
    db.session.commit()
    return jsonify({"message": "Completion status updated", "status": res.status})

# 개별 예약 삭제
@app.route('/api/reservations/<int:id>', methods=['DELETE'])
def delete_reservation(id):
    res = Reservation.query.get_or_404(id)
    db.session.delete(res)
    db.session.commit()
    return jsonify({"message": "Deleted"})

# [주의] 특정 부스의 모든 데이터 삭제
@app.route('/api/booths/<int:booth_id>/clear', methods=['DELETE'])
def clear_booth_data(booth_id):
    Reservation.query.filter_by(booth_id=booth_id).delete()
    db.session.commit()
    return jsonify({"message": "All reservations for this booth cleared"})

# 전체 데이터 초기화
@app.route('/api/clear-all', methods=['DELETE'])
def clear_all_data():
    Reservation.query.delete()
    # Booth.query.delete() # 부스 목록까지 지우려면 주석 해제
    db.session.commit()
    return jsonify({"message": "All data cleared"})

if __name__ == '__main__':
    app.run(debug=True, port=5005, host='0.0.0.0')