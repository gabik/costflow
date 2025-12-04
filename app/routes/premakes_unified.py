"""
Premakes routes using unified Product model.
This version uses Product model with is_premake=True flag instead of separate Premake model.
"""
from flask import Blueprint, render_template, request, redirect, url_for
from sqlalchemy import func, or_
from ..models import db, Product, ProductComponent, RawMaterial, StockLog, Category
from .utils import log_audit, get_or_create_general_category, convert_to_base_unit, units_list

premakes_blueprint = Blueprint('premakes', __name__)

def calculate_premake_current_stock(product_id):
    """Calculate current stock for a product that acts as a premake"""
    # Get last 'set' action
    last_set_log = StockLog.query.filter_by(product_id=product_id, action_type='set') \
        .order_by(StockLog.timestamp.desc()).first()

    stock = last_set_log.quantity if last_set_log else 0

    # Add all 'add' actions after last set
    from datetime import datetime
    add_logs = StockLog.query.filter(
        StockLog.product_id == product_id,
        StockLog.action_type == 'add',
        StockLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
    ).all()

    for log in add_logs:
        stock += log.quantity

    # Deduct usage in production
    from ..models import ProductionLog
    productions = ProductionLog.query.filter(
        ProductionLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
    ).all()

    for production in productions:
        if production.product and production.product.is_product:
            for component in production.product.components:
                if component.component_type == 'premake' and component.component_id == product_id:
                    stock -= component.quantity * production.quantity_produced

    return stock

# ----------------------------
# Premakes Management (using Product model)
# ----------------------------
@premakes_blueprint.route('/premakes')
def premakes():
    # Get all products that are premakes (including hybrids)
    premakes = Product.query.filter_by(is_premake=True).all()

    # Add current stock calculation for each
    for premake in premakes:
        premake.current_stock = calculate_premake_current_stock(premake.id)

    return render_template('premakes.html', premakes=premakes)

@premakes_blueprint.route('/premakes/add', methods=['GET', 'POST'])
def add_premake():
    if request.method == 'POST':
        name = request.form['name']
        category_id = request.form.get('category_id')
        if not category_id:
            category_id = get_or_create_general_category('premake')

        unit = request.form.get('unit', 'kg')
        category = Category.query.get(category_id)

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
            batch_size += final_quantity

            components_data.append({'id': material_id, 'qty': final_quantity, 'type': 'raw_material'})

        # Process premakes (which are now products with is_premake=True)
        premake_ids = request.form.getlist('premake[]')
        premake_quantities = request.form.getlist('premake_quantity[]')
        for premake_id, quantity in zip(premake_ids, premake_quantities):
            if not premake_id or not quantity or float(quantity) <= 0:
                continue
            components_data.append({'id': premake_id, 'qty': float(quantity), 'type': 'premake'})

        # Create as Product with is_premake=True
        premake = Product(
            name=name,
            category_id=category_id,
            products_per_recipe=1,  # Default for premakes
            selling_price_per_unit=0,  # Premakes typically aren't sold
            is_product=False,  # Not sellable by default
            is_premake=True,   # Can be used as component
            batch_size=batch_size
        )
        db.session.add(premake)
        db.session.flush()

        log_audit("CREATE", "Premake", premake.id, f"Created premake {premake.name}")

        # Add components
        for item in components_data:
            component = ProductComponent(
                product_id=premake.id,
                component_type=item['type'],
                component_id=item['id'],
                quantity=item['qty']
            )
            db.session.add(component)

        # Initial Stock Log (start with 0)
        initial_stock_log = StockLog(
            product_id=premake.id,  # Using product_id now
            action_type='set',
            quantity=0
        )
        db.session.add(initial_stock_log)

        db.session.commit()
        return redirect(url_for('premakes.premakes'))

    # Get all raw materials and premakes for the form
    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]

    # Get all products that can be used as premakes (is_premake=True)
    all_premakes = [p.to_dict() for p in Product.query.filter_by(is_premake=True).all()]

    premake_categories = Category.query.filter_by(type='premake').all()
    categories = Category.query.filter_by(type='raw_material').all()

    return render_template(
        'add_or_edit_premake.html',
        premake=None,
        all_raw_materials=all_raw_materials,
        all_premakes=all_premakes,
        premake_categories=premake_categories,
        categories=categories,
        units=units_list
    )

