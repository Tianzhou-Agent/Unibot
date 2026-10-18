# Monthly close

Use this workflow when the user wants to reconcile one month.

1. Call `list_expenses` with the month in `YYYY-MM` form.
2. Look for likely duplicates: the same amount and category on the same day. The category list and their meanings
   are in `references/categories.md`; load it with `load_skill` if you need it.
3. If the Expenses canvas is open, call `highlight_rows` with the ids of the suspected duplicates so the user can
   review them.
4. Ask the user which duplicates to delete. Never delete without an explicit answer; call `delete_expense` once per
   confirmed id.
5. Summarize the month: total, the three largest categories, and how many expenses were removed.
