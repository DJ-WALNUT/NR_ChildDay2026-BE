def test_audit_scope_and_paging(app, db):
    from app.audit import audit, list_audit
    from app.principal import Principal
    with app.test_request_context():
        p = Principal(kind="user", label="홍(hong)", user_id=1, org_id=2)
        for i in range(3):
            audit(p, "booth.update", f"booth:{i}", f"변경 {i}", org_id=1)   # 행위자 기관 2가 기관 1 부스 수정
        audit(None, "org.create", "org:9", "기관 생성", org_id=9)
        a = list_audit(org_id=2, before=None, limit=2, action=None)
        assert [i["summary"] for i in a["items"]] == ["변경 2", "변경 1"] and a["next_before"] is not None
        b = list_audit(org_id=2, before=a["next_before"], limit=2, action=None)
        assert [i["summary"] for i in b["items"]] == ["변경 0"] and b["next_before"] is None
        assert len(list_audit(org_id=1, before=None, limit=50, action="booth.")["items"]) == 3
        assert list_audit(org_id=1, before=None, limit=50, action="org.")["items"] == []
        sys = list_audit(org_id=None, before=None, limit=50, action="org.")["items"][0]
        assert sys["actor"] == "system" and sys["actor_label"] == "시스템"
