---
name: draw-diagram
description: Use when the user wants a flowchart, architecture sketch or other diagram drawn or changed on the whiteboard.
---
# Draw a diagram

1. Call `describe_scene` first so you build on what is already on the board instead of drawing over it.
2. For a flowchart, sequence or other diagram Mermaid can express, call `create_from_mermaid` with the Mermaid
   source. Otherwise call `read_diagram_guide` once, then draw every shape and arrow in one `batch_create_elements`
   call, binding arrows to shapes with `startElementId` and `endElementId`.
3. Leave room between new shapes and existing ones; place new work to the right of or below the current scene.
4. Call `set_viewport` to fit what you drew, so the user sees it at http://127.0.0.1:3000.
5. To change one element, find it with `query_elements` or `describe_scene` and call `update_element`. Ask before
   deleting elements or clearing the board.
6. To save the drawing, call `export_scene` (an `.excalidraw` file) or `export_to_image` (PNG or SVG) with a path
   in the exports folder, for example `exports/onboarding.png`. Other folders are refused.
7. Reply with one or two sentences that say what you drew and where you saved it.
