import json
import io
from datetime import datetime
from flask import Blueprint, request, send_file, redirect, url_for, render_template, jsonify, flash
from flask_babel import gettext as _
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


@admin_blueprint.route('/admin/restore', methods=['GET', 'POST'])
def restore_db():
    if request.method == 'GET':
        return render_template('restore.html')

    file = request.files['file']
    if not file:
        return "No file uploaded", 400

    try:
        file_contents = file.read()

        # Try UTF-8 with BOM handling first
        if file_contents.startswith(b'\xef\xbb\xbf'):
            json_str = file_contents[3:].decode('utf-8')
        else:
            try:
                json_str = file_contents.decode('utf-8')
            except UnicodeDecodeError:
                # Fallback to UTF-8 with ignore
                json_str = file_contents.decode('utf-8', errors='ignore')

        # Attempt to parse JSON data
        data = json.loads(json_str)

    except Exception as e:
        log_audit("RESTORE_ERROR", "System", details=f"Failed to parse backup file: {str(e)}")
        return f"Invalid backup file: {str(e)}", 400

    try:
        # Check version
        version = data.get('version', '1.0')
        if version not in ['1.0', '2.0']:
            return f"Unsupported backup version: {version}", 400

        # Clear tables if requested
        clear_existing = request.form.get('clear_existing') == 'on'

        if clear_existing:
            # Delete in reverse dependency order
            AuditLog.query.delete()
            WeeklyLaborEntry.query.delete()
            WeeklyProductSales.query.delete()
            WeeklyLaborCost.query.delete()
            ProductComponent.query.delete()
            ProductionLog.query.delete()
            StockAudit.query.delete()
            StockLog.query.delete()
            PackagingSupplier.query.delete()
            RawMaterialSupplier.query.delete()
            RawMaterialAlternativeName.query.delete()
            Product.query.delete()
            Packaging.query.delete()
            RawMaterial.query.delete()
            Labor.query.delete()
            Supplier.query.delete()
            Category.query.delete()
            db.session.commit()

        # Helper to safely restore with deduplication
        def restore_items(items, model, unique_key='id'):
            count = 0
            for item_data in items:
                # Skip if exists (based on unique_key)
                if hasattr(model, unique_key):
                    existing = model.query.filter_by(**{unique_key: item_data.get(unique_key)}).first()
                    if existing and not clear_existing:
                        continue

                try:
                    # Create new item with all fields
                    new_item = model(**item_data)
                    db.session.add(new_item)
                    count += 1
                except Exception as e:
                    print(f"Failed to restore {model.__name__}: {e}")
                    continue
            return count

        restored_counts = {}

        # Restore in dependency order
        # Level 0 - No dependencies
        restored_counts['categories'] = restore_items(data.get('categories', []), Category)
        restored_counts['labor'] = restore_items(data.get('labor', []), Labor)
        restored_counts['suppliers'] = restore_items(data.get('suppliers', []), Supplier)

        # Level 1 - Basic dependencies
        restored_counts['raw_materials'] = restore_items(data.get('raw_materials', []), RawMaterial)
        restored_counts['packaging'] = restore_items(data.get('packaging', []), Packaging)
        restored_counts['products'] = restore_items(data.get('products', []), Product)

        # Level 2 - Secondary dependencies
        restored_counts['raw_material_alternative_names'] = restore_items(
            data.get('raw_material_alternative_names', []), RawMaterialAlternativeName)
        restored_counts['raw_material_suppliers'] = restore_items(
            data.get('raw_material_suppliers', []), RawMaterialSupplier)
        restored_counts['packaging_suppliers'] = restore_items(
            data.get('packaging_suppliers', []), PackagingSupplier)
        restored_counts['production_logs'] = restore_items(
            data.get('production_logs', []), ProductionLog)
        restored_counts['weekly_labor_costs'] = restore_items(
            data.get('weekly_labor_costs', []), WeeklyLaborCost)

        # Level 3 - Complex dependencies
        restored_counts['stock_logs'] = restore_items(data.get('stock_logs', []), StockLog)
        restored_counts['stock_audits'] = restore_items(data.get('stock_audits', []), StockAudit)

        # Level 4 - Audit logs (last)
        restored_counts['audit_logs'] = restore_items(data.get('audit_logs', []), AuditLog)

        # Handle product components separately (after products exist)
        if 'product_components' in data:
            for comp_data in data['product_components']:
                try:
                    new_comp = ProductComponent(**comp_data)
                    db.session.add(new_comp)
                except:
                    continue

        # Handle weekly entries separately (after weekly costs exist)
        if 'weekly_labor_entries' in data:
            for entry_data in data['weekly_labor_entries']:
                try:
                    new_entry = WeeklyLaborEntry(**entry_data)
                    db.session.add(new_entry)
                except:
                    continue

        if 'weekly_product_sales' in data:
            for sale_data in data['weekly_product_sales']:
                try:
                    new_sale = WeeklyProductSales(**sale_data)
                    db.session.add(new_sale)
                except:
                    continue

        db.session.commit()

        total_restored = sum(restored_counts.values())
        log_audit("RESTORE", "System", details=f"Restored from backup v{version}: {total_restored} records")

        return render_template('restore_success.html', counts=restored_counts, version=version)

    except Exception as e:
        db.session.rollback()
        log_audit("RESTORE_ERROR", "System", details=f"Restore failed: {str(e)}")
        return f"Restore failed: {str(e)}", 500


