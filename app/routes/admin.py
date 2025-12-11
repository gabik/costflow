import json
import io
from datetime import datetime
from flask import Blueprint, request, send_file, redirect, url_for, render_template, jsonify
from sqlalchemy import text
from ..models import (
    db, Category, RawMaterial, Packaging, Labor, Product, ProductComponent,
    WeeklyLaborCost, WeeklyLaborEntry, WeeklyProductSales, AuditLog,
    Supplier, RawMaterialSupplier, PackagingSupplier, StockLog, ProductionLog,
    StockAudit, RawMaterialAlternativeName
)
from .utils import log_audit

admin_blueprint = Blueprint('admin', __name__)

@admin_blueprint.route('/admin/backup', methods=['GET'])
def backup_db():
    """Create comprehensive backup of all database models and relationships"""

    # Count total records for statistics
    total_records = sum([
        Category.query.count(),
        Labor.query.count(),
        Supplier.query.count(),
        RawMaterial.query.count(),
        RawMaterialSupplier.query.count(),
        RawMaterialAlternativeName.query.count(),
        Packaging.query.count(),
        PackagingSupplier.query.count(),
        Product.query.count(),
        StockLog.query.count(),
        ProductionLog.query.count(),
        StockAudit.query.count(),
        WeeklyLaborCost.query.count(),
        AuditLog.query.count()
    ])

    data = {
        'version': '2.0',  # Version tracking for future migrations
        'timestamp': datetime.now().isoformat(),
        'database_type': 'postgresql' if 'postgresql' in str(db.engine.url) else 'sqlite',

        # Level 0 - No dependencies
        'categories': [c.to_dict() for c in Category.query.all()],
        'labor': [l.to_dict() for l in Labor.query.all()],
        'suppliers': [s.to_dict() for s in Supplier.query.all()],
        'audit_logs': [a.to_dict() for a in AuditLog.query.all()],

        # Level 1 - Basic dependencies
        'raw_materials': [m.to_dict() for m in RawMaterial.query.all()],
        'packaging': [p.to_dict() for p in Packaging.query.all()],
        'products': [p.to_dict() for p in Product.query.all()],

        # Level 2 - Secondary dependencies
        'raw_material_alternative_names': [n.to_dict() for n in RawMaterialAlternativeName.query.all()],
        'raw_material_suppliers': [s.to_dict() for s in RawMaterialSupplier.query.all()],
        'packaging_suppliers': [s.to_dict() for s in PackagingSupplier.query.all()],
        'production_logs': [p.to_dict() for p in ProductionLog.query.all()],
        'weekly_labor_costs': [w.to_dict() for w in WeeklyLaborCost.query.all()],

        # Level 3/4 - Complex dependencies
        'stock_logs': [s.to_dict() for s in StockLog.query.all()],
        'stock_audits': [a.to_dict() for a in StockAudit.query.all()],

        # Metadata
        'statistics': {
            'total_records': total_records,
            'model_counts': {
                'categories': Category.query.count(),
                'suppliers': Supplier.query.count(),
                'raw_materials': RawMaterial.query.count(),
                'raw_material_suppliers': RawMaterialSupplier.query.count(),
                'raw_material_alternative_names': RawMaterialAlternativeName.query.count(),
                'packaging': Packaging.query.count(),
                'packaging_suppliers': PackagingSupplier.query.count(),
                'products': Product.query.count(),
                'stock_logs': StockLog.query.count(),
                'production_logs': ProductionLog.query.count(),
                'stock_audits': StockAudit.query.count(),
                'weekly_labor_costs': WeeklyLaborCost.query.count(),
                'labor': Labor.query.count(),
                'audit_logs': AuditLog.query.count()
            }
        }
    }

    json_str = json.dumps(data, indent=4, ensure_ascii=False)
    mem = io.BytesIO()
    mem.write(json_str.encode('utf-8'))
    mem.seek(0)

    filename = f"costflow_backup_v2_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    log_audit("BACKUP", "System", details=f"Full backup created v2.0 with {total_records} records")

    return send_file(
        mem,
        as_attachment=True,
        download_name=filename,
        mimetype='application/json'
    )

