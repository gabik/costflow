# CostFlow

## Project Overview

Costflow is a Flask-based web application designed for comprehensive cost management and inventory tracking in production businesses. It features multi-supplier support, intelligent stock management, recipe costing, and detailed reporting.

## Technical Architecture

### Backend
- **Framework**: Python 3.10+, Flask
- **Database**: SQLAlchemy ORM
  - **Development**: SQLite (`waste_tracking.db`)
  - **Production**: PostgreSQL
- **Internationalization**: `flask-babel` (Hebrew default, English available)
- **Data Processing**: `pandas`, `openpyxl` for Excel operations

### Frontend
- **Templating**: Jinja2 (HTML/CSS)
- **Framework**: Bootstrap 5 (via CDN)
- **Libraries**: jQuery 3.7.1, Select2
- **Fonts**: Google Fonts (Rubik for Hebrew, Inter for English)
- **Styling**: Custom CSS in `static/css/style.css` + Bootstrap utility classes

### Key Directories & Files
- `run.py`: Application entry point.
- `app/`: Core application package.
  - `__init__.py`: App factory, database & Babel init.
  - `models.py`: Database models (Products, RawMaterials, Suppliers, Logs).
  - `routes/`: Modular blueprints for different functional areas.
- `templates/`: Jinja2 HTML templates.
- `translations/`: `.po` and `.mo` files for localization.
- `Dockerfile`: Production container configuration.

## Development Workflow

### 1. Environment Setup
The project uses a virtual environment.
```bash
# Activate virtual environment
source ~/workspace/venv/costflow/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Running the Application
```bash
python run.py
# Server runs on http://0.0.0.0:8080
```

### 3. Database Management
- The app uses `db.create_all()` in `run.py` to initialize the database if it doesn't exist.
- **Migrations**: Currently handled via explicit migration endpoints or scripts (e.g., `/migrate_[feature]`), rather than strict Alembic revisions. Check `CLAUDE.md` for specific migration notes.

## Critical Conventions

### ⚠️ Localization (Strict Requirement)
**ALL user-facing strings must be localized.** Hardcoded strings are strictly forbidden.

- **Python**:
  ```python
  from flask_babel import gettext as _
  flash(_('Product created successfully'), 'success')
  ```
- **Templates**:
  ```html
  <h1>{{ _('Dashboard') }}</h1>
  <button>{{ _('Save') }}</button>
  ```
- **Process**:
  1. Wrap strings in `_()`.
  2. Extract: `pybabel extract -F babel.cfg -o messages.pot .`
  3. Update: `pybabel update -i messages.pot -d translations -l he`
  4. Edit `.po` files.
  5. Compile: `pybabel compile -d translations`

### Coding Style
- **Blueprints**: All routes are organized into blueprints in `app/routes/`. New features should follow this pattern.
- **Models**: Use `app/models.py`. Prefer specific models for complex relationships (e.g., `RawMaterialSupplier` for M:N with extra fields).
- **Boolean fields**: Use `TRUE`/`FALSE` (PostgreSQL compatibility).

## Key Features
- **Recipe Costing**: Recursive calculation for products and premakes.
- **Inventory**: Multi-supplier stock tracking, "First-in" logic (Primary supplier -> Secondary).
- **Imports**: Excel-based bulk import for ingredients and recipes.
- **Reporting**: Weekly and monthly financial reports.

## Documentation
- `CLAUDE.md`: Contains detailed instructions, specific mandates, and feature breakdowns. **Consult this file for in-depth rules.**
- `README.md`: General project entry point.
