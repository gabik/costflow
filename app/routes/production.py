from datetime import datetime
import json
from flask import Blueprint, render_template, request, redirect, url_for
from flask_babel import gettext as _
from ..models import db, Product, ProductionLog, StockLog, InsufficientStockError, RawMaterial
from .utils import log_audit, deduct_material_stock, calculate_premake_cost_per_unit

production_blueprint = Blueprint('production', __name__)

# ----------------------------
# Production Management
# ----------------------------
@production_blueprint.route('/production', methods=['GET', 'POST'])
def production():
    if request.method == 'POST':
        product_id = request.form['product_id']
        production_mode = request.form.get('production_mode', 'batches')
        quantity_input = float(request.form['quantity_produced'])

        # Parse user-provided timestamp
        timestamp_str = request.form.get('timestamp')
        timestamp = datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M:%S') if timestamp_str else datetime.utcnow()

        product = Product.query.get_or_404(product_id)

        # Convert quantity to batches based on production mode
        if production_mode == 'units':
            # User entered units, convert to batches
            quantity_produced = quantity_input / product.products_per_recipe if product.products_per_recipe > 0 else quantity_input
        else:
            # User entered batches directly
            quantity_produced = quantity_input

        # Track total production cost
        total_production_cost = 0
        cost_details = {'materials': [], 'premakes': [], 'packaging': []}

        # Deduct materials for production and calculate costs
        try:
            for component in product.components:
                # Debug logging
                print(f"DEBUG: Processing component type: {component.component_type}, ID: {component.component_id}")

                if component.component_type == 'raw_material':
                    required_qty = component.quantity * quantity_produced
                    # Get deduction details with costs
                    deductions = deduct_material_stock(component.component_id, required_qty)

                    # Calculate cost for this material
                    material_cost = sum(d[3] for d in deductions)  # d[3] is total_cost
                    total_production_cost += material_cost

                    # Store details for tracking
                    material = RawMaterial.query.get(component.component_id)
                    cost_details['materials'].append({
                        'name': material.name,
                        'suppliers_used': [
                            {'supplier_id': d[0], 'qty': d[1], 'cost': d[3]}
                            for d in deductions
                        ],
                        'total_cost': material_cost
                    })

                elif component.component_type == 'premake':
                    # Calculate premake cost and validate stock
                    premake = Product.query.filter_by(id=component.component_id, is_premake=True).first()
                    if premake:
                        required_qty = component.quantity * quantity_produced

                        # Check if enough premake stock is available
                        from .utils import calculate_premake_current_stock
                        available_stock = calculate_premake_current_stock(premake.id)
                        if available_stock < required_qty:
                            raise InsufficientStockError(
                                f"אין מספיק מלאי עבור {premake.name}. "
                                f"נדרש: {required_qty:.2f} {premake.unit}, זמין: {available_stock:.2f} {premake.unit}"
                            )

                        # Always use recipe-based costs for consistency (not production history)
                        premake_cost_per_unit = calculate_premake_cost_per_unit(premake, use_actual_costs=False)
                        material_cost = premake_cost_per_unit * required_qty
                        total_production_cost += material_cost

                        cost_details['premakes'].append({
                            'name': premake.name,
                            'qty': required_qty,
                            'cost': material_cost
                        })

                    # NOTE: Premake consumption is tracked via ProductionLog only.
                    # Do NOT create StockLog here as it causes double-counting!
                    # The calculate_premake_current_stock() function already
                    # subtracts consumption from ProductionLogs.

                elif component.component_type == 'product':
                    # Handle preproduct components
                    preproduct = Product.query.filter_by(id=component.component_id, is_preproduct=True).first()
                    if preproduct:
                        required_qty = component.quantity * quantity_produced

                        # Debug logging
                        print(f"DEBUG: Preproduct {preproduct.name}")
                        print(f"  - Unit: {preproduct.unit}")
                        print(f"  - Component quantity: {component.quantity}")
                        print(f"  - Quantity produced: {quantity_produced}")
                        print(f"  - Required qty: {required_qty}")

                        # Check stock availability
                        from .utils import calculate_premake_current_stock
                        available_stock = calculate_premake_current_stock(preproduct.id)
                        print(f"  - Available stock: {available_stock}")

                        if available_stock < required_qty:
                            raise InsufficientStockError(
                                f"אין מספיק מלאי עבור {preproduct.name}. "
                                f"נדרש: {required_qty:.2f} {preproduct.unit}, זמין: {available_stock:.2f} {preproduct.unit}"
                            )

                        # Calculate cost
                        from .utils import calculate_prime_cost
                        preproduct_cost_per_unit = calculate_prime_cost(preproduct)
                        material_cost = preproduct_cost_per_unit * required_qty
                        total_production_cost += material_cost

                        print(f"  - Cost per unit: {preproduct_cost_per_unit}")
                        print(f"  - Total cost: {material_cost}")

                        # Initialize preproducts list if not exists
                        if 'preproducts' not in cost_details:
                            cost_details['preproducts'] = []

                        cost_details['preproducts'].append({
                            'name': preproduct.name,
                            'qty': required_qty,
                            'unit': preproduct.unit,
                            'cost': material_cost
                        })

                        # Note: Like premakes, preproduct consumption is tracked via ProductionLog
                        # No StockLog entry needed here to avoid double-counting

                elif component.component_type == 'packaging' and component.packaging:
                    # Calculate packaging cost and deduct stock
                    required_qty = component.quantity * quantity_produced

                    # REMOVED: Packaging stock is now deducted during sales, not production
                    # Previously checked and deducted packaging stock here
                    # from .utils import calculate_packaging_stock, deduct_packaging_stock
                    # available_stock = calculate_packaging_stock(component.component_id)
                    # if available_stock < required_qty:
                    #     raise InsufficientStockError(
                    #         f"אין מספיק מלאי עבור אריזה {component.packaging.name}. "
                    #         f"נדרש: {required_qty:.2f}, זמין: {available_stock:.2f}"
                    #     )
                    # deduct_packaging_stock(component.component_id, required_qty)

                    # Track packaging for informational purposes but DON'T add to production cost
                    # Packaging cost is only applied when products are sold
                    packaging_cost = component.packaging.price_per_unit * required_qty
                    # total_production_cost += packaging_cost  # REMOVED: Packaging not included in production

                    cost_details['packaging'].append({
                        'name': component.packaging.name,
                        'qty': required_qty,
                        'cost': packaging_cost  # For reference only, not in total
                    })

            # Calculate cost per unit
            units_produced = quantity_produced * product.products_per_recipe
            cost_per_unit = total_production_cost / units_produced if units_produced > 0 else 0

            # Debug logging for cost breakdown
            print(f"\nDEBUG: Production Cost Summary for {product.name}")
            print(f"  - Total production cost: {total_production_cost:.2f}")
            print(f"  - Units produced: {units_produced}")
            print(f"  - Cost per unit: {cost_per_unit:.2f}")
            print(f"  - Cost details: {json.dumps(cost_details, indent=2)}")

            # Create production log WITH COST INFORMATION
            production_log = ProductionLog(
                product_id=product_id,
                quantity_produced=quantity_produced,
                total_cost=total_production_cost,
                cost_per_unit=cost_per_unit,
                cost_details=json.dumps(cost_details),
                timestamp=timestamp
            )
            db.session.add(production_log)

            # Create StockLog for preproducts (they need stock tracking like premakes)
            if product.is_preproduct:
                stock_log = StockLog(
                    product_id=product_id,
                    action_type='add',
                    quantity=units_produced  # Total units produced
                )
                db.session.add(stock_log)

            db.session.commit()

            return redirect(url_for('production.production'))

        except InsufficientStockError as e:
            # Rollback transaction on error
            db.session.rollback()

            # Get data for template
            products = Product.query.filter_by(is_product=True).all()
            production_logs = ProductionLog.query.filter(
                ProductionLog.product_id.in_(
                    db.session.query(Product.id).filter_by(is_product=True)
                )
            ).order_by(ProductionLog.timestamp.desc()).all()
            current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

            # Return with error message
            return render_template('production.html',
                                 error=str(e),
                                 products=products,
                                 production_logs=production_logs,
                                 current_time=current_time)

    # Filter to only show actual products (not premakes or preproducts)
    products = Product.query.filter_by(is_product=True).all()
    production_logs = ProductionLog.query.filter(
        ProductionLog.product_id.in_(
            db.session.query(Product.id).filter_by(is_product=True)
        )
    ).order_by(ProductionLog.timestamp.desc()).all()
    current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    return render_template('production.html', products=products, production_logs=production_logs, current_time=current_time)

