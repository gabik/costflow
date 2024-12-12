from flask import Blueprint, render_template, request, redirect, url_for
from .models import db, RawMaterial, Labor, Packaging, Product, ProductComponent

main_blueprint = Blueprint('main', __name__)

@main_blueprint.route('/')
def index():
    return render_template('index.html')

@main_blueprint.route('/products')
def products():
    products = Product.query.all()
    return render_template('products.html', products=products)

@main_blueprint.route('/products/<int:product_id>')
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)
    components = ProductComponent.query.filter_by(product_id=product_id).all()
    details = []
    for component in components:
        if component.component_type == 'raw_material':
            item = RawMaterial.query.get(component.component_id)
        elif component.component_type == 'labor':
            item = Labor.query.get(component.component_id)
        elif component.component_type == 'packaging':
            item = Packaging.query.get(component.component_id)
        details.append({
            'type': component.component_type,
            'name': item.name,
            'quantity': component.quantity,
            'cost_per_unit': item.cost_per_unit if hasattr(item, 'cost_per_unit') else item.total_hourly_rate,
            'total_cost': component.quantity * (item.cost_per_unit if hasattr(item, 'cost_per_unit') else item.total_hourly_rate)
        })
    return render_template('product_detail.html', product=product, details=details)

@main_blueprint.route('/products/add', methods=['GET', 'POST'])
def add_product():
    if request.method == 'POST':
        name = request.form['name']
        product = Product(name=name)
        db.session.add(product)
        db.session.commit()
        return redirect(url_for('main.products'))
    return render_template('add_product.html')

