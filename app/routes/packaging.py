from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from flask_babel import gettext as _
from ..models import db, Packaging, StockLog, StockAudit
from .utils import calculate_packaging_stock, log_audit

packaging_blueprint = Blueprint('packaging', __name__)

# ----------------------------
# Packaging Management
# ----------------------------
@packaging_blueprint.route('/packaging', methods=['GET'])
def packaging():
    all_packaging = Packaging.query.all()

    # Calculate stock for each packaging item
    packaging_with_stock = []
    for pkg in all_packaging:
        stock = calculate_packaging_stock(pkg.id)
        packaging_with_stock.append({
            'id': pkg.id,
            'name': pkg.name,
            'quantity_per_package': pkg.quantity_per_package,
            'price_per_package': pkg.price_per_package,
            'current_stock': stock
        })

    return render_template('packaging.html', packaging=packaging_with_stock)

@packaging_blueprint.route('/packaging/add', methods=['GET', 'POST'])
def add_packaging():
    if request.method == 'POST':
        name = request.form['name']
        quantity_per_package = request.form['quantity_per_package']
        price_per_package = request.form['price_per_package']

        new_packaging = Packaging(
            name=name,
            quantity_per_package=int(quantity_per_package),
            price_per_package=float(price_per_package)
        )
        db.session.add(new_packaging)
        db.session.commit()
        return redirect(url_for('packaging.packaging'))

    return render_template('add_or_edit_packaging.html', packaging=None)

@packaging_blueprint.route('/packaging/edit/<int:packaging_id>', methods=['GET', 'POST'])
def edit_packaging(packaging_id):
    packaging_item = Packaging.query.get_or_404(packaging_id)
    if request.method == 'POST':
        packaging_item.name = request.form['name']
        packaging_item.quantity_per_package = int(request.form['quantity_per_package'])
        packaging_item.price_per_package = float(request.form['price_per_package'])
        db.session.commit()
        return redirect(url_for('packaging.packaging'))

    return render_template('add_or_edit_packaging.html', packaging=packaging_item)

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
    """Update packaging stock (add or set) and create audit records for 'set' actions"""
    from datetime import datetime

    packaging_id = request.form.get('packaging_id', type=int)
    action_type = request.form.get('action_type')  # 'add' or 'set'
    quantity = request.form.get('quantity', type=float)
    auditor_name = request.form.get('auditor_name', '')  # Optional for 'set' actions

    if not packaging_id or not action_type or quantity is None:
        return jsonify({'success': False, 'error': _('Missing required fields')}), 400

    packaging = Packaging.query.get(packaging_id)
    if not packaging:
        return jsonify({'success': False, 'error': _('Packaging not found')}), 404

    # If action_type is 'set', calculate current stock and create audit record
    if action_type == 'set':
        # Calculate current stock before update
        system_stock = calculate_packaging_stock(packaging_id)

        # Calculate variance
        variance = quantity - system_stock
        variance_cost = variance * packaging.price_per_unit

        # Create the stock log entry
        stock_log = StockLog(
            packaging_id=packaging_id,
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

        log_audit("STOCK_AUDIT", "Packaging", packaging_id,
                 f"Physical count: {quantity}, System: {system_stock:.2f}, Variance: {variance:.2f} (Cost: {variance_cost:.2f})")
    else:
        # For 'add' action, just create the stock log
        stock_log = StockLog(
            packaging_id=packaging_id,
            action_type=action_type,
            quantity=quantity,
            timestamp=datetime.utcnow()
        )
        db.session.add(stock_log)
        log_audit("UPDATE_STOCK", "Packaging", packaging_id, f"{action_type} {quantity}")

    db.session.commit()

    # Calculate new stock
    new_stock = calculate_packaging_stock(packaging_id)

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
