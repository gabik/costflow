from datetime import datetime, date
import os
import tempfile
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session
from flask_babel import gettext as _
import pandas as pd
from ..models import db, RawMaterial, StockLog, Category, RawMaterialSupplier, Supplier, RawMaterialAlternativeName
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


def find_column(df_columns, possible_names):
    """Find a column by trying multiple possible names."""
    normalized_cols = {normalize_column_name(col): col for col in df_columns}
    for name in possible_names:
        norm_name = normalize_column_name(name)
        if norm_name in normalized_cols:
            return normalized_cols[norm_name]
    return None


def process_inventory_dataframe(df):
    """
    Process inventory dataframe and return review_data and skipped_rows.
    Returns (review_data, skipped_rows, error_message)

    Expected column positions (first row is header):
    A (0): Material name
    B (1): SKU
    C (2): Supplier name
    E (4): Quantity
    F (5): Price per unit
    J (9): Date
    """
    # Use column positions instead of names
    # Column indices: A=0, B=1, C=2, D=3, E=4, F=5, G=6, H=7, I=8, J=9
    columns = df.columns.tolist()

    # Check we have enough columns
    if len(columns) < 6:
        return None, [], _('File must have at least 6 columns (A through F)')

    # Map to positional indices
    col_name = columns[0]      # A - Material name
    col_sku = columns[1]       # B - SKU
    col_supplier = columns[2]  # C - Supplier name
    col_qty = columns[4] if len(columns) > 4 else None      # E - Quantity
    col_price = columns[5] if len(columns) > 5 else None    # F - Price
    col_date = columns[9] if len(columns) > 9 else None     # J - Date

    # Check for required columns
    if col_qty is None or col_price is None:
        return None, [], _('File must have columns E (quantity) and F (price)')

    review_data = []
    skipped_rows = []

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
        sku = str(row[col_sku]).strip() if col_sku and not pd.isna(row.get(col_sku)) else None
        supplier_name = str(row[col_supplier]).strip() if col_supplier and not pd.isna(row.get(col_supplier)) else None

        # Get date from row (optional)
        row_date = None
        if col_date and not pd.isna(row.get(col_date)):
            try:
                date_val = row[col_date]
                if isinstance(date_val, str):
                    # Try parsing common date formats
                    for fmt in ['%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y']:
                        try:
                            row_date = datetime.strptime(date_val, fmt).strftime('%Y-%m-%d')
                            break
                        except ValueError:
                            continue
                elif hasattr(date_val, 'strftime'):
                    # pandas Timestamp or datetime
                    row_date = date_val.strftime('%Y-%m-%d')
            except Exception:
                pass  # Use default date if parsing fails

        # Enhanced matching logic - SKU + Supplier is ground truth
        material = None
        supplier = None
        supplier_link = None
        matched_by = 'new'
        status_flags = []
        system_name = None  # Name in system (if different from file)

        # Step 1: Try SKU + Supplier match (ground truth)
        if sku and supplier_name:
            supplier = Supplier.query.filter_by(name=supplier_name).first()
            if supplier:
                material_supplier = RawMaterialSupplier.query.filter_by(
                    sku=sku,
                    supplier_id=supplier.id
                ).first()
                if material_supplier:
                    material = material_supplier.raw_material
                    supplier_link = material_supplier
                    matched_by = 'sku'
                    # Check name mismatch
                    if material.name != name:
                        status_flags.append('name_mismatch')
                        system_name = material.name

        # Step 2: If no SKU match, try NAME match (primary name first, then alternative names)
        if not material:
            # Try primary name first
            material = RawMaterial.query.filter_by(name=name, is_deleted=False).first()
            if material:
                matched_by = 'name'
            else:
                # Try alternative names
                alt_name = RawMaterialAlternativeName.query.filter_by(alternative_name=name).first()
                if alt_name and not alt_name.raw_material.is_deleted:
                    material = alt_name.raw_material
                    matched_by = 'alt_name'
                    system_name = material.name  # Show the primary name

        # Step 3: Check supplier if provided but not found yet
        if supplier_name and not supplier:
            supplier = Supplier.query.filter_by(name=supplier_name).first()

        # Step 4: Determine status and flags
        current_price = None

        if material:
            status = 'exists'

            # Check supplier relationship
            if supplier_name:
                if not supplier:
                    # Supplier doesn't exist - will be created
                    status_flags.append('new_supplier')
                    current_price = 0
                else:
                    # Supplier exists, check if linked to material
                    if not supplier_link:
                        supplier_link = RawMaterialSupplier.query.filter_by(
                            raw_material_id=material.id,
                            supplier_id=supplier.id
                        ).first()

                    if supplier_link:
                        current_price = supplier_link.cost_per_unit
                        # Check if SKU needs to be added
                        if sku and not supplier_link.sku:
                            status_flags.append('add_sku')
                    else:
                        # No link - will add supplier to material
                        status_flags.append('add_supplier')
                        # Use primary supplier price for comparison
                        primary_link = next((link for link in material.supplier_links if link.is_primary), None)
                        if primary_link:
                            current_price = primary_link.cost_per_unit
                        elif material.supplier_links:
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

            # Check price difference
            if current_price is not None and abs(current_price - price) > 0.01:
                status_flags.append('price_change')

        else:
            # New material
            status = 'new'
            current_price = None

            # Check if supplier exists
            if supplier_name and not supplier:
                status_flags.append('new_supplier')

        review_data.append({
            'name': name,
            'system_name': system_name,  # Name in system if different
            'sku': sku,
            'supplier_name': supplier_name,
            'supplier_id': supplier.id if supplier else None,
            'supplier_exists': supplier is not None,
            'material_id': material.id if material else None,
            'quantity': quantity,
            'new_price': price,
            'status': status,
            'status_flags': status_flags,
            'current_price': current_price,
            'matched_by': matched_by,
            'row_date': row_date  # Date from row (may be None)
        })

    return review_data, skipped_rows, None


