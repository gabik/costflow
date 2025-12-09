#!/usr/bin/env python3
"""
Bulk translation replacement script for template files.
Replaces hardcoded Hebrew strings with {{ _('English') }} translation calls.
"""
import re
import sys
from pathlib import Path

# Translation mapping: Hebrew -> English
TRANSLATIONS = {
    # Already added strings
    "Food cost includes raw materials, packaging, and premakes for products produced this week.": "עלות המזון כוללת חומרי גלם, אריזות והכנות מקדימות (פרימייקים) עבור מוצרים שיוצרו השבוע.",

    # Stock & Audits
    "סה\"כ": "Total",
    "הפרשי מלאי שנמצאו השבוע": "Stock Discrepancies Found This Week",
    "סיכום הפרשים": "Discrepancy Summary",
    "מספר ספירות:": "Number of Counts:",
    "סך ערך כספי:": "Total Monetary Value:",
    "הפרשים לפי קטגוריה": "Discrepancies by Category",
    "רווח מתואם (כולל הפרשי מלאי):": "Adjusted Profit (including stock discrepancies):",
    "במקום": "instead of",
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
    "מוצגים 10 המוצרים עם עלות הפחת הגבוהה ביותר": "Showing top 10 products with highest waste cost",
    "פחת לפי קטגוריה": "Waste by Category",
    "עלות כוללת": "Total Cost",
}


def replace_hebrew_in_line(line):
    """Replace Hebrew strings in a single line with translation calls"""
    result = line

    # Sort by length (longest first) to avoid partial replacements
    for hebrew, english in sorted(TRANSLATIONS.items(), key=lambda x: len(x[0]), reverse=True):
        # Only replace if not already in _() or {% trans %}
        pattern = re.escape(hebrew)

        # Check if the Hebrew text appears outside of translation calls
        matches = list(re.finditer(pattern, result))
        for match in reversed(matches):
            start, end = match.span()

            # Look back to see if already in translation
            lookback_start = max(0, start - 50)
            lookback = result[lookback_start:start]

            # Skip if already translated
            if "{{ _(" in lookback or "{% trans" in lookback:
                continue

            # Replace with translation call
            replacement = f"{{{{ _('{english}') }}}}"
            result = result[:start] + replacement + result[end:]

    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python bulk_translate.py <template_file>")
        sys.exit(1)

    filepath = Path(sys.argv[1])
    if not filepath.exists():
        print(f"File not found: {filepath}")
        sys.exit(1)

    print(f"Processing: {filepath}")

    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    translated_lines = []
    changes_count = 0

    for i, line in enumerate(lines, 1):
        translated = replace_hebrew_in_line(line)
        if translated != line:
            changes_count += 1
            print(f"Line {i}: Translated")
        translated_lines.append(translated)

    # Write back
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(translated_lines)

    print(f"\nCompleted! {changes_count} lines modified.")


if __name__ == "__main__":
    main()
