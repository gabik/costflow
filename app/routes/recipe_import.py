import os
import tempfile
import json
from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from flask_babel import gettext as _
import pandas as pd
from ..models import db, Product, ProductComponent, RawMaterial, RawMaterialAlternativeName, Category, Supplier, RawMaterialSupplier
from .utils import log_audit, convert_to_base_unit, get_or_create_general_category

recipe_import_blueprint = Blueprint('recipe_import', __name__)

# ----------------------------
# Excel Parsing Utilities
# ----------------------------

def parse_metadata(df):
    """
    Parse metadata from first rows of Excel sheet.
    Expected format:
    # Recipe Import Metadata
    type: premake
    category: בצקים
    unit: g
    """
    metadata = {
        'type': None,
        'category': None,
        'unit': 'g'  # Default
    }

    # Read first 10 rows to find metadata
    for i in range(min(10, len(df))):
        row = df.iloc[i]
        first_cell = str(row.iloc[0]).strip() if not pd.isna(row.iloc[0]) else ''

        # Skip comment lines
        if first_cell.startswith('#'):
            continue

        # Look for key:value patterns
        if ':' in first_cell:
            parts = first_cell.split(':', 1)
            if len(parts) == 2:
                key = parts[0].strip().lower()
                value = parts[1].strip()

                if key == 'type':
                    if value in ['premake', 'product']:
                        metadata['type'] = value
                elif key == 'category':
                    metadata['category'] = value
                elif key == 'unit':
                    metadata['unit'] = value

    return metadata


def find_metadata_end_row(df):
    """Find the row index where metadata ends (first blank row or recipe start)"""
    for i in range(min(15, len(df))):
        row = df.iloc[i]
        first_cell = str(row.iloc[0]).strip() if not pd.isna(row.iloc[0]) else ''

        # Skip comments and metadata
        if first_cell.startswith('#') or (':' in first_cell and i < 10):
            continue

        # Empty row marks end of metadata
        if not first_cell:
            return i + 1

        # First non-metadata, non-empty row is start of recipes
        return i

    return 10  # Default


def parse_recipes_from_sheet(df, metadata_end_row):
    """
    Parse all recipes from Excel sheet.
    Returns list of recipe dictionaries.
    """
    recipes = []
    current_recipe = None
    i = metadata_end_row

    while i < len(df):
        row = df.iloc[i]
        first_cell = str(row.iloc[0]).strip() if not pd.isna(row.iloc[0]) else ''

        # Skip empty rows
        if not first_cell:
            i += 1
            continue

        # Check if this is a recipe title (ends with "תמחור" or is followed by headers)
        if i + 2 < len(df):
            next_row = df.iloc[i + 1]
            next_cell = str(next_row.iloc[0]).strip() if not pd.isna(next_row.iloc[0]) else ''

            # If next row is "חומר גלם" or similar header, this is a recipe title
            if next_cell in ['חומר גלם', 'שם'] or 'חומר' in next_cell:
                # Save previous recipe if exists
                if current_recipe and current_recipe.get('materials'):
                    recipes.append(current_recipe)

                # Start new recipe
                current_recipe = {
                    'name': first_cell,
                    'materials': [],
                    'loss': None,
                    'total_weight': 0,
                    'total_cost': 0
                }

                # Skip header rows (2 rows)
                i += 3
                continue

        # If we're in a recipe, parse material rows
        if current_recipe is not None:
            # Check for total row
            if 'סך הכל' in first_cell or first_cell == 'סך הכל':
                # Save totals
                try:
                    if not pd.isna(row.iloc[3]):  # Weight column
                        current_recipe['total_weight'] = float(row.iloc[3])
                    if not pd.isna(row.iloc[4]):  # Cost column
                        current_recipe['total_cost'] = float(row.iloc[4])
                except (ValueError, IndexError):
                    pass
                i += 1
                continue

            # Check for 100g row (skip it)
            if '100 ג' in first_cell:
                i += 1
                continue

            # Parse material row
            try:
                # Columns: name (0), type (1), price_per_kg (2), weight (3), total_price (4)
                material_name = first_cell
                material_type = str(row.iloc[1]).strip() if not pd.isna(row.iloc[1]) else ''
                price_per_kg = float(row.iloc[2]) if not pd.isna(row.iloc[2]) else 0
                weight = float(row.iloc[3]) if not pd.isna(row.iloc[3]) else 0

                # Check if this is a loss row
                if 'אבדן' in material_type or 'אובדן' in material_type:
                    current_recipe['loss'] = {
                        'name': material_name,
                        'weight': weight  # Already negative in sheet
                    }
                else:
                    current_recipe['materials'].append({
                        'name': material_name,
                        'type': material_type,
                        'price_per_kg': price_per_kg,
                        'weight': weight,
                        'price_per_unit': price_per_kg
                    })
            except (ValueError, IndexError):
                pass

        i += 1

    # Save last recipe
    if current_recipe and current_recipe.get('materials'):
        recipes.append(current_recipe)

    return recipes


