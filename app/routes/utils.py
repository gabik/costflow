from datetime import datetime
from ..models import db, Category, AuditLog, StockLog, ProductionLog, Product

# Predefined units for raw materials
units_list = ["kg", "g", "ml", "l", "piece"]

def hours_to_time_str(hours):
    """Convert decimal hours to HH:MM format string"""
    if hours is None:
        return "00:00"
    total_minutes = int(hours * 60)
    h = total_minutes // 60
    m = total_minutes % 60
    return f"{h:02d}:{m:02d}"

def time_str_to_hours(time_str):
    """Convert HH:MM format string to decimal hours"""
    if not time_str or ':' not in time_str:
        return 0.0
    try:
        parts = time_str.split(':')
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
        return hours + (minutes / 60.0)
    except (ValueError, IndexError):
        return 0.0

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
    elif type_val == 'preproduct':
        name = "מוצרים מקדימים"
        type_val = 'product'  # Preproducts use 'product' type in database

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

def apply_supplier_discount(cost_per_unit, supplier):
    """
    Apply supplier-specific discount to a price.

    Args:
        cost_per_unit: Pre-discount price
        supplier: Supplier object with discount_percentage

    Returns:
        Discounted price
    """
    # DEBUG LOGGING - TEMPORARY
    print(f"DEBUG apply_supplier_discount: cost={cost_per_unit}")
    print(f"DEBUG supplier: {supplier}")
    if supplier:
        print(f"DEBUG supplier.name: {supplier.name if hasattr(supplier, 'name') else 'NO NAME'}")
        print(f"DEBUG has discount_percentage: {hasattr(supplier, 'discount_percentage')}")
        if hasattr(supplier, 'discount_percentage'):
            print(f"DEBUG discount_percentage value: {supplier.discount_percentage}")

    if not supplier or not hasattr(supplier, 'discount_percentage'):
        print(f"DEBUG returning original - supplier check failed")
        return cost_per_unit

    discount_percentage = supplier.discount_percentage or 0.0
    print(f"DEBUG discount_percentage after or: {discount_percentage}")

    if discount_percentage <= 0:
        print(f"DEBUG returning original - discount <= 0")
        return cost_per_unit

    result = cost_per_unit * (1 - discount_percentage / 100.0)
    print(f"DEBUG returning discounted: {result}")
    return result


def get_material_discounted_price(material_id, supplier_id):
    """
    Get discounted price for a material from a specific supplier.

    Args:
        material_id: RawMaterial ID
        supplier_id: Supplier ID

    Returns:
        Discounted price per unit
    """
    from ..models import RawMaterialSupplier

    link = RawMaterialSupplier.query.filter_by(
        raw_material_id=material_id,
        supplier_id=supplier_id
    ).first()

    if not link:
        return 0.0

    return apply_supplier_discount(link.cost_per_unit, link.supplier)


def get_primary_supplier_discounted_price(material):
    """
    Get discounted price from primary supplier for a material.

    Args:
        material: RawMaterial object

    Returns:
        Discounted price from primary supplier, or average if no primary
    """
    # Find primary supplier
    for link in material.supplier_links:
        if link.is_primary:
            return apply_supplier_discount(link.cost_per_unit, link.supplier)

    # Fallback to average (no discount applied to average)
    return material.cost_per_unit

def log_audit(action, target_type, target_id=None, details=None):
    try:
        log = AuditLog(
            action=action,
            target_type=target_type,
            target_id=target_id,
            details=details
        )
        db.session.add(log)
    except Exception:
        # Silently fail audit logging to not interrupt main operations
        pass

