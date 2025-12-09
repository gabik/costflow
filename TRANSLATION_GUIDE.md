# Translation Migration Guide

## Overview

This guide documents the translation migration for the CostFlow application, moving all hardcoded Hebrew strings to use Flask-Babel's translation system for full HE/EN language support.

## What Was Done

### 1. Partial Template Migration
- **weekly_report.html**: ~50% translated (first 320 lines)
  - Headers, navigation, summary cards, food cost analysis
  - Sales sections, premakes, labor costs
  - Remaining: Stock discrepancies, insights, waste analysis sections

### 2. Translation Files Created
- `translations_to_add.po` - Comprehensive Hebrew→English mappings (800+ strings)
- `migrate_translations.py` - Translation mapping reference
- `bulk_translate.py` - Automated replacement script
- `auto_translate.py` - Alternative automation script

### 3. Files Requiring Translation

#### High Priority Templates (Reports & Main Pages)
1. ✅ `weekly_report.html` (50% done - 604 lines)
2. ⬜ `monthly_report.html` (521 lines)
3. ⬜ `index.html` (358 lines)
4. ⬜ `base.html` (navigation, common elements)

#### CRUD Templates
5. ⬜ `add_or_edit_product.html` (942 lines)
6. ⬜ `add_or_edit_raw_material.html` (638 lines)
7. ⬜ `add_or_edit_premake.html` (420 lines)
8. ⬜ `product_details.html` (384 lines)
9. ⬜ `premake_details.html` / `view_premake.html`
10. ⬜ `raw_materials.html` (462 lines)
11. ⬜ `premakes.html` (127 lines)
12. ⬜ `suppliers.html`
13. ⬜ `supplier_materials.html`

#### Production & Costs
14. ⬜ `production.html` (388 lines)
15. ⬜ `premake_production.html` (355 lines)
16. ⬜ `weekly_costs.html`
17. ⬜ `weekly_cost_details.html`
18. ⬜ `update_weekly_sales.html`
19. ⬜ `close_week.html`

#### Other Templates
20. ⬜ `recipe_review.html` (465 lines)
21. ⬜ `recipe_upload.html`
22. ⬜ `upload_inventory.html`
23. ⬜ `stock_audits.html`
24. ⬜ `categories.html`
25. ⬜ `packaging.html`
26. ⬜ `labor.html`
27. ⬜ `add_or_edit_supplier.html`
28. ⬜ `add_or_edit_packaging.html`
29. ⬜ `add_or_edit_labor.html`
30. ⬜ `audit_log.html`

#### Python Route Files (Flash Messages)
31. ⬜ `app/routes/products.py`
32. ⬜ `app/routes/premakes.py`
33. ⬜ `app/routes/raw_materials.py`
34. ⬜ `app/routes/suppliers.py`
35. ⬜ `app/routes/production.py`
36. ⬜ `app/routes/weekly_costs.py`
37. ⬜ `app/routes/reports.py`
38. ⬜ `app/routes/inventory.py`
39. ⬜ `app/routes/recipe_import.py`
40. ⬜ `app/routes/admin.py`
41. ⬜ `app/routes/categories.py`
42. ⬜ `app/routes/labor.py`
43. ⬜ `app/routes/packaging.py`
44. ⬜ `app/routes/main.py`

## How to Continue

### Manual Approach (Recommended for Quality)

1. **For Each Template File:**
   ```bash
   # Open the file
   vim templates/weekly_report.html

   # Find Hebrew strings (not in {{ _() }})
   # Replace with: {{ _('English translation') }}
   ```

2. **Example Pattern:**
   ```html
   <!-- Before -->
   <h5>הכנסות</h5>

   <!-- After -->
   <h5>{{ _('Revenue') }}</h5>
   ```

3. **For Python Files:**
   ```python
   # Before
   from flask import flash
   flash('פעולה הצליחה')

   # After
   from flask import flash
   from flask_babel import gettext as _
   flash(_('Operation successful'))
   ```