@production_blueprint.route('/production/edit/<int:log_id>', methods=['POST'])
def edit_production_log(log_id):
    log = ProductionLog.query.get_or_404(log_id)
    new_quantity = float(request.form['quantity_produced'])
    
    old_qty = log.quantity_produced
    log.quantity_produced = new_quantity
    
    db.session.commit()
    log_audit("UPDATE", "ProductionLog", log.id, f"Updated product production from {old_qty} to {new_quantity}")
    
    return redirect(url_for('production.production'))

@production_blueprint.route('/production/delete/<int:log_id>', methods=['POST'])
def delete_production_log(log_id):
    log = ProductionLog.query.get_or_404(log_id)
    db.session.delete(log)
    db.session.commit()
    log_audit("DELETE", "ProductionLog", log_id, "Deleted product production log")
    return redirect(url_for('production.production'))

@production_blueprint.route('/production/premakes', methods=['GET', 'POST'])
def premake_production():
    if request.method == 'POST':
        premake_id = request.form['premake_id']
        # quantity_produced from form is now in BATCHES
        quantity_batches = float(request.form['quantity_produced'])

        # Parse user-provided timestamp
        timestamp_str = request.form.get('timestamp')
        timestamp = datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M:%S') if timestamp_str else datetime.utcnow()

        # Get premake from unified Product model
        premake = Product.query.filter_by(id=premake_id, is_premake=True).first()

        if not premake:
            return "Premake not found", 404

        batch_size = premake.batch_size or 1

        # Calculate total units for stock log
        quantity_units = quantity_batches * batch_size

        # Track total production cost
        total_production_cost = 0
        cost_details = {'materials': [], 'premakes': [], 'packaging': []}

        # Deduct materials for production and calculate costs (similar to product production)
        try:
            for component in premake.components:
                if component.component_type == 'raw_material':
                    required_qty = component.quantity * quantity_batches
                    # Get deduction details with costs
                    deductions = deduct_material_stock(component.component_id, required_qty)

                    # Calculate cost for this material
                    material_cost = sum(d[3] for d in deductions)  # d[3] is total_cost
                    total_production_cost += material_cost

                    # Store details for tracking
                    material = RawMaterial.query.get(component.component_id)
                    cost_details['materials'].append({
                        'name': material.name,
                        'suppliers_used': [
                            {'supplier_id': d[0], 'qty': d[1], 'cost': d[3]}
                            for d in deductions
                        ],
                        'total_cost': material_cost
                    })

                elif component.component_type == 'premake':
                    # Calculate nested premake cost
                    nested_premake = Product.query.filter_by(id=component.component_id, is_premake=True).first()
                    if nested_premake:
                        # Always use recipe-based costs for consistency (not production history)
                        nested_premake_cost_per_unit = calculate_premake_cost_per_unit(nested_premake, use_actual_costs=False)
                        required_qty = component.quantity * quantity_batches
                        material_cost = nested_premake_cost_per_unit * required_qty
                        total_production_cost += material_cost

                        cost_details['premakes'].append({
                            'name': nested_premake.name,
                            'qty': required_qty,
                            'cost': material_cost
                        })

                elif component.component_type == 'packaging' and component.packaging:
                    # Calculate packaging cost and deduct stock
                    required_qty = component.quantity * quantity_batches

                    # REMOVED: Packaging stock is now deducted during sales, not production
                    # Previously checked and deducted packaging stock here
                    # from .utils import calculate_packaging_stock, deduct_packaging_stock
                    # available_stock = calculate_packaging_stock(component.component_id)
                    # if available_stock < required_qty:
                    #     raise InsufficientStockError(
                    #         f"אין מספיק מלאי עבור אריזה {component.packaging.name}. "
                    #         f"נדרש: {required_qty:.2f}, זמין: {available_stock:.2f}"
                    #     )
                    # deduct_packaging_stock(component.component_id, required_qty)

                    # Track packaging for informational purposes but DON'T add to production cost
                    # Packaging cost is only applied when products are sold
                    packaging_cost = component.packaging.price_per_unit * required_qty
                    # total_production_cost += packaging_cost  # REMOVED: Packaging not included in production

                    cost_details['packaging'].append({
                        'name': component.packaging.name,
                        'qty': required_qty,
                        'cost': packaging_cost  # For reference only, not in total
                    })

            # Calculate cost per unit (per kg/unit, not per batch)
            cost_per_unit = total_production_cost / quantity_units if quantity_units > 0 else 0

            # Create production log WITH COST INFORMATION
            production_log = ProductionLog(
                product_id=premake_id,
                quantity_produced=quantity_batches,
                total_cost=total_production_cost,
                cost_per_unit=cost_per_unit,
                cost_details=json.dumps(cost_details),
                timestamp=timestamp
            )
            db.session.add(production_log)
            db.session.flush()

            # Update Stock - use product_id for unified model
            stock_log = StockLog(
                product_id=premake_id,
                action_type='add',
                quantity=quantity_units
            )
            db.session.add(stock_log)

            db.session.commit()
            return redirect(url_for('production.premake_production'))

        except InsufficientStockError as e:
            # Rollback transaction on error
            db.session.rollback()

            # Get data for template
            premakes = Product.query.filter_by(is_premake=True).all()
            production_logs = ProductionLog.query.filter(
                ProductionLog.product_id.in_(
                    db.session.query(Product.id).filter_by(is_premake=True)
                )
            ).order_by(ProductionLog.timestamp.desc()).all()
            current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

            # Return with error message
            return render_template('premake_production.html',
                                 error=str(e),
                                 premakes=premakes,
                                 production_logs=production_logs,
                                 current_time=current_time)

    # Get premakes - try unified model first
    # Get products that are premakes
    premakes = Product.query.filter_by(is_premake=True).all()

    # Get production logs for premakes
    production_logs = ProductionLog.query.filter(
        ProductionLog.product_id.in_(
            db.session.query(Product.id).filter_by(is_premake=True)
        )
    ).order_by(ProductionLog.timestamp.desc()).all()

    current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    return render_template('premake_production.html', premakes=premakes, production_logs=production_logs, current_time=current_time)