def calculate_premake_cost_per_unit(premake, visited=None, use_actual_costs=True):
    """
    Recursively calculates the cost per unit of a premake.
    Works with both old Premake model and new unified Product model.
    Includes cycle detection to prevent infinite recursion.

    If use_actual_costs=True (default), tries to use weighted average of actual
    production costs from ProductionLog. Falls back to estimated cost if no
    production history exists.
    """
    from ..models import ProductionLog

    if visited is None:
        visited = set()

    # Check for cycles
    premake_id = premake.id if hasattr(premake, 'id') else id(premake)
    if premake_id in visited:
        # Cycle detected, return 0 to break the cycle
        return 0

    visited.add(premake_id)

    # Try to use actual costs from production history if requested
    if use_actual_costs and hasattr(premake, 'id'):
        # Get recent production logs with cost data
        recent_productions = ProductionLog.query.filter(
            ProductionLog.product_id == premake.id,
            ProductionLog.cost_per_unit.isnot(None),
            ProductionLog.cost_per_unit > 0
        ).order_by(ProductionLog.timestamp.desc()).limit(10).all()

        if recent_productions:
            # Calculate weighted average cost (weighted by quantity produced)
            total_quantity = sum(log.quantity_produced * (premake.batch_size or 1) for log in recent_productions)
            if total_quantity > 0:
                weighted_cost = sum(
                    log.cost_per_unit * log.quantity_produced * (premake.batch_size or 1)
                    for log in recent_productions
                ) / total_quantity
                return weighted_cost

    # Fallback to estimated cost based on current material prices
    premake_batch_cost = 0
    calculated_batch_size = 0

    for pm_comp in premake.components:
        if pm_comp.component_type == 'raw_material' and pm_comp.material:
            # Use primary supplier DISCOUNTED price
            primary_price = get_primary_supplier_discounted_price(pm_comp.material)
            premake_batch_cost += pm_comp.quantity * primary_price
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

            if nested_premake:
                # Recursive call for nested premakes with visited set
                nested_cost_per_unit = calculate_premake_cost_per_unit(nested_premake, visited.copy(), use_actual_costs)
                premake_batch_cost += pm_comp.quantity * nested_cost_per_unit

    effective_batch_size = premake.batch_size if hasattr(premake, 'batch_size') and premake.batch_size and premake.batch_size > 0 else calculated_batch_size
    return premake_batch_cost / effective_batch_size if effective_batch_size > 0 else 0

def calculate_prime_cost(product):
    """
    Calculates the prime cost (Materials + Packaging + Premakes + Preproducts) for a single unit of a Product.
    Includes recursive calculation for Premakes and Preproducts.
    Works with both old Premake model and new unified Product model.
    """
    # For migrated products, use stored original cost
    if hasattr(product, 'is_migrated') and product.is_migrated:
        return product.original_prime_cost or 0

    total_cost = 0
    for component in product.components:
        if component.component_type == 'raw_material' and component.material:
            # Use primary supplier DISCOUNTED price
            primary_price = get_primary_supplier_discounted_price(component.material)
            total_cost += component.quantity * primary_price
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

            if premake:
                # Use the recursive function to calculate premake cost
                premake_unit_cost = calculate_premake_cost_per_unit(premake)
                total_cost += component.quantity * premake_unit_cost
        elif component.component_type == 'product':
            # Handle preproduct components
            preproduct = Product.query.filter_by(id=component.component_id, is_preproduct=True).first()
            if preproduct:
                # Recursively calculate the prime cost of the preproduct
                preproduct_unit_cost = calculate_prime_cost(preproduct)
                total_cost += component.quantity * preproduct_unit_cost

    if hasattr(product, 'products_per_recipe') and product.products_per_recipe > 0:
        return total_cost / product.products_per_recipe
    return 0

def calculate_premake_current_stock(premake_id):
    """
    Calculates the current stock of a premake (Product with is_premake=True) based on StockLogs and ProductionLogs.
    """
    # Get last 'set' action for this premake
    last_set_log = StockLog.query.filter(
        StockLog.product_id == premake_id,
        StockLog.action_type == 'set'
    ).order_by(StockLog.timestamp.desc()).first()

    stock = last_set_log.quantity if last_set_log else 0

    # Get all 'add' actions after last set
    add_logs = StockLog.query.filter(
        StockLog.product_id == premake_id,
        StockLog.action_type == 'add',
        StockLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
    ).all()

    for log in add_logs:
        stock += log.quantity

    # Subtract premakes used in produced products (ONLY actual products, not premake production)
    # Get all products (not premakes) to check for premake consumption
    production_logs = ProductionLog.query.join(
        Product, ProductionLog.product_id == Product.id
    ).filter(
        ProductionLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min),
        Product.is_product == True,  # Only actual products, not premake production
        ProductionLog.product_id != None
    ).all()

    for production in production_logs:
        product = production.product
        if product:
            for component in product.components:
                if component.component_type == 'premake' and component.component_id == premake_id:
                    stock -= component.quantity * production.quantity_produced # component.quantity is per recipe

    return stock

