from flask import Blueprint, request, jsonify
from .models import db, RawMaterial, Recipe, Labor, ProductionLog

main_blueprint = Blueprint('main', __name__)

@main_blueprint.route('/raw_materials', methods=['POST'])
def add_raw_material():
    data = request.get_json()
    new_material = RawMaterial(
        name=data['name'],
        category=data['category'],
        unit=data['unit'],
        cost_per_unit=data['cost_per_unit'],
        stock=data['stock']
    )
    db.session.add(new_material)
    db.session.commit()
    return jsonify({"message": "Raw material added successfully."}), 201

@main_blueprint.route('/raw_materials', methods=['GET'])
def get_raw_materials():
    materials = RawMaterial.query.all()
    return jsonify([
        {
            "id": material.id,
            "name": material.name,
            "category": material.category,
            "unit": material.unit,
            "cost_per_unit": material.cost_per_unit,
            "stock": material.stock
        } for material in materials
    ])

@main_blueprint.route('/recipes', methods=['POST'])
def add_recipe():
    data = request.get_json()
    new_recipe = Recipe(
        name=data['name'],
        material_id=data['material_id'],
        quantity=data['quantity'],
        cost_per_unit=data['cost_per_unit']
    )
    db.session.add(new_recipe)
    db.session.commit()
    return jsonify({"message": "Recipe added successfully."}), 201

@main_blueprint.route('/recipes', methods=['GET'])
def get_recipes():
    recipes = Recipe.query.all()
    return jsonify([
        {
            "id": recipe.id,
            "name": recipe.name,
            "material_id": recipe.material_id,
            "quantity": recipe.quantity,
            "cost_per_unit": recipe.cost_per_unit
        } for recipe in recipes
    ])

@main_blueprint.route('/labor', methods=['POST'])
def add_labor():
    data = request.get_json()
    new_labor = Labor(
        name=data['name'],
        base_hourly_rate=data['base_hourly_rate'],
        additional_hourly_rate=data['additional_hourly_rate'],
        total_hourly_rate=data['base_hourly_rate'] + data['additional_hourly_rate']
    )
    db.session.add(new_labor)
    db.session.commit()
    return jsonify({"message": "Labor added successfully."}), 201

@main_blueprint.route('/labor', methods=['GET'])
def get_labor():
    labor = Labor.query.all()
    return jsonify([
        {
            "id": l.id,
            "name": l.name,
            "base_hourly_rate": l.base_hourly_rate,
            "additional_hourly_rate": l.additional_hourly_rate,
            "total_hourly_rate": l.total_hourly_rate
        } for l in labor
    ])

@main_blueprint.route('/')
def index():
    return "Welcome to the Waste Tracking System API"

