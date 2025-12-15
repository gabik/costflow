from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for
from flask_babel import gettext as _
from ..models import db, Product, ProductComponent, StockLog, Category, RawMaterial, Packaging, AuditLog, ProductionLog
from .utils import get_or_create_general_category, log_audit, calculate_premake_current_stock, get_primary_supplier_discounted_price, calculate_premake_cost_per_unit, format_quantity_with_unit, convert_to_base_unit, get_appropriate_price_unit, calculate_standard_unit_cost

premakes_blueprint = Blueprint('premakes', __name__)

# ----------------------------
# Premake Management (using Product model with is_premake=True)
# ----------------------------

@premakes_blueprint.route('/premakes')
def premakes():
    """List all premakes (Products with is_premake=True)"""
    premakes = Product.query.filter_by(is_premake=True).all()

    # Calculate current stock and costs for each premake
    for premake in premakes:
        premake.current_stock = calculate_premake_current_stock(premake.id)

        # Calculate cost per unit using the comprehensive utility function
        premake.cost_per_unit = calculate_premake_cost_per_unit(premake, use_actual_costs=False)  # Use estimated costs for list view

        # Calculate cost per batch
        premake.cost_per_batch = premake.cost_per_unit * premake.batch_size if premake.batch_size > 0 else 0

        # Format batch size for display with appropriate units
        premake.display_batch_size, premake.display_unit = format_quantity_with_unit(premake.batch_size, premake.unit)

        # Get appropriate display unit based on premake's unit and batch size
        display_unit, unit_label = get_appropriate_price_unit(premake.unit, premake.batch_size)

        # Store display unit info for template
        premake.price_display_unit = display_unit
        premake.price_unit_label = unit_label

        # Calculate costs for different display units (for toggle functionality)
        # These are used by the JavaScript toggle in the template
        if premake.unit in ['kg', 'g']:
            # Calculate both per 100g and per kg
            if premake.unit == 'kg':
                premake.cost_per_100g = (premake.cost_per_unit / 10) if premake.cost_per_unit else 0
                premake.cost_per_kg = premake.cost_per_unit if premake.cost_per_unit else 0
            else:  # g
                premake.cost_per_100g = (premake.cost_per_unit * 100) if premake.cost_per_unit else 0
                premake.cost_per_kg = (premake.cost_per_unit * 1000) if premake.cost_per_unit else 0
        elif premake.unit in ['L', 'ml']:
            # Calculate both per 100ml and per L
            if premake.unit == 'L':
                premake.cost_per_100g = (premake.cost_per_unit / 10) if premake.cost_per_unit else 0  # per 100ml
                premake.cost_per_kg = premake.cost_per_unit if premake.cost_per_unit else 0  # per L
            else:  # ml
                premake.cost_per_100g = (premake.cost_per_unit * 100) if premake.cost_per_unit else 0  # per 100ml
                premake.cost_per_kg = (premake.cost_per_unit * 1000) if premake.cost_per_unit else 0  # per L
        else:
            # For piece/unit, just show per unit
            premake.cost_per_100g = premake.cost_per_unit if premake.cost_per_unit else 0
            premake.cost_per_kg = premake.cost_per_unit if premake.cost_per_unit else 0

    return render_template('premakes.html', premakes=premakes)

