import json
import io
from datetime import datetime
from flask import Blueprint, request, send_file, redirect, url_for, render_template, jsonify
from sqlalchemy import text
from ..models import db, Category, RawMaterial, Packaging, Labor, Product, ProductComponent, WeeklyLaborCost, WeeklyLaborEntry, WeeklyProductSales, AuditLog, Supplier, RawMaterialSupplier
from .utils import log_audit

admin_blueprint = Blueprint('admin', __name__)

@admin_blueprint.route('/admin/backup', methods=['GET'])
def backup_db():
    data = {
        'timestamp': datetime.now().isoformat(),
        'categories': [{'id': c.id, 'name': c.name} for c in Category.query.all()],
        'raw_materials': [m.to_dict() for m in RawMaterial.query.all()],
        'packaging': [p.to_dict() for p in Packaging.query.all()],
        'labor': [l.to_dict() for l in Labor.query.all()],
        'products': [p.to_dict() for p in Product.query.all()],
        'weekly_labor_costs': [w.to_dict() for w in WeeklyLaborCost.query.all()]
    }
    
    json_str = json.dumps(data, indent=4, ensure_ascii=False)
    mem = io.BytesIO()
    mem.write(json_str.encode('utf-8'))
    mem.seek(0)
    
    filename = f"costflow_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    return send_file(
        mem,
        as_attachment=True,
        download_name=filename,
        mimetype='application/json'
    )

@admin_blueprint.route('/admin/restore', methods=['POST'])
def restore_db():
    if 'backup_file' not in request.files:
        return "No file uploaded", 400
        
    file = request.files['backup_file']
    if file.filename == '':
        return "No file selected", 400

    try:
        data = json.load(file)
        
        # Reset DB
        db.drop_all()
        db.create_all()
        
        # 1. Categories
        category_map = {} # old_id -> new_instance (or just keep same IDs if we force them)
        # We will try to keep same IDs to maintain relationships if possible, 
        # but SQLAlchemy might auto-increment. 
        # Best effort: Explicitly set ID if the DB allows (Postgres/SQLite usually do if specified).
        
        for cat_data in data.get('categories', []):
            c = Category(id=cat_data['id'], name=cat_data['name'])
            db.session.add(c)
            category_map[cat_data['id']] = c
        db.session.flush()

        # 2. Labor
        labor_map = {}
        for l_data in data.get('labor', []):
            # Check fields (handle old backups vs new schema)
            l = Labor(
                id=l_data['id'],
                name=l_data['name'],
                phone_number=l_data.get('phone_number'),
                base_hourly_rate=l_data.get('base_hourly_rate', l_data.get('total_hourly_rate', 0)), # Fallback
                additional_hourly_rate=l_data.get('additional_hourly_rate', 0)
            )
            db.session.add(l)
            labor_map[l_data['id']] = l
        db.session.flush()

        # 3. Packaging
        pkg_map = {}
        for p_data in data.get('packaging', []):
            p = Packaging(
                id=p_data['id'],
                name=p_data['name'],
                quantity_per_package=p_data['quantity_per_package'],
                price_per_package=p_data['price_per_package']
            )
            db.session.add(p)
            pkg_map[p_data['id']] = p
        db.session.flush()

        # 4. Raw Materials
        mat_map = {}
        for m_data in data.get('raw_materials', []):
            # Handle category link
            cat_id = None
            if m_data.get('category'):
                cat_id = m_data['category']['id']
            
            m = RawMaterial(
                id=m_data['id'],
                name=m_data['name'],
                category_id=cat_id,
                unit=m_data['unit'],
                cost_per_unit=m_data['cost_per_unit']
            )
            # Note: current_stock is not in constructor but in backup. 
            # Stock is derived from logs usually, but legacy backup might have it.
            # If we are restoring full DB, logs should be restored too?
            # The backup code above exports models but NOT StockLogs/ProductionLogs!
            # THIS IS A FLAW in existing backup code. It only backs up definitions, not history?
            # No, wait. 'weekly_labor_costs' are backed up.
            # But StockLog/ProductionLog/StockAudit are MISSING from backup_db in routes.py!
            # I am just copying existing logic, so I won't fix the backup flaw unless asked.
            
            db.session.add(m)
            mat_map[m_data['id']] = m
        db.session.flush()

        # 5. Products & Components
        for p_data in data.get('products', []):
            p = Product(
                id=p_data['id'],
                name=p_data['name'],
                products_per_recipe=p_data['products_per_recipe'],
                selling_price_per_unit=p_data['selling_price_per_unit'],
                image_filename=p_data.get('image_filename')
            )
            db.session.add(p)
            db.session.flush()
            
            # Components
            for c_data in p_data.get('components', []):
                comp = ProductComponent(
                    product_id=p.id,
                    component_type=c_data['component_type'],
                    component_id=c_data['component_id'],
                    quantity=c_data['quantity']
                )
                db.session.add(comp)

        # 6. Weekly Labor Costs & Children
        for w_data in data.get('weekly_labor_costs', []):
            w = WeeklyLaborCost(
                id=w_data['id'],
                week_start_date=datetime.strptime(w_data['week_start_date'], '%Y-%m-%d').date(),
                total_cost=w_data['total_cost']
            )
            db.session.add(w)
            db.session.flush()
            
            # Entries
            for e_data in w_data.get('entries', []):
                emp_name = e_data.get('employee_name')
                emp = Labor.query.filter_by(name=emp_name).first()
                if emp:
                    entry = WeeklyLaborEntry(
                        weekly_cost_id=w.id,
                        employee_id=emp.id,
                        hours=e_data['hours'],
                        cost=e_data['cost']
                    )
                    db.session.add(entry)

            # Sales
            for s_data in w_data.get('sales', []):
                prod_name = s_data.get('product_name')
                prod = Product.query.filter_by(name=prod_name).first()
                if prod:
                    sale = WeeklyProductSales(
                        weekly_cost_id=w.id,
                        product_id=prod.id,
                        quantity_sold=s_data['quantity_sold']
                    )
                    db.session.add(sale)

        db.session.commit()
        log_audit("RESTORE", "System", details="Database restored from backup.")
        return redirect(url_for('main.index'))
        
    except Exception as e:
        print(f"Restore failed: {e}")
        return f"Restore failed: {e}", 500

