from datetime import datetime, date
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app
from flask_babel import gettext as _
import pandas as pd
from ..models import db, RawMaterial, StockLog, Category, RawMaterialSupplier, Supplier
from .utils import log_audit

inventory_blueprint = Blueprint('inventory', __name__)

# ----------------------------
# Bulk Inventory Upload
# ----------------------------
def normalize_column_name(col):
    """Normalize column name to handle various quote types."""
    if not isinstance(col, str):
        return col
    # Replace various quote types with standard single quote
    col = col.replace('"', "'")  # Double quote -> single
    col = col.replace('״', "'")  # Hebrew gershayim -> single
    col = col.replace('׳', "'")  # Hebrew geresh -> single
    col = col.replace(''', "'")  # Smart quote -> single
    col = col.replace(''', "'")  # Smart quote -> single
    col = col.replace('`', "'")  # Backtick -> single
    return col.strip()


@inventory_blueprint.route('/inventory/upload', methods=['GET', 'POST'])
def upload_inventory():
    review_data = None
    skipped_rows = []
    today_date = date.today().isoformat()
    inventory_date = today_date

    if request.method == 'POST':
        # Get the selected inventory date (default to today)
        inventory_date = request.form.get('inventory_date', today_date)

        if 'inventory_file' not in request.files:
            return redirect(request.url)

        file = request.files['inventory_file']
        if file.filename == '':
            return redirect(request.url)

        if file:
            try:
                df = pd.read_excel(file)

                # Normalize column names (strip whitespace and handle quote variations)
                df.columns = [normalize_column_name(col) for col in df.columns]

                # Expected columns (normalized)
                col_name = 'שם מוצר'
                col_qty = "סה''כ כמות"
                col_price = 'מחיר ממוצע'
                col_sku = "מק'ט"  # SKU column (optional) - normalized
                col_supplier = 'ספק'  # Supplier column (optional)

                # Check for required columns
                missing_columns = []
                if col_name not in df.columns:
                    missing_columns.append(col_name)
                if col_qty not in df.columns:
                    missing_columns.append(col_qty)
                if col_price not in df.columns:
                    missing_columns.append(col_price)

                if missing_columns:
                    flash(_('Missing required columns: {}. Found columns: {}').format(
                        ', '.join(missing_columns),
                        ', '.join(df.columns.tolist())
                    ), 'error')
                    return render_template('upload_inventory.html',
                                           review_data=None,
                                           today_date=today_date,
                                           inventory_date=inventory_date)

                review_data = []

                for index, row in df.iterrows():
                    row_num = index + 2  # Excel row number (1-indexed + header)

                    if pd.isna(row[col_name]):
                        skipped_rows.append({'row': row_num, 'reason': _('Empty product name')})
                        continue

                    name = str(row[col_name]).strip()
                    if not name:
                        skipped_rows.append({'row': row_num, 'reason': _('Empty product name')})
                        continue

                    try:
                        quantity = float(row[col_qty])
                    except (ValueError, KeyError, TypeError):
                        skipped_rows.append({'row': row_num, 'name': name, 'reason': _('Invalid quantity')})
                        continue

                    try:
                        price = float(row[col_price])
                    except (ValueError, KeyError, TypeError):
                        skipped_rows.append({'row': row_num, 'name': name, 'reason': _('Invalid price')})
                        continue

                    # Get optional SKU and supplier
                    sku = str(row[col_sku]).strip() if col_sku in df.columns and not pd.isna(row.get(col_sku)) else None
                    supplier_name = str(row[col_supplier]).strip() if col_supplier in df.columns and not pd.isna(row.get(col_supplier)) else None

                    # Match material and supplier
                    material = None
                    supplier = None
                    matched_by = 'name'  # Track how we matched: 'sku', 'name', or 'new'

                    # First try to match by SKU and supplier if both are provided
                    if sku and supplier_name:
                        supplier = Supplier.query.filter_by(name=supplier_name).first()
                        if supplier:
                            material_supplier = RawMaterialSupplier.query.filter_by(
                                sku=sku,
                                supplier_id=supplier.id
                            ).first()
                            if material_supplier:
                                material = material_supplier.raw_material
                                matched_by = 'sku'

                    # If not found by SKU, try by name (exclude deleted materials)
                    if not material:
                        material = RawMaterial.query.filter_by(name=name, is_deleted=False).first()
                        if material and supplier_name and not supplier:
                            # Get supplier from material if not already found
                            supplier = Supplier.query.filter_by(name=supplier_name).first()

                    # Determine status
                    status = 'new'
                    current_price = None
                    price_differs = False

                    if material:
                        status = 'exists'
                        # Get supplier-specific price if supplier is identified
                        if supplier:
                            supplier_link = RawMaterialSupplier.query.filter_by(
                                raw_material_id=material.id,
                                supplier_id=supplier.id
                            ).first()
                            if supplier_link:
                                current_price = supplier_link.cost_per_unit
                            else:
                                # No supplier link found, use first available
                                if material.supplier_links:
                                    current_price = material.supplier_links[0].cost_per_unit
                                else:
                                    current_price = 0
                        else:
                            # No supplier specified, use primary or first available
                            primary_link = next((link for link in material.supplier_links if link.is_primary), None)
                            if primary_link:
                                current_price = primary_link.cost_per_unit
                            elif material.supplier_links:
                                current_price = material.supplier_links[0].cost_per_unit
                            else:
                                current_price = 0

                        if abs(current_price - price) > 0.01:
                            price_differs = True

                    review_data.append({
                        'name': name,
                        'sku': sku,
                        'supplier_name': supplier_name,
                        'supplier_id': supplier.id if supplier else None,
                        'material_id': material.id if material else None,
                        'quantity': quantity,
                        'new_price': price,
                        'status': status,
                        'current_price': current_price,
                        'price_differs': price_differs,
                        'matched_by': matched_by
                    })
                    
                # Show warning if rows were skipped
                if skipped_rows:
                    flash(_('Skipped {} rows due to invalid data. Check the warnings below.').format(len(skipped_rows)), 'warning')

            except Exception as e:
                flash(_('Error processing file: {}').format(str(e)), 'error')
                return render_template('upload_inventory.html',
                                       review_data=None,
                                       skipped_rows=[],
                                       today_date=today_date,
                                       inventory_date=inventory_date)

    return render_template('upload_inventory.html',
                           review_data=review_data,
                           skipped_rows=skipped_rows,
                           today_date=today_date,
                           inventory_date=inventory_date)

