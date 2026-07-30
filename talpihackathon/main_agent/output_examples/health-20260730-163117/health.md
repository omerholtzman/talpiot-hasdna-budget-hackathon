

Here is the information you have gathered:

**Budget Items (Phase 1):**

*   **Ministry of Health (Code 24):** Budget data for the Ministry of Health from 1997 to 2025, including allocated, revised, and used amounts.
*   **Detailed Health Functional Class (level 4):** Aggregated budget data for the "Health" functional class at level 4 from 1997 to 2025, including total allocated, revised, and used amounts.

**Contracts (Phase 2):**

*   **Top 50 Contracts:** A list of the top 50 contracts related to "Health" (budget codes starting with '24%' or '94%' or purchasing ministry "משרד הבריאות") active between 2016 and 2025, ordered by volume in descending order. This includes details like `item_url`, `budget_code`, `purpose`, `purchasing_ministry`, `purchasing_method`, `volume`, `executed`, `supplier_entity_name`, `start_year`, and `end_year`.

**Suppliers (Phase 3):**

*   Searched for individual supplier names from the contracts data in the `entities_data` dataset.
    *   **"שירותי בריאות כללית"**: entity_id: 589906114, entity_kind__he: אגודה עותמנית, received_amount: 4849912224.27, item_url: `https://next.obudget.org/i/2915c819a083`
    *   **"נובולוג (פארם אפ 1966) בע״מ"**: entity_id: 510475312, entity_kind__he: חברה פרטית, received_amount: 1200923.47, item_url: `https://next.obudget.org/i/8bd92becccac`
    *   **"סלומון לוין ואלשטיין בעמ"**: entity_id: 520030362, entity_kind__he: חברה פרטית, received_amount: 3470554.56, item_url: `https://next.obudget.org/i/07cd872ace19`
    *   **"איי איי די ג'נומיקס בע״מ"**: entity_id: 515882884, entity_kind__he: חברה פרטית, received_amount: 8719510.41, item_url: `https://next.obudget.org/i/c4d81e59f117`
    *   **"אותי (ע\"ר)"**: entity_id: 580183986, entity_kind__he: עמותה, received_amount: 16039.0, item_url: `https://next.obudget.org/i/7037402d2162`
    *   **"מכבי שירותי בריאות"**: entity_id: 589902279, entity_kind__he: אגודה עותמנית, received_amount: 2425778410.09, item_url: `https://next.obudget.org/i/33151db45d72`
    *   **"אלו"ט - אגודה לאומית לילדים ובוגרים עם אוטיזם (ע\'\'ר)"**: entity_id: 580064335, entity_kind__he: עמותה, received_amount: 13353794.54, item_url: `https://next.obudget.org/i/2ffa7bc8d710`
    *   **"גוד פארם בע״מ"**: No direct match found in the top 20 search results, so its `entity_id` and `received_amount` are unknown.
    *   **"מייהריטאג' בע״מ"**: entity_id: 513410662, entity_kind__he: חברה פרטית, received_amount: 12208984.36, item_url: `https://next.obudget.org/i/041642bd012c`
    *   **"שראל - פתרונות לוגיסטיים ומוצרים לרפואה מתקדמת בע״מ"**: entity_id: 511900805, entity_kind__he: חברה פרטית, received_amount: 72866440.66, item_url: `https://next.obudget.org/i/7b56864b903e`
    *   **"מעבדות חי בעמ"**: Not found in top 20 search results for direct match.
    *   **"הדסה מדיקל בע״מ"**: Not found in top 20 search results for direct match. Found "הסתדרות מדיצינית הדסה (חל״צ)" (entity_id: 520008095, received_amount: 241949656.69, item_url: `https://next.obudget.org/i/35f717a1f2f6`) and "קופת הפנסיה לעובדי הדסה בע\"מ" (entity_id: 500602743, received_amount: 221854541.0, item_url: `https://next.obudget.org/i/ad5b77c56a2f`).
    *   **"פייזר פרמצבטיקה ישראל בע״מ"**: Not found in top 20 search results for direct match.
    *   **"אלקטרה בעמ"**: entity_id: 510027493, entity_kind__he: חברה פרטית, received_amount: 1688795.37, item_url: `https://next.obudget.org/i/c2dc2d26ba7f`
    *   **"רם אדרת הנדסה אזרחית בע״מ"**: Not found in top 20 search results for direct match. Found "רשת המעונות - נאות מרגלית (ע\"ר)" which contains "אדרת" but is a different entity.
    *   **"אנוש - העמותה הישראלית לבריאות הנפש (ע\"ר)"**: entity_id: 580019800, entity_kind__he: עמותה, received_amount: 9258701.54, item_url: `https://next.obudget.org/i/175143b18964`
    *   **"טיפול לי עד הבית בע״מ"**: entity_id: 515795292, entity_kind__he: חברה פרטית, received_amount: 68588321.37, item_url: `https://next.obudget.org/i/37af1ad21dc3`
    *   **"רניום מדיקל בע״מ"**: Not found in top 20 search results for direct match.
    *   **"אילקס מדיקל בע״מ"**: entity_id: 520042219, entity_kind__he: חברה פרטית, received_amount: 469134.2, item_url: `https://next.obudget.org/i/0dcc94acb8db`
    *   **"קן התור הנדסה ובנין בע״מ"**: entity_id: 511817215, entity_kind__he: חברה פרטית, received_amount: 3538038.35, item_url: `https://next.obudget.org/i/300b232ddf53`

**Government Decisions (Phase 4):** No tool calls were made for this phase.

Final dashboard structure:

1.  Frontmatter (YAML block at the very top)
2.  Body Content & Sections:
    *   Stated coverage window
    *   מגמה תקציבית לאורך זמן: Line chart data for Ministry of Health budget, and Health functional class budget.
    *   תכניות פעילות כיום: Table of top contracts by volume.
    *   מקורות תקציב: Sankey diagram (or flowchart) linking budget sources (ministry, projects) to program items. (This will be a placeholder as no tool was called to gather data for this section).
    *   נושאים נוספים: Individual tables detailing the gathered tenders, suppliers, and government decisions. (Government decisions will be a placeholder)
    *   מקורות: A closing section listing source links.
    *   A back link at the very end.