@admin_blueprint.route('/admin/restore', methods=['POST'])
def restore_db():
    """Restore complete database from backup file with version support"""
    if 'backup_file' not in request.files:
        return "No file uploaded", 400

    file = request.files['backup_file']
    if file.filename == '':
        return "No file selected", 400

    try:
        data = json.load(file)

        # Version checking
        version = data.get('version', '1.0')
        if version == '1.0':
            # Handle legacy backup format
            return restore_legacy_backup(data)

        # Begin transaction for atomicity
        db.session.begin_nested()

        # Clear existing data in reverse dependency order
        StockAudit.query.delete()
        StockLog.query.delete()
        WeeklyProductSales.query.delete()
        WeeklyLaborEntry.query.delete()
        WeeklyLaborCost.query.delete()
        ProductionLog.query.delete()
        ProductComponent.query.delete()
        PackagingSupplier.query.delete()
        RawMaterialSupplier.query.delete()
        RawMaterialAlternativeName.query.delete()
        Product.query.delete()
        Packaging.query.delete()
        RawMaterial.query.delete()
        Supplier.query.delete()
        Labor.query.delete()
        Category.query.delete()
        AuditLog.query.delete()

        db.session.flush()
        # Restore Level 0 - No dependencies
        for cat_data in data.get('categories', []):
            cat = Category(
                id=cat_data['id'],
                name=cat_data['name'],
                type=cat_data.get('type', 'raw_material')
            )
            db.session.add(cat)

        for labor_data in data.get('labor', []):
            labor = Labor(
                id=labor_data['id'],
                name=labor_data['name'],
                phone_number=labor_data.get('phone_number'),
                base_hourly_rate=labor_data.get('base_hourly_rate', 0),
                additional_hourly_rate=labor_data.get('additional_hourly_rate', 0)
            )
            db.session.add(labor)

        for supplier_data in data.get('suppliers', []):
            supplier = Supplier(
                id=supplier_data['id'],
                name=supplier_data['name'],
                contact_person=supplier_data.get('contact_person'),
                phone=supplier_data.get('phone'),
                email=supplier_data.get('email'),
                address=supplier_data.get('address'),
                created_at=datetime.fromisoformat(supplier_data['created_at']) if supplier_data.get('created_at') else datetime.utcnow(),
                is_active=supplier_data.get('is_active', True),
                discount_percentage=supplier_data.get('discount_percentage', 0.0)
            )
            db.session.add(supplier)

        for audit_log_data in data.get('audit_logs', []):
            audit_log = AuditLog(
                id=audit_log_data['id'],
                timestamp=datetime.fromisoformat(audit_log_data['timestamp']) if audit_log_data.get('timestamp') else datetime.utcnow(),
                action=audit_log_data['action'],
                target_type=audit_log_data['target_type'],
                target_id=audit_log_data.get('target_id'),
                details=audit_log_data.get('details')
            )
            db.session.add(audit_log)

        db.session.flush()

        # Restore Level 1 - Basic dependencies
        for mat_data in data.get('raw_materials', []):
            mat = RawMaterial(
                id=mat_data['id'],
                name=mat_data['name'],
                category_id=mat_data.get('category_id'),
                unit=mat_data['unit'],
                is_unlimited=mat_data.get('is_unlimited', False),
                is_deleted=mat_data.get('is_deleted', False)
            )
            db.session.add(mat)

        for pkg_data in data.get('packaging', []):
            pkg = Packaging(
                id=pkg_data['id'],
                name=pkg_data['name'],
                quantity_per_package=pkg_data['quantity_per_package']
            )
            db.session.add(pkg)

        for prod_data in data.get('products', []):
            prod = Product(
                id=prod_data['id'],
                name=prod_data['name'],
                category_id=prod_data.get('category_id'),
                products_per_recipe=prod_data['products_per_recipe'],
                selling_price_per_unit=prod_data.get('selling_price_per_unit'),
                image_filename=prod_data.get('image_filename'),
                is_product=prod_data.get('is_product', True),
                is_premake=prod_data.get('is_premake', False),
                is_preproduct=prod_data.get('is_preproduct', False),
                batch_size=prod_data.get('batch_size'),
                unit=prod_data.get('unit'),
                is_migrated=prod_data.get('is_migrated', False),
                migrated_to_premake_id=prod_data.get('migrated_to_premake_id'),
                original_prime_cost=prod_data.get('original_prime_cost')
            )
            db.session.add(prod)

        db.session.flush()

        # Restore Level 2 - Secondary dependencies
        for alt_name_data in data.get('raw_material_alternative_names', []):
            alt_name = RawMaterialAlternativeName(
                id=alt_name_data['id'],
                raw_material_id=alt_name_data['raw_material_id'],
                alternative_name=alt_name_data['alternative_name'],
                created_at=datetime.fromisoformat(alt_name_data['created_at']) if alt_name_data.get('created_at') else datetime.utcnow()
            )
            db.session.add(alt_name)

        for rm_supplier_data in data.get('raw_material_suppliers', []):
            rm_supplier = RawMaterialSupplier(
                id=rm_supplier_data['id'],
                raw_material_id=rm_supplier_data['raw_material_id'],
                supplier_id=rm_supplier_data['supplier_id'],
                cost_per_unit=rm_supplier_data['cost_per_unit'],
                is_primary=rm_supplier_data.get('is_primary', False),
                sku=rm_supplier_data.get('sku')
            )
            db.session.add(rm_supplier)

        for pkg_supplier_data in data.get('packaging_suppliers', []):
            pkg_supplier = PackagingSupplier(
                id=pkg_supplier_data['id'],
                packaging_id=pkg_supplier_data['packaging_id'],
                supplier_id=pkg_supplier_data['supplier_id'],
                price_per_package=pkg_supplier_data['price_per_package'],
                is_primary=pkg_supplier_data.get('is_primary', False),
                sku=pkg_supplier_data.get('sku')
            )
            db.session.add(pkg_supplier)

        # Restore ProductComponents
        for prod_data in data.get('products', []):
            for comp_data in prod_data.get('components', []):
                comp = ProductComponent(
                    product_id=prod_data['id'],
                    component_type=comp_data['component_type'],
                    component_id=comp_data['component_id'],
                    quantity=comp_data['quantity']
                )
                db.session.add(comp)

        for prod_log_data in data.get('production_logs', []):
            prod_log = ProductionLog(
                id=prod_log_data['id'],
                product_id=prod_log_data['product_id'],
                quantity_produced=prod_log_data['quantity_produced'],
                timestamp=datetime.fromisoformat(prod_log_data['timestamp']) if prod_log_data.get('timestamp') else datetime.utcnow(),
                is_carryover=prod_log_data.get('is_carryover', False),
                total_cost=prod_log_data.get('total_cost'),
                cost_per_unit=prod_log_data.get('cost_per_unit'),
                cost_details=prod_log_data.get('cost_details')
            )
            db.session.add(prod_log)

        db.session.flush()

        # Restore Level 3 - Weekly Labor Costs & Dependencies
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
                        quantity_sold=s_data['quantity_sold'],
                        quantity_waste=s_data.get('quantity_waste', 0)
                    )
                    db.session.add(sale)

        # Restore StockLog entries
        for stock_log_data in data.get('stock_logs', []):
            stock_log = StockLog(
                id=stock_log_data['id'],
                raw_material_id=stock_log_data.get('raw_material_id'),
                product_id=stock_log_data.get('product_id'),
                packaging_id=stock_log_data.get('packaging_id'),
                supplier_id=stock_log_data.get('supplier_id'),
                action_type=stock_log_data['action_type'],
                quantity=stock_log_data['quantity'],
                timestamp=datetime.fromisoformat(stock_log_data['timestamp']) if stock_log_data.get('timestamp') else datetime.utcnow()
            )
            db.session.add(stock_log)

        db.session.flush()

        # Restore Level 4 - StockAudit (depends on StockLog)
        for audit_data in data.get('stock_audits', []):
            audit = StockAudit(
                id=audit_data['id'],
                audit_date=datetime.fromisoformat(audit_data['audit_date']) if audit_data.get('audit_date') else datetime.utcnow(),
                raw_material_id=audit_data.get('raw_material_id'),
                product_id=audit_data.get('product_id'),
                packaging_id=audit_data.get('packaging_id'),
                system_quantity=audit_data['system_quantity'],
                physical_quantity=audit_data['physical_quantity'],
                variance=audit_data['variance'],
                variance_cost=audit_data['variance_cost'],
                auditor_name=audit_data.get('auditor_name'),
                notes=audit_data.get('notes'),
                stock_log_id=audit_data.get('stock_log_id')
            )
            db.session.add(audit)

        db.session.commit()

        # Log successful restore
        total_records = data.get('statistics', {}).get('total_records', 'unknown')
        log_audit("RESTORE", "System", details=f"Database restored from backup v{version} with {total_records} records")

        return redirect(url_for('main.index'))
        
    except Exception as e:
        db.session.rollback()
        log_audit("RESTORE_ERROR", "System", details=f"Restore failed: {str(e)}")
        return f"Restore failed: {e}", 500


