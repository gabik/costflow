from flask import Flask, request, jsonify
import os
from models import db, RawMaterial, Recipe, Labor, ProductionLog

# Initialize Flask app
app = Flask(__name__)

# Configure database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///waste_tracking.db")
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Routes
@app.route('/raw_materials', methods=['POST'])
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

@app.route('/raw_materials', methods=['GET'])
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

@app.route('/recipes', methods=['POST'])
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

@app.route('/recipes', methods=['GET'])
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

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=8080)

