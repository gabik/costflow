from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from flask_babel import gettext as _
from ..models import db, Packaging, PackagingSupplier, Supplier, StockLog, StockAudit
from .utils import calculate_packaging_stock, calculate_total_packaging_stock, calculate_packaging_supplier_stock, log_audit, apply_supplier_discount

packaging_blueprint = Blueprint('packaging', __name__)

# ----------------------------
# Packaging Management
# ----------------------------
@packaging_blueprint.route('/packaging', methods=['GET'])
def packaging():
    all_packaging = Packaging.query.all()

    # Calculate stock and supplier info for each packaging item
    packaging_with_stock = []
    for pkg in all_packaging:
        # Use the function that sums all supplier stocks
        stock = calculate_total_packaging_stock(pkg.id)

        # Get supplier information for this packaging
        supplier_links = PackagingSupplier.query.filter_by(packaging_id=pkg.id).all()
        supplier_count = len(supplier_links)

        # Get per-supplier stock breakdown
        stock_breakdown = []
        primary_supplier = None
        primary_price = pkg.price_per_package  # Fallback to old price

        for link in supplier_links:
            supplier_stock = calculate_packaging_supplier_stock(pkg.id, link.supplier_id)
            # Calculate discounted price
            discounted_price = apply_supplier_discount(link.price_per_package, link.supplier)

            stock_breakdown.append({
                'supplier_id': link.supplier_id,
                'supplier_name': link.supplier.name,
                'stock': supplier_stock,
                'is_primary': link.is_primary,
                'price_per_package': link.price_per_package,
                'discounted_price_per_package': discounted_price,
                'discount_percentage': link.supplier.discount_percentage
            })

            if link.is_primary:
                primary_supplier = link.supplier
                primary_price = discounted_price

        # Sort by primary first, then by stock amount
        stock_breakdown.sort(key=lambda x: (-x['is_primary'], -x['stock']))

        packaging_with_stock.append({
            'id': pkg.id,
            'name': pkg.name,
            'quantity_per_package': pkg.quantity_per_package,
            'price_per_package': primary_price,
            'current_stock': stock,
            'supplier_count': supplier_count,
            'stock_breakdown': stock_breakdown,
            'primary_supplier': primary_supplier
        })

    return render_template('packaging.html', packaging=packaging_with_stock)

@packaging_blueprint.route('/packaging/add', methods=['GET', 'POST'])
def add_packaging():
    if request.method == 'POST':
        name = request.form['name']
        quantity_per_package = request.form['quantity_per_package']

        # Get multiple suppliers
        supplier_ids = request.form.getlist('supplier_ids[]')
        supplier_prices = request.form.getlist('supplier_prices[]')
        supplier_skus = request.form.getlist('supplier_skus[]')
        primary_supplier_value = request.form.get('primary_supplier')

        # Create packaging with default price (will be overridden by supplier price)
        default_price = float(supplier_prices[0]) if supplier_prices else 0
        new_packaging = Packaging(
            name=name,
            quantity_per_package=int(quantity_per_package),
            price_per_package=default_price  # Keep for backward compatibility
        )
        db.session.add(new_packaging)
        db.session.flush()  # Get ID for supplier links

        # Add supplier links
        primary_supplier_id = None
        supplier_count = 0
        for i, supplier_id in enumerate(supplier_ids):
            if supplier_id:  # Skip empty selections
                supplier_count += 1
                # Check if this is the primary supplier
                is_primary = (str(i+1) == primary_supplier_value) if primary_supplier_value else (i == 0)

                # Use the supplier price
                try:
                    supplier_price = float(supplier_prices[i]) if supplier_prices[i] else 0
                except (ValueError, IndexError):
                    supplier_price = 0

                # Get SKU for this supplier (if provided)
                supplier_sku = supplier_skus[i] if i < len(supplier_skus) and supplier_skus[i] else None

                supplier_link = PackagingSupplier(
                    packaging_id=new_packaging.id,
                    supplier_id=int(supplier_id),
                    price_per_package=supplier_price,
                    is_primary=is_primary,
                    sku=supplier_sku
                )
                db.session.add(supplier_link)

                # Track primary supplier for initial stock
                if is_primary:
                    primary_supplier_id = int(supplier_id)

        # If no suppliers provided, use default supplier (ID=1)
        if supplier_count == 0:
            default_supplier = Supplier.query.filter_by(id=1).first()
            if default_supplier:
                supplier_link = PackagingSupplier(
                    packaging_id=new_packaging.id,
                    supplier_id=1,
                    price_per_package=default_price,
                    is_primary=True
                )
                db.session.add(supplier_link)
                primary_supplier_id = 1

        # Add initial stock if provided
        initial_stock = request.form.get('initial_stock', 0)
        if initial_stock and primary_supplier_id:
            stock_log = StockLog(
                packaging_id=new_packaging.id,
                supplier_id=primary_supplier_id,
                action_type='set',
                quantity=float(initial_stock)
            )
            db.session.add(stock_log)

        db.session.commit()
        log_audit("CREATE", "Packaging", new_packaging.id, f"Added packaging: {name}")

        return redirect(url_for('packaging.packaging'))

    # GET request
    suppliers = Supplier.query.filter_by(is_active=True).all()
    return render_template('add_or_edit_packaging.html', packaging=None, suppliers=suppliers)

