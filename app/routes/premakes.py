from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for
from ..models import db, Product, ProductComponent, StockLog, Category, RawMaterial, Packaging, AuditLog, ProductionLog
from .utils import get_or_create_general_category, log_audit, calculate_premake_current_stock

premakes_blueprint = Blueprint('premakes', __name__)

# ----------------------------
# Premake Management (using Product model with is_premake=True)
# ----------------------------

@premakes_blueprint.route('/premakes')
def premakes():
    """List all premakes (Products with is_premake=True)"""
    premakes = Product.query.filter_by(is_premake=True).all()

    # Calculate current stock for each premake
    for premake in premakes:
        premake.current_stock = calculate_premake_current_stock(premake.id)

        # Calculate cost per unit
        cost_per_batch = 0
        for comp in premake.components:
            if comp.component_type == 'raw_material' and comp.material:
                cost_per_batch += comp.quantity * comp.material.cost_per_unit
            elif comp.component_type == 'packaging' and comp.packaging:
                cost_per_batch += comp.quantity * comp.packaging.price_per_unit

        premake.cost_per_unit = cost_per_batch / premake.batch_size if premake.batch_size > 0 else 0

    return render_template('premakes.html', premakes=premakes)

@premakes_blueprint.route('/premakes/add', methods=['GET', 'POST'])
def add_premake():
    """Add a new premake (Product with is_premake=True)"""
    if request.method == 'POST':
        name = request.form['name']
        category_id = request.form.get('category')
        if not category_id:
            category_id = get_or_create_general_category('premake')

        batch_size = float(request.form.get('batch_size', 1))
        unit = request.form.get('unit', 'unit')

        # Create new premake (as Product with is_premake=True)
        new_premake = Product(
            name=name,
            category_id=category_id,
            batch_size=batch_size,
            unit=unit,
            products_per_recipe=1,  # Not used for premakes
            is_product=False,
            is_premake=True,
            is_preproduct=False
        )
        db.session.add(new_premake)
        db.session.flush()

        # Add components
        component_types = request.form.getlist('component_type[]')
        component_ids = request.form.getlist('component_id[]')
        quantities = request.form.getlist('quantity[]')

        for i in range(len(component_types)):
            if component_types[i] and component_ids[i] and quantities[i]:
                component = ProductComponent(
                    product_id=new_premake.id,
                    component_type=component_types[i],
                    component_id=int(component_ids[i]),
                    quantity=float(quantities[i])
                )
                db.session.add(component)

        db.session.commit()
        log_audit("CREATE", "Premake", new_premake.id, f"Created premake: {name}")

        return redirect(url_for('premakes.premakes'))

    # GET request
    categories = Category.query.filter_by(type='premake').all()
    raw_materials = RawMaterial.query.all()
    packagings = Packaging.query.all()
    nested_premakes = Product.query.filter_by(is_premake=True).all()

    return render_template('add_or_edit_premake.html',
                         premake=None,
                         categories=categories,
                         raw_materials=raw_materials,
                         packagings=packagings,
                         nested_premakes=nested_premakes)

@premakes_blueprint.route('/premakes/edit/<int:premake_id>', methods=['GET', 'POST'])
def edit_premake(premake_id):
    """Edit an existing premake"""
    premake = Product.query.filter_by(id=premake_id, is_premake=True).first_or_404()

    if request.method == 'POST':
        premake.name = request.form['name']
        category_id = request.form.get('category')
        if not category_id:
            category_id = get_or_create_general_category('premake')
        premake.category_id = category_id

        premake.batch_size = float(request.form.get('batch_size', 1))
        premake.unit = request.form.get('unit', 'unit')

        # Clear existing components
        ProductComponent.query.filter_by(product_id=premake.id).delete()

        # Add new components
        component_types = request.form.getlist('component_type[]')
        component_ids = request.form.getlist('component_id[]')
        quantities = request.form.getlist('quantity[]')

        for i in range(len(component_types)):
            if component_types[i] and component_ids[i] and quantities[i]:
                # Prevent circular references
                if component_types[i] == 'premake' and int(component_ids[i]) == premake.id:
                    continue

                component = ProductComponent(
                    product_id=premake.id,
                    component_type=component_types[i],
                    component_id=int(component_ids[i]),
                    quantity=float(quantities[i])
                )
                db.session.add(component)

        db.session.commit()
        log_audit("UPDATE", "Premake", premake.id, f"Updated premake: {premake.name}")

        return redirect(url_for('premakes.premakes'))

    # GET request
    categories = Category.query.filter_by(type='premake').all()
    raw_materials = RawMaterial.query.all()
    packagings = Packaging.query.all()
    # Exclude self from nested premakes to prevent circular references
    nested_premakes = Product.query.filter(
        Product.is_premake == True,
        Product.id != premake_id
    ).all()

    return render_template('add_or_edit_premake.html',
                         premake=premake,
                         categories=categories,
                         raw_materials=raw_materials,
                         packagings=packagings,
                         nested_premakes=nested_premakes)

@premakes_blueprint.route('/premakes/delete/<int:premake_id>', methods=['POST'])
def delete_premake(premake_id):
    """Delete a premake"""
    premake = Product.query.filter_by(id=premake_id, is_premake=True).first_or_404()

    # Check if this premake is used as a component in any products
    usage_count = ProductComponent.query.filter_by(
        component_type='premake',
        component_id=premake_id
    ).count()

    if usage_count > 0:
        # Can't delete - it's being used
        return f"Cannot delete: This premake is used in {usage_count} product(s)", 400

    # Delete components
    ProductComponent.query.filter_by(product_id=premake_id).delete()

    # Delete stock logs
    StockLog.query.filter_by(product_id=premake_id).delete()

    # Delete the premake
    db.session.delete(premake)
    log_audit("DELETE", "Premake", premake_id, f"Deleted premake: {premake.name}")

    db.session.commit()

    return redirect(url_for('premakes.premakes'))

@premakes_blueprint.route('/premakes/update_stock', methods=['POST'])
def update_premake_stock():
    """Update stock for a premake"""
    premake_id = request.form['premake_id']
    quantity = float(request.form['quantity'])
    action_type = request.form['action_type']  # 'add' or 'set'

    if action_type not in ['add', 'set']:
        return "Invalid action type", 400

    stock_log = StockLog(
        product_id=premake_id,
        action_type=action_type,
        quantity=quantity
    )
    db.session.add(stock_log)

    log_audit("UPDATE_STOCK", "Premake", premake_id, f"{action_type} {quantity}")

    db.session.commit()

    return redirect(url_for('premakes.premakes'))