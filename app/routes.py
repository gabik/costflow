import os
import pandas as pd
import json
import io
from datetime import datetime, date, timedelta
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, current_app, send_file
from sqlalchemy import func
from .models import db, RawMaterial, Labor, Packaging, Product, ProductComponent, Category, StockLog, ProductionLog, WeeklyLaborCost, WeeklyLaborEntry, WeeklyProductSales, AuditLog

main_blueprint = Blueprint('main', __name__)

# Predefined units for raw materials
units_list = ["g", "kg", "ml", "l", "piece"]

def log_audit(action, target_type, target_id=None, details=None):
    try:
        log = AuditLog(
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details
        )
        db.session.add(log)
    except Exception as e:
        print(f"Failed to log audit: {e}")

# Homepage - Weekly Dashboard
@main_blueprint.route('/')
def index():
    # 1. Fetch all weeks for dropdown
    all_weeks = WeeklyLaborCost.query.order_by(WeeklyLaborCost.week_start_date.desc()).all()
    
    selected_week_id = request.args.get('week_id')
    selected_week = None
    
    if selected_week_id:
        selected_week = WeeklyLaborCost.query.get(selected_week_id)
    elif all_weeks:
        selected_week = all_weeks[0]  # Default to latest

    report_data = []
    total_revenue = 0
    total_cogs = 0
    total_inventory_usage = 0
    total_labor = 0
    
    if selected_week:
        week_start = selected_week.week_start_date
        week_end = week_start + timedelta(days=6)
        total_labor = selected_week.total_cost
        
        # Fetch Production Logs
        logs = ProductionLog.query.filter(
            func.date(ProductionLog.timestamp) >= week_start,
            func.date(ProductionLog.timestamp) <= week_end
        ).all()
        
        # Map Production
        product_production = {}
        for log in logs:
            product_production[log.product_id] = product_production.get(log.product_id, 0) + log.quantity_produced

        # Map Sales
        product_sales = {s.product_id: s.quantity_sold for s in selected_week.sales}
        
        # Iterate Products
        all_products = Product.query.all()
        for product in all_products:
            produced_qty = product_production.get(product.id, 0)
            sold_qty = product_sales.get(product.id, 0)
            
            if produced_qty == 0 and sold_qty == 0:
                continue
                
            # Calculate Prime Cost (Materials + Packaging)
            prime_cost = 0
            for component in product.components:
                if component.component_type == 'raw_material' and component.material:
                    prime_cost += component.quantity * component.material.cost_per_unit
                elif component.component_type == 'packaging' and component.packaging:
                    prime_cost += component.quantity * component.packaging.price_per_unit
            
            if product.products_per_recipe > 0:
                prime_cost_per_unit = prime_cost / product.products_per_recipe
            else:
                prime_cost_per_unit = 0
            
            # Financials
            revenue = sold_qty * product.selling_price_per_unit
            cogs = sold_qty * prime_cost_per_unit
            gross_profit = revenue - cogs
            
            # Inventory Usage Value (what we made)
            inventory_usage_value = produced_qty * prime_cost_per_unit
            
            total_revenue += revenue
            total_cogs += cogs
            total_inventory_usage += inventory_usage_value
            
            report_data.append({
                'product_name': product.name,
                'product_image': product.image_filename,
                'produced_qty': produced_qty,
                'sold_qty': sold_qty,
                'prime_cost': prime_cost_per_unit,
                'selling_price': product.selling_price_per_unit,
                'revenue': revenue,
                'gross_profit': gross_profit
            })

    net_profit = total_revenue - total_cogs - total_labor

    return render_template('index.html', 
                           weeks=all_weeks, 
                           selected_week=selected_week, 
                           report_data=report_data,
                           total_revenue=total_revenue,
                           total_cogs=total_cogs,
                           total_labor=total_labor,
                           total_inventory_usage=total_inventory_usage,
                           net_profit=net_profit,
                           currency_symbol='₪')

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
            product = Product.query.get(production.product_id)
            for component in product.components:
                if component.component_type == 'raw_material' and component.component_id == material.id:
                    stock -= component.quantity * production.quantity_produced

        # Attach calculated stock to material object
        material.current_stock = stock

    return render_template('raw_materials.html', materials=materials)

