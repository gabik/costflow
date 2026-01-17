import os
import pandas as pd
import json
import io
from collections import defaultdict
from datetime import datetime, date, timedelta
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, current_app, send_file, jsonify
from sqlalchemy import func, extract, and_, text
from sqlalchemy.orm import joinedload
from ..models import db, RawMaterial, Labor, Packaging, Product, ProductComponent, Category, StockLog, ProductionLog, WeeklyLaborCost, WeeklyLaborEntry, WeeklyProductSales, StockAudit, AuditLog, PackagingSupplier
from .utils import units_list, get_or_create_general_category, convert_to_base_unit, log_audit, calculate_prime_cost, calculate_premake_current_stock, calculate_premake_stock_at_date, calculate_total_material_stock, calculate_supplier_stock, apply_supplier_discount, calculate_total_packaging_stock, safe_float, convert_cost_to_display_unit
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
    total_sales_stock_value = 0
    total_packaging_stock_value = 0

    if selected_week:
        week_start = selected_week.week_start_date
        week_end = week_start + timedelta(days=6)
        total_labor = selected_week.total_cost

        # ========== OPTIMIZATION: Batch load all data upfront ==========

        # Load ALL products with their components eagerly (single query)
        all_products_list = Product.query.options(
            joinedload(Product.components)
        ).filter_by(is_archived=False).all()

        # Create lookup dictionaries
        all_products_map = {p.id: p for p in all_products_list}
        all_premakes = [p for p in all_products_list if p.is_premake]
        all_non_premakes = [p for p in all_products_list if not p.is_premake]
        premake_ids = [p.id for p in all_premakes]

        # Batch load production logs with product relationship
        logs = ProductionLog.query.options(
            joinedload(ProductionLog.product)
        ).filter(
            func.date(ProductionLog.timestamp) >= week_start,
            func.date(ProductionLog.timestamp) <= week_end,
            ProductionLog.product_id != None
        ).all()

        # Separate logs by type using the map (no additional queries)
        product_logs = []
        premake_logs = []
        for log in logs:
            if log.product_id in premake_ids:
                premake_logs.append(log)
            else:
                product_logs.append(log)

        # ========== Process Production Data ==========

        # Map Product Production
        product_production = {}
        for log in product_logs:
            product = all_products_map.get(log.product_id)
            if not product:
                continue
            units_produced = log.quantity_produced * product.products_per_recipe

            if log.product_id not in product_production:
                product_production[log.product_id] = {'total': 0, 'new': 0}

            product_production[log.product_id]['total'] += units_produced
            if not log.is_carryover:
                product_production[log.product_id]['new'] += units_produced

        # Map Premake Production
        premake_production = {}
        for log in premake_logs:
            premake = all_products_map.get(log.product_id)
            if premake and premake.is_premake:
                units_produced = log.quantity_produced * (premake.batch_size or 1)
                premake_production[log.product_id] = premake_production.get(log.product_id, 0) + units_produced

        # Calculate Premake Usage (from non-carryover product logs)
        premake_usage = {}
        for log in product_logs:
            if log.is_carryover:
                continue
            product = all_products_map.get(log.product_id)
            if not product:
                continue
            for component in product.components:
                if component.component_type == 'premake':
                    usage = component.quantity * log.quantity_produced
                    premake_usage[component.component_id] = premake_usage.get(component.component_id, 0) + usage

        # ========== Batch calculate stocks ==========

        # Simplified approach: Get all stock logs and calculate in Python
        product_ids_to_check = [p.id for p in all_products_list]

        stock_cache = {}
        if product_ids_to_check:
            # Get ALL stock logs for products in a single query, ordered by timestamp
            all_stock_logs = StockLog.query.filter(
                StockLog.product_id.in_(product_ids_to_check)
            ).order_by(StockLog.product_id, StockLog.timestamp).all()

            # Group by product_id and calculate stock in Python
            logs_by_product = defaultdict(list)
            for log in all_stock_logs:
                logs_by_product[log.product_id].append(log)

            for pid in product_ids_to_check:
                product_logs = logs_by_product.get(pid, [])
                stock = 0
                for log in product_logs:
                    if log.action_type == 'set':
                        stock = log.quantity
                    elif log.action_type == 'add':
                        stock += log.quantity
                stock_cache[pid] = stock

        # ========== Batch load material prices ==========
        # Pre-fetch all raw material prices to avoid N+1 queries
        from ..models import RawMaterialSupplier
        all_material_ids = set()
        for p in all_products_list:
            for comp in p.components:
                if comp.component_type == 'raw_material':
                    all_material_ids.add(comp.component_id)

        # Get primary supplier prices for all materials in one query
        material_price_cache = {}
        if all_material_ids:
            primary_prices = db.session.query(
                RawMaterialSupplier.raw_material_id,
                RawMaterialSupplier.cost_per_unit
            ).filter(
                RawMaterialSupplier.raw_material_id.in_(all_material_ids),
                RawMaterialSupplier.is_primary == True
            ).all()
            material_price_cache = {r.raw_material_id: r.cost_per_unit for r in primary_prices}

        # ========== Process Premakes ==========
        for premake in all_premakes:
            produced = premake_production.get(premake.id, 0)
            used = premake_usage.get(premake.id, 0)

            # Use cached stock instead of individual queries
            current_premake_stock = stock_cache.get(premake.id, 0)

            # Calculate beginning stock as current - produced + used (approximation to avoid expensive query)
            beginning_stock = current_premake_stock - produced + used

            # Calculate Cost Per Unit using cached prices
            cost_per_batch = 0
            for comp in premake.components:
                if comp.component_type == 'raw_material':
                    supplier_price = material_price_cache.get(comp.component_id, 0)
                    cost_per_batch += comp.quantity * supplier_price

            cost_per_unit = cost_per_batch / premake.batch_size if premake.batch_size and premake.batch_size > 0 else 0

            # Inventory Value Change (Produced - Used)
            stock_change = produced - used

            # Add NET value change to total inventory usage
            total_inventory_usage += stock_change * cost_per_unit

            if produced == 0 and used == 0 and current_premake_stock == 0:
                continue

            premake_report_data.append({
                'name': premake.name,
                'unit': premake.unit,
                'beginning_stock': beginning_stock,
                'produced': produced,
                'used': used,
                'stock_change': stock_change,
                'current_stock': current_premake_stock,
                'cost_per_unit': cost_per_unit,
                'total_value_produced': produced * cost_per_unit
            })

        # Map Sales
        product_sales = {s.product_id: {'sold': s.quantity_sold, 'waste': s.quantity_waste} for s in selected_week.sales}

        # Use pre-fetched non-premake products (no additional query)
        for product in all_non_premakes:
            prod_data = product_production.get(product.id, {'total': 0, 'new': 0})
            produced_qty = prod_data['total']
            produced_qty_new = prod_data['new']

            sales_data = product_sales.get(product.id, {'sold': 0, 'waste': 0})
            sold_qty = sales_data['sold']
            waste_qty = sales_data['waste']

            # Calculate prime cost using cached material prices (fast)
            prime_cost_per_unit = 0
            if product.products_per_recipe and product.products_per_recipe > 0:
                total_recipe_cost = 0
                for comp in product.components:
                    if comp.component_type == 'raw_material':
                        price = material_price_cache.get(comp.component_id, 0)
                        total_recipe_cost += comp.quantity * price
                    elif comp.component_type == 'premake':
                        # Use premake cost from our cache
                        premake_obj = all_products_map.get(comp.component_id)
                        if premake_obj:
                            premake_cost = 0
                            for pc in premake_obj.components:
                                if pc.component_type == 'raw_material':
                                    premake_cost += pc.quantity * material_price_cache.get(pc.component_id, 0)
                            if premake_obj.batch_size:
                                premake_cost_per_unit = premake_cost / premake_obj.batch_size
                            else:
                                premake_cost_per_unit = 0
                            total_recipe_cost += comp.quantity * premake_cost_per_unit
                prime_cost_per_unit = total_recipe_cost / product.products_per_recipe

            # Financials
            selling_price = product.selling_price_per_unit or 0
            revenue = sold_qty * selling_price
            cogs = sold_qty * prime_cost_per_unit
            waste_cost = waste_qty * prime_cost_per_unit

            # Calculate production cost for unsold inventory
            production_cost = produced_qty_new * prime_cost_per_unit

            # Gross profit
            gross_profit = revenue - cogs - waste_cost - production_cost

            # Inventory Usage Value
            inventory_usage_value = produced_qty_new * prime_cost_per_unit

            # Add to total (Products)
            total_inventory_usage += inventory_usage_value

            # Use cached stock instead of individual query
            available_qty = stock_cache.get(product.id, 0)
            if available_qty < 0: available_qty = 0

            # Unsold Value (cost basis)
            total_unsold_value += available_qty * prime_cost_per_unit

            # Sales Stock Value (potential revenue from unsold stock)
            selling_price = product.selling_price_per_unit or 0
            total_sales_stock_value += available_qty * selling_price

            total_revenue += revenue
            total_cogs += cogs
            # total_inventory_usage was updated above

            # Only add to report if active this week OR has stock
            if produced_qty == 0 and sold_qty == 0 and waste_qty == 0 and available_qty == 0:
                continue

            report_data.append({
                'product_name': product.name,
                'product_image': product.image_filename,
                'produced_qty': produced_qty,
                'sold_qty': sold_qty,
                'waste_qty': waste_qty,
                'available_qty': available_qty,
                'prime_cost': prime_cost_per_unit,
                'selling_price': selling_price,
                'revenue': revenue,
                'gross_profit': gross_profit
            })

    # Calculate total packaging stock value (optimized batch query)
    # Get all packaging with supplier links in one query
    all_packaging = Packaging.query.options(
        joinedload(Packaging.supplier_links)
    ).all()

    # Batch calculate packaging stocks (using set/add logic like products)
    packaging_ids = [pkg.id for pkg in all_packaging]
    packaging_stock_cache = {}
    if packaging_ids:
        # Get ALL stock logs for packaging in a single query, ordered by timestamp
        all_packaging_logs = StockLog.query.filter(
            StockLog.packaging_id.in_(packaging_ids)
        ).order_by(StockLog.packaging_id, StockLog.timestamp).all()

        # Group by packaging_id and calculate stock in Python
        packaging_logs_by_id = defaultdict(list)
        for log in all_packaging_logs:
            packaging_logs_by_id[log.packaging_id].append(log)

        for pkg_id in packaging_ids:
            pkg_logs = packaging_logs_by_id.get(pkg_id, [])
            stock = 0
            for log in pkg_logs:
                if log.action_type == 'set':
                    stock = log.quantity
                elif log.action_type == 'add':
                    stock += log.quantity
            packaging_stock_cache[pkg_id] = stock

    for pkg in all_packaging:
        total_stock = packaging_stock_cache.get(pkg.id, 0)
        if total_stock > 0:
            # Get primary supplier price (already loaded via joinedload)
            primary_link = None
            for link in pkg.supplier_links:
                if link.is_primary:
                    primary_link = link
                    break
            if not primary_link and pkg.supplier_links:
                primary_link = pkg.supplier_links[0]

            if primary_link:
                price_per_package = primary_link.price_per_package or 0
                price_per_unit = price_per_package / pkg.quantity_per_package if pkg.quantity_per_package > 0 else 0
                total_packaging_stock_value += total_stock * price_per_unit

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
                           total_sales_stock_value=total_sales_stock_value,
                           total_packaging_stock_value=total_packaging_stock_value,
                           net_profit=net_profit,
                           adjusted_profit=adjusted_profit,
                           total_stock_variance=total_stock_variance,
                           stock_audit_count=stock_audit_count,
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
    all_materials = RawMaterial.query.filter_by(is_deleted=False).order_by(RawMaterial.name).all()

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
        elif audit.packaging:
            # Handle packaging items
            cat_name = 'Packaging'
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
            category_analysis[cat_name]['materials'].add(audit.packaging.name)

    # Convert sets to lists for template
    for cat in category_analysis.values():
        cat['materials'] = list(cat['materials'])

    return render_template('stock_audits.html',
                         audits=audits,
                         all_materials=all_materials,
                         total_variance_cost=total_variance_cost,
                         total_positive_variance=total_positive_variance,
                         total_negative_variance=total_negative_variance,
                         category_analysis=category_analysis)


