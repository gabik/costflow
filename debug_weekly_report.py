#!/usr/bin/env python3
"""
Debug script for weekly report issues
Run this to check what data exists for a specific week
"""

from datetime import datetime, timedelta
from app import create_app
from app.models import WeeklyLaborCost, WeeklyProductSales, ProductionLog, Product, StockLog

def debug_week(week_start_str):
    """Debug data for a specific week"""
    app = create_app()

    with app.app_context():
        # Parse the week start date
        week_start = datetime.strptime(week_start_str, '%Y-%m-%d').date()
        week_end = week_start + timedelta(days=6)

        print(f"\n=== DEBUGGING WEEK: {week_start} to {week_end} ===\n")

        # 1. Check WeeklyLaborCost
        weekly_cost = WeeklyLaborCost.query.filter_by(week_start_date=week_start).first()
        if weekly_cost:
            print(f"✓ WeeklyLaborCost exists: ID={weekly_cost.id}, Total Cost={weekly_cost.total_cost}")

            # Check sales entries
            sales = WeeklyProductSales.query.filter_by(weekly_cost_id=weekly_cost.id).all()
            print(f"  - WeeklyProductSales entries: {len(sales)}")
            if sales:
                total_sold = sum(s.quantity_sold for s in sales)
                total_waste = sum(s.quantity_waste for s in sales)
                print(f"    Total sold: {total_sold}, Total waste: {total_waste}")
        else:
            print("✗ No WeeklyLaborCost entry found")

        print()

        # 2. Check ProductionLog entries
        from sqlalchemy import func, and_
        production_logs = ProductionLog.query.filter(
            and_(
                func.date(ProductionLog.timestamp) >= week_start,
                func.date(ProductionLog.timestamp) <= week_end
            )
        ).all()

        print(f"ProductionLog entries: {len(production_logs)}")
        if production_logs:
            # Group by product
            production_by_product = {}
            for log in production_logs:
                if log.product:
                    if log.product.id not in production_by_product:
                        production_by_product[log.product.id] = {
                            'name': log.product.name,
                            'is_product': log.product.is_product,
                            'is_premake': log.product.is_premake,
                            'quantity': 0,
                            'units': 0
                        }
                    production_by_product[log.product.id]['quantity'] += log.quantity_produced
                    units = log.quantity_produced * (log.product.products_per_recipe or 1)
                    production_by_product[log.product.id]['units'] += units

            print("\nProduction by product:")
            for pid, data in production_by_product.items():
                product_type = "PRODUCT" if data['is_product'] and not data['is_premake'] else "PREMAKE" if data['is_premake'] else "OTHER"
                print(f"  - {data['name']} ({product_type}): {data['quantity']} batches = {data['units']} units")

        print()

        # 3. Check for any StockLog entries
        stock_logs = StockLog.query.filter(
            and_(
                func.date(StockLog.timestamp) >= week_start,
                func.date(StockLog.timestamp) <= week_end
            )
        ).limit(10).all()

        print(f"StockLog entries (first 10): {len(stock_logs)}")
        for log in stock_logs[:5]:
            item_type = "Product" if log.product_id else "RawMat" if log.raw_material_id else "Packaging"
            print(f"  - {log.timestamp.strftime('%Y-%m-%d')}: {item_type} {log.action_type} {log.quantity}")

        print()

        # 4. Check Product data
        products = Product.query.filter_by(is_product=True, is_premake=False).count()
        premakes = Product.query.filter_by(is_premake=True).count()
        print(f"Total products in database: {products}")
        print(f"Total premakes in database: {premakes}")

        print()

        # 5. Recent production activity
        recent_logs = ProductionLog.query.order_by(ProductionLog.timestamp.desc()).limit(5).all()
        print("Most recent production (any week):")
        for log in recent_logs:
            if log.product:
                print(f"  - {log.timestamp.strftime('%Y-%m-%d')}: {log.product.name} x{log.quantity_produced}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python debug_weekly_report.py YYYY-MM-DD")
        print("Example: python debug_weekly_report.py 2024-12-08")
        sys.exit(1)

    debug_week(sys.argv[1])