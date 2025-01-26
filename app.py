# app.py
import os
from flask import Flask, redirect, url_for
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from models import db
from views.auth import auth_bp
from views.trade import trade_bp

from werkzeug.security import generate_password_hash  # 追加


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'SUPER_SECRET_KEY')
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///unified_db.sqlite3')
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    db.init_app(app)
    CSRFProtect(app)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'

    @login_manager.user_loader
    def load_user(user_id):
        from models.user_models import Users
        return Users.query.get(int(user_id))

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(trade_bp, url_prefix="/trade")

    @app.route('/')
    def root_index():
        # / に来たら /auth/ にリダイレクト (オプション)
        return redirect(url_for('auth.index'))
    @app.route('/db-info')
    def db_info():
        return f"Connected to: {app.config['SQLALCHEMY_DATABASE_URI']}"

    
    with app.app_context():
        db.create_all()
        _ensure_admin_account()

    return app


def _ensure_admin_account():
    """初期管理者 (identity_academy / mori0401_2025yama) を作成する"""
    from models.user_models import Users
    admin_user = Users.query.filter_by(user_name='identity_academy').first()
    if not admin_user:
        pw_hash = generate_password_hash('mori0401_2025yama')
        new_admin = Users(
            user_name='identity_academy',
            user_password=pw_hash,
            role='admin',
            generation_id=None  # adminなので期を固定しない
        )
        db.session.add(new_admin)
        db.session.commit()
        print("Admin user 'identity_academy' created.")
    else:
        print("Admin user already exists.")


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