@inventory_blueprint.route('/inventory/upload', methods=['GET', 'POST'])
def upload_inventory():
    today_date = date.today().isoformat()

    if request.method == 'POST':
        if 'inventory_file' not in request.files:
            return redirect(request.url)

        file = request.files['inventory_file']
        if file.filename == '':
            return redirect(request.url)

        if file:
            try:
                # Save file to temp location
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
                file.save(temp_file.name)
                temp_file.close()

                # Get sheet names
                excel_file = pd.ExcelFile(temp_file.name)
                sheet_names = excel_file.sheet_names

                # Store temp file path in session
                session['inventory_temp_file'] = temp_file.name

                if len(sheet_names) > 1:
                    # Multiple sheets - show selection page
                    return render_template('upload_inventory.html',
                                           review_data=None,
                                           skipped_rows=[],
                                           today_date=today_date,
                                           sheet_names=sheet_names,
                                           file_uploaded=True)
                else:
                    # Single sheet - process directly
                    df = pd.read_excel(temp_file.name, sheet_name=sheet_names[0])
                    review_data, skipped_rows, error_msg = process_inventory_dataframe(df)

                    # Clean up temp file
                    if os.path.exists(temp_file.name):
                        os.remove(temp_file.name)
                    session.pop('inventory_temp_file', None)

                    if error_msg:
                        flash(error_msg, 'error')
                        return render_template('upload_inventory.html',
                                               review_data=None,
                                               skipped_rows=[],
                                               today_date=today_date)

                    if skipped_rows:
                        flash(_('Skipped {} rows due to invalid data. Check the warnings below.').format(len(skipped_rows)), 'warning')

                    return render_template('upload_inventory.html',
                                           review_data=review_data,
                                           skipped_rows=skipped_rows,
                                           today_date=today_date)

            except Exception as e:
                flash(_('Error processing file: {}').format(str(e)), 'error')
                return render_template('upload_inventory.html',
                                       review_data=None,
                                       skipped_rows=[],
                                       today_date=today_date)

    return render_template('upload_inventory.html',
                           review_data=None,
                           skipped_rows=[],
                           today_date=today_date)


@inventory_blueprint.route('/inventory/select_sheet', methods=['POST'])
def select_inventory_sheet():
    """Process selected sheet from multi-sheet Excel file"""
    sheet_name = request.form.get('sheet_name')
    temp_file = session.get('inventory_temp_file')
    today_date = date.today().isoformat()

    if not temp_file or not os.path.exists(temp_file):
        flash(_('File not found. Please upload again.'), 'error')
        return redirect(url_for('inventory.upload_inventory'))

    if not sheet_name:
        flash(_('Please select a sheet.'), 'error')
        return redirect(url_for('inventory.upload_inventory'))

    try:
        df = pd.read_excel(temp_file, sheet_name=sheet_name)
        review_data, skipped_rows, error_msg = process_inventory_dataframe(df)

        # Clean up temp file
        if os.path.exists(temp_file):
            os.remove(temp_file)
        session.pop('inventory_temp_file', None)

        if error_msg:
            flash(error_msg, 'error')
            return render_template('upload_inventory.html',
                                   review_data=None,
                                   skipped_rows=[],
                                   today_date=today_date)

        if skipped_rows:
            flash(_('Skipped {} rows due to invalid data. Check the warnings below.').format(len(skipped_rows)), 'warning')

        return render_template('upload_inventory.html',
                               review_data=review_data,
                               skipped_rows=skipped_rows,
                               today_date=today_date,
                               selected_sheet=sheet_name)

    except Exception as e:
        flash(_('Error processing sheet: {}').format(str(e)), 'error')
        return redirect(url_for('inventory.upload_inventory'))

