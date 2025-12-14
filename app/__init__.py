import os
from flask import Flask, request, session
from flask_babel import Babel
from .models import db

def get_locale():
    selected_locale = request.args.get('lang', session.get('lang', 'he'))
    return selected_locale

def create_app():
    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    @app.context_processor
    def inject_globals():
        return {'currency_symbol': os.getenv('CURRENCY_SYMBOL', '₪')}

    @app.context_processor
    def inject_locale():
        return dict(get_locale=get_locale)

    @app.before_request
    def before_request():
        """Capture language parameter and save to session for persistence across pages"""
        if 'lang' in request.args:
            session['lang'] = request.args.get('lang')

    # Load configurations
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///waste_tracking.db")
    app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

    # Secret key for session management (required for flash messages and sessions)
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

    app.config['BABEL_DEFAULT_LOCALE'] = 'he'
    app.config['BABEL_SUPPORTED_LOCALES'] = ['en', 'he']
    app.config['BABEL_TRANSLATION_DIRECTORIES'] = '../translations'

    # Use /images as the persistent volume for product images (production)
    # or /tmp/images for local development
    if os.path.exists('/images'):
        app.config['UPLOAD_FOLDER'] = '/images'
    else:
        # Local development - use /tmp/images
        app.config['UPLOAD_FOLDER'] = '/tmp/images'

    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max upload size

    # Create the images directory if it doesn't exist
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        try:
            os.makedirs(app.config['UPLOAD_FOLDER'])
        except OSError:
            # Directory might already exist or we don't have permissions
            pass

    Babel(app, locale_selector=get_locale)

    # Initialize database
    db.init_app(app)

    # Register blueprints
    from .routes import main_blueprint, admin_blueprint, categories_blueprint, labor_blueprint, packaging_blueprint, production_blueprint, weekly_costs_blueprint, reports_blueprint, inventory_blueprint, products_blueprint, raw_materials_blueprint, premakes_blueprint, suppliers_blueprint, recipe_import_blueprint
    from .routes.unit_fix_migration import unit_fix_blueprint
    app.register_blueprint(main_blueprint)
    app.register_blueprint(admin_blueprint)
    app.register_blueprint(categories_blueprint)
    app.register_blueprint(labor_blueprint)
    app.register_blueprint(packaging_blueprint)
    app.register_blueprint(production_blueprint)
    app.register_blueprint(weekly_costs_blueprint)
    app.register_blueprint(reports_blueprint)
    app.register_blueprint(inventory_blueprint)
    app.register_blueprint(products_blueprint)
    app.register_blueprint(raw_materials_blueprint)
    app.register_blueprint(premakes_blueprint)
    app.register_blueprint(suppliers_blueprint)
    app.register_blueprint(recipe_import_blueprint)
    app.register_blueprint(unit_fix_blueprint)

    with app.app_context():
        db.create_all()

    return app

