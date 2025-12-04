import os
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, current_app
from ..models import db, Product, ProductComponent, RawMaterial, Packaging, Labor, Premake, PremakeComponent, Category, ProductionLog, StockLog, WeeklyProductSales
from .utils import log_audit, calculate_prime_cost, convert_to_base_unit, get_or_create_general_category, units_list

products_blueprint = Blueprint('products', __name__)

# ----------------------------
# Products Management
# ----------------------------
@products_blueprint.route('/products')
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

@products_blueprint.route('/products/add', methods=['GET', 'POST'])
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
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                filename = f"{timestamp}_{filename}"
                
                upload_folder = current_app.config['UPLOAD_FOLDER']
                if not os.path.exists(upload_folder):
                    os.makedirs(upload_folder)
                    
                file.save(os.path.join(upload_folder, filename))
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

        db.session.commit()  # Save all components
        return redirect(url_for('products.products'))

    # For GET requests, load the data required for the form
    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    categories = Category.query.filter_by(type='raw_material').all() 
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


@products_blueprint.route('/products/<int:product_id>', methods=['GET'])
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

@products_blueprint.route('/products/migrate_to_premake/<int:product_id>', methods=['POST'])
def migrate_to_premake(product_id):
    product = Product.query.get_or_404(product_id)
    
    # 1. Create Premake from Product
    # Ensure category exists or use general
    category_id = product.category_id
    if category_id:
        # Check if category type matches? 
        # Product category is type='product', Premake needs 'premake'.
        # We should probably put it in 'General (Premakes)' or create a new category with same name if needed.
        # Simple approach: Put in General (Premakes)
        category_id = get_or_create_general_category('premake')
    else:
        category_id = get_or_create_general_category('premake')

    premake = Premake(
        name=product.name,
        category_id=category_id,
        batch_size=float(product.products_per_recipe),
        unit='unit' # Default unit for migrated product? Or 'batch'? Let's use 'unit' to match product semantics.
    )
    db.session.add(premake)
    db.session.flush() # Get premake ID

    # 2. Copy Components
    for prod_comp in product.components:
        premake_comp = None
        # Map types. Note: Product has 'labor' which Premake usually doesn't have in this schema?
        # PremakeComponent only has 'raw_material' and 'packaging' in original schema, 
        # but we just added 'premake' support via property (and it uses string type in DB).
        # What about 'labor'? PremakeComponent model doesn't strictly forbid 'labor' string, 
        # but do we have logic for it?
        # Premake cost calculation (in utils.py/calculate_prime_cost) usually iterates components.
        # If we add labor, we should ensure it's handled or ignore it.
        # For MVP migration, we will copy 'raw_material', 'packaging', 'premake'. 
        # We will SKIP 'labor' as premakes usually calculate material cost only?
        # Or does the user expect labor to be part of premake cost?
        # If the user "migrates", they expect equivalent cost structure.
        # However, our PremakeComponent model doesn't have a `labor` property accessor yet.
        # Let's copy it if it's not labor, to be safe.
        
        if prod_comp.component_type in ['raw_material', 'packaging', 'premake']:
            db.session.add(PremakeComponent(
                premake_id=premake.id,
                component_type=prod_comp.component_type,
                component_id=prod_comp.component_id,
                quantity=prod_comp.quantity
            ))

    # 3. Inventory Migration
    # Calculate current product stock (Produced - Sold)
    # We need to iterate logs. This is heavy but necessary.
    # Or we can assume the user knows what they are doing and start with 0?
    # "We need to convert all the inventory we have currently for this product to be the premake inventory"
    
    # Calculate Total Produced (from Product ProductionLogs)
    total_produced = 0
    prod_logs = ProductionLog.query.filter_by(product_id=product.id).all()
    for log in prod_logs:
        total_produced += log.quantity_produced * product.products_per_recipe
    
    # Calculate Total Sold
    total_sold = 0
    sales = WeeklyProductSales.query.filter_by(product_id=product.id).all()
    for sale in sales:
        total_sold += (sale.quantity_sold + sale.quantity_waste)
        
    current_stock = total_produced - total_sold
    if current_stock < 0: current_stock = 0
    
    if current_stock > 0:
        # Add StockLog for Premake
        db.session.add(StockLog(
            premake_id=premake.id,
            action_type='set', # Start fresh
            quantity=current_stock
        ))

    # 4. Convert Production History (Optional but good)
    # Move ProductionLogs to point to Premake
    # Product: quantity = recipes. Premake: quantity = batches.
    # If batch_size = products_per_recipe, then quantity is 1:1.
    for log in prod_logs:
        log.product_id = None
        log.premake_id = premake.id
        # log.quantity_produced stays the same (recipes -> batches)
    
    # 5. Handle Product Migration
    # Keep sales history for historical reporting (don't delete WeeklyProductSales)
    # This preserves the ability to see past sales of products that have been migrated

    # Delete product components to prevent further use in production
    # but keep the product record itself for historical reporting
    ProductComponent.query.filter_by(product_id=product.id).delete()

    # Option: Mark product as migrated by clearing its components and possibly renaming
    # The product stays in the database but becomes unusable for new production
    # This maintains referential integrity with WeeklyProductSales
    product.name = f"{product.name} (Migrated to Premake: {premake.name})"

    # Note: We're NOT deleting the product to preserve foreign key relationships
    # db.session.delete(product)  # REMOVED to prevent foreign key violations
    
    log_audit("MIGRATE", "Product", product_id, f"Migrated product {product.name} to premake {premake.name}")
    db.session.commit()
    
    return redirect(url_for('main.premakes'))

@products_blueprint.route('/products/edit/<int:product_id>', methods=['GET', 'POST'])
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
                
                upload_folder = current_app.config['UPLOAD_FOLDER']
                if not os.path.exists(upload_folder):
                    os.makedirs(upload_folder)
                    
                file.save(os.path.join(upload_folder, filename))
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
        return redirect(url_for('products.products'))

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
