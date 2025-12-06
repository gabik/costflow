from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from ..models import db, RawMaterial, StockLog, ProductComponent, StockAudit, Category, Product, ProductionLog, Supplier, RawMaterialSupplier
from .utils import log_audit, get_or_create_general_category, units_list, calculate_supplier_stock, calculate_total_material_stock

raw_materials_blueprint = Blueprint('raw_materials', __name__)

# ----------------------------
# Raw Materials Management
# ----------------------------
@raw_materials_blueprint.route('/raw_materials')
def raw_materials():
    materials = RawMaterial.query.all()

    for material in materials:
        # Use the function that sums all supplier stocks
        material.current_stock = calculate_total_material_stock(material.id)

        # Skip supplier processing for unlimited materials
        if material.is_unlimited:
            material.supplier_count = 0
            material.stock_breakdown = []
            material.primary_supplier = None
            continue

        # Get supplier information for this material
        supplier_links = RawMaterialSupplier.query.filter_by(raw_material_id=material.id).all()
        material.supplier_count = len(supplier_links)

        # Get per-supplier stock breakdown
        stock_breakdown = []
        for link in supplier_links:
            stock = calculate_supplier_stock(material.id, link.supplier_id)
            if stock > 0 or link.is_primary:  # Include primary even if 0 stock
                stock_breakdown.append({
                    'supplier_name': link.supplier.name,
                    'stock': stock,
                    'is_primary': link.is_primary,
                    'cost_per_unit': link.cost_per_unit
                })

        # Sort by primary first, then by stock amount
        stock_breakdown.sort(key=lambda x: (-x['is_primary'], -x['stock']))
        material.stock_breakdown = stock_breakdown

        # Get primary supplier or first supplier
        primary_supplier = None
        for link in supplier_links:
            if link.is_primary:
                primary_supplier = link.supplier
                break

        # If no primary, get the first supplier
        if not primary_supplier and supplier_links:
            primary_supplier = supplier_links[0].supplier

        material.primary_supplier = primary_supplier

    return render_template('raw_materials.html', materials=materials)

@raw_materials_blueprint.route('/raw_materials/add', methods=['GET', 'POST'])
def add_raw_material():
    if request.method == 'POST':
        name = request.form['name']
        category_id = request.form.get('category') # form field is 'category'
        if not category_id:
            category_id = get_or_create_general_category('raw_material')

        unit = request.form['unit']
        stock = request.form.get('stock', 0) # Optional initial stock
        is_unlimited = request.form.get('is_unlimited') == 'on'  # Checkbox value

        # Get multiple suppliers
        supplier_ids = request.form.getlist('supplier_ids[]')
        supplier_costs = request.form.getlist('supplier_costs[]')
        primary_supplier_value = request.form.get('primary_supplier')

        # For unlimited materials, cost should be 0 (or user-specified)
        if is_unlimited:
            cost_per_unit = 0
        else:
            # Calculate average cost from suppliers for backward compatibility
            valid_costs = []
            for i, supplier_id in enumerate(supplier_ids):
                if supplier_id and i < len(supplier_costs) and supplier_costs[i]:
                    try:
                        valid_costs.append(float(supplier_costs[i]))
                    except ValueError:
                        pass

            cost_per_unit = sum(valid_costs) / len(valid_costs) if valid_costs else 0

        category = Category.query.get(category_id)

        new_material = RawMaterial(name=name, category=category, unit=unit, cost_per_unit=cost_per_unit, is_unlimited=is_unlimited)
        db.session.add(new_material)
        db.session.flush() # Get ID for stock log

        # Skip supplier and stock setup for unlimited materials
        if not is_unlimited:
            # Add supplier links
            primary_supplier_id = None
            supplier_count = 0
            for i, supplier_id in enumerate(supplier_ids):
                if supplier_id:  # Skip empty selections
                    supplier_count += 1
                    # Check if this is the primary supplier (radio value matches index+1)
                    is_primary = (str(i+1) == primary_supplier_value) if primary_supplier_value else (i == 0)

                    # Use the supplier cost or fallback to average
                    try:
                        supplier_cost = float(supplier_costs[i]) if supplier_costs[i] else cost_per_unit
                    except (ValueError, IndexError):
                        supplier_cost = cost_per_unit

                    supplier_link = RawMaterialSupplier(
                        raw_material_id=new_material.id,
                        supplier_id=int(supplier_id),
                        cost_per_unit=supplier_cost,
                        is_primary=is_primary
                    )
                    db.session.add(supplier_link)

                    # Track primary supplier for stock
                    if is_primary:
                        primary_supplier_id = int(supplier_id)

            # If no suppliers provided, use default supplier (ID=1)
            if not primary_supplier_id:
                default_supplier = Supplier.query.filter_by(id=1).first()
                if default_supplier:
                    supplier_link = RawMaterialSupplier(
                        raw_material_id=new_material.id,
                        supplier_id=1,
                        cost_per_unit=cost_per_unit,
                        is_primary=True
                    )
                    db.session.add(supplier_link)
                    primary_supplier_id = 1
                    supplier_count = 1

            # Add initial stock for primary supplier
            if stock and primary_supplier_id:
                initial_stock_log = StockLog(
                    raw_material_id=new_material.id,
                    supplier_id=primary_supplier_id,
                    action_type='set',
                    quantity=float(stock)
                )
                db.session.add(initial_stock_log)

        db.session.commit()

        # Handle modal submissions
        if request.referrer and 'products/add' in request.referrer:
            return redirect(request.referrer)

        return redirect(url_for('raw_materials.raw_materials'))

    # GET request - load form
    categories = Category.query.filter_by(type='raw_material').all()
    suppliers = Supplier.query.filter_by(is_active=True).all()
    return render_template('add_or_edit_raw_material.html',
                         material=None,
                         categories=categories,
                         suppliers=suppliers,
                         units=units_list)

