from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for
from ..models import db, Product, ProductionLog, Premake, StockLog
from .utils import log_audit

production_blueprint = Blueprint('production', __name__)

# ----------------------------
# Production Management
# ----------------------------
@production_blueprint.route('/production', methods=['GET', 'POST'])
def production():
    if request.method == 'POST':
        product_id = request.form['product_id']
        quantity_produced = float(request.form['quantity_produced'])

        # Log production
        production_log = ProductionLog(product_id=product_id, quantity_produced=quantity_produced)
        db.session.add(production_log)
        db.session.commit()

        return redirect(url_for('production.production'))

    products = Product.query.all()
    production_logs = ProductionLog.query.filter(ProductionLog.product_id != None).order_by(ProductionLog.timestamp.desc()).all()
    current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    return render_template('production.html', products=products, production_logs=production_logs, current_time=current_time)

@production_blueprint.route('/production/edit/<int:log_id>', methods=['POST'])
def edit_production_log(log_id):
    log = ProductionLog.query.get_or_404(log_id)
    new_quantity = float(request.form['quantity_produced'])
    
    old_qty = log.quantity_produced
    log.quantity_produced = new_quantity
    
    db.session.commit()
    log_audit("UPDATE", "ProductionLog", log.id, f"Updated product production from {old_qty} to {new_quantity}")
    
    return redirect(url_for('production.production'))

@production_blueprint.route('/production/delete/<int:log_id>', methods=['POST'])
def delete_production_log(log_id):
    log = ProductionLog.query.get_or_404(log_id)
    db.session.delete(log)
    db.session.commit()
    log_audit("DELETE", "ProductionLog", log_id, "Deleted product production log")
    return redirect(url_for('production.production'))

@production_blueprint.route('/production/premakes', methods=['GET', 'POST'])
def premake_production():
    if request.method == 'POST':
        premake_id = request.form['premake_id']
        # quantity_produced from form is in UNITS (e.g. kg), but we store BATCHES
        quantity_units = float(request.form['quantity_produced'])
        
        premake = Premake.query.get(premake_id)
        if not premake:
            return "Premake not found", 404
            
        # Convert units (kg) to batches
        if premake.batch_size > 0:
            quantity_batches = quantity_units / premake.batch_size
        else:
            quantity_batches = quantity_units

        # Log production (store in batches)
        production_log = ProductionLog(premake_id=premake_id, quantity_produced=quantity_batches)
        db.session.add(production_log)
        db.session.flush() # Get ID
        
        # Update Stock (Add produced amount to stock)
        # We add the total UNITS produced to stock.
        stock_log = StockLog(
            premake_id=premake_id,
            action_type='add',
            quantity=quantity_units,
            # We might want to link this stock log to the production log in future, 
            # but for now just implicit by time.
        )
        db.session.add(stock_log)
        
        db.session.commit()
        return redirect(url_for('production.premake_production'))

    premakes = Premake.query.all()
    production_logs = ProductionLog.query.filter(ProductionLog.premake_id != None).order_by(ProductionLog.timestamp.desc()).all()
    current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    return render_template('premake_production.html', premakes=premakes, production_logs=production_logs, current_time=current_time)

@production_blueprint.route('/production/premakes/edit/<int:log_id>', methods=['POST'])
def edit_premake_production_log(log_id):
    log = ProductionLog.query.get_or_404(log_id)
    new_quantity_units = float(request.form['quantity_produced'])
    
    premake = log.premake
    if not premake:
        return "Premake not found", 404

    # Calculate new batches
    if premake.batch_size > 0:
        new_quantity_batches = new_quantity_units / premake.batch_size
    else:
        new_quantity_batches = new_quantity_units

    old_qty_batches = log.quantity_produced
    old_qty_units = old_qty_batches * premake.batch_size

    # Update Production Log
    log.quantity_produced = new_quantity_batches
    
    # Update Stock Log
    # We need to find the associated 'add' stock log.
    # Since we don't have a direct foreign key, we look for a StockLog for this premake
    # created at roughly the same time (within 1-2 seconds) with the old quantity.
    # This is a bit brittle but standard for MVP without schema migration.
    
    # Better approach for correction: Add a correction StockLog (difference).
    # Difference = New - Old
    diff_units = new_quantity_units - old_qty_units
    
    if diff_units != 0:
        correction_log = StockLog(
            premake_id=premake.id,
            action_type='add', # Negative add reduces stock
            quantity=diff_units
        )
        db.session.add(correction_log)

    db.session.commit()
    log_audit("UPDATE", "ProductionLog", log.id, f"Updated premake production from {old_qty_units} to {new_quantity_units}")
    
    return redirect(url_for('production.premake_production'))

@production_blueprint.route('/production/premakes/delete/<int:log_id>', methods=['POST'])
def delete_premake_production_log(log_id):
    log = ProductionLog.query.get_or_404(log_id)
    
    # Revert stock
    premake = log.premake
    if premake:
        qty_units = log.quantity_produced * premake.batch_size
        revert_log = StockLog(
            premake_id=premake.id,
            action_type='add',
            quantity=-qty_units
        )
        db.session.add(revert_log)

    db.session.delete(log)
    db.session.commit()
    log_audit("DELETE", "ProductionLog", log_id, "Deleted premake production log")
    return redirect(url_for('production.premake_production'))
