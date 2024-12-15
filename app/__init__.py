import os
from flask import Flask, request, session
from flask_babel import Babel
from .models import db

def get_locale():
    selected_locale = request.args.get('lang', session.get('lang', 'he'))
    print(f"Selected locale: {selected_locale}")  # Debug locale selection
    return selected_locale

def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    @app.context_processor
    def inject_globals():
        return {'currency_symbol': os.getenv('CURRENCY_SYMBOL', '$')}

    @app.context_processor
    def inject_locale():
        return dict(get_locale=get_locale)

    # Load configurations
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///waste_tracking.db")
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    app.config['BABEL_DEFAULT_LOCALE'] = 'he'
    app.config['BABEL_SUPPORTED_LOCALES'] = ['en', 'he']
    app.config['BABEL_TRANSLATION_DIRECTORIES'] = '../translations'

    babel = Babel(app, locale_selector=get_locale)

    # Initialize database
    db.init_app(app)

    # Register blueprints
    from .routes import main_blueprint
    app.register_blueprint(main_blueprint)

    return app