@production_blueprint.route('/production/premakes/edit/<int:log_id>', methods=['POST'])
def edit_premake_production_log(log_id):
    log = ProductionLog.query.get_or_404(log_id)
    new_quantity_batches = float(request.form['quantity_produced'])

    # Get premake from product relationship (unified model)
    premake = log.product if log.product and log.product.is_premake else None
    if not premake:
        # Try using premake_id for backward compatibility
        if hasattr(log, 'premake_id') and log.premake_id:
            premake = Product.query.filter_by(id=log.premake_id, is_premake=True).first()

    if not premake:
        return "Premake not found", 404

    old_qty_batches = log.quantity_produced
    old_qty_units = old_qty_batches * premake.batch_size
    new_qty_units = new_quantity_batches * premake.batch_size

    # Update Production Log
    log.quantity_produced = new_quantity_batches

    # Update Stock Log
    # Better approach for correction: Add a correction StockLog (difference).
    # Difference = New - Old
    diff_units = new_qty_units - old_qty_units

    if diff_units != 0:
        correction_log = StockLog(
            product_id=premake.id,  # Using product_id for unified model
            action_type='add',  # Negative add reduces stock
            quantity=diff_units
        )
        db.session.add(correction_log)

    db.session.commit()
    log_audit("UPDATE", "ProductionLog", log.id, f"Updated premake production from {old_qty_batches} to {new_quantity_batches} batches")

    return redirect(url_for('production.premake_production'))