@main_blueprint.route('/raw_materials/add', methods=['GET', 'POST'])
def add_raw_material():
    if request.method == 'POST':
        name = request.form['name']
        category_id = request.form['category']
        unit = request.form['unit']
        cost_per_unit = float(request.form['cost_per_unit'])
        stock = request.form.get('stock', 0) # Optional initial stock

        category = Category.query.get(category_id)
        if not category:
            return "Invalid category selected", 400

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

    categories = Category.query.all()
    return render_template('add_or_edit_raw_material.html', material=None, categories=categories, units=units_list)

@main_blueprint.route('/raw_materials/edit/<int:material_id>', methods=['GET', 'POST'])
def edit_raw_material(material_id):
    material = RawMaterial.query.get_or_404(material_id)
    if request.method == 'POST':
        material.name = request.form['name']
        category = Category.query.get(request.form['category'])
        if not category:
            return "Invalid category selected", 400

        material.category = category
        material.unit = request.form['unit']
        material.cost_per_unit = float(request.form['cost_per_unit'])
        
        # Note: Stock is managed via logs, not directly editable here to preserve history
        
        db.session.commit()
        return redirect(url_for('main.raw_materials'))

    categories = Category.query.all()
    return render_template('add_or_edit_raw_material.html', material=material, categories=categories, units=units_list)

@main_blueprint.route('/raw_materials/delete/<int:material_id>', methods=['POST'])
def delete_raw_material(material_id):
    material = RawMaterial.query.get_or_404(material_id)
    
    # Delete related StockLogs
    StockLog.query.filter_by(raw_material_id=material.id).delete()
    
    # Delete related ProductComponents
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

    if action_type not in ['add', 'set']:
        return "Invalid action type", 400

    stock_log = StockLog(raw_material_id=raw_material_id, action_type=action_type, quantity=quantity)
    db.session.add(stock_log)
    
    log_audit("UPDATE_STOCK", "RawMaterial", raw_material_id, f"{action_type} {quantity}")
    db.session.commit()

    return redirect(url_for('main.raw_materials'))

# ----------------------------
# Labor Management
# ----------------------------
@main_blueprint.route('/labor')
def labor():
    labor_list = Labor.query.all()
    return render_template('labor.html', all_labor=labor_list)

@main_blueprint.route('/labor/add', methods=['GET', 'POST'])
def add_labor():
    if request.method == 'POST':
        name = request.form['name']
        phone_number = request.form.get('phone_number')
        
        # Handle single total input
        total_hourly_rate = float(request.form['total_hourly_rate'])
        base_hourly_rate = total_hourly_rate
        additional_hourly_rate = 0.0

        new_labor = Labor(name=name, phone_number=phone_number, base_hourly_rate=base_hourly_rate, additional_hourly_rate=additional_hourly_rate)
        db.session.add(new_labor)
        db.session.commit()
        
        log_audit("CREATE", "Labor", new_labor.id, f"Created labor entry {new_labor.name}")

        # Handle modal submissions
        if request.referrer and 'products/add' in request.referrer:
            return redirect(request.referrer)
    return redirect(url_for('main.labor'))

@main_blueprint.route('/labor/edit/<int:labor_id>', methods=['GET', 'POST'])

