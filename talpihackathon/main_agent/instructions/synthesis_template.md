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

```plotly
{
  "data": [ {PLOTLY_TREND_TRACES} ],
  "layout": {
    "title": "מגמה תקציבית לאורך השנים",
    "xaxis": { "title": "שנה", "type": "category" },
    "yaxis": { "title": "תקציב ב-₪", "rangemode": "tozero", "separatethousands": true }
  }
}
```

{BUDGET_TABLE}

## תכניות פעילות כיום

### התפלגות היקפי התקשרויות לפי ספק (15 הספקים המובילים) בשנים {CONTRACTS_YEARS}

```plotly
{
  "data": [
    { "type": "pie", "textinfo": "label+percent",
      "labels": [ {PLOTLY_PIE_LABELS} ],
      "values": [ {PLOTLY_PIE_VALUES} ] }
  ],
  "layout": { "title": "התפלגות היקפי התקשרויות לפי ספק (15 המובילים) בשנים {CONTRACTS_YEARS}" }
}
```

## מקורות תקציב

{BUDGET_HIERARCHY_EXPLANATION}

```plotly
{
  "data": [
    { "type": "pie", "textinfo": "label+percent",
      "labels": [ {PLOTLY_SOURCES_LABELS} ],
      "values": [ {PLOTLY_SOURCES_VALUES} ] }
  ],
  "layout": { "title": "מקורות תקציב" }
}
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
