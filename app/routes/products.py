import os
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import render_template, request, redirect, url_for, current_app
from ..models import db, Product, ProductComponent, RawMaterial, Packaging, Premake, PremakeComponent, Category, StockLog, Labor
from ..utils import calculate_prime_cost, convert_to_base_unit, get_or_create_general_category, log_audit, units_list
from . import main_blueprint

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
            
            components_data.append({'id': material_id, 'qty': final_quantity})

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
            component = PremakeComponent(
                premake_id=premake.id,
                component_type='raw_material',
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
    premake_categories = Category.query.filter_by(type='premake').all()
    categories = Category.query.filter_by(type='raw_material').all() # For raw material modal if needed

    return render_template(
        'add_or_edit_premake.html',
        premake=None,
        all_raw_materials=all_raw_materials,
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
        
        premake.batch_size = batch_size
            
        log_audit("UPDATE", "Premake", premake.id, f"Updated premake {premake.name}")
        db.session.commit()
        return redirect(url_for('main.premakes'))

    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    premake_categories = Category.query.filter_by(type='premake').all()
    categories = Category.query.filter_by(type='raw_material').all()

    return render_template(
        'add_or_edit_premake.html',
        premake=premake,
        all_raw_materials=all_raw_materials,
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

# ----------------------------
# Products Management
# ----------------------------
@main_blueprint.route('/products')
def products():
    products = Product.query.all()
    products_data = []
    for product in products:
        cost = calculate_prime_cost(product)
        products_data.append({
            'product': product,
            'prime_cost': cost
        })
    return render_template('products.html', products_data=products_data)

@main_blueprint.route('/products/add', methods=['GET', 'POST'])
def add_product():
    if request.method == 'POST':
        # Extract product-level data
        name = request.form['name']
        category_id = request.form.get('category_id')
        if not category_id:
            category_id = get_or_create_general_category('product')
            
        products_per_recipe = request.form['products_per_recipe']
        selling_price_per_unit = request.form['selling_price_per_unit']
        
        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename:
                filename = secure_filename(file.filename)
                # Ensure unique filename to prevent overwrites? For simple MVP, simple secure is okay, or prepend timestamp.
                # Let's prepend timestamp for uniqueness.
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                filename = f"{timestamp}_{filename}"
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                image_filename = filename

        # Create a new Product entry
        product = Product(
            name=name,
            category_id=category_id,
            products_per_recipe=int(products_per_recipe),
            selling_price_per_unit=float(selling_price_per_unit),
            image_filename=image_filename
        )
        db.session.add(product)
        db.session.flush()
        log_audit("CREATE", "Product", product.id, f"Created product {product.name}")
        # Process raw materials
        raw_materials = request.form.getlist('raw_material[]')
        raw_material_quantities = request.form.getlist('raw_material_quantity[]')
        raw_material_units = request.form.getlist('raw_material_unit[]')
        
        # Handle case where units might not be sent if old form used (though we updated templates)
        # Zip stops at shortest list. If units missing, it might break or skip.
        # Ensure lists are same length or handle index.
        # Safest: Iterate by index.
        for i in range(len(raw_materials)):
            material_id = raw_materials[i]
            quantity = raw_material_quantities[i]
            selected_unit = raw_material_units[i] if i < len(raw_material_units) else None
            
            if not material_id or not quantity or float(quantity) <= 0:
                continue
                
            # Convert quantity to base unit
            material = RawMaterial.query.get(material_id)
            if not material:
                continue
                
            final_quantity = convert_to_base_unit(float(quantity), selected_unit, material.unit)
            
            component = ProductComponent(
                product_id=product.id,
                component_type='raw_material',
                component_id=material_id,
                quantity=final_quantity
            )
            db.session.add(component)

        # Process packaging
        packaging_ids = request.form.getlist('packaging[]')
        packaging_quantities = request.form.getlist('packaging_quantity[]')
        for pkg_id, quantity in zip(packaging_ids, packaging_quantities):
            if not pkg_id or not quantity or float(quantity) <= 0:
                continue
            component = ProductComponent(
                product_id=product.id,
                component_type='packaging',
                component_id=pkg_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        # Process premakes
        premake_ids = request.form.getlist('premake[]')
        premake_quantities = request.form.getlist('premake_quantity[]')
        for premake_id, quantity in zip(premake_ids, premake_quantities):
            if not premake_id or not quantity or float(quantity) <= 0:
                continue
            component = ProductComponent(
                product_id=product.id,
                component_type='premake',
                component_id=premake_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        db.session.commit()  # Save all components
        return redirect(url_for('main.products'))

    # For GET requests, load the data required for the form
    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    categories = Category.query.filter_by(type='raw_material').all() # Keep this for modals?
    # No, wait. The 'categories' var passed here is used for "Add Raw Material Modal".
    # I need a separate list for "Product Categories".
    product_categories = Category.query.filter_by(type='product').all()
    
    all_packaging = [p.to_dict() for p in Packaging.query.all()]
    all_labor = [labor_item.to_dict() for labor_item in Labor.query.all()]
    all_premakes = [p.to_dict() for p in Premake.query.all()]
    
    return render_template(
        'add_or_edit_product.html',
        product=None,
        all_raw_materials=all_raw_materials,
        all_packaging=all_packaging,
        all_labor=all_labor,
        all_premakes=all_premakes,
        categories=categories, # For raw material modal
        product_categories=product_categories, # For product form
        units=units_list # For raw material modal
    )


@main_blueprint.route('/products/<int:product_id>', methods=['GET'])
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)

    # Retrieve raw materials used in the product
    raw_materials = [
        {
            'name': RawMaterial.query.get(component.component_id).name,
            'quantity': component.quantity,
            'price_per_unit': RawMaterial.query.get(component.component_id).cost_per_unit,
            'price_per_recipe': component.quantity * RawMaterial.query.get(component.component_id).cost_per_unit,
            'price_per_product': (component.quantity * RawMaterial.query.get(component.component_id).cost_per_unit) / product.products_per_recipe
        }
        for component in ProductComponent.query.filter_by(product_id=product_id, component_type='raw_material')
    ]

    # Retrieve labor costs
    labor_costs = [
        {
            'name': Labor.query.get(component.component_id).name,
            'hours': component.quantity,
            'price_per_hour': Labor.query.get(component.component_id).total_hourly_rate,
            'price_per_recipe': component.quantity * Labor.query.get(component.component_id).total_hourly_rate,
            'price_per_product': (component.quantity * Labor.query.get(component.component_id).total_hourly_rate) / product.products_per_recipe
        }
        for component in ProductComponent.query.filter_by(product_id=product_id, component_type='labor')
    ]

    # Retrieve packaging costs
    packaging_costs = [
        {
            'name': Packaging.query.get(component.component_id).name,
            'quantity': component.quantity,
            'price_per_package': Packaging.query.get(component.component_id).price_per_package,
            'price_per_unit': Packaging.query.get(component.component_id).price_per_package / Packaging.query.get(component.component_id).quantity_per_package,
            'price_per_recipe': component.quantity * (Packaging.query.get(component.component_id).price_per_package / Packaging.query.get(component.component_id).quantity_per_package),
            'price_per_product': (component.quantity * (Packaging.query.get(component.component_id).price_per_package / Packaging.query.get(component.component_id).quantity_per_package)) / product.products_per_recipe
        }
        for component in ProductComponent.query.filter_by(product_id=product_id, component_type='packaging')
    ]

    # Retrieve premake costs
    premake_costs = []
    for component in ProductComponent.query.filter_by(product_id=product_id, component_type='premake'):
        premake = Premake.query.get(component.component_id)
        if not premake:
             continue
             
        # Calculate cost per unit of premake
        premake_total_cost = 0
        calculated_batch_size = 0
        for sub_comp in premake.components:
             if sub_comp.component_type == 'raw_material':
                 mat = RawMaterial.query.get(sub_comp.component_id)
                 if mat:
                     premake_total_cost += sub_comp.quantity * mat.cost_per_unit
                 calculated_batch_size += sub_comp.quantity
             elif sub_comp.component_type == 'packaging':
                 pkg = Packaging.query.get(sub_comp.component_id)
                 if pkg:
                     premake_total_cost += sub_comp.quantity * pkg.price_per_unit
        
        # Use stored batch size if valid, otherwise use calculated sum
        effective_batch_size = premake.batch_size if premake.batch_size > 0 else calculated_batch_size
        
        cost_per_unit_premake = premake_total_cost / effective_batch_size if effective_batch_size > 0 else 0
        
        premake_costs.append({
            'name': premake.name,
            'quantity': component.quantity,
            'unit': premake.unit,
            'batch_size': effective_batch_size,
            'price_per_unit': cost_per_unit_premake,
            'price_per_recipe': component.quantity * cost_per_unit_premake,
            'price_per_product': (component.quantity * cost_per_unit_premake) / product.products_per_recipe,
            'components': [
                 {
                     'name': c.material.name if c.component_type == 'raw_material' else c.packaging.name,
                     'quantity': c.quantity,
                     'unit': c.material.unit if c.component_type == 'raw_material' else 'pcs'
                 } for c in premake.components
            ]
        })

    return render_template(
        'product_details.html',
        product=product,
        raw_materials=raw_materials,
        labor_costs=labor_costs,
        packaging_costs=packaging_costs,
        premake_costs=premake_costs
    )

@main_blueprint.route('/products/edit/<int:product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)

    if request.method == 'POST':
        product.name = request.form['name']
        product.category_id = request.form.get('category_id')
        if not product.category_id:
            product.category_id = get_or_create_general_category('product')
            
        product.products_per_recipe = int(request.form['products_per_recipe'])
        product.selling_price_per_unit = float(request.form['selling_price_per_unit'])
        
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename:
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                filename = f"{timestamp}_{filename}"
                file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                product.image_filename = filename

        # Clear existing components
        ProductComponent.query.filter_by(product_id=product_id).delete()

        # Process raw materials
        raw_materials = request.form.getlist('raw_material[]')
        raw_material_quantities = request.form.getlist('raw_material_quantity[]')
        raw_material_units = request.form.getlist('raw_material_unit[]')
        
        for i in range(len(raw_materials)):
            material_id = raw_materials[i]
            quantity = raw_material_quantities[i]
            selected_unit = raw_material_units[i] if i < len(raw_material_units) else None
            
            if not material_id or not quantity or float(quantity) <= 0:
                continue
                
            material = RawMaterial.query.get(material_id)
            if not material:
                continue
                
            final_quantity = convert_to_base_unit(float(quantity), selected_unit, material.unit)
            
            component = ProductComponent(
                product_id=product.id,
                component_type='raw_material',
                component_id=material_id,
                quantity=final_quantity
            )
            db.session.add(component)

        # Process packaging
        packaging_ids = request.form.getlist('packaging[]')
        packaging_quantities = request.form.getlist('packaging_quantity[]')
        for pkg_id, quantity in zip(packaging_ids, packaging_quantities):
            if not pkg_id or not quantity or float(quantity) <= 0:
                continue
            component = ProductComponent(
                product_id=product.id,
                component_type='packaging',
                component_id=pkg_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        # Process premakes
        premake_ids = request.form.getlist('premake[]')
        premake_quantities = request.form.getlist('premake_quantity[]')
        for premake_id, quantity in zip(premake_ids, premake_quantities):
            if not premake_id or not quantity or float(quantity) <= 0:
                continue
            component = ProductComponent(
                product_id=product.id,
                component_type='premake',
                component_id=premake_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        log_audit("UPDATE", "Product", product.id, f"Updated product {product.name}")
        db.session.commit()
        return redirect(url_for('main.products'))

    # Prepopulate fields for editing
    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    all_packaging = [p.to_dict() for p in Packaging.query.all()]
    all_labor = [labor_item.to_dict() for labor_item in Labor.query.all()]
    all_premakes = [p.to_dict() for p in Premake.query.all()]
    
    categories = Category.query.filter_by(type='raw_material').all()
    product_categories = Category.query.filter_by(type='product').all()

    # Pass both the object (for Jinja server-side) and the dict (for JS client-side)
    return render_template(
        'add_or_edit_product.html',
        product=product,
        all_raw_materials=all_raw_materials,
        all_packaging=all_packaging,
        all_labor=all_labor,
        all_premakes=all_premakes,
        categories=categories,
        product_categories=product_categories,
        units=units_list # For raw material modal
    )
