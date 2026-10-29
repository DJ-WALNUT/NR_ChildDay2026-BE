"""flask create-superadmin"""

from __future__ import annotations

import click
from flask import Flask
from flask.cli import with_appcontext

from .db import tx
from .security import hash_password, normalize_username, password_problem, valid_username
from .timeutil import now


@click.command("create-superadmin")
@click.option("--username", required=True, help="슈퍼관리자 아이디")
@click.option("--password", default=None, help="비밀번호 (생략하면 숨김 입력으로 두 번 묻는다)")
@with_appcontext
def create_superadmin(username: str, password: str | None) -> None:
    """슈퍼관리자를 만든다. 이미 있는 계정이면 슈퍼관리자로 승격하고 비밀번호를 바꾼다."""
    username = normalize_username(username)
    if not valid_username(username):
        raise click.ClickException("아이디는 영문 소문자·숫자·._- 3~32자여야 합니다.")
    if password is None:
        password = click.prompt("비밀번호", hide_input=True, confirmation_prompt=True)
    problem = password_problem(password, username)
    if problem:
        raise click.ClickException(f"비밀번호: {problem}")
    with tx() as db:
        row = db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if row:
            # 복구 경로이므로 주어진 비밀번호로 항상 덮어쓴다
            db.execute(
                "UPDATE users SET is_superadmin = 1, is_active = 1, must_change_password = 0,"
                " org_id = NULL, password_hash = ?, password_changed_at = ? WHERE id = ?",
                (hash_password(password), now(), row["id"]),
            )
            db.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
            click.echo(f"{username} 을(를) 슈퍼관리자로 승격했습니다. 비밀번호를 변경했습니다.")
        else:
            db.execute(
                "INSERT INTO users (org_id, username, display_name, password_hash, must_change_password,"
                " is_superadmin, created_at) VALUES (NULL,?,?,?,0,1,?)",
                (username, username, hash_password(password), now()),
            )
            click.echo(f"슈퍼관리자 {username} 을(를) 만들었습니다.")


def init_cli(app: Flask) -> None:
    app.cli.add_command(create_superadmin)
