"""
CRITICAL UNIT SYSTEM FIX MIGRATION
This migration fixes all unit conversion issues in the database.
Target: Everything stored in kg (or L for liquids), conversions only in UI.
"""

from flask import Blueprint, request, render_template, redirect, url_for, flash
from flask_babel import gettext as _
from sqlalchemy import text
from ..models import db, Product, ProductComponent, RawMaterial
from .utils import log_audit
import json

unit_fix_blueprint = Blueprint('unit_fix', __name__)

@unit_fix_blueprint.route('/migrate_complete_unit_fix', methods=['GET', 'POST'])
def migrate_complete_unit_fix():
    """
    Complete migration to fix ALL unit issues:
    1. Update all products/premakes to unit='kg'
    2. Fix components stored as grams (1000.0 -> 1.0)
    3. Fix batch sizes that are in grams
    """

    if request.method == 'GET':
        # Analyze current state
        analysis = {
            'products_with_g': [],
            'components_to_fix': [],
            'batch_sizes_to_fix': [],
            'total_fixes_needed': 0
        }

        # Find all products/premakes with unit='g'
        products_with_g = Product.query.filter(Product.unit == 'g').all()
        for product in products_with_g:
            analysis['products_with_g'].append({
                'id': product.id,
                'name': product.name,
                'type': 'premake' if product.is_premake else 'product',
                'unit': product.unit,
                'batch_size': product.batch_size
            })

        # Find components with suspiciously high quantities (> 100)
        suspicious_components = ProductComponent.query.filter(
            ProductComponent.quantity > 100
        ).all()

        for comp in suspicious_components:
            product = Product.query.get(comp.product_id)
            if not product:
                continue

            component_info = {
                'product_id': product.id,
                'product_name': product.name,
                'component_id': comp.id,
                'component_type': comp.component_type,
                'quantity': comp.quantity,
                'suggested_fix': comp.quantity / 1000
            }

            if comp.component_type == 'raw_material' and comp.material:
                component_info['material_name'] = comp.material.name
            elif comp.component_type == 'premake':
                premake = Product.query.get(comp.component_id)
                if premake:
                    component_info['material_name'] = premake.name

            analysis['components_to_fix'].append(component_info)

        # Check batch sizes that might need fixing
        products_with_large_batch = Product.query.filter(
            Product.batch_size > 100
        ).all()

        for product in products_with_large_batch:
            analysis['batch_sizes_to_fix'].append({
                'id': product.id,
                'name': product.name,
                'batch_size': product.batch_size,
                'suggested_fix': product.batch_size / 1000
            })

        analysis['total_fixes_needed'] = (
            len(analysis['products_with_g']) +
            len(analysis['components_to_fix']) +
            len(analysis['batch_sizes_to_fix'])
        )

        return render_template('unit_fix_migration.html', analysis=analysis)

    # POST - Execute the migration
    if request.form.get('confirm') != 'yes':
        return redirect(url_for('products.products'))

    try:
        fixes_log = {
            'units_updated': 0,
            'components_fixed': 0,
            'batch_sizes_fixed': 0,
            'details': []
        }

        # PHASE 1: Update ALL products/premakes to unit='kg'
        products_to_update = Product.query.filter(Product.unit == 'g').all()
        for product in products_to_update:
            old_unit = product.unit
            product.unit = 'kg'
            fixes_log['units_updated'] += 1
            fixes_log['details'].append(f"Updated {product.name} unit: g -> kg")

        # PHASE 2: Fix component quantities > 100 (divide by 1000)
        components_to_fix = ProductComponent.query.filter(
            ProductComponent.quantity > 100,
            ProductComponent.component_type.in_(['raw_material', 'premake'])
        ).all()

        for comp in components_to_fix:
            old_quantity = comp.quantity
            comp.quantity = comp.quantity / 1000
            fixes_log['components_fixed'] += 1

            product = Product.query.get(comp.product_id)
            if product:
                fixes_log['details'].append(
                    f"Fixed component in {product.name}: {old_quantity} -> {comp.quantity}"
                )

        # PHASE 3: Fix batch sizes > 100 (divide by 1000)
        products_with_large_batch = Product.query.filter(
            Product.batch_size > 100
        ).all()

        for product in products_with_large_batch:
            old_batch_size = product.batch_size
            product.batch_size = product.batch_size / 1000
            fixes_log['batch_sizes_fixed'] += 1
            fixes_log['details'].append(
                f"Fixed batch size for {product.name}: {old_batch_size} -> {product.batch_size}"
            )

        # PHASE 4: Fix loss components (negative quantities)
        loss_components = ProductComponent.query.filter(
            ProductComponent.component_type == 'loss',
            ProductComponent.quantity < -100  # Negative loss > 100g
        ).all()

        for comp in loss_components:
            old_quantity = comp.quantity
            comp.quantity = comp.quantity / 1000
            product = Product.query.get(comp.product_id)
            if product:
                fixes_log['details'].append(
                    f"Fixed loss in {product.name}: {old_quantity} -> {comp.quantity}"
                )

        # Commit all changes
        db.session.commit()

        # Log the migration
        log_audit(
            "COMPLETE_UNIT_FIX",
            "System",
            None,
            json.dumps(fixes_log, ensure_ascii=False)
        )

        return render_template('unit_fix_complete.html', fixes_log=fixes_log)

    except Exception as e:
        db.session.rollback()
        log_audit("MIGRATION_ERROR", "System", None, f"Unit fix failed: {str(e)}")
        return f"Migration failed: {str(e)}", 500