@production_blueprint.route('/production/premakes/delete/<int:log_id>', methods=['POST'])
def delete_premake_production_log(log_id):
    log = ProductionLog.query.get_or_404(log_id)
    
    # Revert stock
    # Get premake from product relationship (unified model)
    premake = log.product if log.product and log.product.is_premake else None
    if not premake and hasattr(log, 'premake_id') and log.premake_id:
        # Try using premake_id for backward compatibility
        premake = Product.query.filter_by(id=log.premake_id, is_premake=True).first()

    if premake:
        qty_units = log.quantity_produced * (premake.batch_size or 1)
        revert_log = StockLog(
            product_id=premake.id,  # Using product_id for unified model
            action_type='add',
            quantity=-qty_units
        )
        db.session.add(revert_log)

    db.session.delete(log)
    db.session.commit()
    log_audit("DELETE", "ProductionLog", log_id, "Deleted premake production log")
    return redirect(url_for('production.premake_production'))


# ----------------------------
# Daily Batch Production
# ----------------------------

@production_blueprint.route('/production/daily', methods=['GET', 'POST'])
def daily_product_production():
    """Batch production interface for multiple products"""
    from flask import flash, jsonify
    from flask_babel import gettext as _
    from .utils import group_items_by_category, check_item_stock_availability

    if request.method == 'POST':
        return _process_daily_production(is_premake=False)

    # GET: Prepare data for template
    products = Product.query.filter_by(is_product=True, is_archived=False).all()
    categories_data = group_items_by_category(products, item_type='product')
    current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

    return render_template(
        'daily_production.html',
        item_type='product',
        categories=categories_data,
        current_time=current_time,
        api_endpoint='product_recipe'
    )


