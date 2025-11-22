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

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'category_id': self.category_id,
            'unit': self.unit,
            'cost_per_unit': self.cost_per_unit
        }

class Labor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone_number = db.Column(db.String(20), nullable=True)
    base_hourly_rate = db.Column(db.Float, nullable=False)
    additional_hourly_rate = db.Column(db.Float, nullable=False)

    @property
    def total_hourly_rate(self):
        return self.base_hourly_rate + self.additional_hourly_rate

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'phone_number': self.phone_number,
            'base_hourly_rate': self.base_hourly_rate,
            'additional_hourly_rate': self.additional_hourly_rate,
            'total_hourly_rate': self.total_hourly_rate
        }

class Packaging(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    quantity_per_package = db.Column(db.Integer, nullable=False)
    price_per_package = db.Column(db.Float, nullable=False)

    @property
    def price_per_unit(self):
        return self.price_per_package / self.quantity_per_package if self.quantity_per_package > 0 else 0

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name
        }


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    products_per_recipe = db.Column(db.Integer, nullable=False)
    selling_price_per_unit = db.Column(db.Float, nullable=False)
    image_filename = db.Column(db.String(255), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'products_per_recipe': self.products_per_recipe,
            'selling_price_per_unit': self.selling_price_per_unit,
            'image_filename': self.image_filename,
            'components': [c.to_dict() for c in self.components]
        }

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

    def to_dict(self):
        return {
            'id': self.id,
            'component_type': self.component_type,
            'component_id': self.component_id,
            'quantity': self.quantity
        }

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

class WeeklyLaborCost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    week_start_date = db.Column(db.Date, unique=True, nullable=False)
    total_cost = db.Column(db.Float, nullable=False, default=0.0)
    entries = db.relationship('WeeklyLaborEntry', backref='weekly_cost', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'week_start_date': self.week_start_date.strftime('%Y-%m-%d'),
            'total_cost': self.total_cost,
            'entries': [e.to_dict() for e in self.entries]
        }

class WeeklyLaborEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    weekly_cost_id = db.Column(db.Integer, db.ForeignKey('weekly_labor_cost.id'), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('labor.id'), nullable=False)
    hours = db.Column(db.Float, nullable=False)
    cost = db.Column(db.Float, nullable=False) # (Rate + Employer Cost) * Hours

    employee = db.relationship('Labor')

    def to_dict(self):
        return {
            'id': self.id,
            'employee_name': self.employee.name if self.employee else 'Unknown',
            'hours': self.hours,
            'cost': self.cost
        }

class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    action = db.Column(db.String(50), nullable=False)
    target_type = db.Column(db.String(50), nullable=False)
    target_id = db.Column(db.Integer, nullable=True)
    details = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'timestamp': self.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            'action': self.action,
            'target_type': self.target_type,
            'target_id': self.target_id,
            'details': self.details
        }
