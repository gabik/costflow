from datetime import datetime, date
import os
import tempfile
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, session
from flask_babel import gettext as _
import pandas as pd
from ..models import db, RawMaterial, StockLog, StockAudit, RawMaterialSupplier, RawMaterialAlternativeName
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

        # Match material
        material = None
        matched_by = None
        primary_supplier = None
        primary_link = None

        # Step 1: Try SKU match
        if sku:
            material_supplier = RawMaterialSupplier.query.filter_by(sku=sku).first()
            if material_supplier and not material_supplier.raw_material.is_deleted:
                material = material_supplier.raw_material
                matched_by = 'sku'
                primary_link = material_supplier

        # Step 2: Try name match
        if not material:
            material = RawMaterial.query.filter_by(name=name, is_deleted=False).first()
            if material:
                matched_by = 'name'

        # Step 3: Try alternative name match
        if not material:
            alt_name = RawMaterialAlternativeName.query.filter_by(alternative_name=name).first()
            if alt_name and not alt_name.raw_material.is_deleted:
                material = alt_name.raw_material
                matched_by = 'alt_name'

        # Get primary supplier and current stock
        current_stock = 0
        supplier_id = None
        cost_per_unit = 0

        if material:
            # Find primary supplier
            if not primary_link:
                primary_link = next((l for l in material.supplier_links if l.is_primary), None)
                if not primary_link and material.supplier_links:
                    primary_link = material.supplier_links[0]

            if primary_link:
                supplier_id = primary_link.supplier_id
                cost_per_unit = primary_link.cost_per_unit or 0
                current_stock = calculate_supplier_stock(material.id, supplier_id)
            else:
                current_stock = calculate_total_material_stock(material.id)

        variance = quantity - current_stock if material else None

        review_data.append({
            'name': name,
            'sku': sku,
            'material_id': material.id if material else None,
            'material_name': material.name if material else None,
            'matched_by': matched_by,
            'quantity': quantity,
            'current_stock': round(current_stock, 2) if material else None,
            'variance': round(variance, 2) if variance is not None else None,
            'status': 'found' if material else 'not_found',
            'supplier_id': supplier_id,
            'cost_per_unit': cost_per_unit,
            'unit': material.unit if material else None,
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

                # Clean up temp file reference (will be deleted after confirm)
                return render_template('upload_stock_audit.html',
                                       review_data=review_data,
                                       skipped_rows=skipped_rows,
                                       audit_date=audit_date_str,
                                       today_date=today_date)

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

        return render_template('upload_stock_audit.html',
                               review_data=review_data,
                               skipped_rows=skipped_rows,
                               audit_date=audit_date_str,
                               today_date=date.today().isoformat(),
                               selected_sheet=sheet_name)

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

    # Process items
    stats = {
        'audits_created': 0,
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

            # Check if material was found
            material_id = item.get('material_id')
            if not material_id:
                stats['skipped'] += 1
                continue

            try:
                material_id = int(material_id)
                quantity = float(item.get('quantity', 0))
            except (ValueError, TypeError):
                errors.append(f"Item {index}: Invalid data")
                stats['errors'] += 1
                continue

            material = RawMaterial.query.filter_by(id=material_id, is_deleted=False).first()
            if not material:
                errors.append(f"Item {index}: Material not found")
                stats['errors'] += 1
                continue

            # Get supplier info
            supplier_id = item.get('supplier_id')
            if supplier_id:
                try:
                    supplier_id = int(supplier_id)
                except (ValueError, TypeError):
                    supplier_id = None

            # Find primary link for cost
            primary_link = None
            if supplier_id:
                primary_link = RawMaterialSupplier.query.filter_by(
                    raw_material_id=material_id,
                    supplier_id=supplier_id
                ).first()
            if not primary_link:
                primary_link = next((l for l in material.supplier_links if l.is_primary), None)
                if not primary_link and material.supplier_links:
                    primary_link = material.supplier_links[0]
                if primary_link:
                    supplier_id = primary_link.supplier_id

            # Calculate current system stock
            if supplier_id:
                system_stock = calculate_supplier_stock(material_id, supplier_id)
            else:
                system_stock = calculate_total_material_stock(material_id)

            # Create StockLog with 'set' action
            stock_log = StockLog(
                raw_material_id=material_id,
                supplier_id=supplier_id,
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
                raw_material_id=material_id,
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
                f"Stock audit created: material={material.name}, "
                f"system={system_stock}, physical={quantity}, variance={variance}"
            )

        # Commit all changes
        db.session.commit()

        # Log audit event
        log_audit("IMPORT", "StockAudit",
                  details=f"Imported {stats['audits_created']} stock audits for date {audit_date_str}")

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
