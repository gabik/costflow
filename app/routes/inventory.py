import pandas as pd
from datetime import datetime
from flask import render_template, request, redirect, url_for
from ..models import db, RawMaterial, StockLog, ProductionLog, Product, Premake, Category, StockAudit, Packaging
from ..utils import get_or_create_general_category, log_audit, units_list
from . import main_blueprint

# ----------------------------
# Raw Materials Management
# ----------------------------
@main_blueprint.route('/raw_materials')
def raw_materials():
    materials = RawMaterial.query.all()

    for material in materials:
        # Start with the last "Set Stock" log
        last_set_log = StockLog.query.filter_by(raw_material_id=material.id, action_type='set') \
            .order_by(StockLog.timestamp.desc()).first()
        stock = last_set_log.quantity if last_set_log else 0

        # Add "Add Stock" logs after the last "Set Stock"
        add_logs = StockLog.query.filter(
            StockLog.raw_material_id == material.id,
            StockLog.action_type == 'add',
            StockLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
        ).all()
        for log in add_logs:
            stock += log.quantity

        # Subtract raw materials used in produced products
        production_logs = ProductionLog.query.filter(
            ProductionLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
        ).all()

        for production in production_logs:
            if production.product_id:
                product = Product.query.get(production.product_id)
                if product:
                    for component in product.components:
                        if component.component_type == 'raw_material' and component.component_id == material.id:
                            stock -= component.quantity * production.quantity_produced
            elif production.premake_id:
                premake = Premake.query.get(production.premake_id)
                if premake:
                    for component in premake.components:
                        if component.component_type == 'raw_material' and component.component_id == material.id:
                            stock -= component.quantity * production.quantity_produced

        # Attach calculated stock to material object
        material.current_stock = stock

    return render_template('raw_materials.html', materials=materials)

@main_blueprint.route('/raw_materials/add', methods=['GET', 'POST'])
def add_raw_material():
    if request.method == 'POST':
        name = request.form['name']
        category_id = request.form.get('category') # form field is 'category'
        if not category_id:
            category_id = get_or_create_general_category('raw_material')
            
        unit = request.form['unit']
        cost_per_unit = float(request.form['cost_per_unit'])
        stock = request.form.get('stock', 0) # Optional initial stock

        category = Category.query.get(category_id)

        new_material = RawMaterial(name=name, category=category, unit=unit, cost_per_unit=cost_per_unit)
        db.session.add(new_material)
        db.session.flush() # Get ID for stock log

        if stock:
            initial_stock_log = StockLog(
                raw_material_id=new_material.id,
                action_type='set',
                quantity=float(stock)
            )
            db.session.add(initial_stock_log)

        db.session.commit()

        # Handle modal submissions
        if request.referrer and 'products/add' in request.referrer:
            return redirect(request.referrer)

        return redirect(url_for('main.raw_materials'))

    categories = Category.query.filter_by(type='raw_material').all()
    return render_template('add_or_edit_raw_material.html', material=None, categories=categories, units=units_list)

@main_blueprint.route('/raw_materials/edit/<int:material_id>', methods=['GET', 'POST'])
def edit_raw_material(material_id):
    material = RawMaterial.query.get_or_404(material_id)
    if request.method == 'POST':
        material.name = request.form['name']
        category_id = request.form.get('category')
        if not category_id:
            category_id = get_or_create_general_category('raw_material')
            
        category = Category.query.get(category_id)

        material.category = category
        material.unit = request.form['unit']
        material.cost_per_unit = float(request.form['cost_per_unit'])
        
        db.session.commit()
        return redirect(url_for('main.raw_materials'))

    categories = Category.query.filter_by(type='raw_material').all()
    return render_template('add_or_edit_raw_material.html', material=material, categories=categories, units=units_list)

@main_blueprint.route('/raw_materials/delete/<int:material_id>', methods=['POST'])
def delete_raw_material(material_id):
    material = RawMaterial.query.get_or_404(material_id)
    
    # Delete related StockLogs
    StockLog.query.filter_by(raw_material_id=material.id).delete()
    
    # Delete related ProductComponents - Circular import issue if imported from models? No, models import is fine.
    # ProductComponent needs to be imported. 
    from ..models import ProductComponent 
    ProductComponent.query.filter_by(component_id=material.id, component_type='raw_material').delete()
    
    db.session.delete(material)
    log_audit("DELETE", "RawMaterial", material_id, f"Deleted raw material {material.name}")
    db.session.commit()
    return redirect(url_for('main.raw_materials'))

