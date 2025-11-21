from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for
from .models import db, RawMaterial, Labor, Packaging, Product, ProductComponent, Category, StockLog, ProductionLog

main_blueprint = Blueprint('main', __name__)

# Predefined units for raw materials
units_list = ["g", "kg", "ml", "l", "piece"]

# Homepage
@main_blueprint.route('/')
def index():
    return render_template('index.html')

# ----------------------------
# Raw Materials Management
# ----------------------------
@main_blueprint.route('/raw_materials')
def raw_materials():
    materials = RawMaterial.query.all()

    for material in materials:
        # Start with the last "Set Stock" log
        last_set_log = StockLog.query.filter_by(raw_material_id=material.id, action_type='set') \
            .order_by(StockLog.timestamp.desc()).first()
        stock = last_set_log.quantity if last_set_log else 0

        # Add "Add Stock" logs after the last "Set Stock"
        add_logs = StockLog.query.filter(
            StockLog.raw_material_id == material.id,
            StockLog.action_type == 'add',
            StockLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
        ).all()
        for log in add_logs:
            stock += log.quantity

        # Subtract raw materials used in produced products
        production_logs = ProductionLog.query.filter(
            ProductionLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
        ).all()

        for production in production_logs:
            product = Product.query.get(production.product_id)
            for component in product.components:
                if component.component_type == 'raw_material' and component.component_id == material.id:
                    stock -= component.quantity * production.quantity_produced

        # Attach calculated stock to material object
        material.current_stock = stock

    return render_template('raw_materials.html', materials=materials)

@main_blueprint.route('/raw_materials/add', methods=['GET', 'POST'])
def add_raw_material():
    if request.method == 'POST':
        name = request.form['name']
        category_id = request.form['category']
        unit = request.form['unit']
        cost_per_unit = float(request.form['cost_per_unit'])
        stock = request.form.get('stock', 0) # Optional initial stock

        category = Category.query.get(category_id)
        if not category:
            return "Invalid category selected", 400

        new_material = RawMaterial(name=name, category=category, unit=unit, cost_per_unit=cost_per_unit)
        db.session.add(new_material)
        db.session.flush() # Get ID for stock log

        if stock:
            initial_stock_log = StockLog(
                raw_material_id=new_material.id,
                action_type='set',
                quantity=float(stock)
            )
            db.session.add(initial_stock_log)

        db.session.commit()

        # Handle modal submissions
        if request.referrer and 'products/add' in request.referrer:
            return redirect(request.referrer)

        return redirect(url_for('main.raw_materials'))

    categories = Category.query.all()
    return render_template('add_or_edit_raw_material.html', material=None, categories=categories, units=units_list)

@main_blueprint.route('/raw_materials/edit/<int:material_id>', methods=['GET', 'POST'])
def edit_raw_material(material_id):
    material = RawMaterial.query.get_or_404(material_id)
    if request.method == 'POST':
        material.name = request.form['name']
        category = Category.query.get(request.form['category'])
        if not category:
            return "Invalid category selected", 400

        material.category = category
        material.unit = request.form['unit']
        material.cost_per_unit = float(request.form['cost_per_unit'])
        
        # Note: Stock is managed via logs, not directly editable here to preserve history
        
        db.session.commit()
        return redirect(url_for('main.raw_materials'))

    categories = Category.query.all()
    return render_template('add_or_edit_raw_material.html', material=material, categories=categories, units=units_list)

@main_blueprint.route('/raw_materials/delete/<int:material_id>', methods=['POST'])
def delete_raw_material(material_id):
    material = RawMaterial.query.get_or_404(material_id)
    db.session.delete(material)
    db.session.commit()
    return redirect(url_for('main.raw_materials'))

@main_blueprint.route('/raw_materials/update_stock', methods=['POST'])
def update_stock():
    raw_material_id = request.form['raw_material_id']
    quantity = float(request.form['quantity'])
    action_type = request.form['action_type']  # 'add' or 'set'

    if action_type not in ['add', 'set']:
        return "Invalid action type", 400

    stock_log = StockLog(raw_material_id=raw_material_id, action_type=action_type, quantity=quantity)
    db.session.add(stock_log)
    db.session.commit()

    return redirect(url_for('main.raw_materials'))

# ----------------------------
# Labor Management
# ----------------------------
@main_blueprint.route('/labor')
def labor():
    labor = Labor.query.all()
    return render_template('labor.html', labor=labor)

@main_blueprint.route('/labor/add', methods=['GET', 'POST'])
def add_labor():
    if request.method == 'POST':
        name = request.form['name']
        base_hourly_rate = float(request.form['base_hourly_rate'])
        additional_hourly_rate = float(request.form['additional_hourly_rate'])

        new_labor = Labor(name=name, base_hourly_rate=base_hourly_rate, additional_hourly_rate=additional_hourly_rate)
        db.session.add(new_labor)
        db.session.commit()

        # Handle modal submissions
        if request.referrer and 'products/add' in request.referrer:
            return redirect(request.referrer)
    return redirect(url_for('main.labor'))

