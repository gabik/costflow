from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, jsonify, flash
from sqlalchemy.orm import joinedload, subqueryload
from ..models import db, RawMaterial, RawMaterialAlternativeName, StockLog, ProductComponent, StockAudit, Category, Product, ProductionLog, Supplier, RawMaterialSupplier
from .utils import log_audit, get_or_create_general_category, units_list, calculate_supplier_stock, calculate_total_material_stock, apply_supplier_discount

raw_materials_blueprint = Blueprint('raw_materials', __name__)

# ----------------------------
# Helper Functions
# ----------------------------
def validate_alternative_name_uniqueness(name, exclude_material_id=None):
    """
    Check if alternative name already exists for another material.
    Returns: (is_unique, existing_material_name)
    """
    if not name or not name.strip():
        return True, None

    name = name.strip()
    existing = RawMaterialAlternativeName.query.filter_by(alternative_name=name).first()

    if existing and existing.raw_material_id != exclude_material_id:
        return False, existing.raw_material.name
    return True, None

def calculate_all_material_stocks(material_ids):
    """
    Bulk calculate stock for all materials in 2 queries instead of O(N×M).
    Returns: {material_id: {'total': X, 'suppliers': {supplier_id: stock}}}
    """
    if not material_ids:
        return {}

    # Query 1: Get all 'set' logs for these materials (ordered by timestamp desc)
    all_set_logs = StockLog.query.filter(
        StockLog.raw_material_id.in_(material_ids),
        StockLog.action_type == 'set'
    ).order_by(StockLog.timestamp.desc()).all()

    # Query 2: Get all 'add' logs for these materials
    all_add_logs = StockLog.query.filter(
        StockLog.raw_material_id.in_(material_ids),
        StockLog.action_type == 'add'
    ).all()

    # Build lookup: (material_id, supplier_id) -> last_set_log (most recent)
    last_sets = {}
    for log in all_set_logs:
        key = (log.raw_material_id, log.supplier_id)
        if key not in last_sets:  # Keep only the most recent (first due to desc order)
            last_sets[key] = log

    # Build result structure
    result = {mid: {'total': 0, 'suppliers': {}} for mid in material_ids}

    # Process set logs - initialize stock from last set
    for key, log in last_sets.items():
        material_id, supplier_id = key
        if material_id in result:
            result[material_id]['suppliers'][supplier_id] = {
                'stock': log.quantity,
                'last_set_time': log.timestamp
            }

    # Process add logs (only those after corresponding set)
    for log in all_add_logs:
        material_id = log.raw_material_id
        supplier_id = log.supplier_id
        if material_id not in result:
            continue

        key = (material_id, supplier_id)
        if key in last_sets:
            last_set_time = last_sets[key].timestamp
            if log.timestamp > last_set_time:
                if supplier_id in result[material_id]['suppliers']:
                    result[material_id]['suppliers'][supplier_id]['stock'] += log.quantity
        else:
            # No set log for this supplier - add creates initial stock
            if supplier_id not in result[material_id]['suppliers']:
                result[material_id]['suppliers'][supplier_id] = {'stock': 0, 'last_set_time': datetime.min}
            result[material_id]['suppliers'][supplier_id]['stock'] += log.quantity

    # Calculate totals and ensure non-negative values
    for material_id in result:
        total = sum(s['stock'] for s in result[material_id]['suppliers'].values())
        result[material_id]['total'] = max(0, total)
        # Ensure non-negative per-supplier
        for sid in result[material_id]['suppliers']:
            result[material_id]['suppliers'][sid]['stock'] = max(0, result[material_id]['suppliers'][sid]['stock'])

    return result

