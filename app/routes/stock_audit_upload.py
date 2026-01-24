from datetime import datetime, date
import os
import tempfile
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session
from flask_babel import gettext as _
import pandas as pd
from ..models import db, RawMaterial, StockLog, StockAudit, RawMaterialSupplier, RawMaterialAlternativeName, Supplier, Category
from .utils import log_audit, calculate_supplier_stock, calculate_total_material_stock

stock_audit_upload_blueprint = Blueprint('stock_audit_upload', __name__)


def process_stock_audit_dataframe(df):
    """
    Process stock audit dataframe and return review_data and skipped_rows.
    Returns (review_data, skipped_rows, error_message)

    Expected column positions (first row is header):
    A (0): SKU
    B (1): Name
    D (3): Quantity (physical count)
    """
    columns = df.columns.tolist()

    # Check we have enough columns
    if len(columns) < 4:
        return None, [], _('File must have at least 4 columns (A through D)')

    # Map to positional indices
    col_sku = columns[0]       # A - SKU
    col_name = columns[1]      # B - Name
    col_qty = columns[3]       # D - Quantity

    review_data = []
    skipped_rows = []

    for index, row in df.iterrows():
        row_num = index + 2  # Excel row number (1-indexed + header)

        # Get name - skip if empty
        name = str(row[col_name]).strip() if not pd.isna(row[col_name]) else ''
        if not name:
            skipped_rows.append({'row': row_num, 'reason': _('Empty name')})
            continue

        # Get SKU (optional)
        sku = str(row[col_sku]).strip() if not pd.isna(row[col_sku]) else None
        if sku == '' or sku == 'nan':
            sku = None

        # Get quantity
        try:
            quantity = float(row[col_qty])
        except (ValueError, TypeError):
            skipped_rows.append({'row': row_num, 'name': name, 'reason': _('Invalid quantity')})
            continue

        # Match material with updated priority logic
        material = None
        matched_by = None
        primary_link = None
        status = 'not_found'
        add_alt_name = False  # Flag to auto-add file name as alternative name
        existing_skus = []  # For ambiguous status display
        target_sku = None  # The SKU to use for stock tracking

        # STEP 1: Try SKU match (highest priority)
        if sku:
            material_supplier = RawMaterialSupplier.query.filter_by(sku=sku).first()
            if material_supplier and not material_supplier.raw_material.is_deleted:
                material = material_supplier.raw_material
                matched_by = 'sku'
                primary_link = material_supplier
                target_sku = sku
                status = 'found'

                # Check if file name differs from material name - will auto-add as alt name
                if name != material.name:
                    alt_exists = RawMaterialAlternativeName.query.filter_by(
                        alternative_name=name).first()
                    if not alt_exists:
                        add_alt_name = True

        # STEP 2: Try name/alt_name match (with SKU check)
        if not material:
            # Try exact name match
            material = RawMaterial.query.filter_by(name=name, is_deleted=False).first()
            if material:
                matched_by = 'name'
            else:
                # Try alternative name match
                alt_name_entry = RawMaterialAlternativeName.query.filter_by(
                    alternative_name=name).first()
                if alt_name_entry and not alt_name_entry.raw_material.is_deleted:
                    material = alt_name_entry.raw_material
                    matched_by = 'alt_name'

            if material:
                # Material found by name - now check SKU situation
                if sku:
                    # File has SKU - check if it exists in material's supplier links
                    existing_link = next((l for l in material.supplier_links
                                          if l.sku == sku), None)
                    if existing_link:
                        # SKU exists for this material - use it
                        primary_link = existing_link
                        target_sku = sku
                        matched_by = f'{matched_by}+sku'
                        status = 'found'
                    else:
                        # Name matches but SKU is new - AMBIGUOUS
                        status = 'ambiguous'
                        existing_skus = [l.sku for l in material.supplier_links if l.sku]
                        # Still get primary link for display purposes
                        primary_link = next((l for l in material.supplier_links if l.is_primary), None)
                        if not primary_link and material.supplier_links:
                            primary_link = material.supplier_links[0]
                else:
                    # No SKU in file - use primary supplier
                    primary_link = next((l for l in material.supplier_links if l.is_primary), None)
                    if not primary_link and material.supplier_links:
                        primary_link = material.supplier_links[0]
                    target_sku = primary_link.sku if primary_link else None
                    status = 'found'

        # Get current stock and supplier info
        current_stock = 0
        supplier_id = None
        cost_per_unit = 0

        if material and status != 'not_found':
            if primary_link:
                supplier_id = primary_link.supplier_id
                cost_per_unit = primary_link.cost_per_unit or 0
                # Use SKU-specific stock if available
                if target_sku:
                    current_stock = calculate_supplier_stock(material.id, supplier_id, sku=target_sku)
                else:
                    current_stock = calculate_supplier_stock(material.id, supplier_id)
            else:
                current_stock = calculate_total_material_stock(material.id)

        variance = quantity - current_stock if material and status != 'not_found' else None

        review_data.append({
            'name': name,
            'sku': sku,
            'material_id': material.id if material else None,
            'material_name': material.name if material else None,
            'matched_by': matched_by,
            'quantity': quantity,
            'current_stock': round(current_stock, 2) if material and status != 'not_found' else None,
            'variance': round(variance, 2) if variance is not None else None,
            'status': status,
            'supplier_id': supplier_id,
            'supplier_link_id': primary_link.id if primary_link else None,
            'cost_per_unit': cost_per_unit,
            'unit': material.unit if material else None,
            'add_alt_name': add_alt_name,
            'target_sku': target_sku,
            'existing_skus': existing_skus,
        })

    return review_data, skipped_rows, None


