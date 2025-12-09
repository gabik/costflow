from datetime import datetime, date, timedelta
from flask import Blueprint, render_template, request
from sqlalchemy import func, and_
from ..models import WeeklyLaborCost, WeeklyProductSales, ProductionLog, StockAudit, Product
from .utils import calculate_prime_cost, calculate_premake_current_stock

reports_blueprint = Blueprint('reports', __name__)

# Weekly Report
@reports_blueprint.route('/reports/weekly')
def weekly_report():
    # 1. Fetch all weeks for dropdown
    all_weeks = WeeklyLaborCost.query.order_by(WeeklyLaborCost.week_start_date.desc()).all()

    # Get date parameters
    week_start = request.args.get('week_start')

    if not week_start:
        # Default to current week start (Sunday) if not specified
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

        # Filter out premakes - only show actual sellable products
        if not product.is_product or product.is_premake:
            continue

        # Calculate prime cost (materials + packaging + premakes)
        prime_cost_per_unit = calculate_prime_cost(product)

        # Calculate costs and revenue
        material_cost_sold = (sale.quantity_sold or 0) * prime_cost_per_unit
        material_cost_waste = (sale.quantity_waste or 0) * prime_cost_per_unit
        revenue = (sale.quantity_sold or 0) * (product.selling_price_per_unit or 0)

        total_material_costs += material_cost_sold + material_cost_waste
        total_revenue += revenue

        cat_name = product.category.name if product.category else 'ללא קטגוריה'

        # Calculate profit metrics
        profit_per_unit = (product.selling_price_per_unit or 0) - prime_cost_per_unit
        profit_margin_pct = (profit_per_unit / (product.selling_price_per_unit or 1) * 100) if product.selling_price_per_unit else 0

        # Add to sales data
        sales_data.append({
            'name': product.name,
            'category_name': cat_name,
            'selling_price_per_unit': product.selling_price_per_unit,
            'quantity_sold': sale.quantity_sold,
            'quantity_waste': sale.quantity_waste,
            'revenue': revenue,
            'prime_cost_per_unit': prime_cost_per_unit,
            'material_cost': material_cost_sold + material_cost_waste,
            'profit_per_unit': profit_per_unit,
            'profit_margin_pct': profit_margin_pct
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

    # Calculate Food Cost from Production - PRODUCTS ONLY
    # Premake costs are already included in product costs via calculate_prime_cost()
    production_logs = ProductionLog.query.join(Product).filter(
        and_(
            func.date(ProductionLog.timestamp) >= week_start,
            func.date(ProductionLog.timestamp) <= week_end,
            Product.is_product == True,  # Only actual products
            Product.is_premake == False   # Exclude premakes (avoid double counting)
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

    # Fetch Premake Production Logs for the week (using unified Product model)
    premake_logs = ProductionLog.query.join(Product).filter(
        and_(
            func.date(ProductionLog.timestamp) >= week_start,
            func.date(ProductionLog.timestamp) <= week_end,
            Product.is_premake == True
        )
    ).all()

    premake_production = {}
    premake_batches = {}  # Track batches separately for correct value calculation
    for log in premake_logs:
        product = log.product
        if not product:
            continue
        # log.quantity_produced is number of batches
        batches_produced = log.quantity_produced
        # Calculate total units (kg) produced
        units_produced = batches_produced * (product.batch_size or 1)

        premake_production[product.id] = premake_production.get(product.id, 0) + units_produced
        premake_batches[product.id] = premake_batches.get(product.id, 0) + batches_produced

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

    # Process Premakes for Report (using unified Product model)
    all_premakes = Product.query.filter_by(is_premake=True).all()
    for premake in all_premakes:
        produced_kg = premake_production.get(premake.id, 0)
        produced_batches = premake_batches.get(premake.id, 0)
        used = premake_usage.get(premake.id, 0)

        # Calculate current stock for premake
        current_premake_stock = calculate_premake_current_stock(premake.id)

        # Calculate cost per BATCH (what calculate_prime_cost returns for premakes)
        cost_per_batch = calculate_prime_cost(premake)
        # Calculate cost per kg for display
        cost_per_kg = cost_per_batch / (premake.batch_size or 1) if premake.batch_size else 0

        # Inventory Value Change (Produced - Used)
        stock_change = produced_kg - used

        if produced_kg == 0 and used == 0 and current_premake_stock == 0:
            continue

        premake_report_data.append({
            'name': premake.name,
            'unit': premake.unit,
            'produced': produced_kg,  # Show kg for consistency
            'batches_produced': produced_batches,  # For value calc
            'used': used,
            'stock_change': stock_change,
            'current_stock': current_premake_stock,
            'cost_per_unit': cost_per_kg,  # Cost per kg for display
            'cost_per_batch': cost_per_batch,  # For correct value calc
            'total_value_produced': produced_batches * cost_per_batch  # FIXED: batches × $/batch
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

    # ---------------------------------------------------
    # Waste Analysis
    # ---------------------------------------------------
    waste_details = []
    waste_by_category = {}

    for sale in week_sales:
        product = sale.product
        if not product or not product.is_product or product.is_premake:
            continue

        if (sale.quantity_waste or 0) > 0 or (sale.quantity_sold or 0) > 0:
            prime_cost = calculate_prime_cost(product)
            waste_qty = sale.quantity_waste or 0
            sold_qty = sale.quantity_sold or 0
            total_qty = waste_qty + sold_qty

            waste_cost = waste_qty * prime_cost
            waste_pct = (waste_qty / total_qty * 100) if total_qty > 0 else 0

            cat_name = product.category.name if product.category else 'ללא קטגוריה'

            # Per-product waste
            waste_details.append({
                'product_name': product.name,
                'category_name': cat_name,
                'quantity_sold': sold_qty,
                'quantity_waste': waste_qty,
                'total_produced': total_qty,
                'waste_cost': waste_cost,
                'waste_pct': waste_pct,
                'prime_cost_per_unit': prime_cost
            })

            # By category
            if cat_name not in waste_by_category:
                waste_by_category[cat_name] = {
                    'total_waste_qty': 0,
                    'total_waste_cost': 0,
                    'total_produced_qty': 0
                }
            waste_by_category[cat_name]['total_waste_qty'] += waste_qty
            waste_by_category[cat_name]['total_waste_cost'] += waste_cost
            waste_by_category[cat_name]['total_produced_qty'] += total_qty

    # Calculate category waste %
    for cat_data in waste_by_category.values():
        cat_data['waste_pct'] = (cat_data['total_waste_qty'] / cat_data['total_produced_qty'] * 100) if cat_data['total_produced_qty'] > 0 else 0

    # Sort by waste cost descending
    waste_details.sort(key=lambda x: x['waste_cost'], reverse=True)

    # Total waste metrics
    total_waste_qty = sum(w['quantity_waste'] for w in waste_details)
    total_waste_cost = sum(w['waste_cost'] for w in waste_details)
    total_produced_qty = sum(w['total_produced'] for w in waste_details)
    overall_waste_pct = (total_waste_qty / total_produced_qty * 100) if total_produced_qty > 0 else 0

    # ---------------------------------------------------
    # Profit Insights
    # ---------------------------------------------------
    # Best profit margin product
    best_margin_product = max(sales_data, key=lambda x: x['profit_margin_pct']) if sales_data else None

    # Worst profit margin product (with sales > 0)
    products_with_sales = [s for s in sales_data if s['quantity_sold'] > 0]
    worst_margin_product = min(products_with_sales, key=lambda x: x['profit_margin_pct']) if products_with_sales else None

    # Highest total profit product
    highest_profit_product = max(sales_data, key=lambda x: x['profit_per_unit'] * (x['quantity_sold'] or 0)) if sales_data else None

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
                         waste_details=waste_details,
                         waste_by_category=waste_by_category,
                         total_waste_cost=total_waste_cost,
                         overall_waste_pct=overall_waste_pct,
                         best_margin_product=best_margin_product,
                         worst_margin_product=worst_margin_product,
                         highest_profit_product=highest_profit_product,
                         no_data=False)

# Monthly Report - Aggregating Weekly Reports
@reports_blueprint.route('/reports/monthly')
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
            sale_revenue = (sale.quantity_sold or 0) * (product.selling_price_per_unit or 0)

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