@premakes_blueprint.route('/premakes/view/<int:premake_id>')
def view_premake(premake_id):
    """View premake details"""
    premake = Product.query.filter_by(id=premake_id, is_premake=True).first_or_404()

    # Calculate current stock
    premake.current_stock = calculate_premake_current_stock(premake.id)

    # Calculate cost breakdown
    cost_per_batch = 0
    component_costs = []

    for comp in premake.components:
        comp_cost = 0
        comp_name = ""
        comp_unit = ""
        comp_original_price = 0
        comp_discounted_price = 0

        if comp.component_type == 'raw_material' and comp.material:
            # Get both original and discounted prices
            # Find primary supplier original price
            comp_original_price = 0
            for link in comp.material.supplier_links:
                if link.is_primary:
                    comp_original_price = link.cost_per_unit
                    break
            # If no primary, use first supplier
            if comp_original_price == 0 and comp.material.supplier_links:
                comp_original_price = comp.material.supplier_links[0].cost_per_unit
            comp_discounted_price = get_primary_supplier_discounted_price(comp.material)
            comp_cost = comp.quantity * comp_discounted_price
            comp_name = comp.material.name
            comp_unit = comp.material.unit
        elif comp.component_type == 'packaging' and comp.packaging:
            comp_original_price = comp_discounted_price = comp.packaging.price_per_unit
            comp_cost = comp.quantity * comp.packaging.price_per_unit
            comp_name = comp.packaging.name
            comp_unit = "units"
        elif comp.component_type == 'premake' and comp.premake:
            # Calculate nested premake cost recursively with discounts
            nested_cost_per_unit = 0
            nested_original_cost_per_unit = 0
            for nested_comp in comp.premake.components:
                if nested_comp.component_type == 'raw_material' and nested_comp.material:
                    # Use discounted price for nested materials
                    discounted_price = get_primary_supplier_discounted_price(nested_comp.material)
                    nested_cost_per_unit += (nested_comp.quantity * discounted_price) / comp.premake.batch_size if comp.premake.batch_size > 0 else 0
                    # Also calculate original for comparison
                    original_price = 0
                    for link in nested_comp.material.supplier_links:
                        if link.is_primary:
                            original_price = link.cost_per_unit
                            break
                    # If no primary, use first supplier
                    if original_price == 0 and nested_comp.material.supplier_links:
                        original_price = nested_comp.material.supplier_links[0].cost_per_unit
                    nested_original_cost_per_unit += (nested_comp.quantity * original_price) / comp.premake.batch_size if comp.premake.batch_size > 0 else 0
                elif nested_comp.component_type == 'packaging' and nested_comp.packaging:
                    nested_cost_per_unit += (nested_comp.quantity * nested_comp.packaging.price_per_unit) / comp.premake.batch_size if comp.premake.batch_size > 0 else 0
                    nested_original_cost_per_unit += (nested_comp.quantity * nested_comp.packaging.price_per_unit) / comp.premake.batch_size if comp.premake.batch_size > 0 else 0
            comp_discounted_price = nested_cost_per_unit
            comp_original_price = nested_original_cost_per_unit
            comp_cost = comp.quantity * nested_cost_per_unit
            comp_name = comp.premake.name + " (הכנה מקדימה)"
            comp_unit = comp.premake.unit

        cost_per_batch += comp_cost
        # Format quantity with appropriate units for display
        display_quantity, display_unit = format_quantity_with_unit(comp.quantity, comp_unit)
        component_costs.append({
            'name': comp_name,
            'quantity': comp.quantity,  # Keep original for calculations
            'display_quantity': display_quantity,
            'unit': comp_unit,  # Keep original unit
            'display_unit': display_unit,
            'cost': comp_cost,
            'price_per_unit': comp_discounted_price,
            'price_per_unit_original': comp_original_price
        })

    premake.cost_per_unit = cost_per_batch / premake.batch_size if premake.batch_size > 0 else 0
    premake.cost_per_batch = cost_per_batch

    # Get appropriate display unit based on premake's unit and batch size
    display_unit, unit_label = get_appropriate_price_unit(premake.unit, premake.batch_size)

    # Calculate cost for the appropriate display unit
    if premake.unit == 'g':
        if display_unit == 'kg':
            premake.cost_per_kg = premake.cost_per_unit * 1000
            premake.price_unit_display = 'kg'
        else:  # per 100g
            premake.cost_per_kg = premake.cost_per_unit * 100
            premake.price_unit_display = '100g'
    elif premake.unit == 'kg':
        # Already per kg
        premake.cost_per_kg = premake.cost_per_unit
        premake.price_unit_display = 'kg'
    elif premake.unit == 'ml':
        if display_unit == 'L':
            premake.cost_per_kg = premake.cost_per_unit * 1000
            premake.price_unit_display = 'L'
        else:  # per 100ml
            premake.cost_per_kg = premake.cost_per_unit * 100
            premake.price_unit_display = '100ml'
    elif premake.unit == 'L':
        # Already per L
        premake.cost_per_kg = premake.cost_per_unit
        premake.price_unit_display = 'L'
    else:
        # For piece/unit, keep as is
        premake.cost_per_kg = premake.cost_per_unit
        premake.price_unit_display = premake.unit

    # Store the unit label for the template
    premake.price_unit_label = unit_label

    # Format batch size for display
    premake.display_batch_size, premake.display_unit = format_quantity_with_unit(premake.batch_size, premake.unit)

    # Format current stock for display
    if premake.current_stock is not None and premake.current_stock < 999999:
        premake.display_current_stock, premake.display_stock_unit = format_quantity_with_unit(premake.current_stock, premake.unit)
    else:
        premake.display_current_stock = premake.current_stock
        premake.display_stock_unit = premake.unit

    return render_template('view_premake.html', premake=premake, component_costs=component_costs)