@raw_materials_blueprint.route('/raw_materials/edit/<int:material_id>', methods=['GET', 'POST'])
def edit_raw_material(material_id):
    material = RawMaterial.query.get_or_404(material_id)

    if request.method == 'POST':
        material.name = request.form['name']
        category_id = request.form.get('category')
        if not category_id:
            category_id = get_or_create_general_category('raw_material')

        category = Category.query.get(category_id)
        material.category = category
        material.unit = request.form['unit']
        is_unlimited = request.form.get('is_unlimited') == 'on'  # Checkbox value
        material.is_unlimited = is_unlimited

        # Skip supplier handling for unlimited materials
        if is_unlimited:
            # Remove all existing supplier links if switching to unlimited
            RawMaterialSupplier.query.filter_by(raw_material_id=material.id).delete()
            material.cost_per_unit = 0
            db.session.commit()
            return redirect(url_for('raw_materials.raw_materials'))

        # Get existing suppliers before deletion to check for stock
        existing_suppliers = RawMaterialSupplier.query.filter_by(raw_material_id=material.id).all()
        existing_supplier_ids = {link.supplier_id for link in existing_suppliers}

        # Get new suppliers from form
        new_supplier_ids = set()
        supplier_ids = request.form.getlist('supplier_ids[]')
        for supplier_id in supplier_ids:
            if supplier_id:
                new_supplier_ids.add(int(supplier_id))

        # Find removed suppliers
        removed_supplier_ids = existing_supplier_ids - new_supplier_ids

        # Handle stock transfer for removed suppliers
        stock_handling = request.form.get('stock_handling', 'keep')  # 'transfer', 'waste', or 'keep'
        transfer_to_supplier = request.form.get('transfer_to_supplier')

        for removed_id in removed_supplier_ids:
            stock = calculate_supplier_stock(material.id, removed_id)
            if stock > 0:
                if stock_handling == 'transfer' and transfer_to_supplier:
                    # Transfer stock to another supplier
                    # Create a negative log for the removed supplier
                    negative_log = StockLog(
                        raw_material_id=material.id,
                        supplier_id=removed_id,
                        action_type='add',
                        quantity=-stock
                    )
                    db.session.add(negative_log)

                    # Create a positive log for the target supplier
                    positive_log = StockLog(
                        raw_material_id=material.id,
                        supplier_id=int(transfer_to_supplier),
                        action_type='add',
                        quantity=stock
                    )
                    db.session.add(positive_log)

                    log_audit("STOCK_TRANSFER", "RawMaterial", material.id,
                             f"Transferred {stock} {material.unit} from supplier {removed_id} to {transfer_to_supplier}")

                elif stock_handling == 'waste':
                    # Mark stock as waste
                    waste_log = StockLog(
                        raw_material_id=material.id,
                        supplier_id=removed_id,
                        action_type='add',
                        quantity=-stock
                    )
                    db.session.add(waste_log)

                    log_audit("STOCK_WASTE", "RawMaterial", material.id,
                             f"Marked {stock} {material.unit} from supplier {removed_id} as waste")

        # Now clear existing supplier links
        RawMaterialSupplier.query.filter_by(raw_material_id=material.id).delete()

        # Get multiple suppliers
        supplier_ids = request.form.getlist('supplier_ids[]')
        supplier_costs = request.form.getlist('supplier_costs[]')
        primary_supplier_value = request.form.get('primary_supplier')

        # Calculate average cost from suppliers for backward compatibility
        valid_costs = []
        for i, supplier_id in enumerate(supplier_ids):
            if supplier_id and i < len(supplier_costs) and supplier_costs[i]:
                try:
                    valid_costs.append(float(supplier_costs[i]))
                except ValueError:
                    pass

        material.cost_per_unit = sum(valid_costs) / len(valid_costs) if valid_costs else material.cost_per_unit

        # Add new supplier links
        supplier_count = 0
        for i, supplier_id in enumerate(supplier_ids):
            if supplier_id:  # Skip empty selections
                supplier_count += 1
                # Check if this is the primary supplier (radio value matches index+1)
                is_primary = (str(i+1) == primary_supplier_value) if primary_supplier_value else (i == 0)

                # Use the supplier cost or fallback to average
                try:
                    supplier_cost = float(supplier_costs[i]) if supplier_costs[i] else material.cost_per_unit
                except (ValueError, IndexError):
                    supplier_cost = material.cost_per_unit

                supplier_link = RawMaterialSupplier(
                    raw_material_id=material.id,
                    supplier_id=int(supplier_id),
                    cost_per_unit=supplier_cost,
                    is_primary=is_primary
                )
                db.session.add(supplier_link)

        # If no suppliers provided, use default supplier (ID=1)
        if supplier_count == 0:
            default_supplier = Supplier.query.filter_by(id=1).first()
            if default_supplier:
                supplier_link = RawMaterialSupplier(
                    raw_material_id=material.id,
                    supplier_id=1,
                    cost_per_unit=material.cost_per_unit,
                    is_primary=True
                )
                db.session.add(supplier_link)

        db.session.commit()
        return redirect(url_for('raw_materials.raw_materials'))

    # GET request - prepare data
    categories = Category.query.filter_by(type='raw_material').all()
    suppliers = Supplier.query.filter_by(is_active=True).all()

    # Load supplier links for the material with stock info
    material.supplier_links = RawMaterialSupplier.query.filter_by(
        raw_material_id=material.id
    ).all()

    # Add current stock for each supplier
    for link in material.supplier_links:
        link.current_stock = calculate_supplier_stock(material.id, link.supplier_id)

    # Also set primary_supplier for backward compatibility
    primary_link = next((link for link in material.supplier_links if link.is_primary), None)
    if primary_link:
        material.primary_supplier = primary_link.supplier
    else:
        material.primary_supplier = None

    return render_template('add_or_edit_raw_material.html',
                         material=material,
                         categories=categories,
                         suppliers=suppliers,
                         units=units_list)