def match_material(name, material_type):
    """
    Match material by exact name in appropriate table.
    For raw materials, also tries matching by alternative names.
    Returns: (found, material_id, current_price, db_material, matched_via_alternative)
    """
    found = False
    material_id = None
    current_price = None
    db_material = None
    matched_via_alternative = False

    if material_type == 'חומר גלם':
        # Try primary name first
        db_material = RawMaterial.query.filter_by(name=name, is_deleted=False).first()

        # If not found, try alternative names
        if not db_material:
            alt_name_record = RawMaterialAlternativeName.query.filter_by(alternative_name=name).first()
            if alt_name_record and not alt_name_record.raw_material.is_deleted:
                db_material = alt_name_record.raw_material
                matched_via_alternative = True

        if db_material:
            found = True
            material_id = db_material.id
            # Get primary supplier price if available
            primary_price = None
            for link in db_material.supplier_links:
                if link.is_primary:
                    primary_price = link.cost_per_unit
                    break
            # If no primary, use first supplier
            if not primary_price and db_material.supplier_links:
                primary_price = db_material.supplier_links[0].cost_per_unit
            current_price = primary_price if primary_price else 0

    elif material_type == 'הכנה':
        db_material = Product.query.filter_by(name=name, is_premake=True).first()
        if db_material:
            found = True
            material_id = db_material.id
            # Calculate cost per unit for premake
            from .utils import calculate_premake_cost_per_unit
            current_price = calculate_premake_cost_per_unit(db_material)

    elif material_type == 'מוצר מקדים':
        db_material = Product.query.filter_by(name=name, is_preproduct=True).first()
        if db_material:
            found = True
            material_id = db_material.id
            # Calculate cost for preproduct
            from .utils import calculate_prime_cost
            current_price = calculate_prime_cost(db_material)

    return found, material_id, current_price, db_material, matched_via_alternative


def find_existing_recipe(name, is_premake):
    """Find existing recipe by exact name match"""
    recipe = Product.query.filter_by(name=name, is_premake=is_premake).first()
    if recipe:
        components = ProductComponent.query.filter_by(product_id=recipe.id).all()
        return True, recipe, components
    return False, None, []


