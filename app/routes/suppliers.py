from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from ..models import db, Supplier, RawMaterialSupplier, RawMaterial, StockLog
from .utils import log_audit

suppliers_blueprint = Blueprint('suppliers', __name__)

# ----------------------------
# Supplier Management
# ----------------------------
@suppliers_blueprint.route('/suppliers')
def suppliers():
    """List all suppliers with their material counts"""
    # Get 'show_inactive' parameter from URL query string
    show_inactive = request.args.get('show_inactive', 'false') == 'true'

    if show_inactive:
        all_suppliers = Supplier.query.all()  # Show all suppliers
    else:
        all_suppliers = Supplier.query.filter_by(is_active=True).all()  # Only active

    # Calculate active materials count for each supplier
    for supplier in all_suppliers:
        supplier.active_materials_count = len(supplier.material_links)

    return render_template('suppliers.html',
                         suppliers=all_suppliers,
                         show_inactive=show_inactive)

@suppliers_blueprint.route('/suppliers/add', methods=['GET', 'POST'])
def add_supplier():
    """Add new supplier (full form)"""
    if request.method == 'POST':
        name = request.form.get('name')
        contact_person = request.form.get('contact_person')
        phone = request.form.get('phone')
        email = request.form.get('email')
        address = request.form.get('address')

        # Check for duplicate name
        existing = Supplier.query.filter_by(name=name).first()
        if existing:
            return render_template('add_or_edit_supplier.html',
                                 supplier=None,
                                 error="ספק עם שם זה כבר קיים במערכת")

        new_supplier = Supplier(
            name=name,
            contact_person=contact_person,
            phone=phone,
            email=email,
            address=address
        )

        db.session.add(new_supplier)
        db.session.commit()
        log_audit("CREATE", "Supplier", new_supplier.id, f"Added supplier: {name}")

        return redirect(url_for('suppliers.suppliers'))

    return render_template('add_or_edit_supplier.html', supplier=None)

@suppliers_blueprint.route('/suppliers/edit/<int:supplier_id>', methods=['GET', 'POST'])
def edit_supplier(supplier_id):
    """Edit supplier details"""
    supplier = Supplier.query.get_or_404(supplier_id)

    if request.method == 'POST':
        supplier.name = request.form.get('name')
        supplier.contact_person = request.form.get('contact_person')
        supplier.phone = request.form.get('phone')
        supplier.email = request.form.get('email')
        supplier.address = request.form.get('address')

        db.session.commit()
        log_audit("UPDATE", "Supplier", supplier.id, f"Updated supplier: {supplier.name}")

        return redirect(url_for('suppliers.suppliers'))

    return render_template('add_or_edit_supplier.html', supplier=supplier)

@suppliers_blueprint.route('/suppliers/toggle/<int:supplier_id>', methods=['POST'])
def toggle_supplier(supplier_id):
    """Toggle supplier active status"""
    supplier = Supplier.query.get_or_404(supplier_id)
    supplier.is_active = not supplier.is_active

    db.session.commit()
    action = "Activated" if supplier.is_active else "Deactivated"
    log_audit("UPDATE", "Supplier", supplier.id, f"{action} supplier: {supplier.name}")

    return redirect(url_for('suppliers.suppliers'))

@suppliers_blueprint.route('/suppliers/delete/<int:supplier_id>', methods=['POST'])
def delete_supplier(supplier_id):
    """Delete supplier (only if no history)"""
    supplier = Supplier.query.get_or_404(supplier_id)

    # Check if supplier has any material links or stock logs
    has_materials = len(supplier.material_links) > 0
    has_stock_logs = StockLog.query.filter_by(supplier_id=supplier_id).first() is not None

    if has_materials or has_stock_logs:
        # Soft delete - just deactivate
        supplier.is_active = False
        db.session.commit()
        log_audit("UPDATE", "Supplier", supplier.id, f"Soft deleted supplier: {supplier.name}")
    else:
        # Hard delete - completely remove
        db.session.delete(supplier)
        db.session.commit()
        log_audit("DELETE", "Supplier", supplier_id, f"Deleted supplier: {supplier.name}")

    return redirect(url_for('suppliers.suppliers'))

