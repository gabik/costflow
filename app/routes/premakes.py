from flask import Blueprint, render_template, request, redirect, url_for
from sqlalchemy import func
from ..models import db, Premake, PremakeComponent, RawMaterial, StockLog, Category, ProductComponent, Product
from .utils import log_audit, get_or_create_general_category, convert_to_base_unit, units_list, calculate_premake_current_stock

premakes_blueprint = Blueprint('premakes', __name__)

# ----------------------------
# Premakes Management
# ----------------------------
@premakes_blueprint.route('/premakes')
def premakes():
    premakes = Premake.query.all()
    return render_template('premakes.html', premakes=premakes)

@premakes_blueprint.route('/premakes/add', methods=['GET', 'POST'])
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
        return redirect(url_for('premakes.premakes'))

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

@premakes_blueprint.route('/premakes/edit/<int:premake_id>', methods=['GET', 'POST'])
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
        return redirect(url_for('premakes.premakes'))

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

@premakes_blueprint.route('/premakes/delete/<int:premake_id>', methods=['POST'])
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
    return redirect(url_for('premakes.premakes'))

@premakes_blueprint.route('/premakes/<int:premake_id>', methods=['GET'])
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