def edit_labor(labor_id):

    labor_item = Labor.query.get_or_404(labor_id)

    if request.method == 'POST':

        labor_item.name = request.form['name']

        labor_item.phone_number = request.form.get('phone_number')

        

        total_hourly_rate = float(request.form['total_hourly_rate'])

        labor_item.base_hourly_rate = total_hourly_rate

        labor_item.additional_hourly_rate = 0.0



        db.session.commit()

        log_audit("UPDATE", "Labor", labor_item.id, f"Updated labor entry {labor_item.name}")

        return redirect(url_for('main.labor'))
    return render_template('add_or_edit_labor.html', labor=labor_item)

@main_blueprint.route('/labor/delete/<int:labor_id>', methods=['POST'])
def delete_labor(labor_id):
    labor_item = Labor.query.get_or_404(labor_id)
    db.session.delete(labor_item)
    db.session.commit()
    log_audit("DELETE", "Labor", labor_id, f"Deleted labor entry {labor_item.name}")
    return redirect(url_for('main.labor'))

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
# Products Management
# ----------------------------
@main_blueprint.route('/products')
def products():
    products = Product.query.all()
    return render_template('products.html', products=products)

@main_blueprint.route('/products/add', methods=['GET', 'POST'])
def add_product():
    if request.method == 'POST':
        # Extract product-level data
        name = request.form['name']
        products_per_recipe = request.form['products_per_recipe']
        selling_price_per_unit = request.form['selling_price_per_unit']
        
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename:
                filename = secure_filename(file.filename)
                # Ensure unique filename to prevent overwrites? For simple MVP, simple secure is okay, or prepend timestamp.
                # Let's prepend timestamp for uniqueness.
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                filename = f"{timestamp}_{filename}"
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                image_filename = filename

        # Create a new Product entry
        product = Product(
            name=name,
            products_per_recipe=int(products_per_recipe),
            selling_price_per_unit=float(selling_price_per_unit),
            image_filename=image_filename
        )
        db.session.add(product)
        db.session.flush()
        log_audit("CREATE", "Product", product.id, f"Created product {product.name}")
        db.session.commit()  # Save product to get its ID

        # Process raw materials
        raw_materials = request.form.getlist('raw_material[]')
        raw_material_quantities = request.form.getlist('raw_material_quantity[]')
        for material_id, quantity in zip(raw_materials, raw_material_quantities):
            component = ProductComponent(
                product_id=product.id,
                component_type='raw_material',
                component_id=int(material_id),
                quantity=float(quantity)
            )
            db.session.add(component)

        # Process packaging
        packaging = request.form.getlist('packaging[]')
        packaging_quantities = request.form.getlist('packaging_quantity[]')
        for packaging_id, quantity in zip(packaging, packaging_quantities):
            component = ProductComponent(
                product_id=product.id,
                component_type='packaging',
                component_id=int(packaging_id),
                quantity=float(quantity)
            )
            db.session.add(component)

        # Process labor
        labor = request.form.getlist('labor[]')
        labor_hours = request.form.getlist('labor_hours[]')
        for labor_id, hours in zip(labor, labor_hours):
            component = ProductComponent(
                product_id=product.id,
                component_type='labor',
                component_id=int(labor_id),
                quantity=float(hours)
            )
            db.session.add(component)

        db.session.commit()  # Save all components
        return redirect(url_for('main.products'))

    # For GET requests, load the data required for the form
    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    categories = Category.query.all()
    all_packaging = [p.to_dict() for p in Packaging.query.all()]
    all_labor = [labor_item.to_dict() for labor_item in Labor.query.all()]
    return render_template(
        'add_or_edit_product.html',
        product=None,
        product_json=None,
        all_raw_materials=all_raw_materials,
        all_packaging=all_packaging,
        all_labor=all_labor,
        categories=categories
    )

