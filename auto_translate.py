#!/usr/bin/env python3
"""
Automated translation migration script.
Replaces hardcoded Hebrew strings with {{ _('English') }} in templates.
"""
import re
import os
from pathlib import Path

# Comprehensive translation mapping
TRANSLATIONS = {
    # Main headers and navigation
    "מערכת ניהול עלויות": "Cost Management System",
    "ניהול עלויות": "Cost Management",
    "CostFlow": "CostFlow",

    # Reports
    "דו\"ח שבועי": "Weekly Report",
    "דו\"ח חודשי": "Monthly Report",
    "סיכום ביצועים לשבוע": "Performance summary for week",
    "סיכום ביצועים לחודש": "Performance summary for month",

    # Summary cards
    "הכנסות": "Revenue",
    "עלות חומרים": "Material Costs",
    "עלות לייבור": "Labor Costs",
    "רווח נטו": "Net Profit",
    "סך הכנסות": "Total Revenue",

    # Food Cost
    "ניתוח עלות מזון מוצרים (Food Cost)": "Product Food Cost Analysis (Food Cost)",
    "ניתוח עלות מזון (Food Cost) - חודשי": "Monthly Food Cost Analysis (Food Cost)",
    "מגמות חודשיות": "Monthly Trends",
    "סך עלות מזון": "Total Food Cost",
    "עלות ממוצעת למתכון": "Average Cost per Recipe",
    "סך מתכונים שיוצרו": "Total Recipes Produced",
    "אחוז עלות מזון": "Food Cost Percentage",
    "מגמת עלות מזון שבועית": "Weekly Food Cost Trend",

    # Table headers
    "מוצר": "Product",
    "פעמי ייצור": "Production Count",
    "סך מתכונים": "Total Recipes",
    "יח' למתכון": "Units per Recipe",
    "עלות למתכון": "Cost per Recipe",
    "סך עלות": "Total Cost",
    "קטגוריה": "Category",
    "מספר מוצרים": "Number of Products",
    "כמות נמכרה": "Quantity Sold",
    "כמות פחת": "Waste Quantity",
    "רווח גולמי": "Gross Profit",
    "מחיר יחידה": "Unit Price",
    "הכנסות": "Revenue",

    # Sales
    "מכירות לפי קטגוריה": "Sales by Category",
    "פירוט מכירות מוצרים": "Product Sales Details",
    "ללא קטגוריה": "No Category",

    # Premakes
    "הכנות מקדימות": "Premakes",
    "פעילות הכנות מקדימות": "Premakes Activity",
    "שם ההכנה": "Premake Name",
    "יוצר": "Produced",
    "שומש (במוצרים)": "Used (in products)",
    "במלאי": "In Stock",
    "עלות ליחידה (לק\"ג)": "Cost per Unit (per kg)",
    "שווי ייצור כולל": "Total Production Value",
    "ניהול מתכוני בסיס והכנות (Premakes).": "Manage base recipes and premakes.",
    "הוסף הכנה מקדימה": "Add Premake",
    "חפש הכנה מקדימה...": "Search premake...",

    # Labor
    "פירוט עלויות לייבור": "Labor Cost Details",
    "עובד": "Employee",
    "פעמי רישום": "Entry Count",
    "סך שעות": "Total Hours",
    "שם עובד": "Employee Name",
    "עלות שעתית": "Hourly Rate",

    # Stock Audits
    "הפרשי מלאי שנמצאו השבוע": "Stock Discrepancies Found This Week",
    "סיכום הפרשים": "Discrepancy Summary",
    "מספר ספירות:": "Number of Counts:",
    "סך ערך כספי:": "Total Monetary Value:",
    "הפרשים לפי קטגוריה": "Discrepancies by Category",
    "ספירות": "Counts",
    "ערך כספי": "Monetary Value",
    "רווח מתואם (כולל הפרשי מלאי):": "Adjusted Profit (including stock discrepancies):",

    # Insights
    "תובנות מרכזיות": "Key Insights",
    "תובנות חודשיות": "Monthly Insights",
    "נתונים מרכזיים": "Key Data",
    "ביצועים מובילים": "Leading Performance",
    "הקטגוריה המובילה:": "Leading category:",
    "קטגוריה מובילה:": "Leading category:",
    "מוצר מוביל:": "Leading product:",
    "המוצר הנמכר ביותר:": "Best-selling product:",
    "שיעור הרווח השבועי:": "Weekly profit margin:",
    "שיעור רווח חודשי:": "Monthly profit margin:",

    # Waste
    "ניתוח פחת": "Waste Analysis",
    "סה\"כ פחת": "Total Waste",
    "יחידות": "Units",
    "מהייצור": "of production",
    "עלות פחת": "Waste Cost",
    "השפעה על רווח": "Impact on Profit",
    "פחת לפי מוצר": "Waste by Product",
    "נמכר": "Sold",
    "פחת": "Waste",
    "% פחת": "Waste %",
    "פחת לפי קטגוריה": "Waste by Category",
    "כמות פחת": "Waste Quantity",

    # Suppliers
    "ניהול ספקים": "Supplier Management",
    "ניהול ספקי חומרי גלם ומחירים.": "Manage raw material suppliers and pricing.",
    "הוסף ספק חדש": "Add New Supplier",
    "שם הספק": "Supplier Name",
    "איש קשר": "Contact Person",
    "טלפון": "Phone",
    "אימייל": "Email",
    "חומרים פעילים": "Active Materials",
    "סטטוס": "Status",
    "פעיל": "Active",
    "לא פעיל": "Inactive",
    "צפה בחומרים": "View Materials",
    "צפה בפרטים": "View Details",
    "הסתר ספקים לא פעילים": "Hide inactive suppliers",
    "הצג ספקים לא פעילים": "Show inactive suppliers",
    "לא נמצאו ספקים במערכת.": "No suppliers found in system.",

    # Weekly Costs
    "ניהול עלויות שבועיות": "Weekly Cost Management",
    "מעקב אחר שעות עבודה ועלויות לפי שבוע.": "Track work hours and costs by week.",
    "שבוע חדש": "New Week",
    "תאריך התחלה": "Start Date",
    "סה\"כ עלות": "Total Cost",
    "מספר עובדים": "Number of Employees",
    "פרטים ועדכון": "Details and Update",
    "לא הוגדרו עלויות שבועיות עדיין.": "No weekly costs defined yet.",
    "פתיחת שבוע חדש": "Open New Week",
    "תאריך התחלת שבוע (יום ראשון)": "Week start date (Sunday)",

    # Weekly trends
    "מגמות שבועיות": "Weekly Trends",
    "ביצועי קטגוריות": "Category Performance",
    "10 המוצרים המובילים": "Top 10 Products",
    "שבוע": "Week",
    "Food Cost %": "Food Cost %",
    "מתכונים": "Recipes",
    "הפרשי מלאי": "Stock Discrepancies",
    "שבועות פעילים": "Active Weeks",

    # Common UI elements
    "בחר שבוע:": "Select week:",
    "הדפסה": "Print",
    "לא נמצאו נתונים עבור השבוע הנבחר": "No data found for selected week",
    "לא נמצאו נתונים עבור החודש הנבחר": "No data found for selected month",
    "אין שבועות זמינים": "No weeks available",
    "אין נתונים להצגה": "No data to display",
    "אין נתונים שבועיים": "No weekly data",
    "אין נתוני קטגוריות": "No category data",
    "אין נתוני מוצרים": "No product data",
    "אין נתוני מכירות לפי קטגוריה": "No sales data by category",
    "אין נתוני מכירות למוצרים": "No product sales data",

    # Common fields
    "שם": "Name",
    "תאריך": "Date",
    "כמות": "Quantity",
    "מחיר": "Price",
    "יחידה": "Unit",
    "כמות באצ'": "Batch Quantity",
    "פעולות": "Actions",
    "סה\"כ": "Total",
    "עלות כוללת": "Total Cost",
    "ממוצע שבועי:": "Weekly average:",
    "יעד:": "Target:",
    "יעד": "Target",

    # Actions
    "ערוך": "Edit",
    "מחק": "Delete",
    "הצג": "View",
    "הוסף": "Add",
    "עדכן": "Update",
    "שמור": "Save",
    "בטל": "Cancel",
    "ביטול": "Cancel",
    "צור ופתח לפרטים": "Create and Open Details",
    "שמור שינויים": "Save Changes",
    "הצג דו\"ח": "Show Report",

    # Confirmations
    "האם אתה בטוח שברצונך למחוק הכנה זו?": "Are you sure you want to delete this premake?",
    "האם למחוק את הספק": "Delete supplier",
    "השבת": "Reactivate",
    "הפוך ללא פעיל": "Deactivate",

    # Status messages
    "לא נמצאו הכנות מקדימות.": "No premakes found.",
    "לא ידוע": "Unknown",

    # Categories
    "כל הקטגוריות": "All Categories",

    # Months (Hebrew)
    "ינואר": "January",
    "פברואר": "February",
    "מרץ": "March",
    "אפריל": "April",
    "מאי": "May",
    "יוני": "June",
    "יולי": "July",
    "אוגוסט": "August",
    "ספטמבר": "September",
    "אוקטובר": "October",
    "נובמבר": "November",
    "דצמבר": "December",
}


