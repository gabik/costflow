# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Costflow is a Flask-based web application for cost management and inventory tracking in production businesses. It manages raw materials, premakes (intermediate preparations), packaging, and products with comprehensive cost calculations and inventory tracking.

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
    - `main.py`: Dashboard, raw materials, premakes, stock audits
    - `products.py`: Product management and migration
    - `production.py`: Production logging for products and premakes
    - `inventory.py`: Bulk inventory upload/import
    - `weekly_costs.py`: Weekly labor costs and sales tracking
    - `reports.py`: Weekly and monthly reporting
    - `admin.py`: Database backup/restore, audit logs
    - `categories.py`: Category management
    - `labor.py`: Labor/employee management
    - `packaging.py`: Packaging materials management
    - `utils.py`: Shared utility functions

## Database Models

Core models in `app/models.py`:
- **RawMaterial**: Base ingredients with cost per unit
- **Premake**: Intermediate preparations that can contain raw materials, packaging, or other premakes (nested)
- **PremakeComponent**: Links premakes to their components
- **Product**: Final products with recipes and selling prices
- **ProductComponent**: Links products to raw materials, premakes, packaging, and labor
- **Packaging**: Packaging materials with cost calculations
- **Labor**: Employee records with hourly rates (Note: Not currently used in production)
- **Category**: Categorization for raw materials, products, and premakes
- **StockLog**: Tracks inventory changes for raw materials and premakes
- **ProductionLog**: Records production events for products and premakes
- **WeeklyLaborCost**: Weekly labor cost tracking
- **WeeklyProductSales**: Weekly sales and waste tracking
- **WeeklyLaborEntry**: Individual labor entries per week
- **StockAudit**: Physical stock count audits with variance tracking
- **AuditLog**: System-wide audit trail for data changes

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
- Products can include: raw materials, premakes, packaging, and labor (labor not currently active)
- Premakes can include: raw materials, packaging, and other premakes (nested/recursive)
- Total cost calculation is recursive for nested premakes
- Prime cost = Raw materials + Packaging + Premakes (excludes labor)
- Cost per unit calculated based on recipe batch size or premake batch size

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

- **Category** → RawMaterial, Product, Premake (one-to-many)
- **Product** → ProductComponent → RawMaterial/Premake/Packaging/Labor (many-to-many with quantities)
- **Premake** → PremakeComponent → RawMaterial/Packaging/Premake (many-to-many with quantities)
- **RawMaterial** → StockLog (one-to-many)
- **Premake** → StockLog (one-to-many)
- **Product** → ProductionLog (one-to-many)
- **Premake** → ProductionLog (one-to-many)
- **Product** → WeeklyProductSales (one-to-many)
- **WeeklyLaborCost** → WeeklyProductSales (one-to-many)
- **WeeklyLaborCost** → WeeklyLaborEntry (one-to-many)

## Frontend Structure

Templates in `templates/` use base template inheritance:
- `base.html`: Main layout with navigation
- Feature-specific templates for each route
- Static assets in `static/css/style.css`

## Recent Changes & Features

### December 2024 Updates

#### 1. Blueprint Refactoring
- Modularized routes from single `routes.py` into 10 separate blueprint modules
- Improved code organization and maintainability
- Each functional area now has its own blueprint file

#### 2. Premake System Enhancement
- **Nested Premakes**: Premakes can now include other premakes as components
- **Product to Premake Migration**: Products can be converted to premakes
  - Preserves inventory levels
  - Maintains production history
  - Keeps sales history intact for reporting
  - Migrated products remain in database for historical reference (marked with "(Migrated to Premake: X)")
  - Migrated products are automatically filtered from production and selection lists
- **Separate Production Tracking**: Dedicated production logging for premakes

#### 3. Fixed Issues
- Restored missing `edit_product` function after migration implementation
- Added missing `PremakeComponent` import
- Fixed premake production modal JavaScript issues

### November 2024 Features

#### 1. Weekly & Monthly Reports
- Added comprehensive reporting system accessible via dropdown menus
- Weekly reports (`/reports/weekly`) show sales by category, labor costs, and profitability
- Monthly reports (`/reports/monthly`) aggregate weekly data for trend analysis
- Both reports include Hebrew localization and RTL support

#### 2. Stock Audit System
- StockAudit model tracks discrepancies between system stock and physical counts
- Records auditor name, variance, and financial impact
- Automatic variance calculation when setting stock (action_type='set')
- Dedicated audit page (`/stock_audits`) with filtering and analytics

#### 3. Food Cost Analytics
- **Food Cost Tracking**: Calculates total material + packaging costs for production
- **Weekly Report**: Shows total food cost, average cost per recipe, food cost percentage
- **Monthly Report**: Includes visual bar chart of weekly food cost trends
- **Target Indicators**: Color-coded (green: 25-35%, yellow: <25%, red: >35%)
- **Data Aggregation**: Production and labor data aggregated to avoid duplicate rows

#### 4. Key Calculations
- **Food Cost** = Sum of (raw materials + packaging) × quantity for each recipe
- **Food Cost %** = (Total food cost / Total revenue) × 100%
- **Prime Cost** = Raw materials + Packaging + Premakes (per product)
- **Net Profit** = Revenue - Material Costs - Labor Costs - Stock Variance

## Important Notes

### Current System State
1. **Labor Components**: While the UI displays labor options in products, labor components are not actively saved or used in cost calculations
2. **Premake Nesting**: Premakes support recursive nesting (premakes containing other premakes)
3. **Migration Safety**: Product to Premake migration preserves sales history for reporting continuity
   - Products are NOT deleted to maintain foreign key integrity with WeeklyProductSales
   - Migrated products are renamed with "(Migrated to Premake: [name])" suffix
   - Migrated products are automatically filtered from production and weekly cost selections
4. **Blueprint Structure**: Routes are organized into 10 separate blueprint modules for better maintainability

### Known Considerations
- Circular dependencies in nested premakes are prevented at the UI level (self-reference check)
- Stock calculations support both 'add' (incremental) and 'set' (absolute) operations
- Hebrew is the default language with RTL support throughout the application

## Recent Changes (December 2024)

### Route Organization Refactoring
- **Completed migration of routes to separate blueprint files** for better code organization:
  - All admin routes moved from `main.py` to `app/routes/admin.py`
  - Raw materials routes moved to `app/routes/raw_materials.py`
  - Each functional area now has its own dedicated blueprint module
  - Template URL references updated to use correct blueprint namespaces

### Removed Features
- **Migrate Products functionality** (`/admin/migrate_products`) - Completely removed as no longer needed

### UI Improvements
- **Added search and category filtering** to:
  - Raw Materials page - Live search by name, filter by category dropdown
  - Premakes page - Live search by name, filter by category dropdown
  - Both include responsive filter cards with Bootstrap styling
  - JavaScript-based client-side filtering for instant results