from datetime import datetime
from ..models import db, Category, AuditLog, StockLog, ProductionLog, Product

# Predefined units for raw materials
units_list = ["kg", "g", "L", "ml", "piece", "unit"]

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

    # Handle kg/g conversions
    if selected_unit == 'g' and base_unit == 'kg':
        return quantity / 1000.0
    if selected_unit == 'kg' and base_unit == 'g':
        return quantity * 1000.0

    # Handle L/ml conversions (both uppercase L and lowercase l)
    if selected_unit == 'ml' and base_unit in ['L', 'l']:
        return quantity / 1000.0
    if selected_unit in ['L', 'l'] and base_unit == 'ml':
        return quantity * 1000.0

    # Handle g↔ml conversions (1g ≈ 1ml for liquids)
    if selected_unit == 'g' and base_unit == 'ml':
        # g to ml: treat as equivalent (1:1)
        return quantity
    if selected_unit == 'ml' and base_unit == 'g':
        # ml to g: treat as equivalent (1:1)
        return quantity

    # Handle g↔L conversions (when sheet units are 'g', materials in L are treated as ml)
    if selected_unit == 'g' and base_unit in ['L', 'l']:
        # g to L: treat as ml to L (divide by 1000)
        return quantity / 1000.0
    if selected_unit in ['L', 'l'] and base_unit == 'g':
        # L to g: treat as L to ml (multiply by 1000)
        return quantity * 1000.0

    # Handle kg↔L conversions (1kg ≈ 1L for liquids)
    if selected_unit == 'kg' and base_unit in ['L', 'l']:
        # kg to L: treat as equivalent (1:1)
        return quantity
    if selected_unit in ['L', 'l'] and base_unit == 'kg':
        # L to kg: treat as equivalent (1:1)
        return quantity

    # Handle kg↔ml conversions
    if selected_unit == 'kg' and base_unit == 'ml':
        # kg to ml: multiply by 1000 (1kg = 1000ml)
        return quantity * 1000.0
    if selected_unit == 'ml' and base_unit == 'kg':
        # ml to kg: divide by 1000 (1000ml = 1kg)
        return quantity / 1000.0

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
    if not supplier or not hasattr(supplier, 'discount_percentage'):
        return cost_per_unit

    discount_percentage = supplier.discount_percentage or 0.0

    if discount_percentage <= 0:
        return cost_per_unit

    return cost_per_unit * (1 - discount_percentage / 100.0)


def get_material_discounted_price(material_id, supplier_id):
    """
    Get discounted price for a material from a specific supplier,
    adjusted for waste percentage.

    Args:
        material_id: RawMaterial ID
        supplier_id: Supplier ID

    Returns:
        Discounted price per unit adjusted for waste
    """
    from ..models import RawMaterialSupplier, RawMaterial

    link = RawMaterialSupplier.query.filter_by(
        raw_material_id=material_id,
        supplier_id=supplier_id
    ).first()

    if not link:
        return 0.0

    base_price = apply_supplier_discount(link.cost_per_unit, link.supplier)

    # Get material for waste adjustment
    material = RawMaterial.query.get(material_id)
    if material:
        return base_price * material.effective_cost_multiplier

    return base_price


def get_primary_supplier_discounted_price(material):
    """
    Get discounted price from primary supplier for a material.
    NOTE: Does NOT apply waste adjustment - waste is handled via quantity adjustment.

    Args:
        material: RawMaterial object

    Returns:
        Discounted price from primary supplier (without waste adjustment)
    """
    base_price = 0

    # Find primary supplier
    for link in material.supplier_links:
        if link.is_primary:
            base_price = apply_supplier_discount(link.cost_per_unit, link.supplier)
            break

    # Fallback to first supplier if no primary
    if base_price == 0 and material.supplier_links:
        first_link = material.supplier_links[0]
        base_price = apply_supplier_discount(first_link.cost_per_unit, first_link.supplier)

    # Return base price WITHOUT waste adjustment
    # Waste is handled separately:
    # - Quantity adjustment: More material needed from inventory (in deduct_material_stock)
    # - Price adjustment: Applied during production cost calculation (in deduct_material_stock)
    return base_price

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
            # Use primary supplier DISCOUNTED price (WITHOUT waste adjustment)
            primary_price = get_primary_supplier_discounted_price(pm_comp.material)
            # Apply waste to QUANTITY, not price!
            actual_quantity_needed = pm_comp.quantity * pm_comp.material.effective_cost_multiplier
            # Component quantities are stored in kg baseline, material prices are per their unit
            # Only convert if material unit differs from kg
            if pm_comp.material.unit != 'kg':
                # Convert kg to material's unit for cost calculation
                quantity_in_material_unit = convert_to_base_unit(
                    actual_quantity_needed,  # Use waste-adjusted quantity
                    'kg',  # Component is stored in kg
                    pm_comp.material.unit  # Convert to material's unit for pricing
                )
                premake_batch_cost += quantity_in_material_unit * primary_price
            else:
                # Both are in kg, multiply directly
                premake_batch_cost += actual_quantity_needed * primary_price
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
                # Always use recipe-based costs for nested premakes (not production history)
                nested_cost_per_unit = calculate_premake_cost_per_unit(nested_premake, visited.copy(), use_actual_costs=False)
                # Component quantities are ALREADY in kg, no conversion needed
                # Both component quantity and nested premake cost are per kg
                premake_batch_cost += pm_comp.quantity * nested_cost_per_unit

    effective_batch_size = premake.batch_size if hasattr(premake, 'batch_size') and premake.batch_size and premake.batch_size > 0 else calculated_batch_size
    return premake_batch_cost / effective_batch_size if effective_batch_size > 0 else 0

