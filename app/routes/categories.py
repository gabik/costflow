from flask import Blueprint, render_template, request, redirect, url_for
from ..models import db, Category

categories_blueprint = Blueprint('categories', __name__)

# ----------------------------
# Categories Management
# ----------------------------
@categories_blueprint.route('/categories', methods=['GET', 'POST'])
def categories():
    current_type = request.args.get('type', 'raw_material')
    
    if request.method == 'POST':
        name = request.form['name']
        type_val = request.form.get('type', 'raw_material')
        
        if not Category.query.filter_by(name=name, type=type_val).first():
            new_category = Category(name=name, type=type_val)
            db.session.add(new_category)
            db.session.commit()
        return redirect(url_for('categories.categories', type=type_val))
        
    all_categories = Category.query.filter_by(type=current_type).all()
    return render_template('categories.html', categories=all_categories, current_type=current_type)

@categories_blueprint.route('/categories/edit/<int:category_id>', methods=['GET', 'POST'])
def edit_categories(category_id):
    category_item = Category.query.get_or_404(category_id)
    if request.method == 'POST':
        category_item.name = request.form['name']
        db.session.commit()
        return redirect(url_for('categories.categories'))
    return render_template('categories.html', category=category_item)

@categories_blueprint.route('/categories/delete/<int:category_id>', methods=['POST'])
def delete_category(category_id):
    category_item = Category.query.get_or_404(category_id)
    
    # Optional: Check if category is in use before deleting (prevent FK errors)
    # if category_item.raw_materials:
    #    return "Cannot delete category that has associated raw materials", 400

    db.session.delete(category_item)
    db.session.commit()
    return redirect(url_for('categories.categories'))

@categories_blueprint.route('/categories/add_from_modal', methods=['POST'])
def add_category_from_modal():
    name = request.form['name']
    type_val = request.form.get('type', 'raw_material')
    
    if not name.strip():
        return redirect(request.referrer or url_for('main.index'))

    if not Category.query.filter_by(name=name, type=type_val).first():
        new_category = Category(name=name.strip(), type=type_val)
        db.session.add(new_category)
        db.session.commit()

    # Redirect back to the previous page
    return redirect(request.referrer or url_for('main.index'))
