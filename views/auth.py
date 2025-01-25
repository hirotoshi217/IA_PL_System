# views/auth.py

from flask import Blueprint, render_template, request, redirect, url_for
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from models import db
from models.user_models import Users
from models.trade_models import Generation

auth_bp = Blueprint('auth', __name__)

@auth_bp.route('/')
def index():
    # ログイン前トップページ
    return render_template('index.html')

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')
    else:
        username = request.form.get('login_username')
        password = request.form.get('login_password')

        user = Users.query.filter_by(user_name=username).first()
        if user and check_password_hash(user.user_password, password):
            login_user(user)
            return redirect(url_for('auth.unified_dashboard'))
        else:
            return "ログイン失敗", 401

@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('auth.index'))

@auth_bp.route('/register', methods=['GET', 'POST'])
@login_required
def register():
    """管理者のみが使える新規ユーザー登録画面"""
    if current_user.role != 'admin':
        return "権限がありません", 403

    if request.method == 'GET':
        # 所属させる期を選択するためリストアップ
        generation_list = Generation.query.all()
        return render_template('register.html', generation_list=generation_list)
    else:
        new_user_name = request.form.get('register_username')
        new_password = request.form.get('register_password')
        generation_id = request.form.get('register_generation_id')

        # 既に存在してないかチェック
        if Users.query.filter_by(user_name=new_user_name).first():
            return "既に存在するユーザー名です", 400

        hashed_pw = generate_password_hash(new_password)

        new_user = Users(
            user_name=new_user_name,
            user_password=hashed_pw,
            role='user',
            generation_id=generation_id
        )
        db.session.add(new_user)
        db.session.commit()

        return redirect(url_for('auth.unified_dashboard'))

@auth_bp.route('/unified_dashboard')
@login_required
def unified_dashboard():
    """ログイン後のメインダッシュボード。admin / user ともに全期表示"""
    
    # 全期一覧を取得
    gens = Generation.query.order_by(Generation.generation_id).all()
    
    return render_template('unified_dashboard.html', generations=gens)

@auth_bp.route('/users_edit', methods=['GET', 'POST'])
@login_required
def users_edit():
    """既存ユーザーの一覧編集 (adminのみ)"""
    if current_user.role != 'admin':
        return "権限なし", 403

    if request.method == 'GET':
        all_users = Users.query.all()
        gen_list = Generation.query.all()
        return render_template('users_edit.html', all_users=all_users, generation_list=gen_list)
    else:
        action = request.form.get('action')
        if action == 'update':
            user_id = request.form.get('user_id')
            new_user_name = request.form.get('new_user_name')
            new_role = request.form.get('new_role')
            new_gen_id = request.form.get('new_generation_id') or None

            user_obj = Users.query.get(user_id)
            if user_obj:
                user_obj.user_name = new_user_name
                user_obj.role = new_role
                user_obj.generation_id = new_gen_id
                db.session.commit()

            return redirect(url_for('auth.users_edit'))

        elif action == 'delete':
            user_id = request.form.get('user_id')
            user_obj = Users.query.get(user_id)
            if user_obj:
                db.session.delete(user_obj)
                db.session.commit()
            return redirect(url_for('auth.users_edit'))

        elif action == 'change_pw':
            user_id = request.form.get('user_id')
            new_pw = request.form.get('new_pw')
            user_obj = Users.query.get(user_id)
            if user_obj and new_pw:
                from werkzeug.security import generate_password_hash
                user_obj.user_password = generate_password_hash(new_pw)
                db.session.commit()

            return redirect(url_for('auth.users_edit'))

        return "不正なアクション", 400
