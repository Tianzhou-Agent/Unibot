---
name: notes
description: Use when the user wants to write down, find or remove short notes.
---

# Keep notes

- Call `list_notes` before answering questions about the user's notes; never guess what they contain.
- Save one idea per note with `add_note`. Keep the user's own words.
- Before deleting, show the matching notes with `highlight_notes` if the canvas is open, then call `delete_note`
  once per note. Unibot asks the user to confirm every deletion.