@main_blueprint.route('/raw_materials/update_stock', methods=['POST'])
def update_stock():
    raw_material_id = request.form['raw_material_id']
    quantity = float(request.form['quantity'])
    action_type = request.form['action_type']  # 'add' or 'set'
    auditor_name = request.form.get('auditor_name', '')  # Get auditor name if provided

    if action_type not in ['add', 'set']:
        return "Invalid action type", 400

    material = RawMaterial.query.get(raw_material_id)

    # If action_type is 'set', calculate current stock and create audit record
    if action_type == 'set':
        # Calculate current system stock before the update
        last_set_log = StockLog.query.filter_by(raw_material_id=raw_material_id, action_type='set') \
            .order_by(StockLog.timestamp.desc()).first()
        system_stock = last_set_log.quantity if last_set_log else 0

        # Add "Add Stock" logs after the last "Set Stock"
        add_logs = StockLog.query.filter(
            StockLog.raw_material_id == raw_material_id,
            StockLog.action_type == 'add',
            StockLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
        ).all()
        for log in add_logs:
            system_stock += log.quantity

        # Subtract raw materials used in produced products
        production_logs = ProductionLog.query.filter(
            ProductionLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
        ).all()

        for production in production_logs:
            product = Product.query.get(production.product_id)
            for component in product.components:
                if component.component_type == 'raw_material' and component.component_id == int(raw_material_id):
                    system_stock -= component.quantity * production.quantity_produced

        # Calculate variance and create audit record
        variance = quantity - system_stock
        variance_cost = variance * material.cost_per_unit

        # Create the stock log entry first
        stock_log = StockLog(raw_material_id=raw_material_id, action_type=action_type, quantity=quantity)
        db.session.add(stock_log)
        db.session.flush()  # Flush to get the stock_log.id

        # Create stock audit record
        stock_audit = StockAudit(
            raw_material_id=raw_material_id,
            system_quantity=system_stock,
            physical_quantity=quantity,
            variance=variance,
            variance_cost=variance_cost,
            auditor_name=auditor_name,
            stock_log_id=stock_log.id
        )
        db.session.add(stock_audit)

        log_audit("STOCK_AUDIT", "RawMaterial", raw_material_id,
                 f"Physical count: {quantity}, System: {system_stock:.2f}, Variance: {variance:.2f} (Cost: {variance_cost:.2f})")
    else:
        # For 'add' action, just create the stock log
        stock_log = StockLog(raw_material_id=raw_material_id, action_type=action_type, quantity=quantity)
        db.session.add(stock_log)
        log_audit("UPDATE_STOCK", "RawMaterial", raw_material_id, f"{action_type} {quantity}")

    db.session.commit()
    return redirect(url_for('main.raw_materials'))

# ----------------------------
# Packaging Management
# ----------------------------
@main_blueprint.route('/packaging', methods=['GET'])
def packaging():
    all_packaging = Packaging.query.all()
    return render_template('packaging.html', packaging=all_packaging)

@main_blueprint.route('/packaging/add', methods=['GET', 'POST'])
def add_packaging():
    if request.method == 'POST':
        name = request.form['name']
        quantity_per_package = request.form['quantity_per_package']
        price_per_package = request.form['price_per_package']

        new_packaging = Packaging(
            name=name,
            quantity_per_package=int(quantity_per_package),
            price_per_package=float(price_per_package)
        )
        db.session.add(new_packaging)
        db.session.commit()
        return redirect(url_for('main.packaging'))

    return render_template('add_or_edit_packaging.html', packaging=None)

@main_blueprint.route('/packaging/edit/<int:packaging_id>', methods=['GET', 'POST'])
def edit_packaging(packaging_id):
    packaging_item = Packaging.query.get_or_404(packaging_id)
    if request.method == 'POST':
        packaging_item.name = request.form['name']
        packaging_item.quantity_per_package = int(request.form['quantity_per_package'])
        packaging_item.price_per_package = float(request.form['price_per_package'])
        db.session.commit()
        return redirect(url_for('main.packaging'))

    return render_template('add_or_edit_packaging.html', packaging=packaging_item)

@main_blueprint.route('/packaging/delete/<int:packaging_id>', methods=['POST'])
def delete_packaging(packaging_id):
    packaging_item = Packaging.query.get_or_404(packaging_id)
    db.session.delete(packaging_item)
    db.session.commit()
    return redirect(url_for('main.packaging'))

# ----------------------------
# Categories Management
# ----------------------------
@main_blueprint.route('/categories', methods=['GET', 'POST'])
def categories():
    current_type = request.args.get('type', 'raw_material')
    
    if request.method == 'POST':
        name = request.form['name']
        type_val = request.form.get('type', 'raw_material')
        
        if not Category.query.filter_by(name=name, type=type_val).first():
            new_category = Category(name=name, type=type_val)
            db.session.add(new_category)
            db.session.commit()
        return redirect(url_for('main.categories', type=type_val))
        
    all_categories = Category.query.filter_by(type=current_type).all()
    return render_template('categories.html', categories=all_categories, current_type=current_type)

@main_blueprint.route('/categories/edit/<int:category_id>', methods=['GET', 'POST'])
def edit_categories(category_id):
    category_item = Category.query.get_or_404(category_id)
    if request.method == 'POST':
        category_item.name = request.form['name']
        db.session.commit()
        return redirect(url_for('main.categories'))
    return render_template('categories.html', category=category_item)

@main_blueprint.route('/categories/delete/<int:category_id>', methods=['POST'])
def delete_category(category_id):
    category_item = Category.query.get_or_404(category_id)
    db.session.delete(category_item)
    db.session.commit()
    return redirect(url_for('main.categories'))

@main_blueprint.route('/categories/add_from_modal', methods=['POST'])
def add_category_from_modal():
    name = request.form['name']
    type_val = request.form.get('type', 'raw_material')
    
    if not name.strip():
        return redirect(request.referrer or url_for('main.index'))

    if not Category.query.filter_by(name=name, type=type_val).first():
        new_category = Category(name=name.strip(), type=type_val)
        db.session.add(new_category)
        db.session.commit()

    # Redirect back to the previous page
    return redirect(request.referrer or url_for('main.index'))

# ----------------------------
# Bulk Inventory Upload
# ----------------------------
@main_blueprint.route('/inventory/upload', methods=['GET', 'POST'])
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

@main_blueprint.route('/inventory/confirm', methods=['POST'])
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
