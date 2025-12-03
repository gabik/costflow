from flask import Blueprint, render_template, request, redirect
import pandas as pd
from ..models import db, RawMaterial, StockLog, Category
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
                        
                    # Check DB
                    material = RawMaterial.query.filter_by(name=name).first()
                    
                    status = 'new'
                    current_price = None
                    price_differs = False
                    
                    if material:
                        status = 'exists'
                        current_price = material.cost_per_unit
                        if abs(current_price - price) > 0.01:
                            price_differs = True
                    
                    review_data.append({
                        'name': name,
                        'quantity': quantity,
                        'new_price': price,
                        'status': status,
                        'current_price': current_price,
                        'price_differs': price_differs
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
        
        material = RawMaterial.query.filter_by(name=name).first()
        
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
            
            # Initial stock log
            log = StockLog(
                raw_material_id=material.id,
                action_type='set',
                quantity=quantity
            )
            db.session.add(log)
            
        else:
            # Update existing
            if update_price:
                material.cost_per_unit = new_price
            
            # Add stock log
            log = StockLog(
                raw_material_id=material.id,
                action_type='add',
                quantity=quantity
            )
            db.session.add(log)
                                                                                                                                                                
    log_audit("IMPORT", "Inventory", details=f"Imported {len(items_data)} items from Excel.")
    db.session.commit()
    return redirect(url_for('main.raw_materials'))
