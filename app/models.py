from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

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

