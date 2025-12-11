from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, redirect, url_for
from sqlalchemy import func
from ..models import db, WeeklyLaborCost, WeeklyLaborEntry, WeeklyProductSales, Product, ProductionLog, Labor, StockLog
from .utils import log_audit, calculate_prime_cost, hours_to_time_str, time_str_to_hours

weekly_costs_blueprint = Blueprint('weekly_costs', __name__)

# ----------------------------
# Weekly Costs Management
# ----------------------------
@weekly_costs_blueprint.route('/weekly_costs', methods=['GET', 'POST'])
def weekly_costs():
    if request.method == 'POST':
        date_str = request.form.get('week_start_date')
        if date_str:
            week_start = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            # Check for leftovers in previous week unless forced
            if request.form.get('force_create') != 'true':
                previous_week = WeeklyLaborCost.query.filter(WeeklyLaborCost.week_start_date < week_start).order_by(WeeklyLaborCost.week_start_date.desc()).first()
                
                if previous_week:
                    # Calculate Leftovers
                    prev_start = previous_week.week_start_date
                    prev_end = prev_start + timedelta(days=6)
                    
                    logs = ProductionLog.query.filter(
                        func.date(ProductionLog.timestamp) >= prev_start,
                        func.date(ProductionLog.timestamp) <= prev_end
                    ).all()
                    
                    product_production = {}
                    for log in logs:
                        if log.product:
                            units = log.quantity_produced * log.product.products_per_recipe
                            product_production[log.product_id] = product_production.get(log.product_id, 0) + units
                            
                    product_sales = {s.product_id: {'sold': s.quantity_sold, 'waste': s.quantity_waste} for s in previous_week.sales}
                    
                    leftovers = []
                    premake_leftovers = []
                    total_loss = 0
                    total_potential_revenue = 0
                    
                    # Product Leftovers
                    # Filter out migrated products
                    all_products = Product.query.filter_by(is_migrated=False).all()
                    for product in all_products:
                        produced = product_production.get(product.id, 0)
                        sales_data = product_sales.get(product.id, {'sold': 0, 'waste': 0})
                        remaining = produced - sales_data['sold'] - sales_data['waste']
                        
                        if remaining > 0:
                            # Calculate Prime Cost
                            prime_cost_per_unit = calculate_prime_cost(product)

                            cost_value = remaining * prime_cost_per_unit
                            potential_rev = remaining * (product.selling_price_per_unit or 0)

                            total_loss += cost_value
                            total_potential_revenue += potential_rev
                            
                            leftovers.append({
                                'product': product,
                                'quantity': remaining,
                                'cost_value': cost_value,
                                'potential_revenue': potential_rev
                            })

                    # Premake Leftovers (Produced - Used)
                    # Fetch Premake Production Logs (premakes are products with is_premake=True)
                    # First get IDs of all premake products
                    premake_ids = [p.id for p in Product.query.filter_by(is_premake=True).all()]

                    premake_logs = ProductionLog.query.filter(
                        func.date(ProductionLog.timestamp) >= prev_start,
                        func.date(ProductionLog.timestamp) <= prev_end,
                        ProductionLog.product_id.in_(premake_ids)
                    ).all()

                    premake_production_qty = {}
                    for log in premake_logs:
                        if log.product and log.product.is_premake:
                            units_produced = log.quantity_produced * log.product.batch_size
                            premake_production_qty[log.product_id] = premake_production_qty.get(log.product_id, 0) + units_produced

                    # Calculate Premake Usage from Product Logs
                    premake_usage_qty = {}
                    for log in logs:
                        if log.product:
                            for component in log.product.components:
                                if component.component_type == 'premake':
                                    usage = component.quantity * log.quantity_produced
                                    premake_usage_qty[component.component_id] = premake_usage_qty.get(component.component_id, 0) + usage

                    all_premakes = Product.query.filter_by(is_premake=True).all()
                    for premake in all_premakes:
                        produced = premake_production_qty.get(premake.id, 0)
                        used = premake_usage_qty.get(premake.id, 0)
                        remaining = produced - used
                        
                        if remaining > 0:
                            # Calculate cost per unit
                            cost_per_batch = 0
                            for comp in premake.components:
                                if comp.component_type == 'raw_material' and comp.material:
                                    # Get supplier price
                                    supplier_price = 0
                                    for link in comp.material.supplier_links:
                                        if link.is_primary:
                                            supplier_price = link.cost_per_unit
                                            break
                                    if supplier_price == 0 and comp.material.supplier_links:
                                        supplier_price = comp.material.supplier_links[0].cost_per_unit
                                    cost_per_batch += comp.quantity * supplier_price
                            
                            cost_per_unit = cost_per_batch / premake.batch_size if premake.batch_size > 0 else 0
                            cost_value = remaining * cost_per_unit
                            
                            total_loss += cost_value
                            
                            premake_leftovers.append({
                                'premake': premake,
                                'quantity': remaining,
                                'cost_value': cost_value
                            })

                    # Calculate Packaging Stock
                    from .utils import calculate_packaging_stock
                    from ..models import Packaging

                    packaging_leftovers = []
                    all_packaging = Packaging.query.all()
                    for packaging in all_packaging:
                        current_stock = calculate_packaging_stock(packaging.id)
                        if current_stock > 0:
                            cost_value = current_stock * packaging.price_per_unit
                            total_loss += cost_value

                            packaging_leftovers.append({
                                'packaging': packaging,
                                'quantity': current_stock,
                                'cost_value': cost_value
                            })

                    if leftovers or premake_leftovers or packaging_leftovers:
                        return render_template('close_week.html',
                                               leftovers=leftovers,
                                               premake_leftovers=premake_leftovers,
                                               packaging_leftovers=packaging_leftovers,
                                               previous_week=previous_week,
                                               new_week_start_date=date_str,
                                               total_loss=total_loss,
                                               total_potential_revenue=total_potential_revenue)

            # Create Week (if no leftovers or forced)
            week = WeeklyLaborCost.query.filter_by(week_start_date=week_start).first()
            if not week:
                week = WeeklyLaborCost(week_start_date=week_start, total_cost=0)
                db.session.add(week)
                db.session.commit()
            return redirect(url_for('weekly_costs.weekly_cost_details', week_id=week.id))

    weekly_costs = WeeklyLaborCost.query.order_by(WeeklyLaborCost.week_start_date.desc()).all()
    return render_template('weekly_costs.html', weekly_costs=weekly_costs)