@raw_materials_blueprint.route('/raw_materials/delete/<int:material_id>', methods=['POST'])
def delete_raw_material(material_id):
    material = RawMaterial.query.get_or_404(material_id)

    # Delete related StockLogs
    StockLog.query.filter_by(raw_material_id=material.id).delete()

    # Delete related ProductComponents
    ProductComponent.query.filter_by(component_id=material.id, component_type='raw_material').delete()

    db.session.delete(material)
    log_audit("DELETE", "RawMaterial", material_id, f"Deleted raw material {material.name}")
    db.session.commit()
    return redirect(url_for('raw_materials.raw_materials'))

@raw_materials_blueprint.route('/raw_materials/update_stock', methods=['POST'])
def update_stock():
    raw_material_id = request.form['raw_material_id']
    quantity = float(request.form['quantity'])
    action_type = request.form['action_type']  # 'add' or 'set'
    supplier_id = request.form.get('supplier_id')  # Get supplier_id
    auditor_name = request.form.get('auditor_name', '')  # Get auditor name if provided

    if action_type not in ['add', 'set']:
        return "Invalid action type", 400

    # Validate supplier belongs to this material
    if supplier_id:
        supplier_link = RawMaterialSupplier.query.filter_by(
            raw_material_id=raw_material_id,
            supplier_id=supplier_id
        ).first()
        if not supplier_link:
            return "Invalid supplier for this material", 400
    else:
        # If no supplier specified, use primary
        supplier_link = RawMaterialSupplier.query.filter_by(
            raw_material_id=raw_material_id,
            is_primary=True
        ).first()
        if supplier_link:
            supplier_id = supplier_link.supplier_id

    material = RawMaterial.query.get(raw_material_id)

    # If action_type is 'set', calculate current stock and create audit record
    if action_type == 'set':
        # Calculate current stock FOR THIS SUPPLIER
        system_stock = calculate_supplier_stock(raw_material_id, supplier_id) if supplier_id else 0

        # Calculate variance and create audit record
        variance = quantity - system_stock
        variance_cost = variance * material.cost_per_unit

        # Create the stock log entry with supplier_id
        stock_log = StockLog(
            raw_material_id=raw_material_id,
            supplier_id=supplier_id,  # Add supplier_id
            action_type=action_type,
            quantity=quantity
        )
        db.session.add(stock_log)
        db.session.flush()  # Flush to get the stock_log.id

        # Create stock audit record
        stock_audit = StockAudit(
            raw_material_id=raw_material_id,
            system_quantity=system_stock,
            physical_quantity=quantity,
            variance=variance,
            variance_cost=variance_cost,
            auditor_name=auditor_name,
            stock_log_id=stock_log.id
        )
        db.session.add(stock_audit)

        log_audit("STOCK_AUDIT", "RawMaterial", raw_material_id,
                 f"Supplier: {supplier_id}, Physical count: {quantity}, System: {system_stock:.2f}, Variance: {variance:.2f} (Cost: {variance_cost:.2f})")
    else:
        # For 'add' action, just create the stock log with supplier_id
        stock_log = StockLog(
            raw_material_id=raw_material_id,
            supplier_id=supplier_id,  # Add supplier_id
            action_type=action_type,
            quantity=quantity
        )
        db.session.add(stock_log)
        log_audit("UPDATE_STOCK", "RawMaterial", raw_material_id, f"Supplier: {supplier_id}, {action_type} {quantity}")

    db.session.commit()
    return redirect(url_for('raw_materials.raw_materials'))

