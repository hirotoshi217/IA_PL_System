# models/user_models.py
from flask_login import UserMixin
from models import db


class Users(UserMixin, db.Model):
    __tablename__ = 'users'

    user_id = db.Column(db.Integer, primary_key=True)
    user_name = db.Column(db.String(50), unique=True, nullable=False)
    user_password = db.Column(db.String(150), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="user")

    # 単一の期にだけアクセスできる場合に使用 (adminはNULLで全期OKなど)
    generation_id = db.Column(db.Integer, nullable=True)

    def get_id(self):
        return str(self.user_id)
