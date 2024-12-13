import os
from flask import Flask
from .models import db

def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    @app.context_processor
    def inject_globals():
        return {
            'currency_symbol': os.getenv('CURRENCY_SYMBOL', '$')
        }

    # Load configurations
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///waste_tracking.db")
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Initialize database
    db.init_app(app)

    # Register blueprints
    from .routes import main_blueprint
    app.register_blueprint(main_blueprint)

    return app