# ----------------------------
# Raw Materials Management
# ----------------------------
@raw_materials_blueprint.route('/raw_materials')
def raw_materials():
    # Single query with eager loading - eliminates N+1 queries
    materials = RawMaterial.query.filter_by(is_deleted=False).options(
        joinedload(RawMaterial.category),
        subqueryload(RawMaterial.supplier_links).joinedload(RawMaterialSupplier.supplier)
    ).all()

    # Bulk calculate stock for all non-unlimited materials (2 queries total)
    material_ids = [m.id for m in materials if not m.is_unlimited]
    stock_data = calculate_all_material_stocks(material_ids)

    for material in materials:
        # Handle unlimited materials
        if material.is_unlimited:
            material.current_stock = float('inf')
            material.supplier_count = 0
            material.stock_breakdown = []
            material.primary_supplier = None
            continue

        # Get pre-calculated stock data
        mat_stock = stock_data.get(material.id, {'total': 0, 'suppliers': {}})
        material.current_stock = mat_stock['total']

        # Supplier info already eager-loaded
        supplier_links = material.supplier_links
        material.supplier_count = len(supplier_links)

        # Build stock breakdown from pre-fetched data
        stock_breakdown = []
        primary_supplier = None

        for link in supplier_links:
            supplier_stock = mat_stock['suppliers'].get(link.supplier_id, {}).get('stock', 0)
            discounted_price = apply_supplier_discount(link.cost_per_unit, link.supplier)

            stock_breakdown.append({
                'supplier_name': link.supplier.name,
                'stock': supplier_stock,
                'is_primary': link.is_primary,
                'cost_per_unit': link.cost_per_unit,
                'discounted_cost_per_unit': discounted_price,
                'discount_percentage': link.supplier.discount_percentage
            })

            if link.is_primary:
                primary_supplier = link.supplier

        # Sort by primary first, then by stock amount
        stock_breakdown.sort(key=lambda x: (-x['is_primary'], -x['stock']))
        material.stock_breakdown = stock_breakdown

        # Fallback to first supplier if no primary
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

        # Get waste percentage
        has_waste = request.form.get('has_waste') == 'on'
        waste_percentage = 0.0
        if has_waste and not is_unlimited:
            try:
                waste_percentage = float(request.form.get('waste_percentage', 0))
                waste_percentage = max(0, min(99, waste_percentage))  # Clamp to 0-99
            except (ValueError, TypeError):
                waste_percentage = 0.0

        # Get multiple suppliers
        supplier_ids = request.form.getlist('supplier_ids[]')
        supplier_costs = request.form.getlist('supplier_costs[]')
        supplier_skus = request.form.getlist('supplier_skus[]')
        supplier_upps = request.form.getlist('supplier_upps[]')
        primary_supplier_value = request.form.get('primary_supplier')

        # Get alternative names
        alternative_names = request.form.getlist('alternative_names[]')

        category = Category.query.get(category_id)

        new_material = RawMaterial(
            name=name,
            category=category,
            unit=unit,
            is_unlimited=is_unlimited,
            waste_percentage=waste_percentage
        )
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

                    # Use the supplier cost (required)
                    try:
                        supplier_cost = float(supplier_costs[i]) if supplier_costs[i] else 0
                    except (ValueError, IndexError):
                        supplier_cost = 0

                    # Get SKU for this supplier (if provided)
                    supplier_sku = supplier_skus[i] if i < len(supplier_skus) and supplier_skus[i] else None

                    # Get units_per_package for this supplier (default to 1)
                    try:
                        supplier_upp = float(supplier_upps[i]) if i < len(supplier_upps) and supplier_upps[i] else 1.0
                        if supplier_upp <= 0:
                            supplier_upp = 1.0
                    except (ValueError, TypeError, IndexError):
                        supplier_upp = 1.0

                    supplier_link = RawMaterialSupplier(
                        raw_material_id=new_material.id,
                        supplier_id=int(supplier_id),
                        cost_per_unit=supplier_cost,
                        is_primary=is_primary,
                        sku=supplier_sku,
                        units_per_package=supplier_upp
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
                        cost_per_unit=0,  # Default price needs to be set later
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

        # Add alternative names
        for alt_name in alternative_names:
            if alt_name and alt_name.strip():
                # Validate uniqueness
                is_unique, existing_material_name = validate_alternative_name_uniqueness(alt_name.strip())
                if not is_unique:
                    flash(f'השם החלופי "{alt_name}" כבר קיים עבור חומר: {existing_material_name}', 'error')
                    db.session.rollback()
                    categories = Category.query.filter_by(type='raw_material').all()
                    suppliers = Supplier.query.all()
                    return render_template('add_or_edit_raw_material.html',
                                         categories=categories,
                                         suppliers=suppliers,
                                         units_list=units_list())

                # Add alternative name
                alt_name_record = RawMaterialAlternativeName(
                    raw_material_id=new_material.id,
                    alternative_name=alt_name.strip()
                )
                db.session.add(alt_name_record)

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

        # Update waste percentage
        if is_unlimited:
            material.waste_percentage = 0  # Unlimited materials can't have waste
        else:
            has_waste = request.form.get('has_waste') == 'on'
            if has_waste:
                try:
                    waste_percentage = float(request.form.get('waste_percentage', 0))
                    material.waste_percentage = max(0, min(99, waste_percentage))
                except (ValueError, TypeError):
                    material.waste_percentage = 0
            else:
                material.waste_percentage = 0

        # Get alternative names from form (do this early for all materials)
        new_alternative_names = request.form.getlist('alternative_names[]')

        # Skip supplier handling for unlimited materials
        if is_unlimited:
            # Remove all existing supplier links if switching to unlimited
            RawMaterialSupplier.query.filter_by(raw_material_id=material.id).delete()

            # Handle alternative names for unlimited materials before returning
            # Get existing alternative names
            existing_alt_names = {alt.alternative_name: alt for alt in material.alternative_names}
            new_alt_names_set = set()

            # Process new alternative names
            for alt_name in new_alternative_names:
                if alt_name and alt_name.strip():
                    alt_name_stripped = alt_name.strip()
                    new_alt_names_set.add(alt_name_stripped)

                    # If it's a new alternative name, validate and add
                    if alt_name_stripped not in existing_alt_names:
                        is_unique, existing_material_name = validate_alternative_name_uniqueness(alt_name_stripped, material.id)
                        if not is_unique:
                            flash(f'השם החלופי "{alt_name_stripped}" כבר קיים עבור חומר: {existing_material_name}', 'error')
                            db.session.rollback()
                            categories = Category.query.filter_by(type='raw_material').all()
                            suppliers_list = Supplier.query.filter_by(is_active=True).all()
                            return render_template('add_or_edit_raw_material.html',
                                                 material=material,
                                                 categories=categories,
                                                 suppliers=suppliers_list,
                                                 units_list=units_list())

                        # Add new alternative name
                        new_alt_name_record = RawMaterialAlternativeName(
                            raw_material_id=material.id,
                            alternative_name=alt_name_stripped
                        )
                        db.session.add(new_alt_name_record)

            # Remove alternative names that were deleted
            for existing_name, existing_record in existing_alt_names.items():
                if existing_name not in new_alt_names_set:
                    db.session.delete(existing_record)

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
        supplier_skus = request.form.getlist('supplier_skus[]')
        supplier_upps = request.form.getlist('supplier_upps[]')
        primary_supplier_value = request.form.get('primary_supplier')

        # Add new supplier links
        supplier_count = 0
        for i, supplier_id in enumerate(supplier_ids):
            if supplier_id:  # Skip empty selections
                supplier_count += 1
                # Check if this is the primary supplier (radio value matches index+1)
                is_primary = (str(i+1) == primary_supplier_value) if primary_supplier_value else (i == 0)

                # Use the supplier cost (required)
                try:
                    supplier_cost = float(supplier_costs[i]) if supplier_costs[i] else 0
                except (ValueError, IndexError):
                    supplier_cost = 0

                # Get SKU for this supplier (if provided)
                supplier_sku = supplier_skus[i] if i < len(supplier_skus) and supplier_skus[i] else None

                # Get units_per_package for this supplier (default to 1)
                try:
                    supplier_upp = float(supplier_upps[i]) if i < len(supplier_upps) and supplier_upps[i] else 1.0
                    if supplier_upp <= 0:
                        supplier_upp = 1.0
                except (ValueError, TypeError, IndexError):
                    supplier_upp = 1.0

                supplier_link = RawMaterialSupplier(
                    raw_material_id=material.id,
                    supplier_id=int(supplier_id),
                    cost_per_unit=supplier_cost,
                    is_primary=is_primary,
                    sku=supplier_sku,
                    units_per_package=supplier_upp
                )
                db.session.add(supplier_link)

        # If no suppliers provided, use default supplier (ID=1)
        if supplier_count == 0:
            default_supplier = Supplier.query.filter_by(id=1).first()
            if default_supplier:
                supplier_link = RawMaterialSupplier(
                    raw_material_id=material.id,
                    supplier_id=1,
                    cost_per_unit=0,  # Default price needs to be set
                    is_primary=True
                )
                db.session.add(supplier_link)

        # Handle alternative names
        # Get existing alternative names
        existing_alt_names = {alt.alternative_name: alt for alt in material.alternative_names}
        new_alt_names_set = set()

        # Process new alternative names
        for alt_name in new_alternative_names:
            if alt_name and alt_name.strip():
                alt_name_stripped = alt_name.strip()
                new_alt_names_set.add(alt_name_stripped)

                # If it's a new alternative name, validate and add
                if alt_name_stripped not in existing_alt_names:
                    is_unique, existing_material_name = validate_alternative_name_uniqueness(alt_name_stripped, material.id)
                    if not is_unique:
                        flash(f'השם החלופי "{alt_name_stripped}" כבר קיים עבור חומר: {existing_material_name}', 'error')
                        db.session.rollback()
                        categories = Category.query.filter_by(type='raw_material').all()
                        suppliers_list = Supplier.query.filter_by(is_active=True).all()
                        return render_template('add_or_edit_raw_material.html',
                                             material=material,
                                             categories=categories,
                                             suppliers=suppliers_list,
                                             units_list=units_list())

                    # Add new alternative name
                    new_alt_name_record = RawMaterialAlternativeName(
                        raw_material_id=material.id,
                        alternative_name=alt_name_stripped
                    )
                    db.session.add(new_alt_name_record)

        # Remove alternative names that were deleted
        for existing_name, existing_record in existing_alt_names.items():
            if existing_name not in new_alt_names_set:
                db.session.delete(existing_record)

        db.session.commit()
        return redirect(url_for('raw_materials.raw_materials'))

    # GET request - prepare data
    categories = Category.query.filter_by(type='raw_material').all()
    suppliers = Supplier.query.filter_by(is_active=True).all()

    # Load supplier links for the material with stock info
    material.supplier_links = RawMaterialSupplier.query.filter_by(
        raw_material_id=material.id
    ).all()

    # Add current stock and discount info for each supplier
    from .utils import apply_supplier_discount
    for link in material.supplier_links:
        link.current_stock = calculate_supplier_stock(material.id, link.supplier_id)
        link.discounted_cost = apply_supplier_discount(link.cost_per_unit, link.supplier)
        link.discount_percentage = link.supplier.discount_percentage

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

    # Check if material has been "used" (has historical data)
    stock_log_count = StockLog.query.filter_by(raw_material_id=material.id).count()
    stock_audit_count = StockAudit.query.filter_by(raw_material_id=material.id).count()
    component_count = ProductComponent.query.filter_by(component_id=material.id, component_type='raw_material').count()

    total_usage = stock_log_count + stock_audit_count + component_count

    if total_usage > 0:
        # Material has historical data - SOFT DELETE
        material.is_deleted = True
        log_audit("SOFT_DELETE", "RawMaterial", material_id,
                 f"Soft deleted raw material {material.name} (has historical data: {total_usage} records)")
        db.session.commit()
    else:
        # Material has no history - HARD DELETE
        # Delete related entries (should be minimal/none)
        StockLog.query.filter_by(raw_material_id=material.id).delete()
        StockAudit.query.filter_by(raw_material_id=material.id).delete()
        ProductComponent.query.filter_by(component_id=material.id, component_type='raw_material').delete()
        RawMaterialSupplier.query.filter_by(raw_material_id=material.id).delete()

        db.session.delete(material)
        log_audit("HARD_DELETE", "RawMaterial", material_id, f"Hard deleted raw material {material.name} (no historical data)")
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

        # Get supplier price for variance cost calculation
        supplier_price = 0
        if supplier_id:
            supplier_link = RawMaterialSupplier.query.filter_by(
                raw_material_id=raw_material_id,
                supplier_id=supplier_id
            ).first()
            if supplier_link:
                supplier_price = supplier_link.cost_per_unit

        variance_cost = variance * supplier_price

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