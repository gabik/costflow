from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# Custom exceptions
class InsufficientStockError(Exception):
    """Raised when there is insufficient stock for production"""
    pass

class StockLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    raw_material_id = db.Column(db.Integer, db.ForeignKey('raw_material.id'), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=True)  # Unified field for products/premakes
    packaging_id = db.Column(db.Integer, db.ForeignKey('packaging.id'), nullable=True)  # For packaging stock tracking
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=True)  # Track supplier for raw materials
    action_type = db.Column(db.String(10), nullable=False)  # 'add' or 'set'
    quantity = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    raw_material = db.relationship('RawMaterial', backref='stock_logs')
    product = db.relationship('Product', backref='stock_logs', foreign_keys=[product_id])
    packaging = db.relationship('Packaging', backref='stock_logs')
    supplier = db.relationship('Supplier', backref='stock_logs')

    def to_dict(self):
        return {
            'id': self.id,
            'raw_material_id': self.raw_material_id,
            'product_id': self.product_id,
            'packaging_id': self.packaging_id,
            'supplier_id': self.supplier_id,
            'action_type': self.action_type,
            'quantity': self.quantity,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None
        }

class ProductionLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=True)
    quantity_produced = db.Column(db.Float, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    is_carryover = db.Column(db.Boolean, default=False, nullable=False)

    # Cost tracking fields
    total_cost = db.Column(db.Float, nullable=True)  # Total cost of this production
    cost_per_unit = db.Column(db.Float, nullable=True)  # Cost per unit produced
    cost_details = db.Column(db.Text, nullable=True)  # JSON string with supplier breakdown

    # Relationships
    product = db.relationship('Product', backref='production_logs')

    def to_dict(self):
        return {
            'id': self.id,
            'product_id': self.product_id,
            'quantity_produced': self.quantity_produced,
            'timestamp': self.timestamp.isoformat() if self.timestamp else None,
            'is_carryover': self.is_carryover,
            'total_cost': self.total_cost,
            'cost_per_unit': self.cost_per_unit,
            'cost_details': self.cost_details
        }

class RawMaterial(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    category = db.relationship('Category', backref=db.backref('raw_materials', lazy=True))
    unit = db.Column(db.String(50), nullable=False)
    is_unlimited = db.Column(db.Boolean, default=False, nullable=False)  # Unlimited stock materials
    is_deleted = db.Column(db.Boolean, default=False, nullable=False)  # Soft delete flag

    def get_cheapest_available_supplier(self, required_quantity):
        """Get the cheapest supplier with available stock for the required quantity"""
        from .routes.utils import calculate_supplier_stock

        suppliers_with_stock = []
        for link in self.supplier_links:
            stock = calculate_supplier_stock(self.id, link.supplier_id)
            if stock > 0:
                suppliers_with_stock.append({
                    'supplier_id': link.supplier_id,
                    'supplier': link.supplier,
                    'cost_per_unit': link.cost_per_unit,
                    'available_stock': stock
                })

        # Sort by cost (cheapest first)
        suppliers_with_stock.sort(key=lambda x: x['cost_per_unit'])

        # Return the cheapest supplier with enough stock
        for supplier_info in suppliers_with_stock:
            if supplier_info['available_stock'] >= required_quantity:
                return supplier_info

        # If no single supplier has enough, return the cheapest available
        return suppliers_with_stock[0] if suppliers_with_stock else None

    def calculate_total_stock(self):
        """Calculate total stock across all suppliers"""
        from .routes.utils import calculate_supplier_stock

        total = 0
        for link in self.supplier_links:
            total += calculate_supplier_stock(self.id, link.supplier_id)
        return total

    def get_primary_supplier(self):
        """Get the primary supplier for this material"""
        for link in self.supplier_links:
            if link.is_primary:
                return link.supplier
        # If no primary, return first supplier
        return self.supplier_links[0].supplier if self.supplier_links else None

    @property
    def cost_per_unit(self):
        """Get cost per unit from primary supplier for compatibility"""
        for link in self.supplier_links:
            if link.is_primary:
                return link.cost_per_unit
        # If no primary, return first supplier's cost
        if self.supplier_links:
            return self.supplier_links[0].cost_per_unit
        return 0  # No suppliers

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'category_id': self.category_id,
            'unit': self.unit,
            'is_unlimited': self.is_unlimited,
            'is_deleted': self.is_deleted,
            'suppliers': [link.to_dict() for link in self.supplier_links] if hasattr(self, 'supplier_links') else []
        }


class RawMaterialAlternativeName(db.Model):
    __tablename__ = 'raw_material_alternative_name'

    id = db.Column(db.Integer, primary_key=True)
    raw_material_id = db.Column(db.Integer, db.ForeignKey('raw_material.id'), nullable=False)
    alternative_name = db.Column(db.String(200), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationship
    raw_material = db.relationship('RawMaterial', backref=db.backref('alternative_names', lazy=True, cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'raw_material_id': self.raw_material_id,
            'alternative_name': self.alternative_name,
            'created_at': self.created_at.isoformat() if self.created_at else None
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

    @property
    def price_per_unit(self):
        # Use primary supplier price
        primary_link = self.get_primary_supplier_link()
        if primary_link:
            # Apply supplier discount if any
            from .routes.utils import apply_supplier_discount
            discounted_price = apply_supplier_discount(primary_link.price_per_package, primary_link.supplier)
            return discounted_price / self.quantity_per_package if self.quantity_per_package > 0 else 0
        return 0  # No supplier = no price

    @property
    def price_per_package(self):
        """Computed property for backward compatibility - returns primary supplier's price"""
        primary_link = self.get_primary_supplier_link()
        if primary_link:
            from .routes.utils import apply_supplier_discount
            return apply_supplier_discount(primary_link.price_per_package, primary_link.supplier)
        return 0

    def get_primary_supplier_link(self):
        """Get the primary supplier link for this packaging"""
        for link in self.supplier_links:
            if link.is_primary:
                return link
        # If no primary, return first supplier
        return self.supplier_links[0] if self.supplier_links else None

    def get_primary_supplier(self):
        """Get the primary supplier for this packaging"""
        link = self.get_primary_supplier_link()
        return link.supplier if link else None

    def get_cheapest_available_supplier(self, required_quantity):
        """Get the cheapest supplier with available stock for the required quantity"""
        from .routes.utils import calculate_packaging_supplier_stock

        suppliers_with_stock = []
        for link in self.supplier_links:
            stock = calculate_packaging_supplier_stock(self.id, link.supplier_id)
            if stock > 0:
                suppliers_with_stock.append({
                    'supplier_id': link.supplier_id,
                    'supplier': link.supplier,
                    'price_per_package': link.price_per_package,
                    'price_per_unit': link.price_per_package / self.quantity_per_package if self.quantity_per_package > 0 else 0,
                    'available_stock': stock
                })

        # Sort by price per unit (cheapest first)
        suppliers_with_stock.sort(key=lambda x: x['price_per_unit'])

        # Return the cheapest supplier with enough stock
        for supplier_info in suppliers_with_stock:
            if supplier_info['available_stock'] >= required_quantity:
                return supplier_info

        # If no single supplier has enough, return the cheapest available
        return suppliers_with_stock[0] if suppliers_with_stock else None

    def calculate_total_stock(self):
        """Calculate total stock across all suppliers"""
        from .routes.utils import calculate_packaging_supplier_stock

        total = 0
        for link in self.supplier_links:
            total += calculate_packaging_supplier_stock(self.id, link.supplier_id)
        return total

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'quantity_per_package': self.quantity_per_package,
            'suppliers': [link.to_dict() for link in self.supplier_links] if hasattr(self, 'supplier_links') else []
        }


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    products_per_recipe = db.Column(db.Integer, nullable=False)
    selling_price_per_unit = db.Column(db.Float, nullable=True)  # Made nullable for premakes
    image_filename = db.Column(db.String(255), nullable=True)

    # Unified product/premake fields
    is_product = db.Column(db.Boolean, default=True, nullable=False)
    is_premake = db.Column(db.Boolean, default=False, nullable=False)
    is_preproduct = db.Column(db.Boolean, default=False, nullable=False)  # Can be sold AND used as component
    batch_size = db.Column(db.Float, nullable=True)  # From Premake model
    unit = db.Column(db.String(20), nullable=True)  # Unit of measurement ('kg', 'L', 'piece', etc.)

    category = db.relationship('Category', backref='products')

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'category_name': self.category.name if self.category else None,
            'products_per_recipe': self.products_per_recipe,
            'selling_price_per_unit': self.selling_price_per_unit,
            'image_filename': self.image_filename,
            'components': [c.to_dict() for c in self.components],
            'is_product': self.is_product,
            'is_premake': self.is_premake,
            'is_preproduct': self.is_preproduct,
            'batch_size': self.batch_size,
            'unit': self.unit
        }

class ProductComponent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    component_type = db.Column(db.String(20), nullable=False)  # 'raw_material', 'packaging', 'premake', 'product', 'loss'
    component_id = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Float, nullable=False)

    product = db.relationship('Product', backref='components')

    @property
    def material(self):
        if self.component_type == 'raw_material':
            return RawMaterial.query.get(self.component_id)
        return None

    @property
    def packaging(self):
        if self.component_type == 'packaging':
            return Packaging.query.get(self.component_id)
        return None

    @property
    def premake(self):
        if self.component_type == 'premake':
            return Product.query.filter_by(id=self.component_id, is_premake=True).first()
        return None

    @property
    def preproduct(self):
        if self.component_type == 'product':
            return Product.query.get(self.component_id)
        return None

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
    type = db.Column(db.String(20), nullable=False, default='raw_material') # 'raw_material', 'product', 'premake'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'type': self.type
        }