def calculate_prime_cost(product):
    """
    Calculates the prime cost (Materials + Premakes + Preproducts) for a single unit of a Product.
    EXCLUDES packaging costs - packaging is only included when products are sold.
    Includes recursive calculation for Premakes and Preproducts.
    Works with both old Premake model and new unified Product model.
    """
    total_cost = 0
    loss_quantity = 0  # Track loss (stored as negative values)

    for component in product.components:

        if component.component_type == 'loss':
            loss_quantity += component.quantity
            continue

        if component.component_type == 'raw_material' and component.material:
            # Use primary supplier DISCOUNTED price (WITHOUT waste adjustment)
            primary_price = get_primary_supplier_discounted_price(component.material)
            # Apply waste to QUANTITY, not price!
            actual_quantity_needed = component.quantity * component.material.effective_cost_multiplier
            # Component quantities are stored in kg baseline, material prices are per their unit
            # Only convert if material unit differs from kg
            if component.material.unit != 'kg':
                # Convert kg to material's unit for cost calculation
                quantity_in_material_unit = convert_to_base_unit(
                    actual_quantity_needed,  # Use waste-adjusted quantity
                    'kg',  # Component is stored in kg
                    component.material.unit  # Convert to material's unit for pricing
                )
                component_cost = quantity_in_material_unit * primary_price
                total_cost += component_cost
            else:
                # Both are in kg, multiply directly
                component_cost = actual_quantity_needed * primary_price
                total_cost += component_cost
        elif component.component_type == 'packaging' and component.packaging:
            # EXCLUDED FROM PRIME COST - Packaging is only a cost when sold
            pass  # total_cost += component.quantity * component.packaging.price_per_unit
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
                # IMPORTANT: Always use recipe-based costs (use_actual_costs=False) when premakes
                # are components in other products to avoid using corrupted/outdated production costs
                premake_unit_cost = calculate_premake_cost_per_unit(premake, use_actual_costs=False)
                # Component quantities are ALREADY stored in kg, no conversion needed
                # Just multiply directly - both are in kg baseline
                total_cost += component.quantity * premake_unit_cost
        elif component.component_type == 'product':
            # Handle preproduct components
            preproduct = Product.query.filter_by(id=component.component_id, is_preproduct=True).first()
            if preproduct:
                # Recursively calculate the prime cost of the preproduct
                preproduct_unit_cost = calculate_prime_cost(preproduct)
                # Component quantities are stored in their base units:
                # - kg for weight-based preproducts (kg, g)
                # - units for unit-based preproducts (unit, piece, etc.)
                # The calculation works correctly for both types
                total_cost += component.quantity * preproduct_unit_cost

    if hasattr(product, 'products_per_recipe') and product.products_per_recipe > 0:
        # Effective yield = theoretical yield - loss (loss is negative)
        effective_yield = max(0.001, product.products_per_recipe + loss_quantity)
        return total_cost / effective_yield
    return 0