@premakes_blueprint.route('/premakes/add', methods=['GET', 'POST'])
def add_premake():
    """Add a new premake (Product with is_premake=True)"""
    if request.method == 'POST':
        name = request.form['name']
        category_id = request.form.get('category')
        if not category_id or category_id == '':
            category_id = get_or_create_general_category('premake')
        else:
            category_id = int(category_id)

        unit = request.form.get('unit', 'kg')  # Default to kg for consistency

        # Get components to calculate batch size
        component_types = request.form.getlist('component_type[]')
        component_ids = request.form.getlist('component_id[]')
        quantities = request.form.getlist('quantity[]')
        units = request.form.getlist('unit[]')  # Get selected units from form

        # Auto-calculate batch_size as sum of all component quantities (converted to premake's unit)
        batch_size = 0
        # Use minimum length to avoid index errors
        min_len = min(len(component_types), len(component_ids), len(quantities))
        for i in range(min_len):
            if component_types[i] and component_ids[i] and quantities[i]:
                quantity = float(quantities[i])

                # Convert quantity to premake's unit
                if component_types[i] == 'raw_material':
                    material = RawMaterial.query.get(component_ids[i])
                    if material:
                        # Convert from material's unit to premake's unit
                        converted_qty = convert_to_base_unit(quantity, material.unit, unit)
                        batch_size += converted_qty
                elif component_types[i] == 'premake':
                    nested_premake = Product.query.filter_by(id=component_ids[i], is_premake=True).first()
                    if nested_premake:
                        # Convert from nested premake's unit to parent premake's unit
                        converted_qty = convert_to_base_unit(quantity, nested_premake.unit, unit)
                        batch_size += converted_qty
                else:
                    # For packaging and other types, no unit conversion needed
                    batch_size += quantity

        # Default to 1 if no components
        if batch_size == 0:
            batch_size = 1

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

        # Add components (already retrieved above for batch_size calculation)
        for i in range(len(component_types)):
            if component_types[i] and component_ids[i] and quantities[i]:
                quantity = float(quantities[i])
                selected_unit = units[i] if i < len(units) else 'kg'

                # ALWAYS convert to kg for storage (base unit)
                if component_types[i] == 'raw_material':
                    material = RawMaterial.query.get(component_ids[i])
                    if material:
                        # Convert from selected unit to kg (always kg for internal storage)
                        base_unit = 'kg' if material.unit in ['kg', 'g'] else material.unit
                        quantity = convert_to_base_unit(quantity, selected_unit, base_unit)
                elif component_types[i] == 'premake':
                    # For premakes, always store in kg
                    quantity = convert_to_base_unit(quantity, selected_unit, 'kg')

                component = ProductComponent(
                    product_id=new_premake.id,
                    component_type=component_types[i],
                    component_id=int(component_ids[i]),
                    quantity=quantity
                )
                db.session.add(component)

        db.session.commit()
        log_audit("CREATE", "Premake", new_premake.id, f"Created premake: {name}")

        return redirect(url_for('premakes.premakes'))

    # GET request
    premake_categories = Category.query.filter_by(type='premake').all()
    raw_materials = RawMaterial.query.filter_by(is_deleted=False).all()
    packagings = Packaging.query.all()
    premakes = Product.query.filter_by(is_premake=True).all()
    units = ['kg', 'g', 'L', 'ml', 'piece', 'unit']

    # Add price information from suppliers to raw materials
    for material in raw_materials:
        # Get primary supplier price or first supplier price
        price = 0
        if material.supplier_links:
            primary_link = next((link for link in material.supplier_links if link.is_primary), None)
            if primary_link:
                price = primary_link.cost_per_unit
            else:
                price = material.supplier_links[0].cost_per_unit
        # Apply waste percentage adjustment to get effective price
        material.base_price = price
        material.display_price = price * material.effective_cost_multiplier

    # Convert to dicts for JSON serialization in template
    all_raw_materials = []
    for m in raw_materials:
        m_dict = m.to_dict()
        m_dict['base_price'] = m.base_price  # Base price without waste
        m_dict['cost_per_unit'] = m.display_price  # Effective price with waste
        m_dict['effective_cost_multiplier'] = m.effective_cost_multiplier
        all_raw_materials.append(m_dict)
    all_premakes = [p.to_dict() for p in premakes]

    return render_template('add_or_edit_premake.html',
                         premake=None,
                         premake_categories=premake_categories,
                         all_raw_materials=raw_materials,
                         all_raw_materials_json=all_raw_materials,
                         packagings=packagings,
                         all_premakes=premakes,
                         all_premakes_json=all_premakes,
                         units=units)

