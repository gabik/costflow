-- Check all preproducts and their units
SELECT
    id,
    name,
    unit,
    batch_size,
    products_per_recipe,
    is_preproduct
FROM products
WHERE is_preproduct = 1;

-- Check how preproducts are used in recipes
SELECT
    pc.quantity,
    pc.component_type,
    p1.name as product_name,
    p2.name as preproduct_name,
    p2.unit as preproduct_unit
FROM product_components pc
JOIN products p1 ON pc.product_id = p1.id
JOIN products p2 ON pc.component_id = p2.id
WHERE p2.is_preproduct = 1;