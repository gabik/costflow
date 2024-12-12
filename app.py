from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
import os

# Initialize Flask app
app = Flask(__name__)

# Configure database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///waste_tracking.db")
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Database models
class RawMaterial(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    unit = db.Column(db.String(50), nullable=False)
    cost_per_unit = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Float, nullable=False)

class Recipe(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey('raw_material.id'), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    cost_per_unit = db.Column(db.Float, nullable=False)

class Labor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    base_hourly_rate = db.Column(db.Float, nullable=False)
    additional_hourly_rate = db.Column(db.Float, nullable=False)
    total_hourly_rate = db.Column(db.Float, nullable=False)

class ProductionLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(100), nullable=False)
    recipe_id = db.Column(db.Integer, db.ForeignKey('recipe.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    actual_cost = db.Column(db.Float, nullable=False)

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
    db.create_all()
    app.run(debug=True, host='0.0.0.0', port=8080)