@main_blueprint.route('/products/<int:product_id>', methods=['GET'])
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)

    # Retrieve raw materials used in the product
    raw_materials = [
        {
            'name': RawMaterial.query.get(component.component_id).name,
            'quantity': component.quantity,
            'price_per_unit': RawMaterial.query.get(component.component_id).cost_per_unit,
            'price_per_recipe': component.quantity * RawMaterial.query.get(component.component_id).cost_per_unit,
            'price_per_product': (component.quantity * RawMaterial.query.get(component.component_id).cost_per_unit) / product.products_per_recipe
        }
        for component in ProductComponent.query.filter_by(product_id=product_id, component_type='raw_material')
    ]

    # Retrieve labor costs
    labor_costs = [
        {
            'name': Labor.query.get(component.component_id).name,
            'hours': component.quantity,
            'price_per_hour': Labor.query.get(component.component_id).total_hourly_rate,
            'price_per_recipe': component.quantity * Labor.query.get(component.component_id).total_hourly_rate,
            'price_per_product': (component.quantity * Labor.query.get(component.component_id).total_hourly_rate) / product.products_per_recipe
        }
        for component in ProductComponent.query.filter_by(product_id=product_id, component_type='labor')
    ]

    # Retrieve packaging costs
    packaging_costs = [
        {
            'name': Packaging.query.get(component.component_id).name,
            'quantity': component.quantity,
            'price_per_package': Packaging.query.get(component.component_id).price_per_package,
            'price_per_unit': Packaging.query.get(component.component_id).price_per_package / Packaging.query.get(component.component_id).quantity_per_package,
            'price_per_recipe': component.quantity * (Packaging.query.get(component.component_id).price_per_package / Packaging.query.get(component.component_id).quantity_per_package),
            'price_per_product': (component.quantity * (Packaging.query.get(component.component_id).price_per_package / Packaging.query.get(component.component_id).quantity_per_package)) / product.products_per_recipe
        }
        for component in ProductComponent.query.filter_by(product_id=product_id, component_type='packaging')
    ]

    return render_template(
        'product_details.html',
        product=product,
        raw_materials=raw_materials,
        labor_costs=labor_costs,
        packaging_costs=packaging_costs
    )

