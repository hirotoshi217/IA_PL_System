# config.py
# 必要に応じて Flask の設定クラスを作り分けるサンプル
import os

class BaseConfig:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'SUPER_SECRET_KEY')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

class DevelopmentConfig(BaseConfig):
    DEBUG = True

class ProductionConfig(BaseConfig):
    DEBUG = False