@stock_audit_upload_blueprint.route('/stock_audit/upload', methods=['GET', 'POST'])
def upload_stock_audit():
    today_date = date.today().isoformat()

    if request.method == 'POST':
        if 'audit_file' not in request.files:
            flash(_('No file selected'), 'error')
            return redirect(request.url)

        file = request.files['audit_file']
        if file.filename == '':
            flash(_('No file selected'), 'error')
            return redirect(request.url)

        # Get audit date from form
        audit_date_str = request.form.get('audit_date', today_date)

        if file:
            try:
                # Save file to temp location
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
                file.save(temp_file.name)
                temp_file.close()

                # Get sheet names
                excel_file = pd.ExcelFile(temp_file.name)
                sheet_names = excel_file.sheet_names

                # Store temp file path and audit date in session
                session['stock_audit_temp_file'] = temp_file.name
                session['stock_audit_date'] = audit_date_str

                # If multiple sheets, show sheet selection
                if len(sheet_names) > 1:
                    return render_template('upload_stock_audit.html',
                                           sheet_names=sheet_names,
                                           audit_date=audit_date_str,
                                           today_date=today_date)

                # Single sheet - process directly
                df = pd.read_excel(temp_file.name, sheet_name=sheet_names[0])
                review_data, skipped_rows, error = process_stock_audit_dataframe(df)

                if error:
                    flash(error, 'error')
                    return redirect(request.url)

                # Get materials and suppliers for not-found items
                all_materials = RawMaterial.query.filter_by(is_deleted=False).order_by(RawMaterial.name).all()
                all_suppliers = Supplier.query.filter_by(is_active=True).order_by(Supplier.name).all()

                # Clean up temp file reference (will be deleted after confirm)
                return render_template('upload_stock_audit.html',
                                       review_data=review_data,
                                       skipped_rows=skipped_rows,
                                       audit_date=audit_date_str,
                                       today_date=today_date,
                                       all_materials=all_materials,
                                       all_suppliers=all_suppliers)

            except Exception as e:
                current_app.logger.error(f"Stock audit upload error: {str(e)}")
                flash(_('Error reading file: {}').format(str(e)), 'error')
                return redirect(request.url)

    return render_template('upload_stock_audit.html', today_date=today_date)


