# CLAUDE.md

## Important Instructions for Claude

### ⚠️ MANDATORY: String Localization (ZERO TOLERANCE FOR HARDCODED STRINGS)

**BEFORE writing ANY code, you MUST follow these rules:**

#### 🚫 NEVER DO THIS (Hardcoded Strings):
```python
# ❌ WRONG - Python
flash('Product created successfully', 'success')
return render_template('page.html', title='My Page')

# ❌ WRONG - Templates
<h1>Product List</h1>
<button>Save Changes</button>
<p>Total: 100 items</p>
```

#### ✅ ALWAYS DO THIS (Translated Strings):
```python
# ✅ CORRECT - Python
from flask_babel import gettext as _
flash(_('Product created successfully'), 'success')
return render_template('page.html', title=_('My Page'))

# ✅ CORRECT - Templates
<h1>{{ _('Product List') }}</h1>
<button>{{ _('Save Changes') }}</button>
<p>{{ _('Total: {} items').format(count) }}</p>
```

#### Absolute Requirements:
- **100% of user-facing strings** MUST be wrapped in `_()` function
- **Templates**: EVERY visible text uses `{{ _('English text') }}`
- **Python**: EVERY flash message, label, button text, error message uses `_('text')`
- **Special characters**: Escape `%` as `%%` in translations (e.g., `_('Target: 25-35%%')`)
- **No exceptions**: Button labels, headings, messages, tooltips, placeholders - ALL must be translated

#### Workflow for Every Code Change:
1. **Write code** with `_()` around ALL user-facing strings (use English as the key)
2. **Extract strings**: `pybabel extract -F babel.cfg -o messages.pot .`
3. **Update catalogs**: `pybabel update -i messages.pot -d translations -l he` (and `-l en` if needed)
4. **Add translations**: Edit `translations/he/LC_MESSAGES/messages.po` to add Hebrew translations
5. **Compile**: `pybabel compile -d translations`
6. **Reference**: Check `translations_to_add.po` for 800+ pre-mapped translations

#### Translation Files:
- Hebrew: `translations/he/LC_MESSAGES/messages.po`
- English: `translations/en/LC_MESSAGES/messages.po`
- Reference: `translations_to_add.po` (800+ existing translations)

**This is NON-NEGOTIABLE. Every single string you write must be translatable.**

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
  - Keep CLAUDE.md updated with the latest changes (if needed, new feature, modified model, etc..)

### Migration Handling
- **Local dev has no database** - empty SQLite file only
- **Production database**: PostgreSQL (use PostgreSQL-compatible SQL syntax)
- **All migrations must use HTTP endpoints** for remote execution
- **Migration pattern**: Create endpoint at `/migrate_[feature_name]`, user will confirm when done
- **Cleanup**: Remove migration endpoints after user confirms completion
- **Important**: Use `FALSE`/`TRUE` for boolean defaults in PostgreSQL (not 0/1)

## Project Overview

Costflow is a Flask-based web application for cost management and inventory tracking in production businesses with multi-supplier support and intelligent stock management.

## Tech Stack

- **Backend**: Python 3.10+, Flask
- **Database**: SQLAlchemy ORM with SQLite (default) or PostgreSQL
- **Frontend**: Jinja2 templates with HTML/CSS
- **Internationalization**: Flask-Babel (Hebrew default, English available)
  - Translation files: `translations/he/` and `translations/en/`
  - Language switching: `?lang=en` or `?lang=he` parameter
  - Template usage: `{{ _('English text') }}` for translatable strings
  - Python usage: `from flask_babel import gettext as _` then `_('text')`
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
    - `products.py`: Product management
    - `premakes.py`: Premake management (Products with is_premake=True)
    - `raw_materials.py`: Raw material management with multi-supplier support
    - `suppliers.py`: Supplier management (updated Dec 2024 to include packaging items)
    - `production.py`: Production logging for products and premakes
    - `inventory.py`: Bulk inventory upload/import
    - `recipe_import.py`: Recipe import from Excel with validation and diff
    - `weekly_costs.py`: Weekly labor costs and sales tracking
    - `reports.py`: Weekly and monthly reporting
    - `admin.py`: Database backup/restore, audit logs
    - `categories.py`: Category management
    - `labor.py`: Labor/employee management
    - `packaging.py`: Packaging materials management with multi-supplier support (updated Dec 2024)
    - `utils.py`: Shared utility functions and stock calculations