@raw_materials_blueprint.route('/api/material/<int:material_id>/suppliers')
def get_material_suppliers(material_id):
    """API endpoint to get suppliers for a specific material."""
    supplier_links = RawMaterialSupplier.query.filter_by(
        raw_material_id=material_id
    ).all()

    suppliers_data = []
    for link in supplier_links:
        stock = calculate_supplier_stock(material_id, link.supplier_id)
        suppliers_data.append({
            'supplier_id': link.supplier_id,
            'supplier_name': link.supplier.name,
            'cost_per_unit': link.cost_per_unit,
            'is_primary': link.is_primary,
            'current_stock': stock
        })

    return jsonify({'suppliers': suppliers_data})

def calculate_raw_material_current_stock(material_id):
    """
    Calculates the current stock of a given raw material based on StockLogs and ProductionLogs.
    """
    last_set_log = StockLog.query.filter_by(raw_material_id=material_id, action_type='set') \
        .order_by(StockLog.timestamp.desc()).first()
    stock = last_set_log.quantity if last_set_log else 0

    add_logs = StockLog.query.filter(
        StockLog.raw_material_id == material_id,
        StockLog.action_type == 'add',
        StockLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
    ).all()
    for log in add_logs:
        stock += log.quantity

    # Subtract raw materials used in produced products (both final products and premakes)
    # Note: This is an expensive operation if not optimized (e.g., pre-calculated sums)
    # For real-time stock calculation, it means iterating a lot of history.
    # Assumes ProductionLogs timestamp is after initial stock or last set.
    # We need to consider *all* production up to current point.
    # Let's just iterate over relevant production logs, but this can be slow.

    # Filter production logs by material usage
    production_logs_raw_material_usage = db.session.query(ProductionLog, ProductComponent).\
        join(ProductComponent, ProductionLog.product_id == ProductComponent.product_id).\
        filter(
            ProductComponent.component_type == 'raw_material',
            ProductComponent.component_id == material_id,
            ProductionLog.timestamp > (last_set_log.timestamp if last_set_log else datetime.min)
        ).all()

    for production_log, product_component in production_logs_raw_material_usage:
        stock -= product_component.quantity * production_log.quantity_produced

    return stock