from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class StockLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    raw_material_id = db.Column(db.Integer, db.ForeignKey('raw_material.id'), nullable=False)
    action_type = db.Column(db.String(10), nullable=False)  # 'add' or 'set'
    quantity = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    raw_material = db.relationship('RawMaterial', backref='stock_logs')

class ProductionLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity_produced = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    # Relationships
    product = db.relationship('Product', backref='production_logs')

class RawMaterial(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    category = db.relationship('Category', backref=db.backref('raw_materials', lazy=True))
    unit = db.Column(db.String(50), nullable=False)
    cost_per_unit = db.Column(db.Float, nullable=False)

class Labor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    base_hourly_rate = db.Column(db.Float, nullable=False)
    additional_hourly_rate = db.Column(db.Float, nullable=False)

    @property
    def total_hourly_rate(self):
        return self.base_hourly_rate + self.additional_hourly_rate

class Packaging(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    quantity_per_package = db.Column(db.Integer, nullable=False)
    price_per_package = db.Column(db.Float, nullable=False)

    @property
    def price_per_unit(self):
        return self.price_per_package / self.quantity_per_package if self.quantity_per_package > 0 else 0


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    products_per_recipe = db.Column(db.Integer, nullable=False)
    selling_price_per_unit = db.Column(db.Float, nullable=False)

class ProductComponent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    component_type = db.Column(db.String(50), nullable=False)  # 'raw_material', 'labor', 'packaging'
    component_id = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    product = db.relationship('Product', backref=db.backref('components', lazy=True))

    @property
    def material(self):
        if self.component_type == 'raw_material':
            return RawMaterial.query.get(self.component_id)

    @property
    def labor(self):
        if self.component_type == 'labor':
            return Labor.query.get(self.component_id)

    @property
    def packaging(self):
        if self.component_type == 'packaging':
            return Packaging.query.get(self.component_id)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
