# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Costflow is a Flask-based web application for cost management and inventory tracking in production businesses. It manages raw materials, labor costs, packaging, and products with comprehensive cost calculations and inventory tracking.

## Tech Stack

- **Backend**: Python 3.10+, Flask
- **Database**: SQLAlchemy ORM with SQLite (default) or PostgreSQL
- **Frontend**: Jinja2 templates with HTML/CSS
- **Internationalization**: Flask-Babel (Hebrew default, English available)
- **Data Processing**: Pandas, OpenPyxl for Excel operations

## Application Architecture

The application follows Flask's application factory pattern with blueprints:

- **Entry Point**: `run.py` - Creates app instance and initializes database
- **Core Application**: `app/` package
  - `__init__.py`: Application factory, Babel configuration, database initialization
  - `models.py`: SQLAlchemy models (RawMaterial, Product, Labor, Packaging, StockLog, ProductionLog, Category, WeeklyProduction, AuditLog)
  - `routes.py`: All application routes as single blueprint (`main_blueprint`)
  - `database.py`: Database helper functions

## Key Features & Routes

Main functional areas accessible via routes:
- **Dashboard** (`/`): Main interface with weekly production tracking
- **Raw Materials** (`/raw_materials`): Inventory management with stock tracking
- **Products** (`/products`): Product management with recipe cost calculations
- **Labor** (`/labor`): Worker management with hourly rates
- **Categories** (`/categories`): Category management for materials and products
- **Production** (`/production`, `/close_week`): Production logging and weekly reports
- **Data Import** (`/upload_inventory`): Excel/CSV data import functionality

## Development Commands

### Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Run development server (with debug mode)
python run.py
# Server runs on http://0.0.0.0:8080
```

### Docker Deployment
```bash
# Build image
docker build -t costflow .

# Run container
docker run -p 8080:8080 costflow
```

### Database Operations
```bash
# Database is auto-created on first run via db.create_all() in run.py
# To seed initial data from CSV files:
python csv/insert_raw_materials.py
python csv/insert_labor.py
python csv/insert_packaging.py
python csv/insert_products.py
```

## Environment Variables

- `DATABASE_URL`: Database connection string (default: `sqlite:///waste_tracking.db`)
- `CURRENCY_SYMBOL`: Currency symbol for display (default: `₪`)

## Key Implementation Details

### Localization
- Language selection via `?lang=` query parameter or session storage
- Default locale: Hebrew ('he')
- Translations located in `translations/` directory

### Cost Calculation
- Products have recipes linking to raw materials with quantities
- Total cost includes: raw material costs + labor costs + packaging costs
- Cost per unit calculated based on recipe batch size

### Stock Management
- StockLog tracks all inventory changes with timestamps
- Actions: 'add' (increment) or 'set' (absolute value)
- Current stock calculated from latest 'set' action plus subsequent 'add' actions

### Production Tracking
- ProductionLog records production events with timestamps
- WeeklyProduction aggregates production data by week
- Audit logging tracks all significant data changes

### File Uploads
- Product images stored in `static/uploads/products/`
- Max upload size: 16MB
- Excel/CSV import for bulk data operations

## Database Schema Relationships

- **Category** → RawMaterial, Product (one-to-many)
- **Product** → Recipe → RawMaterial (many-to-many with quantities)
- **Product** → ProductLabor → Labor (many-to-many with hours)
- **Product** → Packaging (many-to-one)
- **RawMaterial** → StockLog (one-to-many)
- **Product** → ProductionLog (one-to-many)

## Frontend Structure

Templates in `templates/` use base template inheritance:
- `base.html`: Main layout with navigation
- Feature-specific templates for each route
- Static assets in `static/css/style.css`

## Recent Changes & Features (Nov 2024)

### 1. Weekly & Monthly Reports
- Added comprehensive reporting system accessible via dropdown menus
- Weekly reports (`/reports/weekly`) show sales by category, labor costs, and profitability
- Monthly reports (`/reports/monthly`) aggregate weekly data for trend analysis
- Both reports include Hebrew localization and RTL support

### 2. Stock Audit System
- StockAudit model tracks discrepancies between system stock and physical counts
- Records auditor name, variance, and financial impact
- Automatic variance calculation when setting stock (action_type='set')
- Dedicated audit page (`/stock_audits`) with filtering and analytics

### 3. Food Cost Analytics
- **Food Cost Tracking**: Calculates total material + packaging costs for production
- **Weekly Report**: Shows total food cost, average cost per recipe, food cost percentage
- **Monthly Report**: Includes visual bar chart of weekly food cost trends
- **Target Indicators**: Color-coded (green: 25-35%, yellow: <25%, red: >35%)
- **Data Aggregation**: Production and labor data aggregated to avoid duplicate rows

### 4. Key Calculations
- **Food Cost** = Sum of (raw materials + packaging) × quantity for each recipe
- **Food Cost %** = (Total food cost / Total revenue) × 100%
- **Prime Cost** = Raw materials + Packaging (per product)
- **Net Profit** = Revenue - Material Costs - Labor Costs - Stock Variance