@unit_fix_blueprint.route('/verify_unit_baseline')
def verify_unit_baseline():
    """Quick verification that everything is in kg baseline"""

    # Count products with wrong units
    products_with_g = Product.query.filter(Product.unit == 'g').count()
    products_with_kg = Product.query.filter(Product.unit == 'kg').count()

    # Count suspicious component quantities
    suspicious_components = ProductComponent.query.filter(
        ProductComponent.quantity > 100,
        ProductComponent.component_type.in_(['raw_material', 'premake'])
    ).count()

    # Get sample of products with their components
    sample_products = Product.query.limit(10).all()
    product_samples = []
    for product in sample_products:
        components_info = []
        for comp in product.components:
            comp_info = {
                'type': comp.component_type,
                'quantity': comp.quantity
            }
            if comp.component_type == 'raw_material' and comp.material:
                comp_info['name'] = comp.material.name
            elif comp.component_type == 'premake':
                premake = Product.query.get(comp.component_id)
                comp_info['name'] = premake.name if premake else 'Unknown'
            components_info.append(comp_info)

        product_samples.append({
            'name': product.name,
            'unit': product.unit,
            'batch_size': product.batch_size,
            'components': components_info
        })

    return f'''
    <html dir="rtl" lang="he">
    <head>
        <title>Unit Baseline Verification</title>
        <style>
            body {{ font-family: monospace; margin: 40px; direction: rtl; }}
            .good {{ color: green; font-weight: bold; }}
            .bad {{ color: red; font-weight: bold; }}
            .sample {{ background: #f5f5f5; padding: 10px; margin: 10px 0; }}
        </style>
    </head>
    <body>
        <h1>Unit Baseline Quick Check</h1>

        <div>
            <h2>Statistics:</h2>
            <ul>
                <li class="{'bad' if products_with_g > 0 else 'good'}">
                    Products with unit='g': {products_with_g}
                </li>
                <li class="{'good' if products_with_kg > 0 else 'bad'}">
                    Products with unit='kg': {products_with_kg}
                </li>
                <li class="{'bad' if suspicious_components > 0 else 'good'}">
                    Components > 100 (suspicious): {suspicious_components}
                </li>
            </ul>
        </div>

        <div>
            <h2>Sample Products (First 10):</h2>
            {''.join([
                f'<div class="sample">' +
                f'<strong>{p["name"]}</strong> - Unit: {p["unit"]} - Batch: {p["batch_size"]}<br>' +
                'Components:<ul>' +
                ''.join([
                    f'<li>{c.get("name", c["type"])}: {c["quantity"]:.3f} kg</li>'
                    for c in p['components']
                ]) +
                '</ul></div>'
                for p in product_samples
            ])}
        </div>

        <p class="{'good' if products_with_g == 0 and suspicious_components == 0 else 'bad'}">
            {'✅ All good! Everything in kg baseline' if products_with_g == 0 and suspicious_components == 0
             else '❌ Issues found - run migration!'}
        </p>

        <a href="/migrate_complete_unit_fix">Run Complete Unit Fix</a> |
        <a href="/verify_units">Detailed Verification</a> |
        <a href="/">Dashboard</a>
    </body>
    </html>
    '''