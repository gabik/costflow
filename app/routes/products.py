import os
from datetime import datetime
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, current_app
from ..models import db, Product, ProductComponent, RawMaterial, Packaging, Labor, Category, ProductionLog, StockLog, WeeklyProductSales
from .utils import log_audit, calculate_prime_cost, calculate_premake_cost_per_unit, convert_to_base_unit, get_or_create_general_category, units_list, calculate_total_material_stock, calculate_premake_current_stock

products_blueprint = Blueprint('products', __name__)

# ----------------------------
# Products Management
# ----------------------------
@products_blueprint.route('/products')
def products():
    # Show products that can be sold (is_product=True), including hybrids
    # First check if new columns exist
    try:
        products = Product.query.filter(
            Product.is_product == True,
            Product.is_migrated == False
        ).all()
    except:
        # Fallback for pre-migration
        products = Product.query.filter_by(is_migrated=False).all()

    products_data = []
    for product in products:
        cost = calculate_prime_cost(product)

        # Check if we can produce at least one batch
        can_produce = True
        missing_materials = []

        for component in product.components:
            if component.component_type == 'raw_material':
                available = calculate_total_material_stock(component.component_id)
                required = component.quantity  # For one batch
                if available < required:
                    can_produce = False
                    missing_materials.append({
                        'name': component.material.name,
                        'required': required,
                        'available': available
                    })
            elif component.component_type == 'premake':
                available = calculate_premake_current_stock(component.component_id)
                required = component.quantity  # For one batch
                if available < required:
                    can_produce = False
                    missing_materials.append({
                        'name': component.premake.name if component.premake else f'Premake {component.component_id}',
                        'required': required,
                        'available': available
                    })

        products_data.append({
            'product': product,
            'prime_cost': cost,
            'can_produce': can_produce,
            'missing_materials': missing_materials
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

        # Products are always products (not premakes)
        is_product = True
        is_premake = False
        is_preproduct = 'is_preproduct' in request.form  # Check if checkbox is checked
        batch_size = None

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
            image_filename=image_filename,
            is_product=is_product,
            is_premake=is_premake,
            is_preproduct=is_preproduct,
            batch_size=batch_size
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

        # Process preproducts
        preproduct_ids = request.form.getlist('preproduct[]')
        preproduct_quantities = request.form.getlist('preproduct_quantity[]')
        for preproduct_id, quantity in zip(preproduct_ids, preproduct_quantities):
            if not preproduct_id or not quantity or float(quantity) <= 0:
                continue
            component = ProductComponent(
                product_id=product.id,
                component_type='product',
                component_id=preproduct_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        db.session.commit()  # Save all components
        return redirect(url_for('products.products'))

    # For GET requests, load the data required for the form
    # Enhanced material data using primary supplier price
    all_raw_materials = []
    for material in RawMaterial.query.filter_by(is_deleted=False).all():
        material_dict = material.to_dict()

        # Find primary supplier's price
        primary_price = None
        primary_supplier_name = None
        for link in material.supplier_links:
            if link.is_primary:
                primary_price = link.cost_per_unit
                primary_supplier_name = link.supplier.name
                break

        # If no primary supplier, fall back to average (for backward compatibility)
        if primary_price is None:
            primary_price = material.cost_per_unit
            primary_supplier_name = "ממוצע"

        # Override the cost_per_unit with primary supplier's price
        material_dict['cost_per_unit'] = primary_price
        material_dict['display_price'] = primary_price
        material_dict['price_source'] = primary_supplier_name

        all_raw_materials.append(material_dict)

    categories = Category.query.filter_by(type='raw_material').all()
    product_categories = Category.query.filter_by(type='product').all()

    # Enhanced packaging data with price_per_unit
    all_packaging = []
    for pkg in Packaging.query.all():
        pkg_dict = pkg.to_dict()
        pkg_dict['price_per_unit'] = pkg.price_per_unit
        all_packaging.append(pkg_dict)

    all_labor = [labor_item.to_dict() for labor_item in Labor.query.all()]

    # Enhanced premakes data with cost_per_unit
    all_premakes = []
    for p in Product.query.filter_by(is_premake=True).all():
        premake_dict = p.to_dict()
        premake_dict['cost_per_unit'] = calculate_premake_cost_per_unit(p)
        all_premakes.append(premake_dict)

    # Enhanced preproducts data with cost_per_unit
    all_preproducts = []
    for p in Product.query.filter_by(is_preproduct=True).all():
        preproduct_dict = p.to_dict()
        preproduct_dict['cost_per_unit'] = calculate_prime_cost(p)
        all_preproducts.append(preproduct_dict)

    return render_template(
        'add_or_edit_product.html',
        product=None,
        all_raw_materials=all_raw_materials,
        all_packaging=all_packaging,
        all_labor=all_labor,
        all_premakes=all_premakes,
        all_preproducts=all_preproducts,
        categories=categories, # For raw material modal
        product_categories=product_categories, # For product form
        units=units_list # For raw material modal
    )


@products_blueprint.route('/products/<int:product_id>', methods=['GET'])
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)

    # Retrieve raw materials used in the product
    raw_materials = []
    for component in ProductComponent.query.filter_by(product_id=product_id, component_type='raw_material'):
        material = RawMaterial.query.get(component.component_id)
        if not material:
            continue

        # Find primary supplier price for the material
        primary_price = material.cost_per_unit  # default fallback
        for link in material.supplier_links:
            if link.is_primary:
                primary_price = link.cost_per_unit
                break

        raw_materials.append({
            'name': material.name,
            'quantity': component.quantity,
            'price_per_unit': primary_price,  # Use primary supplier price
            'price_per_recipe': component.quantity * primary_price,
            'price_per_product': (component.quantity * primary_price) / product.products_per_recipe if product.products_per_recipe > 0 else 0
        })

    # Retrieve labor costs
    labor_costs = []
    for component in ProductComponent.query.filter_by(product_id=product_id, component_type='labor'):
        labor = Labor.query.get(component.component_id)
        if not labor:
            continue

        labor_costs.append({
            'name': labor.name,
            'hours': component.quantity,
            'price_per_hour': labor.total_hourly_rate,
            'price_per_recipe': component.quantity * labor.total_hourly_rate,
            'price_per_product': (component.quantity * labor.total_hourly_rate) / product.products_per_recipe if product.products_per_recipe > 0 else 0
        })

    # Retrieve packaging costs
    packaging_costs = []
    for component in ProductComponent.query.filter_by(product_id=product_id, component_type='packaging'):
        packaging = Packaging.query.get(component.component_id)
        if not packaging:
            continue

        price_per_unit = packaging.price_per_package / packaging.quantity_per_package if packaging.quantity_per_package > 0 else 0
        packaging_costs.append({
            'name': packaging.name,
            'quantity': component.quantity,
            'price_per_package': packaging.price_per_package,
            'price_per_unit': price_per_unit,
            'price_per_recipe': component.quantity * price_per_unit,
            'price_per_product': (component.quantity * price_per_unit) / product.products_per_recipe if product.products_per_recipe > 0 else 0
        })

    # Retrieve premake costs
    premake_costs = []
    for component in ProductComponent.query.filter_by(product_id=product_id, component_type='premake'):
        # Get premake from unified Product model
        premake = Product.query.filter_by(id=component.component_id, is_premake=True).first()

        if not premake:
            continue

        # Calculate cost per unit of premake using utility function
        premake_unit_cost = calculate_premake_cost_per_unit(premake)

        # Get effective batch size
        effective_batch_size = premake.batch_size if hasattr(premake, 'batch_size') and premake.batch_size and premake.batch_size > 0 else 1

        # Build components list with proper null checks
        components_list = []
        for c in premake.components:
            comp_data = {'quantity': c.quantity}

            if c.component_type == 'raw_material' and c.material:
                comp_data['name'] = c.material.name
                comp_data['unit'] = c.material.unit
            elif c.component_type == 'packaging' and c.packaging:
                comp_data['name'] = c.packaging.name
                comp_data['unit'] = 'pcs'
            elif c.component_type == 'premake':
                # Handle nested premake
                nested = Product.query.filter_by(id=c.component_id, is_premake=True).first()
                if nested:
                    comp_data['name'] = nested.name
                    comp_data['unit'] = getattr(nested, 'unit', 'unit')
                else:
                    continue
            else:
                continue

            components_list.append(comp_data)

        premake_costs.append({
            'name': premake.name,
            'quantity': component.quantity,
            'unit': getattr(premake, 'unit', 'unit'),
            'batch_size': effective_batch_size,
            'price_per_unit': premake_unit_cost,
            'price_per_recipe': component.quantity * premake_unit_cost,
            'price_per_product': (component.quantity * premake_unit_cost) / product.products_per_recipe if product.products_per_recipe > 0 else 0,
            'components': components_list
        })

    # Retrieve preproduct costs
    preproduct_costs = []
    for component in ProductComponent.query.filter_by(product_id=product_id, component_type='product'):
        # Get preproduct from Product model
        preproduct = Product.query.filter_by(id=component.component_id, is_preproduct=True).first()

        if not preproduct:
            continue

        # Calculate prime cost for the preproduct
        preproduct_unit_cost = calculate_prime_cost(preproduct)

        preproduct_costs.append({
            'name': preproduct.name,
            'quantity': component.quantity,
            'unit': getattr(preproduct, 'unit', 'unit'),
            'price_per_unit': preproduct_unit_cost,
            'price_per_recipe': component.quantity * preproduct_unit_cost,
            'price_per_product': (component.quantity * preproduct_unit_cost) / product.products_per_recipe if product.products_per_recipe > 0 else 0
        })

    return render_template(
        'product_details.html',
        product=product,
        raw_materials=raw_materials,
        labor_costs=labor_costs,
        packaging_costs=packaging_costs,
        premake_costs=premake_costs,
        preproduct_costs=preproduct_costs
    )

@products_blueprint.route('/products/migrate_to_premake/<int:product_id>', methods=['POST'])
def migrate_to_premake(product_id):
    """Convert a product to a premake by changing its flags"""
    product = Product.query.get_or_404(product_id)

    # Simply toggle the flags - convert product to premake
    product.is_product = False
    product.is_premake = True

    # Set batch size based on products_per_recipe
    if not product.batch_size:
        product.batch_size = float(product.products_per_recipe) if product.products_per_recipe else 1.0

    # Update category to premake category
    product.category_id = get_or_create_general_category('premake')

    # Calculate and store current stock for tracking
    total_produced = 0
    prod_logs = ProductionLog.query.filter_by(product_id=product.id).all()
    for log in prod_logs:
        total_produced += log.quantity_produced * product.products_per_recipe

    total_sold = 0
    sales = WeeklyProductSales.query.filter_by(product_id=product.id).all()
    for sale in sales:
        total_sold += (sale.quantity_sold + sale.quantity_waste)

    current_stock = total_produced - total_sold
    if current_stock < 0:
        current_stock = 0

    # Set initial stock for the premake
    if current_stock > 0:
        db.session.add(StockLog(
            product_id=product.id,  # Use product_id for unified model
            action_type='set',
            quantity=current_stock
        ))

    log_audit("MIGRATE", "Product", product_id, f"Converted product {product.name} to premake")
    db.session.commit()

    return redirect(url_for('premakes.premakes'))

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

        # Don't change the is_product/is_premake flags - keep them as they were
        # But do update is_preproduct
        product.is_preproduct = 'is_preproduct' in request.form
        
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

        # Process preproducts
        preproduct_ids = request.form.getlist('preproduct[]')
        preproduct_quantities = request.form.getlist('preproduct_quantity[]')
        for preproduct_id, quantity in zip(preproduct_ids, preproduct_quantities):
            if not preproduct_id or not quantity or float(quantity) <= 0:
                continue
            component = ProductComponent(
                product_id=product.id,
                component_type='product',
                component_id=preproduct_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        log_audit("UPDATE", "Product", product.id, f"Updated product {product.name}")
        db.session.commit()
        return redirect(url_for('products.products'))

    # Prepopulate fields for editing
    # Enhanced material data using primary supplier price
    all_raw_materials = []
    for material in RawMaterial.query.filter_by(is_deleted=False).all():
        material_dict = material.to_dict()

        # Find primary supplier's price
        primary_price = None
        primary_supplier_name = None
        for link in material.supplier_links:
            if link.is_primary:
                primary_price = link.cost_per_unit
                primary_supplier_name = link.supplier.name
                break

        # If no primary supplier, fall back to average (for backward compatibility)
        if primary_price is None:
            primary_price = material.cost_per_unit
            primary_supplier_name = "ממוצע"

        # Override the cost_per_unit with primary supplier's price
        material_dict['cost_per_unit'] = primary_price
        material_dict['display_price'] = primary_price
        material_dict['price_source'] = primary_supplier_name

        all_raw_materials.append(material_dict)

    # Enhanced packaging data with price_per_unit
    all_packaging = []
    for pkg in Packaging.query.all():
        pkg_dict = pkg.to_dict()
        pkg_dict['price_per_unit'] = pkg.price_per_unit
        all_packaging.append(pkg_dict)

    all_labor = [labor_item.to_dict() for labor_item in Labor.query.all()]

    # Enhanced premakes data with cost_per_unit
    all_premakes = []
    for p in Product.query.filter_by(is_premake=True).all():
        premake_dict = p.to_dict()
        premake_dict['cost_per_unit'] = calculate_premake_cost_per_unit(p)
        all_premakes.append(premake_dict)

    # Enhanced preproducts data with cost_per_unit
    all_preproducts = []
    for p in Product.query.filter_by(is_preproduct=True).all():
        preproduct_dict = p.to_dict()
        preproduct_dict['cost_per_unit'] = calculate_prime_cost(p)
        all_preproducts.append(preproduct_dict)

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
        all_preproducts=all_preproducts,
        categories=categories,
        product_categories=product_categories,
        units=units_list # For raw material modal
    )
