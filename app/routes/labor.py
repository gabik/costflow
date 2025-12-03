from flask import Blueprint, render_template, request, redirect, url_for
from ..models import db, Labor
from .utils import log_audit

labor_blueprint = Blueprint('labor', __name__)

# ----------------------------
# Labor Management
# ----------------------------
@labor_blueprint.route('/labor')
def labor():
    labor_list = Labor.query.all()
    return render_template('labor.html', all_labor=labor_list)

@labor_blueprint.route('/labor/add', methods=['GET', 'POST'])
def add_labor():
    if request.method == 'POST':
        name = request.form['name']
        phone_number = request.form.get('phone_number')
        
        # Handle single total input
        total_hourly_rate = float(request.form['total_hourly_rate'])
        base_hourly_rate = total_hourly_rate
        additional_hourly_rate = 0.0

        new_labor = Labor(name=name, phone_number=phone_number, base_hourly_rate=base_hourly_rate, additional_hourly_rate=additional_hourly_rate)
        db.session.add(new_labor)
        db.session.commit()
        
        log_audit("CREATE", "Labor", new_labor.id, f"Created labor entry {new_labor.name}")

        # Handle modal submissions
        if request.referrer and 'products/add' in request.referrer:
            return redirect(request.referrer)
    return redirect(url_for('labor.labor'))

@labor_blueprint.route('/labor/edit/<int:labor_id>', methods=['GET', 'POST'])
def edit_labor(labor_id):
    labor_item = Labor.query.get_or_404(labor_id)

    if request.method == 'POST':
        labor_item.name = request.form['name']
        labor_item.phone_number = request.form.get('phone_number')
        
        total_hourly_rate = float(request.form['total_hourly_rate'])
        labor_item.base_hourly_rate = total_hourly_rate
        labor_item.additional_hourly_rate = 0.0

        db.session.commit()
        log_audit("UPDATE", "Labor", labor_item.id, f"Updated labor entry {labor_item.name}")

        return redirect(url_for('labor.labor'))
    return render_template('add_or_edit_labor.html', labor=labor_item)

@labor_blueprint.route('/labor/delete/<int:labor_id>', methods=['POST'])
def delete_labor(labor_id):
    labor_item = Labor.query.get_or_404(labor_id)
    db.session.delete(labor_item)
    db.session.commit()
    log_audit("DELETE", "Labor", labor_id, f"Deleted labor entry {labor_item.name}")
    return redirect(url_for('labor.labor'))
