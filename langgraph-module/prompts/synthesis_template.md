# {SUBJECT_HEBREW}
{Summary of the subject and its significance. Don't add meta-statement like "דף זה נוצר על ידי שימוש ב-MCP" or "דשבורד זה מרכז מידע..."}

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

## תכניות פעילות כיום
### סעיפים בולטים
10 הסעיפים הגדולים בנושא בתקציב הנוכחי:
```plotly
{
  "data": [
    { "type": "pie", "textinfo": "label+percent",
      "labels": [ {PLOTLY_PIE_LABELS} ],
      "values": [ {PLOTLY_PIE_VALUES} ] }
  ],
  "layout": { "title": "סעיפי התקציב הגדולים ביותר (top 10)" }
}
```

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

### רשימת סעיפי תקציב נבחרים
{BUDGET_HIERARCHY_LIST - Including links to the specific items}


## נושאים נוספים

### התקשרויות/חוזים
{CONTRACTS_COMMENTS - How many of them have "פטור ממכרז"?}
{CONTRACTS_TABLE sorted by descending order of budget}

### ספקים
{SUPPLIERS_TABLE sorted by descending order of budget}

### החלטות ממשלה
{DECISIONS_TABLE sorted by recency (descending)}

[חזרה לעמוד הראשי](../../README.md)

---
title: נתוני תקציב, התקשרויות והחלטות ממשלה בתחום {SUBJECT_HEBREW}
created: {TODAY}
updated: {TODAY}
model: {MODEL}
path: reports/{SUBJECT_SLUG}
---
