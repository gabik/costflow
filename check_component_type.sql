-- Check the component linking for the product that uses the preproduct
SELECT
    pc.*,
    p1.name as product_name,
    p2.name as component_name,
    p2.is_preproduct
FROM product_components pc
JOIN products p1 ON pc.product_id = p1.id
LEFT JOIN products p2 ON pc.component_id = p2.id
WHERE p1.name LIKE '%בריוש מקושקשת%'
ORDER BY pc.component_type;

-- Check if the component_type is correct
SELECT
    pc.component_type,
    COUNT(*) as count
FROM product_components pc
JOIN products p ON pc.component_id = p.id
WHERE p.is_preproduct = 1
GROUP BY pc.component_type;