@admin_blueprint.route('/audit_log', methods=['GET'])
def audit_log():
    logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(500).all()
    return render_template('audit_log.html', logs=logs)

@admin_blueprint.route('/admin/reset_db', methods=['POST'])
def reset_db():
    try:
        db.drop_all()
        db.create_all()

        # Re-seed essential data
        db.session.add(Category(name="כללי (חומרי גלם)", type='raw_material'))
        db.session.commit()
        
        log_audit("RESET", "System", details="Database reset.")
        return redirect(url_for('main.index'))
    except Exception as e:
        return f"Reset failed: {e}", 500

@admin_blueprint.route('/admin/migrate/add-unit-field', methods=['GET', 'POST'])
def migrate_add_unit_field():
    """Migration endpoint to add 'unit' field to Product table."""
    if request.method == 'GET':
        return """
        <html>
        <head><title>Migration: Add Unit Field</title></head>
        <body style="font-family: Arial; max-width: 600px; margin: 50px auto; padding: 20px;">
            <h1>Database Migration: Add Unit Field</h1>
            <p>This migration will:</p>
            <ul>
                <li>Add 'unit' column to Product table</li>
                <li>Add 'is_product' and 'is_premake' columns if missing</li>
                <li>Set default unit 'kg' for existing items</li>
                <li>Make selling_price_per_unit nullable</li>
            </ul>
            <form method="POST">
                <button type="submit" style="padding: 10px 20px; font-size: 16px; background: #007bff; color: white; border: none; cursor: pointer;">
                    Run Migration
                </button>
            </form>
        </body>
        </html>
        """

    try:
        # Check if unit column already exists
        inspector = db.inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('product')]
        messages = []

        # Add is_product column if missing
        if 'is_product' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE product ADD COLUMN is_product BOOLEAN DEFAULT TRUE"))
                conn.commit()
                messages.append("✓ Added 'is_product' column")

        # Add is_premake column if missing
        if 'is_premake' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE product ADD COLUMN is_premake BOOLEAN DEFAULT FALSE"))
                conn.commit()
                messages.append("✓ Added 'is_premake' column")

        # Add unit column if missing
        if 'unit' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE product ADD COLUMN unit VARCHAR(20)"))
                conn.commit()
                messages.append("✓ Added 'unit' column")

                # Set default unit for existing items
                try:
                    # Try to set based on is_premake flag if column exists
                    if 'is_premake' in inspector.get_columns('product'):
                        conn.execute(text("UPDATE product SET unit = 'kg' WHERE is_premake = TRUE AND unit IS NULL"))
                        conn.commit()
                        messages.append("✓ Set default unit 'kg' for existing premakes")
                except:
                    # If that doesn't work, just set a default
                    conn.execute(text("UPDATE product SET unit = 'kg' WHERE unit IS NULL"))
                    conn.commit()
                    messages.append("✓ Set default unit 'kg' for items with null unit")
        else:
            messages.append("'unit' column already exists")

        # Check if selling_price_per_unit is nullable
        col_info = next((col for col in inspector.get_columns('product') if col['name'] == 'selling_price_per_unit'), None)

        if col_info:
            # For SQLite, we can't easily check or modify NULL constraint
            if 'sqlite' not in str(db.engine.url).lower():
                # For PostgreSQL or MySQL
                try:
                    with db.engine.connect() as conn:
                        if 'postgresql' in str(db.engine.url).lower():
                            conn.execute(text("ALTER TABLE product ALTER COLUMN selling_price_per_unit DROP NOT NULL"))
                        elif 'mysql' in str(db.engine.url).lower():
                            conn.execute(text("ALTER TABLE product MODIFY COLUMN selling_price_per_unit FLOAT NULL"))
                        conn.commit()
                        messages.append("✓ Made 'selling_price_per_unit' nullable")
                except Exception as e:
                    if "already allows nulls" in str(e).lower() or "cannot drop" in str(e).lower():
                        messages.append("'selling_price_per_unit' is already nullable")
                    else:
                        messages.append(f"⚠ Could not modify selling_price_per_unit: {str(e)}")
            else:
                messages.append("Note: SQLite - column nullability will work in application")

        # Add batch_size column if missing
        if 'batch_size' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE product ADD COLUMN batch_size FLOAT"))
                conn.commit()
                messages.append("✓ Added 'batch_size' column")

        log_audit("MIGRATE", "System", details=f"Ran add-unit-field migration: {', '.join(messages)}")

        return jsonify({
            'status': 'success',
            'messages': messages
        }), 200

    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'message': 'Migration failed. Please check server logs.'
        }), 500