def calculate_recipe_diff(existing_components, new_materials, matched_materials_lookup, metadata):
    """
    Calculate diff between existing and new components.
    Uses material IDs instead of names to handle alternative names correctly.

    Args:
        existing_components: List of ProductComponent objects from existing recipe
        new_materials: List of material dicts from Excel sheet
        matched_materials_lookup: Dict mapping material index to matched DB material data
        metadata: Recipe metadata dict containing 'unit' for unit conversion

    Returns: (added, removed, changed, unchanged)
    """
    # Build lookup for existing components using IDs
    existing_lookup = {}
    for comp in existing_components:
        key = None
        if comp.component_type == 'raw_material' and comp.material:
            key = ('raw_material', comp.component_id)  # Use component_id, not material_id!
        elif comp.component_type == 'premake' and comp.premake:
            key = ('premake', comp.component_id)
        elif comp.component_type == 'product' and comp.preproduct:
            key = ('product', comp.component_id)
        elif comp.component_type == 'loss':
            key = ('loss', 0)  # Loss has no ID

        if key:
            existing_lookup[key] = comp

    # Build lookup for new materials using IDs from matched data
    new_lookup = {}
    for idx, mat in enumerate(new_materials):
        # Get matched material data
        matched = matched_materials_lookup.get(idx)
        if matched and matched['found']:
            mat_id = matched['material_id']
            type_map = {
                'חומר גלם': 'raw_material',
                'הכנה': 'premake',
                'מוצר מקדים': 'product'
            }
            comp_type = type_map.get(mat['type'], 'raw_material')
            key = (comp_type, mat_id)
            new_lookup[key] = (mat, idx)  # Store both material and index

    added = []
    removed = []
    changed = []
    unchanged = []

    # Find added and changed
    for key, (new_mat, idx) in new_lookup.items():
        if key not in existing_lookup:
            added.append(new_mat)
        else:
            existing_comp = existing_lookup[key]

            # Get matched material to determine base unit for conversion
            matched = matched_materials_lookup.get(idx)
            material_unit = None

            if matched and matched['found']:
                # Get base unit from the material
                comp_type = key[0]
                mat_id = matched['material_id']

                if comp_type == 'raw_material':
                    db_mat = RawMaterial.query.get(mat_id)
                    material_unit = db_mat.unit if db_mat else 'kg'
                elif comp_type in ['premake', 'product']:
                    db_prod = Product.query.get(mat_id)
                    material_unit = db_prod.unit if db_prod else 'kg'  # Default to kg

            # Convert new quantity to material's base unit for fair comparison
            new_quantity_converted = convert_to_base_unit(
                new_mat['weight'],
                metadata['unit'],  # Recipe unit (e.g., 'g')
                material_unit      # Material's base unit (e.g., 'kg')
            ) if material_unit else new_mat['weight']

            # Handle historical issue with liquid materials imported with wrong conversion
            # If material is in L and sheet is in g/kg, check for 10x factor issue
            if material_unit in ['L', 'l'] and metadata['unit'] in ['g', 'kg']:
                # Check if existing quantity is likely wrong (10x too high)
                # For example: 0.4L instead of 0.04L for 40ml
                ratio = existing_comp.quantity / new_quantity_converted if new_quantity_converted > 0 else 0

                # If ratio is close to 10, this is likely the historical issue
                if 9.5 <= ratio <= 10.5:
                    # Adjust for comparison - the existing data is 10x too high
                    # Don't change the data, just recognize it's not actually different
                    new_quantity_converted = existing_comp.quantity

            # Similar check for ml materials with kg sheet units
            elif material_unit == 'ml' and metadata['unit'] == 'kg':
                # Check for 1000x factor issue (kg to ml direct conversion)
                ratio = existing_comp.quantity / new_quantity_converted if new_quantity_converted > 0 else 0
                if 950 <= ratio <= 1050:
                    new_quantity_converted = existing_comp.quantity

            # Check if quantity changed (compare in same units)
            if abs(existing_comp.quantity - new_quantity_converted) > 0.01:
                changed.append({
                    'material': new_mat,
                    'old_quantity': existing_comp.quantity,
                    'new_quantity': new_mat['weight']  # Keep original for display
                })
            else:
                unchanged.append(new_mat)

    # Find removed components
    for key, existing_comp in existing_lookup.items():
        if key not in new_lookup and key[0] != 'loss':  # Don't count loss as removed
            # Get the component name for display
            comp_name = None
            if existing_comp.component_type == 'raw_material' and existing_comp.material:
                comp_name = existing_comp.material.name
            elif existing_comp.component_type == 'premake' and existing_comp.premake:
                comp_name = existing_comp.premake.name
            elif existing_comp.component_type == 'product' and existing_comp.preproduct:
                comp_name = existing_comp.preproduct.name

            if comp_name:
                removed.append({'name': comp_name, 'type': key[0]})

    return added, removed, changed, unchanged


def calculate_100g_cost(total_weight, total_cost):
    """Calculate cost per 100g"""
    if total_weight > 0:
        return (total_cost / total_weight) * 100
    return 0


# ----------------------------
# AJAX API Endpoints
# ----------------------------

