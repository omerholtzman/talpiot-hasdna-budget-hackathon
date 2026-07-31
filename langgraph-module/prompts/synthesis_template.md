---
title: נתוני תקציב, התקשרויות והחלטות ממשלה בתחום {SUBJECT_HEBREW}
created: {TODAY}
updated: {TODAY}
model: {MODEL}
path: reports/{SUBJECT_SLUG}
---

# נתוני תקציב, התקשרויות והחלטות ממשלה בתחום {SUBJECT_HEBREW}

{SUMMARY}

## מגמה תקציבית לאורך זמן

{BUDGET_XYCHART_LINES}

## תכניות פעילות כיום

### התפלגות היקפי התקשרויות לפי ספק (15 הספקים המובילים) בשנים {CONTRACTS_YEARS}

```mermaid
pie
    title התפלגות היקפי התקשרויות לפי ספק (15 המובילים) בשנים {CONTRACTS_YEARS}
    {MERMAID_PIE_DATA}
```

## מקורות תקציב

{BUDGET_HIERARCHY_EXPLANATION}

```mermaid
graph TD
    {MERMAID_FLOWCHART_DATA}
```

### רשימת סעיפי תקציב נבחרים (עם קישורים)
{BUDGET_HIERARCHY_LIST}


## נושאים נוספים

### התקשרויות/חוזים
{CONTRACTS_TABLE}

### ספקים
{SUPPLIERS_TABLE}

### החלטות ממשלה
{DECISIONS_TABLE}

## מקורות
{SOURCES_LIST}

[חזרה לעמוד הראשי](../../README.md)