def restore_legacy_backup(data):
    """Handle v1.0 backup format for backward compatibility"""
    try:
        # Clear existing data
        db.drop_all()
        db.create_all()

        # 1. Categories (v1.0 only had id and name)
        for cat_data in data.get('categories', []):
            c = Category(
                id=cat_data['id'],
                name=cat_data['name'],
                type='raw_material'  # Default type for legacy
            )
            db.session.add(c)
        db.session.flush()

        # 2. Labor
        for l_data in data.get('labor', []):
            l = Labor(
                id=l_data['id'],
                name=l_data['name'],
                phone_number=l_data.get('phone_number'),
                base_hourly_rate=l_data.get('base_hourly_rate', l_data.get('total_hourly_rate', 0)),
                additional_hourly_rate=l_data.get('additional_hourly_rate', 0)
            )
            db.session.add(l)
        db.session.flush()

        # 3. Packaging (v1.0 had price_per_package in model, now in supplier links)
        for p_data in data.get('packaging', []):
            p = Packaging(
                id=p_data['id'],
                name=p_data['name'],
                quantity_per_package=p_data['quantity_per_package']
            )
            db.session.add(p)
        db.session.flush()

        # 4. Raw Materials (v1.0 didn't have is_unlimited or is_deleted)
        for m_data in data.get('raw_materials', []):
            cat_id = None
            if m_data.get('category'):
                cat_id = m_data['category']['id']
            elif m_data.get('category_id'):
                cat_id = m_data['category_id']

            m = RawMaterial(
                id=m_data['id'],
                name=m_data['name'],
                category_id=cat_id,
                unit=m_data['unit'],
                is_unlimited=False,  # Default for legacy
                is_deleted=False     # Default for legacy
            )
            db.session.add(m)
        db.session.flush()

        # 5. Products & Components
        for p_data in data.get('products', []):
            p = Product(
                id=p_data['id'],
                name=p_data['name'],
                products_per_recipe=p_data['products_per_recipe'],
                selling_price_per_unit=p_data.get('selling_price_per_unit'),
                image_filename=p_data.get('image_filename'),
                is_product=p_data.get('is_product', True),
                is_premake=p_data.get('is_premake', False),
                is_preproduct=p_data.get('is_preproduct', False),
                batch_size=p_data.get('batch_size'),
                unit=p_data.get('unit'),
                is_migrated=p_data.get('is_migrated', False),
                migrated_to_premake_id=p_data.get('migrated_to_premake_id'),
                original_prime_cost=p_data.get('original_prime_cost')
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
                        quantity_sold=s_data['quantity_sold'],
                        quantity_waste=s_data.get('quantity_waste', 0)
                    )
                    db.session.add(sale)

        db.session.commit()
        log_audit("RESTORE", "System", details="Database restored from legacy v1.0 backup")
        return redirect(url_for('main.index'))

    except Exception as e:
        db.session.rollback()
        log_audit("RESTORE_ERROR", "System", details=f"Legacy restore failed: {str(e)}")
        return f"Legacy restore failed: {e}", 500


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