@recipe_import_blueprint.route('/api/recipe_import/create_supplier', methods=['POST'])
def create_supplier_ajax():
    """AJAX endpoint to create supplier during import"""
    try:
        # Get form data
        name = request.form.get('name', '').strip()
        contact_person = request.form.get('contact_person', '').strip()
        phone = request.form.get('phone', '').strip()
        email = request.form.get('email', '').strip()
        discount_percentage = float(request.form.get('discount_percentage', 0))

        # Validate required fields
        if not name:
            return jsonify({'success': False, 'error': _('Supplier name is required')}), 400

        # Check if supplier already exists
        existing = Supplier.query.filter_by(name=name).first()
        if existing:
            return jsonify({'success': False, 'error': _('Supplier with this name already exists')}), 400

        # Create new supplier
        new_supplier = Supplier(
            name=name,
            contact_person=contact_person if contact_person else None,
            phone=phone if phone else None,
            email=email if email else None,
            discount_percentage=discount_percentage,
            is_active=True
        )

        db.session.add(new_supplier)
        db.session.commit()

        # Track new supplier in session
        new_suppliers = session.get('recipe_import_new_suppliers', [])
        new_suppliers.append(new_supplier.id)
        session['recipe_import_new_suppliers'] = new_suppliers

        # Log audit
        log_audit("CREATE", "Supplier", new_supplier.id, f"Created supplier during recipe import: {name}")

        return jsonify({
            'success': True,
            'supplier_id': new_supplier.id,
            'supplier_name': new_supplier.name,
            'discount_percentage': new_supplier.discount_percentage
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@recipe_import_blueprint.route('/api/recipe_import/create_material', methods=['POST'])
def create_material_ajax():
    """AJAX endpoint to create material during import"""
    try:
        # Get form data
        name = request.form.get('name', '').strip()
        category_id = request.form.get('category_id')
        unit = request.form.get('unit', 'kg')
        supplier_id = request.form.get('supplier_id')
        sku = request.form.get('sku', '').strip()
        price = float(request.form.get('price', 0))

        # Validate required fields (SKU is optional)
        if not all([name, category_id, unit, supplier_id]):
            return jsonify({'success': False, 'error': _('Name, category, unit and supplier are required')}), 400

        # Check if material already exists
        existing = RawMaterial.query.filter_by(name=name, is_deleted=False).first()
        if existing:
            return jsonify({'success': False, 'error': _('Material with this name already exists')}), 400

        # Check alternative names
        alt_name = RawMaterialAlternativeName.query.filter_by(alternative_name=name).first()
        if alt_name and not alt_name.raw_material.is_deleted:
            return jsonify({
                'success': False,
                'error': _('This name is already used as an alternative for: {}').format(alt_name.raw_material.name)
            }), 400

        # Create new material
        new_material = RawMaterial(
            name=name,
            category_id=int(category_id),
            unit=unit,
            is_unlimited=False,
            is_deleted=False
        )

        db.session.add(new_material)
        db.session.flush()  # Get ID for supplier link

        # Apply supplier discount if any
        supplier = Supplier.query.get(int(supplier_id))
        if supplier and supplier.discount_percentage > 0:
            discounted_price = price * (1 - supplier.discount_percentage / 100)
        else:
            discounted_price = price

        # Create supplier link with SKU
        supplier_link = RawMaterialSupplier(
            raw_material_id=new_material.id,
            supplier_id=int(supplier_id),
            cost_per_unit=discounted_price,
            is_primary=True,  # First supplier is primary
            sku=sku if sku else None
        )

        db.session.add(supplier_link)
        db.session.commit()

        # Track created material in session
        created_materials = session.get('recipe_import_created_materials', [])
        created_materials.append(new_material.id)
        session['recipe_import_created_materials'] = created_materials

        # Log audit
        log_audit("CREATE", "RawMaterial", new_material.id, f"Created material during recipe import: {name}")

        return jsonify({
            'success': True,
            'material_id': new_material.id,
            'material_name': new_material.name,
            'final_price': discounted_price
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@recipe_import_blueprint.route('/api/recipe_import/get_categories', methods=['GET'])
def get_categories_ajax():
    """Get categories for dropdown"""
    try:
        categories = Category.query.filter_by(type='raw_material').order_by(Category.name).all()

        return jsonify({
            'success': True,
            'categories': [
                {'id': cat.id, 'name': cat.name}
                for cat in categories
            ]
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@recipe_import_blueprint.route('/api/recipe_import/get_suppliers', methods=['GET'])
def get_suppliers_ajax():
    """Get all active suppliers for dropdown"""
    try:
        suppliers = Supplier.query.filter_by(is_active=True).order_by(Supplier.name).all()

        # Include newly created suppliers from session if any
        new_supplier_ids = session.get('recipe_import_new_suppliers', [])

        return jsonify({
            'success': True,
            'suppliers': [
                {
                    'id': sup.id,
                    'name': sup.name,
                    'discount_percentage': sup.discount_percentage,
                    'is_new': sup.id in new_supplier_ids
                }
                for sup in suppliers
            ]
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@recipe_import_blueprint.route('/api/recipe_import/validate_material_name', methods=['POST'])
def validate_material_name():
    """Check if material name already exists"""
    try:
        name = request.form.get('name', '').strip()

        if not name:
            return jsonify({'exists': False})

        # Check main material name
        material = RawMaterial.query.filter_by(name=name, is_deleted=False).first()
        if material:
            return jsonify({
                'exists': True,
                'material_id': material.id,
                'message': _('Material with this name already exists')
            })

        # Check alternative names
        alt_name = RawMaterialAlternativeName.query.filter_by(alternative_name=name).first()
        if alt_name and not alt_name.raw_material.is_deleted:
            return jsonify({
                'exists': True,
                'material_id': alt_name.raw_material.id,
                'message': _('This name is used as alternative for: {}').format(alt_name.raw_material.name)
            })

        return jsonify({'exists': False})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@recipe_import_blueprint.route('/api/recipe_import/create_category', methods=['POST'])
def create_category_ajax():
    """AJAX endpoint to create category during import"""
    try:
        name = request.form.get('name', '').strip()
        type_val = request.form.get('type', 'raw_material')

        # Validate
        if not name:
            return jsonify({'success': False, 'error': _('Category name is required')}), 400

        # Check if exists
        existing = Category.query.filter_by(name=name, type=type_val).first()
        if existing:
            return jsonify({'success': True, 'category_id': existing.id, 'category_name': existing.name})

        # Create new category
        new_category = Category(name=name, type=type_val)
        db.session.add(new_category)
        db.session.commit()

        # Log audit
        log_audit("CREATE", "Category", new_category.id, f"Created category during recipe import: {name}")

        return jsonify({
            'success': True,
            'category_id': new_category.id,
            'category_name': new_category.name
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# ----------------------------
# Routes
# ----------------------------

@recipe_import_blueprint.route('/recipes/upload', methods=['GET', 'POST'])
def upload_recipes():
    """Upload Excel file and select sheet"""
    if request.method == 'POST':
        # Clean up any previous temp files from abandoned imports
        old_temp_file = session.get('recipe_temp_file')
        old_data_file = session.get('recipe_data_file')
        if old_temp_file and os.path.exists(old_temp_file):
            os.remove(old_temp_file)
        if old_data_file and os.path.exists(old_data_file):
            os.remove(old_data_file)
        session.pop('recipe_temp_file', None)
        session.pop('recipe_data_file', None)

        if 'recipe_file' not in request.files:
            flash('לא נבחר קובץ', 'error')
            return redirect(request.url)

        file = request.files['recipe_file']
        if file.filename == '':
            flash('לא נבחר קובץ', 'error')
            return redirect(request.url)

        if file:
            try:
                # Save file temporarily
                temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
                file.save(temp_file.name)
                temp_file.close()

                # Store temp file path in session
                session['recipe_temp_file'] = temp_file.name

                # Get sheet names
                excel_file = pd.ExcelFile(temp_file.name)
                sheet_names = excel_file.sheet_names

                # Parse metadata from first sheet for preview
                first_sheet_df = pd.read_excel(temp_file.name, sheet_name=sheet_names[0], header=None)
                metadata = parse_metadata(first_sheet_df)

                return render_template('recipe_upload.html',
                                     sheet_names=sheet_names,
                                     metadata_preview=metadata,
                                     file_uploaded=True)

            except Exception as e:
                flash(f'שגיאה בקריאת הקובץ: {str(e)}', 'error')
                return redirect(request.url)

    return render_template('recipe_upload.html', file_uploaded=False)


@recipe_import_blueprint.route('/recipes/get_sheet_metadata', methods=['POST'])
def get_sheet_metadata():
    """AJAX endpoint to get metadata for a specific sheet"""
    sheet_name = request.form.get('sheet_name')
    temp_file = session.get('recipe_temp_file')

    if not temp_file or not os.path.exists(temp_file):
        return {'error': 'הקובץ לא נמצא'}, 400

    try:
        # Read sheet
        df = pd.read_excel(temp_file, sheet_name=sheet_name, header=None)

        # Parse metadata
        metadata = parse_metadata(df)

        # Check if category exists
        category_exists = True
        category_type = None
        if metadata['category']:
            category_type = 'premake' if metadata['type'] == 'premake' else 'product'
            category = Category.query.filter_by(name=metadata['category'], type=category_type).first()
            category_exists = category is not None

        return {
            'type': metadata['type'] or '',
            'category': metadata['category'] or '',
            'unit': metadata['unit'],
            'category_exists': category_exists,
            'category_type': category_type
        }
    except Exception as e:
        return {'error': str(e)}, 500


@recipe_import_blueprint.route('/recipes/select_sheet', methods=['POST'])
def select_sheet():
    """Parse selected sheet and show review page"""
    sheet_name = request.form.get('sheet_name')
    temp_file = session.get('recipe_temp_file')

    if not temp_file or not os.path.exists(temp_file):
        flash('הקובץ לא נמצא, אנא העלה מחדש', 'error')
        return redirect(url_for('recipe_import.upload_recipes'))

    try:
        # Read sheet
        df = pd.read_excel(temp_file, sheet_name=sheet_name, header=None)

        # Parse metadata
        metadata = parse_metadata(df)

        # Validate metadata
        if not metadata['type']:
            flash('חסר שדה type במטאדאטה', 'error')
            return redirect(url_for('recipe_import.upload_recipes'))

        if not metadata['category']:
            flash('חסר שדה category במטאדאטה', 'error')
            return redirect(url_for('recipe_import.upload_recipes'))

        # Validate category exists
        category_type = 'premake' if metadata['type'] == 'premake' else 'product'
        category = Category.query.filter_by(name=metadata['category'], type=category_type).first()
        if not category:
            flash(f'הקטגוריה "{metadata["category"]}" לא נמצאה במערכת', 'error')
            return redirect(url_for('recipe_import.upload_recipes'))

        # Parse recipes
        metadata_end_row = find_metadata_end_row(df)
        recipes = parse_recipes_from_sheet(df, metadata_end_row)

        if not recipes:
            flash('לא נמצאו מתכונים בגיליון', 'error')
            return redirect(url_for('recipe_import.upload_recipes'))

        # Query all available materials for dropdowns
        all_raw_materials = RawMaterial.query.filter_by(is_deleted=False).order_by(RawMaterial.name).all()
        all_premakes = Product.query.filter_by(is_premake=True, is_archived=False).order_by(Product.name).all()
        all_preproducts = Product.query.filter_by(is_preproduct=True, is_archived=False).order_by(Product.name).all()

        # Query categories and suppliers for material creation
        all_categories = Category.query.filter_by(type='raw_material').order_by(Category.name).all()
        all_suppliers = Supplier.query.filter_by(is_active=True).order_by(Supplier.name).all()

        # Process each recipe
        recipes_review_data = []
        all_missing_materials = []

        for recipe in recipes:
            # Check if recipe exists
            is_premake = metadata['type'] == 'premake'
            exists, existing_recipe, existing_components = find_existing_recipe(recipe['name'], is_premake)

            # Match all materials
            materials_data = []
            has_missing = False

            for material in recipe['materials']:
                found, mat_id, current_price, db_material, matched_via_alternative = match_material(
                    material['name'],
                    material['type']
                )

                price_differs = False
                if found and current_price:
                    # Compare prices (allow 0.01 difference for rounding)
                    if abs(current_price - material['price_per_kg']) > 0.01:
                        price_differs = True

                materials_data.append({
                    'name': material['name'],
                    'matched_via_alternative': matched_via_alternative,
                    'type': material['type'],
                    'weight': material['weight'],
                    'sheet_price': material['price_per_kg'],
                    'db_price': current_price,
                    'found': found,
                    'material_id': mat_id,
                    'price_differs': price_differs
                })

                if not found:
                    has_missing = True
                    all_missing_materials.append({
                        'name': material['name'],
                        'type': material['type']
                    })

            # Calculate diff if exists
            diff_data = None
            if exists:
                # Build matched materials lookup for diff calculation
                matched_materials_lookup = {}
                for idx, material_data in enumerate(materials_data):
                    matched_materials_lookup[idx] = material_data

                added, removed, changed, unchanged = calculate_recipe_diff(
                    existing_components,
                    recipe['materials'],
                    matched_materials_lookup,
                    metadata
                )
                diff_data = {
                    'added': added,
                    'removed': removed,
                    'changed': changed,
                    'unchanged': unchanged
                }

            # Calculate 100g cost
            net_weight = recipe['total_weight']
            if recipe['loss']:
                net_weight += recipe['loss']['weight']  # Loss is negative

            cost_100g = calculate_100g_cost(net_weight, recipe['total_cost'])

            # Determine if recipe has only price differences (NEW)
            has_actual_changes = False
            has_price_differences = any(mat['price_differs'] for mat in materials_data if mat['found'])

            if exists and diff_data:
                has_actual_changes = bool(diff_data['added'] or diff_data['removed'] or diff_data['changed'])

            recipes_review_data.append({
                'name': recipe['name'],
                'exists': exists,
                'materials': materials_data,
                'loss': recipe['loss'],
                'total_weight': recipe['total_weight'],
                'total_cost': recipe['total_cost'],
                'net_weight': net_weight,
                'cost_100g': cost_100g,
                'has_missing': has_missing,
                'has_actual_changes': has_actual_changes,  # NEW
                'has_price_only': has_price_differences and not has_actual_changes,  # NEW
                'diff': diff_data
            })

        # Store data in temporary JSON file (session cookie has 4KB limit!)
        temp_data_file = temp_file.replace('.xlsx', '_data.json').replace('.xls', '_data.json')
        with open(temp_data_file, 'w', encoding='utf-8') as f:
            json.dump({
                'metadata': metadata,
                'category_id': category.id,
                'recipes': recipes
            }, f, ensure_ascii=False)

        session['recipe_data_file'] = temp_data_file

        return render_template('recipe_review.html',
                             metadata=metadata,
                             category=category,
                             recipes=recipes_review_data,
                             has_missing_materials=len(all_missing_materials) > 0,
                             missing_materials=all_missing_materials,
                             all_raw_materials=all_raw_materials,
                             all_premakes=all_premakes,
                             all_preproducts=all_preproducts,
                             all_categories=all_categories,
                             all_suppliers=all_suppliers)

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"ERROR IN RECIPE REVIEW: {str(e)}")
        print(f"TRACEBACK: {error_details}")
        flash(f'שגיאה בעיבוד הגיליון: {str(e)}', 'error')
        # Return error page with details for debugging
        return f"""
        <h1>Error in Recipe Review</h1>
        <pre>{error_details}</pre>
        <p>Error: {str(e)}</p>
        <a href="/recipes/upload">Go Back</a>
        """, 500


@recipe_import_blueprint.route('/recipes/confirm', methods=['POST'])
def confirm_import():
    """Confirm and save recipes to database"""
    temp_data_file = session.get('recipe_data_file')

    if not temp_data_file or not os.path.exists(temp_data_file):
        flash('נתוני הייבוא לא נמצאו, אנא התחל מחדש', 'error')
        return redirect(url_for('recipe_import.upload_recipes'))

    # Read recipe data from temporary JSON file
    try:
        with open(temp_data_file, 'r', encoding='utf-8') as f:
            import_data = json.load(f)
    except Exception as e:
        flash(f'שגיאה בקריאת נתוני הייבוא: {str(e)}', 'error')
        return redirect(url_for('recipe_import.upload_recipes'))

    # Extract material mappings from form
    material_mappings = {}
    for key in request.form:
        if key.startswith('mapping_'):
            mapping_id = request.form[key]
            if mapping_id:  # Not empty
                material_mappings[key.replace('mapping_', '')] = int(mapping_id)

    # Extract weight modifications from form
    weight_modifications = {}
    for key in request.form:
        if key.startswith('weight_'):
            weight_value = request.form[key]
            if weight_value:
                weight_modifications[key.replace('weight_', '')] = float(weight_value)

    # Extract selected recipes from form
    selected_recipe_indices = set()
    for key in request.form:
        if key.startswith('import_recipe'):
            idx_str = key.replace('import_recipe', '')
            if idx_str.isdigit():
                selected_recipe_indices.add(int(idx_str))

    try:
        metadata = import_data['metadata']
        category_id = import_data['category_id']
        recipes = import_data['recipes']

        # Check if any recipes were selected
        if not selected_recipe_indices:
            flash('לא נבחרו מתכונים לייבוא', 'warning')
            return redirect(url_for('recipe_import.upload_recipes'))

        is_premake = metadata['type'] == 'premake'
        created_count = 0
        updated_count = 0
        skipped_recipes = []
        skipped_materials = []
        created_materials_count = 0

        # Track materials created during this import session
        created_material_ids = session.get('recipe_import_created_materials', [])

        # Process only selected recipes, using original indices for form field lookups
        for recipe_idx in sorted(selected_recipe_indices):
            recipe = recipes[recipe_idx]
            # Check if exists
            existing_recipe = Product.query.filter_by(
                name=recipe['name'],
                is_premake=is_premake
            ).first()

            if existing_recipe:
                # Update existing
                product = existing_recipe
                product.category_id = category_id
                # Convert batch size to kg if needed
                batch_size_kg = convert_to_base_unit(
                    recipe['total_weight'],
                    metadata.get('unit', 'g'),
                    'kg'
                )
                product.batch_size = batch_size_kg
                product.unit = 'kg'  # Always store in kg internally

                # Clear old components
                ProductComponent.query.filter_by(product_id=product.id).delete()
                updated_count += 1
            else:
                # Create new
                # Convert batch size to kg if needed
                batch_size_kg = convert_to_base_unit(
                    recipe['total_weight'],
                    metadata.get('unit', 'g'),
                    'kg'
                )
                product = Product(
                    name=recipe['name'],
                    category_id=category_id,
                    batch_size=batch_size_kg,
                    unit='kg',  # Always store in kg internally
                    products_per_recipe=1,
                    is_product=not is_premake,
                    is_premake=is_premake,
                    is_preproduct=False
                )
                db.session.add(product)
                db.session.flush()
                created_count += 1

            # Add components
            for material_idx, material in enumerate(recipe['materials']):
                # Check for mapping first
                mapping_key = f"recipe{recipe_idx}_mat{material_idx}"
                found = False
                mat_id = None

                if mapping_key in material_mappings:
                    # Use mapped material ID
                    mat_id = material_mappings[mapping_key]
                    found = True
                    original_name = material['name']  # Name from Excel

                    # Validate material type matches
                    if material['type'] == 'חומר גלם':
                        db_mat = RawMaterial.query.get(mat_id)
                        found = db_mat is not None and not db_mat.is_deleted

                        # Auto-add alternative name for raw materials
                        if found and db_mat.name != original_name:
                            # Check if alternative name already exists
                            existing_alt = RawMaterialAlternativeName.query.filter_by(
                                alternative_name=original_name
                            ).first()

                            if existing_alt:
                                # Check if it's for the same material
                                if existing_alt.raw_material_id != mat_id:
                                    # Error: name already exists for another material
                                    flash(f'שם חלופי "{original_name}" כבר קיים עבור חומר אחר: {existing_alt.raw_material.name}', 'error')
                                    db.session.rollback()
                                    return redirect(url_for('recipe_import.upload_recipes'))
                                # else: Alternative name already exists for this material, skip adding

                            # Only add if not already an alternative (including the check above)
                            elif not any(alt.alternative_name == original_name for alt in db_mat.alternative_names):
                                # Add new alternative name
                                new_alt = RawMaterialAlternativeName(
                                    raw_material_id=mat_id,
                                    alternative_name=original_name
                                )
                                db.session.add(new_alt)
                                # Note: Will be committed with the rest of the import

                    elif material['type'] == 'הכנה':
                        db_mat = Product.query.filter_by(id=mat_id, is_premake=True).first()
                        found = db_mat is not None
                    elif material['type'] == 'מוצר מקדים':
                        db_mat = Product.query.filter_by(id=mat_id, is_preproduct=True).first()
                        found = db_mat is not None
                else:
                    # Try original exact match
                    found, mat_id, _, _, _ = match_material(material['name'], material['type'])

                if not found:
                    # Track skipped material
                    skipped_materials.append({
                        'material_name': material['name'],
                        'recipe_name': recipe['name'],
                        'reason': _('Material not found or invalid mapping')
                    })
                    # Skip this recipe entirely
                    skipped_recipes.append({
                        'recipe_name': recipe['name'],
                        'reason': _('Missing material: {}').format(material['name'])
                    })
                    # Continue to next recipe
                    break

                # Determine component type
                type_map = {
                    'חומר גלם': 'raw_material',
                    'הכנה': 'premake',
                    'מוצר מקדים': 'product'
                }
                comp_type = type_map.get(material['type'], 'raw_material')

                # Check for weight modification
                weight_key = f"recipe{recipe_idx}_mat{material_idx}"
                quantity = weight_modifications.get(weight_key, material['weight'])

                # Get material's base unit and convert quantity
                material_unit = None
                if comp_type == 'raw_material':
                    db_mat = RawMaterial.query.get(mat_id)
                    material_unit = db_mat.unit if db_mat else 'kg'
                elif comp_type in ['premake', 'product']:
                    db_prod = Product.query.get(mat_id)
                    material_unit = db_prod.unit if db_prod else 'kg'  # Default to kg

                # Convert from Excel unit to material's base unit
                final_quantity = convert_to_base_unit(
                    quantity,
                    metadata['unit'],  # e.g., 'g' from Excel
                    material_unit      # e.g., 'kg' from DB
                )

                component = ProductComponent(
                    product_id=product.id,
                    component_type=comp_type,
                    component_id=mat_id,
                    quantity=final_quantity
                )
                db.session.add(component)

            # Add loss component if exists
            if recipe.get('loss'):
                # Convert loss weight from Excel unit to kg
                final_loss_quantity = convert_to_base_unit(
                    recipe['loss']['weight'],  # Negative value
                    metadata['unit'],          # e.g., 'g' from Excel
                    'kg'                       # Store in kg for consistency
                )

                loss_component = ProductComponent(
                    product_id=product.id,
                    component_type='loss',
                    component_id=0,  # No reference needed
                    quantity=final_loss_quantity
                )
                db.session.add(loss_component)

            # Log audit
            action = "UPDATE" if existing_recipe else "CREATE"
            log_audit(action, "Recipe Import", product.id, f"Imported recipe: {recipe['name']}")

        db.session.commit()

        # Clean up temporary files
        temp_file = session.get('recipe_temp_file')
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)
        if temp_data_file and os.path.exists(temp_data_file):
            os.remove(temp_data_file)
        session.pop('recipe_temp_file', None)
        session.pop('recipe_data_file', None)

        # Build comprehensive summary message
        summary_parts = []

        if created_count > 0 or updated_count > 0:
            summary_parts.append(f'{created_count} מתכונים חדשים, {updated_count} מתכונים עודכנו')

        if created_materials_count > 0:
            summary_parts.append(f'{created_materials_count} חומרי גלם נוצרו')

        if skipped_recipes:
            summary_parts.append(f'{len(skipped_recipes)} מתכונים דולגו')

        # Main success/warning message
        if summary_parts:
            if skipped_recipes:
                flash(f'ייבוא הושלם עם אזהרות: {", ".join(summary_parts)}', 'warning')
            else:
                flash(f'ייבוא הושלם בהצלחה: {", ".join(summary_parts)}', 'success')
        else:
            flash('לא בוצעו שינויים', 'info')

        # Show details of skipped items if any
        if skipped_recipes:
            skipped_list = '<br>'.join([f'• {item["recipe_name"]}: {item["reason"]}' for item in skipped_recipes])
            flash(f'המתכונים הבאים דולגו:<br>{skipped_list}', 'warning')

        # Clear session data
        session.pop('recipe_import_new_suppliers', None)
        session.pop('recipe_import_created_materials', None)

        # Redirect based on type
        if is_premake:
            return redirect(url_for('premakes.premakes'))
        else:
            return redirect(url_for('products.products'))

    except Exception as e:
        db.session.rollback()
        flash(f'שגיאה בשמירת המתכונים: {str(e)}', 'error')
        return redirect(url_for('recipe_import.upload_recipes'))