@stock_audit_upload_blueprint.route('/stock_audit/select_sheet', methods=['POST'])
def select_stock_audit_sheet():
    sheet_name = request.form.get('sheet_name')
    temp_file_path = session.get('stock_audit_temp_file')
    audit_date_str = session.get('stock_audit_date', date.today().isoformat())

    if not temp_file_path or not os.path.exists(temp_file_path):
        flash(_('Session expired. Please upload the file again.'), 'error')
        return redirect(url_for('stock_audit_upload.upload_stock_audit'))

    try:
        df = pd.read_excel(temp_file_path, sheet_name=sheet_name)
        review_data, skipped_rows, error = process_stock_audit_dataframe(df)

        if error:
            flash(error, 'error')
            return redirect(url_for('stock_audit_upload.upload_stock_audit'))

        # Get materials and suppliers for not-found items
        all_materials = RawMaterial.query.filter_by(is_deleted=False).order_by(RawMaterial.name).all()
        all_suppliers = Supplier.query.filter_by(is_active=True).order_by(Supplier.name).all()

        return render_template('upload_stock_audit.html',
                               review_data=review_data,
                               skipped_rows=skipped_rows,
                               audit_date=audit_date_str,
                               today_date=date.today().isoformat(),
                               selected_sheet=sheet_name,
                               all_materials=all_materials,
                               all_suppliers=all_suppliers)

    except Exception as e:
        flash(_('Error processing sheet: {}').format(str(e)), 'error')
        return redirect(url_for('stock_audit_upload.upload_stock_audit'))


