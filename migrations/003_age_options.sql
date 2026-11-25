-- 행사별 연령대 항목 이름과 선택지(JSON 문자열 배열). 기존 행사는 지금까지의 여섯 구간으로 시작한다.
ALTER TABLE events ADD COLUMN age_label TEXT NOT NULL DEFAULT '연령대';
ALTER TABLE events ADD COLUMN age_options TEXT NOT NULL DEFAULT '["0~8세","9~13세","14~16세","17~19세","20~24세","24세 이상"]';