@suppliers_blueprint.route('/suppliers/quick-add', methods=['POST'])
def quick_add_supplier():
    """AJAX endpoint for quick supplier addition via modal"""
    try:
        # Handle both JSON and form data
        if request.is_json:
            data = request.get_json()
            name = data.get('name')
            contact_person = data.get('contact_person')
            phone = data.get('phone')
            email = data.get('email')
            address = data.get('address')
        else:
            # Form data from modal
            name = request.form.get('name')
            contact_person = request.form.get('contact_person')
            phone = request.form.get('phone')
            email = request.form.get('email')
            address = request.form.get('address')

        # Check for duplicate name
        existing = Supplier.query.filter_by(name=name).first()
        if existing:
            return jsonify({
                'success': False,
                'error': 'ספק עם שם זה כבר קיים במערכת'
            }), 400

        new_supplier = Supplier(
            name=name,
            contact_person=contact_person,
            phone=phone,
            email=email,
            address=address
        )

        db.session.add(new_supplier)
        db.session.commit()
        log_audit("CREATE", "Supplier", new_supplier.id, f"Quick added supplier: {name}")

        # Return different response based on request type
        if request.is_json:
            return jsonify({
                'success': True,
                'supplier': {
                    'id': new_supplier.id,
                    'name': new_supplier.name
                }
            })
        else:
            # Redirect back to the referrer (raw materials form)
            return redirect(request.referrer or url_for('raw_materials.add_raw_material'))

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@suppliers_blueprint.route('/suppliers/<int:supplier_id>/materials')
def supplier_materials(supplier_id):
    """View all materials for a specific supplier"""
    supplier = Supplier.query.get_or_404(supplier_id)

    # Get all material links for this supplier
    material_links = RawMaterialSupplier.query.filter_by(supplier_id=supplier_id).all()

    materials_data = []
    for link in material_links:
        material = link.raw_material

        # Calculate current stock for this supplier-material combination
        from .utils import calculate_supplier_stock
        stock = calculate_supplier_stock(material.id, supplier_id)

        materials_data.append({
            'material': material,
            'cost_per_unit': link.cost_per_unit,
            'is_primary': link.is_primary,
            'sku': link.sku,
            'current_stock': stock
        })

    return render_template('supplier_materials.html',
                         supplier=supplier,
                         materials_data=materials_data)

@suppliers_blueprint.route('/suppliers/link-material', methods=['POST'])
def link_material_to_supplier():
    """Link a raw material to a supplier with specific pricing"""
    try:
        material_id = request.form.get('material_id')
        supplier_id = request.form.get('supplier_id')
        cost_per_unit = float(request.form.get('cost_per_unit'))
        is_primary = request.form.get('is_primary') == 'true'
        sku = request.form.get('sku', '').strip() or None

        # Check if link already exists
        existing = RawMaterialSupplier.query.filter_by(
            raw_material_id=material_id,
            supplier_id=supplier_id
        ).first()

        if existing:
            # Update existing link
            existing.cost_per_unit = cost_per_unit
            existing.is_primary = is_primary
            existing.sku = sku
        else:
            # Create new link
            new_link = RawMaterialSupplier(
                raw_material_id=material_id,
                supplier_id=supplier_id,
                cost_per_unit=cost_per_unit,
                is_primary=is_primary,
                sku=sku
            )
            db.session.add(new_link)

        # If setting as primary, unset other primaries
        if is_primary:
            RawMaterialSupplier.query.filter(
                RawMaterialSupplier.raw_material_id == material_id,
                RawMaterialSupplier.supplier_id != supplier_id
            ).update({'is_primary': False})

        db.session.commit()

        material = RawMaterial.query.get(material_id)
        supplier = Supplier.query.get(supplier_id)
        sku_info = f" (SKU: {sku})" if sku else ""
        log_audit("UPDATE", "RawMaterialSupplier", None,
                 f"Linked {material.name} to {supplier.name} at {cost_per_unit}/unit{sku_info}")

        return jsonify({'success': True})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@suppliers_blueprint.route('/suppliers/unlink-material', methods=['POST'])
def unlink_material_from_supplier():
    """Remove link between material and supplier"""
    try:
        material_id = request.form.get('material_id')
        supplier_id = request.form.get('supplier_id')

        link = RawMaterialSupplier.query.filter_by(
            raw_material_id=material_id,
            supplier_id=supplier_id
        ).first()

        if link:
            db.session.delete(link)
            db.session.commit()

            material = RawMaterial.query.get(material_id)
            supplier = Supplier.query.get(supplier_id)
            log_audit("DELETE", "RawMaterialSupplier", None,
                     f"Unlinked {material.name} from {supplier.name}")

            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Link not found'}), 404

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500