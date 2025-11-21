# costflow

## Project Overview

**costflow** is a web-based cost management and product tracking application designed to help businesses manage their inventory, production, and costs. It allows users to track raw materials, labor, and packaging, combine them into products, and calculate costs per unit and per recipe. It also features inventory tracking (stock logs) and production logging.

**Key Technologies:**
*   **Backend:** Python 3.10+, Flask
*   **Database:** SQLAlchemy (SQLite by default, supports PostgreSQL)
*   **Frontend:** HTML, CSS (static/css/style.css), Jinja2 Templates
*   **Internationalization:** Flask-Babel (supports English and Hebrew)
*   **Containerization:** Docker

## Architecture

The application follows a standard Flask application factory pattern.

*   **`run.py`**: The entry point of the application.
*   **`app/`**: The core application package.
    *   **`__init__.py`**: Application factory (`create_app`), database initialization, and configuration (Babel, SQLAlchemy).
    *   **`models.py`**: SQLAlchemy database models defining the schema (`RawMaterial`, `Product`, `Labor`, `Packaging`, etc.).
    *   **`routes.py`**: Defines the URL routes and view functions (`main_blueprint`).
    *   **`database.py`**: (Inferred) Likely contains DB connection helpers or seeds (though `models.py` holds the schema).
*   **`templates/`**: Jinja2 HTML templates for the UI.
*   **`static/`**: Static assets like CSS.
*   **`translations/`**: `.po` and `.mo` files for internationalization.
*   **`csv/`**: Python scripts for data extraction/insertion (likely for seeding or migration).

## Building and Running

### Local Development

1.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

2.  **Run the Application:**
    ```bash
    python run.py
    ```
    The app will start on `http://0.0.0.0:8080`.

    *Note: `run.py` sets `debug=True`.*

### Docker

1.  **Build the Image:**
    ```bash
    docker build -t costflow .
    ```

2.  **Run the Container:**
    ```bash
    docker run -p 8080:8080 costflow
    ```

## Development Conventions

*   **Database:** The app uses SQLAlchemy ORM. `db.create_all()` is called in `run.py` to initialize the schema.
*   **Localization:** Language is selected via the `lang` query parameter or session. Default is Hebrew ('he').
*   **Environment Variables:**
    *   `DATABASE_URL`: Connection string (defaults to `sqlite:///waste_tracking.db`).
    *   `CURRENCY_SYMBOL`: Symbol used in templates (defaults to `$`).