@main_blueprint.route('/labor/edit/<int:labor_id>', methods=['GET', 'POST'])
def edit_labor(labor_id):
    labor_item = Labor.query.get_or_404(labor_id)
    if request.method == 'POST':
        labor_item.name = request.form['name']
        labor_item.base_hourly_rate = float(request.form['base_hourly_rate'])
        labor_item.additional_hourly_rate = float(request.form['additional_hourly_rate'])
        db.session.commit()
        return redirect(url_for('main.labor'))
    return render_template('add_or_edit_labor.html', labor=labor_item)

@main_blueprint.route('/labor/delete/<int:labor_id>', methods=['POST'])
def delete_labor(labor_id):
    # Fetch the labor entry by its ID
    labor = Labor.query.get_or_404(labor_id)

    # Check if this labor is used in any product components
    associated_components = ProductComponent.query.filter_by(component_id=labor_id, component_type='labor').all()
    if associated_components:
        return "Cannot delete labor entry; it is associated with existing products.", 400

    # Delete the labor entry
    db.session.delete(labor)
    db.session.commit()

    return redirect(url_for('main.labor'))

# ----------------------------
# Packaging Management
# ----------------------------
@main_blueprint.route('/packaging', methods=['GET'])
def packaging():
    all_packaging = Packaging.query.all()
    return render_template('packaging.html', packaging=all_packaging)

@main_blueprint.route('/packaging/add', methods=['GET', 'POST'])
def add_packaging():
    if request.method == 'POST':
        name = request.form['name']
        quantity_per_package = request.form['quantity_per_package']
        price_per_package = request.form['price_per_package']

        new_packaging = Packaging(
            name=name,
            quantity_per_package=int(quantity_per_package),
            price_per_package=float(price_per_package)
        )
        db.session.add(new_packaging)
        db.session.commit()
        return redirect(url_for('main.packaging'))

    return render_template('add_or_edit_packaging.html', packaging=None)

@main_blueprint.route('/packaging/edit/<int:packaging_id>', methods=['GET', 'POST'])
def edit_packaging(packaging_id):
    packaging_item = Packaging.query.get_or_404(packaging_id)
    if request.method == 'POST':
        packaging_item.name = request.form['name']
        packaging_item.quantity_per_package = int(request.form['quantity_per_package'])
        packaging_item.price_per_package = float(request.form['price_per_package'])
        db.session.commit()
        return redirect(url_for('main.packaging'))

    return render_template('add_or_edit_packaging.html', packaging=packaging_item)

@main_blueprint.route('/packaging/delete/<int:packaging_id>', methods=['POST'])
def delete_packaging(packaging_id):
    packaging_item = Packaging.query.get_or_404(packaging_id)
    db.session.delete(packaging_item)
    db.session.commit()
    return redirect(url_for('main.packaging'))

# ----------------------------
# Products Management
# ----------------------------
@main_blueprint.route('/products')
def products():
    products = Product.query.all()
    return render_template('products.html', products=products)