class WeeklyLaborCost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    week_start_date = db.Column(db.Date, unique=True, nullable=False)
    total_cost = db.Column(db.Float, nullable=False, default=0.0)
    entries = db.relationship('WeeklyLaborEntry', backref='weekly_cost', lazy=True, cascade="all, delete-orphan")
    sales = db.relationship('WeeklyProductSales', backref='weekly_cost', lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            'id': self.id,
            'week_start_date': self.week_start_date.strftime('%Y-%m-%d'),
            'total_cost': self.total_cost,
            'entries': [e.to_dict() for e in self.entries],
            'sales': [s.to_dict() for s in self.sales]
        }

class WeeklyProductSales(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    weekly_cost_id = db.Column(db.Integer, db.ForeignKey('weekly_labor_cost.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity_sold = db.Column(db.Integer, nullable=False, default=0)
    quantity_waste = db.Column(db.Integer, nullable=False, default=0)

    product = db.relationship('Product')

    def to_dict(self):
        return {
            'id': self.id,
            'product_name': self.product.name if self.product else 'Unknown',
            'quantity_sold': self.quantity_sold,
            'quantity_waste': self.quantity_waste
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

class StockAudit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    audit_date = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    raw_material_id = db.Column(db.Integer, db.ForeignKey('raw_material.id'), nullable=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=True)  # Unified field for products/premakes
    packaging_id = db.Column(db.Integer, db.ForeignKey('packaging.id'), nullable=True)  # For packaging audit
    system_quantity = db.Column(db.Float, nullable=False)  # Calculated stock before audit
    physical_quantity = db.Column(db.Float, nullable=False)  # Actual count
    variance = db.Column(db.Float, nullable=False)  # physical - system
    variance_cost = db.Column(db.Float, nullable=False)  # variance * cost_per_unit
    auditor_name = db.Column(db.String(100), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    stock_log_id = db.Column(db.Integer, db.ForeignKey('stock_log.id'), nullable=True)

    # Relationships
    raw_material = db.relationship('RawMaterial', backref='stock_audits')
    product = db.relationship('Product', backref='stock_audits', foreign_keys=[product_id])
    packaging = db.relationship('Packaging', backref='stock_audits')
    stock_log = db.relationship('StockLog', backref='audit')

    def to_dict(self):
        return {
            'id': self.id,
            'audit_date': self.audit_date.strftime('%Y-%m-%d %H:%M:%S'),
            'raw_material_name': self.raw_material.name if self.raw_material else (self.product.name if self.product else 'Unknown'),
            'system_quantity': self.system_quantity,
            'physical_quantity': self.physical_quantity,
            'variance': self.variance,
            'variance_cost': self.variance_cost,
            'auditor_name': self.auditor_name,
            'notes': self.notes
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

class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    contact_person = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    email = db.Column(db.String(100), nullable=True)
    address = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    discount_percentage = db.Column(db.Float, default=0.0, nullable=False)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'contact_person': self.contact_person,
            'phone': self.phone,
            'email': self.email,
            'address': self.address,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'is_active': self.is_active,
            'discount_percentage': self.discount_percentage,
            'materials_count': len(self.material_links) if hasattr(self, 'material_links') else 0
        }

class RawMaterialSupplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    raw_material_id = db.Column(db.Integer, db.ForeignKey('raw_material.id'), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=False)
    cost_per_unit = db.Column(db.Float, nullable=False)
    is_primary = db.Column(db.Boolean, default=False)  # Mark primary supplier
    sku = db.Column(db.String(100), nullable=True)  # SKU for supplier-specific product identification

    raw_material = db.relationship('RawMaterial', backref='supplier_links')
    supplier = db.relationship('Supplier', backref='material_links')

    __table_args__ = (db.UniqueConstraint('raw_material_id', 'supplier_id'),)

    def to_dict(self):
        return {
            'id': self.id,
            'raw_material_id': self.raw_material_id,
            'supplier_id': self.supplier_id,
            'supplier_name': self.supplier.name if self.supplier else None,
            'cost_per_unit': self.cost_per_unit,
            'is_primary': self.is_primary,
            'sku': self.sku
        }

class PackagingSupplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    packaging_id = db.Column(db.Integer, db.ForeignKey('packaging.id'), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=False)
    price_per_package = db.Column(db.Float, nullable=False)
    is_primary = db.Column(db.Boolean, default=False)  # Mark primary supplier
    sku = db.Column(db.String(100), nullable=True)  # SKU for supplier-specific product identification

    packaging = db.relationship('Packaging', backref='supplier_links')
    supplier = db.relationship('Supplier', backref='packaging_links')

    __table_args__ = (db.UniqueConstraint('packaging_id', 'supplier_id'),)

    def to_dict(self):
        return {
            'id': self.id,
            'packaging_id': self.packaging_id,
            'supplier_id': self.supplier_id,
            'supplier_name': self.supplier.name if self.supplier else None,
            'price_per_package': self.price_per_package,
            'is_primary': self.is_primary,
            'sku': self.sku
        }