@weekly_costs_blueprint.route('/close_week_confirm', methods=['POST'])
def close_week_confirm():
    prev_week_id = request.form.get('previous_week_id')
    new_week_date = request.form.get('new_week_start_date')
    
    prev_week = WeeklyLaborCost.query.get_or_404(prev_week_id)
    
    # Re-calculate leftovers to ensure data integrity
    prev_start = prev_week.week_start_date
    prev_end = prev_start + timedelta(days=6)
    
    # 1. Product Leftovers
    logs = ProductionLog.query.filter(
        func.date(ProductionLog.timestamp) >= prev_start,
        func.date(ProductionLog.timestamp) <= prev_end
    ).all()
    
    product_production = {}
    for log in logs:
        if log.product:
            units = log.quantity_produced * log.product.products_per_recipe
            product_production[log.product_id] = product_production.get(log.product_id, 0) + units
            
    product_sales = {s.product_id: s for s in prev_week.sales}

    # Filter out migrated products
    all_products = Product.query.filter_by(is_migrated=False).all()
    new_week_start_dt = datetime.strptime(new_week_date, '%Y-%m-%d')

    for product in all_products:
        produced = product_production.get(product.id, 0)
        sale_record = product_sales.get(product.id)
        sold = sale_record.quantity_sold if sale_record else 0
        waste = sale_record.quantity_waste if sale_record else 0
        
        remaining = produced - sold - waste
        
        if remaining > 0:
            # Check if user marked this to be kept (not wasted)
            keep_key = f"keep_product_{product.id}"
            if keep_key in request.form:
                # Create Carryover Log for New Week
                # Convert units back to recipe qty
                if product.products_per_recipe > 0:
                    qty_recipes = remaining / product.products_per_recipe
                    
                    carryover_log = ProductionLog(
                        product_id=product.id,
                        quantity_produced=qty_recipes,
                        timestamp=new_week_start_dt,
                        is_carryover=True
                    )
                    db.session.add(carryover_log)
                continue # Skip wasting

            if sale_record:
                sale_record.quantity_waste += remaining
            else:
                new_sale = WeeklyProductSales(
                    weekly_cost_id=prev_week.id,
                    product_id=product.id,
                    quantity_sold=0,
                    quantity_waste=remaining
                )
                db.session.add(new_sale)

    # 2. Premake Leftovers
    # Fetch Premake Production
    # Get IDs of all premake products
    premake_ids = [p.id for p in Product.query.filter_by(is_premake=True).all()]

    premake_logs = ProductionLog.query.filter(
        func.date(ProductionLog.timestamp) >= prev_start,
        func.date(ProductionLog.timestamp) <= prev_end,
        ProductionLog.product_id.in_(premake_ids)
    ).all()

    premake_production_qty = {}
    for log in premake_logs:
        if log.product and log.product.is_premake:
            units_produced = log.quantity_produced * log.product.batch_size
            premake_production_qty[log.product_id] = premake_production_qty.get(log.product_id, 0) + units_produced

    # Calculate Premake Usage from Product Logs (re-using 'logs' fetched above)
    premake_usage_qty = {}
    for log in logs:
        if log.product:
            for component in log.product.components:
                if component.component_type == 'premake':
                    usage = component.quantity * log.quantity_produced
                    premake_usage_qty[component.component_id] = premake_usage_qty.get(component.component_id, 0) + usage

    all_premakes = Product.query.filter_by(is_premake=True).all()
    for premake in all_premakes:
        # Check if user marked to keep
        keep_key = f"keep_premake_{premake.id}"
        if keep_key in request.form:
            continue # Skip wasting (it stays in stock)

        # User chose to waste this premake - set stock to 0
        # Use 'set' action to ensure complete zeroing regardless of beginning stock
        from .utils import calculate_premake_current_stock, calculate_premake_cost_per_unit
        current_stock = calculate_premake_current_stock(premake.id)

        if current_stock > 0:
            # Calculate cost of wasted stock for audit trail
            cost_per_unit = calculate_premake_cost_per_unit(premake)
            waste_cost = current_stock * cost_per_unit

            # Set stock to 0 (this accounts for beginning stock + weekly production - usage)
            stock_log = StockLog(
                product_id=premake.id,
                action_type='set',  # Use 'set' not 'add'
                quantity=0,  # Set to 0, not negative remaining
                timestamp=new_week_start_dt
            )
            db.session.add(stock_log)

            # Log the waste for audit trail
            log_audit("PREMAKE_WASTE", "Product", premake.id,
                     f"Wasted {current_stock:.2f} {premake.unit} of {premake.name} (Cost: ₪{waste_cost:.2f}) when closing week {prev_week.week_start_date}")

    # 3. Packaging Leftovers
    from .utils import calculate_packaging_stock
    from ..models import Packaging

    all_packaging = Packaging.query.all()
    for packaging in all_packaging:
        # Check if user marked to keep
        keep_key = f"keep_packaging_{packaging.id}"
        if keep_key in request.form:
            continue  # Skip wasting (it stays in stock)

        # User chose to waste this packaging - set stock to 0
        current_stock = calculate_packaging_stock(packaging.id)

        if current_stock > 0:
            # Calculate cost of wasted stock for audit trail
            waste_cost = current_stock * packaging.price_per_unit

            # Set stock to 0
            stock_log = StockLog(
                packaging_id=packaging.id,
                action_type='set',  # Use 'set' not 'add'
                quantity=0,  # Set to 0, not negative
                timestamp=new_week_start_dt
            )
            db.session.add(stock_log)

            # Log the waste for audit trail
            log_audit("PACKAGING_WASTE", "Packaging", packaging.id,
                     f"Wasted {current_stock:.2f} units of {packaging.name} (Cost: ₪{waste_cost:.2f}) when closing week {prev_week.week_start_date}")

    db.session.commit()
    log_audit("CLOSE_WEEK", "WeeklySales", prev_week.id, f"Closed week {prev_week.week_start_date}. Processed leftovers.")

    # Create New Week
    week_start = datetime.strptime(new_week_date, '%Y-%m-%d').date()
    week = WeeklyLaborCost.query.filter_by(week_start_date=week_start).first()
    if not week:
        week = WeeklyLaborCost(week_start_date=week_start, total_cost=0)
        db.session.add(week)
        db.session.commit()
        
    return redirect(url_for('weekly_costs.weekly_cost_details', week_id=week.id))

