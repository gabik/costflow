@products_blueprint.route('/products/debug_170')
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