@packaging_blueprint.route('/packaging/edit/<int:packaging_id>', methods=['GET', 'POST'])
def edit_packaging(packaging_id):
    packaging_item = Packaging.query.get_or_404(packaging_id)

    if request.method == 'POST':
        packaging_item.name = request.form['name']
        packaging_item.quantity_per_package = int(request.form['quantity_per_package'])

        # Get existing suppliers before deletion to check for stock
        existing_suppliers = PackagingSupplier.query.filter_by(packaging_id=packaging_id).all()
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
            stock = calculate_packaging_supplier_stock(packaging_id, removed_id)
            if stock > 0:
                if stock_handling == 'transfer' and transfer_to_supplier:
                    # Transfer stock to another supplier
                    # Create a negative log for the removed supplier
                    negative_log = StockLog(
                        packaging_id=packaging_id,
                        supplier_id=removed_id,
                        action_type='add',
                        quantity=-stock
                    )
                    db.session.add(negative_log)

                    # Create a positive log for the target supplier
                    positive_log = StockLog(
                        packaging_id=packaging_id,
                        supplier_id=int(transfer_to_supplier),
                        action_type='add',
                        quantity=stock
                    )
                    db.session.add(positive_log)

                    log_audit("STOCK_TRANSFER", "Packaging", packaging_id,
                             f"Transferred {stock} units from supplier {removed_id} to {transfer_to_supplier}")

                elif stock_handling == 'waste':
                    # Mark stock as waste
                    waste_log = StockLog(
                        packaging_id=packaging_id,
                        supplier_id=removed_id,
                        action_type='add',
                        quantity=-stock
                    )
                    db.session.add(waste_log)

                    log_audit("STOCK_WASTE", "Packaging", packaging_id,
                             f"Marked {stock} units from supplier {removed_id} as waste")

        # Clear existing supplier links
        PackagingSupplier.query.filter_by(packaging_id=packaging_id).delete()

        # Get multiple suppliers from form
        supplier_ids = request.form.getlist('supplier_ids[]')
        supplier_prices = request.form.getlist('supplier_prices[]')
        supplier_skus = request.form.getlist('supplier_skus[]')
        primary_supplier_value = request.form.get('primary_supplier')

        # Add new supplier links
        supplier_count = 0
        default_price = 0
        for i, supplier_id in enumerate(supplier_ids):
            if supplier_id:  # Skip empty selections
                supplier_count += 1
                # Check if this is the primary supplier
                is_primary = (str(i+1) == primary_supplier_value) if primary_supplier_value else (i == 0)

                # Use the supplier price
                try:
                    supplier_price = float(supplier_prices[i]) if supplier_prices[i] else 0
                except (ValueError, IndexError):
                    supplier_price = 0

                if i == 0:
                    default_price = supplier_price

                # Get SKU for this supplier (if provided)
                supplier_sku = supplier_skus[i] if i < len(supplier_skus) and supplier_skus[i] else None

                supplier_link = PackagingSupplier(
                    packaging_id=packaging_id,
                    supplier_id=int(supplier_id),
                    price_per_package=supplier_price,
                    is_primary=is_primary,
                    sku=supplier_sku
                )
                db.session.add(supplier_link)

        # Update default price for backward compatibility
        packaging_item.price_per_package = default_price if default_price else packaging_item.price_per_package

        # If no suppliers provided, use default supplier (ID=1)
        if supplier_count == 0:
            default_supplier = Supplier.query.filter_by(id=1).first()
            if default_supplier:
                supplier_link = PackagingSupplier(
                    packaging_id=packaging_id,
                    supplier_id=1,
                    price_per_package=packaging_item.price_per_package,
                    is_primary=True
                )
                db.session.add(supplier_link)

        db.session.commit()
        log_audit("UPDATE", "Packaging", packaging_id, f"Updated packaging: {packaging_item.name}")

        return redirect(url_for('packaging.packaging'))

    # GET request
    suppliers = Supplier.query.filter_by(is_active=True).all()
    return render_template('add_or_edit_packaging.html',
                         packaging=packaging_item,
                         suppliers=suppliers)