@admin_blueprint.route('/audit_log')
def audit_log():
    """Display audit log with optional filtering"""
    page = request.args.get('page', 1, type=int)
    per_page = 100  # Number of items per page

    # Optional filters
    action_filter = request.args.get('action')
    date_filter = request.args.get('date')

    # Build query
    query = AuditLog.query

    if action_filter:
        query = query.filter(AuditLog.action == action_filter)

    if date_filter:
        try:
            filter_date = datetime.strptime(date_filter, '%Y-%m-%d')
            next_day = filter_date + timedelta(days=1)
            query = query.filter(AuditLog.timestamp >= filter_date,
                                AuditLog.timestamp < next_day)
        except:
            pass  # Invalid date format, ignore filter

    # Order by timestamp descending (newest first)
    query = query.order_by(AuditLog.timestamp.desc())

    # Paginate results
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    logs = pagination.items

    # Get unique actions for filter dropdown
    unique_actions = db.session.query(AuditLog.action).distinct().all()
    unique_actions = [a[0] for a in unique_actions]

    return render_template('audit_log.html',
                         logs=logs,
                         pagination=pagination,
                         unique_actions=unique_actions,
                         current_filters={
                             'action': action_filter,
                             'date': date_filter
                         })

from datetime import timedelta

@admin_blueprint.route('/migrate_add_raw_material_is_deleted', methods=['GET', 'POST'])
def migrate_add_raw_material_is_deleted():
    """
    Migration to add is_deleted column to raw_material table for soft deletes.
    This is a one-time migration for production database.
    """
    if request.method == 'GET':
        # Show migration information
        return '''
        <html>
        <head>
            <title>Migration: Add is_deleted to raw_material</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                .warning { color: red; font-weight: bold; }
                .info { background: #f0f0f0; padding: 10px; margin: 10px 0; }
            </style>
        </head>
        <body>
            <h1>Migration: Add Soft Delete Support for Raw Materials</h1>

            <div class="info">
                <h2>What this migration does:</h2>
                <ul>
                    <li>Adds 'is_deleted' column to raw_material table</li>
                    <li>Sets default value to FALSE for all existing materials</li>
                    <li>Enables soft delete functionality for materials with historical data</li>
                </ul>
            </div>

            <div class="warning">
                ⚠️ WARNING: This will modify the production database structure!
            </div>

            <p>This migration is safe and non-destructive. It only adds a new column.</p>

            <form method="POST" onsubmit="return confirm('Are you sure you want to run this migration on the production database?');">
                <button type="submit" name="confirm" value="yes">Run Migration</button>
                <a href="/"><button type="button">Cancel</button></a>
            </form>
        </body>
        </html>
        '''

    # POST - Execute the migration
    if request.form.get('confirm') != 'yes':
        return "Migration cancelled", 400

    try:
        # PostgreSQL compatible ALTER TABLE
        db.session.execute(text('''
            ALTER TABLE raw_material
            ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE
        '''))

        # Update any NULL values to FALSE (safety measure)
        db.session.execute(text('''
            UPDATE raw_material
            SET is_deleted = FALSE
            WHERE is_deleted IS NULL
        '''))

        db.session.commit()

        log_audit("MIGRATION", "System", None,
                 "Added is_deleted column to raw_material table for soft delete support")

        return '''
        <html>
        <head>
            <title>Migration Complete</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                .success { color: green; font-weight: bold; }
            </style>
        </head>
        <body>
            <h1 class="success">✓ Migration Completed Successfully!</h1>
            <p>The is_deleted column has been added to the raw_material table.</p>
            <p>Raw materials can now be soft-deleted to preserve historical data.</p>
            <br>
            <p><strong>Next steps:</strong></p>
            <ul>
                <li>The application will now use soft deletes for materials with history</li>
                <li>Hard deletes will only occur for materials with no historical data</li>
                <li>You can remove this migration endpoint from the code</li>
            </ul>
            <br>
            <a href="/">Return to Dashboard</a>
        </body>
        </html>
        '''

    except Exception as e:
        db.session.rollback()
        log_audit("MIGRATION_ERROR", "System", None,
                 f"Failed to add is_deleted column: {str(e)}")
        return f"Migration failed: {e}", 500


