import os
import pandas as pd
import json
import io
from datetime import datetime, date, timedelta
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, current_app, send_file, jsonify
from sqlalchemy import func, extract, and_, text
from ..models import db, RawMaterial, Labor, Packaging, Product, ProductComponent, Category, StockLog, ProductionLog, WeeklyLaborCost, WeeklyLaborEntry, WeeklyProductSales, StockAudit, AuditLog, Premake, PremakeComponent
from .utils import units_list, get_or_create_general_category, convert_to_base_unit, log_audit, calculate_prime_cost, calculate_premake_current_stock
from .raw_materials import calculate_raw_material_current_stock

main_blueprint = Blueprint('main', __name__)

@main_blueprint.route('/images/<path:filename>')
def serve_image(filename):
    """Serve product images from the persistent /images volume."""
    import os
    from flask import send_from_directory, abort

    # Security: Only allow specific image extensions
    allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
    if not any(filename.lower().endswith(ext) for ext in allowed_extensions):
        abort(404)

    # Serve the image from /images directory
    images_dir = current_app.config.get('UPLOAD_FOLDER', '/images')

    # Check if file exists
    file_path = os.path.join(images_dir, filename)
    if not os.path.exists(file_path):
        abort(404)

    return send_from_directory(images_dir, filename)







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
                'is_migrated': product.is_migrated if hasattr(product, 'is_migrated') else False,
                'migrated_to_premake': product.migrated_to_premake.name if hasattr(product, 'migrated_to_premake') and product.migrated_to_premake else None,
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
            
            components_data.append({'id': material_id, 'qty': final_quantity, 'type': 'raw_material'})

        # Process premakes
        premake_ids = request.form.getlist('premake[]')
        premake_quantities = request.form.getlist('premake_quantity[]')
        for premake_id, quantity in zip(premake_ids, premake_quantities):
            if not premake_id or not quantity or float(quantity) <= 0:
                continue
            components_data.append({'id': premake_id, 'qty': float(quantity), 'type': 'premake'})
            # Note: We don't add premake quantities to batch_size as they're already processed items

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
            component_type = item.get('type', 'raw_material')  # Default to raw_material for backward compatibility
            component = PremakeComponent(
                premake_id=premake.id,
                component_type=component_type,
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
    all_premakes = [p.to_dict() for p in Premake.query.all()]  # Add available premakes for nesting
    premake_categories = Category.query.filter_by(type='premake').all()
    categories = Category.query.filter_by(type='raw_material').all() # For raw material modal if needed

    return render_template(
        'add_or_edit_premake.html',
        premake=None,
        all_raw_materials=all_raw_materials,
        all_premakes=all_premakes,  # Pass premakes to template
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

        # Process premakes
        premake_ids = request.form.getlist('premake[]')
        premake_quantities = request.form.getlist('premake_quantity[]')
        for sub_premake_id, quantity in zip(premake_ids, premake_quantities):
            if not sub_premake_id or not quantity or float(quantity) <= 0:
                continue
            # Check to prevent self-referencing
            if int(sub_premake_id) == premake.id:
                continue
            component = PremakeComponent(
                premake_id=premake.id,
                component_type='premake',
                component_id=sub_premake_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        premake.batch_size = batch_size
            
        log_audit("UPDATE", "Premake", premake.id, f"Updated premake {premake.name}")
        db.session.commit()
        return redirect(url_for('main.premakes'))

    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    print(f"DEBUG: edit_premake - Found {len(all_raw_materials)} raw materials")
    # Filter out the current premake to avoid self-reference
    all_premakes = [p.to_dict() for p in Premake.query.all() if p.id != premake_id]
    premake_categories = Category.query.filter_by(type='premake').all()
    categories = Category.query.filter_by(type='raw_material').all()

    return render_template(
        'add_or_edit_premake.html',
        premake=premake,
        all_raw_materials=all_raw_materials,
        all_premakes=all_premakes,  # Pass premakes to template
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









# Weekly Report
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


