from app import create_app, db
import models  # models 内の各ファイル（trade_models.py など）が読み込まれるように

app = create_app()
if __name__ == '__main__':
    app.run()