@premakes_blueprint.route('/premakes/edit/<int:premake_id>', methods=['GET', 'POST'])
def edit_premake(premake_id):
    # Get product that is a premake
    premake = Product.query.filter_by(id=premake_id, is_premake=True).first_or_404()

    if request.method == 'POST':
        premake.name = request.form['name']
        premake.category_id = request.form.get('category_id')
        if not premake.category_id:
            premake.category_id = get_or_create_general_category('premake')

        unit = request.form.get('unit', 'kg')

        # Check if batch_size is stored in a different field or calculated
        # For unified model, we'll use batch_size field

        # Clear existing components
        ProductComponent.query.filter_by(product_id=premake.id).delete()

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

            component = ProductComponent(
                product_id=premake.id,
                component_type='raw_material',
                component_id=material_id,
                quantity=final_quantity
            )
            db.session.add(component)

        # Process premake components
        premake_ids = request.form.getlist('premake[]')
        premake_quantities = request.form.getlist('premake_quantity[]')
        for sub_premake_id, quantity in zip(premake_ids, premake_quantities):
            if not sub_premake_id or not quantity or float(quantity) <= 0:
                continue
            # Check to prevent self-referencing
            if int(sub_premake_id) == premake.id:
                continue

            component = ProductComponent(
                product_id=premake.id,
                component_type='premake',
                component_id=sub_premake_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        premake.batch_size = batch_size

        log_audit("UPDATE", "Premake", premake.id, f"Updated premake {premake.name}")
        db.session.commit()
        return redirect(url_for('premakes.premakes'))

    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    # Filter out the current premake to avoid self-reference
    all_premakes = [p.to_dict() for p in Product.query.filter(
        Product.is_premake == True, Product.id != premake_id
    ).all()]

    premake_categories = Category.query.filter_by(type='premake').all()
    categories = Category.query.filter_by(type='raw_material').all()

    # Create a premake-like object for backward compatibility with templates
    # Add a unit property if needed
    if not hasattr(premake, 'unit'):
        premake.unit = 'kg'  # Default unit

    return render_template(
        'add_or_edit_premake.html',
        premake=premake,
        all_raw_materials=all_raw_materials,
        all_premakes=all_premakes,
        premake_categories=premake_categories,
        categories=categories,
        units=units_list
    )

@premakes_blueprint.route('/premakes/delete/<int:premake_id>', methods=['POST'])
def delete_premake(premake_id):
    premake = Product.query.filter_by(id=premake_id, is_premake=True).first_or_404()

    # Check if used as component in other products
    if ProductComponent.query.filter_by(component_type='premake', component_id=premake.id).first():
        return "Cannot delete premake used in products", 400

    # Delete related StockLogs
    StockLog.query.filter_by(product_id=premake.id).delete()

    # Delete related Components
    ProductComponent.query.filter_by(product_id=premake.id).delete()

    db.session.delete(premake)
    log_audit("DELETE", "Premake", premake_id, f"Deleted premake {premake.name}")
    db.session.commit()
    return redirect(url_for('premakes.premakes'))

@premakes_blueprint.route('/premakes/<int:premake_id>', methods=['GET'])
def premake_detail(premake_id):
    premake = Product.query.filter_by(id=premake_id, is_premake=True).first_or_404()

    components_data = []
    total_cost = 0

    for component in premake.components:
        if component.component_type == 'raw_material' and component.material:
            cost = component.quantity * component.material.cost_per_unit
            total_cost += cost
            components_data.append({
                'type': 'raw_material',
                'name': component.material.name,
                'quantity': component.quantity,
                'unit': component.material.unit,
                'cost_per_unit': component.material.cost_per_unit,
                'total_cost': cost
            })
        elif component.component_type == 'packaging' and component.packaging:
            cost = component.quantity * component.packaging.price_per_unit
            total_cost += cost
            components_data.append({
                'type': 'packaging',
                'name': component.packaging.name,
                'quantity': component.quantity,
                'unit': 'pcs',
                'cost_per_unit': component.packaging.price_per_unit,
                'total_cost': cost
            })
        elif component.component_type == 'premake':
            # Handle nested premakes - get the Product with is_premake=True
            nested_premake = Product.query.filter_by(id=component.component_id, is_premake=True).first()
            if nested_premake:
                # Calculate nested premake cost using utility function
                from .utils import calculate_premake_cost_per_unit
                nested_cost_per_unit = calculate_premake_cost_per_unit(nested_premake)
                cost = component.quantity * nested_cost_per_unit
                total_cost += cost
                components_data.append({
                    'type': 'premake',
                    'name': nested_premake.name,
                    'quantity': component.quantity,
                    'unit': getattr(nested_premake, 'unit', 'unit'),
                    'cost_per_unit': nested_cost_per_unit,
                    'total_cost': cost
                })

    # Add percentage
    for item in components_data:
        item['cost_percentage'] = (item['total_cost'] / total_cost * 100) if total_cost > 0 else 0

    cost_per_unit = total_cost / premake.batch_size if premake.batch_size and premake.batch_size > 0 else 0

    # Add unit property for template compatibility
    if not hasattr(premake, 'unit'):
        premake.unit = 'kg'  # Default unit

    return render_template('premake_details.html',
                           premake=premake,
                           components_data=components_data,
                           total_cost=total_cost,
                           cost_per_unit=cost_per_unit,
                           currency_symbol='₪')