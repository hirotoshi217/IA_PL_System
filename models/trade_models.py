# models/trade_models.py
from datetime import datetime
from models import db
from sqlalchemy.ext.mutable import MutableDict


# ＊Generation, Groupはユーザー管理やシステム全体で利用している。
class Generation(db.Model):
    __tablename__ = 'generations'
    generation_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    generation_name = db.Column(db.String(50), nullable=False)
    activeness = db.Column(db.Integer, nullable=False, default=1)  # 0: 非アクティブ, 1: アクティブ
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Group(db.Model):
    __tablename__ = 'groups'
    group_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    generation_id = db.Column(db.Integer, db.ForeignKey('generations.generation_id'), nullable=False)
    group_name = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# モデル：Request
class Request(db.Model):
    __tablename__ = 'request'
    request_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    generation_id = db.Column(db.Integer, db.ForeignKey('generations.generation_id'), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('groups.group_id'), nullable=False)
    ticker = db.Column(db.String(20), nullable=False)
    request_type = db.Column(db.String(10), nullable=False)  # "buy" or "sell"
    trade_type = db.Column(db.String(10), nullable=False)    # "成行" or "指値"
    limit_order_price = db.Column(db.Float, nullable=True)     # 指値注文の場合に必須（成行の場合は None）
    request_quantity = db.Column(db.Float, nullable=False)
    requested_execution_date = db.Column(db.Date, nullable=False)  # 実際の執行日
    requested_execution_timing = db.Column(db.String(20), nullable=False)  # 「前場寄付」「前場引け」「後場寄付」「後場引け」「ザラ場」
    requested_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    pending = db.Column(db.Integer, nullable=False, default=0)


# モデル：Accept
class Accept(db.Model):
    __tablename__ = 'accept'
    accept_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    generation_id = db.Column(db.Integer, db.ForeignKey('generations.generation_id'), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('groups.group_id'), nullable=False)
    ticker = db.Column(db.String(20), nullable=False)
    request_type = db.Column(db.String(10), nullable=False)  # "buy" or "sell"
    request_date = db.Column(db.DateTime, nullable=False)     # 申請時刻
    accepted_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)  # 承認時刻
    transaction_date = db.Column(db.Date, nullable=False)     # 実際の取引日
    transaction_price = db.Column(db.Float, nullable=False)
    transaction_quantity = db.Column(db.Float, nullable=False)


# モデル：PLRecord
class PLRecord(db.Model):
    __tablename__ = 'pl_record'
    pl_record_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    generation_id = db.Column(db.Integer, db.ForeignKey('generations.generation_id'), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('groups.group_id'), nullable=False)
    ticker = db.Column(db.String(20), nullable=False)
    # pl_data は、各日付 (YYYYMMDD) をキーとした dict 形式のデータを保持する
    # 例：
    # {
    #   "20230501": {
    #       "close_price": 123.45,
    #       "holding_quantity": 100.0,
    #       "sold_quantity": 0,
    #       "transaction_price": 120.0,
    #       "sold_price": 0,
    #       "holding_pl": 345.0,
    #       "sold_pl": -500.0
    #   },
    #   "20230502": { ... },
    #   ...
    # }
    pl_data = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
