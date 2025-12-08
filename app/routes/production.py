from datetime import datetime
import json
from flask import Blueprint, render_template, request, redirect, url_for
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
        quantity_produced = float(request.form['quantity_produced'])

        product = Product.query.get_or_404(product_id)

        # Track total production cost
        total_production_cost = 0
        cost_details = {'materials': [], 'premakes': [], 'packaging': []}

        # Deduct materials for production and calculate costs
        try:
            for component in product.components:
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

                        premake_cost_per_unit = calculate_premake_cost_per_unit(premake)
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

                elif component.component_type == 'packaging' and component.packaging:
                    # Calculate packaging cost
                    required_qty = component.quantity * quantity_produced
                    packaging_cost = component.packaging.price_per_unit * required_qty
                    total_production_cost += packaging_cost

                    cost_details['packaging'].append({
                        'name': component.packaging.name,
                        'qty': required_qty,
                        'cost': packaging_cost
                    })

            # Calculate cost per unit
            units_produced = quantity_produced * product.products_per_recipe
            cost_per_unit = total_production_cost / units_produced if units_produced > 0 else 0

            # Create production log WITH COST INFORMATION
            production_log = ProductionLog(
                product_id=product_id,
                quantity_produced=quantity_produced,
                total_cost=total_production_cost,
                cost_per_unit=cost_per_unit,
                cost_details=json.dumps(cost_details)
            )
            db.session.add(production_log)
            db.session.commit()

            return redirect(url_for('production.production'))

        except InsufficientStockError as e:
            # Rollback transaction on error
            db.session.rollback()

            # Get data for template
            products = Product.query.filter_by(is_product=True, is_migrated=False).all()
            production_logs = ProductionLog.query.filter(ProductionLog.product_id != None).order_by(ProductionLog.timestamp.desc()).all()
            current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')

            # Return with error message
            return render_template('production.html',
                                 error=str(e),
                                 products=products,
                                 production_logs=production_logs,
                                 current_time=current_time)

    # Filter to only show actual products (not premakes or preproducts)
    products = Product.query.filter_by(is_product=True, is_migrated=False).all()
    production_logs = ProductionLog.query.filter(ProductionLog.product_id != None).order_by(ProductionLog.timestamp.desc()).all()
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
                        nested_premake_cost_per_unit = calculate_premake_cost_per_unit(nested_premake)
                        required_qty = component.quantity * quantity_batches
                        material_cost = nested_premake_cost_per_unit * required_qty
                        total_production_cost += material_cost

                        cost_details['premakes'].append({
                            'name': nested_premake.name,
                            'qty': required_qty,
                            'cost': material_cost
                        })

                elif component.component_type == 'packaging' and component.packaging:
                    # Calculate packaging cost
                    required_qty = component.quantity * quantity_batches
                    packaging_cost = component.packaging.price_per_unit * required_qty
                    total_production_cost += packaging_cost

                    cost_details['packaging'].append({
                        'name': component.packaging.name,
                        'qty': required_qty,
                        'cost': packaging_cost
                    })

            # Calculate cost per unit (per kg/unit, not per batch)
            cost_per_unit = total_production_cost / quantity_units if quantity_units > 0 else 0

            # Create production log WITH COST INFORMATION
            production_log = ProductionLog(
                product_id=premake_id,
                quantity_produced=quantity_batches,
                total_cost=total_production_cost,
                cost_per_unit=cost_per_unit,
                cost_details=json.dumps(cost_details)
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