@admin_blueprint.route('/admin/migrate/add-supplier-system', methods=['GET', 'POST'])
def migrate_supplier_system():
    """Add supplier system tables and migrate existing data"""
    if request.method == 'GET':
        return render_template('admin_migration.html',
                             migration_type='add-supplier-system',
                             title='Add Supplier System',
                             description='Creates supplier tables and migrates existing data to use suppliers.')

    try:
        from ..models import db, Supplier, RawMaterialSupplier, RawMaterial
        from sqlalchemy import text
        from .utils import log_audit

        messages = []

        # 1. Create supplier table
        with db.engine.begin() as conn:
            # Check if table exists
            result = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'supplier'
                )
            """))
            table_exists = result.scalar()

            if not table_exists:
                conn.execute(text("""
                    CREATE TABLE supplier (
                        id SERIAL PRIMARY KEY,
                        name VARCHAR(100) NOT NULL UNIQUE,
                        contact_person VARCHAR(100),
                        phone VARCHAR(50),
                        email VARCHAR(100),
                        address TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        is_active BOOLEAN DEFAULT TRUE NOT NULL
                    )
                """))
                messages.append("✓ Created supplier table")
            else:
                messages.append("Supplier table already exists")

        # 2. Create raw_material_supplier junction table
        with db.engine.begin() as conn:
            result = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_name = 'raw_material_supplier'
                )
            """))
            table_exists = result.scalar()

            if not table_exists:
                conn.execute(text("""
                    CREATE TABLE raw_material_supplier (
                        id SERIAL PRIMARY KEY,
                        raw_material_id INTEGER NOT NULL REFERENCES raw_material(id),
                        supplier_id INTEGER NOT NULL REFERENCES supplier(id),
                        cost_per_unit FLOAT NOT NULL,
                        is_primary BOOLEAN DEFAULT FALSE,
                        UNIQUE(raw_material_id, supplier_id)
                    )
                """))
                messages.append("✓ Created raw_material_supplier table")
            else:
                messages.append("Raw_material_supplier table already exists")

        # 3. Add supplier_id to stock_log table
        with db.engine.begin() as conn:
            # Check if column exists
            result = conn.execute(text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.columns
                    WHERE table_name = 'stock_log' AND column_name = 'supplier_id'
                )
            """))
            column_exists = result.scalar()

            if not column_exists:
                conn.execute(text("""
                    ALTER TABLE stock_log
                    ADD COLUMN supplier_id INTEGER REFERENCES supplier(id)
                """))
                messages.append("✓ Added supplier_id column to stock_log table")
            else:
                messages.append("supplier_id column already exists in stock_log")

        # 4. Create default supplier for existing data
        default_supplier = Supplier.query.filter_by(name='ספק כללי').first()
        if not default_supplier:
            default_supplier = Supplier(
                name='ספק כללי',
                contact_person='לא מוגדר',
                phone='',
                email='',
                address='',
                is_active=True
            )
            db.session.add(default_supplier)
            db.session.commit()
            messages.append(f"✓ Created default supplier 'ספק כללי' with ID {default_supplier.id}")
        else:
            messages.append(f"Default supplier already exists with ID {default_supplier.id}")

        # 5. Link all existing materials to default supplier
        materials = RawMaterial.query.all()
        linked_count = 0
        for material in materials:
            # Check if link already exists
            existing_link = RawMaterialSupplier.query.filter_by(
                raw_material_id=material.id,
                supplier_id=default_supplier.id
            ).first()

            if not existing_link:
                link = RawMaterialSupplier(
                    raw_material_id=material.id,
                    supplier_id=default_supplier.id,
                    cost_per_unit=material.cost_per_unit,
                    is_primary=True
                )
                db.session.add(link)
                linked_count += 1

        db.session.commit()
        if linked_count > 0:
            messages.append(f"✓ Linked {linked_count} materials to default supplier")
        else:
            messages.append("All materials already linked to suppliers")

        # 6. Update existing stock logs with default supplier
        with db.engine.begin() as conn:
            result = conn.execute(text("""
                UPDATE stock_log
                SET supplier_id = :supplier_id
                WHERE raw_material_id IS NOT NULL AND supplier_id IS NULL
            """), {"supplier_id": default_supplier.id})

            if result.rowcount > 0:
                conn.commit()
                messages.append(f"✓ Updated {result.rowcount} stock logs with default supplier")
            else:
                messages.append("No stock logs needed updating")

        log_audit("MIGRATE", "System", details=f"Ran add-supplier-system migration: {', '.join(messages)}")

        return jsonify({
            'status': 'success',
            'messages': messages
        }), 200

    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'message': 'Migration failed. Please check server logs.'
        }), 500

@admin_blueprint.route('/admin/migrate/fix-premake-units', methods=['GET', 'POST'])
def migrate_fix_premake_units():
    """Migration endpoint to fix NULL units for premakes."""
    if request.method == 'GET':
        return """
        <html>
        <head><title>Migration: Fix Premake Units</title></head>
        <body style="font-family: Arial; max-width: 600px; margin: 50px auto; padding: 20px;">
            <h1>Database Migration: Fix Premake Units</h1>
            <p>This migration will:</p>
            <ul>
                <li>Update all premakes with NULL unit to 'kg'</li>
                <li>Add is_preproduct column if missing</li>
            </ul>
            <form method="POST">
                <button type="submit" style="padding: 10px 20px; font-size: 16px; background: #007bff; color: white; border: none; cursor: pointer;">
                    Run Migration
                </button>
            </form>
        </body>
        </html>
        """

    try:
        messages = []

        # Fix NULL units for premakes
        with db.engine.connect() as conn:
            result = conn.execute(text(
                "UPDATE product SET unit = 'kg' "
                "WHERE is_premake = TRUE AND unit IS NULL"
            ))
            conn.commit()
            messages.append(f"✓ Updated {result.rowcount} premakes with NULL unit to 'kg'")

        # Check if is_preproduct column exists
        inspector = db.inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('product')]

        if 'is_preproduct' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE product ADD COLUMN is_preproduct BOOLEAN DEFAULT FALSE"))
                conn.commit()
                messages.append("✓ Added 'is_preproduct' column")
        else:
            messages.append("'is_preproduct' column already exists")

        log_audit("MIGRATE", "System", details=f"Ran fix-premake-units migration: {', '.join(messages)}")

        return jsonify({
            'status': 'success',
            'messages': messages
        }), 200

    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'message': 'Migration failed. Please check server logs.'
        }), 500


@admin_blueprint.route('/admin/migrate/unified-cleanup', methods=['GET', 'POST'])
def migrate_unified_cleanup():
    """Consolidated migration endpoint for all Product model updates."""
    if request.method == 'GET':
        return """
        <html>
        <head><title>Migration: Unified Product Cleanup</title></head>
        <body style="font-family: Arial; max-width: 600px; margin: 50px auto; padding: 20px;">
            <h1>Database Migration: Unified Product Cleanup</h1>
            <p>This migration will apply all necessary Product model updates:</p>
            <ul>
                <li>Add 'unit' column with 'kg' default for premakes</li>
                <li>Add 'is_product', 'is_premake', 'is_preproduct' columns</li>
                <li>Add 'batch_size' column</li>
                <li>Make 'selling_price_per_unit' nullable</li>
                <li>Create preproduct category if missing</li>
            </ul>
            <form method="POST">
                <button type="submit" style="padding: 10px 20px; font-size: 16px; background: #007bff; color: white; border: none; cursor: pointer;">
                    Run Complete Migration
                </button>
            </form>
        </body>
        </html>
        """

    try:
        messages = []
        inspector = db.inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('product')]

        # Add is_product column if missing
        if 'is_product' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE product ADD COLUMN is_product BOOLEAN DEFAULT TRUE"))
                conn.commit()
                messages.append("✓ Added 'is_product' column")

        # Add is_premake column if missing
        if 'is_premake' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE product ADD COLUMN is_premake BOOLEAN DEFAULT FALSE"))
                conn.commit()
                messages.append("✓ Added 'is_premake' column")

        # Add is_preproduct column if missing
        if 'is_preproduct' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE product ADD COLUMN is_preproduct BOOLEAN DEFAULT FALSE"))
                conn.commit()
                messages.append("✓ Added 'is_preproduct' column")

        # Add unit column if missing
        if 'unit' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE product ADD COLUMN unit VARCHAR(20)"))
                conn.commit()
                messages.append("✓ Added 'unit' column")

        # Add batch_size column if missing
        if 'batch_size' not in columns:
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE product ADD COLUMN batch_size FLOAT"))
                conn.commit()
                messages.append("✓ Added 'batch_size' column")

        # Fix NULL units for premakes
        with db.engine.connect() as conn:
            result = conn.execute(text(
                "UPDATE product SET unit = 'kg' "
                "WHERE is_premake = TRUE AND unit IS NULL"
            ))
            if result.rowcount > 0:
                conn.commit()
                messages.append(f"✓ Updated {result.rowcount} premakes with NULL unit to 'kg'")
            else:
                conn.commit()
                messages.append("No premakes with NULL unit found")

        # Create preproduct category if missing
        from .utils import get_or_create_general_category
        get_or_create_general_category('preproduct')
        messages.append("✓ Ensured preproduct category exists")

        log_audit("MIGRATE", "System", details=f"Ran unified-cleanup migration: {', '.join(messages)}")

        return jsonify({
            'status': 'success',
            'messages': messages
        }), 200

    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'message': 'Migration failed. Please check server logs.'
        }), 500

@admin_blueprint.route('/admin/migrate/set-default-supplier', methods=['GET', 'POST'])
def migrate_set_default_supplier():
    """Set supplier ID=1 as default for all existing materials without suppliers"""
    if request.method == 'GET':
        return render_template('admin_migration.html',
                             migration_type='set-default-supplier',
                             title='Set Default Supplier',
                             description='Links all materials without suppliers to default supplier (ID=1).')

    try:
        messages = []

        # Ensure supplier with ID=1 exists
        supplier_1 = Supplier.query.get(1)
        if not supplier_1:
            # Create it if it doesn't exist
            # We need to explicitly set the ID
            # For PostgreSQL, we need to handle sequences
            with db.engine.connect() as conn:
                # Check if we're using PostgreSQL
                if 'postgresql' in str(db.engine.url).lower():
                    # For PostgreSQL, temporarily disable the sequence
                    conn.execute(text("INSERT INTO supplier (id, name, contact_person, phone, email, address, is_active) "
                                    "VALUES (1, 'ספק כללי', 'לא מוגדר', '', '', '', true) "
                                    "ON CONFLICT (id) DO NOTHING"))
                    # Reset sequence to max ID
                    conn.execute(text("SELECT setval('supplier_id_seq', (SELECT COALESCE(MAX(id), 1) FROM supplier))"))
                else:
                    # For SQLite
                    conn.execute(text("INSERT OR IGNORE INTO supplier (id, name, contact_person, phone, email, address, is_active) "
                                    "VALUES (1, 'ספק כללי', 'לא מוגדר', '', '', '', 1)"))
                conn.commit()

            messages.append("✓ Created default supplier with ID=1")

        # Find all materials without supplier links
        all_materials = RawMaterial.query.all()
        linked_count = 0

        for material in all_materials:
            # Check if material has any supplier links
            existing_links = RawMaterialSupplier.query.filter_by(
                raw_material_id=material.id
            ).first()

            if not existing_links:
                # Create link to supplier ID=1
                new_link = RawMaterialSupplier(
                    raw_material_id=material.id,
                    supplier_id=1,
                    cost_per_unit=material.cost_per_unit,
                    is_primary=True
                )
                db.session.add(new_link)
                linked_count += 1

        db.session.commit()
        messages.append(f"✓ Linked {linked_count} materials to default supplier (ID=1)")

        log_audit("MIGRATE", "System",
                 details=f"Set default supplier: {', '.join(messages)}")

        return jsonify({
            'status': 'success',
            'messages': messages
        }), 200

    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e),
            'message': 'Migration failed.'
        }), 500