@admin_blueprint.route('/migrate_packaging_stock_units', methods=['GET', 'POST'])
def migrate_packaging_stock_units():
    """
    Migration endpoint to fix existing packaging stock data.
    Multiplies historical stock quantities by quantity_per_package.
    This is needed because the old system stored container counts instead of unit counts.

    IMPORTANT: Remove this endpoint after successful migration to prevent accidental re-runs.
    """
    from ..models import StockLog, StockAudit

    if request.method == 'GET':
        # Show migration info page
        return '''
        <html>
        <head>
            <title>Migrate Packaging Stock Units</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                .warning { color: red; font-weight: bold; }
                .info { background: #f0f0f0; padding: 10px; margin: 10px 0; }
                button { padding: 10px 20px; font-size: 16px; }
            </style>
        </head>
        <body>
            <h1>Packaging Stock Units Migration</h1>
            <div class="info">
                <h2>What this migration does:</h2>
                <ul>
                    <li>Converts packaging stock from container counts to unit counts</li>
                    <li>Multiplies all existing packaging StockLog quantities by quantity_per_package</li>
                    <li>Updates StockAudit records to reflect correct unit counts</li>
                    <li>This is a one-time migration for the container quantity feature</li>
                </ul>
            </div>
            <div class="warning">
                ⚠️ WARNING: This migration should only be run ONCE!<br>
                Running it multiple times will corrupt the data.
            </div>
            <form method="POST" onsubmit="return confirm('Are you sure you want to run this migration? This action cannot be undone.');">
                <button type="submit">Run Migration</button>
            </form>
        </body>
        </html>
        '''

    # POST - Run the migration
    try:
        # Get all packaging stock logs
        packaging_logs = StockLog.query.filter(StockLog.packaging_id.isnot(None)).all()

        migrated_count = 0
        for log in packaging_logs:
            if log.packaging and log.packaging.quantity_per_package > 1:
                # Multiply quantity by quantity_per_package
                old_quantity = log.quantity
                log.quantity = log.quantity * log.packaging.quantity_per_package
                migrated_count += 1

                # Log the change
                log_audit("MIGRATION", "StockLog", log.id,
                         f"Migrated packaging {log.packaging.name}: {old_quantity} containers -> {log.quantity} units")

        # Update stock audits
        packaging_audits = StockAudit.query.filter(StockAudit.packaging_id.isnot(None)).all()

        audit_count = 0
        for audit in packaging_audits:
            if audit.packaging and audit.packaging.quantity_per_package > 1:
                # Multiply quantities by quantity_per_package
                audit.system_quantity = audit.system_quantity * audit.packaging.quantity_per_package
                audit.physical_quantity = audit.physical_quantity * audit.packaging.quantity_per_package
                audit.variance = audit.variance * audit.packaging.quantity_per_package
                audit_count += 1

        db.session.commit()

        log_audit("MIGRATION_COMPLETE", "System", None,
                 f"Packaging units migration completed: {migrated_count} stock logs and {audit_count} audits updated")

        return f'''
        <html>
        <head>
            <title>Migration Complete</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .success {{ color: green; font-weight: bold; }}
            </style>
        </head>
        <body>
            <h1 class="success">Migration Completed Successfully!</h1>
            <p>Migrated {migrated_count} stock log entries and {audit_count} audit entries.</p>
            <p>Packaging stock now correctly reflects unit counts instead of container counts.</p>
            <a href="/">Return to Dashboard</a>
        </body>
        </html>
        '''

    except Exception as e:
        db.session.rollback()
        log_audit("MIGRATION_ERROR", "System", None, f"Packaging units migration failed: {str(e)}")
        return f"Migration failed: {e}", 500




