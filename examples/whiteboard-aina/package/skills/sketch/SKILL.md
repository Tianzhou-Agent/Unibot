---
name: sketch
description: Use when the user wants something drawn, annotated or rearranged on the open whiteboard.
---
# Sketch on the whiteboard

The board is 1200 x 800 scene pixels; (0, 0) is the top-left corner. The canvas state in your context lists what is
on the board; call `get_scene` when you need exact positions or ids.

1. To mark an existing shape (for example "circle the login box"), find it in the scene, then draw around it: a circle
   centered on the shape's center with a radius about 0.6 x its larger side, or a slightly larger rectangle.
2. To add a diagram, lay it out left to right with at least 40 px between shapes. Give every box a short `label`, and
   connect boxes with `draw_arrow` from the right edge of one to the left edge of the next.
3. Use `#1e1e1e` for normal strokes, `#e03131` to highlight, `#2f9e44` for success paths.
4. Only call `clear_scene` when the user explicitly asks to clear the board; Superbot asks them to confirm.
5. After drawing, describe in one sentence what you added. The user's board is saved when they press Save; while they are away, your changes are saved as you make them.
