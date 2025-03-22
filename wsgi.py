from app import create_app, db
from flask_migrate import Migrate
import models  # models 内の各ファイル（trade_models.py など）が読み込まれるように

app = create_app()
migrate = Migrate(app, db)

if __name__ == '__main__':
    app.run()