def calculate_premake_stock_at_date(premake_id, cutoff_date):
    """
    Calculates the stock of a premake as of a specific date.
    Similar to calculate_premake_current_stock but with a date cutoff.
    """
    from ..models import StockLog, ProductionLog, Product
    from sqlalchemy import func

    # Get last 'set' action before or on cutoff date
    last_set_log = StockLog.query.filter(
        StockLog.product_id == premake_id,
        StockLog.action_type == 'set',
        func.date(StockLog.timestamp) <= cutoff_date
    ).order_by(StockLog.timestamp.desc()).first()

    stock = last_set_log.quantity if last_set_log else 0

    # Get all 'add' actions after last set but before/on cutoff date
    if last_set_log:
        add_logs = StockLog.query.filter(
            StockLog.product_id == premake_id,
            StockLog.action_type == 'add',
            StockLog.timestamp > last_set_log.timestamp,
            func.date(StockLog.timestamp) <= cutoff_date
        ).all()
    else:
        add_logs = StockLog.query.filter(
            StockLog.product_id == premake_id,
            StockLog.action_type == 'add',
            func.date(StockLog.timestamp) <= cutoff_date
        ).all()

    for log in add_logs:
        stock += log.quantity

    # Subtract premakes used in produced products before/on cutoff date (ONLY actual products)
    if last_set_log:
        production_logs = ProductionLog.query.join(
            Product, ProductionLog.product_id == Product.id
        ).filter(
            Product.is_product == True,  # Only actual products, not premake production
            ProductionLog.product_id != None,
            ProductionLog.timestamp > last_set_log.timestamp,
            func.date(ProductionLog.timestamp) <= cutoff_date
        ).all()
    else:
        production_logs = ProductionLog.query.join(
            Product, ProductionLog.product_id == Product.id
        ).filter(
            Product.is_product == True,  # Only actual products, not premake production
            ProductionLog.product_id != None,
            func.date(ProductionLog.timestamp) <= cutoff_date
        ).all()

    for production in production_logs:
        product = production.product
        if product:
            for component in product.components:
                if component.component_type == 'premake' and component.component_id == premake_id:
                    stock -= component.quantity * production.quantity_produced

    return max(0, stock)  # Ensure non-negative

def calculate_supplier_stock(material_id, supplier_id):
    """
    Calculate current stock for a specific supplier-material combination.
    """
    from ..models import StockLog, ProductionLog, Product, RawMaterial

    # Check if material is unlimited
    material = RawMaterial.query.get(material_id)
    if material and material.is_unlimited:
        return float('inf')

    # Get last 'set' action for this supplier
    last_set = StockLog.query.filter_by(
        raw_material_id=material_id,
        supplier_id=supplier_id,
        action_type='set'
    ).order_by(StockLog.timestamp.desc()).first()

    stock = last_set.quantity if last_set else 0

    # Add all 'add' actions after last set
    add_logs = StockLog.query.filter(
        StockLog.raw_material_id == material_id,
        StockLog.supplier_id == supplier_id,
        StockLog.action_type == 'add',
        StockLog.timestamp > (last_set.timestamp if last_set else datetime.min)
    ).all()

    for log in add_logs:
        stock += log.quantity

    # Note: Supplier-specific stock consumption is now tracked in production logs
    # via the deduct_material_stock function which records which suppliers were used.
    # However, for this specific stock calculation function, we don't deduct
    # historical production to avoid circular dependencies.

    return max(0, stock)  # Ensure non-negative

def calculate_total_material_stock(material_id):
    """
    Calculate total stock for a material across all suppliers.
    """
    from ..models import RawMaterialSupplier, StockLog, RawMaterial

    # Check if material is unlimited
    material = RawMaterial.query.get(material_id)
    if material and material.is_unlimited:
        return float('inf')

    total = 0
    supplier_links = RawMaterialSupplier.query.filter_by(raw_material_id=material_id).all()

    # Calculate stock for each supplier
    for link in supplier_links:
        # Now safe to call calculate_supplier_stock since we removed the circular dependency
        supplier_stock = calculate_supplier_stock(material_id, link.supplier_id)
        total += supplier_stock

    # Also include any stock logs without a supplier (legacy data)
    last_set_no_supplier = StockLog.query.filter_by(
        raw_material_id=material_id,
        supplier_id=None,
        action_type='set'
    ).order_by(StockLog.timestamp.desc()).first()

    stock_no_supplier = last_set_no_supplier.quantity if last_set_no_supplier else 0

    # Add all 'add' actions without supplier after last set
    add_logs_no_supplier = StockLog.query.filter(
        StockLog.raw_material_id == material_id,
        StockLog.supplier_id == None,
        StockLog.action_type == 'add',
        StockLog.timestamp > (last_set_no_supplier.timestamp if last_set_no_supplier else datetime.min)
    ).all()

    for log in add_logs_no_supplier:
        stock_no_supplier += log.quantity

    total += stock_no_supplier

    return total

