from datetime import datetime
from ..models import db, Category, AuditLog, StockLog, ProductionLog, Product, Premake

# Predefined units for raw materials
units_list = ["kg", "g", "ml", "l", "piece"]

def get_or_create_general_category(type_val):
    """
    Returns the ID of a 'General' category for the given type.
    Creates it if it doesn't exist.
    """
    name = "כללי"
    if type_val == 'raw_material':
        name = "כללי (חומרי גלם)"
    elif type_val == 'product':
        name = "כללי (מוצרים)"
    elif type_val == 'premake':
        name = "כללי (הכנות)"
        
    category = Category.query.filter_by(name=name, type=type_val).first()
    if not category:
        category = Category(name=name, type=type_val)
        db.session.add(category)
        db.session.commit()
    return category.id

def convert_to_base_unit(quantity, selected_unit, base_unit):
    if selected_unit == base_unit:
        return quantity
    
    if selected_unit == 'g' and base_unit == 'kg':
        return quantity / 1000.0
    if selected_unit == 'kg' and base_unit == 'g':
        return quantity * 1000.0
    
    if selected_unit == 'ml' and base_unit == 'l':
        return quantity / 1000.0
    if selected_unit == 'l' and base_unit == 'ml':
        return quantity * 1000.0
        
    return quantity

def log_audit(action, target_type, target_id=None, details=None):
    try:
        log = AuditLog(
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details
        )
        db.session.add(log)
    except Exception as e:
        print(f"Failed to log audit: {e}")

def calculate_premake_cost_per_unit(premake):
    """
    Recursively calculates the cost per unit of a premake.
    Works with both old Premake model and new unified Product model.
    """
    premake_batch_cost = 0
    calculated_batch_size = 0

    for pm_comp in premake.components:
        if pm_comp.component_type == 'raw_material' and pm_comp.material:
            premake_batch_cost += pm_comp.quantity * pm_comp.material.cost_per_unit
            calculated_batch_size += pm_comp.quantity
        elif pm_comp.component_type == 'packaging' and pm_comp.packaging:
            premake_batch_cost += pm_comp.quantity * pm_comp.packaging.price_per_unit
        elif pm_comp.component_type == 'premake':
            # Handle both old and new models for nested premakes
            nested_premake = None

            # Try to get nested premake from unified Product model first
            if isinstance(premake, Product):
                nested_product = Product.query.filter_by(id=pm_comp.component_id, is_premake=True).first()
                if nested_product:
                    nested_premake = nested_product

            # Fallback to property accessor for old model
            if not nested_premake and hasattr(pm_comp, 'nested_premake'):
                nested_premake = pm_comp.nested_premake

            # If still no nested premake, try old Premake model
            if not nested_premake:
                try:
                    nested_premake = Premake.query.get(pm_comp.component_id)
                except:
                    pass

            if nested_premake:
                # Recursive call for nested premakes
                nested_cost_per_unit = calculate_premake_cost_per_unit(nested_premake)
                premake_batch_cost += pm_comp.quantity * nested_cost_per_unit

    effective_batch_size = premake.batch_size if hasattr(premake, 'batch_size') and premake.batch_size and premake.batch_size > 0 else calculated_batch_size
    return premake_batch_cost / effective_batch_size if effective_batch_size > 0 else 0

def calculate_prime_cost(product):
    """
    Calculates the prime cost (Materials + Packaging + Premakes) for a single unit of a Product.
    Includes recursive calculation for Premakes.
    Works with both old Premake model and new unified Product model.
    """
    # For migrated products, use stored original cost
    if hasattr(product, 'is_migrated') and product.is_migrated:
        return product.original_prime_cost or 0

    total_cost = 0
    for component in product.components:
        if component.component_type == 'raw_material' and component.material:
            total_cost += component.quantity * component.material.cost_per_unit
        elif component.component_type == 'packaging' and component.packaging:
            total_cost += component.quantity * component.packaging.price_per_unit
        elif component.component_type == 'premake':
            # Handle both old and new models for premakes
            premake = None

            # Try unified Product model first
            try:
                premake_product = Product.query.filter_by(id=component.component_id, is_premake=True).first()
                if premake_product:
                    premake = premake_product
            except:
                pass

            # Fallback to component.premake property for old model
            if not premake and hasattr(component, 'premake') and component.premake:
                premake = component.premake

            # Try old Premake model if still no premake
            if not premake:
                try:
                    premake = Premake.query.get(component.component_id)
                except:
                    pass

            if premake:
                # Use the recursive function to calculate premake cost
                premake_unit_cost = calculate_premake_cost_per_unit(premake)
                total_cost += component.quantity * premake_unit_cost

    if hasattr(product, 'products_per_recipe') and product.products_per_recipe > 0:
        return total_cost / product.products_per_recipe
    return 0

def calculate_premake_current_stock(premake_id):
    """
    Calculates the current stock of a given premake based on StockLogs and ProductionLogs.
    Works with both old Premake model (using premake_id) and new unified Product model (using product_id).
    """
    from sqlalchemy import or_

    # Check if this is a unified Product with is_premake=True
    is_unified_product = False
    try:
        product = Product.query.filter_by(id=premake_id, is_premake=True).first()
        if product:
            is_unified_product = True
    except:
        pass

    # Get last 'set' action
    last_set_log = None
    if is_unified_product:
        # Check both fields for unified model
        last_set_log = StockLog.query.filter(
            or_(StockLog.product_id == premake_id, StockLog.premake_id == premake_id),
            StockLog.action_type == 'set'
        ).order_by(StockLog.timestamp.desc()).first()
    else:
        # Old model - check premake_id only
        last_set_log = StockLog.query.filter_by(premake_id=premake_id, action_type='set') \
            .order_by(StockLog.timestamp.desc()).first()

    stock = last_set_log.quantity if last_set_log else 0

    # Get all 'add' actions after last set
    if is_unified_product:
        add_logs = StockLog.query.filter(
            or_(StockLog.product_id == premake_id, StockLog.premake_id == premake_id),
            StockLog.action_type == 'add',
            StockLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
        ).all()
    else:
        add_logs = StockLog.query.filter(
            StockLog.premake_id == premake_id,
            StockLog.action_type == 'add',
            StockLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
        ).all()

    for log in add_logs:
        stock += log.quantity

    # Subtract premakes used in produced products
    production_logs = ProductionLog.query.filter(
        ProductionLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min),
        ProductionLog.product_id != None # Only consider product production that consumes premakes
    ).all()

    for production in production_logs:
        product = Product.query.get(production.product_id)
        if product:
            for component in product.components:
                if component.component_type == 'premake' and component.component_id == premake_id:
                    stock -= component.quantity * production.quantity_produced # component.quantity is per recipe

    return stock
