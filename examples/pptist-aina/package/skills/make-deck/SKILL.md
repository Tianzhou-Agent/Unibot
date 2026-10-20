---
name: make-deck
description: Use when the user wants a presentation drafted, slides added or changed, a deck reviewed, or a deck exported as .pptx in PPTist.
---
# Make a slide deck in PPTist

PPTist is open beside the conversation. Your context shows the open deck: its id, title, and each slide's id and title.
All tools except `list_decks` act on the open canvas; if it is closed, ask the user to open the Slides canvas.

Slides are 1000 x 562.5 logical px (16:9), origin at the top-left. Write all text in the user's language.

## A new presentation

1. Agree on the topic, audience and length when the request leaves them open (a short question is enough); otherwise
   go ahead.
2. Write the outline and call `generate_deck` with a `template` that suits the tone:
   - `cover` with `{title, text}`; the subtitle is one sentence;
   - `contents` with `{items: [...]}`, the section names, matching the transition slides;
   - per section, one `transition` with `{title, text}` and one or more `content` with `{title, items: [{title, text}]}`, with
     2 to 4 items per slide (up to 12 split across slides) and item text of one or two sentences;
   - `end` last.
3. The templates keep a few fixed Chinese labels: the contents slide's heading and the end slide's thank-you. For a deck
   in another language, replace them with `update_elements`.
4. Check the cover and the densest content slide with `get_slide` (see "Checking layout" below). Fix text that overflows
   its box or overlaps with `update_elements`: shorten the text first, then reduce its `font-size`.
5. Tell the user the deck is ready and what is in it, in two or three sentences. Do not list every slide.

## Changing slides

- Rewording or moving things: find the element ids with `get_deck`, then `update_elements` with only the changed
  properties (e.g. `{"content": "<p>...</p>"}`, or `{"left": 80, "top": 120}`).
- A new custom slide (a chart, a table, a comparison): write it in the slide JSON of
  [references/slide-schema.md](references/slide-schema.md) and call `add_slides`. Reuse the deck's theme colors and
  fonts from `get_deck`, and copy positions from a similar slide so the deck stays consistent.
- Re-laying out a slide: `get_slide`, change the JSON, `update_slide` with the same slide id.
- Check every slide you add or re-lay out before you answer (see "Checking layout").
- Text is HTML limited to `p`, `ul`, `ol`, `li`, `blockquote`, `strong`, `em`, `u`, `strike`, `sup`, `sub`, `code` and
  `span` (styles: `color`, `background-color`, `font-size`, `font-family`; `text-align` on `p`). Anything else is
  removed.
- Pictures: add an `image` element only with an `https://` URL the user gave you. The schema's placeholder picture
  rule does not apply here; without a picture, use shapes and color instead.

## Checking layout

Check geometry with `get_slide`: every element stays inside 1000 x 562.5, boxes that should not touch do not overlap,
and each text box is tall enough for its text. A text box needs about lines x font size x line height (1.5 unless set)
plus 20 px of padding; estimate a line as box width / (0.55 x font size) Latin characters, or box width / font size
CJK characters.

`render_slide` returns a picture of a slide. Superbot does not pass canvas pictures to you yet (you get
"[image omitted]"), so rely on geometry; the picture still helps when the user asks to see a slide.

## Decks, saving and export

- The canvas saves the open deck by itself; never tell the user to save. `new_deck`, `open_deck` and `generate_deck`
  save the deck shown before they replace it.
- `list_decks` lists saved decks even when the canvas is closed; `open_deck` shows one.
- Delete slides (`delete_slides`) or decks (`delete_deck`) only when the user asks; Superbot asks them to confirm.
- `export_pptx` downloads the open deck as .pptx in the user's browser; use it when the user asks for a file.