@production_blueprint.route('/production/premakes/daily', methods=['GET', 'POST'])
def daily_premake_production():
    """Batch production interface for multiple premakes"""
    from flask import flash, jsonify
    from flask_babel import gettext as _
    from .utils import group_items_by_category, check_item_stock_availability

    if request.method == 'POST':
        return _process_daily_production(is_premake=True)

    # GET: Prepare data for template
    premakes = Product.query.filter_by(is_premake=True, is_archived=False).all()
    categories_data = group_items_by_category(premakes, item_type='premake')
    current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

    return render_template(
        'daily_production.html',
        item_type='premake',
        categories=categories_data,
        current_time=current_time,
        api_endpoint='premake_recipe'
    )


def _process_daily_production(is_premake):
    """Process batch production submission for products or premakes"""
    from flask import jsonify
    from flask_babel import gettext as _
    from .utils import check_item_stock_availability

    # Parse request data
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': _('Invalid request data')}), 400

    timestamp_str = data.get('timestamp')
    items = data.get('items', [])

    # Parse timestamp
    try:
        timestamp = datetime.strptime(timestamp_str, '%Y-%m-%dT%H:%M:%S') if timestamp_str else datetime.utcnow()
    except ValueError:
        timestamp = datetime.utcnow()

    if not items:
        return jsonify({'success': False, 'error': _('No items to produce')}), 400

    # Filter out zero quantities
    items = [item for item in items if item.get('quantity', 0) > 0]
    if not items:
        return jsonify({'success': False, 'error': _('No items with quantity > 0')}), 400

    # Phase 1: Validate ALL items have sufficient stock
    validation_errors = []
    for item_data in items:
        item_id = item_data.get('id')
        quantity = item_data.get('quantity', 0)

        if is_premake:
            product = Product.query.filter_by(id=item_id, is_premake=True).first()
        else:
            product = Product.query.filter_by(id=item_id, is_product=True).first()

        if not product:
            validation_errors.append({'id': item_id, 'name': f'ID {item_id}', 'error': _('Item not found')})
            continue

        # Check stock availability
        stock_check = check_item_stock_availability(product, quantity)
        if not stock_check['has_stock']:
            missing_names = ', '.join([m['name'] for m in stock_check['missing_components'][:3]])
            validation_errors.append({
                'id': item_id,
                'name': product.name,
                'error': _('Insufficient stock'),
                'missing': missing_names
            })

    if validation_errors:
        return jsonify({
            'success': False,
            'error': _('Stock validation failed'),
            'validation_errors': validation_errors
        }), 400

    # Phase 2: Execute production for ALL items in a single transaction
    try:
        production_results = []

        for item_data in items:
            item_id = item_data.get('id')
            quantity = item_data.get('quantity', 0)

            if is_premake:
                result = _execute_single_premake_production(item_id, quantity, timestamp)
            else:
                result = _execute_single_product_production(item_id, quantity, timestamp)

            production_results.append(result)

        db.session.commit()

        return jsonify({
            'success': True,
            'message': _('Production logged successfully'),
            'count': len(production_results),
            'results': production_results
        })

    except InsufficientStockError as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400

    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': _('Production failed: %(error)s', error=str(e))
        }), 500