@main_blueprint.route('/api/product_recipe/<int:product_id>')
def get_product_recipe(product_id):
    """API endpoint to get product recipe with consumption breakdown"""
    product = Product.query.get_or_404(product_id)

    # Get quantity being produced (if provided)
    quantity_produced = request.args.get('quantity', 1, type=float)

    components_data = []
    for comp in product.components:
        if comp.component_type == 'raw_material':
            material = comp.material
            if material:
                # Calculate total needed for this production (with waste adjustment)
                needed_quantity = comp.quantity * quantity_produced * material.effective_cost_multiplier

                # Use total stock across all suppliers
                stock = calculate_total_material_stock(material.id)

                # Skip consumption breakdown for unlimited materials
                if material.is_unlimited:
                    # Get supplier price
                    from .utils import get_primary_supplier_discounted_price
                    supplier_price = get_primary_supplier_discounted_price(material)

                    components_data.append({
                        'type': 'Raw Material',
                        'name': material.name,
                        'material_id': material.id,
                        'qty_per_batch': comp.quantity,
                        'unit': material.unit,
                        'current_stock': None,  # null in JSON for unlimited
                        'cost_per_unit': supplier_price,
                        'suppliers': [],
                        'consumption_breakdown': [],
                        'show_multiple_rows': False,
                        'is_unlimited': True,
                        'waste_percentage': material.waste_percentage  # Add waste percentage for tooltips
                    })
                    continue  # Skip to next component

                # Calculate consumption breakdown per supplier
                consumption_breakdown = []
                remaining_to_consume = needed_quantity

                # Get supplier links sorted by primary first
                from ..models import RawMaterialSupplier

                # Eager load supplier with all fields including discount_percentage
                supplier_links = db.session.query(RawMaterialSupplier).filter_by(
                    raw_material_id=material.id
                ).options(joinedload(RawMaterialSupplier.supplier)).all()
                supplier_links = sorted(supplier_links,
                                       key=lambda x: (not x.is_primary, x.supplier.name))

                # First pass: Consume from available stock only
                import math
                for link in supplier_links:
                    supplier_stock = calculate_supplier_stock(material.id, link.supplier_id)

                    if remaining_to_consume > 0 and supplier_stock > 0:
                        # Take what we can from this supplier's available stock
                        # Handle infinity case for unlimited materials
                        if math.isinf(supplier_stock):
                            amount_to_consume = remaining_to_consume
                        else:
                            amount_to_consume = min(supplier_stock, remaining_to_consume)
                        remaining_to_consume -= amount_to_consume

                        # Apply supplier discount to the cost
                        discounted_cost = apply_supplier_discount(link.cost_per_unit, link.supplier)

                        # Convert to cost per 100g for display
                        cost_per_100g = convert_cost_to_display_unit(discounted_cost, material.unit)
                        original_cost_per_100g = convert_cost_to_display_unit(link.cost_per_unit, material.unit)

                        consumption_breakdown.append({
                            'supplier_id': link.supplier_id,
                            'supplier_name': link.supplier.name,
                            'is_primary': link.is_primary,
                            'stock_available': safe_float(supplier_stock),
                            'amount_to_consume': amount_to_consume,
                            'remaining_after': safe_float(supplier_stock - amount_to_consume),
                            'cost_per_unit': cost_per_100g,  # Now this is cost per 100g for display
                            'original_cost': original_cost_per_100g,  # Original price per 100g for comparison
                            'discounted_cost_per_unit': discounted_cost,  # Keep original for total calculation
                            'total_cost': amount_to_consume * discounted_cost,  # Use original discounted_cost for total
                            'is_deficit': False
                        })
                    elif link.is_primary and supplier_stock == 0 and remaining_to_consume == needed_quantity:
                        # Primary has no stock and nothing consumed yet - include for display
                        # Apply supplier discount to the cost
                        discounted_cost = apply_supplier_discount(link.cost_per_unit, link.supplier)

                        # Convert to cost per 100g for display
                        cost_per_100g = convert_cost_to_display_unit(discounted_cost, material.unit)
                        original_cost_per_100g = convert_cost_to_display_unit(link.cost_per_unit, material.unit)

                        consumption_breakdown.append({
                            'supplier_id': link.supplier_id,
                            'supplier_name': link.supplier.name,
                            'is_primary': True,
                            'stock_available': 0,
                            'amount_to_consume': 0,
                            'remaining_after': 0,
                            'cost_per_unit': cost_per_100g,  # Now this is cost per 100g for display
                            'original_cost': original_cost_per_100g,  # Original price per 100g for comparison
                            'discounted_cost_per_unit': discounted_cost,  # Keep original for total calculation
                            'total_cost': 0,
                            'is_deficit': False
                        })

                # Second pass: If still need more, assign deficit to primary
                if remaining_to_consume > 0:
                    # Find primary supplier in breakdown or add it
                    primary_found = False
                    for item in consumption_breakdown:
                        if item['is_primary']:
                            # Adjust primary to take the deficit
                            item['amount_to_consume'] += remaining_to_consume
                            item['remaining_after'] -= remaining_to_consume
                            item['total_cost'] = item['amount_to_consume'] * item['discounted_cost_per_unit']  # Use discounted_cost_per_unit for calculation
                            item['is_deficit'] = item['remaining_after'] < 0
                            primary_found = True
                            break

                    if not primary_found:
                        # Primary wasn't in the list (had 0 stock), add it with deficit
                        primary_link = next((l for l in supplier_links if l.is_primary), None)
                        if primary_link:
                            primary_stock = calculate_supplier_stock(material.id, primary_link.supplier_id)
                            # Apply supplier discount to the cost
                            discounted_cost = apply_supplier_discount(primary_link.cost_per_unit, primary_link.supplier)

                            # Convert to cost per 100g for display
                            cost_per_100g = convert_cost_to_display_unit(discounted_cost, material.unit)
                            original_cost_per_100g = convert_cost_to_display_unit(primary_link.cost_per_unit, material.unit)

                            consumption_breakdown.insert(0, {
                                'supplier_id': primary_link.supplier_id,
                                'supplier_name': primary_link.supplier.name,
                                'is_primary': True,
                                'stock_available': safe_float(primary_stock),
                                'amount_to_consume': remaining_to_consume,
                                'remaining_after': safe_float(primary_stock - remaining_to_consume),
                                'cost_per_unit': cost_per_100g,  # Now this is cost per 100g for display
                                'original_cost': original_cost_per_100g,  # Original price per 100g for comparison
                                'discounted_cost_per_unit': discounted_cost,  # Keep original for total calculation
                                'total_cost': remaining_to_consume * discounted_cost,  # Use original discounted_cost for total
                                'is_deficit': True
                            })

                # Determine if we should show multiple rows
                show_multiple_rows = len([c for c in consumption_breakdown if c['amount_to_consume'] > 0]) > 1

                # Add old supplier info for backward compatibility
                supplier_info = []
                for link in material.supplier_links:
                    supplier_stock = calculate_supplier_stock(material.id, link.supplier_id)
                    # Show all suppliers regardless of stock level
                    supplier_info.append({
                        'name': link.supplier.name,
                        'stock': safe_float(supplier_stock),
                        'is_primary': link.is_primary
                    })

                # Get primary supplier's discounted price for backward compatibility
                from .utils import get_primary_supplier_discounted_price
                primary_discounted_price = get_primary_supplier_discounted_price(material)

                # Convert to cost per 100g for display
                primary_cost_per_100g = convert_cost_to_display_unit(primary_discounted_price, material.unit)

                components_data.append({
                    'type': 'Raw Material',
                    'name': material.name,
                    'material_id': material.id,
                    'qty_per_batch': comp.quantity,
                    'unit': material.unit,
                    'current_stock': safe_float(stock),
                    'cost_per_unit': primary_cost_per_100g,  # Now this is cost per 100g
                    'suppliers': supplier_info,  # Keep for backward compatibility
                    'consumption_breakdown': consumption_breakdown,
                    'show_multiple_rows': show_multiple_rows,
                    'is_unlimited': False,  # Normal material with suppliers
                    'waste_percentage': material.waste_percentage  # Add waste percentage for tooltips
                })

        elif comp.component_type == 'premake':
            # Check if this is actually a preproduct stored with wrong component_type
            maybe_preproduct = Product.query.filter_by(id=comp.component_id, is_preproduct=True).first()
            if maybe_preproduct:
                # Handle as preproduct
                stock = calculate_premake_current_stock(maybe_preproduct.id)
                needed_quantity = comp.quantity * quantity_produced

                # Calculate prime cost for preproduct
                from .utils import calculate_prime_cost
                cost_per_unit = calculate_prime_cost(maybe_preproduct)

                # Calculate cost per 100g for weight-based preproducts
                cost_per_100g = None
                if maybe_preproduct.unit in ['kg', 'g']:
                    if maybe_preproduct.unit == 'kg':
                        cost_per_100g = cost_per_unit * 0.1  # cost per kg * 0.1 = cost per 100g
                    else:  # unit is 'g'
                        cost_per_100g = cost_per_unit * 100  # cost per g * 100 = cost per 100g

                components_data.append({
                    'type': 'Preproduct',
                    'name': maybe_preproduct.name,
                    'qty_per_batch': comp.quantity,
                    'unit': maybe_preproduct.unit or 'piece',
                    'current_stock': safe_float(stock),
                    'cost_per_unit': cost_per_unit,
                    'cost_per_100g': cost_per_100g,  # New field for frontend display
                    'is_deficit': needed_quantity > stock
                })
            else:
                # Handle as regular premake
                premake = comp.premake
                if premake:
                    stock = calculate_premake_current_stock(premake.id)
                    needed_quantity = comp.quantity * quantity_produced

                    # Calculate cost per unit for premake with discounts
                    from .utils import calculate_premake_cost_per_unit
                    cost_per_unit = calculate_premake_cost_per_unit(premake)

                    components_data.append({
                        'type': 'Premake',
                        'name': premake.name,
                        'qty_per_batch': comp.quantity,
                        'unit': premake.unit,
                        'current_stock': safe_float(stock),
                        'cost_per_unit': cost_per_unit,
                        'is_deficit': needed_quantity > stock
                    })

        elif comp.component_type == 'product':
            # Handle preproducts (products used as components)
            # Note: preproduct relationship might not exist, query directly
            preproduct = Product.query.filter_by(id=comp.component_id, is_preproduct=True).first()
            if preproduct:
                # Calculate stock using production logs
                stock = calculate_premake_current_stock(preproduct.id)
                needed_quantity = comp.quantity * quantity_produced

                # Calculate prime cost for preproduct
                from .utils import calculate_prime_cost
                cost_per_unit = calculate_prime_cost(preproduct)

                # Calculate cost per 100g for weight-based preproducts
                cost_per_100g = None
                if preproduct.unit in ['kg', 'g']:
                    if preproduct.unit == 'kg':
                        cost_per_100g = cost_per_unit * 0.1  # cost per kg * 0.1 = cost per 100g
                    else:  # unit is 'g'
                        cost_per_100g = cost_per_unit * 100  # cost per g * 100 = cost per 100g

                components_data.append({
                    'type': 'Preproduct',
                    'name': preproduct.name,
                    'qty_per_batch': comp.quantity,
                    'unit': preproduct.unit or 'piece',
                    'current_stock': safe_float(stock),
                    'cost_per_unit': cost_per_unit,
                    'cost_per_100g': cost_per_100g,  # New field for frontend display
                    'is_deficit': needed_quantity > stock
                })

        elif comp.component_type == 'packaging':
            # Handle packaging components
            packaging = comp.packaging
            if packaging:
                from .utils import calculate_packaging_stock
                stock = calculate_packaging_stock(packaging.id)
                needed_quantity = comp.quantity * quantity_produced

                components_data.append({
                    'type': 'Packaging',
                    'name': packaging.name,
                    'qty_per_batch': comp.quantity,
                    'unit': 'units',
                    'current_stock': safe_float(stock),
                    'cost_per_unit': packaging.price_per_unit,
                    'is_deficit': needed_quantity > stock
                })

    return jsonify({
        'product_name': product.name,
        'products_per_recipe': product.products_per_recipe,
        'components': components_data
    })