### Semi-Automated Approach

Use the provided `translations_to_add.po` file as reference:

1. Search for Hebrew string in template
2. Look up English equivalent in `translations_to_add.po`
3. Replace with `{{ _('English') }}`

### After Template Migration

1. **Extract All Strings:**
   ```bash
   # This will scan all templates and Python files
   pybabel extract -F babel.cfg -o messages.pot .
   ```

2. **Update Translation Files:**
   ```bash
   # Update Hebrew translations
   pybabel update -i messages.pot -d translations -l he

   # Update English translations
   pybabel update -i messages.pot -d translations -l en
   ```

3. **Add Translations:**
   - Open `translations/he/LC_MESSAGES/messages.po`
   - For each `msgid "English"`, add `msgstr "Hebrew"`
   - Open `translations/en/LC_MESSAGES/messages.po`
   - For each `msgid "English"`, add `msgstr "English"` (same)
   - Use `translations_to_add.po` as your comprehensive reference

4. **Compile Translations:**
   ```bash
   pybabel compile -d translations
   ```

5. **Test:**
   ```bash
   # Start the app
   python run.py

   # Test Hebrew (default)
   http://localhost:8080/

   # Test English
   http://localhost:8080/?lang=en
   ```

## Translation Conventions

### Template Strings
- **Short strings**: `{{ _('Product') }}`
- **Strings with context**: `{{ _('Select week:') }}`
- **Multi-word phrases**: `{{ _('Product Sales Details') }}`

### Python Flash Messages
```python
from flask_babel import gettext as _

# Success
flash(_('Product created successfully'), 'success')

# Error
flash(_('Error: Invalid data'), 'error')

# Warning
flash(_('Stock level low'), 'warning')
```

### Dynamic Content
```html
<!-- With variables -->
{{ _('Performance summary for week') }} {{ week_start.strftime('%d/%m/%Y') }}

<!-- With formatting -->
{{ _('Total:') }} {{ currency_symbol }}{{ "%.2f"|format(total) }}
```

## Common Translations Reference

See `translations_to_add.po` for 800+ pre-mapped translations including:
- Navigation & UI elements
- Report headers and metrics
- Table columns
- Actions (Edit, Delete, View, etc.)
- Status messages
- Form labels
- Error messages

## Progress Tracking

Update this checklist as you complete files:

**Templates Completed: 1/32** (3%)
- [x] weekly_report.html (partial - 50%)
- [ ] monthly_report.html
- [ ] index.html
- [ ] (see full list above)

**Python Files Completed: 0/16** (0%)

## Next Steps

1. **Immediate:** Complete remaining sections of `weekly_report.html`
2. **High Priority:** Translate `monthly_report.html`, `index.html`, `base.html`
3. **Medium Priority:** Main CRUD templates (products, premakes, raw_materials)
4. **Final:** Python flash messages and remaining templates

## Git Workflow

After completing translation migration:

```bash
git add .
git commit -m "Feat: Add comprehensive i18n support for HE/EN languages

- Migrate all template strings to Flask-Babel translation system
- Add Hebrew and English translation files
- Enable language switching via ?lang= parameter
- Update CLAUDE.md with translation workflow

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
git push
```

## Testing Checklist

After migration:
- [ ] All pages load without errors
- [ ] Hebrew displays correctly (default)
- [ ] English displays correctly (?lang=en)
- [ ] Language persists across pages
- [ ] No untranslated strings visible
- [ ] Flash messages appear in correct language
- [ ] Form labels translated
- [ ] Error messages translated
- [ ] Reports display correctly in both languages

## Notes

- Default language: Hebrew (he)
- Fallback language: English (en)
- Language selection persists in session
- Can be overridden with `?lang=` parameter
- Current translation coverage: ~5% complete
- Estimated remaining work: 40-60 hours for complete migration