@admin_blueprint.route('/migrate_packaging_multi_supplier', methods=['GET', 'POST'])
def migrate_packaging_multi_supplier():
    """
    Migration to enable multi-supplier support for packaging materials.
    Creates PackagingSupplier records from existing single-supplier data.
    """
    if request.method == 'GET':
        # Analyze current state
        packaging_count = Packaging.query.count()
        suppliers_count = Supplier.query.count()

        # Count how many packaging items have price data (will need migration)
        packaging_with_price = Packaging.query.filter(
            (Packaging.price_per_package != None) & (Packaging.price_per_package > 0)
        ).count()

        return f'''
        <html>
        <head>
            <title>Migration: Packaging Multi-Supplier Support</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .warning {{ color: red; font-weight: bold; }}
                .info {{ background: #f0f0f0; padding: 15px; margin: 20px 0; }}
                .stats {{ background: #e8f4f8; padding: 15px; margin: 20px 0; }}
            </style>
        </head>
        <body>
            <h1>Migration: Enable Multi-Supplier for Packaging</h1>

            <div class="stats">
                <h2>Current Database Status:</h2>
                <ul>
                    <li>Total packaging items: {packaging_count}</li>
                    <li>Packaging with prices: {packaging_with_price}</li>
                    <li>Available suppliers: {suppliers_count}</li>
                </ul>
            </div>

            <div class="info">
                <h2>What this migration does:</h2>
                <ol>
                    <li>Creates PackagingSupplier records for existing packaging prices</li>
                    <li>Links packaging to the 'General Supplier' (or creates it)</li>
                    <li>Migrates existing price_per_package values</li>
                    <li>Preserves all existing stock and pricing data</li>
                </ol>
            </div>

            <div class="warning">
                ⚠️ WARNING: This migration modifies production data!<br>
                ⚠️ Make sure you have a recent backup before proceeding.
            </div>

            <form method="POST" onsubmit="return confirm('Have you backed up the database? This will create {packaging_with_price} new supplier links.');">
                <input type="hidden" name="confirm" value="yes">
                <button type="submit" style="background: #28a745; color: white; padding: 10px 20px; font-size: 16px;">
                    Run Migration
                </button>
                <a href="/"><button type="button" style="padding: 10px 20px; font-size: 16px;">Cancel</button></a>
            </form>
        </body>
        </html>
        '''

    # POST - Execute the migration
    if request.form.get('confirm') != 'yes':
        return "Migration cancelled", 400

    try:
        # Get or create a general supplier for legacy data
        general_supplier = Supplier.query.filter_by(name='General Supplier').first()
        if not general_supplier:
            general_supplier = Supplier(
                name='General Supplier',
                contact_person='System',
                phone='N/A',
                email='system@costflow.local',
                address='Legacy Data Migration',
                is_active=True
            )
            db.session.add(general_supplier)
            db.session.flush()  # Get the ID

        # Track migration stats
        created_count = 0
        skipped_count = 0
        errors = []

        # Migrate all packaging items with prices
        all_packaging = Packaging.query.all()

        for packaging in all_packaging:
            # Skip if no price data
            if not packaging.price_per_package or packaging.price_per_package <= 0:
                skipped_count += 1
                continue

            # Check if supplier link already exists
            existing_link = PackagingSupplier.query.filter_by(
                packaging_id=packaging.id,
                supplier_id=general_supplier.id
            ).first()

            if existing_link:
                skipped_count += 1
                continue

            try:
                # Create new supplier link
                supplier_link = PackagingSupplier(
                    packaging_id=packaging.id,
                    supplier_id=general_supplier.id,
                    price_per_package=packaging.price_per_package,
                    sku=None,  # No SKU for legacy data
                    is_primary=True,  # Set as primary since it's the only one
                    notes='Migrated from single-supplier system'
                )
                db.session.add(supplier_link)
                created_count += 1

                # Log the migration
                log_audit(
                    "PACKAGING_MIGRATION",
                    "Packaging",
                    packaging.id,
                    f"Created supplier link: {packaging.name} -> General Supplier @ {packaging.price_per_package}"
                )

            except Exception as e:
                errors.append(f"Failed to migrate {packaging.name}: {str(e)}")

        # Commit all changes
        db.session.commit()

        # Final summary
        log_audit(
            "MIGRATION_COMPLETE",
            "System",
            None,
            f"Packaging multi-supplier migration: {created_count} created, {skipped_count} skipped, {len(errors)} errors"
        )

        return f'''
        <html>
        <head>
            <title>Migration Complete</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .success {{ color: green; font-weight: bold; }}
                .warning {{ color: orange; }}
                .error {{ color: red; }}
                .summary {{ background: #f0f8ff; padding: 15px; margin: 20px 0; border-radius: 5px; }}
            </style>
        </head>
        <body>
            <h1 class="success">✓ Migration Completed Successfully!</h1>

            <div class="summary">
                <h2>Migration Summary:</h2>
                <ul>
                    <li>✓ Created {created_count} new supplier links</li>
                    <li>➤ Skipped {skipped_count} items (no price or already migrated)</li>
                    <li>{"✗ " + str(len(errors)) + " errors" if errors else "✓ No errors"}</li>
                </ul>
            </div>

            {"<div class='error'><h3>Errors:</h3><ul>" + "".join([f"<li>{e}</li>" for e in errors]) + "</ul></div>" if errors else ""}

            <div style="margin-top: 30px;">
                <h3>Next Steps:</h3>
                <ol>
                    <li>Verify packaging costs are still correct</li>
                    <li>You can now add multiple suppliers for each packaging item</li>
                    <li>Update supplier information from "General Supplier" to actual suppliers</li>
                    <li>Remove this migration endpoint after verification</li>
                </ol>
            </div>

            <a href="/packaging">View Packaging</a> |
            <a href="/suppliers">View Suppliers</a> |
            <a href="/">Return to Dashboard</a>
        </body>
        </html>
        '''

    except Exception as e:
        db.session.rollback()
        log_audit("MIGRATION_ERROR", "System", None, f"Packaging migration failed: {str(e)}")
        return f'''
        <html>
        <head>
            <title>Migration Failed</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .error {{ color: red; font-weight: bold; }}
            </style>
        </head>
        <body>
            <h1 class="error">✗ Migration Failed</h1>
            <p>Error: {str(e)}</p>
            <p>The database has been rolled back. No changes were made.</p>
            <a href="/">Return to Dashboard</a>
        </body>
        </html>
        ''', 500