## Core Database Models

- **Product**: Unified model for products/premakes/preproducts (boolean flags)
- **ProductComponent**: Links products to materials/premakes/packaging/loss
  - Supports `component_type='loss'` for water loss tracking (negative quantity)
- **RawMaterial**: Raw material tracking with `is_unlimited` flag for infinite-stock materials
- **RawMaterialSupplier**: Multi-supplier support with individual pricing per supplier and SKU tracking
  - `sku` field (VARCHAR(100), nullable): Optional SKU for supplier-specific product identification
  - Enables reliable material matching during inventory imports when product names vary by supplier
- **PackagingSupplier**: Multi-supplier support for packaging materials (added Dec 2024)
  - Similar structure to RawMaterialSupplier with pricing, SKU, and primary supplier designation
  - `price_per_package` field for package-level pricing
  - Enables supplier-specific stock tracking for packaging
- **StockLog**: Inventory tracking with supplier information for raw materials and packaging
- **ProductionLog**: Production events with actual cost tracking
- **WeeklyLaborCost/WeeklyProductSales**: Weekly tracking and reporting
- **StockAudit**: Physical count variance tracking

### Key Relationships
- Product → ProductComponent → {RawMaterial, Product(as premake), Packaging}
- RawMaterial → RawMaterialSupplier → Supplier (many-to-many with pricing)
- Packaging → PackagingSupplier → Supplier (many-to-many with pricing)
- Product/RawMaterial/Packaging → StockLog (tracks inventory changes per supplier)
- Product → ProductionLog (tracks production with actual costs)
- WeeklyLaborCost → {WeeklyProductSales, WeeklyLaborEntry}

## Key Features & Routes

Main functional areas:
- **Dashboard** (`/`): Main interface with weekly production tracking, stock values, and packaging inventory
  - Displays total packaging stock value alongside other inventory metrics
- **Raw Materials** (`/raw_materials`): Inventory management with stock tracking
- **Premakes** (`/premakes`): Intermediate preparation management with nested components
- **Products** (`/products`): Product management with recipe cost calculations
- **Production** (`/production`, `/production/premakes`): Separate production logging for products and premakes
- **Weekly Management** (`/weekly_costs`, `/close_week_confirm`): Labor costs and weekly closing
- **Reports** (`/reports/weekly`, `/reports/monthly`): Comprehensive reporting
- **Inventory** (`/inventory/upload`): Bulk data import from Excel/CSV
- **Recipe Import** (`/recipes/upload`): Bulk recipe import from Excel with validation and diff
- **Categories** (`/categories`): Category management for all item types
- **Admin** (`/admin/backup`, `/admin/restore`, `/audit_log`): System administration

## Development Commands

