# CLAUDE.md

## Important Instructions for Claude

### Git and Testing Responsibilities
- **Git operations**: User handles all git commits and pushes
- **Testing**: User performs all testing
- **Claude's role**:
  - Provide git commands and single-line commit messages for copy/paste
  - Specify what to test after each change
  - Format: Always provide git commands as:
    ```bash
    git add .
    git commit -m "Type: Brief description of change"
    git push
    ```
  - Commit message types: Feat, Fix, Chore, Refactor, Docs

### Migration Handling
- **Local dev has no database** - empty SQLite file only
- **All migrations must use HTTP endpoints** for remote execution
- **Migration pattern**: Create endpoint at `/migrate_[feature_name]`, user will confirm when done
- **Cleanup**: Remove migration endpoints after user confirms completion

## Project Overview

Costflow is a Flask-based web application for cost management and inventory tracking in production businesses with multi-supplier support and intelligent stock management.

## Tech Stack

- **Backend**: Python 3.10+, Flask
- **Database**: SQLAlchemy ORM with SQLite (default) or PostgreSQL
- **Frontend**: Jinja2 templates with HTML/CSS
- **Internationalization**: Flask-Babel (Hebrew default, English available)
- **Data Processing**: Pandas, OpenPyxl for Excel operations

## Application Architecture

The application follows Flask's application factory pattern with modular blueprints:

- **Entry Point**: `run.py` - Creates app instance and initializes database
- **Core Application**: `app/` package
  - `__init__.py`: Application factory, Babel configuration, database initialization
  - `models.py`: SQLAlchemy models (see Database Models section)
  - `database.py`: Database helper functions
  - `routes/`: Modular blueprint structure (refactored Dec 2024)
    - `main.py`: Dashboard and overview
    - `products.py`: Product management and migration
    - `premakes.py`: Premake management (Products with is_premake=True)
    - `raw_materials.py`: Raw material management with multi-supplier support
    - `suppliers.py`: Supplier management
    - `production.py`: Production logging for products and premakes
    - `inventory.py`: Bulk inventory upload/import
    - `weekly_costs.py`: Weekly labor costs and sales tracking
    - `reports.py`: Weekly and monthly reporting
    - `admin.py`: Database backup/restore, audit logs
    - `categories.py`: Category management
    - `labor.py`: Labor/employee management
    - `packaging.py`: Packaging materials management
    - `utils.py`: Shared utility functions and stock calculations

## Core Database Models

- **Product**: Unified model for products/premakes/preproducts (boolean flags)
- **ProductComponent**: Links products to materials/premakes/packaging
- **RawMaterial/RawMaterialSupplier**: Multi-supplier support with individual pricing
- **StockLog**: Inventory tracking with supplier information
- **ProductionLog**: Production events with actual cost tracking
- **WeeklyLaborCost/WeeklyProductSales**: Weekly tracking and reporting
- **StockAudit**: Physical count variance tracking

### Key Relationships
- Product → ProductComponent → {RawMaterial, Product(as premake), Packaging}
- RawMaterial → RawMaterialSupplier → Supplier (many-to-many with pricing)
- Product/RawMaterial → StockLog (tracks inventory changes per supplier)
- Product → ProductionLog (tracks production with actual costs)
- WeeklyLaborCost → {WeeklyProductSales, WeeklyLaborEntry}

## Key Features & Routes

Main functional areas:
- **Dashboard** (`/`): Main interface with weekly production tracking
- **Raw Materials** (`/raw_materials`): Inventory management with stock tracking
- **Premakes** (`/premakes`): Intermediate preparation management with nested components
- **Products** (`/products`): Product management with recipe cost calculations
- **Production** (`/production`, `/production/premakes`): Separate production logging for products and premakes
- **Weekly Management** (`/weekly_costs`, `/close_week_confirm`): Labor costs and weekly closing
- **Reports** (`/reports/weekly`, `/reports/monthly`): Comprehensive reporting
- **Inventory** (`/inventory/upload`): Bulk data import from Excel/CSV
- **Categories** (`/categories`): Category management for all item types
- **Admin** (`/admin/backup`, `/admin/restore`, `/audit_log`): System administration

## Development Commands

### Local Development
```bash
# Activate virtual environment
venv costflow

# Run development server
python run.py
# Server runs on http://0.0.0.0:8080
```

### Docker Deployment
```bash
# Build and run container
docker build -t costflow .
docker run -p 8080:8080 costflow
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
- Products can include: raw materials, premakes (other Products with is_premake=True), packaging, and labor (labor not currently active)
- Premakes can include: raw materials, packaging, and other premakes (nested/recursive)
- Total cost calculation is recursive for nested premakes
- Prime cost = Raw materials + Packaging + Premakes (excludes labor)
- Cost per unit calculated based on recipe batch size or premake batch size
- Multi-supplier pricing: System uses actual supplier prices during production
- Production tracking: Each batch stores its actual cost based on suppliers used
- Weekly reports: Use weighted average of actual production costs, not estimates

### Stock Management
- StockLog tracks all inventory changes with timestamps and supplier information
- Actions: 'add' (increment) or 'set' (absolute value)
- Current stock calculated from latest 'set' action plus subsequent 'add' actions
- Supplier-specific stock tracking for raw materials
- Intelligent deduction strategy: "Primary supplier first, then others" during production
- Automatic fallback when primary supplier stock depleted

### Production Tracking
- ProductionLog records production events with timestamps
- WeeklyProduction aggregates production data by week
- Audit logging tracks all significant data changes

### File Uploads
- Product images stored in `static/uploads/products/`
- Max upload size: 16MB
- Excel/CSV import for bulk data operations



## Important System Notes

### Current Architecture
- **Unified Product Model**: Single Product model with boolean flags (is_product, is_premake, is_preproduct)
- **Multi-Supplier Support**: Primary/secondary suppliers with individual pricing
- **Production Cost Tracking**: ProductionLog stores actual costs per batch with supplier breakdown
- **Stock Management**: "Primary first, then others" deduction strategy
- **Migrated Products**: Kept for historical data, marked with "(Migrated to Premake: X)"
- **Hebrew Default**: RTL support throughout with Flask-Babel

### Key Implementation Details
- Labor components shown in UI but not used in cost calculations
- Premakes support recursive nesting with cycle prevention
- Stock calculations use 'set' (absolute) or 'add' (incremental) operations
- Weekly dashboard uses weighted average of actual production costs
- Migration endpoint pattern: `/migrate_[feature_name]` for remote DB updates