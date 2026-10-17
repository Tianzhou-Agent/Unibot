---
name: record-expense
description: Use when the user mentions money they spent and wants it recorded.
---
# Record an expense

1. Extract the amount, a category and an optional note from the user's message. If the amount is missing, ask for
   it; do not guess.
2. Use one of these categories: food, transport, housing, shopping, health, entertainment, other.
3. Call `add_expense` once per expense. The user confirms each new expense before it is saved.
4. If the user asks what they already recorded, call `list_expenses` first instead of adding a duplicate.
5. Reply with one short sentence that repeats the amount and category that were saved.