def get_cheapest_supplier_for_material(material_id, required_quantity):
    """
    Returns supplier info for the cheapest available supplier with enough stock.
    """
    from ..models import RawMaterialSupplier

    suppliers_with_stock = []
    supplier_links = RawMaterialSupplier.query.filter_by(raw_material_id=material_id).all()

    for link in supplier_links:
        stock = calculate_supplier_stock(material_id, link.supplier_id)
        if stock > 0:
            # Apply discount to cost
            discounted_price = apply_supplier_discount(link.cost_per_unit, link.supplier)
            suppliers_with_stock.append({
                'supplier_id': link.supplier_id,
                'supplier': link.supplier,
                'cost_per_unit': discounted_price,  # Use discounted price
                'available_stock': stock,
                'is_primary': link.is_primary
            })

    # Sort by DISCOUNTED cost (cheapest first)
    suppliers_with_stock.sort(key=lambda x: x['cost_per_unit'])

    # Return the cheapest supplier with enough stock
    for supplier_info in suppliers_with_stock:
        if supplier_info['available_stock'] >= required_quantity:
            return supplier_info

    # If no single supplier has enough, return the cheapest available
    return suppliers_with_stock[0] if suppliers_with_stock else None

def calculate_material_consumption_plan(product_id, quantity):
    """
    Returns detailed plan of which supplier's stock to use for production.
    """
    from ..models import Product, RawMaterialSupplier

    product = Product.query.get(product_id)
    if not product:
        return []

    consumption_plan = []

    for component in product.components:
        if component.component_type == 'raw_material':
            required_qty = component.quantity * quantity
            material_id = component.component_id

            # Get consumption plan for this material
            material_plan = consume_material_cheapest_first(material_id, required_qty)
            consumption_plan.extend(material_plan)

    return consumption_plan

def consume_material_cheapest_first(material_id, required_qty):
    """
    Plan material consumption using cheapest-first strategy.
    """
    from ..models import RawMaterialSupplier

    supplier_links = RawMaterialSupplier.query.filter_by(raw_material_id=material_id).all()

    suppliers_with_stock = []
    for link in supplier_links:
        stock = calculate_supplier_stock(material_id, link.supplier_id)
        if stock > 0:
            # Apply discount to cost
            discounted_price = apply_supplier_discount(link.cost_per_unit, link.supplier)
            suppliers_with_stock.append({
                'supplier_id': link.supplier_id,
                'supplier_name': link.supplier.name,
                'material_id': material_id,
                'cost_per_unit': discounted_price,  # Use discounted price
                'available_stock': stock
            })

    # Sort by DISCOUNTED cost (cheapest first)
    suppliers_with_stock.sort(key=lambda x: x['cost_per_unit'])

    consumption_plan = []
    remaining = required_qty

    for supplier in suppliers_with_stock:
        if remaining <= 0:
            break

        to_consume = min(supplier['available_stock'], remaining)
        consumption_plan.append({
            'material_id': material_id,
            'supplier_id': supplier['supplier_id'],
            'supplier_name': supplier['supplier_name'],
            'quantity': to_consume,
            'cost_per_unit': supplier['cost_per_unit'],
            'total_cost': to_consume * supplier['cost_per_unit']
        })
        remaining -= to_consume

    if remaining > 0:
        # Not enough stock from any supplier
        consumption_plan.append({
            'material_id': material_id,
            'error': f'Insufficient stock. Need {remaining} more units.',
            'quantity_missing': remaining
        })

    return consumption_plan

