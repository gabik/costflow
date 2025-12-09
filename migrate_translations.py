#!/usr/bin/env python3
"""
Script to help migrate hardcoded Hebrew strings to translation functions.
This creates a mapping of Hebrew strings to English translations.
"""

# Common translation mappings (Hebrew -> English)
TRANSLATIONS = {
    # Navigation & Common
    "מערכת ניהול עלויות": "Cost Management System",
    "ניהול עלויות": "Cost Management",
    "דשבורד": "Dashboard",
    "הגדרות": "Settings",

    # Reports
    "דו\"ח שבועי": "Weekly Report",
    "דו\"ח חודשי": "Monthly Report",
    "הכנסות": "Revenue",
    "עלות חומרים": "Material Costs",
    "עלות לייבור": "Labor Costs",
    "רווח נטו": "Net Profit",
    "בחר שבוע:": "Select week:",
    "שבוע": "Week",
    "אין שבועות זמינים": "No weeks available",
    "הדפסה": "Print",
    "לא נמצאו נתונים עבור השבוע הנבחר": "No data found for selected week",
    "סיכום ביצועים לשבוע": "Performance summary for week",

    # Food Cost
    "ניתוח עלות מזון מוצרים (Food Cost)": "Product Food Cost Analysis (Food Cost)",
    "מגמות חודשיות": "Monthly Trends",
    "סך עלות מזון": "Total Food Cost",
    "עלות ממוצעת למתכון": "Average Cost per Recipe",
    "סך מתכונים שיוצרו": "Total Recipes Produced",
    "מוצר": "Product",
    "פעמי ייצור": "Production Count",
    "סך מתכונים": "Total Recipes",
    "יח' למתכון": "Units per Recipe",
    "עלות למתכון": "Cost per Recipe",
    "סך עלות": "Total Cost",
    "אחוז עלות מזון:": "Food Cost Percentage:",
    "מההכנסות": "of revenue",
    "יעד מומלץ: 25-35%": "Recommended target: 25-35%",

    # Sales
    "מכירות לפי קטגוריה": "Sales by Category",
    "קטגוריה": "Category",
    "מספר מוצרים": "Number of Products",
    "כמות נמכרה": "Quantity Sold",
    "כמות פחת": "Waste Quantity",
    "רווח גולמי": "Gross Profit",
    "סה\"כ": "Total",
    "אין נתוני מכירות לפי קטגוריה": "No sales data by category",

    # Product Details
    "פירוט מכירות מוצרים": "Product Sales Details",
    "מחיר יחידה": "Unit Price",
    "ללא קטגוריה": "No Category",
    "אין נתוני מכירות למוצרים": "No product sales data",

    # Premakes
    "הכנות מקדימות": "Premakes",
    "פעילות הכנות מקדימות": "Premakes Activity",
    "שם ההכנה": "Premake Name",
    "יוצר": "Produced",
    "שומש (במוצרים)": "Used (in products)",
    "במלאי": "In Stock",
    "עלות ליחידה (לק\"ג)": "Cost per Unit (per kg)",
    "שווי ייצור כולל": "Total Production Value",
    "ניהול מתכוני בסיס והכנות (Premakes)": "Manage Base Recipes and Premakes",
    "הוסף הכנה מקדימה": "Add Premake",
    "חפש הכנה מקדימה...": "Search premake...",
    "כל הקטגוריות": "All Categories",
    "שם": "Name",
    "כמות באצ'": "Batch Quantity",
    "יחידה": "Unit",
    "פעולות": "Actions",
    "צפה בפרטים": "View Details",
    "ערוך": "Edit",
    "מחק": "Delete",
    "האם אתה בטוח שברצונך למחוק הכנה זו?": "Are you sure you want to delete this premake?",
    "לא נמצאו הכנות מקדימות.": "No premakes found.",

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
    "במקום": "instead of",
    "תאריך": "Date",
    "חומר גלם": "Raw Material",
    "הפרש": "Difference",
    "סופר": "Counter",
    "לא ידוע": "Unknown",
    "מוצגות 5 הספירות האחרונות מתוך": "Showing last 5 counts out of",

    # Insights
    "תובנות מרכזיות": "Key Insights",
    "הקטגוריה המובילה:": "Leading category:",
    "עם הכנסות של": "with revenue of",
    "המוצר הנמכר ביותר:": "Best-selling product:",
    "שיעור הרווח השבועי:": "Weekly profit margin:",
    "המוצר עם הרווחיות הגבוהה ביותר:": "Product with highest profitability:",
    "עם שיעור רווח של": "with profit margin of",
    "המוצר עם הרווחיות הנמוכה ביותר:": "Product with lowest profitability:",
    "זקוק לבחינה מחדש": "Needs review",
    "המוצר עם התרומה הגבוהה ביותר לרווח:": "Product with highest profit contribution:",
    "עם רווח כולל של": "with total profit of",
    "שיעור הפחת הכולל:": "Overall waste percentage:",
    "עלות:": "Cost:",

    # Waste Analysis
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
    "עלות פחת": "Waste Cost",
    "מוצגים 10 המוצרים עם עלות הפחת הגבוהה ביותר": "Showing top 10 products with highest waste cost",
    "פחת לפי קטגוריה": "Waste by Category",
    "כמות פחת": "Waste Quantity",
    "עלות כוללת": "Total Cost",

    # Suppliers
    "ניהול ספקים": "Supplier Management",
    "ניהול ספקי חומרי גלם ומחירים": "Manage raw material suppliers and pricing",
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
    "השבת": "Reactivate",
    "הפוך ללא פעיל": "Deactivate",
    "האם למחוק את הספק": "Delete supplier",
    "לא נמצאו ספקים במערכת.": "No suppliers found in system.",
    "הסתר ספקים לא פעילים": "Hide inactive suppliers",
    "הצג ספקים לא פעילים": "Show inactive suppliers",

    # Weekly Costs
    "ניהול עלויות שבועיות": "Weekly Cost Management",
    "מעקב אחר שעות עבודה ועלויות לפי שבוע": "Track work hours and costs by week",
    "שבוע חדש": "New Week",
    "תאריך התחלה": "Start Date",
    "סה\"כ עלות": "Total Cost",
    "מספר עובדים": "Number of Employees",
    "פרטים ועדכון": "Details and Update",
    "לא הוגדרו עלויות שבועיות עדיין.": "No weekly costs defined yet.",
    "פתיחת שבוע חדש": "Open New Week",
    "תאריך התחלת שבוע (יום ראשון)": "Week start date (Sunday)",
    "ביטול": "Cancel",
    "צור ופתח לפרטים": "Create and Open Details",

    # Products
    "מוצרים": "Products",
    "חומרי גלם": "Raw Materials",
    "אריזה": "Packaging",
    "עובדים": "Labor",
    "קטגוריות": "Categories",
    "ייצור": "Production",
    "הוסף": "Add",
    "עדכן": "Update",
    "בטל": "Cancel",
    "שמור": "Save",
    "הצג": "View",
    "כמות": "Quantity",
    "מחיר": "Price",
    "במלאי": "In Stock",

    # Common actions and messages
    "כן": "Yes",
    "לא": "No",
    "שגיאה": "Error",
    "הצלחה": "Success",
    "אזהרה": "Warning",
    "מידע": "Info",
}

def print_translations():
    """Print all translations in a format suitable for PO files"""
    print("# Common translations")
    for he, en in sorted(TRANSLATIONS.items()):
        print(f'\nmsgid "{en}"')
        print(f'msgstr "{he}"')

if __name__ == "__main__":
    print_translations()