@inventory_blueprint.route('/inventory/confirm', methods=['POST'])
def confirm_inventory_upload():
    # Parse the complex form data (items[0][name], items[0][quantity], etc.)
    # Flask doesn't parse nested dicts automatically, so we iterate manually.

    # Get the inventory date and parse it to datetime with noon time
    inventory_date_str = request.form.get('inventory_date')
    if inventory_date_str:
        # Parse date string (YYYY-MM-DD) and set time to noon (12:00:00)
        inventory_timestamp = datetime.strptime(inventory_date_str, '%Y-%m-%d').replace(hour=12, minute=0, second=0)
    else:
        inventory_timestamp = datetime.utcnow()

    items_data = {}
    for key, value in request.form.items():
        if key.startswith('items['):
            # items[0][name] -> index=0, field=name
            parts = key.replace(']', '').split('[')
            index = int(parts[1])
            field = parts[2]

            if index not in items_data:
                items_data[index] = {}
            items_data[index][field] = value

    # Debug logging
    current_app.logger.info(f"INVENTORY IMPORT: Processing {len(items_data)} items")

    if not items_data:
        flash(_('No items to process. Please upload a file first.'), 'warning')
        return redirect(url_for('inventory.upload_inventory'))

    # Process items
    # Default category for new items (or create a 'General' one)
    default_category = Category.query.first()
    if not default_category:
        default_category = Category(name="כללי")
        db.session.add(default_category)
        db.session.commit()

    # Track statistics
    created_count = 0
    updated_count = 0
    errors = []

    try:
        for index, item in items_data.items():
            name = item.get('name', '')
            if not name:
                errors.append(f"Item {index}: Missing name")
                continue

            try:
                quantity = float(item.get('quantity', 0))
                new_price = float(item.get('new_price', 0))
            except (ValueError, TypeError) as e:
                errors.append(f"{name}: Invalid quantity or price")
                continue

            update_price = item.get('update_price') == 'yes'
            material_id = item.get('material_id')
            supplier_id = item.get('supplier_id')

            current_app.logger.debug(f"Processing: {name}, qty={quantity}, material_id={material_id}, supplier_id={supplier_id}")

            # Get material by ID if provided, otherwise by name (exclude deleted materials)
            material = None
            if material_id:
                try:
                    material = RawMaterial.query.filter_by(id=int(material_id), is_deleted=False).first()
                except (ValueError, TypeError):
                    pass

            if not material:
                material = RawMaterial.query.filter_by(name=name, is_deleted=False).first()

            if not material:
                # Create new
                material = RawMaterial(
                    name=name,
                    category=default_category,
                    unit='kg',  # Default unit
                    cost_per_unit=new_price
                )
                db.session.add(material)
                db.session.flush()  # Get ID

                # Initial stock log with supplier if provided
                log = StockLog(
                    raw_material_id=material.id,
                    supplier_id=int(supplier_id) if supplier_id else None,
                    action_type='set',
                    quantity=quantity,
                    timestamp=inventory_timestamp
                )
                db.session.add(log)
                created_count += 1
                current_app.logger.info(f"Created new material: {name} (ID: {material.id}), stock: {quantity}")

            else:
                # Update existing
                if update_price and supplier_id:
                    # Update supplier-specific price
                    supplier_link = RawMaterialSupplier.query.filter_by(
                        raw_material_id=material.id,
                        supplier_id=int(supplier_id)
                    ).first()
                    if supplier_link:
                        supplier_link.cost_per_unit = new_price
                    else:
                        # Create new supplier link if it doesn't exist
                        new_link = RawMaterialSupplier(
                            raw_material_id=material.id,
                            supplier_id=int(supplier_id),
                            cost_per_unit=new_price,
                            is_primary=not material.supplier_links  # Primary if no other links
                        )
                        db.session.add(new_link)
                        current_app.logger.info(f"Created new supplier link for {name} with supplier_id={supplier_id}")
                elif update_price:
                    # Update primary supplier price if no specific supplier
                    primary_link = next((link for link in material.supplier_links if link.is_primary), None)
                    if primary_link:
                        primary_link.cost_per_unit = new_price
                    elif material.supplier_links:
                        # If no primary, update first supplier
                        material.supplier_links[0].cost_per_unit = new_price

                # Add stock log with supplier
                log = StockLog(
                    raw_material_id=material.id,
                    supplier_id=int(supplier_id) if supplier_id else None,
                    action_type='add',
                    quantity=quantity,
                    timestamp=inventory_timestamp
                )
                db.session.add(log)
                updated_count += 1
                current_app.logger.info(f"Updated material: {name} (ID: {material.id}), added stock: {quantity}")

        log_audit("IMPORT", "Inventory", details=f"Imported {len(items_data)} items from Excel. Created: {created_count}, Updated: {updated_count}")
        db.session.commit()

        # Show success message
        if created_count > 0 and updated_count > 0:
            flash(_('Inventory updated successfully. Created {} new materials, updated {} existing.').format(created_count, updated_count), 'success')
        elif created_count > 0:
            flash(_('Inventory updated successfully. Created {} new materials.').format(created_count), 'success')
        elif updated_count > 0:
            flash(_('Inventory updated successfully. Updated {} materials.').format(updated_count), 'success')

        if errors:
            flash(_('Some items had errors: {}').format(', '.join(errors[:3])), 'warning')

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"INVENTORY IMPORT ERROR: {str(e)}")
        flash(_('Error processing inventory: {}').format(str(e)), 'error')
        return redirect(url_for('inventory.upload_inventory'))

    return redirect(url_for('main.raw_materials'))
