# models/trade_models.py
from datetime import datetime
from models import db


class Generation(db.Model):
    __tablename__ = 'generations'
    generation_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    generation_name = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Group(db.Model):
    __tablename__ = 'groups'
    group_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    generation_id = db.Column(db.Integer, db.ForeignKey('generations.generation_id'), nullable=False)
    group_name = db.Column(db.String(50), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Holding(db.Model):
    __tablename__ = 'holding'
    holding_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    generation_id = db.Column(db.Integer, db.ForeignKey('generations.generation_id'), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('groups.group_id'), nullable=False)

    trade_date = db.Column(db.DateTime, default=datetime.utcnow)
    ticker = db.Column(db.String(20), nullable=False)
    ticker_name = db.Column(db.String(100), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    buy_price = db.Column(db.Float, nullable=False)
    current_price = db.Column(db.Float, nullable=False)
    current_pl = db.Column(db.Float, nullable=False)


class Sold(db.Model):
    __tablename__ = 'sold'
    sold_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    generation_id = db.Column(db.Integer, db.ForeignKey('generations.generation_id'), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('groups.group_id'), nullable=False)

    trade_date = db.Column(db.DateTime, default=datetime.utcnow)
    ticker = db.Column(db.String(20), nullable=False)
    ticker_name = db.Column(db.String(100), nullable=False)
    sold_quantity = db.Column(db.Float, nullable=False)
    buy_price = db.Column(db.Float, nullable=False)
    sell_price = db.Column(db.Float, nullable=False)
    realized_pl = db.Column(db.Float, nullable=False)


class PLHistory(db.Model):
    __tablename__ = 'pl_history'
    pl_history_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    generation_id = db.Column(db.Integer, db.ForeignKey('generations.generation_id'), nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey('groups.group_id'), nullable=False)

    date = db.Column(db.Date, default=datetime.utcnow)
    total_pl = db.Column(db.Float, nullable=False)
