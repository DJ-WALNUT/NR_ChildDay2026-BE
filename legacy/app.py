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

# [신규 추가] 행사(Event) 모델
class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False) # 예: "5월 어린이날 행사"
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    
    # 해당 행사에 속한 부스들을 가져오기 위한 관계 설정
    booths = db.relationship('Booth', backref='event', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            # 행사에 속한 부스 목록도 함께 반환 (프론트 트리 구조용)
            "booths": [b.to_dict() for b in self.booths] 
        }

class Booth(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    
    # [추가] 행사에 종속되도록 event_id 외래키 추가 (기존 DB 호환을 위해 일단 nullable=True로 설정)
    event_id = db.Column(db.Integer, db.ForeignKey('event.id'), nullable=True) 

    name = db.Column(db.String(100), nullable=False)
    mode = db.Column(db.String(20), default='time')  
    use_waitlist = db.Column(db.Boolean, default=False)
    total_limit = db.Column(db.Integer, default=0)
    start_hour = db.Column(db.Integer, default=11)   
    end_hour = db.Column(db.Integer, default=16)     
    slots_per_hour = db.Column(db.Integer, default=3) 
    limit_per_slot = db.Column(db.Integer, default=0) 
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    reservations = db.relationship('Reservation', backref='booth', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        valid_reservations = [r for r in self.reservations if r.status != 'noshow']
        res_count = len(valid_reservations)
        
        slot_counts = {}
        if self.mode == 'time':
            for r in valid_reservations:
                slot_counts[r.time] = slot_counts.get(r.time, 0) + 1
        return {
            "id": self.id,
            "event_id": self.event_id, # [추가] 프론트엔드에서 구분할 수 있게 추가
            "event_name": self.event.name if self.event else "미분류", # [추가]
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
    status = db.Column(db.String(20), default='normal')
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    def to_dict(self):
        return {
            "id": self.id,
            "booth_id": self.booth_id,
            "name": self.name,
            "gender": self.gender,
            "ageGroup": self.ageGroup,
            "phone": self.phone,
            "time": self.time,
            "status": self.status,
            "created_at": self.created_at.isoformat() if getattr(self, 'created_at', None) else None
        }

    __table_args__ = (
        db.UniqueConstraint('booth_id', 'name', 'phone', name='_booth_user_uc'),
    )

with app.app_context():
    db.create_all()
    
    # 1. 컬럼 추가 안전장치 (use_waitlist, event_id)
    try:
        db.session.execute(text('ALTER TABLE booth ADD COLUMN use_waitlist BOOLEAN DEFAULT 0'))
        db.session.commit()
    except:
        db.session.rollback()

    try:
        db.session.execute(text('ALTER TABLE booth ADD COLUMN event_id INTEGER REFERENCES event(id)'))
        db.session.commit()
    except:
        db.session.rollback()

    # [추가] reservation 테이블에 created_at 컬럼을 안전하게 추가합니다.
    try:
        db.session.execute(text('ALTER TABLE reservation ADD COLUMN created_at DATETIME'))
        db.session.commit()
    except:
        db.session.rollback()

    # 2. [추가] 기존 데이터를 살리기 위한 '기본 행사' 마이그레이션 방어 로직
    default_event = Event.query.first()
    if not default_event:
        default_event = Event(name="기본 행사(미분류)")
        db.session.add(default_event)
        db.session.commit()
    
    # event_id가 없는 예전 부스들을 '기본 행사'에 강제로 연결해줍니다.
    orphaned_booths = Booth.query.filter_by(event_id=None).all()
    for b in orphaned_booths:
        b.event_id = default_event.id
    if orphaned_booths:
        db.session.commit()
        print("기존 부스들을 기본 행사에 연결 완료했습니다.")

# --- Event Management API ---

@app.route('/api/events', methods=['GET', 'POST'])
def manage_events():
    if request.method == 'GET':
        events = Event.query.all()
        return jsonify([e.to_dict() for e in events])
    
    if request.method == 'POST':
        data = request.json
        if not data or not data.get('name'):
            return jsonify({"error": "행사 이름은 필수입니다."}), 400
        
        new_event = Event(name=data['name'])
        db.session.add(new_event)
        db.session.commit()
        return jsonify({"message": "행사가 생성되었습니다.", "event": new_event.to_dict()}), 201

# [추가] 행사 이름 수정 API
@app.route('/api/events/<int:id>', methods=['PUT'])
def update_event(id):
    event = Event.query.get_or_404(id)
    data = request.json
    
    if not data or not data.get('name'):
        return jsonify({"error": "변경할 행사 이름을 입력해주세요."}), 400
        
    event.name = data['name']
    db.session.commit()
    return jsonify({"message": "행사 이름이 변경되었습니다.", "event": event.to_dict()})

# [수정] 행사 삭제 API (부스 살리기 로직 적용)
@app.route('/api/events/<int:id>', methods=['DELETE'])
def delete_event(id):
    event = Event.query.get_or_404(id)
    
    # 기본 행사(가장 첫 번째 생성된 행사, 보통 '기본 행사(미분류)')를 찾습니다.
    default_event = Event.query.first()
    
    # 만약 삭제하려는 행사가 기본 행사라면 삭제를 막습니다. (시스템 보호)
    if default_event and event.id == default_event.id:
        return jsonify({"error": "기본 행사(미분류)는 삭제할 수 없습니다."}), 400
        
    # 삭제하려는 행사에 속한 부스들을 모두 기본 행사로 대피시킵니다.
    if default_event:
        for booth in event.booths:
            booth.event_id = default_event.id
        db.session.commit() # 부스 이동을 먼저 확정지음
    
    # 그 후 비어있는 행사를 삭제합니다.
    db.session.delete(event)
    db.session.commit()
    return jsonify({"message": "행사가 삭제되었으며, 소속된 부스들은 '기본 행사'로 이동되었습니다."})


# --- Booth Management API ---

@app.route('/api/booths', methods=['GET','POST'])
def create_booth():
    if request.method == 'GET':
        booths = Booth.query.all()
        return jsonify([b.to_dict() for b in booths])
    
    if request.method == 'POST':
        data = request.json
        if not data or not data.get('name'):
            return jsonify({"error": "부스 이름은 필수입니다."}), 400
        
        # event_id가 안들어오면 가장 첫 번째 행사(기본 행사)에 할당
        event_id = data.get('event_id')
        if not event_id:
            first_event = Event.query.first()
            event_id = first_event.id if first_event else None

        try:
            new_booth = Booth(
                event_id=event_id, # [추가]
                name=data.get('name'),
                mode=data.get('mode', 'time'),
                use_waitlist=data.get('use_waitlist', False), 
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
            return jsonify({"error": str(e)}), 400

# ... (이하 기존 toggle_booth_active, update_booth, delete_booth, create_reservation 유지) ...

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
        if 'event_id' in data: booth.event_id = data['event_id'] # [추가] 행사 이동 가능
        if 'name' in data: booth.name = data['name']
        if 'mode' in data:  booth.mode = data['mode']
        if 'use_waitlist' in data: booth.use_waitlist = data['use_waitlist']
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
# ... (create_reservation 유지, 이전 응답과 동일하므로 생략하지 않고 포함합니다) ...

@app.route('/api/reservations', methods=['POST'])
def create_reservation():
    data = request.json
    booth_id = data.get('booth_id')
    booth = Booth.query.get_or_404(booth_id)

    existing = Reservation.query.filter_by(
        name=data.get('name'),
        phone=data.get('phone'),
        booth_id=booth_id
    ).first()
    
    if existing:
        return jsonify({"error": "이미 해당 부스에 예약된 정보가 있습니다."}), 400
    
    reservation_status = 'normal'
    is_waiting = False

    if booth.mode == 'fcfs':
        current_count = Reservation.query.filter_by(booth_id=booth_id).count()
        if booth.total_limit > 0 and current_count >= booth.total_limit:
            if booth.use_waitlist:
                reservation_status = 'waiting'
                is_waiting = True
            else:
                return jsonify({"error": "현재 부스의 정원이 모두 마감되었습니다."}), 400
    else:
        selected_time = data.get('time')
        current_time_count = Reservation.query.filter_by(
            booth_id=booth_id, 
            time=selected_time
        ).count()
        if booth.limit_per_slot > 0 and current_time_count >= booth.limit_per_slot:
            if booth.use_waitlist:
                reservation_status = 'waiting'
                is_waiting = True
            else:
                return jsonify({"error": "해당 시간대의 정원이 모두 마감되었습니다."}), 400

    new_res = Reservation(
        name=data.get('name'),
        gender=data.get('gender'),
        ageGroup=data.get('ageGroup'),
        phone=data.get('phone'),
        time=data.get('time', '선착순 접수'), 
        booth_id=booth_id,
        status=reservation_status 
    )
    
    db.session.add(new_res)
    db.session.commit()
    
    response_data = new_res.to_dict()
    response_data['is_waiting'] = is_waiting
    
    return jsonify(response_data), 201


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

@app.route('/api/reservations', methods=['GET'])
def get_all_reservations():
    res_list = Reservation.query.all()
    output = [{
        "id": r.id, "booth_name": r.booth.name, "name": r.name, "gender": r.gender,
        "ageGroup": r.ageGroup, "phone": r.phone, "time": r.time, "status": r.status,
        "created_at": getattr(r, 'created_at', None).isoformat() if getattr(r, 'created_at', None) else None
    } for r in res_list]
    return jsonify(output)

# [신규 추가] 다중 부스 엑셀/ZIP 추출을 위한 통합 데이터 제공 API
@app.route('/api/reservations/bulk', methods=['POST'])
def get_bulk_reservations():
    data = request.json
    booth_ids = data.get('booth_ids', [])
    
    if not booth_ids:
        return jsonify({"error": "조회할 부스 ID 목록이 필요합니다."}), 400

    booths = Booth.query.filter(Booth.id.in_(booth_ids)).all()
    
    result = {}
    for booth in booths:
        # 각 부스별로 신청자 목록을 배열로 묶어서 딕셔너리 형태로 반환
        reservations = []
        for r in booth.reservations:
            reservations.append({
                "시간": r.time, "이름": r.name, "성별": r.gender, 
                "연령": r.ageGroup, "연락처": r.phone, "상태": r.status
            })
        # 프론트엔드에서 파일명(시트명)으로 쓰기 좋게 key를 생성 ("행사이름_부스이름")
        file_key = f"[{booth.event.name}] {booth.name}" if booth.event else booth.name
        result[file_key] = reservations
        
    # 예시 형태: { "[5월행사] 부스1": [{...}, {...}], "[5월행사] 부스2": [{...}] }
    return jsonify(result), 200

# ... (나머지 상태 토글 및 삭제 API 유지)
@app.route('/api/reservations/<int:id>/toggle', methods=['PATCH'])
def toggle_noshow(id):
    res = Reservation.query.get_or_404(id)
    res.status = 'noshow' if res.status == 'normal' else 'normal'
    db.session.commit()
    return jsonify({"message": "Status updated", "status": res.status})

@app.route('/api/reservations/<int:id>/complete', methods=['PATCH'])
def complete_reservation(id):
    res = Reservation.query.get_or_404(id)
    res.status = 'completed' if res.status != 'completed' else 'normal'
    db.session.commit()
    return jsonify({"message": "Completion status updated", "status": res.status})

@app.route('/api/reservations/<int:id>', methods=['DELETE'])
def delete_reservation(id):
    res = Reservation.query.get_or_404(id)
    db.session.delete(res)
    db.session.commit()
    return jsonify({"message": "Deleted"})

@app.route('/api/booths/<int:booth_id>/clear', methods=['DELETE'])
def clear_booth_data(booth_id):
    Reservation.query.filter_by(booth_id=booth_id).delete()
    db.session.commit()
    return jsonify({"message": "All reservations for this booth cleared"})

@app.route('/api/clear-all', methods=['DELETE'])
def clear_all_data():
    Reservation.query.delete()
    db.session.commit()
    return jsonify({"message": "All data cleared"})

if __name__ == '__main__':
    app.run(debug=True, port=5005, host='0.0.0.0')