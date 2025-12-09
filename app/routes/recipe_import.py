import os
import tempfile
import json
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
import pandas as pd
from ..models import db, Product, ProductComponent, RawMaterial, Category
from .utils import log_audit, convert_to_base_unit

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
    Returns: (found, material_id, current_price, db_material)
    """
    found = False
    material_id = None
    current_price = None
    db_material = None

    if material_type == 'חומר גלם':
        db_material = RawMaterial.query.filter_by(name=name, is_deleted=False).first()
        if db_material:
            found = True
            material_id = db_material.id
            # Get primary supplier price if available
            primary_price = None
            for link in db_material.supplier_links:
                if link.is_primary:
                    primary_price = link.cost_per_unit
                    break
            current_price = primary_price if primary_price else db_material.cost_per_unit

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

    return found, material_id, current_price, db_material


def find_existing_recipe(name, is_premake):
    """Find existing recipe by exact name match"""
    recipe = Product.query.filter_by(name=name, is_premake=is_premake).first()
    if recipe:
        components = ProductComponent.query.filter_by(product_id=recipe.id).all()
        return True, recipe, components
    return False, None, []


def calculate_recipe_diff(existing_components, new_materials):
    """
    Calculate diff between existing and new components.
    Returns: (added, removed, changed, unchanged)
    """
    # Build lookup for existing components
    existing_lookup = {}
    for comp in existing_components:
        key = None
        if comp.component_type == 'raw_material' and comp.material:
            key = ('raw_material', comp.material.name)
        elif comp.component_type == 'premake' and comp.premake:
            key = ('premake', comp.premake.name)
        elif comp.component_type == 'product' and comp.preproduct:
            key = ('product', comp.preproduct.name)
        elif comp.component_type == 'loss':
            key = ('loss', 'loss')

        if key:
            existing_lookup[key] = comp

    # Build lookup for new materials
    new_lookup = {}
    for mat in new_materials:
        type_map = {
            'חומר גלם': 'raw_material',
            'הכנה': 'premake',
            'מוצר מקדים': 'product'
        }
        comp_type = type_map.get(mat['type'], 'raw_material')
        key = (comp_type, mat['name'])
        new_lookup[key] = mat

    added = []
    removed = []
    changed = []
    unchanged = []

    # Find added and changed
    for key, new_mat in new_lookup.items():
        if key not in existing_lookup:
            added.append(new_mat)
        else:
            existing_comp = existing_lookup[key]
            # Check if quantity changed
            if abs(existing_comp.quantity - new_mat['weight']) > 0.01:
                changed.append({
                    'material': new_mat,
                    'old_quantity': existing_comp.quantity,
                    'new_quantity': new_mat['weight']
                })
            else:
                unchanged.append(new_mat)

    # Find removed
    for key, existing_comp in existing_lookup.items():
        if key not in new_lookup and key[0] != 'loss':  # Don't count loss as removed
            comp_name = key[1]
            removed.append({'name': comp_name, 'type': key[0]})

    return added, removed, changed, unchanged


def calculate_100g_cost(total_weight, total_cost):
    """Calculate cost per 100g"""
    if total_weight > 0:
        return (total_cost / total_weight) * 100
    return 0


# ----------------------------
# Routes
# ----------------------------

@recipe_import_blueprint.route('/recipes/upload', methods=['GET', 'POST'])
def upload_recipes():
    """Upload Excel file and select sheet"""
    if request.method == 'POST':
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
        all_premakes = Product.query.filter_by(is_premake=True).order_by(Product.name).all()
        all_preproducts = Product.query.filter_by(is_preproduct=True).order_by(Product.name).all()

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
                found, mat_id, current_price, db_material = match_material(
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
                added, removed, changed, unchanged = calculate_recipe_diff(
                    existing_components,
                    recipe['materials']
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
                'diff': diff_data
            })

        # Store data in session for confirm step
        session['recipe_import_data'] = {
            'metadata': metadata,
            'category_id': category.id,
            'recipes': recipes
        }

        return render_template('recipe_review.html',
                             metadata=metadata,
                             category=category,
                             recipes=recipes_review_data,
                             has_missing_materials=len(all_missing_materials) > 0,
                             missing_materials=all_missing_materials,
                             all_raw_materials=all_raw_materials,
                             all_premakes=all_premakes,
                             all_preproducts=all_preproducts)

    except Exception as e:
        flash(f'שגיאה בעיבוד הגיליון: {str(e)}', 'error')
        return redirect(url_for('recipe_import.upload_recipes'))


@recipe_import_blueprint.route('/recipes/confirm', methods=['POST'])
def confirm_import():
    """Confirm and save recipes to database"""
    import_data = session.get('recipe_import_data')

    if not import_data:
        flash('נתוני הייבוא לא נמצאו, אנא התחל מחדש', 'error')
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
                product.batch_size = recipe['total_weight']
                product.unit = metadata.get('unit', 'g')

                # Clear old components
                ProductComponent.query.filter_by(product_id=product.id).delete()
                updated_count += 1
            else:
                # Create new
                product = Product(
                    name=recipe['name'],
                    category_id=category_id,
                    batch_size=recipe['total_weight'],
                    unit=metadata.get('unit', 'g'),
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

                    # Validate material type matches
                    if material['type'] == 'חומר גלם':
                        db_mat = RawMaterial.query.get(mat_id)
                        found = db_mat is not None and not db_mat.is_deleted
                    elif material['type'] == 'הכנה':
                        db_mat = Product.query.filter_by(id=mat_id, is_premake=True).first()
                        found = db_mat is not None
                    elif material['type'] == 'מוצר מקדים':
                        db_mat = Product.query.filter_by(id=mat_id, is_preproduct=True).first()
                        found = db_mat is not None
                else:
                    # Try original exact match
                    found, mat_id, _, _ = match_material(material['name'], material['type'])

                if not found:
                    # Skip missing materials or show error
                    flash(f'חומר חסר או מיפוי שגוי: {material["name"]}', 'error')
                    return redirect(url_for('recipe_import.upload_recipes'))

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
                    material_unit = db_prod.unit if db_prod else 'g'

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
                # Convert loss weight from Excel unit to grams (typical for loss)
                final_loss_quantity = convert_to_base_unit(
                    recipe['loss']['weight'],  # Negative value
                    metadata['unit'],          # e.g., 'g' from Excel
                    'g'                        # Loss typically stored in grams
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

        # Clean up
        temp_file = session.get('recipe_temp_file')
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)
        session.pop('recipe_temp_file', None)
        session.pop('recipe_import_data', None)

        # Success message
        flash(f'ייבוא הושלם בהצלחה: {created_count} מתכונים חדשים, {updated_count} מתכונים עודכנו', 'success')

        # Redirect based on type
        if is_premake:
            return redirect(url_for('premakes.premakes'))
        else:
            return redirect(url_for('products.products'))

    except Exception as e:
        db.session.rollback()
        flash(f'שגיאה בשמירת המתכונים: {str(e)}', 'error')
        return redirect(url_for('recipe_import.upload_recipes'))