@inventory_blueprint.route('/inventory/confirm', methods=['POST'])
def confirm_inventory_upload():
    # Parse the complex form data (items[0][name], items[0][quantity], etc.)
    # Flask doesn't parse nested dicts automatically, so we iterate manually.

    # Default timestamp (today at noon) for items without a date
    default_timestamp = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)

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
    stats = {
        'created_materials': 0,
        'updated_materials': 0,
        'created_suppliers': 0,
        'added_supplier_links': 0,
        'added_skus': 0,
        'price_updates': 0,
        'added_alt_names': 0
    }
    errors = []
    new_suppliers_created = []

    skipped_count = 0
    try:
        for index, item in items_data.items():
            # Check if row is selected for import
            include = item.get('include', 'yes')
            if include != 'yes':
                skipped_count += 1
                continue

            name = item.get('name', '')
            if not name:
                errors.append(f"Item {index}: Missing name")
                continue

            try:
                quantity = float(item.get('quantity', 0))
                new_price = float(item.get('new_price', 0))
            except (ValueError, TypeError):
                errors.append(f"{name}: Invalid quantity or price")
                continue

            material_id = item.get('material_id')
            supplier_id = item.get('supplier_id')
            supplier_name = item.get('supplier_name')
            supplier_exists = item.get('supplier_exists', '').lower() == 'true'
            sku = item.get('sku')
            status = item.get('status')
            # Parse status_flags from comma-separated string
            status_flags_str = item.get('status_flags', '')
            status_flags = [f.strip() for f in status_flags_str.split(',') if f.strip()]

            # Parse per-row date (use default if not provided)
            row_date_str = item.get('row_date', '')
            if row_date_str:
                try:
                    inventory_timestamp = datetime.strptime(row_date_str, '%Y-%m-%d').replace(hour=12, minute=0, second=0)
                except ValueError:
                    inventory_timestamp = default_timestamp
            else:
                inventory_timestamp = default_timestamp

            current_app.logger.info(f"Processing: {name}, status={status}, flags={status_flags}, material_id={material_id}, date={inventory_timestamp}")

            # Step 1: Create new supplier if needed
            supplier = None
            if supplier_id:
                supplier = Supplier.query.get(int(supplier_id))
            elif supplier_name and 'new_supplier' in status_flags:
                # Create new supplier
                supplier = Supplier(name=supplier_name)
                db.session.add(supplier)
                db.session.flush()  # Get ID
                stats['created_suppliers'] += 1
                new_suppliers_created.append(supplier_name)
                current_app.logger.info(f"CREATED NEW SUPPLIER: {supplier_name} (ID: {supplier.id})")
            elif supplier_name:
                # Supplier should exist, find it
                supplier = Supplier.query.filter_by(name=supplier_name).first()

            # Step 2: Get or create material
            material = None
            if material_id:
                try:
                    material = RawMaterial.query.filter_by(id=int(material_id), is_deleted=False).first()
                except (ValueError, TypeError):
                    pass

            if not material and status != 'new':
                # Try to find by name
                material = RawMaterial.query.filter_by(name=name, is_deleted=False).first()

            if status == 'new' and not material:
                # Create new material
                material = RawMaterial(
                    name=name,
                    category=default_category,
                    unit='kg',
                    cost_per_unit=new_price
                )
                db.session.add(material)
                db.session.flush()
                stats['created_materials'] += 1
                current_app.logger.info(f"Created new material: {name} (ID: {material.id})")

                # Create supplier link if supplier provided
                if supplier:
                    new_link = RawMaterialSupplier(
                        raw_material_id=material.id,
                        supplier_id=supplier.id,
                        cost_per_unit=new_price,
                        sku=sku if sku else None,
                        is_primary=True
                    )
                    db.session.add(new_link)
                    stats['added_supplier_links'] += 1

                # Create initial stock log
                log = StockLog(
                    raw_material_id=material.id,
                    supplier_id=supplier.id if supplier else None,
                    action_type='set',
                    quantity=quantity,
                    timestamp=inventory_timestamp
                )
                db.session.add(log)

            elif material:
                # Update existing material
                stats['updated_materials'] += 1

                # Step 3: Handle supplier link
                supplier_link = None
                if supplier:
                    supplier_link = RawMaterialSupplier.query.filter_by(
                        raw_material_id=material.id,
                        supplier_id=supplier.id
                    ).first()

                    if not supplier_link and 'add_supplier' in status_flags:
                        # Create new supplier link
                        supplier_link = RawMaterialSupplier(
                            raw_material_id=material.id,
                            supplier_id=supplier.id,
                            cost_per_unit=new_price,
                            sku=sku if sku else None,
                            is_primary=not material.supplier_links  # Primary if no other links
                        )
                        db.session.add(supplier_link)
                        stats['added_supplier_links'] += 1
                        current_app.logger.info(f"Added supplier {supplier.name} to material {name}")

                # Step 4: Add SKU if needed
                if supplier_link and 'add_sku' in status_flags and sku:
                    supplier_link.sku = sku
                    stats['added_skus'] += 1
                    current_app.logger.info(f"Added SKU {sku} to material {name}")

                # Step 4b: Add alternative name if name mismatch (SKU matched but name differs)
                if 'name_mismatch' in status_flags:
                    current_app.logger.info(f"Name mismatch detected: file='{name}' vs system='{material.name}'")
                    if name != material.name:
                        # Check if this name is not already an alternative
                        existing_alt = RawMaterialAlternativeName.query.filter_by(
                            alternative_name=name
                        ).first()
                        if not existing_alt:
                            alt_name = RawMaterialAlternativeName(
                                raw_material_id=material.id,
                                alternative_name=name
                            )
                            db.session.add(alt_name)
                            stats['added_alt_names'] += 1
                            current_app.logger.info(f"ADDED alternative name '{name}' for material '{material.name}'")
                        else:
                            current_app.logger.info(f"Alternative name '{name}' already exists for material ID {existing_alt.raw_material_id}")

                # Step 5: Update price if needed
                if 'price_change' in status_flags:
                    if supplier_link:
                        supplier_link.cost_per_unit = new_price
                    elif supplier and not supplier_link:
                        # Link was just created above with new_price
                        pass
                    else:
                        # Update primary supplier price
                        primary_link = next((link for link in material.supplier_links if link.is_primary), None)
                        if primary_link:
                            primary_link.cost_per_unit = new_price
                        elif material.supplier_links:
                            material.supplier_links[0].cost_per_unit = new_price
                    stats['price_updates'] += 1

                # Step 6: Create stock log
                log = StockLog(
                    raw_material_id=material.id,
                    supplier_id=supplier.id if supplier else None,
                    action_type='add',
                    quantity=quantity,
                    timestamp=inventory_timestamp
                )
                db.session.add(log)
                current_app.logger.info(f"Updated material: {name}, added stock: {quantity}")

        # Build audit details
        audit_details = f"Imported {len(items_data)} items. "
        audit_details += f"Materials: {stats['created_materials']} new, {stats['updated_materials']} updated. "
        if stats['created_suppliers'] > 0:
            audit_details += f"Suppliers created: {stats['created_suppliers']}. "
        if stats['added_supplier_links'] > 0:
            audit_details += f"Supplier links: {stats['added_supplier_links']}. "
        if stats['added_alt_names'] > 0:
            audit_details += f"Alternative names: {stats['added_alt_names']}. "

        log_audit("IMPORT", "Inventory", details=audit_details)
        db.session.commit()

        # Show success messages
        messages = []
        if stats['created_materials'] > 0:
            messages.append(_('Created {} new materials').format(stats['created_materials']))
        if stats['updated_materials'] > 0:
            messages.append(_('Updated {} materials').format(stats['updated_materials']))
        if stats['added_supplier_links'] > 0:
            messages.append(_('Added {} supplier links').format(stats['added_supplier_links']))
        if stats['added_skus'] > 0:
            messages.append(_('Added {} SKUs').format(stats['added_skus']))
        if stats['added_alt_names'] > 0:
            messages.append(_('Added {} alternative names').format(stats['added_alt_names']))
        if stats['price_updates'] > 0:
            messages.append(_('Updated {} prices').format(stats['price_updates']))

        if messages:
            flash(_('Inventory updated successfully. {}').format(', '.join(messages)), 'success')

        # Show info about skipped rows
        if skipped_count > 0:
            flash(_('Skipped {} items (not selected)').format(skipped_count), 'info')

        # Show crucial warning for new suppliers
        if new_suppliers_created:
            flash(_('NEW SUPPLIERS CREATED: {}').format(', '.join(new_suppliers_created)), 'warning')

        if errors:
            flash(_('Some items had errors: {}').format(', '.join(errors[:3])), 'warning')

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"INVENTORY IMPORT ERROR: {str(e)}")
        flash(_('Error processing inventory: {}').format(str(e)), 'error')
        return redirect(url_for('inventory.upload_inventory'))

    return redirect(url_for('raw_materials.raw_materials'))