### Local Development
```bash
# IMPORTANT: Always use the specific virtual environment at ~/workspace/venv/costflow/bin/
# This ensures consistent Python environment across all development

# Activate virtual environment (shortcut if configured)
venv costflow

# OR use the full path directly:
source ~/workspace/venv/costflow/bin/activate

# Run development server
~/workspace/venv/costflow/bin/python run.py
# Server runs on http://0.0.0.0:8080

# For any Python commands, always use:
~/workspace/venv/costflow/bin/python [command]
# For pip installations:
~/workspace/venv/costflow/bin/pip install [package]
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
- `SECRET_KEY`: Flask secret key for session management (default: `dev-secret-key-change-in-production`)
  - **Production**: Set to a random, secure value (e.g., `openssl rand -hex 32`)
  - **Required for**: Session storage, flash messages, recipe import temporary files

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
- Supplier-specific stock tracking for raw materials and packaging
- Intelligent deduction strategy: "Primary supplier first, then others" during production
- Automatic fallback when primary supplier stock depleted
- **Unlimited Materials**: Materials (like water) can be marked as unlimited with `is_unlimited=True`
  - Return `float('inf')` for stock calculations
  - No stock deduction during production
  - No supplier tracking required
  - Display "∞" symbol in UI
  - Cost per unit is 0 (zero contribution to product costs)
- **Premake Stock Management**:
  - Premake production creates positive 'add' StockLog entries
  - Premake consumption tracked ONLY via ProductionLog (not StockLog)
  - Week closing with "waste" option creates 'set to 0' StockLog entries (not negative adds)
  - This ensures complete stock zeroing including beginning stock carryover
- **Raw Material Deletion**:
  - **Smart deletion** system checks for historical usage before deletion
  - **Hard delete**: Materials with no history (no stock logs, audits, or product usage) are permanently deleted
  - **Soft delete**: Materials with historical data are marked `is_deleted=True` and hidden from UI
  - Soft-deleted materials preserve all historical data (StockLog, StockAudit, ProductComponent, pricing)
  - Historical reports and production logs retain access to soft-deleted material details
  - Migration required: Run `/migrate_add_raw_material_is_deleted` endpoint to add `is_deleted` column

### Production Tracking
- ProductionLog records production events with timestamps
- WeeklyProduction aggregates production data by week
- Audit logging tracks all significant data changes

### File Uploads
- Product images stored in `static/uploads/products/`
- Max upload size: 16MB
- Excel/CSV import for bulk data operations

### Inventory Import with SKU Support
- **Required columns**: שם מוצר (Product Name), סה"כ כמות (Total Quantity), מחיר ממוצע (Average Price)
- **Optional columns**: מק"ט (SKU), ספק (Supplier)
- **Matching Priority**:
  1. SKU + Supplier (most reliable) - matches via RawMaterialSupplier.sku
  2. Product Name (fallback) - matches via RawMaterial.name
- **Benefits of SKU**:
  - Reliable identification when product names vary between suppliers
  - Supplier-specific stock tracking and pricing updates
  - Eliminates ambiguity in multi-supplier scenarios
- **Usage**: Add SKU field when creating/editing raw materials per supplier (optional)

### Recipe Import System
- **Purpose**: Bulk import/update of recipes (premakes/products) from Excel sheets
- **Access**: Settings → ייבוא מתכונים (Recipe Import)
- **Flow**: Upload Excel → Select Sheet → Review & Validate → Confirm Import

#### Excel File Format
**Metadata Section** (first 3-5 rows):
```
# Recipe Import Metadata
type: premake
category: בצקים
unit: g
```
- `type:` - Required - "premake" or "product"
- `category:` - Required - Must match existing category name exactly
- `unit:` - Optional - Default "g"
- Blank row after metadata

**Recipe Format**:
- Recipe Title (row 1)
- Headers (rows 2-3): "חומר גלם", "סוג", "מחיר לק"ג", "משקל במתכון (ג')", "מחיר במתכון"
- Material rows:
  - Columns: Name, Type, Price/kg, Weight(g), Total Price
  - Types: "חומר גלם" (raw_material), "הכנה" (premake), "מוצר מקדים" (preproduct), "אבדן" (loss)
- Total row: "סך הכל"
- 100g calculation row: "100 ג'" (informational, calculated in UI)
- Blank rows between recipes

#### Import Features
- **Material Matching**: Exact name matching against database
- **Validation**: Shows missing materials, prevents import if any not found
- **Price Comparison**: Displays sheet price vs DB price (no automatic updates)
- **Diff Detection**: For existing recipes, shows added/removed/changed components
- **Loss Tracking**: Supports "אבדן" (water loss) as special component type with negative quantity
- **100g Cost**: Calculated and displayed in review (net weight after loss)
- **Batch Operations**: Import multiple recipes from one sheet at once

#### Loss Components
- Stored as ProductComponent with `component_type='loss'`
- Quantity is negative (e.g., -190.4g for water loss)
- `component_id=0` (no reference needed)
- Deducted from total weight for net weight calculation
- Used in cost per 100g calculations: `(total_cost / net_weight) * 100`
- Applies to both products and premakes

#### Utility Functions
- `calculate_100g_cost(product)`: Returns (cost_100g, total_cost, net_weight) accounting for loss
- All cost functions in `utils.py` handle loss components by skipping them in cost calculations


## Important System Notes

### Current Architecture
- **Unified Product Model**: Single Product model with boolean flags (is_product, is_premake, is_preproduct)
- **Multi-Supplier Support**: Primary/secondary suppliers with individual pricing for both raw materials and packaging
- **Packaging Supplier Implementation** (Added Dec 2024):
  - PackagingSupplier model for many-to-many relationships with pricing
  - Supplier-specific stock tracking via StockLog
  - SKU support for reliable identification
  - Primary supplier designation for intelligent deduction
  - Discount percentage application per supplier
  - Stock transfer options when removing suppliers
- **Production Cost Tracking**: ProductionLog stores actual costs per batch with supplier breakdown
- **Stock Management**: "Primary first, then others" deduction strategy
- **Hebrew Default**: RTL support throughout with Flask-Babel

### Key Implementation Details
- Labor components shown in UI but not used in cost calculations
- Premakes support recursive nesting with cycle prevention
- Stock calculations use 'set' (absolute) or 'add' (incremental) operations
- Weekly dashboard uses weighted average of actual production costs
- Migration endpoint pattern: `/migrate_[feature_name]` for remote DB updates
- **Packaging Multi-Supplier Support** (Dec 2024):
  - Packaging materials now support multiple suppliers with individual pricing
  - Each supplier can have unique SKU and pricing for the same packaging item
  - Primary supplier designation for intelligent stock deduction
  - Supplier discounts automatically applied to packaging costs
  - Stock tracked per supplier with automatic fallback during production

## Translation & Internationalization

### Overview
- **System**: Flask-Babel for i18n support
- **Supported Languages**: Hebrew (he) - default, English (en)
- **Language Selection**: Via `?lang=en` or `?lang=he` URL parameter (persists in session)
- **Translation Files**: `translations/{lang}/LC_MESSAGES/messages.po` and `.mo`

### Translation Workflow

#### For Templates (Jinja2)
Use the `_()` function for all user-facing strings:
```html
<!-- English as key -->
<h1>{{ _('Weekly Report') }}</h1>
<button>{{ _('Save Changes') }}</button>