@main_blueprint.route('/products/add', methods=['GET', 'POST'])
def add_product():
    if request.method == 'POST':
        # Extract product-level data
        name = request.form['name']
        products_per_recipe = request.form['products_per_recipe']
        selling_price_per_unit = request.form['selling_price_per_unit']

        # Create a new Product entry
        product = Product(
            name=name,
            products_per_recipe=int(products_per_recipe),
            selling_price_per_unit=float(selling_price_per_unit)
        )
        db.session.add(product)
        db.session.commit()  # Save product to get its ID

        # Process raw materials
        raw_materials = request.form.getlist('raw_material[]')
        raw_material_quantities = request.form.getlist('raw_material_quantity[]')
        for material_id, quantity in zip(raw_materials, raw_material_quantities):
            component = ProductComponent(
                product_id=product.id,
                component_type='raw_material',
                component_id=int(material_id),
                quantity=float(quantity)
            )
            db.session.add(component)

        # Process packaging
        packaging = request.form.getlist('packaging[]')
        packaging_quantities = request.form.getlist('packaging_quantity[]')
        for packaging_id, quantity in zip(packaging, packaging_quantities):
            component = ProductComponent(
                product_id=product.id,
                component_type='packaging',
                component_id=int(packaging_id),
                quantity=float(quantity)
            )
            db.session.add(component)

        # Process labor
        labor = request.form.getlist('labor[]')
        labor_hours = request.form.getlist('labor_hours[]')
        for labor_id, hours in zip(labor, labor_hours):
            component = ProductComponent(
                product_id=product.id,
                component_type='labor',
                component_id=int(labor_id),
                quantity=float(hours)
            )
            db.session.add(component)

        db.session.commit()  # Save all components
        return redirect(url_for('main.products'))

    # For GET requests, load the data required for the form
    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    categories = Category.query.all()
    all_packaging = [p.to_dict() for p in Packaging.query.all()]
    all_labor = [l.to_dict() for l in Labor.query.all()]
    return render_template(
        'add_or_edit_product.html',
        product=None,
        product_json=None,
        raw_materials=all_raw_materials, # Note: template uses 'all_raw_materials' for the JS loop, but 'raw_materials' was passed here originally. I will align it to 'all_raw_materials' in the template call for consistency.
        all_packaging=all_packaging,
        all_labor=all_labor,
        categories=categories
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

    return render_template(
        'product_details.html',
        product=product,
        raw_materials=raw_materials,
        labor_costs=labor_costs,
        packaging_costs=packaging_costs
    )

@main_blueprint.route('/products/edit/<int:product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    product = Product.query.get_or_404(product_id)

    if request.method == 'POST':
        product.name = request.form['name']
        product.products_per_recipe = int(request.form['products_per_recipe'])
        product.selling_price_per_unit = float(request.form['selling_price_per_unit'])

        # Clear existing components
        ProductComponent.query.filter_by(product_id=product_id).delete()

        # Add updated raw materials
        raw_materials = request.form.getlist('raw_material[]')
        raw_material_quantities = request.form.getlist('raw_material_quantity[]')
        for material_id, quantity in zip(raw_materials, raw_material_quantities):
            if material_id and quantity: # Check if both are not empty
                component = ProductComponent(
                    product_id=product.id,
                    component_type='raw_material',
                    component_id=int(material_id),
                    quantity=float(quantity)
                )
                db.session.add(component)

        # Add updated packaging
        packaging = request.form.getlist('packaging[]')
        packaging_quantities = request.form.getlist('packaging_quantity[]')
        for packaging_id, quantity in zip(packaging, packaging_quantities):
            if packaging_id and quantity: # Check if both are not empty
                component = ProductComponent(
                    product_id=product.id,
                    component_type='packaging',
                    component_id=int(packaging_id),
                    quantity=float(quantity)
                )
                db.session.add(component)

        # Add updated labor
        labor = request.form.getlist('labor[]')
        labor_hours = request.form.getlist('labor_hours[]')
        for labor_id, hours in zip(labor, labor_hours):
            if labor_id and hours: # Check if both are not empty
                component = ProductComponent(
                    product_id=product.id,
                    component_type='labor',
                    component_id=int(labor_id),
                    quantity=float(hours)
                )
                db.session.add(component)

        db.session.commit()
        return redirect(url_for('main.products'))

    # Prepopulate fields for editing
    all_raw_materials = [m.to_dict() for m in RawMaterial.query.all()]
    all_packaging = [p.to_dict() for p in Packaging.query.all()]
    all_labor = [l.to_dict() for l in Labor.query.all()]

    # Pass both the object (for Jinja server-side) and the dict (for JS client-side)
    return render_template(
        'add_or_edit_product.html',
        product=product,
        product_json=product.to_dict(),
        all_raw_materials=all_raw_materials,
        all_packaging=all_packaging,
        all_labor=all_labor
    )

# ----------------------------
# Categories Management
# ----------------------------
@main_blueprint.route('/categories', methods=['GET', 'POST'])
def categories():
    if request.method == 'POST':
        name = request.form['name']
        if not Category.query.filter_by(name=name).first():
            new_category = Category(name=name)
            db.session.add(new_category)
            db.session.commit()
    all_categories = Category.query.all()
    return render_template('categories.html', categories=all_categories)

@main_blueprint.route('/categories/edit/<int:category_id>', methods=['GET', 'POST'])
def edit_categories(category_id):
    category_item = Category.query.get_or_404(category_id)
    if request.method == 'POST':
        category_item.name = request.form['name']
        db.session.commit()
        return redirect(url_for('main.categories'))
    return render_template('categories.html', category=category_item)

@main_blueprint.route('/categories/delete/<int:category_id>', methods=['POST'])
def delete_category(category_id):
    category_item = Category.query.get_or_404(category_id)
    
    # Optional: Check if category is in use before deleting (prevent FK errors)
    # if category_item.raw_materials:
    #    return "Cannot delete category that has associated raw materials", 400

    db.session.delete(category_item)
    db.session.commit()
    return redirect(url_for('main.categories'))

@main_blueprint.route('/categories/add_from_modal', methods=['POST'])
def add_category_from_modal():
    name = request.form['name']
    if not name.strip():
        return redirect(url_for('main.add_raw_material'))  # Handle empty submissions gracefully

    if not Category.query.filter_by(name=name).first():
        new_category = Category(name=name.strip())
        db.session.add(new_category)
        db.session.commit()

    # Redirect back to the raw materials form
    return redirect(url_for('main.add_raw_material'))

# ----------------------------
# Production Management
# ----------------------------
@main_blueprint.route('/production', methods=['GET', 'POST'])
def production():
    if request.method == 'POST':
        product_id = request.form['product_id']
        quantity_produced = float(request.form['quantity_produced'])

        # Log production
        production_log = ProductionLog(product_id=product_id, quantity_produced=quantity_produced)
        db.session.add(production_log)
        db.session.commit()

        return redirect(url_for('main.production'))

    products = Product.query.all()
    production_logs = ProductionLog.query.order_by(ProductionLog.timestamp.desc()).all()
    current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    return render_template('production.html', products=products, production_logs=production_logs, current_time=current_time)