def _execute_single_product_production(product_id, quantity_produced, timestamp):
    """Execute production for a single product (reuses existing logic)"""
    from .utils import calculate_premake_cost_per_unit, calculate_prime_cost, calculate_premake_current_stock

    product = Product.query.get_or_404(product_id)

    total_production_cost = 0
    cost_details = {'materials': [], 'premakes': [], 'packaging': [], 'preproducts': []}

    # Process each component (same logic as main production route)
    for component in product.components:
        if component.component_type == 'raw_material':
            required_qty = component.quantity * quantity_produced
            deductions = deduct_material_stock(component.component_id, required_qty)

            material_cost = sum(d[3] for d in deductions)
            total_production_cost += material_cost

            material = RawMaterial.query.get(component.component_id)
            cost_details['materials'].append({
                'name': material.name,
                'suppliers_used': [{'supplier_id': d[0], 'qty': d[1], 'cost': d[3]} for d in deductions],
                'total_cost': material_cost
            })

        elif component.component_type == 'premake':
            premake = Product.query.filter_by(id=component.component_id, is_premake=True).first()
            if premake:
                required_qty = component.quantity * quantity_produced

                # Check stock
                available_stock = calculate_premake_current_stock(premake.id)
                if available_stock < required_qty:
                    raise InsufficientStockError(
                        _('Insufficient stock for %(name)s. Required: %(required).2f %(unit)s, Available: %(available).2f %(unit)s',
                          name=premake.name, required=required_qty,
                          unit=premake.unit or 'kg', available=available_stock)
                    )

                # Calculate cost from recipe
                premake_cost_per_unit = calculate_premake_cost_per_unit(premake, use_actual_costs=False)
                material_cost = premake_cost_per_unit * required_qty
                total_production_cost += material_cost

                cost_details['premakes'].append({
                    'name': premake.name,
                    'qty': required_qty,
                    'cost': material_cost
                })

        elif component.component_type == 'product':
            preproduct = Product.query.filter_by(id=component.component_id, is_preproduct=True).first()
            if preproduct:
                required_qty = component.quantity * quantity_produced

                available_stock = calculate_premake_current_stock(preproduct.id)
                if available_stock < required_qty:
                    raise InsufficientStockError(
                        _('Insufficient stock for %(name)s. Required: %(required).2f %(unit)s, Available: %(available).2f %(unit)s',
                          name=preproduct.name, required=required_qty,
                          unit=preproduct.unit or 'units', available=available_stock)
                    )

                preproduct_cost_per_unit = calculate_prime_cost(preproduct)
                material_cost = preproduct_cost_per_unit * required_qty
                total_production_cost += material_cost

                cost_details['preproducts'].append({
                    'name': preproduct.name,
                    'qty': required_qty,
                    'cost': material_cost
                })

        elif component.component_type == 'packaging' and component.packaging:
            required_qty = component.quantity * quantity_produced
            packaging_cost = component.packaging.price_per_unit * required_qty
            # Note: Packaging NOT added to production cost (only when sold)
            cost_details['packaging'].append({
                'name': component.packaging.name,
                'qty': required_qty,
                'cost': packaging_cost
            })

    # Calculate cost per unit
    units_produced = quantity_produced * product.products_per_recipe
    cost_per_unit = total_production_cost / units_produced if units_produced > 0 else 0

    # Create production log
    production_log = ProductionLog(
        product_id=product_id,
        quantity_produced=quantity_produced,
        total_cost=total_production_cost,
        cost_per_unit=cost_per_unit,
        cost_details=json.dumps(cost_details),
        timestamp=timestamp
    )
    db.session.add(production_log)

    # For preproducts: Add to stock
    if product.is_preproduct:
        stock_log = StockLog(
            product_id=product_id,
            action_type='add',
            quantity=units_produced
        )
        db.session.add(stock_log)

    return {
        'id': product_id,
        'name': product.name,
        'quantity': quantity_produced,
        'units': units_produced,
        'cost': total_production_cost
    }