@weekly_costs_blueprint.route('/weekly_costs/<int:week_id>', methods=['GET'])
def weekly_cost_details(week_id):
    week = WeeklyLaborCost.query.get_or_404(week_id)
    employees = Labor.query.all()
    return render_template('weekly_cost_details.html', week=week, employees=employees, hours_to_time_str=hours_to_time_str)

@weekly_costs_blueprint.route('/weekly_costs/<int:week_id>/add', methods=['POST'])
def add_weekly_labor(week_id):
    week = WeeklyLaborCost.query.get_or_404(week_id)
    employee_id = request.form.get('employee_id')
    hours_input = request.form.get('hours')

    # Convert time string (HH:MM) to decimal hours
    if ':' in hours_input:
        hours = time_str_to_hours(hours_input)
    else:
        # Allow backward compatibility with decimal input
        try:
            hours = float(hours_input)
        except ValueError:
            hours = 0

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

    return redirect(url_for('weekly_costs.weekly_cost_details', week_id=week.id))

@weekly_costs_blueprint.route('/weekly_costs/<int:week_id>/delete/<int:entry_id>', methods=['POST'])
def delete_weekly_labor(week_id, entry_id):
    week = WeeklyLaborCost.query.get_or_404(week_id)
    entry = WeeklyLaborEntry.query.get_or_404(entry_id)
    
    if entry.weekly_cost_id == week.id:
        week.total_cost -= entry.cost
        db.session.delete(entry)
        db.session.commit()
        
    return redirect(url_for('weekly_costs.weekly_cost_details', week_id=week.id))