@packaging_blueprint.route('/packaging/delete/<int:packaging_id>', methods=['POST'])
def delete_packaging(packaging_id):
    packaging_item = Packaging.query.get_or_404(packaging_id)
    db.session.delete(packaging_item)
    db.session.commit()
    return redirect(url_for('packaging.packaging'))

# ----------------------------
# Stock Management
# ----------------------------
@packaging_blueprint.route('/packaging/update_stock', methods=['POST'])
def update_packaging_stock():
    """Update packaging stock (add or set) for specific supplier and create audit records for 'set' actions"""
    from datetime import datetime

    packaging_id = request.form.get('packaging_id', type=int)
    supplier_id = request.form.get('supplier_id', type=int)
    action_type = request.form.get('action_type')  # 'add' or 'set'
    quantity = request.form.get('quantity', type=float)
    auditor_name = request.form.get('auditor_name', '')  # Optional for 'set' actions

    if not packaging_id or not action_type or quantity is None:
        return jsonify({'success': False, 'error': _('Missing required fields')}), 400

    packaging = Packaging.query.get(packaging_id)
    if not packaging:
        return jsonify({'success': False, 'error': _('Packaging not found')}), 404

    # If no supplier specified, try to get primary supplier
    if not supplier_id:
        primary_link = packaging.get_primary_supplier_link()
        if primary_link:
            supplier_id = primary_link.supplier_id
        else:
            # Backward compatibility: no supplier specified and no primary
            supplier_id = None

    # If action_type is 'set', calculate current stock and create audit record
    if action_type == 'set':
        # Calculate current stock before update
        if supplier_id:
            system_stock = calculate_packaging_supplier_stock(packaging_id, supplier_id)
        else:
            system_stock = calculate_packaging_stock(packaging_id)

        # Calculate variance
        variance = quantity - system_stock
        # Get price per unit from supplier or fallback to packaging default
        if supplier_id:
            link = PackagingSupplier.query.filter_by(
                packaging_id=packaging_id,
                supplier_id=supplier_id
            ).first()
            price_per_unit = (link.price_per_package / packaging.quantity_per_package) if link else packaging.price_per_unit
        else:
            price_per_unit = packaging.price_per_unit
        variance_cost = variance * price_per_unit

        # Create the stock log entry
        stock_log = StockLog(
            packaging_id=packaging_id,
            supplier_id=supplier_id,
            action_type=action_type,
            quantity=quantity,
            timestamp=datetime.utcnow()
        )
        db.session.add(stock_log)
        db.session.flush()  # Flush to get the stock_log.id

        # Create stock audit record
        stock_audit = StockAudit(
            packaging_id=packaging_id,
            system_quantity=system_stock,
            physical_quantity=quantity,
            variance=variance,
            variance_cost=variance_cost,
            auditor_name=auditor_name if auditor_name else None,
            stock_log_id=stock_log.id
        )
        db.session.add(stock_audit)

        supplier_info = f" (Supplier: {supplier_id})" if supplier_id else ""
        log_audit("STOCK_AUDIT", "Packaging", packaging_id,
                 f"Physical count: {quantity}, System: {system_stock:.2f}, Variance: {variance:.2f} (Cost: {variance_cost:.2f}){supplier_info}")
    else:
        # For 'add' action, just create the stock log
        stock_log = StockLog(
            packaging_id=packaging_id,
            supplier_id=supplier_id,
            action_type=action_type,
            quantity=quantity,
            timestamp=datetime.utcnow()
        )
        db.session.add(stock_log)
        supplier_info = f" (Supplier: {supplier_id})" if supplier_id else ""
        log_audit("UPDATE_STOCK", "Packaging", packaging_id, f"{action_type} {quantity}{supplier_info}")

    db.session.commit()

    # Calculate new stock
    if supplier_id:
        new_stock = calculate_packaging_supplier_stock(packaging_id, supplier_id)
    else:
        new_stock = calculate_total_packaging_stock(packaging_id)

    return jsonify({
        'success': True,
        'message': _('Stock updated successfully'),
        'new_stock': new_stock
    })

@packaging_blueprint.route('/api/packaging/<int:packaging_id>/stock', methods=['GET'])
def get_packaging_stock(packaging_id):
    """Get current stock for a packaging item"""
    packaging = Packaging.query.get(packaging_id)
    if not packaging:
        return jsonify({'error': _('Packaging not found')}), 404

    current_stock = calculate_packaging_stock(packaging_id)

    return jsonify({
        'packaging_id': packaging_id,
        'name': packaging.name,
        'current_stock': current_stock
    })
