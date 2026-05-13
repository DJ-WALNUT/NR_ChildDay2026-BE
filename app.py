from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
import os

app = Flask(__name__)

# CORS 설정
allowed_origins = [
    "http://localhost:5173",
    "https://2026child.team-cluster.kr",
    "https://nrbooth.team-cluster.kr",
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
    mode = db.Column(db.String(20), default='time')  # 'time' (타임별) 또는 'fcfs' (선착순)
    # [추가] 대기자 명단 운영 여부 (기본값 False)
    use_waitlist = db.Column(db.Boolean, default=False)

    # 선착순(fcfs)일 때 사용
    total_limit = db.Column(db.Integer, default=0)
    start_hour = db.Column(db.Integer, default=11)   # 운영 시작 시간
    end_hour = db.Column(db.Integer, default=16)     # 운영 종료 시간
    slots_per_hour = db.Column(db.Integer, default=3) # 시간당 타임 수 (1이면 11시, 3이면 11시 A,B,C)
    limit_per_slot = db.Column(db.Integer, default=0) # 타임당 인원 제한
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    # [추가] 이 줄이 있어야 booth.reservations를 호출할 수 있습니다.
    reservations = db.relationship('Reservation', backref='booth', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        # 'noshow' 상태를 제외한 정상 및 대기자 예약만 카운트합니다.
        valid_reservations = [r for r in self.reservations if r.status != 'noshow']
        res_count = len(valid_reservations)
        
        # [추가] 타임별 예약자 수를 계산합니다.
        slot_counts = {}
        if self.mode == 'time':
            for r in valid_reservations:
                slot_counts[r.time] = slot_counts.get(r.time, 0) + 1
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "use_waitlist": getattr(self, 'use_waitlist', False),
            "total_limit": self.total_limit,
            "start_hour": self.start_hour,
            "end_hour": self.end_hour,
            "slots_per_hour": self.slots_per_hour,
            "limit_per_slot": self.limit_per_slot,
            "is_active": self.is_active,
            "count": len(self.reservations),
            "slot_counts": slot_counts
        }
    
class Reservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    booth_id = db.Column(db.Integer, db.ForeignKey('booth.id'), nullable=False)
    name = db.Column(db.String(50), nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    ageGroup = db.Column(db.String(20), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    time = db.Column(db.String(50), nullable=True, default="선착순 접수")
    status = db.Column(db.String(20), default='normal') # 'normal', 'noshow', 'completed'

    def to_dict(self):
        return {
            "id": self.id,
            "booth_id": self.booth_id,
            "name": self.name,
            "gender": self.gender,
            "ageGroup": self.ageGroup,
            "phone": self.phone,
            "time": self.time,
            "status": self.status
        }

    # [경쟁 상태 보완] 동일 부스 내 이름+번호 중복 방지 제약 조건
    __table_args__ = (
        db.UniqueConstraint('booth_id', 'name', 'phone', name='_booth_user_uc'),
    )

with app.app_context():
    db.create_all()
    try:
        # 이미 만들어져 있는 테이블에 use_waitlist 컬럼을 기본값 0(False)로 강제 추가합니다.
        db.session.execute(text('ALTER TABLE booth ADD COLUMN use_waitlist BOOLEAN DEFAULT 0'))
        db.session.commit()
        print("use_waitlist 컬럼이 DB에 성공적으로 추가되었습니다.")
    except Exception as e:
        # 이미 컬럼이 존재해서 나는 에러는 무시합니다.
        db.session.rollback()

# --- Booth Management API ---

@app.route('/api/booths', methods=['GET','POST'])
def create_booth():
    if request.method == 'GET':
        # 부스 목록 조회 로직 추가
        booths = Booth.query.all()
        return jsonify([b.to_dict() for b in booths])
    
    if request.method == 'POST':
        data = request.json
        if not data or not data.get('name'):
            return jsonify({"error": "부스 이름은 필수입니다."}), 400
        
    try:
        # 프론트에서 보내주는 값 외에 모델에 정의된 신규 컬럼들의 기본값을 확실히 잡아줍니다.
        new_booth = Booth(
            name=data.get('name'),
            mode=data.get('mode', 'time'),
            use_waitlist=data.get('use_waitlist', False), # [추가]
            # 아래 값들은 프론트엔드 추가 폼에서 보내주기 전까지는 기본값을 할당합니다.
            total_limit=data.get('total_limit', 0),
            start_hour=data.get('start_hour', 11),
            end_hour=data.get('end_hour', 16),
            slots_per_hour=data.get('slots_per_hour', 3),
            limit_per_slot=data.get('limit_per_slot', 0)
        )
        db.session.add(new_booth)
        db.session.commit()
        return jsonify({"message": "부스가 생성되었습니다.", "id": new_booth.id}), 201
    except Exception as e:
        db.session.rollback()
        print(f"Error creating booth: {str(e)}") # 서버 로그 확인용
        return jsonify({"error": str(e)}), 400

@app.route('/api/booths/<int:id>/toggle', methods=['PATCH'])
def toggle_booth_active(id):
    booth = Booth.query.get_or_404(id)
    booth.is_active = not booth.is_active
    db.session.commit()
    return jsonify({"message": "부스 상태 변경", "is_active": booth.is_active})

@app.route('/api/booths/<int:id>', methods=['PUT'])
def update_booth(id):
    booth = Booth.query.get_or_404(id)
    data = request.json
    
    try:
        if 'name' in data: booth.name = data['name']
        if 'mode' in data:  booth.mode = data['mode']
        if 'use_waitlist' in data: booth.use_waitlist = data['use_waitlist'] # [추가]
        if 'total_limit' in data: booth.total_limit = data['total_limit']
        if 'start_hour' in data: booth.start_hour = data['start_hour']
        if 'end_hour' in data: booth.end_hour = data['end_hour']
        if 'slots_per_hour' in data: booth.slots_per_hour = data['slots_per_hour']
        if 'limit_per_slot' in data: booth.limit_per_slot = data['limit_per_slot']
            
        db.session.commit()
        return jsonify({"message": "부스 정보가 수정되었습니다.", "booth": booth.to_dict()}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400

@app.route('/api/booths/<int:id>', methods=['DELETE'])
def delete_booth(id):
    booth = Booth.query.get_or_404(id)
    db.session.delete(booth)
    db.session.commit()
    return jsonify({"message": "부스가 삭제되었습니다."})

# --- Reservation API (Business Logic) ---
@app.route('/api/reservations', methods=['POST'])
def create_reservation():
    data = request.json
    booth_id = data.get('booth_id')
    booth = Booth.query.get_or_404(booth_id)

    # 중복 예약 체크 (이름 + 전화번호 기반)를 정원 체크보다 먼저 수행합니다.
    existing = Reservation.query.filter_by(
        name=data.get('name'),
        phone=data.get('phone'),
        booth_id=booth_id
    ).first()
    
    if existing:
        return jsonify({"error": "이미 해당 부스에 예약된 정보가 있습니다."}), 400
    
    # 상태 관리를 위한 변수 초기화
    reservation_status = 'normal'
    is_waiting = False

    # 1. 선착순(fcfs) 모드 체크
    if booth.mode == 'fcfs':
        current_count = Reservation.query.filter_by(booth_id=booth_id).count()
        
        # 정원이 다 찼을 경우
        if booth.total_limit > 0 and current_count >= booth.total_limit:
            if booth.use_waitlist: # 대기자를 허용할 때만 대기자 상태로
                reservation_status = 'waiting'
                is_waiting = True
            else: # 대기자를 허용하지 않으면 예약 불가 에러 반환
                return jsonify({"error": "현재 부스의 정원이 모두 마감되었습니다."}), 400
        # 정원이 다 차지 않았다면 정상적으로 아래 코드(db.session.add)로 넘어가서 'normal'로 접수됩니다.

    # 2. 타임별(time) 모드 체크
    else:
        selected_time = data.get('time')
        current_time_count = Reservation.query.filter_by(
            booth_id=booth_id, 
            time=selected_time
        ).count()
        if booth.limit_per_slot > 0 and current_time_count >= booth.limit_per_slot:
            if booth.use_waitlist: # [수정] 대기자를 허용할 때만
                reservation_status = 'waiting'
                is_waiting = True
            else: # [수정] 대기자를 허용하지 않으면 예약 불가
                return jsonify({"error": "해당 시간대의 정원이 모두 마감되었습니다."}), 400

    new_res = Reservation(
        name=data.get('name'),
        gender=data.get('gender'),
        ageGroup=data.get('ageGroup'),
        phone=data.get('phone'),
        time=data.get('time', '선착순 접수'), 
        booth_id=booth_id,
        status=reservation_status # 'normal' 또는 'waiting'이 저장됨
    )
    
    db.session.add(new_res)
    db.session.commit()
    
    # 프론트엔드에서 대기자 여부를 판단할 수 있도록 응답에 is_waiting 플래그 추가
    response_data = new_res.to_dict()
    response_data['is_waiting'] = is_waiting
    
    return jsonify(response_data), 201

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