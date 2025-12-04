import json
import io
from datetime import datetime
from flask import Blueprint, request, send_file, redirect, url_for, render_template, jsonify
from ..models import db, Category, RawMaterial, Packaging, Labor, Product, ProductComponent, WeeklyLaborCost, WeeklyLaborEntry, WeeklyProductSales, AuditLog
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

@admin_blueprint.route('/admin/migrate_premakes', methods=['GET', 'POST'])
def migrate_premakes_to_products():
    """Endpoint to migrate Premakes to unified Product model"""
    from ..models import Product, Premake, PremakeComponent, ProductComponent, StockLog, ProductionLog, StockAudit
    from sqlalchemy import text

    if request.method == 'GET':
        # Show migration status page
        try:
            premake_count = Premake.query.count()
        except:
            premake_count = 0

        try:
            product_count = Product.query.count()
        except:
            product_count = 0

        # Check if migration already done
        migration_done = False
        try:
            # Try to check if columns exist by accessing them
            products_with_flags = Product.query.filter(
                (Product.is_premake == True) | (Product.batch_size != None)
            ).count()
            migration_done = products_with_flags > 0 and premake_count > 0
        except:
            # Columns don't exist yet, migration not done
            migration_done = False

        return render_template('migrate_premakes.html',
                              premake_count=premake_count,
                              product_count=product_count,
                              migration_done=migration_done)

    # POST - Run migration
    try:
        print("Starting migration: Unifying Products and Premakes...")

        # Step 1: Add new columns to Product table if they don't exist
        with db.engine.connect() as conn:
            # Check database type
            dialect_name = db.engine.dialect.name

            if dialect_name == 'postgresql':
                # PostgreSQL approach
                result = conn.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'product'
                """))
                columns = [row[0] for row in result]
            else:
                # SQLite approach
                result = conn.execute(text("PRAGMA table_info(product)"))
                columns = [row[1] for row in result]

            # Add columns if they don't exist
            if 'is_product' not in columns:
                try:
                    conn.execute(text("ALTER TABLE product ADD COLUMN is_product BOOLEAN DEFAULT TRUE"))
                    conn.commit()
                except:
                    pass  # Column might already exist

            if 'is_premake' not in columns:
                try:
                    conn.execute(text("ALTER TABLE product ADD COLUMN is_premake BOOLEAN DEFAULT FALSE"))
                    conn.commit()
                except:
                    pass  # Column might already exist

            if 'batch_size' not in columns:
                try:
                    conn.execute(text("ALTER TABLE product ADD COLUMN batch_size FLOAT"))
                    conn.commit()
                except:
                    pass  # Column might already exist

        # Step 2: Set is_product=True for all existing products
        Product.query.update({Product.is_product: True, Product.is_premake: False})
        db.session.commit()

        # Step 3: Migrate all Premake records to Product table
        premakes = Premake.query.all()
        premake_id_mapping = {}

        for premake in premakes:
            new_product = Product(
                name=premake.name,
                category_id=premake.category_id,
                products_per_recipe=1,
                selling_price_per_unit=0,
                is_product=False,
                is_premake=True,
                batch_size=premake.batch_size
            )
            db.session.add(new_product)
            db.session.flush()
            premake_id_mapping[premake.id] = new_product.id

        db.session.commit()

        # Step 4: Migrate PremakeComponent records to ProductComponent
        premake_components = PremakeComponent.query.all()

        for comp in premake_components:
            if comp.premake_id in premake_id_mapping:
                component_id = comp.component_id
                if comp.component_type == 'premake' and comp.component_id in premake_id_mapping:
                    component_id = premake_id_mapping[comp.component_id]

                new_component = ProductComponent(
                    product_id=premake_id_mapping[comp.premake_id],
                    component_type=comp.component_type,
                    component_id=component_id,
                    quantity=comp.quantity
                )
                db.session.add(new_component)

        db.session.commit()

        # Step 5: Update ProductComponent records where component_type='premake'
        product_components = ProductComponent.query.filter_by(component_type='premake').all()

        for comp in product_components:
            if comp.component_id in premake_id_mapping:
                comp.component_id = premake_id_mapping[comp.component_id]

        db.session.commit()

        # Step 6: Update StockLog references
        with db.engine.connect() as conn:
            # Check database type
            dialect_name = db.engine.dialect.name

            if dialect_name == 'postgresql':
                result = conn.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'stock_log'
                """))
                columns = [row[0] for row in result]
            else:
                result = conn.execute(text("PRAGMA table_info(stock_log)"))
                columns = [row[1] for row in result]

            if 'product_id' not in columns:
                try:
                    conn.execute(text("ALTER TABLE stock_log ADD COLUMN product_id INTEGER"))
                    conn.commit()
                except:
                    pass  # Column might already exist

        stock_logs = StockLog.query.filter(StockLog.premake_id != None).all()
        for log in stock_logs:
            if log.premake_id in premake_id_mapping:
                db.session.execute(
                    text("UPDATE stock_log SET product_id = :product_id WHERE id = :log_id"),
                    {"product_id": premake_id_mapping[log.premake_id], "log_id": log.id}
                )

        db.session.commit()

        # Step 7: Update ProductionLog references
        production_logs = ProductionLog.query.filter(ProductionLog.premake_id != None).all()

        for log in production_logs:
            if log.premake_id in premake_id_mapping:
                if not log.product_id:
                    log.product_id = premake_id_mapping[log.premake_id]

        db.session.commit()

        # Step 8: Update StockAudit references
        with db.engine.connect() as conn:
            # Check database type
            dialect_name = db.engine.dialect.name

            if dialect_name == 'postgresql':
                result = conn.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'stock_audit'
                """))
                columns = [row[0] for row in result]
            else:
                result = conn.execute(text("PRAGMA table_info(stock_audit)"))
                columns = [row[1] for row in result]

            if 'product_id' not in columns:
                try:
                    conn.execute(text("ALTER TABLE stock_audit ADD COLUMN product_id INTEGER"))
                    conn.commit()
                except:
                    pass  # Column might already exist

        stock_audits = StockAudit.query.filter(StockAudit.premake_id != None).all()

        for audit in stock_audits:
            if audit.premake_id in premake_id_mapping:
                db.session.execute(
                    text("UPDATE stock_audit SET product_id = :product_id WHERE id = :audit_id"),
                    {"product_id": premake_id_mapping[audit.premake_id], "audit_id": audit.id}
                )

        db.session.commit()

        # Step 9: Update Product.migrated_to_premake_id references
        with db.engine.connect() as conn:
            # Check database type
            dialect_name = db.engine.dialect.name

            if dialect_name == 'postgresql':
                result = conn.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'product'
                """))
                columns = [row[0] for row in result]
            else:
                result = conn.execute(text("PRAGMA table_info(product)"))
                columns = [row[1] for row in result]

            if 'migrated_to_product_id' not in columns:
                try:
                    conn.execute(text("ALTER TABLE product ADD COLUMN migrated_to_product_id INTEGER"))
                    conn.commit()
                except:
                    pass  # Column might already exist

        products_with_migration = Product.query.filter(Product.migrated_to_premake_id != None).all()

        for product in products_with_migration:
            if product.migrated_to_premake_id in premake_id_mapping:
                db.session.execute(
                    text("UPDATE product SET migrated_to_product_id = :new_id WHERE id = :product_id"),
                    {"new_id": premake_id_mapping[product.migrated_to_premake_id], "product_id": product.id}
                )

        db.session.commit()

        log_audit("MIGRATE", "System", details=f"Migrated {len(premakes)} premakes to unified Product model")

        return jsonify({
            "success": True,
            "message": f"Successfully migrated {len(premakes)} premakes",
            "mapping": premake_id_mapping
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500
