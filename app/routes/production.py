from datetime import datetime
from flask import render_template, request, redirect, url_for
from ..models import db, Product, ProductionLog, Premake, StockLog
from . import main_blueprint

# ----------------------------
# Production Management
# ----------------------------
@main_blueprint.route('/production', methods=['GET', 'POST'])
def production():
    if request.method == 'POST':
        product_id = request.form['product_id']
        quantity_produced = float(request.form['quantity_produced'])

        # Log production
        production_log = ProductionLog(product_id=product_id, quantity_produced=quantity_produced)
        db.session.add(production_log)
        db.session.commit()

        return redirect(url_for('main.production'))

    products = Product.query.all()
    production_logs = ProductionLog.query.filter(ProductionLog.product_id != None).order_by(ProductionLog.timestamp.desc()).all()
    current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    return render_template('production.html', products=products, production_logs=production_logs, current_time=current_time)

@main_blueprint.route('/production/premakes', methods=['GET', 'POST'])
def premake_production():
    if request.method == 'POST':
        premake_id = request.form['premake_id']
        # quantity_produced from form is in UNITS (e.g. kg), but we store BATCHES
        quantity_units = float(request.form['quantity_produced'])
        
        premake = Premake.query.get(premake_id)
        if not premake:
            return "Premake not found", 404
            
        # Convert units (kg) to batches
        # If batch_size is 10kg and we made 5kg, we made 0.5 batches.
        if premake.batch_size > 0:
            quantity_batches = quantity_units / premake.batch_size
        else:
            # Fallback if batch size is invalid? 
            # If batch size 0, we can't calculate batches.
            # Assume 1 batch = 1 unit? Or error?
            # Let's assume 1:1 to prevent crash, but this is data error.
            quantity_batches = quantity_units

        # Log production (store in batches)
        production_log = ProductionLog(premake_id=premake_id, quantity_produced=quantity_batches)
        db.session.add(production_log)
        
        # Update Stock (Add produced amount to stock)
        # We add the total UNITS produced to stock.
        # If we made 5kg (0.5 batches * 10kg/batch), we add 5kg.
        # StockLog stores UNITS.
        stock_log = StockLog(
            premake_id=premake_id,
            action_type='add',
            quantity=quantity_units
        )
        db.session.add(stock_log)
        
        db.session.commit()
        return redirect(url_for('main.premake_production'))

    premakes = Premake.query.all()
    production_logs = ProductionLog.query.filter(ProductionLog.premake_id != None).order_by(ProductionLog.timestamp.desc()).all()
    current_time = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    return render_template('premake_production.html', premakes=premakes, production_logs=production_logs, current_time=current_time)
