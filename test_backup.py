#!/usr/bin/env python3
"""Test script to verify the backup system includes all necessary data."""

import json
import sys
from datetime import datetime


def check_backup(filename):
    """Verify that a backup file contains all required models and data."""

    print(f"\n{'='*60}")
    print(f"Costflow Backup Verification Report")
    print(f"{'='*60}")
    print(f"File: {filename}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"❌ Error: File '{filename}' not found")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ Error: Invalid JSON in file - {e}")
        return False

    # Check version
    version = data.get('version', '1.0')
    print(f"📋 Backup Version: {version}")
    print(f"📅 Backup Created: {data.get('timestamp', 'Unknown')}")
    print(f"💾 Database Type: {data.get('database_type', 'Unknown')}")
    print()

    # Define required models for v2.0
    required_models_v2 = [
        'categories',
        'suppliers',
        'labor',
        'audit_logs',
        'raw_materials',
        'raw_material_suppliers',
        'raw_material_alternative_names',
        'packaging',
        'packaging_suppliers',
        'products',
        'production_logs',
        'stock_logs',
        'stock_audits',
        'weekly_labor_costs'
    ]

    # Define models that were in v1.0
    models_v1 = [
        'categories',
        'raw_materials',
        'packaging',
        'labor',
        'products',
        'weekly_labor_costs'
    ]

    expected_models = required_models_v2 if version == '2.0' else models_v1

    print("📊 Model Coverage:")
    print("-" * 40)

    missing_models = []
    model_counts = {}

    for model in expected_models:
        if model in data:
            count = len(data[model])
            model_counts[model] = count
            status = "✅" if count > 0 else "⚠️"
            print(f"{status} {model:30s}: {count:5d} records")
        else:
            missing_models.append(model)
            print(f"❌ {model:30s}: MISSING!")

    # Check for unexpected models (good to have extras)
    print("\n📦 Additional Models (if any):")
    print("-" * 40)
    for key in data.keys():
        if key not in expected_models and key not in ['version', 'timestamp', 'database_type', 'statistics']:
            print(f"➕ {key}: {len(data[key]) if isinstance(data[key], list) else 'N/A'} records")

    # Statistics
    if 'statistics' in data:
        print("\n📈 Statistics:")
        print("-" * 40)
        stats = data['statistics']
        print(f"Total Records: {stats.get('total_records', 'Unknown')}")

        if 'model_counts' in stats:
            print("\nDetailed Counts from Statistics:")
            for model, count in stats['model_counts'].items():
                print(f"  {model}: {count}")

    # Validation Summary
    print("\n" + "="*60)
    print("🔍 VALIDATION SUMMARY")
    print("="*60)

    all_good = True

    if version == '2.0':
        print("\n✅ Version 2.0 Backup Detected")

        # Check critical models for production use
        critical_checks = {
            'stock_logs': 'Inventory history',
            'production_logs': 'Production journal',
            'suppliers': 'Supplier information',
            'raw_material_suppliers': 'Material-supplier links',
            'packaging_suppliers': 'Packaging-supplier links',
            'stock_audits': 'Physical count records'
        }

        print("\n🔑 Critical Data Verification:")
        for model, description in critical_checks.items():
            if model in data and len(data[model]) >= 0:
                print(f"  ✅ {description:25s} ({model})")
            else:
                print(f"  ❌ {description:25s} ({model}) - MISSING!")
                all_good = False

    elif version == '1.0':
        print("\n⚠️ Legacy Version 1.0 Backup Detected")
        print("   This backup is missing critical operational data:")
        print("   - No inventory history (StockLog)")
        print("   - No production journal (ProductionLog)")
        print("   - No supplier relationships")
        print("   - No stock audits")
        print("   Consider upgrading to v2.0 backup format")
        all_good = False

    if missing_models:
        print(f"\n❌ Missing {len(missing_models)} required models:")
        for model in missing_models:
            print(f"   - {model}")
        all_good = False

    # Final verdict
    print("\n" + "="*60)
    if all_good:
        print("✅ BACKUP VALIDATION PASSED")
        print("   All required data is present in the backup")
    else:
        print("❌ BACKUP VALIDATION FAILED")
        print("   Some required data is missing or using legacy format")
    print("="*60)

    return all_good


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_backup.py <backup_file.json>")
        print("\nExample:")
        print("  python test_backup.py costflow_backup_v2_20241211_150000.json")
        sys.exit(1)

    backup_file = sys.argv[1]
    success = check_backup(backup_file)
    sys.exit(0 if success else 1)