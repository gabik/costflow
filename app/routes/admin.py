import json
import io
from datetime import datetime, timedelta
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


@admin_blueprint.route('/admin/reset_db', methods=['POST'])
def reset_db():
    """
    Full system reset - drops all tables and recreates them.
    WARNING: This will delete ALL data in the system!
    """
    from flask_babel import gettext as _
    try:
        # Log the reset action before dropping tables
        log_audit("RESET_INITIATED", "System", None, "Database reset initiated")

        # Drop all tables
        db.drop_all()

        # Recreate all tables
        db.create_all()

        # Re-seed essential data
        from ..models import Category
        default_category = Category(name="כללי", type='raw_material')
        db.session.add(default_category)

        # Create audit log entry for the reset
        log_audit("RESET_COMPLETE", "System", None, "Database was fully reset")

        db.session.commit()

        flash(_("System has been reset successfully"), 'success')
        return redirect(url_for('main.index'))
    except Exception as e:
        db.session.rollback()
        flash(_("Error resetting database: {}").format(str(e)), 'error')
        return redirect(url_for('admin.audit_log'))


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
                except Exception:
                    # Skip items that fail to restore
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


@admin_blueprint.route('/migrate_debug_fill_inventory')
def debug_fill_inventory():
    """Debug endpoint to fill all raw materials with 1000 units of stock"""
    from flask_babel import gettext as _
    try:
        # Get all raw materials
        materials = RawMaterial.query.filter_by(is_deleted=False).all()
        updated_count = 0

        for material in materials:
            # Skip unlimited materials
            if material.is_unlimited:
                continue

            # Get primary supplier or first available supplier
            supplier = None
            for rms in material.supplier_links:
                if rms.is_primary:
                    supplier = rms.supplier
                    break
            if not supplier and material.supplier_links:
                supplier = material.supplier_links[0].supplier

            # Create stock log entry setting stock to 1000
            stock_entry = StockLog(
                raw_material_id=material.id,
                supplier_id=supplier.id if supplier else None,
                action_type='set',
                quantity=1000.0,
                timestamp=datetime.now()
            )
            db.session.add(stock_entry)
            updated_count += 1

        db.session.commit()

        message = f"Successfully filled {updated_count} raw materials with 1000 units each"
        log_audit("DEBUG_FILL_INVENTORY", "System", details=message)

        return jsonify({
            'status': 'success',
            'message': message,
            'materials_updated': updated_count
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
    

@admin_blueprint.route('/migrate_add_component_description')
def migrate_add_component_description():
    """Migration: Add description column to product_component table"""
    try:
        # Check if column exists (naive check by trying to select it)
        try:
            db.session.execute(text("SELECT description FROM product_component LIMIT 1"))
            return jsonify({'status': 'skipped', 'message': 'Column description already exists'})
        except Exception:
            db.session.rollback()

        # Add the column
        with db.engine.connect() as conn:
            conn.execute(text("ALTER TABLE product_component ADD COLUMN description VARCHAR(255)"))
            conn.commit()
        
        log_audit("MIGRATION", "System", details="Added description column to product_component table")
        return jsonify({'status': 'success', 'message': 'Added description column to product_component'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

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