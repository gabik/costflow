-- Debug queries for weekly report issue
-- Replace '2024-12-08' with your actual week start date (Sunday)

-- 1. Check if WeeklyLaborCost exists for the week
SELECT id, week_start_date, total_cost
FROM weekly_labor_cost
WHERE week_start_date = '2024-12-08';

-- 2. Check ProductionLog entries for the week
SELECT
    p.name as product_name,
    p.is_product,
    p.is_premake,
    pl.quantity_produced,
    pl.timestamp,
    p.products_per_recipe,
    (pl.quantity_produced * COALESCE(p.products_per_recipe, 1)) as total_units
FROM production_log pl
JOIN product p ON pl.product_id = p.id
WHERE DATE(pl.timestamp) >= '2024-12-08'
  AND DATE(pl.timestamp) <= '2024-12-14'
ORDER BY pl.timestamp;

-- 3. Check WeeklyProductSales for the week
SELECT
    wps.id,
    p.name as product_name,
    wps.quantity_sold,
    wps.quantity_waste
FROM weekly_product_sales wps
JOIN weekly_labor_cost wlc ON wps.weekly_cost_id = wlc.id
JOIN product p ON wps.product_id = p.id
WHERE wlc.week_start_date = '2024-12-08';

-- 4. Summary: Count production entries by type
SELECT
    CASE
        WHEN p.is_premake = true THEN 'Premake'
        WHEN p.is_product = true THEN 'Product'
        ELSE 'Other'
    END as product_type,
    COUNT(*) as production_count,
    SUM(pl.quantity_produced) as total_batches,
    SUM(pl.quantity_produced * COALESCE(p.products_per_recipe, 1)) as total_units
FROM production_log pl
JOIN product p ON pl.product_id = p.id
WHERE DATE(pl.timestamp) >= '2024-12-08'
  AND DATE(pl.timestamp) <= '2024-12-14'
GROUP BY product_type;

-- 5. Check if there's ANY production data in the database
SELECT
    MIN(timestamp) as first_production,
    MAX(timestamp) as last_production,
    COUNT(*) as total_production_logs
FROM production_log;

-- 6. Check recent production (last 10 entries)
SELECT
    pl.timestamp,
    p.name as product_name,
    pl.quantity_produced,
    p.is_product,
    p.is_premake
FROM production_log pl
JOIN product p ON pl.product_id = p.id
ORDER BY pl.timestamp DESC
LIMIT 10;