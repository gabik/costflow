from flask import Blueprint, render_template, request, redirect, url_for
import pandas as pd
from ..models import db, RawMaterial, StockLog, Category, RawMaterialSupplier, Supplier
from .utils import log_audit

inventory_blueprint = Blueprint('inventory', __name__)

# ----------------------------
# Bulk Inventory Upload
# ----------------------------
@inventory_blueprint.route('/inventory/upload', methods=['GET', 'POST'])
def upload_inventory():
    review_data = None
    
    if request.method == 'POST':
        if 'inventory_file' not in request.files:
            return redirect(request.url)
            
        file = request.files['inventory_file']
        if file.filename == '':
            return redirect(request.url)

        if file:
            try:
                df = pd.read_excel(file)
                
                # Normalize column names (strip whitespace)
                df.columns = df.columns.str.strip()
                
                # Expected columns
                col_name = 'שם מוצר'
                col_qty = "סה''כ כמות"
                col_price = 'מחיר ממוצע'
                col_sku = 'מק"ט'  # SKU column (optional)
                col_supplier = 'ספק'  # Supplier column (optional)

                review_data = []

                for index, row in df.iterrows():
                    if pd.isna(row[col_name]):
                        continue

                    name = str(row[col_name]).strip()
                    try:
                        quantity = float(row[col_qty])
                        price = float(row[col_price])
                    except (ValueError, KeyError):
                        continue # Skip invalid rows

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
                                current_price = material.cost_per_unit
                        else:
                            current_price = material.cost_per_unit

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
                    
            except Exception as e:
                print(f"Error processing Excel: {e}")
                return f"Error processing file: {e}", 400

    return render_template('upload_inventory.html', review_data=review_data)

@inventory_blueprint.route('/inventory/confirm', methods=['POST'])
def confirm_inventory_upload():
    # Parse the complex form data (items[0][name], items[0][quantity], etc.)
    # Flask doesn't parse nested dicts automatically, so we iterate manually.
    
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

    # Process items
    # Default category for new items (or create a 'General' one)
    default_category = Category.query.first()
    if not default_category:
        default_category = Category(name="כללי")
        db.session.add(default_category)
        db.session.commit()

    for index, item in items_data.items():
        name = item['name']
        quantity = float(item['quantity'])
        new_price = float(item['new_price'])
        update_price = item.get('update_price') == 'yes'
        material_id = item.get('material_id')
        supplier_id = item.get('supplier_id')

        # Get material by ID if provided, otherwise by name (exclude deleted materials)
        if material_id:
            material = RawMaterial.query.filter_by(id=int(material_id), is_deleted=False).first()
        else:
            material = RawMaterial.query.filter_by(name=name, is_deleted=False).first()

        if not material:
            # Create new
            material = RawMaterial(
                name=name,
                category=default_category,
                unit='kg', # Default unit
                cost_per_unit=new_price
            )
            db.session.add(material)
            db.session.flush() # Get ID

            # Initial stock log with supplier if provided
            log = StockLog(
                raw_material_id=material.id,
                supplier_id=int(supplier_id) if supplier_id else None,
                action_type='set',
                quantity=quantity
            )
            db.session.add(log)

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
            elif update_price:
                # Update general price
                material.cost_per_unit = new_price

            # Add stock log with supplier
            log = StockLog(
                raw_material_id=material.id,
                supplier_id=int(supplier_id) if supplier_id else None,
                action_type='add',
                quantity=quantity
            )
            db.session.add(log)
                                                                                                                                                                
    log_audit("IMPORT", "Inventory", details=f"Imported {len(items_data)} items from Excel.")
    db.session.commit()
    return redirect(url_for('main.raw_materials'))