def _execute_single_premake_production(premake_id, quantity_batches, timestamp):
    """Execute production for a single premake (reuses existing logic)"""
    from .utils import calculate_premake_cost_per_unit

    premake = Product.query.filter_by(id=premake_id, is_premake=True).first()
    if not premake:
        raise ValueError(f"Premake ID {premake_id} not found")

    batch_size = premake.batch_size or 1
    quantity_units = quantity_batches * batch_size

    total_production_cost = 0
    cost_details = {'materials': [], 'premakes': [], 'packaging': []}

    # Process each component
    for component in premake.components:
        if component.component_type == 'raw_material':
            required_qty = component.quantity * quantity_batches
            deductions = deduct_material_stock(component.component_id, required_qty)

            material_cost = sum(d[3] for d in deductions)
            total_production_cost += material_cost

            material = RawMaterial.query.get(component.component_id)
            cost_details['materials'].append({
                'name': material.name,
                'suppliers_used': [{'supplier_id': d[0], 'qty': d[1], 'cost': d[3]} for d in deductions],
                'total_cost': material_cost
            })

        elif component.component_type == 'premake':
            nested_premake = Product.query.filter_by(id=component.component_id, is_premake=True).first()
            if nested_premake:
                required_qty = component.quantity * quantity_batches

                # Check stock for nested premake
                from .utils import calculate_premake_current_stock
                available_stock = calculate_premake_current_stock(nested_premake.id)
                if available_stock < required_qty:
                    raise InsufficientStockError(
                        _('Insufficient stock for %(name)s. Required: %(required).2f %(unit)s, Available: %(available).2f %(unit)s',
                          name=nested_premake.name, required=required_qty,
                          unit=nested_premake.unit or 'kg', available=available_stock)
                    )

                nested_premake_cost_per_unit = calculate_premake_cost_per_unit(nested_premake, use_actual_costs=False)
                material_cost = nested_premake_cost_per_unit * required_qty
                total_production_cost += material_cost

                cost_details['premakes'].append({
                    'name': nested_premake.name,
                    'qty': required_qty,
                    'cost': material_cost
                })

        elif component.component_type == 'packaging' and component.packaging:
            required_qty = component.quantity * quantity_batches
            packaging_cost = component.packaging.price_per_unit * required_qty
            # Note: Packaging NOT added to production cost
            cost_details['packaging'].append({
                'name': component.packaging.name,
                'qty': required_qty,
                'cost': packaging_cost
            })

    # Calculate cost per unit (per kg/unit, not per batch)
    cost_per_unit = total_production_cost / quantity_units if quantity_units > 0 else 0

    # Create production log
    production_log = ProductionLog(
        product_id=premake_id,
        quantity_produced=quantity_batches,
        total_cost=total_production_cost,
        cost_per_unit=cost_per_unit,
        cost_details=json.dumps(cost_details),
        timestamp=timestamp
    )
    db.session.add(production_log)

    # Update stock for premake
    stock_log = StockLog(
        product_id=premake_id,
        action_type='add',
        quantity=quantity_units
    )
    db.session.add(stock_log)

    return {
        'id': premake_id,
        'name': premake.name,
        'quantity_batches': quantity_batches,
        'quantity_units': quantity_units,
        'cost': total_production_cost
    }
