import os
import pandas as pd
import json
import io
from datetime import datetime, date, timedelta
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, current_app, send_file, jsonify
from sqlalchemy import func, extract, and_
from ..models import db, RawMaterial, Labor, Packaging, Product, ProductComponent, Category, StockLog, ProductionLog, WeeklyLaborCost, WeeklyLaborEntry, WeeklyProductSales, StockAudit, AuditLog, Premake, PremakeComponent
from .utils import units_list, get_or_create_general_category, convert_to_base_unit, log_audit, calculate_prime_cost, calculate_premake_current_stock

main_blueprint = Blueprint('main', __name__)







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
    premake_report_data = []
    total_revenue = 0
    total_cogs = 0
    total_inventory_usage = 0
    total_labor = 0
    total_unsold_value = 0
    
    if selected_week:
        week_start = selected_week.week_start_date
        week_end = week_start + timedelta(days=6)
        total_labor = selected_week.total_cost
        
        # Fetch Product Production Logs
        logs = ProductionLog.query.filter(
            func.date(ProductionLog.timestamp) >= week_start,
            func.date(ProductionLog.timestamp) <= week_end,
            ProductionLog.product_id != None
        ).all()
        
        # Map Production
        product_production = {}
        for log in logs:
            # Calculate total units based on recipes count * units per recipe
            units_produced = log.quantity_produced * log.product.products_per_recipe
            
            if log.product_id not in product_production:
                product_production[log.product_id] = {'total': 0, 'new': 0}
            
            product_production[log.product_id]['total'] += units_produced
            if not log.is_carryover:
                product_production[log.product_id]['new'] += units_produced

        # Fetch Premake Production Logs
        premake_logs = ProductionLog.query.filter(
            func.date(ProductionLog.timestamp) >= week_start,
            func.date(ProductionLog.timestamp) <= week_end,
            ProductionLog.premake_id != None
        ).all()
        
        premake_production = {}
        for log in premake_logs:
            # Premake quantity is in Batches. Total units = Batches * Batch Size
            # Premakes carryover via StockLog, not ProductionLog carryover flag usually.
            # But if we start using is_carryover for premakes (unlikely needed), we'd check it.
            # For now, assume all premake logs are new production.
            units_produced = log.quantity_produced * log.premake.batch_size
            premake_production[log.premake_id] = premake_production.get(log.premake_id, 0) + units_produced

        # Calculate Premake Usage (from Product Production)
        premake_usage = {}
        for log in logs:
            # Premake usage should technically include usage from Carryover products?
            # If I carried over 10 products, did I "use" the premake *this week*?
            # No, I used it last week.
            # So Premake Usage should ONLY be calculated from NEW production logs.
            if log.is_carryover:
                continue

            product = log.product
            for component in product.components:
                if component.component_type == 'premake':
                    # Units used = component qty per recipe * recipes produced
                    # component.quantity is per recipe. log.quantity_produced is recipes (batches).
                    usage = component.quantity * log.quantity_produced
                    premake_usage[component.component_id] = premake_usage.get(component.component_id, 0) + usage

        # Process Premakes for Report and Inventory Value
        all_premakes = Premake.query.all()
        for premake in all_premakes:
            produced = premake_production.get(premake.id, 0)
            used = premake_usage.get(premake.id, 0)
            
            # Calculate current stock for premake
            current_premake_stock = calculate_premake_current_stock(premake.id)

            # Calculate Cost Per Unit
            cost_per_batch = 0
            for comp in premake.components:
                if comp.component_type == 'raw_material' and comp.material:
                    cost_per_batch += comp.quantity * comp.material.cost_per_unit
            
            cost_per_unit = cost_per_batch / premake.batch_size if premake.batch_size > 0 else 0
            
            # Inventory Value Change (Produced - Used)
            stock_change = produced - used
            
            # Add NET value change to total inventory usage (cost of goods flow)
            # This accounts for "Unused Premakes" being an expense this week.
            total_inventory_usage += stock_change * cost_per_unit
            
            if produced == 0 and used == 0 and current_premake_stock == 0:
                continue
                
            premake_report_data.append({
                'name': premake.name,
                'unit': premake.unit,
                'produced': produced,
                'used': used,
                'stock_change': stock_change,
                'current_stock': current_premake_stock,
                'cost_per_unit': cost_per_unit,
                'total_value_produced': produced * cost_per_unit
            })

        # Map Sales
        product_sales = {s.product_id: {'sold': s.quantity_sold, 'waste': s.quantity_waste} for s in selected_week.sales}
        
        # Iterate Products
        all_products = Product.query.all()
        
        for product in all_products:
            prod_data = product_production.get(product.id, {'total': 0, 'new': 0})
            produced_qty = prod_data['total']
            produced_qty_new = prod_data['new']
            
            sales_data = product_sales.get(product.id, {'sold': 0, 'waste': 0})
            sold_qty = sales_data['sold']
            waste_qty = sales_data['waste']
            
            # Calculate Prime Cost (Materials + Packaging + Premakes)
            prime_cost_per_unit = calculate_prime_cost(product)
            
            # Financials
            revenue = sold_qty * product.selling_price_per_unit
            cogs = sold_qty * prime_cost_per_unit
            waste_cost = waste_qty * prime_cost_per_unit # Cost of waste
            gross_profit = revenue - cogs - waste_cost # Profit is reduced by waste cost
            
            # Inventory Usage Value (what we made)
            # ONLY count NEW production for Cost
            inventory_usage_value = produced_qty_new * prime_cost_per_unit
            
            # Add to total (Products)
            total_inventory_usage += inventory_usage_value
            
            # Available (Unsold)
            # Count TOTAL (carryover + new) - sold - waste
            available_qty = produced_qty - sold_qty - waste_qty
            if available_qty < 0: available_qty = 0 # Should not happen with valid input but safe to clamp
            
            # Unsold Value
            total_unsold_value += available_qty * prime_cost_per_unit
            
            total_revenue += revenue
            total_cogs += cogs
            # total_inventory_usage was updated above
            
            # Only add to report if active this week
            if produced_qty == 0 and sold_qty == 0 and waste_qty == 0:
                continue

            report_data.append({
                'product_name': product.name,
                'product_image': product.image_filename,
                'produced_qty': produced_qty,
                'sold_qty': sold_qty,
                'waste_qty': waste_qty,
                'available_qty': available_qty,
                'prime_cost': prime_cost_per_unit,
                'selling_price': product.selling_price_per_unit,
                'revenue': revenue,
                'gross_profit': gross_profit
            })

    # Get stock audits for the selected week
    total_stock_variance = 0
    stock_audit_count = 0
    if selected_week:
        week_audits = StockAudit.query.filter(
            and_(
                func.date(StockAudit.audit_date) >= week_start,
                func.date(StockAudit.audit_date) <= week_end
            )
        ).all()
        total_stock_variance = sum(audit.variance_cost for audit in week_audits)
        stock_audit_count = len(week_audits)

    # Net Profit (Cash Flow view: considers Production Cost as expense)
    net_profit = total_revenue - total_inventory_usage - total_labor
    adjusted_profit = net_profit - abs(total_stock_variance)

    return render_template('index.html',
                           weeks=all_weeks,
                           selected_week=selected_week,
                           report_data=report_data,
                           premake_report_data=premake_report_data,
                           total_revenue=total_revenue,
                           total_cogs=total_cogs,
                           total_labor=total_labor,
                           total_inventory_usage=total_inventory_usage,
                           total_unsold_value=total_unsold_value,
                           net_profit=net_profit,
                           adjusted_profit=adjusted_profit,
                           total_stock_variance=total_stock_variance,
                           stock_audit_count=stock_audit_count,
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
        # if not category:  # Handled by get_or_create or existing valid ID
        #    return "Invalid category selected", 400

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
        # if not category:
        #    return "Invalid category selected", 400

        material.category = category
        material.unit = request.form['unit']
        material.cost_per_unit = float(request.form['cost_per_unit'])
        
        # Note: Stock is managed via logs, not directly editable here to preserve history
        
        db.session.commit()
        return redirect(url_for('main.raw_materials'))

    categories = Category.query.filter_by(type='raw_material').all()
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
            if production.product_id:
                product = Product.query.get(production.product_id)
                if product:
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
# Premakes Management
# ----------------------------
@main_blueprint.route('/premakes')
def premakes():
    premakes = Premake.query.all()
    return render_template('premakes.html', premakes=premakes)

@main_blueprint.route('/premakes/add', methods=['GET', 'POST'])
def add_premake():
    if request.method == 'POST':
        name = request.form['name']
        category_id = request.form.get('category_id')
        if not category_id:
            category_id = get_or_create_general_category('premake')
            
        unit = request.form.get('unit', 'kg') # Default to kg
        
        category = Category.query.get(category_id) # if category_id else None (guaranteed by get_or_create)
        
        # Process components first to calculate batch size
        raw_materials = request.form.getlist('raw_material[]')
        raw_material_quantities = request.form.getlist('raw_material_quantity[]')
        raw_material_units = request.form.getlist('raw_material_unit[]')
        
        batch_size = 0
        components_data = []
        
        for i in range(len(raw_materials)):
            material_id = raw_materials[i]
            quantity_str = raw_material_quantities[i]
            selected_unit = raw_material_units[i] if i < len(raw_material_units) else None
            
            if not material_id or not quantity_str or float(quantity_str) <= 0:
                continue
            
            quantity = float(quantity_str)
            
            material = RawMaterial.query.get(material_id)
            if not material:
                continue
                
            final_quantity = convert_to_base_unit(quantity, selected_unit, material.unit)
            
            # Batch size is sum of base quantities (assuming base units are compatible, e.g. all weight)
            # If mixed units (kg and l), this sum is weird but standard for MVP.
            batch_size += final_quantity
            
            components_data.append({'id': material_id, 'qty': final_quantity})

        premake = Premake(
            name=name,
            category=category,
            batch_size=batch_size,
            unit=unit
        )
        db.session.add(premake)
        db.session.flush() # Get ID
        
        log_audit("CREATE", "Premake", premake.id, f"Created premake {premake.name}")

        # Add components to DB
        for item in components_data:
            component = PremakeComponent(
                premake_id=premake.id,
                component_type='raw_material',
                component_id=item['id'],
                quantity=item['qty']
            )
            db.session.add(component)

        # Initial Stock Log (start with 0)
        initial_stock_log = StockLog(
            premake_id=premake.id,
            action_type='set',
            quantity=0
        )
        db.session.add(initial_stock_log)

        db.session.commit()
        return redirect(url_for('main.premakes'))

    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    print(f"DEBUG: add_premake - Found {len(all_raw_materials)} raw materials")
    premake_categories = Category.query.filter_by(type='premake').all()
    categories = Category.query.filter_by(type='raw_material').all() # For raw material modal if needed

    return render_template(
        'add_or_edit_premake.html',
        premake=None,
        all_raw_materials=all_raw_materials,
        premake_categories=premake_categories,
        categories=categories,
        units=units_list
    )

@main_blueprint.route('/premakes/edit/<int:premake_id>', methods=['GET', 'POST'])
def edit_premake(premake_id):
    premake = Premake.query.get_or_404(premake_id)

    if request.method == 'POST':
        premake.name = request.form['name']
        premake.category_id = request.form.get('category_id')
        if not premake.category_id:
            premake.category_id = get_or_create_general_category('premake')
            
        premake.unit = request.form['unit']
        
        # Clear existing components
        PremakeComponent.query.filter_by(premake_id=premake.id).delete()
        
        # Process components
        raw_materials = request.form.getlist('raw_material[]')
        raw_material_quantities = request.form.getlist('raw_material_quantity[]')
        raw_material_units = request.form.getlist('raw_material_unit[]')
        
        batch_size = 0
        
        for i in range(len(raw_materials)):
            material_id = raw_materials[i]
            quantity_str = raw_material_quantities[i]
            selected_unit = raw_material_units[i] if i < len(raw_material_units) else None
            
            if not material_id or not quantity_str or float(quantity_str) <= 0:
                continue
            
            quantity = float(quantity_str)
            
            material = RawMaterial.query.get(material_id)
            if not material:
                continue
                
            final_quantity = convert_to_base_unit(quantity, selected_unit, material.unit)
            batch_size += final_quantity
            
            component = PremakeComponent(
                premake_id=premake.id,
                component_type='raw_material',
                component_id=material_id,
                quantity=final_quantity
            )
            db.session.add(component)
        
        premake.batch_size = batch_size
            
        log_audit("UPDATE", "Premake", premake.id, f"Updated premake {premake.name}")
        db.session.commit()
        return redirect(url_for('main.premakes'))

    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    print(f"DEBUG: edit_premake - Found {len(all_raw_materials)} raw materials")
    premake_categories = Category.query.filter_by(type='premake').all()
    categories = Category.query.filter_by(type='raw_material').all()

    return render_template(
        'add_or_edit_premake.html',
        premake=premake,
        all_raw_materials=all_raw_materials,
        premake_categories=premake_categories,
        categories=categories,
        units=units_list
    )

@main_blueprint.route('/premakes/delete/<int:premake_id>', methods=['POST'])
def delete_premake(premake_id):
    premake = Premake.query.get_or_404(premake_id)
    
    # Check dependency: ProductComponent
    if ProductComponent.query.filter_by(component_type='premake', component_id=premake.id).first():
         return "Cannot delete premake used in products", 400

    # Delete related StockLogs
    StockLog.query.filter_by(premake_id=premake.id).delete()
    
    # Delete related Components
    PremakeComponent.query.filter_by(premake_id=premake.id).delete()
    
    db.session.delete(premake)
    log_audit("DELETE", "Premake", premake_id, f"Deleted premake {premake.name}")
    db.session.commit()
    return redirect(url_for('main.premakes'))

@main_blueprint.route('/premakes/<int:premake_id>', methods=['GET'])
def premake_detail(premake_id):
    premake = Premake.query.get_or_404(premake_id)
    
    components_data = []
    total_cost = 0
    
    for component in premake.components:
        if component.component_type == 'raw_material' and component.material:
            cost = component.quantity * component.material.cost_per_unit
            total_cost += cost
            components_data.append({
                'name': component.material.name,
                'quantity': component.quantity,
                'unit': component.material.unit,
                'cost_per_unit': component.material.cost_per_unit,
                'total_cost': cost
            })
    
    # Add percentage
    for item in components_data:
        item['cost_percentage'] = (item['total_cost'] / total_cost * 100) if total_cost > 0 else 0
        
    cost_per_unit = total_cost / premake.batch_size if premake.batch_size > 0 else 0
    
    return render_template('premake_details.html', 
                           premake=premake, 
                           components_data=components_data, 
                           total_cost=total_cost, 
                           cost_per_unit=cost_per_unit,
                           currency_symbol='₪')

# ----------------------------
# Products Management
# ----------------------------
@main_blueprint.route('/products')
def products():
    products = Product.query.all()
    products_data = []
    for product in products:
        cost = calculate_prime_cost(product)
        products_data.append({
            'product': product,
            'prime_cost': cost
        })
    return render_template('products.html', products_data=products_data)

@main_blueprint.route('/products/add', methods=['GET', 'POST'])
def add_product():
    if request.method == 'POST':
        # Extract product-level data
        name = request.form['name']
        category_id = request.form.get('category_id')
        if not category_id:
            category_id = get_or_create_general_category('product')
            
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
            category_id=category_id,
            products_per_recipe=int(products_per_recipe),
            selling_price_per_unit=float(selling_price_per_unit),
            image_filename=image_filename
        )
        db.session.add(product)
        db.session.flush()
        log_audit("CREATE", "Product", product.id, f"Created product {product.name}")
        # Process raw materials
        raw_materials = request.form.getlist('raw_material[]')
        raw_material_quantities = request.form.getlist('raw_material_quantity[]')
        raw_material_units = request.form.getlist('raw_material_unit[]')
        
        # Handle case where units might not be sent if old form used (though we updated templates)
        # Zip stops at shortest list. If units missing, it might break or skip.
        # Ensure lists are same length or handle index.
        # Safest: Iterate by index.
        for i in range(len(raw_materials)):
            material_id = raw_materials[i]
            quantity = raw_material_quantities[i]
            selected_unit = raw_material_units[i] if i < len(raw_material_units) else None
            
            if not material_id or not quantity or float(quantity) <= 0:
                continue
                
            # Convert quantity to base unit
            material = RawMaterial.query.get(material_id)
            if not material:
                continue
                
            final_quantity = convert_to_base_unit(float(quantity), selected_unit, material.unit)
            
            component = ProductComponent(
                product_id=product.id,
                component_type='raw_material',
                component_id=material_id,
                quantity=final_quantity
            )
            db.session.add(component)

        # Process packaging
        packaging_ids = request.form.getlist('packaging[]')
        packaging_quantities = request.form.getlist('packaging_quantity[]')
        for pkg_id, quantity in zip(packaging_ids, packaging_quantities):
            if not pkg_id or not quantity or float(quantity) <= 0:
                continue
            component = ProductComponent(
                product_id=product.id,
                component_type='packaging',
                component_id=pkg_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        # Process premakes
        premake_ids = request.form.getlist('premake[]')
        premake_quantities = request.form.getlist('premake_quantity[]')
        for premake_id, quantity in zip(premake_ids, premake_quantities):
            if not premake_id or not quantity or float(quantity) <= 0:
                continue
            component = ProductComponent(
                product_id=product.id,
                component_type='premake',
                component_id=premake_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        db.session.commit()  # Save all components
        return redirect(url_for('main.products'))

    # For GET requests, load the data required for the form
    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    categories = Category.query.filter_by(type='raw_material').all() # Keep this for modals?
    # No, wait. The 'categories' var passed here is used for "Add Raw Material Modal".
    # I need a separate list for "Product Categories".
    product_categories = Category.query.filter_by(type='product').all()
    
    all_packaging = [p.to_dict() for p in Packaging.query.all()]
    all_labor = [labor_item.to_dict() for labor_item in Labor.query.all()]
    all_premakes = [p.to_dict() for p in Premake.query.all()]
    
    return render_template(
        'add_or_edit_product.html',
        product=None,
        all_raw_materials=all_raw_materials,
        all_packaging=all_packaging,
        all_labor=all_labor,
        all_premakes=all_premakes,
        categories=categories, # For raw material modal
        product_categories=product_categories, # For product form
        units=units_list # For raw material modal
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

    # Retrieve premake costs
    premake_costs = []
    for component in ProductComponent.query.filter_by(product_id=product_id, component_type='premake'):
        premake = Premake.query.get(component.component_id)
        if not premake:
             continue
             
        # Calculate cost per unit of premake
        premake_total_cost = 0
        calculated_batch_size = 0
        for sub_comp in premake.components:
             if sub_comp.component_type == 'raw_material':
                 mat = RawMaterial.query.get(sub_comp.component_id)
                 if mat:
                     premake_total_cost += sub_comp.quantity * mat.cost_per_unit
                 calculated_batch_size += sub_comp.quantity
             elif sub_comp.component_type == 'packaging':
                 pkg = Packaging.query.get(sub_comp.component_id)
                 if pkg:
                     premake_total_cost += sub_comp.quantity * pkg.price_per_unit
        
        # Use stored batch size if valid, otherwise use calculated sum
        effective_batch_size = premake.batch_size if premake.batch_size > 0 else calculated_batch_size
        
        cost_per_unit_premake = premake_total_cost / effective_batch_size if effective_batch_size > 0 else 0
        
        premake_costs.append({
            'name': premake.name,
            'quantity': component.quantity,
            'unit': premake.unit,
            'batch_size': effective_batch_size,
            'price_per_unit': cost_per_unit_premake,
            'price_per_recipe': component.quantity * cost_per_unit_premake,
            'price_per_product': (component.quantity * cost_per_unit_premake) / product.products_per_recipe,
            'components': [
                 {
                     'name': c.material.name if c.component_type == 'raw_material' else c.packaging.name,
                     'quantity': c.quantity,
                     'unit': c.material.unit if c.component_type == 'raw_material' else 'pcs'
                 } for c in premake.components
            ]
        })

    return render_template(
        'product_details.html',
        product=product,
        raw_materials=raw_materials,
        labor_costs=labor_costs,
        packaging_costs=packaging_costs,
        premake_costs=premake_costs
    )

@main_blueprint.route('/products/edit/<int:product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)

    if request.method == 'POST':
        product.name = request.form['name']
        product.category_id = request.form.get('category_id')
        if not product.category_id:
            product.category_id = get_or_create_general_category('product')
            
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

        # Process raw materials
        raw_materials = request.form.getlist('raw_material[]')
        raw_material_quantities = request.form.getlist('raw_material_quantity[]')
        raw_material_units = request.form.getlist('raw_material_unit[]')
        
        for i in range(len(raw_materials)):
            material_id = raw_materials[i]
            quantity = raw_material_quantities[i]
            selected_unit = raw_material_units[i] if i < len(raw_material_units) else None
            
            if not material_id or not quantity or float(quantity) <= 0:
                continue
                
            material = RawMaterial.query.get(material_id)
            if not material:
                continue
                
            final_quantity = convert_to_base_unit(float(quantity), selected_unit, material.unit)
            
            component = ProductComponent(
                product_id=product.id,
                component_type='raw_material',
                component_id=material_id,
                quantity=final_quantity
            )
            db.session.add(component)

        # Process packaging
        packaging_ids = request.form.getlist('packaging[]')
        packaging_quantities = request.form.getlist('packaging_quantity[]')
        for pkg_id, quantity in zip(packaging_ids, packaging_quantities):
            if not pkg_id or not quantity or float(quantity) <= 0:
                continue
            component = ProductComponent(
                product_id=product.id,
                component_type='packaging',
                component_id=pkg_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        # Process premakes
        premake_ids = request.form.getlist('premake[]')
        premake_quantities = request.form.getlist('premake_quantity[]')
        for premake_id, quantity in zip(premake_ids, premake_quantities):
            if not premake_id or not quantity or float(quantity) <= 0:
                continue
            component = ProductComponent(
                product_id=product.id,
                component_type='premake',
                component_id=premake_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        log_audit("UPDATE", "Product", product.id, f"Updated product {product.name}")
        db.session.commit()
        return redirect(url_for('main.products'))

    # Prepopulate fields for editing
    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    all_packaging = [p.to_dict() for p in Packaging.query.all()]
    all_labor = [labor_item.to_dict() for labor_item in Labor.query.all()]
    all_premakes = [p.to_dict() for p in Premake.query.all()]
    
    categories = Category.query.filter_by(type='raw_material').all()
    product_categories = Category.query.filter_by(type='product').all()

    # Pass both the object (for Jinja server-side) and the dict (for JS client-side)
    return render_template(
        'add_or_edit_product.html',
        product=product,
        all_raw_materials=all_raw_materials,
        all_packaging=all_packaging,
        all_labor=all_labor,
        all_premakes=all_premakes,
        categories=categories,
        product_categories=product_categories,
        units=units_list # For raw material modal
    )






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

@main_blueprint.route('/admin/restore', methods=['POST'])
def restore_db():
    if 'backup_file' not in request.files:
        return "No file uploaded", 400
        
    file = request.files['backup_file']
    if file.filename == '':
        return "No file selected", 400

    try:
        data = json.load(file)
        
        # Reset DB
        db.drop_all()
        db.create_all()
        
        # 1. Categories
        category_map = {} # old_id -> new_instance (or just keep same IDs if we force them)
        # We will try to keep same IDs to maintain relationships if possible, 
        # but SQLAlchemy might auto-increment. 
        # Best effort: Explicitly set ID if the DB allows (Postgres/SQLite usually do if specified).
        
        for cat_data in data.get('categories', []):
            c = Category(id=cat_data['id'], name=cat_data['name'])
            db.session.add(c)
            category_map[cat_data['id']] = c
        db.session.flush()

        # 2. Labor
        labor_map = {}
        for l_data in data.get('labor', []):
            # Check fields (handle old backups vs new schema)
            l = Labor(
                id=l_data['id'],
                name=l_data['name'],
                phone_number=l_data.get('phone_number'),
                base_hourly_rate=l_data.get('base_hourly_rate', l_data.get('total_hourly_rate', 0)), # Fallback
                additional_hourly_rate=l_data.get('additional_hourly_rate', 0)
            )
            db.session.add(l)
            labor_map[l_data['id']] = l
        db.session.flush()

        # 3. Packaging
        pkg_map = {}
        for p_data in data.get('packaging', []):
            p = Packaging(
                id=p_data['id'],
                name=p_data['name'],
                quantity_per_package=p_data['quantity_per_package'],
                price_per_package=p_data['price_per_package']
            )
            db.session.add(p)
            pkg_map[p_data['id']] = p
        db.session.flush()

        # 4. Raw Materials
        mat_map = {}
        for m_data in data.get('raw_materials', []):
            # Handle category link
            cat_id = None
            if m_data.get('category'):
                cat_id = m_data['category']['id']
            
            m = RawMaterial(
                id=m_data['id'],
                name=m_data['name'],
                category_id=cat_id,
                unit=m_data['unit'],
                cost_per_unit=m_data['cost_per_unit'],
                current_stock=m_data['current_stock']
            )
            db.session.add(m)
            mat_map[m_data['id']] = m
        db.session.flush()

        # 5. Products & Components
        for p_data in data.get('products', []):
            p = Product(
                id=p_data['id'],
                name=p_data['name'],
                products_per_recipe=p_data['products_per_recipe'],
                selling_price_per_unit=p_data['selling_price_per_unit'],
                image_filename=p_data.get('image_filename')
            )
            db.session.add(p)
            db.session.flush()
            
            # Components
            for c_data in p_data.get('components', []):
                comp = ProductComponent(
                    product_id=p.id,
                    component_type=c_data['component_type'],
                    component_id=c_data['component_id'],
                    quantity=c_data['quantity']
                )
                db.session.add(comp)

        # 6. Weekly Labor Costs & Children
        for w_data in data.get('weekly_labor_costs', []):
            w = WeeklyLaborCost(
                id=w_data['id'],
                week_start_date=datetime.strptime(w_data['week_start_date'], '%Y-%m-%d').date(),
                total_cost=w_data['total_cost']
            )
            db.session.add(w)
            db.session.flush()
            
            # Entries (if present in backup - backup_db needs to export them! models.py to_dict includes them)
            for e_data in w_data.get('entries', []):
                # Need to map employee name to ID? or assuming ID matches?
                # Entries dict has 'employee_name' but not ID.
                # Fix: The backup (to_dict) is LOSING foreign keys (ids) and replacing with names/objects!
                # THIS IS A PROBLEM for restoration.
                # Ideally we fix to_dict or backup logic.
                # Workaround: Look up by name.
                emp_name = e_data.get('employee_name')
                emp = Labor.query.filter_by(name=emp_name).first()
                if emp:
                    entry = WeeklyLaborEntry(
                        weekly_cost_id=w.id,
                        employee_id=emp.id,
                        hours=e_data['hours'],
                        cost=e_data['cost']
                    )
                    db.session.add(entry)

            # Sales
            for s_data in w_data.get('sales', []):
                prod_name = s_data.get('product_name')
                prod = Product.query.filter_by(name=prod_name).first()
                if prod:
                    sale = WeeklyProductSales(
                        weekly_cost_id=w.id,
                        product_id=prod.id,
                        quantity_sold=s_data['quantity_sold']
                    )
                    db.session.add(sale)

        db.session.commit()
        log_audit("RESTORE", "System", details="Database restored from backup.")
        return redirect(url_for('main.index'))
        
    except Exception as e:
        print(f"Restore failed: {e}")
        return f"Restore failed: {e}", 500

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
        db.session.add(Category(name="כללי (חומרי גלם)", type='raw_material'))
        db.session.add(Category(name="כללי (מוצרים)", type='product'))

        log_audit("RESET", "System", details="Database was fully reset.")
        db.session.commit()
        return redirect(url_for('main.index'))
    except Exception as e:
        return f"Error resetting DB: {e}", 500

# Weekly Report
@main_blueprint.route('/reports/weekly')
def weekly_report():
    # 1. Fetch all weeks for dropdown
    all_weeks = WeeklyLaborCost.query.order_by(WeeklyLaborCost.week_start_date.desc()).all()

    # Get date parameters
    week_start = request.args.get('week_start')

    if not week_start:
        # Default to current week start (Sunday) if not specified, or the latest available week
        if all_weeks:
             # Prefer the latest recorded week if available and no specific date requested
             # But usually user wants "current context".
             # Let's stick to requesting specific date, or default to "latest week" if user lands on page without args?
             # The original logic defaulted to "today's week".
             # Let's keep "today's week" as default but allow selection from dropdown.
             pass
        
        today = date.today()
        days_since_sunday = (today.weekday() + 1) % 7
        week_start = today - timedelta(days=days_since_sunday)
    else:
        week_start = datetime.strptime(week_start, '%Y-%m-%d').date()

    week_end = week_start + timedelta(days=6)

    # Get the weekly data from WeeklyLaborCost
    weekly_cost = WeeklyLaborCost.query.filter_by(week_start_date=week_start).first()

    if not weekly_cost:
        # No data for this week
        return render_template('weekly_report.html',
                             weeks=all_weeks,
                             week_start=week_start,
                             week_end=week_end,
                             sales_data=[],
                             category_summaries={},
                             total_revenue=0,
                             labor_costs=0,
                             net_profit=0,
                             total_food_cost=0,
                             avg_food_cost_per_recipe=0,
                             total_recipes_produced=0,
                             no_data=True)

    # Get sales data with product and category info
    sales_data = []
    total_revenue = 0
    total_material_costs = 0
    category_summaries = {}

    # Get all sales for this week
    week_sales = WeeklyProductSales.query.filter_by(weekly_cost_id=weekly_cost.id).all()

    for sale in week_sales:
        product = sale.product
        if not product:
            continue

        # Calculate prime cost (materials + packaging + premakes)
        prime_cost_per_unit = calculate_prime_cost(product)

        # Calculate costs for sold and waste quantities
        material_cost_sold = (sale.quantity_sold or 0) * prime_cost_per_unit
        material_cost_waste = (sale.quantity_waste or 0) * prime_cost_per_unit
        revenue = (sale.quantity_sold or 0) * product.selling_price_per_unit

        total_material_costs += material_cost_sold + material_cost_waste
        total_revenue += revenue

        cat_name = product.category.name if product.category else 'ללא קטגוריה'

        # Add to sales data
        sales_data.append({
            'name': product.name,
            'category_name': cat_name,
            'selling_price_per_unit': product.selling_price_per_unit,
            'quantity_sold': sale.quantity_sold,
            'quantity_waste': sale.quantity_waste,
            'revenue': revenue,
            'prime_cost_per_unit': prime_cost_per_unit,
            'material_cost': material_cost_sold + material_cost_waste
        })

        # Update category summaries
        if cat_name not in category_summaries:
            category_summaries[cat_name] = {
                'quantity_sold': 0,
                'quantity_waste': 0,
                'revenue': 0,
                'material_cost': 0,
                'products': []
            }

        category_summaries[cat_name]['quantity_sold'] += sale.quantity_sold or 0
        category_summaries[cat_name]['quantity_waste'] += sale.quantity_waste or 0
        category_summaries[cat_name]['revenue'] += revenue
        category_summaries[cat_name]['material_cost'] += material_cost_sold + material_cost_waste
        if product.name not in category_summaries[cat_name]['products']:
            category_summaries[cat_name]['products'].append(product.name)

    # Calculate Food Cost from Production
    production_logs = ProductionLog.query.filter(
        and_(
            func.date(ProductionLog.timestamp) >= week_start,
            func.date(ProductionLog.timestamp) <= week_end
        )
    ).all()

    total_food_cost = 0
    total_recipes_produced = 0
    production_aggregates = {}  # Aggregate by product_id

    for log in production_logs:
        product = log.product
        if not product:
            continue

        # Calculate prime cost per recipe (food cost)
        # calculate_prime_cost returns cost per UNIT. We need cost per RECIPE (Batch).
        prime_cost_per_unit = calculate_prime_cost(product)
        recipe_cost = prime_cost_per_unit * product.products_per_recipe

        # Aggregate by product
        if product.id not in production_aggregates:
            production_aggregates[product.id] = {
                'product_name': product.name,
                'recipes_produced': 0,
                'cost_per_recipe': recipe_cost,
                'total_cost': 0,
                'units_per_recipe': product.products_per_recipe,
                'production_count': 0  # Track how many times produced
            }

        production_aggregates[product.id]['recipes_produced'] += log.quantity_produced
        production_aggregates[product.id]['total_cost'] += recipe_cost * log.quantity_produced
        production_aggregates[product.id]['production_count'] += 1

        # Update totals
        total_food_cost += recipe_cost * log.quantity_produced
        total_recipes_produced += log.quantity_produced

    # Convert aggregates to list for template, sorted by total cost (descending)
    production_details = sorted(production_aggregates.values(), key=lambda x: x['total_cost'], reverse=True)

    # Calculate average food cost per recipe
    avg_food_cost_per_recipe = total_food_cost / total_recipes_produced if total_recipes_produced > 0 else 0

    # Get labor breakdown - aggregate by employee
    labor_aggregates = {}
    for entry in weekly_cost.entries:
        employee_id = entry.employee_id
        if employee_id not in labor_aggregates:
            labor_aggregates[employee_id] = {
                'employee': entry.employee,
                'employee_name': entry.employee.name if entry.employee else 'עובד לא ידוע',
                'hours': 0,
                'cost': 0,
                'entry_count': 0  # Track how many separate entries
            }
        labor_aggregates[employee_id]['hours'] += entry.hours
        labor_aggregates[employee_id]['cost'] += entry.cost
        labor_aggregates[employee_id]['entry_count'] += 1

    # Convert to list sorted by cost (highest first)
    labor_entries = sorted(labor_aggregates.values(), key=lambda x: x['cost'], reverse=True)

    # ---------------------------------------------------
    # Premake Activity Analysis
    # ---------------------------------------------------
    premake_report_data = []
    
    # Fetch Premake Production Logs for the week
    premake_logs = ProductionLog.query.filter(
        and_(
            func.date(ProductionLog.timestamp) >= week_start,
            func.date(ProductionLog.timestamp) <= week_end,
            ProductionLog.premake_id != None
        )
    ).all()
    
    premake_production = {}
    for log in premake_logs:
        # Premake quantity is in Batches. Total units = Batches * Batch Size
        units_produced = log.quantity_produced * log.premake.batch_size
        premake_production[log.premake_id] = premake_production.get(log.premake_id, 0) + units_produced

    # Calculate Premake Usage (from Product Production Logs already fetched)
    premake_usage = {}
    for log in production_logs:
        product = log.product
        if not product: continue
        for component in product.components:
            if component.component_type == 'premake':
                # Units used = component qty per recipe * recipes produced
                # component.quantity is per recipe. log.quantity_produced is recipes (batches).
                usage = component.quantity * log.quantity_produced
                premake_usage[component.component_id] = premake_usage.get(component.component_id, 0) + usage

    # Process Premakes for Report
    all_premakes = Premake.query.all()
    for premake in all_premakes:
        produced = premake_production.get(premake.id, 0)
        used = premake_usage.get(premake.id, 0)
        
        # Calculate current stock for premake
        current_premake_stock = calculate_premake_current_stock(premake.id)

        # Calculate Cost Per Unit
        cost_per_batch = 0
        for comp in premake.components:
            if comp.component_type == 'raw_material' and comp.material:
                cost_per_batch += comp.quantity * comp.material.cost_per_unit
        
        cost_per_unit = cost_per_batch / premake.batch_size if premake.batch_size > 0 else 0
        
        # Inventory Value Change (Produced - Used)
        stock_change = produced - used
        
        if produced == 0 and used == 0 and current_premake_stock == 0:
            continue
            
        premake_report_data.append({
            'name': premake.name,
            'unit': premake.unit,
            'produced': produced,
            'used': used,
            'stock_change': stock_change,
            'current_stock': current_premake_stock,
            'cost_per_unit': cost_per_unit,
            'total_value_produced': produced * cost_per_unit
        })

    # Get stock audits for the week
    stock_audits = StockAudit.query.filter(
        and_(
            func.date(StockAudit.audit_date) >= week_start,
            func.date(StockAudit.audit_date) <= week_end
        )
    ).order_by(StockAudit.audit_date.desc()).all()

    # Calculate stock discrepancy totals
    total_stock_variance_cost = sum(audit.variance_cost for audit in stock_audits)
    stock_audit_count = len(stock_audits)

    # Group audits by category
    audit_by_category = {}
    for audit in stock_audits:
        if audit.raw_material and audit.raw_material.category:
            cat_name = audit.raw_material.category.name
            if cat_name not in audit_by_category:
                audit_by_category[cat_name] = {
                    'count': 0,
                    'variance': 0,
                    'variance_cost': 0
                }
            audit_by_category[cat_name]['count'] += 1
            audit_by_category[cat_name]['variance'] += audit.variance
            audit_by_category[cat_name]['variance_cost'] += audit.variance_cost

    # Calculate adjusted profit (including stock losses)
    adjusted_profit = total_revenue - total_material_costs - weekly_cost.total_cost - abs(total_stock_variance_cost)

    return render_template('weekly_report.html',
                         weeks=all_weeks,
                         week_start=week_start,
                         week_end=week_end,
                         sales_data=sales_data,
                         labor_entries=labor_entries,
                         category_summaries=category_summaries,
                         total_revenue=total_revenue,
                         total_material_costs=total_material_costs,
                         labor_costs=weekly_cost.total_cost,
                         net_profit=total_revenue - total_material_costs - weekly_cost.total_cost,
                         stock_audits=stock_audits,
                         total_stock_variance_cost=total_stock_variance_cost,
                         stock_audit_count=stock_audit_count,
                         audit_by_category=audit_by_category,
                         adjusted_profit=adjusted_profit,
                         total_food_cost=total_food_cost,
                         avg_food_cost_per_recipe=avg_food_cost_per_recipe,
                         total_recipes_produced=total_recipes_produced,
                         production_details=production_details,
                         premake_report_data=premake_report_data,
                         no_data=False)

# Monthly Report - Aggregating Weekly Reports
@main_blueprint.route('/reports/monthly')
def monthly_report():
    # Get month and year parameters
    month = request.args.get('month', type=int)
    year = request.args.get('year', type=int)

    if not month or not year:
        # Default to current month
        today = date.today()
        month = today.month
        year = today.year

    # Calculate month start and end
    month_start = date(year, month, 1)
    if month == 12:
        month_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        month_end = date(year, month + 1, 1) - timedelta(days=1)

    # Get all weekly reports for the month
    weekly_costs = WeeklyLaborCost.query.filter(
        and_(
            WeeklyLaborCost.week_start_date >= month_start,
            WeeklyLaborCost.week_start_date <= month_end
        )
    ).order_by(WeeklyLaborCost.week_start_date).all()

    if not weekly_costs:
        # No data for this month
        return render_template('monthly_report.html',
                             month=month,
                             year=year,
                             month_start=month_start,
                             month_end=month_end,
                             weekly_summaries=[],
                             product_totals=[],
                             category_summaries={},
                             total_revenue=0,
                             total_labor_costs=0,
                             net_profit=0,
                             no_data=True)

    # Aggregate data from all weeks
    product_aggregates = {}
    category_summaries = {}
    weekly_summaries = []
    total_revenue = 0
    total_labor_costs = 0
    total_material_costs = 0
    total_stock_variance_cost = 0

    for week in weekly_costs:
        week_revenue = 0
        week_sales_count = 0
        week_material_costs = 0
        week_food_cost = 0
        week_recipes_produced = 0

        # Get sales for this week with actual product objects
        week_sales = WeeklyProductSales.query.filter_by(weekly_cost_id=week.id).all()

        for sale in week_sales:
            product = sale.product
            if not product:
                continue

            # Calculate prime cost (materials + packaging + premakes) for this product
            prime_cost_per_unit = calculate_prime_cost(product)

            # Calculate costs and revenue
            material_cost = ((sale.quantity_sold or 0) + (sale.quantity_waste or 0)) * prime_cost_per_unit
            sale_revenue = (sale.quantity_sold or 0) * product.selling_price_per_unit

            product_key = product.id
            cat_name = product.category.name if product.category else 'ללא קטגוריה'

            # Aggregate by product
            if product_key not in product_aggregates:
                product_aggregates[product_key] = {
                    'name': product.name,
                    'category_name': cat_name,
                    'price_per_unit': product.selling_price_per_unit,
                    'prime_cost_per_unit': prime_cost_per_unit,
                    'quantity_sold': 0,
                    'quantity_waste': 0,
                    'revenue': 0,
                    'material_cost': 0,
                    'weeks_active': 0
                }

            product_aggregates[product_key]['quantity_sold'] += sale.quantity_sold or 0
            product_aggregates[product_key]['quantity_waste'] += sale.quantity_waste or 0
            product_aggregates[product_key]['revenue'] += sale_revenue
            product_aggregates[product_key]['material_cost'] += material_cost
            product_aggregates[product_key]['weeks_active'] += 1

            # Aggregate by category
            if cat_name not in category_summaries:
                category_summaries[cat_name] = {
                    'quantity_sold': 0,
                    'quantity_waste': 0,
                    'revenue': 0,
                    'material_cost': 0,
                    'products': set(),
                    'weeks_active': set()
                }

            category_summaries[cat_name]['quantity_sold'] += sale.quantity_sold or 0
            category_summaries[cat_name]['quantity_waste'] += sale.quantity_waste or 0
            category_summaries[cat_name]['revenue'] += sale_revenue
            category_summaries[cat_name]['material_cost'] += material_cost
            category_summaries[cat_name]['products'].add(product.name)
            category_summaries[cat_name]['weeks_active'].add(week.week_start_date)

            week_revenue += sale_revenue
            week_material_costs += material_cost
            week_sales_count += sale.quantity_sold or 0

        # Get stock audits for this week
        week_end_date = week.week_start_date + timedelta(days=6)
        week_audits = StockAudit.query.filter(
            and_(
                func.date(StockAudit.audit_date) >= week.week_start_date,
                func.date(StockAudit.audit_date) <= week_end_date
            )
        ).all()

        week_stock_variance_cost = sum(audit.variance_cost for audit in week_audits)

        # Calculate Food Cost from Production for this week
        week_production_logs = ProductionLog.query.filter(
            and_(
                func.date(ProductionLog.timestamp) >= week.week_start_date,
                func.date(ProductionLog.timestamp) <= week_end_date
            )
        ).all()

        for log in week_production_logs:
            product = log.product
            if not product:
                continue

            # Calculate prime cost per recipe (food cost)
            prime_cost_per_unit = calculate_prime_cost(product)
            recipe_cost = prime_cost_per_unit * product.products_per_recipe

            # Total cost for this production run
            production_cost = recipe_cost * log.quantity_produced
            week_food_cost += production_cost
            week_recipes_produced += log.quantity_produced

        # Add weekly summary
        weekly_summaries.append({
            'week_start': week.week_start_date,
            'week_end': week.week_start_date + timedelta(days=6),
            'revenue': week_revenue,
            'material_cost': week_material_costs,
            'labor_cost': week.total_cost,
            'stock_variance_cost': week_stock_variance_cost,
            'profit': week_revenue - week_material_costs - week.total_cost,
            'adjusted_profit': week_revenue - week_material_costs - week.total_cost - (week_stock_variance_cost if week_stock_variance_cost < 0 else -week_stock_variance_cost),
            'sales_count': week_sales_count,
            'audit_count': len(week_audits),
            'food_cost': week_food_cost,
            'recipes_produced': week_recipes_produced
        })

        total_revenue += week_revenue
        total_labor_costs += week.total_cost
        total_material_costs += week_material_costs
        total_stock_variance_cost += week_stock_variance_cost

    # Convert sets to counts for category summaries
    for cat in category_summaries.values():
        cat['product_count'] = len(cat['products'])
        cat['weeks_active'] = len(cat['weeks_active'])
        cat['products'] = list(cat['products'])  # Convert set to list for template

    # Get top products by revenue
    product_list = list(product_aggregates.values())
    top_products = sorted(product_list, key=lambda x: x['revenue'], reverse=True)[:10]

    # Calculate total food cost and recipes from summaries
    total_food_cost = sum(week['food_cost'] for week in weekly_summaries)
    total_recipes_produced = sum(week['recipes_produced'] for week in weekly_summaries)
    avg_food_cost_per_recipe = total_food_cost / total_recipes_produced if total_recipes_produced > 0 else 0

    # Calculate average metrics
    num_weeks = len(weekly_costs)
    avg_weekly_revenue = total_revenue / num_weeks if num_weeks > 0 else 0
    avg_weekly_labor = total_labor_costs / num_weeks if num_weeks > 0 else 0
    avg_weekly_food_cost = total_food_cost / num_weeks if num_weeks > 0 else 0

    return render_template('monthly_report.html',
                         month=month,
                         year=year,
                         month_start=month_start,
                         month_end=month_end,
                         weekly_summaries=weekly_summaries,
                         product_totals=product_list,
                         category_summaries=category_summaries,
                         top_products=top_products,
                         total_revenue=total_revenue,
                         total_material_costs=total_material_costs,
                         total_labor_costs=total_labor_costs,
                         total_stock_variance_cost=total_stock_variance_cost,
                         net_profit=total_revenue - total_material_costs - total_labor_costs,
                         adjusted_profit=total_revenue - total_material_costs - total_labor_costs - (total_stock_variance_cost if total_stock_variance_cost < 0 else -total_stock_variance_cost),
                         avg_weekly_revenue=avg_weekly_revenue,
                         avg_weekly_labor=avg_weekly_labor,
                         avg_weekly_material=total_material_costs / num_weeks if num_weeks > 0 else 0,
                         avg_weekly_stock_variance=total_stock_variance_cost / num_weeks if num_weeks > 0 else 0,
                         avg_weekly_food_cost=avg_weekly_food_cost,
                         total_food_cost=total_food_cost,
                         total_recipes_produced=total_recipes_produced,
                         avg_food_cost_per_recipe=avg_food_cost_per_recipe,
                         num_weeks=num_weeks,
                         no_data=False)

# Stock Audits Page
@main_blueprint.route('/stock_audits')
def stock_audits():
    # Get filter parameters
    material_id = request.args.get('material_id', type=int)
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')

    # Base query
    query = StockAudit.query

    # Apply filters
    if material_id:
        query = query.filter_by(raw_material_id=material_id)

    if date_from:
        date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
        query = query.filter(StockAudit.audit_date >= date_from_obj)

    if date_to:
        date_to_obj = datetime.strptime(date_to, '%Y-%m-%d')
        # Add 1 day to include the entire end date
        date_to_obj = date_to_obj + timedelta(days=1)
        query = query.filter(StockAudit.audit_date < date_to_obj)

    # Get audits ordered by date
    audits = query.order_by(StockAudit.audit_date.desc()).all()

    # Calculate totals
    total_variance_cost = sum(audit.variance_cost for audit in audits)
    total_positive_variance = sum(audit.variance for audit in audits if audit.variance > 0)
    total_negative_variance = sum(audit.variance for audit in audits if audit.variance < 0)

    # Get materials for filter dropdown
    all_materials = RawMaterial.query.order_by(RawMaterial.name).all()

    # Get category-wise analysis
    category_analysis = {}
    for audit in audits:
        if audit.raw_material and audit.raw_material.category:
            cat_name = audit.raw_material.category.name
            if cat_name not in category_analysis:
                category_analysis[cat_name] = {
                    'count': 0,
                    'total_variance': 0,
                    'total_variance_cost': 0,
                    'materials': set()
                }
            category_analysis[cat_name]['count'] += 1
            category_analysis[cat_name]['total_variance'] += audit.variance
            category_analysis[cat_name]['total_variance_cost'] += audit.variance_cost
            category_analysis[cat_name]['materials'].add(audit.raw_material.name)

    # Convert sets to lists for template
    for cat in category_analysis.values():
        cat['materials'] = list(cat['materials'])

    print("DEBUG: Returning from stock_audits")
    return render_template('stock_audits.html',
                         audits=audits,
                         all_materials=all_materials,
                         total_variance_cost=total_variance_cost,
                         total_positive_variance=total_positive_variance,
                         total_negative_variance=total_negative_variance,
                         category_analysis=category_analysis)

def calculate_raw_material_current_stock(material_id):
    """
    Calculates the current stock of a given raw material based on StockLogs and ProductionLogs.
    """
    last_set_log = StockLog.query.filter_by(raw_material_id=material_id, action_type='set') \
        .order_by(StockLog.timestamp.desc()).first()
    stock = last_set_log.quantity if last_set_log else 0

    add_logs = StockLog.query.filter(
        StockLog.raw_material_id == material_id,
        StockLog.action_type == 'add',
        StockLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
    ).all()
    for log in add_logs:
        stock += log.quantity

    # Subtract raw materials used in produced products (both final products and premakes)
    # Note: This is an expensive operation if not optimized (e.g., pre-calculated sums)
    # For real-time stock calculation, it means iterating a lot of history. 
    # Assumes ProductionLogs timestamp is after initial stock or last set. 
    # We need to consider *all* production up to current point.
    # Let's just iterate over relevant production logs, but this can be slow.
    
    # Filter production logs by material usage
    production_logs_raw_material_usage = db.session.query(ProductionLog, ProductComponent).\
        join(ProductComponent, ProductionLog.product_id == ProductComponent.product_id).\
        filter(
            ProductComponent.component_type == 'raw_material',
            ProductComponent.component_id == material_id,
            ProductionLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
        ).all()
    
    for production_log, product_component in production_logs_raw_material_usage:
        stock -= product_component.quantity * production_log.quantity_produced
    
    # Also subtract usage in premake production 
    production_logs_premake_usage = db.session.query(ProductionLog, PremakeComponent).\
        join(Premake, ProductionLog.premake_id == Premake.id).\
        join(PremakeComponent, Premake.id == PremakeComponent.premake_id).\
        filter(
            PremakeComponent.component_type == 'raw_material',
            PremakeComponent.component_id == material_id,
            ProductionLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
        ).all()
    
    for production_log, premake_component in production_logs_premake_usage:
        stock -= premake_component.quantity * production_log.quantity_produced

    return stock

@main_blueprint.route('/api/product_recipe/<int:product_id>')
def get_product_recipe(product_id):
    product = Product.query.get_or_404(product_id)
    
    components_data = []
    for comp in product.components:
        if comp.component_type == 'raw_material':
            material = comp.material
            if material:
                stock = calculate_raw_material_current_stock(material.id)
                components_data.append({
                    'type': 'Raw Material',
                    'name': material.name,
                    'qty_per_batch': comp.quantity,
                    'unit': material.unit,
                    'current_stock': stock,
                    'cost_per_unit': material.cost_per_unit
                })
        
        elif comp.component_type == 'premake':
            premake = comp.premake
            if premake:
                stock = calculate_premake_current_stock(premake.id)
                
                # Calculate cost per unit for premake
                cost_per_batch = 0
                for pm_comp in premake.components:
                    if pm_comp.component_type == 'raw_material' and pm_comp.material:
                        cost_per_batch += pm_comp.quantity * pm_comp.material.cost_per_unit
                cost_per_unit = cost_per_batch / premake.batch_size if premake.batch_size > 0 else 0

                components_data.append({
                    'type': 'Premake',
                    'name': premake.name,
                    'qty_per_batch': comp.quantity,
                    'unit': premake.unit,
                    'current_stock': stock,
                    'cost_per_unit': cost_per_unit
                })

    return jsonify({
        'product_name': product.name,
        'products_per_recipe': product.products_per_recipe,
        'components': components_data
    })