@premakes_blueprint.route('/premakes/edit/<int:premake_id>', methods=['GET', 'POST'])
def edit_premake(premake_id):
    """Edit an existing premake"""
    premake = Product.query.filter_by(id=premake_id, is_premake=True).first_or_404()

    if request.method == 'POST':
        premake.name = request.form['name']
        category_id = request.form.get('category')
        if not category_id or category_id == '':
            premake.category_id = get_or_create_general_category('premake')
        else:
            premake.category_id = int(category_id)

        premake.unit = request.form.get('unit', 'unit')

        # Get components to calculate batch size
        component_types = request.form.getlist('component_type[]')
        component_ids = request.form.getlist('component_id[]')
        quantities = request.form.getlist('quantity[]')
        units = request.form.getlist('unit[]')  # Get selected units from form

        # Auto-calculate batch_size as sum of all component quantities (converted to premake's unit)
        batch_size = 0
        # Use minimum length to avoid index errors
        min_len = min(len(component_types), len(component_ids), len(quantities))
        for i in range(min_len):
            if component_types[i] and component_ids[i] and quantities[i]:
                quantity = float(quantities[i])

                # Convert quantity to premake's unit
                if component_types[i] == 'raw_material':
                    material = RawMaterial.query.get(component_ids[i])
                    if material:
                        # Convert from material's unit to premake's unit
                        converted_qty = convert_to_base_unit(quantity, material.unit, premake.unit)
                        batch_size += converted_qty
                elif component_types[i] == 'premake':
                    nested_premake = Product.query.filter_by(id=component_ids[i], is_premake=True).first()
                    if nested_premake:
                        # Convert from nested premake's unit to parent premake's unit
                        converted_qty = convert_to_base_unit(quantity, nested_premake.unit, premake.unit)
                        batch_size += converted_qty
                else:
                    # For packaging and other types, no unit conversion needed
                    batch_size += quantity

        # Default to 1 if no components
        if batch_size == 0:
            batch_size = 1

        premake.batch_size = batch_size

        # Clear existing components
        ProductComponent.query.filter_by(product_id=premake.id).delete()

        # Add new components
        for i in range(len(component_types)):
            if component_types[i] and component_ids[i] and quantities[i]:
                # Prevent circular references
                if component_types[i] == 'premake' and int(component_ids[i]) == premake.id:
                    continue

                quantity = float(quantities[i])
                selected_unit = units[i] if i < len(units) else 'kg'

                # ALWAYS convert to kg for storage (base unit)
                if component_types[i] == 'raw_material':
                    material = RawMaterial.query.get(component_ids[i])
                    if material:
                        # Convert from selected unit to kg (always kg for internal storage)
                        base_unit = 'kg' if material.unit in ['kg', 'g'] else material.unit
                        quantity = convert_to_base_unit(quantity, selected_unit, base_unit)
                elif component_types[i] == 'premake':
                    # For premakes, always store in kg
                    quantity = convert_to_base_unit(quantity, selected_unit, 'kg')

                component = ProductComponent(
                    product_id=premake.id,
                    component_type=component_types[i],
                    component_id=int(component_ids[i]),
                    quantity=quantity
                )
                db.session.add(component)

        db.session.commit()
        log_audit("UPDATE", "Premake", premake.id, f"Updated premake: {premake.name}")

        return redirect(url_for('premakes.premakes'))

    # GET request
    premake_categories = Category.query.filter_by(type='premake').all()
    raw_materials = RawMaterial.query.filter_by(is_deleted=False).all()
    packagings = Packaging.query.all()
    # Exclude self from nested premakes to prevent circular references
    premakes = Product.query.filter(
        Product.is_premake == True,
        Product.id != premake_id
    ).all()
    units = ['kg', 'g', 'L', 'ml', 'piece', 'unit']

    # Add price information from suppliers to raw materials
    for material in raw_materials:
        # Get primary supplier price or first supplier price
        price = 0
        if material.supplier_links:
            primary_link = next((link for link in material.supplier_links if link.is_primary), None)
            if primary_link:
                price = primary_link.cost_per_unit
            else:
                price = material.supplier_links[0].cost_per_unit
        # Apply waste percentage adjustment to get effective price
        material.base_price = price
        material.display_price = price * material.effective_cost_multiplier

    # Convert to dicts for JSON serialization in template
    all_raw_materials = []
    for m in raw_materials:
        m_dict = m.to_dict()
        m_dict['base_price'] = m.base_price  # Base price without waste
        m_dict['cost_per_unit'] = m.display_price  # Effective price with waste
        m_dict['effective_cost_multiplier'] = m.effective_cost_multiplier
        all_raw_materials.append(m_dict)
    all_premakes = [p.to_dict() for p in premakes]

    return render_template('add_or_edit_premake.html',
                         premake=premake,
                         premake_categories=premake_categories,
                         all_raw_materials=raw_materials,
                         all_raw_materials_json=all_raw_materials,
                         packagings=packagings,
                         all_premakes=premakes,
                         all_premakes_json=all_premakes,
                         units=units)

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