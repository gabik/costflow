from datetime import datetime, date
import os
import tempfile
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session
from flask_babel import gettext as _
import pandas as pd
from ..models import db, RawMaterial, StockLog, Category, RawMaterialSupplier, Supplier, RawMaterialAlternativeName
from .utils import log_audit, normalize_unit, normalize_name

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
    E (4): Quantity (in packages)
    F (5): Price per unit (e.g., price per kg, per L)
    J (9): Date
    K (10): Unit (ק״ג, ליטר, יחידה) - optional, for validation
    L (11): Units per package (e.g., 22.8 kg per box) - used for quantity calculation only
    """
    # Use column positions instead of names
    # Column indices: A=0, B=1, C=2, D=3, E=4, F=5, G=6, H=7, I=8, J=9, K=10, L=11
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
    col_unit = columns[10] if len(columns) > 10 else None   # K - Unit (display only)
    col_units_per_pkg = columns[11] if len(columns) > 11 else None  # L - Units per package

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

        # Get unit from column K (optional, for validation)
        # Normalize to standard unit (e.g., ק״ג → kg)
        file_unit = None
        if col_unit and not pd.isna(row.get(col_unit)):
            raw_unit = str(row[col_unit]).strip()
            file_unit = normalize_unit(raw_unit)

        # Get units per package from column L (optional)
        units_per_package_file = None  # None means "not provided in file"
        units_per_package_invalid = False
        if col_units_per_pkg and not pd.isna(row.get(col_units_per_pkg)):
            try:
                val = float(row[col_units_per_pkg])
                if val <= 0:
                    units_per_package_invalid = True  # Will show warning
                    units_per_package_file = None
                else:
                    units_per_package_file = val
            except (ValueError, TypeError):
                pass  # Leave as None

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
            # Normalize the name from file for comparison
            normalized_file_name = normalize_name(name)

            # Try primary name first (exact match)
            material = RawMaterial.query.filter_by(name=name, is_deleted=False).first()
            if material:
                matched_by = 'name'
            else:
                # Try primary name with normalized comparison
                all_materials = RawMaterial.query.filter_by(is_deleted=False).all()
                for mat in all_materials:
                    if normalize_name(mat.name) == normalized_file_name:
                        material = mat
                        matched_by = 'name'
                        if mat.name != name:
                            system_name = mat.name  # Show the actual system name
                        break

            # If still not found, try alternative names
            if not material:
                # Try exact match first
                alt_name = RawMaterialAlternativeName.query.filter_by(alternative_name=name).first()
                if alt_name and not alt_name.raw_material.is_deleted:
                    material = alt_name.raw_material
                    matched_by = 'alt_name'
                    system_name = material.name  # Show the primary name
                else:
                    # Try normalized match on alternative names
                    all_alt_names = RawMaterialAlternativeName.query.join(RawMaterial).filter(
                        RawMaterial.is_deleted == False
                    ).all()
                    for alt in all_alt_names:
                        if normalize_name(alt.alternative_name) == normalized_file_name:
                            material = alt.raw_material
                            matched_by = 'alt_name'
                            system_name = material.name  # Show the primary name
                            break

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

        else:
            # New material
            status = 'new'
            current_price = None

            # Check if supplier exists
            if supplier_name and not supplier:
                status_flags.append('new_supplier')

        # Get system units_per_package (from supplier link if exists)
        units_per_package_system = None
        if supplier_link:
            units_per_package_system = supplier_link.units_per_package or 1.0

        # Determine effective units_per_package for calculation
        # Priority: file value > system value > default 1.0
        if units_per_package_file is not None:
            effective_upp = units_per_package_file
        elif units_per_package_system is not None:
            effective_upp = units_per_package_system
        else:
            effective_upp = 1.0

        # Column F is price per unit (kg, L, etc.) - use directly
        new_price = price

        # Check price difference (compare file price vs system price)
        if current_price is not None and abs(current_price - new_price) > 0.01:
            status_flags.append('price_change')

        # Check for units_per_package mismatch
        # Mismatch when: file has explicit value AND system has value AND they differ
        units_per_package_mismatch = False
        if units_per_package_file is not None and units_per_package_system is not None:
            if abs(units_per_package_system - units_per_package_file) > 0.001:
                units_per_package_mismatch = True
                status_flags.append('units_per_package_mismatch')

        # Warning: invalid units_per_package value (<=0)
        if units_per_package_invalid:
            status_flags.append('units_per_package_invalid')

        # Warning: units_per_package provided but no supplier (can't save)
        if units_per_package_file is not None and not supplier_name:
            status_flags.append('units_per_package_no_supplier')

        # Info: new supplier link will get units_per_package
        if units_per_package_file is not None and 'add_supplier' in status_flags:
            status_flags.append('units_per_package_new_supplier')

        # Get system unit for comparison
        system_unit = material.unit if material else None

        # Check for unit mismatch (column K vs system)
        unit_mismatch = False
        if file_unit and system_unit and file_unit != system_unit:
            unit_mismatch = True
            status_flags.append('unit_mismatch')

        # Calculate actual quantity to add (packages × units per package)
        calculated_quantity = quantity * effective_upp

        review_data.append({
            'name': name,
            'system_name': system_name,  # Name in system if different
            'sku': sku,
            'supplier_name': supplier_name,
            'supplier_id': supplier.id if supplier else None,
            'supplier_exists': supplier is not None,
            'material_id': material.id if material else None,
            'quantity': quantity,
            'new_price': new_price,  # Price per unit from file (F)
            'status': status,
            'status_flags': status_flags,
            'current_price': current_price,
            'matched_by': matched_by,
            'row_date': row_date,  # Date from row (may be None)
            'unit': system_unit,  # Unit from existing material
            'file_unit': file_unit,  # Unit from file (column K)
            'unit_mismatch': unit_mismatch,
            'units_per_package_file': units_per_package_file,
            'units_per_package_system': units_per_package_system,
            'units_per_package_mismatch': units_per_package_mismatch,
            'effective_upp': effective_upp,
            'calculated_quantity': calculated_quantity
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

            # Get unit from form (for new materials or unit updates)
            unit = item.get('unit', 'kg')
            file_unit = item.get('file_unit', '')
            unit_action = item.get('unit_action', 'keep_system')

            # Parse units_per_package data
            upp_file_str = item.get('units_per_package_file', '')
            upp_system_str = item.get('units_per_package_system', '')

            units_per_package_file = None
            if upp_file_str and upp_file_str.strip():
                try:
                    units_per_package_file = float(upp_file_str)
                except (ValueError, TypeError):
                    pass

            units_per_package_system = None
            if upp_system_str and upp_system_str.strip():
                try:
                    units_per_package_system = float(upp_system_str)
                except (ValueError, TypeError):
                    pass

            units_per_package_action = item.get('units_per_package_action', 'use_file')

            # Determine effective units_per_package based on action
            if units_per_package_action == 'update_system' and units_per_package_file is not None:
                effective_upp = units_per_package_file
            elif units_per_package_action == 'use_system' and units_per_package_system is not None:
                effective_upp = units_per_package_system
            elif units_per_package_file is not None:
                effective_upp = units_per_package_file
            elif units_per_package_system is not None:
                effective_upp = units_per_package_system
            else:
                effective_upp = 1.0

            # Use the user-edited calculated quantity directly (overrides any multiplication)
            try:
                final_quantity = float(item.get('calculated_quantity', quantity * effective_upp))
            except (ValueError, TypeError):
                final_quantity = quantity * effective_upp

            current_app.logger.info(f"Processing: {name}, status={status}, flags={status_flags}, material_id={material_id}, date={inventory_timestamp}, upp_file={units_per_package_file}, upp_system={units_per_package_system}, action={units_per_package_action}, final_qty={final_quantity}")

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
                # Create new material (price is set via supplier link, not directly)
                material = RawMaterial(
                    name=name,
                    category=default_category,
                    unit=unit
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
                    # Set units_per_package from file if provided
                    if units_per_package_file is not None and units_per_package_file != 1.0:
                        new_link.units_per_package = units_per_package_file
                    db.session.add(new_link)
                    stats['added_supplier_links'] += 1

                # Create initial stock log (using final_quantity which accounts for units_per_package)
                log = StockLog(
                    raw_material_id=material.id,
                    supplier_id=supplier.id if supplier else None,
                    action_type='set',
                    quantity=final_quantity,
                    timestamp=inventory_timestamp
                )
                db.session.add(log)
                current_app.logger.info(f"NEW MATERIAL StockLog: material_id={material.id}, qty={final_quantity} (file_qty={quantity} × upp={effective_upp}), action='set'")

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
                        # Set units_per_package from file if provided
                        if units_per_package_file is not None and units_per_package_file != 1.0:
                            supplier_link.units_per_package = units_per_package_file
                            current_app.logger.info(f"Set units_per_package={units_per_package_file} for new supplier link")
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

                # Step 5b: Update units_per_package if action is 'update_system'
                if supplier_link and units_per_package_action == 'update_system' and units_per_package_file is not None:
                    supplier_link.units_per_package = units_per_package_file
                    current_app.logger.info(f"Updated units_per_package for {name} to {units_per_package_file}")

                # Step 5c: Update unit if user chose 'update_system'
                if unit_action == 'update_system' and file_unit:
                    old_unit = material.unit
                    material.unit = file_unit
                    current_app.logger.info(f"Updated unit for {name} from {old_unit} to {file_unit}")

                # Step 6: Create stock log (using final_quantity which accounts for units_per_package)
                log = StockLog(
                    raw_material_id=material.id,
                    supplier_id=supplier.id if supplier else None,
                    action_type='add',
                    quantity=final_quantity,
                    timestamp=inventory_timestamp
                )
                db.session.add(log)
                current_app.logger.info(f"EXISTING MATERIAL StockLog: material_id={material.id}, qty={final_quantity} (file_qty={quantity} × upp={effective_upp}), action='add', supplier_id={supplier.id if supplier else None}")

            else:
                # Neither new nor existing material found - this shouldn't happen
                current_app.logger.error(f"NO STOCK LOG CREATED for '{name}': status={status}, material_id={material_id}, material found={material is not None}")
                errors.append(f"{name}: Material not found or created")

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