@admin_blueprint.route('/migrate_reset_postgres_sequences', methods=['GET', 'POST'])
def migrate_reset_postgres_sequences():
    """
    Reset PostgreSQL sequences to max(id) + 1 for all tables.
    This fixes duplicate key errors after restoring from backup.
    """
    if request.method == 'GET':
        # Check if we're using PostgreSQL
        is_postgres = 'postgresql' in str(db.engine.url)

        if not is_postgres:
            return '''
            <html>
            <body style="font-family: Arial; margin: 40px;">
                <h2>This migration is only for PostgreSQL databases</h2>
                <p>Your current database is not PostgreSQL.</p>
                <a href="/">Return to Dashboard</a>
            </body>
            </html>
            '''

        return '''
        <html>
        <head>
            <title>Reset PostgreSQL Sequences</title>
            <style>
                body { font-family: Arial, sans-serif; margin: 40px; }
                .warning { color: red; font-weight: bold; }
                .info { background: #f0f0f0; padding: 15px; margin: 20px 0; }
            </style>
        </head>
        <body>
            <h1>Reset PostgreSQL Sequences</h1>

            <div class="info">
                <h2>What this migration does:</h2>
                <ul>
                    <li>Resets all table sequences to max(id) + 1</li>
                    <li>Fixes "duplicate key value violates unique constraint" errors</li>
                    <li>Safe to run multiple times (idempotent)</li>
                </ul>
            </div>

            <div class="warning">
                ⚠️ This is typically needed after restoring from a backup
            </div>

            <form method="POST" onsubmit="return confirm('Reset all PostgreSQL sequences?');">
                <input type="hidden" name="confirm" value="yes">
                <button type="submit" style="background: #28a745; color: white; padding: 10px 20px;">
                    Reset Sequences
                </button>
                <a href="/"><button type="button" style="padding: 10px 20px;">Cancel</button></a>
            </form>
        </body>
        </html>
        '''

    # POST - Execute the migration
    if request.form.get('confirm') != 'yes':
        return "Operation cancelled", 400

    try:
        # Tables with sequences (id columns)
        tables_with_sequences = [
            ('category', 'category_id_seq'),
            ('labor', 'labor_id_seq'),
            ('supplier', 'supplier_id_seq'),
            ('raw_material', 'raw_material_id_seq'),
            ('raw_material_supplier', 'raw_material_supplier_id_seq'),
            ('raw_material_alternative_name', 'raw_material_alternative_name_id_seq'),
            ('packaging', 'packaging_id_seq'),
            ('packaging_supplier', 'packaging_supplier_id_seq'),
            ('product', 'product_id_seq'),
            ('product_component', 'product_component_id_seq'),
            ('production_log', 'production_log_id_seq'),
            ('stock_log', 'stock_log_id_seq'),
            ('stock_audit', 'stock_audit_id_seq'),
            ('weekly_labor_cost', 'weekly_labor_cost_id_seq'),
            ('weekly_labor_entry', 'weekly_labor_entry_id_seq'),
            ('weekly_product_sales', 'weekly_product_sales_id_seq'),
            ('audit_log', 'audit_log_id_seq')
        ]

        results = []
        reset_count = 0

        for table_name, sequence_name in tables_with_sequences:
            try:
                # Get max id from table
                result = db.session.execute(text(f"SELECT COALESCE(MAX(id), 0) FROM {table_name}"))
                max_id = result.scalar()

                # Reset sequence to max_id + 1
                db.session.execute(text(f"SELECT setval('{sequence_name}', :max_id, true)"), {'max_id': max_id})

                results.append(f"✓ {table_name}: sequence set to {max_id + 1}")
                reset_count += 1

            except Exception as e:
                results.append(f"{table_name}: {str(e)}")

        db.session.commit()

        log_audit("MIGRATION_COMPLETE", "System", None,
                 f"PostgreSQL sequences reset: {reset_count} sequences updated")

        return f'''
        <html>
        <head>
            <title>Sequences Reset Complete</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .success {{ color: green; font-weight: bold; }}
                .results {{ background: #f0f0f0; padding: 10px; margin: 10px 0; }}
                pre {{ white-space: pre-wrap; }}
            </style>
        </head>
        <body>
            <h1 class="success">Sequences Reset Successfully!</h1>
            <p>Reset {reset_count} sequences.</p>
            <div class="results">
                <h3>Results:</h3>
                <pre>{chr(10).join(results)}</pre>
            </div>
            <p>You should no longer get duplicate key errors.</p>
            <a href="/">Return to Dashboard</a>
        </body>
        </html>
        '''

    except Exception as e:
        db.session.rollback()
        log_audit("MIGRATION_ERROR", "System", None, f"Sequence reset failed: {str(e)}")
        return f"Migration failed: {e}", 500