<!-- With variables -->
<p>{{ _('Performance summary for week') }} {{ week_start.strftime('%d/%m/%Y') }}</p>
```

#### For Python Code
Import and use gettext:
```python
from flask_babel import gettext as _

# Flash messages
flash(_('Product created successfully'), 'success')
flash(_('Error: Invalid data'), 'error')

# Variables
return _('Total items: {}').format(count)
```

### Working with Translation Files

#### Extract Translatable Strings
```bash
# Scan all templates and Python files
pybabel extract -F babel.cfg -o messages.pot .
```

#### Update Translation Catalogs
```bash
# Update existing translations
pybabel update -i messages.pot -d translations -l he
pybabel update -i messages.pot -d translations -l en
```

#### Edit Translation Files
Open `translations/he/LC_MESSAGES/messages.po`:
```po
msgid "Weekly Report"
msgstr "דו\"ח שבועי"

msgid "Revenue"
msgstr "הכנסות"
```

#### Compile Translations
```bash
# Compile .po files to .mo (binary) for production use
pybabel compile -d translations
```

### Translation Status
- **Templates**: Partially migrated (~5% complete)
- **Python Routes**: Not yet migrated
- **Reference File**: `translations_to_add.po` contains 800+ pre-mapped Hebrew↔English translations
- **Guide**: See `TRANSLATION_GUIDE.md` for complete migration instructions

### Adding New Strings
1. Add string in templates: `{{ _('New String') }}`
2. Extract: `pybabel extract -F babel.cfg -o messages.pot .`
3. Update: `pybabel update -i messages.pot -d translations`
4. Edit `.po` files to add Hebrew translation
5. Compile: `pybabel compile -d translations`
6. Restart app to load new translations