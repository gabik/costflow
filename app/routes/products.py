import os
from datetime import datetime
from collections import defaultdict
from PIL import Image
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, request, redirect, url_for, current_app, jsonify
from sqlalchemy.orm import joinedload
from sqlalchemy import text
from ..models import db, Product, ProductComponent, RawMaterial, Packaging, Labor, Category, ProductionLog, StockLog, WeeklyProductSales, StockAudit
from .utils import log_audit, calculate_prime_cost, calculate_premake_cost_per_unit, convert_to_base_unit, get_or_create_general_category, units_list, calculate_total_material_stock, calculate_premake_current_stock, get_primary_supplier_discounted_price, format_quantity_with_unit

products_blueprint = Blueprint('products', __name__)

# ----------------------------
# Products Management
# ----------------------------
@products_blueprint.route('/products')
def products():
    """
    Optimized products page using app-side joins and bulk fetching.
    """
    # 1. Fetch Basic Data (Filtered)
    show_archived = request.args.get('show_archived') == 'true'
    
    # Use joinedload to fetch components in the same query (SOLVES N+1 for components list)
    query = Product.query.options(joinedload(Product.components))
    query = query.filter(Product.is_product == True)
    if not show_archived:
        query = query.filter(Product.is_archived == False)
    
    products = query.all()
    
    # 2. Bulk Fetch Dependencies (Materials, Prices, Stock)
    # We fetch ALL materials/prices because filtering by "used" ones might be more expensive/complex logic
    # and the dataset size for materials is usually manageable (hundreds, not millions).
    
    # A. Materials Map
    all_materials = RawMaterial.query.all()
    material_map = {m.id: m for m in all_materials}
    
    # B. Prices Map (Primary Supplier)
    from ..models import RawMaterialSupplier
    all_prices = RawMaterialSupplier.query.options(joinedload(RawMaterialSupplier.supplier)).all()
    
    # Calculate effective base price (discounted) for each material
    price_map = {} 
    mat_prices = defaultdict(list)
    for rms in all_prices:
        mat_prices[rms.raw_material_id].append(rms)
        
    for mid, links in mat_prices.items():
        # Primary first, else first available
        selected = next((l for l in links if l.is_primary), links[0] if links else None)
        if selected:
            discount = selected.supplier.discount_percentage or 0
            # Base price (before waste)
            price_map[mid] = selected.cost_per_unit * (1 - discount/100.0)
        else:
            price_map[mid] = 0
            
    # C. Stock Map (Raw SQL for speed)
    # We need to account for SET and ADD actions
    # Note: This ignores 'Production' consumption of premakes for now, as that requires parsing ProductionLog
    # For Raw Materials, this is accurate (they aren't "produced", only bought/consumed via logs)
    stock_rows = db.session.execute(text(
        "SELECT raw_material_id, action_type, quantity, supplier_id FROM stock_log WHERE raw_material_id IS NOT NULL ORDER BY timestamp ASC"
    )).fetchall()
    
    temp_stock = defaultdict(lambda: defaultdict(float))
    for row in stock_rows:
        mid, action, qty, sid = row[0], row[1], row[2], row[3]
        if action == 'set':
            temp_stock[mid][sid] = qty
        else:
            temp_stock[mid][sid] += qty
            
    stock_map = {}
    for mid, suppliers in temp_stock.items():
        stock_map[mid] = sum(qty for qty in suppliers.values())

    # D. Premake Stock (Complex)
    # Ideally we'd optimize this too, but it's harder. 
    # For now, we'll use memoization on the existing function to avoid re-calculating same premake
    premake_stock_cache = {}
    def get_premake_stock(pid):
        if pid not in premake_stock_cache:
            premake_stock_cache[pid] = calculate_premake_current_stock(pid)
        return premake_stock_cache[pid]

    # E. Product & Premake Master Map (Recursive Cost)
    # We need a map of ALL items that can be components (premakes AND preproducts)
    component_products = Product.query.filter(
        (Product.is_premake == True) | (Product.is_preproduct == True)
    ).options(joinedload(Product.components)).all()
    
    # Merge visible products and component products into a master map for lookup
    all_product_map = {p.id: p for p in products}
    for p in component_products:
        all_product_map[p.id] = p
        
    cost_cache = {}
    weight_cache = {}

    def get_recipe_weight(pid, visited=None):
        """Recursively calculate the total weight of a product's recipe in kg/L"""
        if visited is None: visited = set()
        if pid in visited: return 0
        if pid in weight_cache: return weight_cache[pid]
        
        visited.add(pid)
        prod = all_product_map.get(pid)
        if not prod: return 0
        
        total_weight = 0
        for comp in prod.components:
            # Raw materials and premakes are stored in kg/L base units
            if comp.component_type == 'raw_material':
                total_weight += comp.quantity
            elif comp.component_type == 'premake':
                # Premakes are products with is_premake=True.
                # If the premake unit is 'unit'/'piece', we treat it like a preproduct (qty * unit_weight)
                # Otherwise (kg/g/L), the quantity IS the weight.
                
                child = all_product_map.get(comp.component_id)
                if child and getattr(child, 'unit', 'kg') in ['piece', 'unit', 'units']:
                    # It's a unit-based premake. Calculate its unit weight.
                    child_batch_weight = get_recipe_weight(comp.component_id, visited)
                    child_yield = child.batch_size if child.batch_size and child.batch_size > 0 else 1
                    child_unit_weight = child_batch_weight / child_yield
                    total_weight += comp.quantity * child_unit_weight
                else:
                    # It's a weight-based premake (quantity is already in kg)
                    total_weight += comp.quantity
                    
            elif comp.component_type == 'product':
                # Preproduct logic
                child_prod = all_product_map.get(comp.component_id)
                if child_prod:
                    child_unit = getattr(child_prod, 'unit', 'unit')
                    if child_unit in ['kg', 'g', 'L', 'ml']:
                        # Weight based - stored as kg/L
                        total_weight += comp.quantity
                    else:
                        # Unit based - stored as count (e.g. 12 buns)
                        # We need the weight of one unit of the child product
                        
                        # Recursive call to get the child's total recipe weight
                        child_recipe_weight = get_recipe_weight(comp.component_id, visited)
                        
                        # Calculate weight per unit for the child
                        child_yield = child_prod.products_per_recipe if child_prod.products_per_recipe and child_prod.products_per_recipe > 0 else 1
                        child_unit_weight = child_recipe_weight / child_yield
                        
                        # Add total weight contribution: (12 buns) * (0.1kg per bun) = 1.2kg
                        total_weight += comp.quantity * child_unit_weight
                        
            elif comp.component_type == 'loss':
                # Loss is negative weight
                total_weight += comp.quantity
                
        weight_cache[pid] = total_weight
        return total_weight
    
    def get_unit_cost(pid, visited=None):
        if visited is None: visited = set()
        if pid in visited: return 0
        if pid in cost_cache: return cost_cache[pid]
        
        visited.add(pid)
        prod = all_product_map.get(pid)
        
        if not prod: return 0
        
        total_cost = 0
        loss_quantity = 0 # Track loss weight/units
        
        for comp in prod.components:
            if comp.component_type == 'raw_material':
                mat = material_map.get(comp.component_id)
                base_price = price_map.get(comp.component_id, 0)
                if mat:
                    waste = mat.waste_percentage
                    # Quantity is in kg/L. Price is in Supplier Unit.
                    # We must convert Quantity to Supplier Unit.
                    
                    # 1. Calculate effective quantity needed (in base kg/L) due to waste
                    eff_qty_base = comp.quantity / (1 - waste/100.0) if waste < 100 else comp.quantity
                    
                    # 2. Convert base kg/L to material's unit (which price corresponds to)                   
                    if mat.unit == 'g':
                        qty_in_price_unit = eff_qty_base * 1000
                    elif mat.unit == 'ml':
                        qty_in_price_unit = eff_qty_base * 1000
                    elif mat.unit in ['kg', 'L', 'unit', 'piece']:
                        qty_in_price_unit = eff_qty_base
                    else:
                        qty_in_price_unit = eff_qty_base # Fallback
                        
                    total_cost += qty_in_price_unit * base_price
            elif comp.component_type in ['premake', 'product']:
                # Recursive for both premakes and preproducts
                u_cost = get_unit_cost(comp.component_id, visited)
                total_cost += comp.quantity * u_cost
            elif comp.component_type == 'loss':
                # Loss has negative quantity, decreases effective yield
                loss_quantity += comp.quantity
            elif comp.component_type == 'packaging':
                pass
                
        # Divide by yield (net of loss)
        if prod.is_premake:
            batch_sz = prod.batch_size if prod.batch_size else 1
            effective_yield = max(0.001, batch_sz + loss_quantity)
            final_cost = total_cost / effective_yield
        else:
            yield_amt = prod.products_per_recipe if prod.products_per_recipe else 1
            effective_yield = max(0.001, yield_amt + loss_quantity)
            final_cost = total_cost / effective_yield
            
        cost_cache[pid] = final_cost
        return final_cost

    # 3. Assemble Data
    products_data = []
    
    for product in products:
        # Calculate Cost
        prime_cost = get_unit_cost(product.id)
        
        # Calculate Weight
        recipe_weight = get_recipe_weight(product.id)
        unit_weight = recipe_weight / product.products_per_recipe if product.products_per_recipe else 0
        
        # Check Stock
        can_produce = True
        missing_materials = []
        
        for component in product.components:
            if component.component_type == 'raw_material':
                available = stock_map.get(component.component_id, 0)
                required = component.quantity
                
                mat = material_map.get(component.component_id)
                if mat and mat.is_unlimited:
                    available = float('inf')
                
                if available < required:
                    can_produce = False
                    missing_materials.append({
                        'name': mat.name if mat else f"Material {component.component_id}",
                        'required': required,
                        'available': available
                    })
            elif component.component_type in ['premake', 'product']:
                # Check stock for both premakes and preproduct components
                available = get_premake_stock(component.component_id)
                required = component.quantity
                if available < required:
                    can_produce = False
                    premake_obj = all_product_map.get(component.component_id)
                    missing_materials.append({
                        'name': premake_obj.name if premake_obj else f'Component {component.component_id}',
                        'required': required,
                        'available': available
                    })

        products_data.append({
            'product': product,
            'prime_cost': prime_cost,
            'recipe_weight': recipe_weight,
            'unit_weight': unit_weight,
            'can_produce': can_produce,
            'missing_materials': missing_materials
        })

    return render_template('products.html', products_data=products_data, show_archived=show_archived)

