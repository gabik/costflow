from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from .models import db, RawMaterial, Labor, Packaging, Product, ProductComponent, Category

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
    return render_template('raw_materials.html', materials=materials)

@main_blueprint.route('/raw_materials/add', methods=['GET', 'POST'])
def add_raw_material():
    if request.method == 'POST':
        name = request.form['name']
        category_id = request.form['category']
        unit = request.form['unit']
        cost_per_unit = request.form['cost_per_unit']
        stock = request.form['stock']

        category = Category.query.get(category_id)
        if not category:
            return "Invalid category selected", 400

        new_material = RawMaterial(name=name, category=category.name, unit=unit, cost_per_unit=cost_per_unit, stock=stock)
        db.session.add(new_material)
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

        material.category = category.name
        material.unit = request.form['unit']
        material.cost_per_unit = request.form['cost_per_unit']
        material.stock = request.form['stock']
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
        base_hourly_rate = request.form['base_hourly_rate']
        additional_hourly_rate = request.form['additional_hourly_rate']
        total_hourly_rate = float(base_hourly_rate) + float(additional_hourly_rate)

        new_labor = Labor(name=name, base_hourly_rate=base_hourly_rate, additional_hourly_rate=additional_hourly_rate, total_hourly_rate=total_hourly_rate)
        db.session.add(new_labor)
        db.session.commit()

        # Handle modal submissions
        if request.referrer and 'products/add' in request.referrer:
            return redirect(request.referrer)

        return redirect(url_for('main.labor'))

    return render_template('add_labor.html')

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
        name = request.form['name']
        products_per_recipe = request.form['products_per_recipe']
        selling_price_per_unit = request.form['selling_price_per_unit']

        # Create the product
        product = Product(name=name, products_per_recipe=int(products_per_recipe), selling_price_per_unit=float(selling_price_per_unit))
        db.session.add(product)
        db.session.commit()

        # Add raw materials
        for key in request.form.keys():
            if key.startswith('raw_material_id_'):
                index = key.split('_')[-1]
                raw_material_id = request.form.get(f'raw_material_id_{index}')
                amount = request.form.get(f'raw_material_amount_{index}')
                if raw_material_id and amount:
                    component = ProductComponent(
                        product_id=product.id,
                        component_type='raw_material',
                        component_id=int(raw_material_id),
                        quantity=float(amount)
                    )
                    db.session.add(component)

        # Add packaging
        for key in request.form.keys():
            if key.startswith('packaging_id_'):
                index = key.split('_')[-1]
                packaging_id = request.form.get(f'packaging_id_{index}')
                units_per_recipe = request.form.get(f'packaging_amount_{index}')
                if packaging_id and units_per_recipe:
                    component = ProductComponent(
                        product_id=product.id,
                        component_type='packaging',
                        component_id=int(packaging_id),
                        quantity=float(units_per_recipe)
                    )
                    db.session.add(component)

        # Add labor
        for key in request.form.keys():
            if key.startswith('labor_id_'):
                index = key.split('_')[-1]
                labor_id = request.form.get(f'labor_id_{index}')
                hours = request.form.get(f'labor_hours_{index}')
                if labor_id and hours:
                    component = ProductComponent(
                        product_id=product.id,
                        component_type='labor',
                        component_id=int(labor_id),
                        quantity=float(hours)
                    )
                    db.session.add(component)

        # Commit all changes to the database
        db.session.commit()
        return redirect(url_for('main.products'))

    # Fetch all available data for the form
    all_raw_materials = RawMaterial.query.all()
    all_packaging = Packaging.query.all()
    all_labor = Labor.query.all()
    categories = Category.query.all()
    return render_template(
        'add_or_edit_product.html',
        product=None,
        all_raw_materials=all_raw_materials,
        all_packaging=all_packaging,
        all_labor=all_labor,
        categories=categories,
        units=units_list
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