def calculate_cogs_with_packaging(product):
    """
    Calculates the Cost of Goods Sold (COGS) including packaging for a single unit of a Product.
    This is used when products are actually sold.
    COGS = Prime Cost (Materials + Premakes) + Packaging
    """
    # Start with prime cost (materials + premakes, no packaging)
    prime_cost = calculate_prime_cost(product)

    # Add packaging cost
    packaging_cost = 0
    for component in product.components:
        if component.component_type == 'packaging' and component.packaging:
            packaging_cost += component.quantity * component.packaging.price_per_unit

    # Calculate per unit cost
    if hasattr(product, 'products_per_recipe') and product.products_per_recipe > 0:
        packaging_cost_per_unit = packaging_cost / product.products_per_recipe
    else:
        packaging_cost_per_unit = 0

    return prime_cost + packaging_cost_per_unit

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
    from ..models import RawMaterialSupplier, RawMaterial

    # Get material to access waste percentage
    material = RawMaterial.query.get(material_id)

    suppliers_with_stock = []
    supplier_links = RawMaterialSupplier.query.filter_by(raw_material_id=material_id).all()

    for link in supplier_links:
        stock = calculate_supplier_stock(material_id, link.supplier_id)
        if stock > 0:
            # Apply discount to cost (without waste - to be added separately)
            discounted_price = apply_supplier_discount(link.cost_per_unit, link.supplier)
            # Apply waste adjustment
            if material:
                discounted_price = discounted_price * material.effective_cost_multiplier
            suppliers_with_stock.append({
                'supplier_id': link.supplier_id,
                'supplier': link.supplier,
                'cost_per_unit': discounted_price,  # Use discounted price with waste
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
    from ..models import RawMaterialSupplier, RawMaterial

    # Get material to access waste percentage
    material = RawMaterial.query.get(material_id)

    supplier_links = RawMaterialSupplier.query.filter_by(raw_material_id=material_id).all()

    suppliers_with_stock = []
    for link in supplier_links:
        stock = calculate_supplier_stock(material_id, link.supplier_id)
        if stock > 0:
            # Apply discount to cost (without waste - to be added separately)
            discounted_price = apply_supplier_discount(link.cost_per_unit, link.supplier)
            # Apply waste adjustment
            if material:
                discounted_price = discounted_price * material.effective_cost_multiplier
            suppliers_with_stock.append({
                'supplier_id': link.supplier_id,
                'supplier_name': link.supplier.name,
                'material_id': material_id,
                'cost_per_unit': discounted_price,  # Use discounted price with waste
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
    Adjusts for waste percentage - if recipe needs 5kg usable and material has 50% waste,
    deducts 10kg from inventory.
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

    # Apply waste adjustment to quantity needed
    # If recipe needs 5kg usable and material has 50% waste, we need to deduct 10kg
    if material and material.waste_percentage > 0:
        quantity_needed = quantity_needed / (1 - material.waste_percentage / 100)

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

            # Apply discount AND waste adjustment to cost
            # Both quantity AND price are affected by waste
            discounted_cost_per_unit = apply_supplier_discount(link.cost_per_unit, link.supplier) * material.effective_cost_multiplier

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

def calculate_packaging_stock(packaging_id):
    """
    Calculate current stock for a packaging item.
    Similar to calculate_supplier_stock but simpler (no supplier logic).
    """
    from ..models import StockLog

    # Find the last 'set' action if any
    last_set = StockLog.query.filter_by(
        packaging_id=packaging_id,
        action_type='set'
    ).order_by(StockLog.timestamp.desc()).first()

    # Start with the last set value or 0
    stock = last_set.quantity if last_set else 0

    # Add all subsequent 'add' actions (including negative for consumption)
    add_logs = StockLog.query.filter(
        StockLog.packaging_id == packaging_id,
        StockLog.action_type == 'add',
        StockLog.timestamp > (last_set.timestamp if last_set else datetime.min)
    ).all()

    for log in add_logs:
        stock += log.quantity

    return max(0, stock)  # Ensure non-negative

def deduct_packaging_stock(packaging_id, quantity_needed):
    """
    Deduct packaging stock during production using supplier strategy.
    Returns list of (supplier_id, quantity_deducted) tuples.
    Raises InsufficientStockError if not enough stock.
    """
    from ..models import db, StockLog, Packaging, PackagingSupplier, InsufficientStockError

    packaging = Packaging.query.get(packaging_id)
    if not packaging:
        from flask_babel import gettext as _
        raise InsufficientStockError(_('Packaging not found'))

    # Get supplier links for this packaging
    supplier_links = PackagingSupplier.query.filter_by(packaging_id=packaging_id).all()

    if not supplier_links:
        # Backward compatibility: no suppliers defined, use old method
        available = calculate_packaging_stock(packaging_id)
        if available < quantity_needed:
            from flask_babel import gettext as _
            raise InsufficientStockError(
                _('Insufficient packaging stock for %(name)s. Required: %(required).2f, Available: %(available).2f').replace(
                    '%(name)s', packaging.name
                ).replace(
                    '%(required).2f', f"{quantity_needed:.2f}"
                ).replace(
                    '%(available).2f', f"{available:.2f}"
                )
            )
        # Create negative stock log for consumption
        stock_log = StockLog(
            packaging_id=packaging_id,
            action_type='add',
            quantity=-quantity_needed
        )
        db.session.add(stock_log)
        return None

    # Strategy: Use primary supplier first, then others
    remaining_needed = quantity_needed
    suppliers_used = []

    # Try primary supplier first
    primary_link = None
    for link in supplier_links:
        if link.is_primary:
            primary_link = link
            break

    if primary_link:
        deducted = deduct_packaging_stock_from_supplier(
            packaging_id,
            primary_link.supplier_id,
            remaining_needed
        )
        if deducted > 0:
            suppliers_used.append((primary_link.supplier_id, deducted))
            remaining_needed -= deducted

    # If still need more, try other suppliers
    if remaining_needed > 0:
        for link in supplier_links:
            if link.is_primary:
                continue  # Already tried

            deducted = deduct_packaging_stock_from_supplier(
                packaging_id,
                link.supplier_id,
                remaining_needed
            )
            if deducted > 0:
                suppliers_used.append((link.supplier_id, deducted))
                remaining_needed -= deducted

            if remaining_needed <= 0:
                break

    # Check if we have enough stock
    if remaining_needed > 0:
        total_available = calculate_total_packaging_stock(packaging_id)
        from flask_babel import gettext as _
        raise InsufficientStockError(
            _('Insufficient packaging stock for %(name)s. Required: %(required).2f, Available: %(available).2f').replace(
                '%(name)s', packaging.name
            ).replace(
                '%(required).2f', f"{quantity_needed:.2f}"
            ).replace(
                '%(available).2f', f"{total_available:.2f}"
            )
        )

    return suppliers_used

def calculate_packaging_stock_at_date(packaging_id, cutoff_date):
    """
    Calculate packaging stock at a specific date.
    Used for reporting and historical analysis.
    """
    from ..models import StockLog

    # Find the last 'set' action before cutoff date
    last_set = StockLog.query.filter(
        StockLog.packaging_id == packaging_id,
        StockLog.action_type == 'set',
        StockLog.timestamp <= cutoff_date
    ).order_by(StockLog.timestamp.desc()).first()

    # Start with the last set value or 0
    stock = last_set.quantity if last_set else 0

    # Add all 'add' actions between last_set and cutoff_date
    query = StockLog.query.filter(
        StockLog.packaging_id == packaging_id,
        StockLog.action_type == 'add',
        StockLog.timestamp <= cutoff_date
    )

    if last_set:
        query = query.filter(StockLog.timestamp > last_set.timestamp)

    add_logs = query.all()

    for log in add_logs:
        stock += log.quantity

    return max(0, stock)  # Ensure non-negative

def calculate_packaging_supplier_stock(packaging_id, supplier_id):
    """
    Calculate current stock for a packaging item from a specific supplier.
    Similar to calculate_supplier_stock but for packaging.
    """
    from ..models import StockLog

    # Find the last 'set' action if any
    last_set = StockLog.query.filter_by(
        packaging_id=packaging_id,
        supplier_id=supplier_id,
        action_type='set'
    ).order_by(StockLog.timestamp.desc()).first()

    # Start with the last set value or 0
    stock = last_set.quantity if last_set else 0

    # Add all subsequent 'add' actions (including negative for consumption)
    add_logs = StockLog.query.filter(
        StockLog.packaging_id == packaging_id,
        StockLog.supplier_id == supplier_id,
        StockLog.action_type == 'add',
        StockLog.timestamp > (last_set.timestamp if last_set else datetime.min)
    ).all()

    for log in add_logs:
        stock += log.quantity

    return max(0, stock)  # Ensure non-negative

def calculate_total_packaging_stock(packaging_id):
    """
    Calculate total stock for packaging across all suppliers.
    """
    from ..models import PackagingSupplier

    # Get all supplier links for this packaging
    links = PackagingSupplier.query.filter_by(packaging_id=packaging_id).all()

    total = 0
    for link in links:
        total += calculate_packaging_supplier_stock(packaging_id, link.supplier_id)

    # If no suppliers, use old method (backward compatibility)
    if not links:
        return calculate_packaging_stock(packaging_id)

    return total

def deduct_packaging_stock_from_supplier(packaging_id, supplier_id, quantity_needed):
    """
    Deduct packaging stock from a specific supplier.
    Returns actual quantity deducted.
    """
    from ..models import db, StockLog

    # Check available stock from this supplier
    available = calculate_packaging_supplier_stock(packaging_id, supplier_id)

    # Deduct what we can
    quantity_to_deduct = min(available, quantity_needed)

    if quantity_to_deduct > 0:
        # Create negative stock log for consumption
        stock_log = StockLog(
            packaging_id=packaging_id,
            supplier_id=supplier_id,
            action_type='add',
            quantity=-quantity_to_deduct
        )
        db.session.add(stock_log)

    return quantity_to_deduct

def deduct_packaging_for_sales(product_id, quantity_sold):
    """
    Deduct packaging stock based on products sold.
    Called during week closing when sales are recorded.
    Returns list of (packaging_id, quantity_deducted) tuples.
    """
    from ..models import db, Product, ProductComponent

    product = Product.query.get(product_id)
    if not product:
        return []

    deductions = []

    # Get all packaging components for this product
    packaging_components = ProductComponent.query.filter_by(
        product_id=product_id,
        component_type='packaging'
    ).all()

    for component in packaging_components:
        # Calculate packaging needed for sold quantity
        # quantity_sold is in units, component.quantity is per recipe
        recipes_sold = quantity_sold / product.products_per_recipe if product.products_per_recipe > 0 else 0
        packaging_needed = component.quantity * recipes_sold

        if packaging_needed > 0:
            try:
                # Use existing deduction function
                deduct_packaging_stock(component.component_id, packaging_needed)
                deductions.append((component.component_id, packaging_needed))
            except Exception as e:
                # Log warning but don't fail the sale
                from flask_babel import gettext as _
                log_audit("WARNING", "Packaging", component.component_id,
                         f"Failed to deduct packaging during sale: {str(e)}")

    return deductions

def calculate_100g_cost(product):
    """
    Calculate cost per 100g for a product or premake.
    Takes into account loss components (negative weight).
    Returns: (cost_100g, total_cost, net_weight)

    NOTE: This function is kept for backward compatibility.
    For new code, use calculate_standard_unit_cost() which is unit-aware.
    """
    # Use the new unit-aware function internally
    cost_per_standard, total_cost, net_quantity, unit_label = calculate_standard_unit_cost(product)

    # Get the product's unit to convert appropriately
    product_unit = product.unit if hasattr(product, 'unit') else 'kg'

    # Convert to per 100g for backward compatibility
    if product_unit == 'kg':
        # cost_per_standard is per kg, convert to per 100g
        cost_100g = cost_per_standard / 10
    elif product_unit == 'g':
        # Need to check what unit cost_per_standard is in
        if 'per kg' in unit_label:
            # Convert from per kg to per 100g
            cost_100g = cost_per_standard / 10
        elif 'per 100g' in unit_label:
            # Already per 100g
            cost_100g = cost_per_standard
        else:
            # per g, convert to per 100g
            cost_100g = cost_per_standard * 100
    elif product_unit in ['L', 'ml']:
        # For liquids, return as if it were per 100ml (similar to 100g)
        if product_unit == 'L':
            cost_100g = cost_per_standard / 10  # per L to per 100ml
        else:
            if 'per L' in unit_label:
                cost_100g = cost_per_standard / 10
            elif 'per 100ml' in unit_label:
                cost_100g = cost_per_standard
            else:
                cost_100g = cost_per_standard * 100  # per ml to per 100ml
    else:
        # For other units, just return the standard cost
        cost_100g = cost_per_standard

    return cost_100g, total_cost, net_quantity

def format_quantity_with_unit(quantity, unit):
    """
    Format quantity with appropriate unit conversion for display.
    Rules:
    - Less than 1 kg/L: convert to g/ml
    - More than 1000 g/ml: convert to kg/L
    - Keep values between 1-1000 g/ml as is

    Returns: (formatted_quantity, display_unit) tuple
    """
    if quantity is None:
        return 0, unit

    # Handle weight units (kg/g)
    if unit == 'kg':
        if quantity < 1:
            # Convert to grams
            return quantity * 1000, 'g'
        else:
            # Keep as kg
            return quantity, 'kg'
    elif unit == 'g':
        if quantity >= 1000:
            # Convert to kg
            return quantity / 1000, 'kg'
        else:
            # Keep as g
            return quantity, 'g'

    # Handle liquid units (L/ml)
    elif unit == 'L':
        if quantity < 1:
            # Convert to ml
            return quantity * 1000, 'ml'
        else:
            # Keep as L
            return quantity, 'L'
    elif unit == 'ml':
        if quantity >= 1000:
            # Convert to L
            return quantity / 1000, 'L'
        else:
            # Keep as ml
            return quantity, 'ml'

    # For other units (piece, unit, etc.), keep as is
    else:
        return quantity, unit

def get_display_quantity_and_unit(quantity, unit):
    """
    Get formatted quantity and unit for display purposes.
    This is a wrapper function that returns the display values.
    """
    return format_quantity_with_unit(quantity, unit)

def get_appropriate_price_unit(unit, batch_size=None):
    """
    Determine the appropriate unit for price display based on the product's unit and batch size.

    Args:
        unit: The base unit of the product ('kg', 'g', 'L', 'ml', 'piece', 'unit', etc.)
        batch_size: Optional batch size to help determine appropriate scale

    Returns:
        A tuple of (display_unit, unit_label) e.g., ('kg', 'per kg')
    """
    from flask_babel import gettext as _

    if unit == 'kg':
        return 'kg', _('per kg')
    elif unit == 'g':
        # For gram-based items, decide based on batch size
        if batch_size and batch_size >= 1000:
            return 'kg', _('per kg')
        else:
            return '100g', _('per 100g')
    elif unit == 'L':
        return 'L', _('per L')
    elif unit == 'ml':
        # For ml-based items, decide based on batch size
        if batch_size and batch_size >= 1000:
            return 'L', _('per L')
        else:
            return '100ml', _('per 100ml')
    elif unit in ['piece', 'unit', 'יחידה', 'units']:
        return 'unit', _('per unit')
    else:
        # Default case for any other unit
        return unit, f"{_('per')} {unit}"

def calculate_unit_price(product, display_unit=None):
    """
    Calculate the price for a product based on the appropriate display unit.

    Args:
        product: The product/premake object
        display_unit: Optional specific display unit to use (e.g., 'kg', '100g', 'L', '100ml', 'unit')
                     If not provided, will determine automatically

    Returns:
        A tuple of (price, unit_label) e.g., (12.50, 'per kg')
    """
    from flask_babel import gettext as _

    # Calculate base cost per unit
    total_cost = calculate_prime_cost(product) if hasattr(product, 'components') else 0

    # For products with batch size
    if hasattr(product, 'products_per_recipe') and product.products_per_recipe:
        cost_per_unit = total_cost / product.products_per_recipe
    # For premakes with batch size
    elif hasattr(product, 'batch_size') and product.batch_size:
        cost_per_unit = total_cost / product.batch_size
    else:
        cost_per_unit = total_cost

    # Determine display unit if not provided
    if not display_unit:
        display_unit, unit_label = get_appropriate_price_unit(
            product.unit if hasattr(product, 'unit') else 'kg',
            product.batch_size if hasattr(product, 'batch_size') else None
        )
    else:
        # Create unit label for provided display unit
        if display_unit == '100g':
            unit_label = _('per 100g')
        elif display_unit == '100ml':
            unit_label = _('per 100ml')
        elif display_unit == 'kg':
            unit_label = _('per kg')
        elif display_unit == 'L':
            unit_label = _('per L')
        elif display_unit == 'unit':
            unit_label = _('per unit')
        else:
            unit_label = f"{_('per')} {display_unit}"

    # Get the product's base unit
    product_unit = product.unit if hasattr(product, 'unit') else 'kg'

    # Convert price based on display unit
    if product_unit == 'kg':
        if display_unit == '100g':
            return cost_per_unit / 10, unit_label  # 1kg = 1000g, so per 100g = per kg / 10
        elif display_unit == 'g':
            return cost_per_unit / 1000, unit_label
        else:
            return cost_per_unit, unit_label
    elif product_unit == 'g':
        if display_unit == 'kg':
            return cost_per_unit * 1000, unit_label
        elif display_unit == '100g':
            return cost_per_unit * 100, unit_label
        else:
            return cost_per_unit, unit_label
    elif product_unit == 'L':
        if display_unit == '100ml':
            return cost_per_unit / 10, unit_label  # 1L = 1000ml, so per 100ml = per L / 10
        elif display_unit == 'ml':
            return cost_per_unit / 1000, unit_label
        else:
            return cost_per_unit, unit_label
    elif product_unit == 'ml':
        if display_unit == 'L':
            return cost_per_unit * 1000, unit_label
        elif display_unit == '100ml':
            return cost_per_unit * 100, unit_label
        else:
            return cost_per_unit, unit_label
    else:
        # For piece, unit, or other units, no conversion needed
        return cost_per_unit, unit_label

def calculate_standard_unit_cost(product):
    """
    Calculate cost per standard unit for a product or premake.
    This is a unit-aware version of the old calculate_100g_cost function.
    Takes into account loss components (negative weight) and uses appropriate units.

    Returns: (cost_per_standard_unit, total_cost, net_quantity, unit_label)
    """
    from flask_babel import gettext as _

    total_cost = 0
    total_quantity = 0
    loss_quantity = 0

    # Get the product's unit
    product_unit = product.unit if hasattr(product, 'unit') else 'kg'

    for component in product.components:
        if component.component_type == 'loss':
            # Loss has negative quantity
            loss_quantity += component.quantity  # This is negative
            continue

        # Add quantity
        if component.component_type in ['raw_material', 'premake', 'product']:
            total_quantity += abs(component.quantity)

        # Calculate cost for this component
        if component.component_type == 'raw_material':
            if component.material:
                if component.material.is_unlimited:
                    comp_cost = 0
                else:
                    # Get primary supplier discounted price (WITHOUT waste adjustment)
                    price = get_primary_supplier_discounted_price(component.material)
                    # Apply waste to QUANTITY, not price!
                    actual_quantity_needed = component.quantity * component.material.effective_cost_multiplier
                    comp_cost = actual_quantity_needed * price
            else:
                comp_cost = 0
        elif component.component_type == 'premake':
            if component.premake:
                # Recursive calculation for premakes
                premake_cost_per_unit = calculate_premake_cost_per_unit(component.premake, use_actual_costs=False)
                comp_cost = component.quantity * premake_cost_per_unit
            else:
                comp_cost = 0
        elif component.component_type == 'product':
            if component.product:
                # For preproducts, use their cost
                product_cost = calculate_prime_cost(component.product)
                if component.product.products_per_recipe:
                    cost_per_unit = product_cost / component.product.products_per_recipe
                else:
                    cost_per_unit = product_cost
                comp_cost = component.quantity * cost_per_unit
            else:
                comp_cost = 0
        elif component.component_type == 'packaging':
            if component.packaging:
                comp_cost = component.quantity * component.packaging.price_per_unit
            else:
                comp_cost = 0
        else:
            comp_cost = 0

        total_cost += comp_cost

    # Calculate net quantity (after loss)
    net_quantity = total_quantity + loss_quantity  # loss_quantity is negative

    # Determine appropriate standard unit
    display_unit, unit_label = get_appropriate_price_unit(product_unit, net_quantity)

    # Calculate cost per standard unit
    if net_quantity > 0:
        # Convert to standard unit for calculation
        if product_unit == 'kg':
            if display_unit == '100g':
                cost_per_standard = (total_cost / net_quantity) / 10  # per 100g
            else:
                cost_per_standard = total_cost / net_quantity  # per kg
        elif product_unit == 'g':
            if display_unit == 'kg':
                cost_per_standard = (total_cost / net_quantity) * 1000  # per kg
            elif display_unit == '100g':
                cost_per_standard = (total_cost / net_quantity) * 100  # per 100g
            else:
                cost_per_standard = total_cost / net_quantity  # per g
        elif product_unit == 'L':
            if display_unit == '100ml':
                cost_per_standard = (total_cost / net_quantity) / 10  # per 100ml
            else:
                cost_per_standard = total_cost / net_quantity  # per L
        elif product_unit == 'ml':
            if display_unit == 'L':
                cost_per_standard = (total_cost / net_quantity) * 1000  # per L
            elif display_unit == '100ml':
                cost_per_standard = (total_cost / net_quantity) * 100  # per 100ml
            else:
                cost_per_standard = total_cost / net_quantity  # per ml
        else:
            cost_per_standard = total_cost / net_quantity if net_quantity else 0
    else:
        cost_per_standard = 0

    return cost_per_standard, total_cost, net_quantity, unit_label

def safe_float(value):
    """Convert infinity to None for JSON serialization"""
    import math
    if math.isinf(value):
        return None
    return value

def convert_cost_to_display_unit(cost, unit):
    """Convert cost from native unit to cost per 100g/100ml for display

    Args:
        cost: The cost value to convert
        unit: The unit of the material (kg, g, L, ml, etc.)

    Returns:
        The cost converted to per 100g/100ml for weight/volume units,
        or unchanged for other units (pieces, etc.)
    """
    if unit == 'kg' or unit == 'L':
        return cost / 10  # 1kg = 1000g, so per 100g = per kg / 10
    elif unit == 'g' or unit == 'ml':
        return cost * 100  # Convert from per g to per 100g
    else:
        return cost  # For other units (pieces, etc.), keep as is

def calculate_consumption_breakdown(supplier_links, material, remaining_to_consume, needed_quantity):
    """Calculate consumption breakdown for material across suppliers

    Args:
        supplier_links: List of supplier links for the material
        material: The RawMaterial object
        remaining_to_consume: Amount of material needed
        needed_quantity: Total quantity needed (for deficit detection)

    Returns:
        Tuple of (consumption_breakdown list, remaining_to_consume)
    """
    import math
    consumption_breakdown = []

    for link in supplier_links:
        supplier_stock = calculate_supplier_stock(material.id, link.supplier_id)

        if remaining_to_consume > 0 and supplier_stock > 0:
            if math.isinf(supplier_stock):
                amount_to_consume = remaining_to_consume
            else:
                amount_to_consume = min(supplier_stock, remaining_to_consume)
            remaining_to_consume -= amount_to_consume

            # Apply supplier discount to the cost
            discounted_cost = apply_supplier_discount(link.cost_per_unit, link.supplier)

            # Convert to cost per 100g for display
            cost_per_100g = convert_cost_to_display_unit(discounted_cost, material.unit)
            original_cost_per_100g = convert_cost_to_display_unit(link.cost_per_unit, material.unit)

            consumption_breakdown.append({
                'supplier_id': link.supplier_id,
                'supplier_name': link.supplier.name,
                'is_primary': link.is_primary,
                'stock_available': safe_float(supplier_stock),
                'amount_to_consume': amount_to_consume,
                'remaining_after': safe_float(supplier_stock - amount_to_consume),
                'cost_per_unit': cost_per_100g,  # Now this is cost per 100g for display
                'original_cost': original_cost_per_100g,  # Original price per 100g for comparison
                'discounted_cost_per_unit': discounted_cost,  # Keep original for total calculation
                'total_cost': amount_to_consume * discounted_cost,  # Use original discounted_cost for total
                'is_deficit': False
            })
        elif link.is_primary and supplier_stock == 0 and remaining_to_consume == needed_quantity:
            # Primary has no stock and nothing consumed yet - include for display
            # Apply supplier discount to the cost
            discounted_cost = apply_supplier_discount(link.cost_per_unit, link.supplier)

            # Convert to cost per 100g for display
            cost_per_100g = convert_cost_to_display_unit(discounted_cost, material.unit)
            original_cost_per_100g = convert_cost_to_display_unit(link.cost_per_unit, material.unit)

            consumption_breakdown.append({
                'supplier_id': link.supplier_id,
                'supplier_name': link.supplier.name,
                'is_primary': True,
                'stock_available': 0,
                'amount_to_consume': 0,
                'remaining_after': 0,
                'cost_per_unit': cost_per_100g,  # Now this is cost per 100g for display
                'original_cost': original_cost_per_100g,  # Original price per 100g for comparison
                'discounted_cost_per_unit': discounted_cost,  # Keep original for total calculation
                'total_cost': 0,
                'is_deficit': False
            })

    return consumption_breakdown, remaining_to_consume


def check_item_stock_availability(item, quantity=1):
    """
    Check if a product/premake has sufficient stock for production.

    Args:
        item: Product object (can be product or premake)
        quantity: Number of batches to produce

    Returns:
        dict: {
            'has_stock': bool,
            'missing_components': [{'name': str, 'type': str, 'required': float, 'available': float}]
        }
    """
    missing_components = []

    try:
        for component in item.components:
            if component.component_type == 'raw_material':
                material = component.material
                if material and not material.is_unlimited:
                    # Apply waste adjustment to required quantity
                    required_qty = component.quantity * quantity * material.effective_cost_multiplier
                    available = calculate_total_material_stock(material.id)
                    if available < required_qty:
                        missing_components.append({
                            'name': material.name,
                            'type': 'raw_material',
                            'required': required_qty,
                            'available': available
                        })

            elif component.component_type == 'premake':
                premake = component.premake
                if premake:
                    required_qty = component.quantity * quantity
                    available = calculate_premake_current_stock(premake.id)
                    if available < required_qty:
                        missing_components.append({
                            'name': premake.name,
                            'type': 'premake',
                            'required': required_qty,
                            'available': available
                        })

            elif component.component_type == 'product':
                preproduct = component.preproduct
                if preproduct:
                    required_qty = component.quantity * quantity
                    available = calculate_premake_current_stock(preproduct.id)
                    if available < required_qty:
                        missing_components.append({
                            'name': preproduct.name,
                            'type': 'preproduct',
                            'required': required_qty,
                            'available': available
                        })

        return {
            'has_stock': len(missing_components) == 0,
            'missing_components': missing_components
        }
    except Exception:
        # Conservative: if check fails, assume insufficient stock
        return {
            'has_stock': False,
            'missing_components': [{'name': 'Unknown', 'type': 'error', 'required': 0, 'available': 0}]
        }


def group_items_by_category(items, item_type='product'):
    """
    Group products/premakes by category for display.

    Args:
        items: List of Product objects
        item_type: 'product' or 'premake' for category type lookup

    Returns:
        list: [{
            'id': category_id,
            'name': category_name,
            'items': [{'id', 'name', 'has_stock', 'missing_components', 'unit', 'batch_size', ...}]
        }]
    """
    from ..models import Category

    # Get categories of the appropriate type
    category_type = 'premake' if item_type == 'premake' else 'product'
    categories = Category.query.filter_by(type=category_type).order_by(Category.name).all()

    # Build category ID to category mapping
    category_map = {cat.id: {'id': cat.id, 'name': cat.name, 'items': []} for cat in categories}

    # Add uncategorized bucket
    uncategorized = {'id': None, 'name': 'ללא קטגוריה', 'items': []}

    # Group items
    for item in items:
        stock_info = check_item_stock_availability(item, quantity=1)

        item_data = {
            'id': item.id,
            'name': item.name,
            'has_stock': stock_info['has_stock'],
            'missing_components': stock_info['missing_components'],
            'unit': item.unit if hasattr(item, 'unit') and item.unit else 'kg',
            'batch_size': item.batch_size if hasattr(item, 'batch_size') and item.batch_size else None,
            'products_per_recipe': item.products_per_recipe if hasattr(item, 'products_per_recipe') else 1
        }

        if item.category_id and item.category_id in category_map:
            category_map[item.category_id]['items'].append(item_data)
        else:
            uncategorized['items'].append(item_data)

    # Build result list (only categories with items)
    result = []
    for cat_id in category_map:
        if category_map[cat_id]['items']:
            # Sort items within category by name
            category_map[cat_id]['items'].sort(key=lambda x: x['name'])
            result.append(category_map[cat_id])

    # Add uncategorized if it has items
    if uncategorized['items']:
        uncategorized['items'].sort(key=lambda x: x['name'])
        result.append(uncategorized)

    # Sort categories by name
    result.sort(key=lambda x: x['name'])

    return result