@main_blueprint.route('/api/premake_recipe/<int:premake_id>')
def get_premake_recipe(premake_id):
    """API endpoint to get premake recipe with consumption breakdown"""
    premake = Product.query.filter_by(id=premake_id, is_premake=True).first_or_404()

    # Get quantity being produced (if provided)
    quantity_produced = request.args.get('quantity', 1, type=float)

    components_data = []
    for comp in premake.components:
        if comp.component_type == 'raw_material':
            material = comp.material
            if material:
                # Calculate total needed for this production (with waste adjustment)
                needed_quantity = comp.quantity * quantity_produced * material.effective_cost_multiplier

                # Use total stock across all suppliers
                stock = calculate_total_material_stock(material.id)

                # Skip consumption breakdown for unlimited materials
                if material.is_unlimited:
                    # Get supplier price
                    from .utils import get_primary_supplier_discounted_price
                    supplier_price = get_primary_supplier_discounted_price(material)

                    # Convert to cost per 100g for display
                    cost_per_100g = convert_cost_to_display_unit(supplier_price, material.unit)

                    components_data.append({
                        'type': 'Raw Material',
                        'name': material.name,
                        'material_id': material.id,
                        'qty_per_batch': comp.quantity,
                        'unit': material.unit,
                        'current_stock': None,
                        'cost_per_unit': cost_per_100g,  # Now this is cost per 100g
                        'suppliers': [],
                        'consumption_breakdown': [],
                        'show_multiple_rows': False,
                        'is_unlimited': True
                    })
                    continue

                # Calculate consumption breakdown per supplier
                consumption_breakdown = []
                remaining_to_consume = needed_quantity

                # Get supplier links sorted by primary first
                from ..models import RawMaterialSupplier

                # Eager load supplier with all fields including discount_percentage
                supplier_links = db.session.query(RawMaterialSupplier).filter_by(
                    raw_material_id=material.id
                ).options(joinedload(RawMaterialSupplier.supplier)).all()
                supplier_links = sorted(supplier_links,
                                       key=lambda x: (not x.is_primary, x.supplier.name))

                # First pass: Consume from available stock only
                import math
                for link in supplier_links:
                    supplier_stock = calculate_supplier_stock(material.id, link.supplier_id)

                    if remaining_to_consume > 0 and supplier_stock > 0:
                        if math.isinf(supplier_stock):
                            amount_to_consume = remaining_to_consume
                        else:
                            amount_to_consume = min(supplier_stock, remaining_to_consume)
                        remaining_to_consume -= amount_to_consume

                        # Apply supplier discount to the cost
                        discounted_cost = apply_supplier_discount(link.cost_per_unit, link.supplier)

                        # Convert to cost per 100g for display
                        cost_per_100g = convert_cost_to_display_unit(discounted_cost, material.unit)
                        original_cost_per_100g = convert_cost_to_display_unit(link.cost_per_unit, material.unit)

                        consumption_breakdown.append({
                            'supplier_id': link.supplier_id,
                            'supplier_name': link.supplier.name,
                            'is_primary': link.is_primary,
                            'stock_available': safe_float(supplier_stock),
                            'amount_to_consume': amount_to_consume,
                            'remaining_after': safe_float(supplier_stock - amount_to_consume),
                            'cost_per_unit': cost_per_100g,  # Now this is cost per 100g for display
                            'original_cost': original_cost_per_100g,  # Original price per 100g for comparison
                            'discounted_cost_per_unit': discounted_cost,  # Keep original for total calculation
                            'total_cost': amount_to_consume * discounted_cost,  # Use original discounted_cost for total
                            'is_deficit': False
                        })
                    elif link.is_primary and supplier_stock == 0 and remaining_to_consume == needed_quantity:
                        # Apply supplier discount to the cost
                        discounted_cost = apply_supplier_discount(link.cost_per_unit, link.supplier)

                        # Convert to cost per 100g for display
                        cost_per_100g = convert_cost_to_display_unit(discounted_cost, material.unit)
                        original_cost_per_100g = convert_cost_to_display_unit(link.cost_per_unit, material.unit)

                        consumption_breakdown.append({
                            'supplier_id': link.supplier_id,
                            'supplier_name': link.supplier.name,
                            'is_primary': True,
                            'stock_available': 0,
                            'amount_to_consume': 0,
                            'remaining_after': 0,
                            'cost_per_unit': cost_per_100g,  # Now this is cost per 100g for display
                            'original_cost': original_cost_per_100g,  # Original price per 100g for comparison
                            'discounted_cost_per_unit': discounted_cost,  # Keep original for total calculation
                            'total_cost': 0,
                            'is_deficit': False
                        })

                # Second pass: If still need more, assign deficit to primary
                if remaining_to_consume > 0:
                    primary_found = False
                    for item in consumption_breakdown:
                        if item['is_primary']:
                            item['amount_to_consume'] += remaining_to_consume
                            item['remaining_after'] -= remaining_to_consume
                            item['total_cost'] = item['amount_to_consume'] * item['discounted_cost_per_unit']  # Use discounted_cost_per_unit for calculation
                            item['is_deficit'] = item['remaining_after'] < 0
                            primary_found = True
                            break

                    if not primary_found:
                        primary_link = next((l for l in supplier_links if l.is_primary), None)
                        if primary_link:
                            primary_stock = calculate_supplier_stock(material.id, primary_link.supplier_id)
                            # Apply supplier discount to the cost
                            discounted_cost = apply_supplier_discount(primary_link.cost_per_unit, primary_link.supplier)

                            # Convert to cost per 100g for display
                            cost_per_100g = convert_cost_to_display_unit(discounted_cost, material.unit)
                            original_cost_per_100g = convert_cost_to_display_unit(primary_link.cost_per_unit, material.unit)

                            consumption_breakdown.insert(0, {
                                'supplier_id': primary_link.supplier_id,
                                'supplier_name': primary_link.supplier.name,
                                'is_primary': True,
                                'stock_available': safe_float(primary_stock),
                                'amount_to_consume': remaining_to_consume,
                                'remaining_after': safe_float(primary_stock - remaining_to_consume),
                                'cost_per_unit': cost_per_100g,  # Now this is cost per 100g for display
                                'original_cost': original_cost_per_100g,  # Original price per 100g for comparison
                                'discounted_cost_per_unit': discounted_cost,  # Keep original for total calculation
                                'total_cost': remaining_to_consume * discounted_cost,  # Use original discounted_cost for total
                                'is_deficit': True
                            })

                # Determine if we should show multiple rows
                show_multiple_rows = len([c for c in consumption_breakdown if c['amount_to_consume'] > 0]) > 1

                # Get primary supplier's discounted price for backward compatibility
                from .utils import get_primary_supplier_discounted_price
                primary_discounted_price = get_primary_supplier_discounted_price(material)

                # Convert to cost per 100g for display
                primary_cost_per_100g = convert_cost_to_display_unit(primary_discounted_price, material.unit)

                components_data.append({
                    'type': 'Raw Material',
                    'name': material.name,
                    'material_id': material.id,
                    'qty_per_batch': comp.quantity,
                    'unit': material.unit,
                    'current_stock': safe_float(stock),
                    'cost_per_unit': primary_cost_per_100g,  # Now this is cost per 100g
                    'consumption_breakdown': consumption_breakdown,
                    'show_multiple_rows': show_multiple_rows,
                    'is_unlimited': False
                })

        elif comp.component_type == 'premake':
            # Handle nested premakes
            nested_premake = comp.premake
            if nested_premake:
                stock = calculate_premake_current_stock(nested_premake.id)
                needed_quantity = comp.quantity * quantity_produced

                # Calculate cost per unit using the utility function
                cost_per_unit = calculate_premake_cost_per_unit(nested_premake)

                components_data.append({
                    'type': 'Premake',
                    'name': nested_premake.name,
                    'qty_per_batch': comp.quantity,
                    'unit': nested_premake.unit,
                    'current_stock': safe_float(stock),
                    'cost_per_unit': cost_per_unit,
                    'is_deficit': needed_quantity > stock
                })

        elif comp.component_type == 'packaging':
            # Handle packaging components
            packaging = comp.packaging
            if packaging:
                from .utils import calculate_packaging_stock
                stock = calculate_packaging_stock(packaging.id)
                needed_quantity = comp.quantity * quantity_produced

                components_data.append({
                    'type': 'Packaging',
                    'name': packaging.name,
                    'qty_per_batch': comp.quantity,
                    'unit': 'units',
                    'current_stock': safe_float(stock),
                    'cost_per_unit': packaging.price_per_unit,
                    'is_deficit': needed_quantity > stock
                })

    return jsonify({
        'premake_name': premake.name,
        'batch_size': premake.batch_size,
        'unit': premake.unit,
        'components': components_data
    })


