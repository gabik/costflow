from flask import Blueprint, render_template, request, redirect, url_for
from .models import db, RawMaterial, Recipe, Labor, ProductionLog

main_blueprint = Blueprint('main', __name__)

@main_blueprint.route('/')
def index():
    return render_template('index.html')

@main_blueprint.route('/raw_materials')
def raw_materials():
    materials = RawMaterial.query.all()
    return render_template('raw_materials.html', materials=materials)

@main_blueprint.route('/raw_materials/add', methods=['GET', 'POST'])
def add_raw_material():
    if request.method == 'POST':
        name = request.form['name']
        category = request.form['category']
        unit = request.form['unit']
        cost_per_unit = request.form['cost_per_unit']
        stock = request.form['stock']
        new_material = RawMaterial(name=name, category=category, unit=unit, cost_per_unit=cost_per_unit, stock=stock)
        db.session.add(new_material)
        db.session.commit()
        return redirect(url_for('main.raw_materials'))
    return render_template('add_raw_material.html')

@main_blueprint.route('/recipes')
def recipes():
    recipes = Recipe.query.all()
    return render_template('recipes.html', recipes=recipes)

@main_blueprint.route('/recipes/add', methods=['GET', 'POST'])
def add_recipe():
    if request.method == 'POST':
        name = request.form['name']
        material_id = request.form['material_id']
        quantity = request.form['quantity']
        cost_per_unit = request.form['cost_per_unit']
        new_recipe = Recipe(name=name, material_id=material_id, quantity=quantity, cost_per_unit=cost_per_unit)
        db.session.add(new_recipe)
        db.session.commit()
        return redirect(url_for('main.recipes'))
    materials = RawMaterial.query.all()
    return render_template('add_recipe.html', materials=materials)

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
        return redirect(url_for('main.labor'))
    return render_template('add_labor.html')