@products_blueprint.route('/products/add', methods=['GET', 'POST'])
def add_product():
    if request.method == 'POST':
        # Extract product-level data
        name = request.form['name']
        category_id = request.form.get('category_id')
        if not category_id or category_id == '':
            category_id = get_or_create_general_category('product')
        else:
            category_id = int(category_id)

        products_per_recipe = request.form.get('products_per_recipe', '').strip()
        selling_price_per_unit = request.form.get('selling_price_per_unit', '').strip()

        # Default values if empty
        if not products_per_recipe:
            products_per_recipe = '1'

        # Get product type selection
        product_type_selection = request.form.get('product_type_selection', 'product')

        # Determine product type and sale status based on selection
        if product_type_selection == 'product':
            product_type = 'product'
            is_for_sale = True
            is_product = True
            is_premake = False
            is_preproduct = False
        elif product_type_selection == 'preproduct_sale':
            product_type = 'preproduct'
            is_for_sale = True
            is_product = True  # For backward compatibility
            is_premake = False
            is_preproduct = True
        elif product_type_selection == 'preproduct_internal':
            product_type = 'preproduct'
            is_for_sale = False
            is_product = True  # For backward compatibility
            is_premake = False
            is_preproduct = True
            selling_price_per_unit = '0'  # No selling price for internal use
        else:
            # Default to product
            product_type = 'product'
            is_for_sale = True
            is_product = True
            is_premake = False
            is_preproduct = False

        if not selling_price_per_unit:
            selling_price_per_unit = '0'

        batch_size = None

        image_filename = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename:
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                filename = f"{timestamp}_{filename}"

                upload_folder = current_app.config['UPLOAD_FOLDER']
                if not os.path.exists(upload_folder):
                    os.makedirs(upload_folder)

                filepath = os.path.join(upload_folder, filename)
                try:
                    # Process and resize image
                    img = Image.open(file)
                    
                    # Convert to RGB if necessary (e.g. RGBA)
                    if img.mode in ('RGBA', 'P'):
                        img = img.convert('RGB')
                        
                    # Resize to max 1024x1024
                    img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                    
                    # Save optimized
                    img.save(filepath, quality=85, optimize=True)
                except Exception as e:
                    # Fallback if image processing fails
                    print(f"Image resize failed: {e}")
                    file.seek(0)
                    file.save(filepath)

                image_filename = filename

        # Create a new Product entry
        product = Product(
            name=name,
            category_id=category_id,
            products_per_recipe=int(products_per_recipe),
            selling_price_per_unit=float(selling_price_per_unit),
            image_filename=image_filename,
            product_type=product_type,  # New field
            is_for_sale=is_for_sale,    # New field
            is_product=is_product,      # Keep for backward compatibility
            is_premake=is_premake,       # Keep for backward compatibility
            is_preproduct=is_preproduct, # Keep for backward compatibility
            batch_size=batch_size,
            unit='kg'  # Always use kg internally
        )
        db.session.add(product)
        db.session.flush()
        log_audit("CREATE", "Product", product.id, f"Created product {product.name}")
        # Process raw materials
        raw_materials = request.form.getlist('raw_material[]')
        raw_material_quantities = request.form.getlist('raw_material_quantity[]')
        raw_material_units = request.form.getlist('raw_material_unit[]')

        for i in range(len(raw_materials)):
            material_id = raw_materials[i]
            quantity = raw_material_quantities[i]
            selected_unit = raw_material_units[i] if i < len(raw_material_units) else None

            if not material_id or not quantity or float(quantity) <= 0:
                continue

            material = RawMaterial.query.get(material_id)
            if not material:
                continue

            # ALWAYS convert to base unit (kg for solids, L for liquids)
            base_unit = 'kg' if material.unit in ['kg', 'g'] else material.unit
            final_quantity = convert_to_base_unit(float(quantity), selected_unit, base_unit)

            component = ProductComponent(
                product_id=product.id,
                component_type='raw_material',
                component_id=material_id,
                quantity=final_quantity
            )
            db.session.add(component)

        # Process packaging
        packaging_ids = request.form.getlist('packaging[]')
        packaging_quantities = request.form.getlist('packaging_quantity[]')
        for pkg_id, quantity in zip(packaging_ids, packaging_quantities):
            if not pkg_id or not quantity or float(quantity) <= 0:
                continue
            component = ProductComponent(
                product_id=product.id,
                component_type='packaging',
                component_id=pkg_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        # Process premakes
        premake_ids = request.form.getlist('premake[]')
        premake_quantities = request.form.getlist('premake_quantity[]')
        premake_units = request.form.getlist('premake_unit[]')

        for i in range(len(premake_ids)):
            premake_id = premake_ids[i]
            quantity = premake_quantities[i] if i < len(premake_quantities) else None
            selected_unit = premake_units[i] if i < len(premake_units) else None

            if not premake_id or not quantity or float(quantity) <= 0:
                continue

            # Get the premake to find its base unit
            premake = Product.query.get(premake_id)
            if not premake or not premake.is_premake:
                continue

            # ALWAYS convert to kg for consistent storage (not to premake's unit)
            final_quantity = convert_to_base_unit(float(quantity), selected_unit, 'kg')

            component = ProductComponent(
                product_id=product.id,
                component_type='premake',
                component_id=premake_id,
                quantity=final_quantity
            )
            db.session.add(component)

        # Process preproducts
        preproduct_ids = request.form.getlist('preproduct[]')
        preproduct_quantities = request.form.getlist('preproduct_quantity[]')
        preproduct_units = request.form.getlist('preproduct_unit[]')

        for i in range(len(preproduct_ids)):
            preproduct_id = preproduct_ids[i]
            quantity = preproduct_quantities[i] if i < len(preproduct_quantities) else None
            selected_unit = preproduct_units[i] if i < len(preproduct_units) else None

            if not preproduct_id or not quantity or float(quantity) <= 0:
                continue

            # Get the preproduct to check its base unit
            preproduct = Product.query.get(preproduct_id)
            if not preproduct or not preproduct.is_preproduct:
                continue

            # Determine the final quantity based on unit type
            preproduct_base_unit = getattr(preproduct, 'unit', 'unit')

            if preproduct_base_unit in ['kg', 'g']:
                # Weight-based preproduct - convert to kg for storage
                base_unit = 'kg'
                final_quantity = convert_to_base_unit(float(quantity), selected_unit, base_unit)
            else:
                # Unit-based preproduct (unit, piece, etc.) - store as-is
                final_quantity = float(quantity)

            component = ProductComponent(
                product_id=product.id,
                component_type='product',
                component_id=preproduct_id,
                quantity=final_quantity
            )
            db.session.add(component)

        # Process Loss/Waste
        loss_quantities = request.form.getlist('loss_quantity[]')
        loss_units = request.form.getlist('loss_unit[]')
        loss_descriptions = request.form.getlist('loss_description[]')
        
        # Get yield for percentage calculation
        recipe_yield = product.products_per_recipe or 1.0

        for i in range(len(loss_quantities)):
            if loss_quantities[i]:
                try:
                    loss_qty = float(loss_quantities[i])
                    loss_u = loss_units[i] if i < len(loss_units) else 'unit'
                    loss_desc = loss_descriptions[i] if i < len(loss_descriptions) else None
                    
                    if loss_u == '%':
                        # Percentage of yield
                        final_loss = recipe_yield * (loss_qty / 100.0)
                    else:
                        # Fixed unit amount
                        final_loss = loss_qty
                        
                    # Save as negative quantity
                    component = ProductComponent(
                        product_id=product.id,
                        component_type='loss',
                        component_id=0,
                        quantity=-final_loss,
                        description=loss_desc
                    )
                    db.session.add(component)
                except (ValueError, TypeError):
                    continue

        db.session.commit()  # Save all components
        return redirect(url_for('products.products'))

    # For GET requests, load the data required for the form
    # Enhanced material data using primary supplier price
    all_raw_materials = []
    for material in RawMaterial.query.filter_by(is_deleted=False).all():
        material_dict = material.to_dict()

        # Find primary supplier's price
        primary_price = None
        primary_supplier_name = None
        for link in material.supplier_links:
            if link.is_primary:
                primary_price = link.cost_per_unit
                primary_supplier_name = link.supplier.name
                break

        # If no primary supplier, use first supplier
        if primary_price is None and material.supplier_links:
            first_link = material.supplier_links[0]
            primary_price = first_link.cost_per_unit
            primary_supplier_name = first_link.supplier.name
        elif primary_price is None:
            # No suppliers at all (shouldn't happen)
            primary_price = 0
            primary_supplier_name = "לא הוגדר ספק"

        # Apply waste percentage adjustment for effective price
        material_dict['base_price'] = primary_price  # Base price without waste
        material_dict['cost_per_unit'] = primary_price * material.effective_cost_multiplier  # Effective price with waste
        material_dict['display_price'] = primary_price * material.effective_cost_multiplier
        material_dict['effective_cost_multiplier'] = material.effective_cost_multiplier
        material_dict['price_source'] = primary_supplier_name

        all_raw_materials.append(material_dict)

    categories = Category.query.filter_by(type='raw_material').all()
    product_categories = Category.query.filter_by(type='product').all()

    # Enhanced packaging data with price_per_unit
    all_packaging = []
    for pkg in Packaging.query.all():
        pkg_dict = pkg.to_dict()
        pkg_dict['price_per_unit'] = pkg.price_per_unit
        all_packaging.append(pkg_dict)

    all_labor = [labor_item.to_dict() for labor_item in Labor.query.all()]

    # Enhanced premakes data with cost_per_unit
    all_premakes = []
    for p in Product.query.filter_by(is_premake=True).all():
        premake_dict = p.to_dict()
        premake_dict['cost_per_unit'] = calculate_premake_cost_per_unit(p, use_actual_costs=False)
        all_premakes.append(premake_dict)

    # Enhanced preproducts data with cost_per_unit
    all_preproducts = []
    for p in Product.query.filter_by(is_preproduct=True).all():
        preproduct_dict = p.to_dict()
        preproduct_dict['cost_per_unit'] = calculate_prime_cost(p)
        all_preproducts.append(preproduct_dict)

    return render_template(
        'add_or_edit_product.html',
        product=None,
        all_raw_materials=all_raw_materials,
        all_packaging=all_packaging,
        all_labor=all_labor,
        all_premakes=all_premakes,
        all_preproducts=all_preproducts,
        categories=categories, # For raw material modal
        product_categories=product_categories, # For product form
        units=units_list # For raw material modal
    )


@products_blueprint.route('/products/<int:product_id>', methods=['GET'])
def product_detail(product_id):
    product = Product.query.get_or_404(product_id)

    # Retrieve raw materials used in the product
    raw_materials = []
    for component in ProductComponent.query.filter_by(product_id=product_id, component_type='raw_material'):
        material = RawMaterial.query.get(component.component_id)
        if not material:
            continue

        # Find primary supplier original price for the material
        primary_price_original = 0  # default if no suppliers
        for link in material.supplier_links:
            if link.is_primary:
                primary_price_original = link.cost_per_unit
                break

        # If no primary, use first supplier
        if primary_price_original == 0 and material.supplier_links:
            primary_price_original = material.supplier_links[0].cost_per_unit

        # Get discounted price using helper
        primary_price_discounted = get_primary_supplier_discounted_price(material)

        # Convert prices to per 100g for display
        # Price is already per unit in the material's unit (e.g., per kg, per liter)
        # We need to convert to price per 100g
        if material.unit == 'kg':
            price_per_100g = primary_price_discounted * 0.1  # 100g = 0.1kg
            price_per_100g_original = primary_price_original * 0.1
        elif material.unit == 'g':
            price_per_100g = primary_price_discounted * 100  # price is per g, we want per 100g
            price_per_100g_original = primary_price_original * 100
        elif material.unit == 'l':
            price_per_100g = primary_price_discounted * 0.1  # Assuming 1l = 1kg for liquids
            price_per_100g_original = primary_price_original * 0.1
        elif material.unit == 'ml':
            price_per_100g = primary_price_discounted * 100  # Assuming 1ml = 1g for liquids
            price_per_100g_original = primary_price_original * 100
        else:
            # For units like 'unit', keep the price as is
            price_per_100g = primary_price_discounted
            price_per_100g_original = primary_price_original

        # Calculate gross quantity (including waste)
        gross_quantity = component.quantity * material.effective_cost_multiplier

        # Apply dynamic unit conversion for display
        display_quantity, display_unit = format_quantity_with_unit(gross_quantity, material.unit)

        raw_materials.append({
            'name': material.name,
            'quantity': component.quantity,
            'gross_quantity': gross_quantity,
            'unit': material.unit,
            'display_quantity': display_quantity,  # For dynamic display
            'display_unit': display_unit,  # For dynamic display
            'price_per_unit': primary_price_discounted,  # Keep for calculations
            'price_per_unit_original': primary_price_original,  # Keep for calculations
            'price_per_100g': price_per_100g,  # For display
            'price_per_100g_original': price_per_100g_original,  # For display
            'price_per_recipe': gross_quantity * primary_price_discounted,
            'price_per_product': (gross_quantity * primary_price_discounted) / product.products_per_recipe if product.products_per_recipe > 0 else 0,
            'waste_percentage': material.waste_percentage
        })

    # Retrieve packaging costs
    packaging_costs = []
    for component in ProductComponent.query.filter_by(product_id=product_id, component_type='packaging'):
        packaging = Packaging.query.get(component.component_id)
        if not packaging:
            continue

        packaging_costs.append({
            'name': packaging.name,
            'quantity': component.quantity,
            'price_per_package': packaging.price_per_package,  # This is now a computed property
            'price_per_unit': packaging.price_per_unit,  # Use the property directly
            'price_per_recipe': component.quantity * packaging.price_per_unit,
            'price_per_product': (component.quantity * packaging.price_per_unit) / product.products_per_recipe if product.products_per_recipe > 0 else 0
        })

    # Retrieve premake costs
    premake_costs = []
    for component in ProductComponent.query.filter_by(product_id=product_id, component_type='premake'):
        # Get premake from unified Product model
        premake = Product.query.filter_by(id=component.component_id, is_premake=True).first()

        if not premake:
            continue

        # Calculate cost per unit of premake using utility function
        premake_unit_cost = calculate_premake_cost_per_unit(premake, use_actual_costs=False)

        # Get effective batch size
        effective_batch_size = premake.batch_size if hasattr(premake, 'batch_size') and premake.batch_size and premake.batch_size > 0 else 1

        # Build components list with proper null checks
        components_list = []
        for c in premake.components:
            comp_data = {'quantity': c.quantity}

            if c.component_type == 'raw_material' and c.material:
                comp_data['name'] = c.material.name
                comp_data['unit'] = c.material.unit
            elif c.component_type == 'packaging' and c.packaging:
                comp_data['name'] = c.packaging.name
                comp_data['unit'] = 'pcs'
            elif c.component_type == 'premake':
                # Handle nested premake
                nested = Product.query.filter_by(id=c.component_id, is_premake=True).first()
                if nested:
                    comp_data['name'] = nested.name
                    comp_data['unit'] = getattr(nested, 'unit', 'unit')
                else:
                    continue
            else:
                continue

            components_list.append(comp_data)

        # Apply dynamic unit conversion for display
        display_quantity, display_unit = format_quantity_with_unit(component.quantity, getattr(premake, 'unit', 'unit'))

        # Convert premake cost to per 100g
        premake_unit = getattr(premake, 'unit', 'kg')
        if premake_unit == 'kg':
            price_per_100g = premake_unit_cost * 0.1  # 100g = 0.1kg
        elif premake_unit == 'g':
            price_per_100g = premake_unit_cost * 100  # price is per g, we want per 100g
        else:
            price_per_100g = premake_unit_cost  # Keep as is for other units

        premake_costs.append({
            'name': premake.name,
            'quantity': component.quantity,  # Keep original for calculations
            'unit': getattr(premake, 'unit', 'unit'),  # Keep original unit
            'display_quantity': display_quantity,  # Add display quantity
            'display_unit': display_unit,  # Add display unit
            'batch_size': effective_batch_size,
            'price_per_unit': premake_unit_cost,  # Keep for calculations
            'price_per_100g': price_per_100g,  # For display
            'price_per_recipe': component.quantity * premake_unit_cost,
            'price_per_product': (component.quantity * premake_unit_cost) / product.products_per_recipe if product.products_per_recipe > 0 else 0,
            'components': components_list
        })

    # Retrieve preproduct costs
    preproduct_costs = []

    # Look for preproducts in both 'product' AND 'premake' component types
    # (Some preproducts might be incorrectly stored as premake components)
    for component in ProductComponent.query.filter_by(product_id=product_id).filter(
        ProductComponent.component_type.in_(['product', 'premake'])
    ):
        # Get preproduct from Product model - check if it's a preproduct
        preproduct = Product.query.filter_by(id=component.component_id, is_preproduct=True).first()

        if not preproduct:
            # Not a preproduct, skip
            continue

        # Calculate prime cost for the preproduct
        preproduct_unit_cost = calculate_prime_cost(preproduct)

        # Handle different unit types
        preproduct_unit = getattr(preproduct, 'unit', 'unit')

        # Apply display formatting based on unit type
        if preproduct_unit in ['kg', 'g']:
            # Weight-based - apply conversion for display
            display_quantity, display_unit = format_quantity_with_unit(component.quantity, 'kg')
        else:
            # Unit-based - display as-is
            display_quantity = component.quantity
            display_unit = preproduct_unit

        # For display purposes
        if preproduct_unit == 'kg':
            display_price = preproduct_unit_cost * 0.1  # Per 100g
            display_price_label = 'price_per_100g'
        elif preproduct_unit == 'g':
            display_price = preproduct_unit_cost * 100  # Per 100g
            display_price_label = 'price_per_100g'
        else:
            # For 'unit' or other non-weight units, show per unit
            display_price = preproduct_unit_cost
            display_price_label = 'price_per_unit'

        preproduct_costs.append({
            'name': preproduct.name,
            'quantity': component.quantity,  # Keep raw quantity for calculations
            'unit': preproduct_unit,  # Keep original unit
            'display_quantity': display_quantity,  # Add formatted quantity for display
            'display_unit': display_unit,  # Add display unit
            'price_per_unit': preproduct_unit_cost,  # Actual cost per unit
            'price_per_100g': display_price,  # For display (might be per unit if not weight)
            'display_price_label': display_price_label,  # Label to use in template
            'price_per_recipe': component.quantity * preproduct_unit_cost,
            'price_per_product': (component.quantity * preproduct_unit_cost) / product.products_per_recipe if product.products_per_recipe > 0 else 0
        })

    # Retrieve Loss/Waste components
    loss_costs = []
    from flask_babel import gettext as _
    for component in ProductComponent.query.filter_by(product_id=product_id, component_type='loss'):
        loss_costs.append({
            'name': component.description if component.description else _('Loss / Waste'),
            'quantity': abs(component.quantity),
            'unit': 'unit' if not hasattr(product, 'unit') or not product.unit else product.unit, # Default to product unit
            'price_per_unit': 0,
            'price_per_recipe': 0,
            'price_per_product': 0
        })

    return render_template(
        'product_details.html',
        product=product,
        raw_materials=raw_materials,
        packaging_costs=packaging_costs,
        premake_costs=premake_costs,
        preproduct_costs=preproduct_costs,
        loss_costs=loss_costs
    )


@products_blueprint.route('/products/edit/<int:product_id>', methods=['GET', 'POST'])
def edit_product(product_id):
    from sqlalchemy.orm import joinedload
    
    # Fetch product with components eagerly loaded
    product = Product.query.options(joinedload(Product.components)).get_or_404(product_id)

    if request.method == 'POST':
        product.name = request.form['name']
        category_id = request.form.get('category_id')
        if not category_id or category_id == '':
            product.category_id = get_or_create_general_category('product')
        else:
            product.category_id = int(category_id)

        product.products_per_recipe = int(request.form['products_per_recipe'])

        # Get product type selection
        product_type_selection = request.form.get('product_type_selection', 'product')

        # Update product type and sale status based on selection
        if product_type_selection == 'product':
            product.product_type = 'product'
            product.is_for_sale = True
            product.is_product = True
            product.is_premake = False
            product.is_preproduct = False
            product.selling_price_per_unit = float(request.form.get('selling_price_per_unit', 0))
        elif product_type_selection == 'preproduct_sale':
            product.product_type = 'preproduct'
            product.is_for_sale = True
            product.is_product = True  # For backward compatibility
            product.is_premake = False
            product.is_preproduct = True
            product.selling_price_per_unit = float(request.form.get('selling_price_per_unit', 0))
        elif product_type_selection == 'preproduct_internal':
            product.product_type = 'preproduct'
            product.is_for_sale = False
            product.is_product = True  # For backward compatibility
            product.is_premake = False
            product.is_preproduct = True
            product.selling_price_per_unit = 0  # No selling price for internal use

        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename:
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                filename = f"{timestamp}_{filename}"

                upload_folder = current_app.config['UPLOAD_FOLDER']
                if not os.path.exists(upload_folder):
                    os.makedirs(upload_folder)

                filepath = os.path.join(upload_folder, filename)
                try:
                    # Process and resize image
                    img = Image.open(file)
                    
                    # Convert to RGB if necessary (e.g. RGBA)
                    if img.mode in ('RGBA', 'P'):
                        img = img.convert('RGB')
                        
                    # Resize to max 1024x1024
                    img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
                    
                    # Save optimized
                    img.save(filepath, quality=85, optimize=True)
                except Exception as e:
                    # Fallback if image processing fails
                    print(f"Image resize failed: {e}")
                    file.seek(0)
                    file.save(filepath)

                product.image_filename = filename

        # Clear existing components
        ProductComponent.query.filter_by(product_id=product_id).delete()

        # Process raw materials
        raw_materials = request.form.getlist('raw_material[]')
        raw_material_quantities = request.form.getlist('raw_material_quantity[]')
        raw_material_units = request.form.getlist('raw_material_unit[]')

        for i in range(len(raw_materials)):
            material_id = raw_materials[i]
            quantity = raw_material_quantities[i]
            selected_unit = raw_material_units[i] if i < len(raw_material_units) else None

            if not material_id or not quantity or float(quantity) <= 0:
                continue

            material = RawMaterial.query.get(material_id)
            if not material:
                continue

            # ALWAYS convert to base unit (kg for solids, L for liquids)
            base_unit = 'kg' if material.unit in ['kg', 'g'] else material.unit
            final_quantity = convert_to_base_unit(float(quantity), selected_unit, base_unit)

            component = ProductComponent(
                product_id=product.id,
                component_type='raw_material',
                component_id=material_id,
                quantity=final_quantity
            )
            db.session.add(component)

        # Process packaging
        packaging_ids = request.form.getlist('packaging[]')
        packaging_quantities = request.form.getlist('packaging_quantity[]')
        for pkg_id, quantity in zip(packaging_ids, packaging_quantities):
            if not pkg_id or not quantity or float(quantity) <= 0:
                continue
            component = ProductComponent(
                product_id=product.id,
                component_type='packaging',
                component_id=pkg_id,
                quantity=float(quantity)
            )
            db.session.add(component)

        # Process premakes
        premake_ids = request.form.getlist('premake[]')
        premake_quantities = request.form.getlist('premake_quantity[]')
        premake_units = request.form.getlist('premake_unit[]')

        for i in range(len(premake_ids)):
            premake_id = premake_ids[i]
            quantity = premake_quantities[i] if i < len(premake_quantities) else None
            selected_unit = premake_units[i] if i < len(premake_units) else None

            if not premake_id or not quantity or float(quantity) <= 0:
                continue

            # Get the premake to find its base unit
            premake = Product.query.get(premake_id)
            if not premake or not premake.is_premake:
                continue

            # ALWAYS convert to kg for consistent storage (not to premake's unit)
            final_quantity = convert_to_base_unit(float(quantity), selected_unit, 'kg')

            component = ProductComponent(
                product_id=product.id,
                component_type='premake',
                component_id=premake_id,
                quantity=final_quantity
            )
            db.session.add(component)

        # Process preproducts
        preproduct_ids = request.form.getlist('preproduct[]')
        preproduct_quantities = request.form.getlist('preproduct_quantity[]')
        preproduct_units = request.form.getlist('preproduct_unit[]')

        for i in range(len(preproduct_ids)):
            preproduct_id = preproduct_ids[i]
            quantity = preproduct_quantities[i] if i < len(preproduct_quantities) else None
            selected_unit = preproduct_units[i] if i < len(preproduct_units) else None

            if not preproduct_id or not quantity or float(quantity) <= 0:
                continue

            # Get the preproduct to check its base unit
            preproduct = Product.query.get(preproduct_id)
            if not preproduct or not preproduct.is_preproduct:
                continue

            # Determine the final quantity based on unit type
            preproduct_base_unit = getattr(preproduct, 'unit', 'unit')

            if preproduct_base_unit in ['kg', 'g']:
                # Weight-based preproduct - convert to kg for storage
                base_unit = 'kg'
                final_quantity = convert_to_base_unit(float(quantity), selected_unit, base_unit)
            else:
                # Unit-based preproduct (unit, piece, etc.) - store as-is
                final_quantity = float(quantity)

            component = ProductComponent(
                product_id=product.id,
                component_type='product',
                component_id=preproduct_id,
                quantity=final_quantity
            )
            db.session.add(component)

        # Process Loss/Waste
        loss_quantities = request.form.getlist('loss_quantity[]')
        loss_units = request.form.getlist('loss_unit[]')
        loss_descriptions = request.form.getlist('loss_description[]')
        
        # Get yield for percentage calculation
        recipe_yield = product.products_per_recipe or 1.0

        for i in range(len(loss_quantities)):
            if loss_quantities[i]:
                try:
                    loss_qty = float(loss_quantities[i])
                    loss_u = loss_units[i] if i < len(loss_units) else 'unit'
                    loss_desc = loss_descriptions[i] if i < len(loss_descriptions) else None
                    
                    if loss_u == '%':
                        # Percentage of yield
                        final_loss = recipe_yield * (loss_qty / 100.0)
                    else:
                        # Fixed unit amount
                        final_loss = loss_qty
                        
                    # Save as negative quantity
                    component = ProductComponent(
                        product_id=product.id,
                        component_type='loss',
                        component_id=0,
                        quantity=-final_loss,
                        description=loss_desc
                    )
                    db.session.add(component)
                except (ValueError, TypeError):
                    continue

        log_audit("UPDATE", "Product", product.id, f"Updated product {product.name}")
        db.session.commit()
        return redirect(url_for('products.products'))

    # Prepopulate fields for editing
    # --- GET Request Optimization: Bulk Fetch ---
    from ..models import RawMaterialSupplier, PackagingSupplier
    
    # 1. Fetch All Raw Materials + Suppliers (Bulk query)
    all_materials_query = RawMaterial.query.filter_by(is_deleted=False).options(
        joinedload(RawMaterial.supplier_links).joinedload(RawMaterialSupplier.supplier)
    ).all()
    
    all_raw_materials = []
    for material in all_materials_query:
        primary_link = None
        for link in material.supplier_links:
            if link.is_primary:
                primary_link = link
                break
        
        if not primary_link and material.supplier_links:
            primary_link = material.supplier_links[0]
            
        base_price = 0
        supplier_name = "לא הוגדר ספק"
        
        if primary_link:
            discount = primary_link.supplier.discount_percentage or 0
            base_price = primary_link.cost_per_unit * (1 - discount/100.0)
            supplier_name = primary_link.supplier.name
            
        eff_multiplier = 1.0 / (1.0 - material.waste_percentage / 100.0) if material.waste_percentage < 100 else 1.0
        
        all_raw_materials.append({
            'id': material.id,
            'name': material.name,
            'category_id': material.category_id,
            'unit': material.unit,
            'is_unlimited': material.is_unlimited,
            'is_deleted': material.is_deleted,
            'waste_percentage': material.waste_percentage,
            'base_price': base_price,
            'cost_per_unit': base_price * eff_multiplier,
            'display_price': base_price * eff_multiplier,
            'effective_cost_multiplier': eff_multiplier,
            'price_source': supplier_name,
            'suppliers': [] 
        })

    # 2. Fetch Packaging
    all_packaging_query = Packaging.query.options(joinedload(Packaging.supplier_links).joinedload(PackagingSupplier.supplier)).all()
    all_packaging = []
    for pkg in all_packaging_query:
        price = 0
        primary_link = next((l for l in pkg.supplier_links if l.is_primary), pkg.supplier_links[0] if pkg.supplier_links else None)
        if primary_link:
            discount = primary_link.supplier.discount_percentage or 0
            pkg_price = primary_link.price_per_package * (1 - discount/100.0)
            price = pkg_price / pkg.quantity_per_package if pkg.quantity_per_package > 0 else 0
            
        pkg_dict = pkg.to_dict()
        pkg_dict['price_per_unit'] = price
        all_packaging.append(pkg_dict)

    # 3. Labor
    all_labor = [l.to_dict() for l in Labor.query.all()]

    # 4. Premakes & Preproducts
    all_premakes_query = Product.query.filter_by(is_premake=True).all()
    all_premakes = []
    for p in all_premakes_query:
        d = p.to_dict()
        d['cost_per_unit'] = 0 # Placeholder for simplicity
        all_premakes.append(d)
        
    all_preproducts_query = Product.query.filter_by(is_preproduct=True).all()
    all_preproducts = []
    for p in all_preproducts_query:
        d = p.to_dict()
        d['cost_per_unit'] = 0 
        all_preproducts.append(d)

    categories = Category.query.filter_by(type='raw_material').all()
    product_categories = Category.query.filter_by(type='product').all()

    # Pass both the object (for Jinja server-side) and the dict (for JS client-side)
    return render_template(
        'add_or_edit_product.html',
        product=product,
        all_raw_materials=all_raw_materials,
        all_packaging=all_packaging,
        all_labor=all_labor,
        all_premakes=all_premakes,
        all_preproducts=all_preproducts,
        categories=categories,
        product_categories=product_categories,
        units=units_list # For raw material modal
    )


@products_blueprint.route('/products/delete/<int:product_id>', methods=['POST'])
def delete_product(product_id):
    """Delete or archive a product based on its history"""
    from flask_babel import gettext as _
    from flask import flash

    product = Product.query.filter_by(id=product_id, is_product=True).first_or_404()

    # Check if this product is used as a preproduct (component) in any other products
    usage_as_component = ProductComponent.query.filter_by(
        component_type='product',
        component_id=product_id
    ).count()

    if usage_as_component > 0:
        # Archive the product instead of deleting
        product.is_archived = True
        db.session.commit()
        log_audit("ARCHIVE", "Product", product_id, f"Archived product: {product.name} (used in {usage_as_component} products)")
        flash(_('Product archived: It is used as a component in {} other product(s)').format(usage_as_component), 'warning')
        return redirect(url_for('products.products'))

    # Check if there are production logs for this product
    production_logs = ProductionLog.query.filter_by(product_id=product_id).count()

    if production_logs > 0:
        # Archive the product instead of deleting
        product.is_archived = True
        db.session.commit()
        log_audit("ARCHIVE", "Product", product_id, f"Archived product: {product.name} ({production_logs} production records)")
        flash(_('Product archived: It has {} production record(s)').format(production_logs), 'warning')
        return redirect(url_for('products.products'))

    # Check if there are weekly sales records for this product
    weekly_sales = WeeklyProductSales.query.filter_by(product_id=product_id).count()

    if weekly_sales > 0:
        # Archive the product instead of deleting
        product.is_archived = True
        db.session.commit()
        log_audit("ARCHIVE", "Product", product_id, f"Archived product: {product.name} ({weekly_sales} sales records)")
        flash(_('Product archived: It has {} weekly sales record(s)').format(weekly_sales), 'warning')
        return redirect(url_for('products.products'))

    # If all checks pass, proceed with deletion
    try:
        # Delete components
        ProductComponent.query.filter_by(product_id=product_id).delete()

        # Delete stock logs
        StockLog.query.filter_by(product_id=product_id).delete()

        # Delete stock audits
        StockAudit.query.filter_by(product_id=product_id).delete()

        # Delete the product
        db.session.delete(product)
        log_audit("DELETE", "Product", product_id, f"Deleted product: {product.name}")

        db.session.commit()
        flash(_('Product deleted successfully'), 'success')
    except Exception as e:
        db.session.rollback()
        flash(_('Error deleting product: {}').format(str(e)), 'error')

    return redirect(url_for('products.products'))


@products_blueprint.route('/products/restore/<int:product_id>', methods=['POST'])
def restore_product(product_id):
    """Restore an archived product"""
    from flask_babel import gettext as _
    from flask import flash

    product = Product.query.filter_by(id=product_id, is_product=True, is_archived=True).first_or_404()

    try:
        product.is_archived = False
        db.session.commit()
        log_audit("RESTORE", "Product", product_id, f"Restored product: {product.name}")
        flash(_('Product restored successfully'), 'success')
    except Exception as e:
        db.session.rollback()
        flash(_('Error restoring product: {}').format(str(e)), 'error')

    return redirect(url_for('products.products') + '?show_archived=true')@products_blueprint.route('/products/debug_170')
def debug_170():
    from sqlalchemy.orm import joinedload
    from collections import defaultdict
    from ..models import RawMaterialSupplier

    # 1. Fetch relevant products
    target_ids = [170, 168] 
    products = Product.query.filter(Product.id.in_(target_ids)).options(joinedload(Product.components)).all()
    all_product_map = {p.id: p for p in products}
    
    # 2. Materials & Prices
    all_materials = RawMaterial.query.all()
    material_map = {m.id: m for m in all_materials}
    
    all_prices = RawMaterialSupplier.query.options(joinedload(RawMaterialSupplier.supplier)).all()
    price_map = {} 
    mat_prices = defaultdict(list)
    for rms in all_prices:
        mat_prices[rms.raw_material_id].append(rms)
    for mid, links in mat_prices.items():
        selected = next((l for l in links if l.is_primary), links[0] if links else None)
        if selected:
            discount = selected.supplier.discount_percentage or 0
            price_map[mid] = selected.cost_per_unit * (1 - discount/100.0)
        else:
            price_map[mid] = 0

    log = []

    def get_recipe_weight(pid, visited=None):
        if visited is None: visited = set()
        visited.add(pid)
        prod = all_product_map.get(pid)
        
        total_weight = 0
        log.append(f"Calculating Weight for {pid} ({prod.name})")
        
        for comp in prod.components:
            if comp.component_type == 'raw_material':
                log.append(f"  + Material {comp.component_id}: {comp.quantity}kg")
                total_weight += comp.quantity
            elif comp.component_type == 'product':
                child = all_product_map.get(comp.component_id)
                log.append(f"  + Preproduct {comp.component_id} ({child.name if child else '?'}) Qty: {comp.quantity}")
                
                if child:
                    child_unit = getattr(child, 'unit', 'unit')
                    if child_unit in ['kg', 'g', 'L', 'ml']:
                        total_weight += comp.quantity
                        log.append(f"    -> Added directly (weight unit): {comp.quantity}")
                    else:
                        child_w = get_recipe_weight(comp.component_id, visited)
                        yield_amt = child.products_per_recipe if child.products_per_recipe else 1
                        unit_w = child_w / yield_amt
                        contrib = comp.quantity * unit_w
                        total_weight += contrib
                        log.append(f"    -> Unit Calc: ChildTotal={child_w:.4f} / Yield={yield_amt} = {unit_w:.4f}/unit. Total contrib={contrib:.4f}")
                        
        log.append(f"= Total Weight {pid}: {total_weight:.4f}")
        return total_weight

    weight_168 = get_recipe_weight(168)
    weight_170 = get_recipe_weight(170)
    
    return "<br>".join(log)