def escape_for_regex(text):
    """Escape special regex characters"""
    return re.escape(text)


def replace_in_file(filepath, dry_run=True):
    """Replace Hebrew strings with translation functions in a file"""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content
    replacements_made = []

    # Sort by length (longest first) to avoid partial replacements
    sorted_translations = sorted(TRANSLATIONS.items(), key=lambda x: len(x[0]), reverse=True)

    for hebrew, english in sorted_translations:
        # Pattern to match Hebrew text but NOT inside {{ _() }} or {% trans %}
        # This is a simple version - more sophisticated version would use proper HTML/Jinja2 parsing
        pattern = re.escape(hebrew)

        # Find all occurrences
        matches = list(re.finditer(pattern, content))

        for match in reversed(matches):  # Reverse to maintain positions
            start, end = match.span()

            # Check if already inside a translation function
            # Look back for {{ _( or {% trans
            lookback = content[max(0, start-50):start]
            if "{{ _(" in lookback or "{% trans" in lookback:
                continue

            # Replace with translation function
            replacement = f"{{{{ _('{english}') }}}}"
            content = content[:start] + replacement + content[end:]
            replacements_made.append((hebrew, english))

    if content != original_content:
        if not dry_run:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
        return True, len(replacements_made)
    return False, 0


def main():
    templates_dir = Path("templates")

    print("Scanning templates for hardcoded Hebrew strings...")
    print("=" * 60)

    for template_file in templates_dir.glob("*.html"):
        changed, count = replace_in_file(template_file, dry_run=True)
        if changed:
            print(f"✓ {template_file.name}: {count} replacements")

    print("\n" + "=" * 60)
    print("This was a DRY RUN. To apply changes, modify dry_run=False")


if __name__ == "__main__":
    main()