def deduct_material_with_supplier_tracking(material_id, quantity):
    """
    Deducts material using cheapest-first strategy, returns list of deductions made.
    """
    from ..models import db, StockLog

    consumption_plan = consume_material_cheapest_first(material_id, quantity)
    deductions = []

    for item in consumption_plan:
        if 'error' not in item:
            # Create a negative stock log for this supplier
            stock_log = StockLog(
                raw_material_id=material_id,
                supplier_id=item['supplier_id'],
                action_type='add',
                quantity=-item['quantity']  # Negative to deduct
            )
            db.session.add(stock_log)

            deductions.append({
                'supplier_id': item['supplier_id'],
                'supplier_name': item['supplier_name'],
                'quantity': item['quantity'],
                'cost': item['total_cost']
            })

    return deductions

def deduct_material_stock(material_id, quantity_needed):
    """
    Deduct stock from suppliers using 'primary first, then others' strategy.
    Returns list of (supplier_id, quantity_deducted, cost_per_unit, total_cost) tuples.
    Raises InsufficientStockError if not enough stock available.
    For unlimited materials, returns empty list (no deduction needed).
    """
    from ..models import db, RawMaterialSupplier, StockLog, InsufficientStockError, RawMaterial

    # Check if material is unlimited
    material = RawMaterial.query.get(material_id)
    if material and material.is_unlimited:
        # Unlimited materials don't need stock deduction
        return []

    deductions = []
    remaining = quantity_needed

    # Get all supplier links sorted by primary first
    supplier_links = RawMaterialSupplier.query.filter_by(
        raw_material_id=material_id
    ).order_by(
        RawMaterialSupplier.is_primary.desc()
    ).all()

    for link in supplier_links:
        if remaining <= 0:
            break

        # Calculate available stock for this supplier
        available = calculate_supplier_stock(material_id, link.supplier_id)

        if available > 0:
            # Deduct what we can from this supplier
            to_deduct = min(available, remaining)

            # Apply discount to cost
            discounted_cost_per_unit = apply_supplier_discount(link.cost_per_unit, link.supplier)

            # Create stock log for deduction (negative add)
            stock_log = StockLog(
                raw_material_id=material_id,
                supplier_id=link.supplier_id,
                action_type='add',  # Using negative value for deduction
                quantity=-to_deduct
            )
            db.session.add(stock_log)

            # Include DISCOUNTED cost information in deductions
            deductions.append((
                link.supplier_id,
                to_deduct,
                discounted_cost_per_unit,  # Discounted cost per unit
                to_deduct * discounted_cost_per_unit  # Discounted total cost
            ))
            remaining -= to_deduct

    if remaining > 0:
        # Not enough stock available
        material_name = material.name if material else f"ID {material_id}"

        raise InsufficientStockError(
            f"אין מספיק מלאי עבור {material_name}. "
            f"נדרש: {quantity_needed:.2f}, זמין: {(quantity_needed - remaining):.2f}"
        )

    return deductions

def calculate_100g_cost(product):
    """
    Calculate cost per 100g for a product or premake.
    Takes into account loss components (negative weight).
    Returns: (cost_100g, total_cost, net_weight)
    """
    total_cost = 0
    total_weight = 0
    loss_weight = 0

    for component in product.components:
        if component.component_type == 'loss':
            # Loss has negative quantity
            loss_weight += component.quantity  # This is negative
            continue

        # Add weight
        if component.component_type in ['raw_material', 'premake', 'product']:
            total_weight += abs(component.quantity)

        # Calculate cost
        if component.component_type == 'raw_material' and component.material:
            # Use primary supplier DISCOUNTED price
            primary_price = get_primary_supplier_discounted_price(component.material)
            total_cost += component.quantity * primary_price

        elif component.component_type == 'packaging' and component.packaging:
            total_cost += component.quantity * component.packaging.price_per_unit

        elif component.component_type == 'premake':
            premake = Product.query.filter_by(id=component.component_id, is_premake=True).first()
            if premake:
                premake_unit_cost = calculate_premake_cost_per_unit(premake)
                total_cost += component.quantity * premake_unit_cost

        elif component.component_type == 'product':
            preproduct = Product.query.filter_by(id=component.component_id, is_preproduct=True).first()
            if preproduct:
                preproduct_unit_cost = calculate_prime_cost(preproduct)
                total_cost += component.quantity * preproduct_unit_cost

    # Calculate net weight (total - loss)
    net_weight = total_weight + loss_weight  # loss_weight is negative

    # Calculate cost per 100g
    if net_weight > 0:
        cost_100g = (total_cost / net_weight) * 100
    else:
        cost_100g = 0

    return cost_100g, total_cost, net_weight