@admin_blueprint.route('/migrate_fix_preproduct_units')
def migrate_fix_preproduct_units():
    """Fix preproduct units from kg/g to unit"""
    try:
        # Find all preproducts
        preproducts = Product.query.filter_by(is_preproduct=True).all()

        updated_count = 0
        update_details = []

        for preproduct in preproducts:
            old_unit = preproduct.unit

            # Preproducts should typically be in 'unit' not kg/g
            if preproduct.unit in ['kg', 'g']:
                preproduct.unit = 'unit'
                updated_count += 1
                update_details.append(f"'{preproduct.name}': {old_unit} → unit")
            else:
                update_details.append(f"'{preproduct.name}' already has unit: {preproduct.unit}")

            # Ensure products_per_recipe is set
            if not preproduct.products_per_recipe or preproduct.products_per_recipe == 0:
                preproduct.products_per_recipe = 1
                update_details.append(f"  - Set products_per_recipe to 1 for '{preproduct.name}'")

        db.session.commit()

        log_audit("MIGRATION", "System", None,
                 f"Fixed preproduct units: Updated {updated_count} of {len(preproducts)} preproducts")

        return f'''
        <html>
        <head>
            <title>Preproduct Units Migration Complete</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .success {{ color: green; font-weight: bold; }}
                .info {{ background-color: #f0f8ff; padding: 10px; border-left: 3px solid #4CAF50; }}
                .details {{ background: #f5f5f5; padding: 10px; margin: 10px 0; }}
                pre {{ white-space: pre-wrap; }}
            </style>
        </head>
        <body>
            <h1 class="success">✓ Migration Completed Successfully!</h1>
            <div class="info">
                <p>Found {len(preproducts)} preproducts in the system.</p>
                <p>Updated {updated_count} preproducts from kg/g to 'unit'.</p>
            </div>

            <div class="details">
                <h3>Migration Details:</h3>
                <pre>{chr(10).join(update_details)}</pre>
            </div>

            <p><strong>What changed:</strong></p>
            <ul>
                <li>Preproducts with unit 'kg' or 'g' have been changed to 'unit'</li>
                <li>This means preproducts are now counted as pieces/units instead of weight</li>
                <li>Any preproduct with missing products_per_recipe was set to 1</li>
            </ul>

            <p><strong>Next steps:</strong></p>
            <ul>
                <li>Verify that preproduct quantities in recipes are correct (should be number of units)</li>
                <li>You can remove this migration endpoint from the code</li>
            </ul>
            <br>
            <a href="/">Return to Dashboard</a>
        </body>
        </html>
        '''

    except Exception as e:
        db.session.rollback()
        log_audit("MIGRATION_ERROR", "System", None,
                 f"Failed to fix preproduct units: {str(e)}")
        return f"Migration failed: {e}", 500