@weekly_costs_blueprint.route('/weekly_sales/<int:week_id>', methods=['GET', 'POST'])
def update_weekly_sales(week_id):
    week = WeeklyLaborCost.query.get_or_404(week_id)
    # Filter out migrated products and premakes - only show products and preproducts (sellable items)
    products = Product.query.filter(
        Product.is_migrated == False,
        db.or_(Product.is_product == True, Product.is_preproduct == True)
    ).all()
    
    if request.method == 'POST':
        for product in products:
            key_sales = f"sales_{product.id}"
            key_waste = f"waste_{product.id}"
            
            new_sold = request.form.get(key_sales)
            new_waste = request.form.get(key_waste)
            
            if new_sold is not None or new_waste is not None:
                new_sold = int(new_sold) if new_sold else 0
                new_waste = int(new_waste) if new_waste else 0
                
                # Find existing
                sale = WeeklyProductSales.query.filter_by(weekly_cost_id=week.id, product_id=product.id).first()
                
                if sale:
                    # Deduct packaging for newly sold quantity
                    if new_sold > 0:
                        from .utils import deduct_packaging_for_sales
                        deduct_packaging_for_sales(product.id, new_sold)

                    sale.quantity_sold += new_sold
                    sale.quantity_waste += new_waste
                else:
                    sale = WeeklyProductSales(
                        weekly_cost_id=week.id,
                        product_id=product.id,
                        quantity_sold=new_sold,
                        quantity_waste=new_waste
                    )
                    db.session.add(sale)

                    # Deduct packaging for newly sold quantity
                    if new_sold > 0:
                        from .utils import deduct_packaging_for_sales
                        deduct_packaging_for_sales(product.id, new_sold)

        db.session.commit()
        log_audit("UPDATE", "WeeklySales", week.id, f"Added sales/waste for week {week.week_start_date}")
        return redirect(url_for('main.index', week_id=week.id))

    # Calculate Production for this week (Limit for sales)
    week_start = week.week_start_date
    week_end = week_start + timedelta(days=6)
    
    logs = ProductionLog.query.filter(
        func.date(ProductionLog.timestamp) >= week_start,
        func.date(ProductionLog.timestamp) <= week_end
    ).all()
    
    production_map = {}
    for log in logs:
        # Ensure we use the product associated with the log to get the recipe multiplier
        if log.product:
            units = log.quantity_produced * log.product.products_per_recipe
            production_map[log.product_id] = production_map.get(log.product_id, 0) + units

    # Create a map of existing sales for easy lookup in template
    sales_map = {s.product_id: {'sold': s.quantity_sold, 'waste': s.quantity_waste} for s in week.sales}
    
    return render_template('update_weekly_sales.html', week=week, products=products, sales_map=sales_map, production_map=production_map)
