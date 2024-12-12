from .database import db

class RawMaterial(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(100), nullable=False)
    unit = db.Column(db.String(50), nullable=False)
    cost_per_unit = db.Column(db.Float, nullable=False)
    stock = db.Column(db.Float, nullable=False)

class Labor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    base_hourly_rate = db.Column(db.Float, nullable=False)
    additional_hourly_rate = db.Column(db.Float, nullable=False)
    total_hourly_rate = db.Column(db.Float, nullable=False)

class Packaging(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    cost_per_unit = db.Column(db.Float, nullable=False)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)

class ProductComponent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    component_type = db.Column(db.String(50), nullable=False)  # 'raw_material', 'labor', 'packaging'
    component_id = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Float, nullable=False)

    product = db.relationship('Product', backref=db.backref('components', lazy=True))