@admin_blueprint.route('/migrate_product_type_system', methods=['GET', 'POST'])
def migrate_product_type_system():
    """
    Migrate from old boolean flags (is_product, is_premake, is_preproduct)
    to new product type system (product_type enum and is_for_sale boolean)
    """

    if request.method == 'GET':
        # First check if columns exist to avoid SQLAlchemy errors
        inspector = db.inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('product')]

        needs_migration = 'product_type' not in columns or 'is_for_sale' not in columns

        # Show migration status and confirmation page
        if needs_migration:
            # Use raw SQL to avoid SQLAlchemy trying to select non-existent columns
            result = db.session.execute(text('''
                SELECT id, name, is_product, is_premake, is_preproduct
                FROM product
            '''))
            products = result.fetchall()

            stats = {
                'total': len(products),
                'products': 0,
                'premakes': 0,
                'preproducts': 0,
                'already_migrated': 0
            }

            for row in products:
                if row.is_premake:
                    stats['premakes'] += 1
                elif row.is_preproduct:
                    stats['preproducts'] += 1
                else:
                    stats['products'] += 1
        else:
            # Columns exist, check if data is already migrated
            result = db.session.execute(text('''
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN product_type IS NOT NULL THEN 1 END) as migrated,
                    COUNT(CASE WHEN product_type IS NULL AND is_premake = TRUE THEN 1 END) as premakes,
                    COUNT(CASE WHEN product_type IS NULL AND is_preproduct = TRUE THEN 1 END) as preproducts,
                    COUNT(CASE WHEN product_type IS NULL AND is_premake = FALSE AND is_preproduct = FALSE THEN 1 END) as products
                FROM product
            '''))
            row = result.fetchone()

            stats = {
                'total': row.total,
                'products': row.products,
                'premakes': row.premakes,
                'preproducts': row.preproducts,
                'already_migrated': row.migrated
            }

        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>{_('Product Type System Migration')}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .stats {{ background: #f0f0f0; padding: 15px; margin: 20px 0; border-radius: 5px; }}
                .warning {{ background: #fff3cd; padding: 10px; border-left: 5px solid #ffc107; margin: 20px 0; }}
                button {{ background: #007bff; color: white; padding: 10px 20px; border: none; cursor: pointer; }}
                button:hover {{ background: #0056b3; }}
            </style>
        </head>
        <body>
            <h1>{_('Product Type System Migration')}</h1>

            <div class="stats">
                <h3>{_('Current Database Status:')}</h3>
                <ul>
                    <li>{_('Total items')}: {stats['total']}</li>
                    <li>{_('Regular products')}: {stats['products']}</li>
                    <li>{_('Premakes')}: {stats['premakes']}</li>
                    <li>{_('Preproducts')}: {stats['preproducts']}</li>
                    <li>{_('Already migrated')}: {stats['already_migrated']}</li>
                </ul>
            </div>

            <div class="warning">
                <strong>{_('What will happen:')}</strong>
                <ul>
                    <li>{_('Products will be set to')} product_type='product', is_for_sale=True</li>
                    <li>{_('Premakes will be set to')} product_type='premake', is_for_sale=False</li>
                    <li>{_('Preproducts will be set to')} product_type='preproduct', is_for_sale=True {_('(you can change later)')}</li>
                    <li>{_('New columns product_type and is_for_sale will be added if not present')}</li>
                    <li>{_('Old boolean fields will be kept for backward compatibility')}</li>
                </ul>
            </div>

            <form method="POST">
                <button type="submit">{_('Run Migration')}</button>
                <a href="/" style="margin-left: 20px;">{_('Cancel')}</a>
            </form>
        </body>
        </html>
        '''

    # POST - Execute migration
    try:
        # First, check if columns exist and add them if not
        # Check if product_type column exists
        inspector = db.inspect(db.engine)
        columns = [col['name'] for col in inspector.get_columns('product')]

        if 'product_type' not in columns:
            # Add product_type column
            db.session.execute(text('''
                ALTER TABLE product
                ADD COLUMN product_type VARCHAR(20)
            '''))
            db.session.commit()
            log_audit("MIGRATION", "System", None, "Added product_type column to Product table")

        if 'is_for_sale' not in columns:
            # Add is_for_sale column
            db.session.execute(text('''
                ALTER TABLE product
                ADD COLUMN is_for_sale BOOLEAN
            '''))
            db.session.commit()
            log_audit("MIGRATION", "System", None, "Added is_for_sale column to Product table")

        # Now migrate the data using raw SQL to avoid ORM issues
        # Count items to be migrated
        result = db.session.execute(text('''
            SELECT COUNT(*) as count
            FROM product
            WHERE product_type IS NULL
        '''))
        to_migrate = result.fetchone().count

        # Migrate premakes
        result = db.session.execute(text('''
            UPDATE product
            SET product_type = 'premake', is_for_sale = FALSE
            WHERE is_premake = TRUE AND product_type IS NULL
        '''))

        # Migrate preproducts (default to for_sale=True, can be changed later)
        result = db.session.execute(text('''
            UPDATE product
            SET product_type = 'preproduct', is_for_sale = TRUE
            WHERE is_preproduct = TRUE AND product_type IS NULL
        '''))

        # Migrate regular products
        result = db.session.execute(text('''
            UPDATE product
            SET product_type = 'product', is_for_sale = TRUE
            WHERE is_premake = FALSE AND is_preproduct = FALSE AND product_type IS NULL
        '''))

        db.session.commit()

        # Count items that were already migrated
        result = db.session.execute(text('''
            SELECT COUNT(*) as count
            FROM product
            WHERE product_type IS NOT NULL
        '''))
        total_migrated = result.fetchone().count

        migrated_count = to_migrate
        skipped_count = total_migrated - to_migrate

        log_audit("MIGRATION_SUCCESS", "System", None,
                 f"Successfully migrated {migrated_count} products to new type system")

        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>{_('Migration Complete')}</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 20px; }}
                .success {{ background: #d4edda; padding: 15px; border-left: 5px solid #28a745; margin: 20px 0; }}
            </style>
        </head>
        <body>
            <h1>{_('Migration Complete')}</h1>

            <div class="success">
                <h3>{_('Success!')}</h3>
                <p>{_('Migrated')} {migrated_count} {_('items to new product type system')}</p>
                <p>{_('Skipped')} {skipped_count} {_('items (already migrated)')}</p>
            </div>

            <h3>{_('Next Steps:')}</h3>
            <ul>
                <li>{_('Review preproducts and mark which ones are not for sale')}</li>
                <li>{_('Test product creation and editing with new system')}</li>
                <li>{_('After confirming everything works, this migration endpoint can be removed')}</li>
            </ul>

            <a href="/">{_('Return to Dashboard')}</a>
        </body>
        </html>
        '''

    except Exception as e:
        db.session.rollback()
        log_audit("MIGRATION_ERROR", "System", None,
                 f"Failed to migrate product type system: {str(e)}")
        return f"Migration failed: {e}", 500