@main_blueprint.route('/products/edit/<int:product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)

    if request.method == 'POST':
        product.name = request.form['name']
        product.products_per_recipe = int(request.form['products_per_recipe'])
        product.selling_price_per_unit = float(request.form['selling_price_per_unit'])
        
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename:
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                filename = f"{timestamp}_{filename}"
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                product.image_filename = filename

        # Clear existing components
        ProductComponent.query.filter_by(product_id=product_id).delete()

        # Add updated raw materials
        raw_materials = request.form.getlist('raw_material[]')
        raw_material_quantities = request.form.getlist('raw_material_quantity[]')
        for material_id, quantity in zip(raw_materials, raw_material_quantities):
            if material_id and quantity: # Check if both are not empty
                component = ProductComponent(
                    product_id=product.id,
                    component_type='raw_material',
                    component_id=int(material_id),
                    quantity=float(quantity)
                )
                db.session.add(component)

        # Add updated packaging
        packaging = request.form.getlist('packaging[]')
        packaging_quantities = request.form.getlist('packaging_quantity[]')
        for packaging_id, quantity in zip(packaging, packaging_quantities):
            if packaging_id and quantity: # Check if both are not empty
                component = ProductComponent(
                    product_id=product.id,
                    component_type='packaging',
                    component_id=int(packaging_id),
                    quantity=float(quantity)
                )
                db.session.add(component)

        # Add updated labor
        labor = request.form.getlist('labor[]')
        labor_hours = request.form.getlist('labor_hours[]')
        for labor_id, hours in zip(labor, labor_hours):
            if labor_id and hours: # Check if both are not empty
                component = ProductComponent(
                    product_id=product.id,
                    component_type='labor',
                    component_id=int(labor_id),
                    quantity=float(hours)
                )
                db.session.add(component)

        log_audit("UPDATE", "Product", product.id, f"Updated product {product.name}")
        db.session.commit()
        return redirect(url_for('main.products'))

    # Prepopulate fields for editing
    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    all_packaging = [p.to_dict() for p in Packaging.query.all()]
    all_labor = [labor_item.to_dict() for labor_item in Labor.query.all()]

    # Pass both the object (for Jinja server-side) and the dict (for JS client-side)
    return render_template(
        'add_or_edit_product.html',
        product=product,
        product_json=product.to_dict(),
        all_raw_materials=all_raw_materials,
        all_packaging=all_packaging,
        all_labor=all_labor
    )

# ----------------------------
# Categories Management
# ----------------------------
@main_blueprint.route('/categories', methods=['GET', 'POST'])
def categories():
    if request.method == 'POST':
        name = request.form['name']
        if not Category.query.filter_by(name=name).first():
            new_category = Category(name=name)
            db.session.add(new_category)
            db.session.commit()
    all_categories = Category.query.all()
    return render_template('categories.html', categories=all_categories)

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
    
    # Optional: Check if category is in use before deleting (prevent FK errors)
    # if category_item.raw_materials:
    #    return "Cannot delete category that has associated raw materials", 400

    db.session.delete(category_item)
    db.session.commit()
    return redirect(url_for('main.categories'))

@main_blueprint.route('/categories/add_from_modal', methods=['POST'])
def add_category_from_modal():
    name = request.form['name']
    if not name.strip():
        return redirect(url_for('main.add_raw_material'))  # Handle empty submissions gracefully

    if not Category.query.filter_by(name=name).first():
        new_category = Category(name=name.strip())
        db.session.add(new_category)
        db.session.commit()

    # Redirect back to the raw materials form
    return redirect(url_for('main.add_raw_material'))

# ----------------------------
# Production Management
# ----------------------------
@main_blueprint.route('/production', methods=['GET', 'POST'])
def production():
    if request.method == 'POST':
        product_id = request.form['product_id']
        quantity_produced = float(request.form['quantity_produced'])

        # Log production
        production_log = ProductionLog(product_id=product_id, quantity_produced=quantity_produced)
        db.session.add(production_log)
        db.session.commit()

        return redirect(url_for('main.production'))

    products = Product.query.all()
    production_logs = ProductionLog.query.order_by(ProductionLog.timestamp.desc()).all()
    current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    return render_template('production.html', products=products, production_logs=production_logs, current_time=current_time)

# ----------------------------
# Weekly Costs Management
# ----------------------------
@main_blueprint.route('/weekly_costs', methods=['GET', 'POST'])
def weekly_costs():
    if request.method == 'POST':
        date_str = request.form.get('week_start_date')
        if date_str:
            week_start = datetime.strptime(date_str, '%Y-%m-%d').date()
            week = WeeklyLaborCost.query.filter_by(week_start_date=week_start).first()
            if not week:
                week = WeeklyLaborCost(week_start_date=week_start, total_cost=0)
                db.session.add(week)
                db.session.commit()
            return redirect(url_for('main.weekly_cost_details', week_id=week.id))

    weekly_costs = WeeklyLaborCost.query.order_by(WeeklyLaborCost.week_start_date.desc()).all()
    return render_template('weekly_costs.html', weekly_costs=weekly_costs)

@main_blueprint.route('/weekly_costs/<int:week_id>', methods=['GET'])
def weekly_cost_details(week_id):
    week = WeeklyLaborCost.query.get_or_404(week_id)
    employees = Labor.query.all()
    return render_template('weekly_cost_details.html', week=week, employees=employees)

@main_blueprint.route('/weekly_costs/<int:week_id>/add', methods=['POST'])
def add_weekly_labor(week_id):
    week = WeeklyLaborCost.query.get_or_404(week_id)
    employee_id = request.form.get('employee_id')
    hours = float(request.form.get('hours'))
    
    employee = Labor.query.get(employee_id)
    if employee and hours > 0:
        cost = employee.total_hourly_rate * hours
        entry = WeeklyLaborEntry(
            weekly_cost_id=week.id,
            employee_id=employee.id,
            hours=hours,
            cost=cost
        )
        db.session.add(entry)
        week.total_cost += cost
        db.session.commit()
        
    return redirect(url_for('main.weekly_cost_details', week_id=week.id))

@main_blueprint.route('/weekly_costs/<int:week_id>/delete/<int:entry_id>', methods=['POST'])
def delete_weekly_labor(week_id, entry_id):
    week = WeeklyLaborCost.query.get_or_404(week_id)
    entry = WeeklyLaborEntry.query.get_or_404(entry_id)
    
    if entry.weekly_cost_id == week.id:
        week.total_cost -= entry.cost
        db.session.delete(entry)
        db.session.commit()
        
    return redirect(url_for('main.weekly_cost_details', week_id=week.id))

@main_blueprint.route('/weekly_sales/<int:week_id>', methods=['GET', 'POST'])
def update_weekly_sales(week_id):
    week = WeeklyLaborCost.query.get_or_404(week_id)
    products = Product.query.all()
    
    if request.method == 'POST':
        for product in products:
            key = f"sales_{product.id}"
            qty = request.form.get(key)
            if qty:
                qty = int(qty)
                # Find existing
                sale = WeeklyProductSales.query.filter_by(weekly_cost_id=week.id, product_id=product.id).first()
                if sale:
                    sale.quantity_sold = qty
                else:
                    sale = WeeklyProductSales(weekly_cost_id=week.id, product_id=product.id, quantity_sold=qty)
                    db.session.add(sale)
        
        db.session.commit()
        log_audit("UPDATE", "WeeklySales", week.id, f"Updated sales for week {week.week_start_date}")
        return redirect(url_for('main.index', week_id=week.id))

    # Create a map of existing sales for easy lookup in template
    sales_map = {s.product_id: s.quantity_sold for s in week.sales}
    
    return render_template('update_weekly_sales.html', week=week, products=products, sales_map=sales_map)
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

# ----------------------------
# Admin Actions
# ----------------------------
@main_blueprint.route('/admin/backup', methods=['GET'])
def backup_db():
    data = {
        'timestamp': datetime.now().isoformat(),
        'categories': [c.name for c in Category.query.all()], # Simple list for categories if to_dict missing, but let's check. Category has no to_dict in my memory, I'll just export names or dicts.
        'raw_materials': [m.to_dict() for m in RawMaterial.query.all()],
        'packaging': [p.to_dict() for p in Packaging.query.all()],
        'labor': [l.to_dict() for l in Labor.query.all()],
        'products': [p.to_dict() for p in Product.query.all()],
        'weekly_labor_costs': [w.to_dict() for w in WeeklyLaborCost.query.all()]
    }
    
    # Handle Category separately if needed or ensure it has to_dict. 
    # Checking models.py: Category has 'id', 'name'. No to_dict.
    # I'll do manual dict for category.
    data['categories'] = [{'id': c.id, 'name': c.name} for c in Category.query.all()]

    json_str = json.dumps(data, indent=4, ensure_ascii=False)
    mem = io.BytesIO()
    mem.write(json_str.encode('utf-8'))
    mem.seek(0)
    
    filename = f"costflow_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    return send_file(
        mem,
        as_attachment=True,
        download_name=filename,
        mimetype='application/json'
    )

@main_blueprint.route('/audit_log', methods=['GET'])
def audit_log():
    logs = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(500).all()
    return render_template('audit_log.html', logs=logs)

@main_blueprint.route('/admin/reset_db', methods=['POST'])
def reset_db():
    try:
        db.drop_all()
        db.create_all()
        
        # Re-seed essential data
        default_category = Category(name="כללי")
        db.session.add(default_category)
        
        log_audit("RESET", "System", details="Database was fully reset.")
        db.session.commit()
        return redirect(url_for('main.index'))
    except Exception as e:
        return f"Error resetting DB: {e}", 500