@stock_audit_upload_blueprint.route('/stock_audit/confirm', methods=['POST'])
def confirm_stock_audit():
    # Get audit date from form
    audit_date_str = request.form.get('audit_date', date.today().isoformat())
    try:
        audit_datetime = datetime.strptime(audit_date_str, '%Y-%m-%d').replace(hour=12, minute=0, second=0)
    except ValueError:
        audit_datetime = datetime.utcnow().replace(hour=12, minute=0, second=0, microsecond=0)

    # Parse form data
    items_data = {}
    for key, value in request.form.items():
        if key.startswith('items['):
            parts = key.replace(']', '').split('[')
            index = int(parts[1])
            field = parts[2]

            if index not in items_data:
                items_data[index] = {}
            items_data[index][field] = value

    if not items_data:
        flash(_('No items to process.'), 'warning')
        return redirect(url_for('stock_audit_upload.upload_stock_audit'))

    # Get default category for new materials
    default_category = Category.query.first()
    if not default_category:
        default_category = Category(name="כללי")
        db.session.add(default_category)
        db.session.flush()

    # Process items
    stats = {
        'audits_created': 0,
        'materials_created': 0,
        'alt_names_added': 0,
        'sku_variants_created': 0,
        'skipped': 0,
        'errors': 0
    }
    errors = []

    try:
        for index, item in items_data.items():
            # Check if row is selected
            include = item.get('include', 'yes')
            if include != 'yes':
                stats['skipped'] += 1
                continue

            name = item.get('name', '')
            status = item.get('status', 'found')
            action = item.get('action', '')  # Action for not_found/ambiguous items
            sku = item.get('sku') or None
            target_sku = item.get('target_sku') or sku  # SKU to use for stock tracking

            try:
                quantity = float(item.get('quantity', 0))
            except (ValueError, TypeError):
                errors.append(f"{name}: Invalid quantity")
                stats['errors'] += 1
                continue

            material = None
            supplier_id = None
            primary_link = None

            # Handle found items
            if status == 'found':
                material_id = item.get('material_id')
                if not material_id:
                    stats['skipped'] += 1
                    continue

                try:
                    material_id = int(material_id)
                except (ValueError, TypeError):
                    errors.append(f"{name}: Invalid material ID")
                    stats['errors'] += 1
                    continue

                material = RawMaterial.query.filter_by(id=material_id, is_deleted=False).first()
                if not material:
                    errors.append(f"{name}: Material not found")
                    stats['errors'] += 1
                    continue

                # Get supplier info
                supplier_id = item.get('supplier_id')
                if supplier_id:
                    try:
                        supplier_id = int(supplier_id)
                    except (ValueError, TypeError):
                        supplier_id = None

                # Auto-add alternative name if flagged (SKU matched but name differs)
                add_alt_name = item.get('add_alt_name', '') == 'true'
                if add_alt_name and name != material.name:
                    existing_alt = RawMaterialAlternativeName.query.filter_by(
                        alternative_name=name).first()
                    if not existing_alt:
                        alt_name_entry = RawMaterialAlternativeName(
                            raw_material_id=material.id,
                            alternative_name=name
                        )
                        db.session.add(alt_name_entry)
                        stats['alt_names_added'] += 1
                        current_app.logger.info(f"Auto-added alternative name '{name}' for material '{material.name}'")

            # Handle ambiguous items (name matched but SKU is new)
            elif status == 'ambiguous':
                material_id = item.get('material_id')
                if not material_id:
                    stats['skipped'] += 1
                    continue

                try:
                    material_id = int(material_id)
                except (ValueError, TypeError):
                    errors.append(f"{name}: Invalid material ID")
                    stats['errors'] += 1
                    continue

                material = RawMaterial.query.filter_by(id=material_id, is_deleted=False).first()
                if not material:
                    errors.append(f"{name}: Material not found")
                    stats['errors'] += 1
                    continue

                if action == 'add_new_sku':
                    # Create new SKU variant for existing material
                    if not sku:
                        errors.append(f"{name}: SKU required for new variant")
                        stats['errors'] += 1
                        continue

                    new_supplier_id = item.get('new_supplier_id')
                    try:
                        new_price = float(item.get('new_price', 0))
                    except (ValueError, TypeError):
                        new_price = 0

                    if not new_supplier_id:
                        errors.append(f"{name}: No supplier selected for new SKU")
                        stats['errors'] += 1
                        continue

                    try:
                        new_supplier_id = int(new_supplier_id)
                    except (ValueError, TypeError):
                        errors.append(f"{name}: Invalid supplier ID")
                        stats['errors'] += 1
                        continue

                    supplier = Supplier.query.get(new_supplier_id)
                    if not supplier:
                        errors.append(f"{name}: Supplier not found")
                        stats['errors'] += 1
                        continue

                    # Check if SKU variant already exists (race condition protection)
                    existing_link = RawMaterialSupplier.query.filter_by(
                        raw_material_id=material.id,
                        supplier_id=supplier.id,
                        sku=sku
                    ).first()

                    if existing_link:
                        # SKU already exists - use it instead of creating
                        primary_link = existing_link
                        supplier_id = supplier.id
                        target_sku = sku
                        current_app.logger.info(f"SKU variant '{sku}' already exists, using existing link")
                    else:
                        # Create new supplier link with the new SKU
                        new_link = RawMaterialSupplier(
                            raw_material_id=material.id,
                            supplier_id=supplier.id,
                            cost_per_unit=new_price,
                            sku=sku,
                            is_primary=False
                        )
                        db.session.add(new_link)
                        db.session.flush()
                        primary_link = new_link
                        stats['sku_variants_created'] += 1
                        current_app.logger.info(f"Created new SKU variant '{sku}' for material '{material.name}'")

                    supplier_id = supplier.id
                    target_sku = sku

                elif action == 'link_existing_sku':
                    # Use selected existing SKU variant
                    sku_variant_id = item.get('sku_variant_id')
                    if not sku_variant_id:
                        errors.append(f"{name}: No SKU variant selected")
                        stats['errors'] += 1
                        continue

                    try:
                        sku_variant_id = int(sku_variant_id)
                    except (ValueError, TypeError):
                        errors.append(f"{name}: Invalid SKU variant ID")
                        stats['errors'] += 1
                        continue

                    link = RawMaterialSupplier.query.get(sku_variant_id)
                    if not link or link.raw_material_id != material.id:
                        errors.append(f"{name}: SKU variant not found")
                        stats['errors'] += 1
                        continue

                    supplier_id = link.supplier_id
                    primary_link = link
                    target_sku = link.sku

                elif action == 'create_material':
                    # Create entirely new material (reuse existing create logic)
                    new_unit = item.get('new_unit', 'kg')
                    new_supplier_id = item.get('new_supplier_id')

                    material = RawMaterial(
                        name=name,
                        category=default_category,
                        unit=new_unit
                    )
                    db.session.add(material)
                    db.session.flush()
                    stats['materials_created'] += 1
                    current_app.logger.info(f"Created new material from ambiguous: {name} (ID: {material.id})")

                    if new_supplier_id:
                        try:
                            new_supplier_id = int(new_supplier_id)
                            supplier = Supplier.query.get(new_supplier_id)
                            if supplier:
                                new_link = RawMaterialSupplier(
                                    raw_material_id=material.id,
                                    supplier_id=supplier.id,
                                    cost_per_unit=0,
                                    sku=sku,
                                    is_primary=True
                                )
                                db.session.add(new_link)
                                supplier_id = supplier.id
                                target_sku = sku
                        except (ValueError, TypeError):
                            pass
                else:
                    # No action selected for ambiguous item
                    stats['skipped'] += 1
                    continue

            # Handle not_found items with action
            elif status == 'not_found':
                if action == 'link':
                    # Link to existing material and add alternative name
                    link_material_id = item.get('link_material_id')
                    if not link_material_id:
                        errors.append(f"{name}: No material selected for linking")
                        stats['errors'] += 1
                        continue

                    try:
                        link_material_id = int(link_material_id)
                    except (ValueError, TypeError):
                        errors.append(f"{name}: Invalid link material ID")
                        stats['errors'] += 1
                        continue

                    material = RawMaterial.query.filter_by(id=link_material_id, is_deleted=False).first()
                    if not material:
                        errors.append(f"{name}: Linked material not found")
                        stats['errors'] += 1
                        continue

                    # Add alternative name if not already exists
                    existing_alt = RawMaterialAlternativeName.query.filter_by(alternative_name=name).first()
                    if not existing_alt and name != material.name:
                        alt_name_entry = RawMaterialAlternativeName(
                            raw_material_id=material.id,
                            alternative_name=name
                        )
                        db.session.add(alt_name_entry)
                        stats['alt_names_added'] += 1
                        current_app.logger.info(f"Added alternative name '{name}' for material '{material.name}'")

                elif action == 'create':
                    # Create new material
                    new_unit = item.get('new_unit', 'kg')
                    new_supplier_id = item.get('new_supplier_id')

                    material = RawMaterial(
                        name=name,
                        category=default_category,
                        unit=new_unit
                    )
                    db.session.add(material)
                    db.session.flush()
                    stats['materials_created'] += 1
                    current_app.logger.info(f"Created new material: {name} (ID: {material.id})")

                    # Create supplier link if supplier provided
                    if new_supplier_id:
                        try:
                            new_supplier_id = int(new_supplier_id)
                            supplier = Supplier.query.get(new_supplier_id)
                            if supplier:
                                new_link = RawMaterialSupplier(
                                    raw_material_id=material.id,
                                    supplier_id=supplier.id,
                                    cost_per_unit=0,
                                    sku=sku,
                                    is_primary=True
                                )
                                db.session.add(new_link)
                                supplier_id = supplier.id
                                target_sku = sku
                        except (ValueError, TypeError):
                            pass
                else:
                    # No action selected for not_found item
                    stats['skipped'] += 1
                    continue

            if not material:
                stats['skipped'] += 1
                continue

            # Find primary link for cost
            if not supplier_id:
                primary_link = next((l for l in material.supplier_links if l.is_primary), None)
                if not primary_link and material.supplier_links:
                    primary_link = material.supplier_links[0]
                if primary_link:
                    supplier_id = primary_link.supplier_id
                    target_sku = primary_link.sku
            elif not primary_link:
                # Find link by supplier_id and target_sku
                if target_sku:
                    primary_link = RawMaterialSupplier.query.filter_by(
                        raw_material_id=material.id,
                        supplier_id=supplier_id,
                        sku=target_sku
                    ).first()
                if not primary_link:
                    primary_link = RawMaterialSupplier.query.filter_by(
                        raw_material_id=material.id,
                        supplier_id=supplier_id
                    ).first()

            # Calculate current system stock (SKU-specific if available)
            if supplier_id:
                if target_sku:
                    system_stock = calculate_supplier_stock(material.id, supplier_id, sku=target_sku)
                else:
                    system_stock = calculate_supplier_stock(material.id, supplier_id)
            else:
                system_stock = calculate_total_material_stock(material.id)

            # Create StockLog with 'set' action (including SKU)
            stock_log = StockLog(
                raw_material_id=material.id,
                supplier_id=supplier_id,
                sku=target_sku,
                action_type='set',
                quantity=quantity,
                timestamp=audit_datetime
            )
            db.session.add(stock_log)
            db.session.flush()

            # Calculate variance
            variance = quantity - system_stock
            cost_per_unit = primary_link.cost_per_unit if primary_link else 0
            variance_cost = variance * (cost_per_unit or 0)

            # Create StockAudit
            audit = StockAudit(
                raw_material_id=material.id,
                system_quantity=system_stock,
                physical_quantity=quantity,
                variance=variance,
                variance_cost=variance_cost,
                auditor_name=_('Excel Import'),
                stock_log_id=stock_log.id
            )
            db.session.add(audit)
            stats['audits_created'] += 1

            current_app.logger.info(
                f"Stock audit created: material={material.name}, sku={target_sku}, "
                f"system={system_stock}, physical={quantity}, variance={variance}"
            )

        # Commit all changes
        db.session.commit()

        # Log audit event
        audit_details = f"Imported {stats['audits_created']} stock audits for date {audit_date_str}"
        if stats['materials_created'] > 0:
            audit_details += f", created {stats['materials_created']} materials"
        if stats['sku_variants_created'] > 0:
            audit_details += f", created {stats['sku_variants_created']} SKU variants"
        if stats['alt_names_added'] > 0:
            audit_details += f", added {stats['alt_names_added']} alternative names"
        log_audit("IMPORT", "StockAudit", details=audit_details)

        # Clean up temp file
        temp_file_path = session.pop('stock_audit_temp_file', None)
        session.pop('stock_audit_date', None)
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception:
                pass

        # Show results
        if stats['audits_created'] > 0:
            flash(_('Created {} stock audits successfully').format(stats['audits_created']), 'success')
        if stats['materials_created'] > 0:
            flash(_('Created {} new materials').format(stats['materials_created']), 'info')
        if stats['sku_variants_created'] > 0:
            flash(_('Created {} SKU variants').format(stats['sku_variants_created']), 'info')
        if stats['alt_names_added'] > 0:
            flash(_('Added {} alternative names').format(stats['alt_names_added']), 'info')
        if stats['skipped'] > 0:
            flash(_('Skipped {} items').format(stats['skipped']), 'info')
        if errors:
            flash(_('Errors: {}').format(', '.join(errors[:3])), 'warning')

    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Stock audit confirm error: {str(e)}")
        flash(_('Error processing stock audit: {}').format(str(e)), 'error')
        return redirect(url_for('stock_audit_upload.upload_stock_audit'))

    return redirect(url_for('main.stock_audits'))
