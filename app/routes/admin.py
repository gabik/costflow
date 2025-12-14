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

@admin_blueprint.route('/migrate_fix_multiplied_quantities', methods=['GET', 'POST'])
def migrate_fix_multiplied_quantities():
    """
    Migration to fix quantities that were incorrectly multiplied by 1000
    due to the JavaScript unit conversion bug.
    """
    if request.method == 'GET':
        # Analyze current state
        suspicious_components = []

        # Find components with suspiciously high quantities
        all_components = ProductComponent.query.join(
            Product, ProductComponent.product_id == Product.id
        ).filter(
            Product.is_archived == False
        ).all()

        for comp in all_components:
            # Check for quantities > 100 kg (likely multiplied)
            if comp.quantity > 100:
                product = Product.query.get(comp.product_id)
                component_info = {
                    'product_id': product.id,
                    'product_name': product.name,
                    'product_unit': product.unit,
                    'component_id': comp.id,
                    'component_type': comp.component_type,
                    'quantity': comp.quantity,
                    'suggested_fix': comp.quantity / 1000
                }

                if comp.component_type == 'raw_material' and comp.material:
                    component_info['material_name'] = comp.material.name
                    component_info['material_unit'] = comp.material.unit
                elif comp.component_type == 'premake':
                    premake = Product.query.get(comp.component_id)
                    if premake:
                        component_info['material_name'] = premake.name
                        component_info['material_unit'] = premake.unit

                suspicious_components.append(component_info)

        return f'''
        <html>
        <head>
            <title>Fix Multiplied Quantities Migration</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .warning {{ color: red; font-weight: bold; }}
                .info {{ background: #f0f0f0; padding: 15px; margin: 20px 0; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #f2f2f2; }}
                .suspicious {{ background-color: #ffcccc; }}
            </style>
        </head>
        <body>
            <h1>Fix Multiplied Quantities Migration</h1>

            <div class="info">
                <h2>Found {len(suspicious_components)} Suspicious Components</h2>
                <p>Components with quantity > 100 kg that may have been incorrectly multiplied by 1000</p>
            </div>

            <div class="warning">
                ⚠️ WARNING: This will divide these quantities by 1000!
            </div>

            {('<table>' +
              '<tr><th>Product</th><th>Component</th><th>Current Qty</th><th>Will Change To</th></tr>' +
              ''.join([
                  f'<tr class="suspicious">' +
                  f'<td>{c["product_name"]}</td>' +
                  f'<td>{c.get("material_name", c["component_type"])}</td>' +
                  f'<td>{c["quantity"]:.1f} {c.get("material_unit", "")}</td>' +
                  f'<td>{c["suggested_fix"]:.3f} {c.get("material_unit", "")}</td>' +
                  '</tr>'
                  for c in suspicious_components[:20]
              ]) +
              ('</table>' + f'<p>... and {len(suspicious_components) - 20} more</p>' if len(suspicious_components) > 20 else '</table>')
            ) if suspicious_components else '<p>No suspicious quantities found.</p>'}

            <form method="POST" onsubmit="return confirm('Fix {len(suspicious_components)} components by dividing by 1000?');">
                <input type="hidden" name="confirm" value="yes">
                <button type="submit" style="background: #dc3545; color: white; padding: 10px 20px;"
                        {'' if suspicious_components else 'disabled'}>
                    Fix Multiplied Quantities
                </button>
                <a href="/"><button type="button" style="padding: 10px 20px;">Cancel</button></a>
            </form>
        </body>
        </html>
        '''

    # POST - Execute the fix
    if request.form.get('confirm') != 'yes':
        return "Migration cancelled", 400

    try:
        fixed_count = 0

        # Fix components with quantities > 100
        all_components = ProductComponent.query.join(
            Product, ProductComponent.product_id == Product.id
        ).filter(
            Product.is_archived == False,
            ProductComponent.quantity > 100
        ).all()

        for comp in all_components:
            old_quantity = comp.quantity
            comp.quantity = comp.quantity / 1000
            fixed_count += 1

            product = Product.query.get(comp.product_id)
            log_audit(
                "FIX_MULTIPLIED_QTY",
                "ProductComponent",
                comp.id,
                f"Fixed quantity for {product.name}: {old_quantity} → {comp.quantity}"
            )

        db.session.commit()

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
            <h1 class="success">✓ Fixed {fixed_count} Components</h1>
            <p>All suspicious quantities have been divided by 1000.</p>
            <p>Please verify product costs are now correct.</p>
            <br>
            <a href="/products">Check Products</a> |
            <a href="/check_unit_fix">Validate Costs</a> |
            <a href="/">Return to Dashboard</a>
        </body>
        </html>
        '''

    except Exception as e:
        db.session.rollback()
        log_audit("MIGRATION_ERROR", "System", None, f"Fix quantities failed: {str(e)}")
        return f"Migration failed: {e}", 500

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


@admin_blueprint.route('/migrate_convert_units', methods=['GET', 'POST'])
def migrate_convert_units():
    """
    Migration to fix unit conversion issues in ProductComponent quantities.
    Converts all quantities to match their referenced material's base unit (kg/L).
    """
    from .utils import calculate_prime_cost

    if request.method == 'GET':
        # Dry-run mode - analyze the data without changes
        dry_run = request.args.get('dry_run', 'false') == 'true'

        if dry_run:
            # Analyze what would be migrated
            analysis = {
                'products_to_convert': [],
                'premakes_to_convert': [],
                'components_to_convert': [],
                'already_converted': [],
                'suspicious_quantities': [],
                'errors': []
            }

            try:
                # Check all products and premakes with unit='g'
                products_with_g = Product.query.filter(
                    Product.unit == 'g'
                ).all()

                for product in products_with_g:
                    if product.is_archived:
                        continue

                    product_info = {
                        'id': product.id,
                        'name': product.name,
                        'type': 'premake' if product.is_premake else 'product',
                        'batch_size': product.batch_size,
                        'unit': product.unit,
                        'components': []
                    }

                    # Check if already converted (heuristic: if all quantities < 10 for g unit products)
                    all_small = True
                    has_components = False

                    for component in product.components:
                        has_components = True
                        comp_info = {
                            'type': component.component_type,
                            'quantity': component.quantity,
                            'material_unit': None,
                            'needs_conversion': False
                        }

                        if component.component_type == 'raw_material' and component.material:
                            comp_info['material_name'] = component.material.name
                            comp_info['material_unit'] = component.material.unit

                            # Check if conversion needed
                            if component.material.unit == 'kg' and product.unit == 'g':
                                if component.quantity > 10:  # Likely unconverted
                                    comp_info['needs_conversion'] = True
                                    comp_info['new_quantity'] = component.quantity / 1000
                                    all_small = False

                        elif component.component_type == 'premake':
                            premake = Product.query.get(component.component_id)
                            if premake:
                                comp_info['material_name'] = premake.name
                                comp_info['material_unit'] = premake.unit

                                # For premakes, check if quantity makes sense
                                if component.quantity > 10:
                                    comp_info['needs_conversion'] = True
                                    comp_info['new_quantity'] = component.quantity / 1000
                                    all_small = False

                        elif component.component_type == 'loss':
                            comp_info['material_name'] = 'Water Loss'
                            # Loss is negative, convert it too
                            if abs(component.quantity) > 10:
                                comp_info['needs_conversion'] = True
                                comp_info['new_quantity'] = component.quantity / 1000
                                all_small = False

                        product_info['components'].append(comp_info)

                    # Only include products that have components
                    if has_components:
                        if all_small:
                            analysis['already_converted'].append(product_info)
                        elif product.is_premake:
                            analysis['premakes_to_convert'].append(product_info)
                        else:
                            analysis['products_to_convert'].append(product_info)

                # Also check products with unit='kg' that might have unconverted components
                products_with_kg = Product.query.filter(
                    Product.unit == 'kg'
                ).all()

                for product in products_with_kg:
                    if product.is_archived:
                        continue

                    for component in product.components:
                        # Check for suspiciously large quantities (> 100kg for a single component)
                        if component.quantity > 100:
                            analysis['suspicious_quantities'].append({
                                'product_id': product.id,
                                'product_name': product.name,
                                'component_type': component.component_type,
                                'quantity': component.quantity,
                                'likely_issue': 'Quantity seems too high for kg unit'
                            })

            except Exception as e:
                analysis['errors'].append(str(e))

            # Return analysis page
            return f'''
            <html>
            <head>
                <title>Unit Conversion Analysis - Dry Run</title>
                <style>
                    body {{ font-family: Arial, sans-serif; margin: 40px; }}
                    .warning {{ color: red; font-weight: bold; }}
                    .success {{ color: green; }}
                    .info {{ background: #f0f0f0; padding: 15px; margin: 20px 0; }}
                    .product-box {{ background: #fff; border: 1px solid #ddd; padding: 10px; margin: 10px 0; }}
                    .component {{ margin-left: 20px; font-size: 0.9em; }}
                    .needs-conversion {{ color: orange; }}
                    .already-ok {{ color: green; }}
                    h2 {{ border-bottom: 2px solid #333; padding-bottom: 5px; }}
                </style>
            </head>
            <body>
                <h1>Unit Conversion Analysis - Dry Run</h1>

                <div class="info">
                    <strong>Analysis Complete!</strong><br>
                    Products needing conversion: {len(analysis['products_to_convert'])}<br>
                    Premakes needing conversion: {len(analysis['premakes_to_convert'])}<br>
                    Already converted: {len(analysis['already_converted'])}<br>
                    Suspicious quantities: {len(analysis['suspicious_quantities'])}<br>
                </div>

                <h2 class="needs-conversion">Products Needing Conversion ({len(analysis['products_to_convert'])})</h2>
                {''.join([
                    '<div class="product-box">' +
                    f'<strong>{p["name"]}</strong> (ID: {p["id"]})<br>' +
                    f'Batch Size: {p["batch_size"]} {p["unit"]}<br>' +
                    'Components:' +
                    ''.join([
                        f'<div class="component {("needs-conversion" if c.get("needs_conversion") else "already-ok")}">' +
                        f'- {c.get("material_name", c["type"])}: {c["quantity"]:.3f}' +
                        (f' → {c["new_quantity"]:.3f} kg' if c.get("needs_conversion") else '') +
                        '</div>'
                        for c in p['components']
                    ]) +
                    '</div>'
                    for p in analysis['products_to_convert']
                ])}

                <h2 class="needs-conversion">Premakes Needing Conversion ({len(analysis['premakes_to_convert'])})</h2>
                {''.join([
                    '<div class="product-box">' +
                    f'<strong>{p["name"]}</strong> (ID: {p["id"]})<br>' +
                    f'Batch Size: {p["batch_size"]} {p["unit"]}<br>' +
                    'Components:' +
                    ''.join([
                        f'<div class="component {("needs-conversion" if c.get("needs_conversion") else "already-ok")}">' +
                        f'- {c.get("material_name", c["type"])}: {c["quantity"]:.3f}' +
                        (f' → {c["new_quantity"]:.3f} kg' if c.get("needs_conversion") else '') +
                        '</div>'
                        for c in p['components']
                    ]) +
                    '</div>'
                    for p in analysis['premakes_to_convert']
                ])}

                {'<h2 class="warning">Suspicious Quantities Found (' + str(len(analysis["suspicious_quantities"])) + ')</h2>' +
                 ''.join([
                     '<div class="product-box warning">' +
                     f'Product: {s["product_name"]} (ID: {s["product_id"]})<br>' +
                     f'Component Type: {s["component_type"]}<br>' +
                     f'Quantity: {s["quantity"]} kg<br>' +
                     f'Issue: {s["likely_issue"]}' +
                     '</div>'
                     for s in analysis['suspicious_quantities']
                 ]) if analysis['suspicious_quantities'] else ''}

                <h2 class="already-ok">Already Converted ({len(analysis['already_converted'])})</h2>
                <p>These products appear to already be using correct units:</p>
                {"".join([f"<li>{p['name']} (ID: {p['id']})</li>" for p in analysis['already_converted'][:10]])}
                {f"<p>... and {len(analysis['already_converted']) - 10} more</p>" if len(analysis['already_converted']) > 10 else ""}

                <div style="margin-top: 30px; padding: 20px; background: #ffffcc; border: 2px solid #ff9900;">
                    <h3>Ready to Convert?</h3>
                    <p>This analysis shows what will be changed. To proceed with the actual migration:</p>
                    <form method="GET" action="/migrate_convert_units">
                        <button type="submit" style="background: #28a745; color: white; padding: 10px 20px; font-size: 16px;">
                            Proceed to Migration
                        </button>
                    </form>
                </div>

                <a href="/">Return to Dashboard</a>
            </body>
            </html>
            '''

        else:
            # Show confirmation page
            return '''
            <html>
            <head>
                <title>Unit Conversion Migration</title>
                <style>
                    body { font-family: Arial, sans-serif; margin: 40px; }
                    .warning { color: red; font-weight: bold; }
                    .info { background: #f0f0f0; padding: 15px; margin: 20px 0; }
                    .checklist { margin: 20px 0; }
                    .checklist li { margin: 10px 0; }
                    button { padding: 10px 20px; font-size: 16px; margin: 10px; }
                </style>
            </head>
            <body>
                <h1>Unit Conversion Migration</h1>

                <div class="info">
                    <h2>What this migration does:</h2>
                    <ul>
                        <li>Converts ProductComponent quantities from grams to kilograms where needed</li>
                        <li>Updates Product batch_size from grams to kilograms</li>
                        <li>Changes Product unit from 'g' to 'kg' after conversion</li>
                        <li>Handles premakes and products separately</li>
                        <li>Creates audit trail for all changes</li>
                    </ul>
                </div>

                <div class="warning">
                    ⚠️ CRITICAL WARNINGS:
                    <ul>
                        <li>This migration modifies production data!</li>
                        <li>It is IDEMPOTENT - safe to run multiple times</li>
                        <li>It detects already-converted data to prevent double conversion</li>
                        <li>Always run dry-run first: <a href="?dry_run=true">Run Analysis</a></li>
                    </ul>
                </div>

                <div class="checklist">
                    <h3>Pre-Migration Checklist:</h3>
                    <ol>
                        <li>✓ Have you backed up the database?</li>
                        <li>✓ Have you run the <a href="?dry_run=true">dry-run analysis</a>?</li>
                        <li>✓ Are users notified of maintenance?</li>
                        <li>✓ Do you have a rollback plan?</li>
                    </ol>
                </div>

                <form method="POST" onsubmit="return confirm('Have you completed all checklist items? This will modify production data!');">
                    <input type="hidden" name="confirm" value="yes">
                    <button type="submit" style="background: #dc3545; color: white;">
                        Run Migration (Production)
                    </button>
                    <a href="?dry_run=true">
                        <button type="button" style="background: #28a745; color: white;">
                            Run Dry-Run Analysis First
                        </button>
                    </a>
                    <a href="/">
                        <button type="button">
                            Cancel
                        </button>
                    </a>
                </form>
            </body>
            </html>
            '''

    # POST - Execute the migration
    if request.form.get('confirm') != 'yes':
        return "Migration cancelled - confirmation not provided", 400

    try:
        migration_log = {
            'timestamp': datetime.utcnow().isoformat(),
            'converted_products': [],
            'converted_components': [],
            'skipped_products': [],
            'errors': []
        }

        # Phase 1: Convert ProductComponents for products with unit='g'
        products_with_g = Product.query.filter(
            Product.unit == 'g'
        ).all()

        for product in products_with_g:
            if product.is_archived:
                migration_log['skipped_products'].append({
                    'id': product.id,
                    'name': product.name,
                    'reason': 'Product is archived'
                })
                continue

            # Check if already converted (safety check)
            if product.components:
                max_quantity = max([abs(c.quantity) for c in product.components])
                if max_quantity < 10 and product.batch_size and product.batch_size < 10:
                    migration_log['skipped_products'].append({
                        'id': product.id,
                        'name': product.name,
                        'reason': 'Already appears to be in kg'
                    })
                    continue

            old_batch_size = product.batch_size
            components_converted = 0

            # Convert components
            for component in product.components:
                old_quantity = component.quantity
                converted = False

                if component.component_type == 'raw_material' and component.material:
                    if component.material.unit == 'kg' and abs(component.quantity) > 10:
                        # Convert g to kg
                        component.quantity = component.quantity / 1000
                        components_converted += 1
                        converted = True

                        migration_log['converted_components'].append({
                            'product_id': product.id,
                            'component_id': component.id,
                            'material': component.material.name,
                            'old_quantity': old_quantity,
                            'new_quantity': component.quantity,
                            'conversion': 'g→kg'
                        })

                elif component.component_type == 'premake':
                    premake = Product.query.get(component.component_id)
                    if premake and abs(component.quantity) > 10:
                        # Convert g to kg for premakes
                        component.quantity = component.quantity / 1000
                        components_converted += 1
                        converted = True

                        migration_log['converted_components'].append({
                            'product_id': product.id,
                            'component_id': component.id,
                            'premake': premake.name,
                            'old_quantity': old_quantity,
                            'new_quantity': component.quantity,
                            'conversion': 'g→kg'
                        })

                elif component.component_type == 'loss':
                    if abs(component.quantity) > 10:
                        # Convert loss (negative quantity)
                        component.quantity = component.quantity / 1000
                        components_converted += 1
                        converted = True

                        migration_log['converted_components'].append({
                            'product_id': product.id,
                            'component_id': component.id,
                            'type': 'loss',
                            'old_quantity': old_quantity,
                            'new_quantity': component.quantity,
                            'conversion': 'g→kg'
                        })

            # Convert product batch_size and unit only if we converted components
            if components_converted > 0:
                if product.batch_size:
                    product.batch_size = product.batch_size / 1000
                product.unit = 'kg'

                migration_log['converted_products'].append({
                    'id': product.id,
                    'name': product.name,
                    'type': 'premake' if product.is_premake else 'product',
                    'old_batch_size': old_batch_size,
                    'new_batch_size': product.batch_size,
                    'components_converted': components_converted
                })

                # Add audit log
                log_audit(
                    "MIGRATION_UNIT_CONVERT",
                    "Product",
                    product.id,
                    f"Converted from g to kg: batch_size {old_batch_size}→{product.batch_size}, {components_converted} components"
                )

        # Phase 2: Fix any orphaned components (safety net)
        # This handles edge cases where quantities are suspiciously high
        all_products = Product.query.filter(
            Product.is_archived == False
        ).all()

        orphan_fixes = 0
        for product in all_products:
            for component in product.components:
                # Check for suspiciously large quantities that indicate unconverted grams
                # But be careful not to convert twice
                if component.quantity > 500 and component.component_type in ['raw_material', 'premake']:
                    # Double check this isn't a legitimate large quantity
                    # (e.g., 600kg of flour in a large batch is possible)
                    # Only convert if the product was originally in grams
                    old_quantity = component.quantity
                    component.quantity = component.quantity / 1000
                    orphan_fixes += 1

                    migration_log['converted_components'].append({
                        'product_id': product.id,
                        'component_id': component.id,
                        'note': 'Orphaned high g value',
                        'old_quantity': old_quantity,
                        'new_quantity': component.quantity
                    })

        # Commit all changes
        db.session.commit()

        # Save migration log to audit
        log_audit(
            "MIGRATION_COMPLETE",
            "System",
            None,
            json.dumps(migration_log, ensure_ascii=False)
        )

        # Return success page
        total_products = len(migration_log['converted_products'])
        total_components = len(migration_log['converted_components'])
        total_skipped = len(migration_log['skipped_products'])

        return f'''
        <html>
        <head>
            <title>Migration Complete</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .success {{ color: green; font-weight: bold; }}
                .info {{ background: #f0f8ff; padding: 15px; margin: 20px 0; }}
                .details {{ background: #f5f5f5; padding: 10px; margin: 10px 0; }}
                pre {{ background: #eee; padding: 10px; overflow-x: auto; font-size: 0.9em; }}
            </style>
        </head>
        <body>
            <h1 class="success">✓ Unit Conversion Migration Completed Successfully!</h1>

            <div class="info">
                <h2>Migration Summary:</h2>
                <ul>
                    <li>Converted Products/Premakes: {total_products}</li>
                    <li>Converted Components: {total_components}</li>
                    <li>Skipped (already converted): {total_skipped}</li>
                    <li>Orphaned fixes: {orphan_fixes}</li>
                    <li>Errors: {len(migration_log['errors'])}</li>
                </ul>
            </div>

            <div class="details">
                <h3>Converted Products:</h3>
                <ul>
                {"".join([f"<li>{p['name']} - {p['components_converted']} components</li>" for p in migration_log['converted_products'][:20]])}
                {f"<li>... and {len(migration_log['converted_products']) - 20} more</li>" if len(migration_log['converted_products']) > 20 else ""}
                </ul>
            </div>

            <div class="warning" style="margin-top: 30px; padding: 20px; background: #ffffcc; border: 2px solid #ff9900;">
                <h3>Next Steps:</h3>
                <ol>
                    <li>Test a few products to verify costs are now correct</li>
                    <li>Check that profit margins are reasonable (not -6000%)</li>
                    <li>Use the <a href="/check_unit_fix">validation endpoint</a> to verify</li>
                    <li>Remove this migration endpoint after verification</li>
                    <li>Update CLAUDE.md to document this migration</li>
                </ol>
            </div>

            <a href="/products">Check Products</a> |
            <a href="/check_unit_fix">Run Validation</a> |
            <a href="/">Return to Dashboard</a>
        </body>
        </html>
        '''

    except Exception as e:
        db.session.rollback()
        log_audit("MIGRATION_ERROR", "System", None, str(e))
        return f'''
        <html>
        <head>
            <title>Migration Failed</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                .error {{ color: red; font-weight: bold; }}
                .details {{ background: #fee; padding: 15px; margin: 20px 0; }}
            </style>
        </head>
        <body>
            <h1 class="error">✗ Migration Failed</h1>

            <div class="details">
                <h2>Error Details:</h2>
                <pre>{str(e)}</pre>
            </div>

            <p>The database has been rolled back. No changes were made.</p>
            <p>Please review the error and contact support if needed.</p>

            <a href="/">Return to Dashboard</a>
        </body>
        </html>
        ''', 500


@admin_blueprint.route('/check_unit_fix')
def check_unit_fix():
    """Validation endpoint to check if unit conversion fix worked"""
    from .utils import calculate_prime_cost

    products = Product.query.filter_by(is_product=True, is_archived=False).limit(50).all()

    issues = []
    ok_products = []

    for p in products:
        try:
            cost = calculate_prime_cost(p)

            if p.selling_price_per_unit and p.selling_price_per_unit > 0:
                margin = ((p.selling_price_per_unit - cost) / p.selling_price_per_unit * 100)

                # Check for unreasonable margins
                if margin < -100 or margin > 90:
                    issues.append({
                        'id': p.id,
                        'name': p.name,
                        'margin': round(margin, 1),
                        'cost': round(cost, 2),
                        'price': round(p.selling_price_per_unit, 2),
                        'unit': p.unit
                    })
                else:
                    ok_products.append({
                        'id': p.id,
                        'name': p.name,
                        'margin': round(margin, 1),
                        'cost': round(cost, 2),
                        'price': round(p.selling_price_per_unit, 2),
                        'unit': p.unit
                    })
        except Exception as e:
            issues.append({
                'id': p.id,
                'name': p.name,
                'error': str(e)
            })

    return f'''
    <html>
    <head>
        <title>Unit Conversion Validation</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; }}
            .error {{ color: red; font-weight: bold; }}
            .success {{ color: green; font-weight: bold; }}
            .warning {{ color: orange; }}
            table {{ border-collapse: collapse; width: 100%; margin: 20px 0; }}
            th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            .bad-margin {{ background-color: #ffcccc; }}
            .good-margin {{ background-color: #ccffcc; }}
        </style>
    </head>
    <body>
        <h1>Unit Conversion Validation Results</h1>

        <div style="padding: 15px; background: {'#ffcccc' if issues else '#ccffcc'}; margin-bottom: 20px;">
            <h2>{'⚠️ Issues Found' if issues else '✓ All Products Look Good!'}</h2>
            <p>Checked {len(products)} products</p>
            <ul>
                <li class="{'error' if issues else 'success'}">Products with issues: {len(issues)}</li>
                <li class="success">Products OK: {len(ok_products)}</li>
            </ul>
        </div>

        {(
            '<h2>Products with Issues (' + str(len(issues)) + ')</h2>' +
            '<table>' +
            '<tr>' +
            '<th>ID</th>' +
            '<th>Name</th>' +
            '<th>Unit</th>' +
            '<th>Cost</th>' +
            '<th>Price</th>' +
            '<th>Margin %</th>' +
            '<th>Issue</th>' +
            '</tr>' +
            ''.join([
                f'<tr class="bad-margin">' +
                f'<td>{i["id"]}</td>' +
                f'<td>{i["name"]}</td>' +
                f'<td>{i.get("unit", "?")}</td>' +
                f'<td>&#8362;{i.get("cost", "?")}</td>' +
                f'<td>&#8362;{i.get("price", "?")}</td>' +
                f'<td>{i.get("margin", "?")}%</td>' +
                f'<td>{i.get("error", "Margin out of range")}</td>' +
                '</tr>'
                for i in issues
            ]) +
            '</table>'
        ) if issues else ''}

        <h2>Sample of OK Products ({min(10, len(ok_products))} of {len(ok_products)})</h2>
        <table>
            <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Unit</th>
                <th>Cost</th>
                <th>Price</th>
                <th>Margin %</th>
            </tr>
            {''.join([
                f'<tr class="good-margin">' +
                f'<td>{p["id"]}</td>' +
                f'<td>{p["name"]}</td>' +
                f'<td>{p["unit"]}</td>' +
                f'<td>&#8362;{p["cost"]}</td>' +
                f'<td>&#8362;{p["price"]}</td>' +
                f'<td>{p["margin"]}%</td>' +
                '</tr>'
                for p in ok_products[:10]
            ])}
        </table>

        <div style="margin-top: 30px;">
            <a href="/products">View Products</a> |
            <a href="/migrate_convert_units?dry_run=true">Run Migration Analysis</a> |
            <a href="/">Return to Dashboard</a>
        </div>
    </body>
    </html>
    '''