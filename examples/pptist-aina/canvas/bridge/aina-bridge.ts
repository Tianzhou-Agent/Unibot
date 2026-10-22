// Connects PPTist to Superbot when it runs as an AINA URL canvas. `prepare.sh` copies this folder to `src/aina/` in a
// PPTist checkout and loads it as a second module script after `src/main.ts`, so it shares the editor's Pinia stores
// and hooks without changing any PPTist file. Opened outside Superbot (no parent frame), it does nothing.
//
// It registers the canvas tools declared in package/aina.json, keeps the agent's view of the deck current with
// setModelContext, and saves the open deck through the AINA's MCP tools (load_deck / save_deck).
import { createApp, h, nextTick } from 'vue'
import { getActivePinia } from 'pinia'
import { nanoid } from 'nanoid'
import { debounce } from 'lodash'
import { toJpeg } from 'html-to-image'
import { useMainStore, useSlidesStore, useSnapshotStore } from '@/store'
import { db } from '@/utils/database'
import message from '@/utils/message'
import Directive from '@/directive'
import useAIPPT from '@/hooks/useAIPPT'
import useAddSlidesOrElements from '@/hooks/useAddSlidesOrElements'
import useHistorySnapshot from '@/hooks/useHistorySnapshot'
import useExport from '@/hooks/useExport'
import ThumbnailSlide from '@/views/components/ThumbnailSlide/index.vue'
import type { PPTElement, Slide, SlideTheme } from '@/types/slides'
import type { AIPPTSlide } from '@/types/AIPPT'
import type { Host } from './aina-ui.js'

type Args = Record<string, unknown>
type ToolResult = { structuredContent?: unknown; content?: ({ type: 'text'; text: string } | { type: 'image'; data: string; mimeType: string })[] }

interface Deck {
  title: string
  width: number
  height: number
  theme: SlideTheme
  slides: Slide[]
}

const TEMPLATES = ['template_1', 'template_2', 'template_3', 'template_4', 'template_5', 'template_6', 'template_7', 'template_8']
const MAX_RESULT_CHARS = 60_000 // canvas tool results may carry at most 64 KiB of structured content
const OMITTED_SRC = '[omitted data URL]'
const AUTOSAVE_MS = 1500

const slidesStore = useSlidesStore()
const mainStore = useMainStore()
const snapshotStore = useSnapshotStore()

// The UI bridge (aina-ui.js) is imported only inside Superbot's frame: on import it greets window.parent, which is the
// page itself when PPTist is opened on its own.
let host: Host
let deckId = nanoid(10)
let lastSaved = ''
// Whether the open deck has been saved: one kept when a conversation starts is then made that conversation's deck.
let deckSaved = false
// Whether the canvas is open beside a conversation (getContext().conversation, then context_changed).
let inConversation = false
let autosave = true
let saving: Promise<void> = Promise.resolve()
// In Superbot's headless canvas host (getContext().background): nobody sees this canvas while the user is away.
let background = false
let saveError = ''

// --- the deck ---------------------------------------------------------------------------------------------------------

// Store state is reactive (Proxy objects), which postMessage cannot clone; everything sent to Superbot is plain JSON.
const plain = <T>(value: T): T => JSON.parse(JSON.stringify(value))

const currentDeck = (): Deck => plain({
  title: slidesStore.title,
  width: slidesStore.viewportSize,
  height: slidesStore.viewportSize * slidesStore.viewportRatio,
  theme: slidesStore.theme,
  slides: slidesStore.slides,
})

const blankSlide = (): Slide => ({
  id: nanoid(10),
  elements: [],
  background: { type: 'solid', color: slidesStore.theme.backgroundColor },
})

// Show another deck and start its undo history from it, so undo never brings back the previous deck.
const showDeck = async (id: string, deck: Partial<Deck> | null, saved = false) => {
  deckId = id
  deckSaved = saved
  slidesStore.setTitle(deck?.title || '')
  if (deck?.width && deck.height) {
    slidesStore.setViewportSize(deck.width)
    slidesStore.setViewportRatio(deck.height / deck.width)
  }
  if (deck?.theme) slidesStore.setTheme(deck.theme)
  slidesStore.setSlides(deck?.slides?.length ? deck.slides : [blankSlide()])
  slidesStore.updateSlideIndex(0)
  mainStore.setActiveElementIdList([])
  // A loaded deck is already saved, and a new one is saved once something changes in it.
  lastSaved = JSON.stringify(currentDeck())
  await db.snapshots.clear()
  await snapshotStore.initSnapshotDatabase()
}

// Each save also makes the deck the conversation's own (bind), except the last save of a deck the user moved away from:
// that save is made in the conversation now beside the canvas, which must not get it. `force` saves an unchanged deck,
// to make it the conversation's.
const save = ({ bind = true, force = false } = {}) => {
  saving = saving.then(async () => {
    const deck = currentDeck()
    const json = JSON.stringify(deck)
    if (!autosave || (json === lastSaved && !force)) return
    try {
      await host.callTool('save_deck', { id: deckId, deck, bind })
      lastSaved = json
      deckSaved = true
    }
    catch (error) {
      saveError = (error as Error).message
      // In the background nobody declines a save: the next change tries again, and the agent hears of the failure.
      if (background) return
      // The user declined the save, or the server refused it: stop saving until the canvas is opened again.
      autosave = false
      message.warning(`Changes are no longer saved to Superbot: ${saveError}`, { duration: 6000 })
    }
  })
  return saving
}
const saveSoon = debounce(() => save(), AUTOSAVE_MS)
const flushSave = (options?: { bind?: boolean }) => {
  saveSoon.cancel()
  return save(options)
}

// --- what the agent sees ----------------------------------------------------------------------------------------------

const plainText = (html: string) => (new DOMParser().parseFromString(html, 'text/html').body.textContent || '').replace(/\s+/g, ' ').trim()

const elementText = (el: PPTElement): string => {
  if (el.type === 'text') return plainText(el.content)
  if (el.type === 'shape' && el.text) return plainText(el.text.content)
  if (el.type === 'table') return el.data.map(row => row.map(cell => cell.text).join(' | ')).join(' / ')
  if (el.type === 'chart') return `${el.chartType} chart: ${el.data.labels.join(', ')}`
  if (el.type === 'image') return el.imageType ? `${el.imageType} image` : 'image'
  return ''
}

const clip = (text: string, length: number) => (text.length > length ? `${text.slice(0, length - 1)}…` : text)

const slideTitle = (slide: Slide) => {
  for (const el of slide.elements) {
    const text = elementText(el)
    if (text && el.type !== 'image') return clip(text, 60)
  }
  return '(no text)'
}

const updateModelContext = debounce(() => {
  const { slides, slideIndex, title } = slidesStore
  const lines = slides.map((slide, index) => `${index === slideIndex ? '>' : ' '} ${index + 1}. [${slide.id}] ${slideTitle(slide)}`)
  void host.setModelContext(
    `The PPTist canvas shows the deck "${title}" (deck_id ${deckId}), ${slides.length} slide(s); the user is on slide ${slideIndex + 1}.\n${lines.join('\n')}`,
  )
}, 500)

const deckOutline = (textLength: number, withElements: boolean) => ({
  deck_id: deckId,
  title: slidesStore.title,
  size: { width: slidesStore.viewportSize, height: slidesStore.viewportSize * slidesStore.viewportRatio },
  theme: slidesStore.theme,
  current_slide: slidesStore.slideIndex + 1,
  slides: slidesStore.slides.map((slide, index) => ({
    index: index + 1,
    id: slide.id,
    title: slideTitle(slide),
    ...(withElements
      ? {
        elements: slide.elements.map(el => ({
          id: el.id,
          type: el.type,
          left: Math.round(el.left),
          top: Math.round(el.top),
          width: Math.round(el.width),
          ...('height' in el ? { height: Math.round(el.height) } : {}),
          text: clip(elementText(el), textLength),
        })),
      }
      : { element_count: slide.elements.length }),
  })),
})

// Large data URLs (uploaded or imported pictures) are replaced by a marker the agent sends back unchanged.
const withoutDataUrls = (slide: Slide): Slide => JSON.parse(JSON.stringify(slide), (key, value) =>
  key === 'src' && typeof value === 'string' && value.startsWith('data:') && value.length > 512 ? OMITTED_SRC : value,
)

// --- what the agent writes --------------------------------------------------------------------------------------------

const ALLOWED_TAGS = new Set(['P', 'UL', 'OL', 'LI', 'BLOCKQUOTE', 'STRONG', 'EM', 'U', 'STRIKE', 'SUP', 'SUB', 'CODE', 'SPAN', 'BR'])
const ALLOWED_STYLES = ['color', 'background-color', 'font-size', 'font-family', 'text-align']

// Rich text is rendered with v-html, so keep only the tags and inline styles PPTist's text schema allows.
const sanitizeHtml = (html: string) => {
  const doc = new DOMParser().parseFromString(html, 'text/html')
  const clean = (node: Element) => {
    for (const child of Array.from(node.children)) {
      if (!ALLOWED_TAGS.has(child.tagName)) {
        if (['SCRIPT', 'STYLE', 'IFRAME', 'OBJECT', 'EMBED', 'TEMPLATE'].includes(child.tagName)) child.remove()
        else child.replaceWith(...Array.from(child.childNodes))
        continue
      }
      const style = (child as HTMLElement).style
      const kept = ALLOWED_STYLES.map(name => [name, style.getPropertyValue(name)]).filter(([, value]) => value && !/url\(/i.test(value))
      for (const attr of Array.from(child.attributes)) child.removeAttribute(attr.name)
      for (const [name, value] of kept) (child as HTMLElement).style.setProperty(name, value)
      clean(child)
    }
  }
  clean(doc.body)
  // Unwrapping may expose new children; one more pass catches them.
  clean(doc.body)
  return doc.body.innerHTML
}

const safeSrc = (src: unknown) => typeof src === 'string' && (/^https:\/\//i.test(src) || /^data:image\/(png|jpe?g|gif|webp|svg\+xml);/i.test(src))

const normalizeElement = (raw: unknown, previous?: PPTElement): PPTElement => {
  if (!raw || typeof raw !== 'object') throw new Error('Every element must be an object')
  const el = { rotate: 0, ...(raw as Record<string, unknown>) } as Record<string, unknown>
  if (typeof el.type !== 'string') throw new Error('Every element needs a type')
  if (typeof el.id !== 'string' || !el.id) el.id = nanoid(10)
  if (el.type === 'text') {
    el.content = sanitizeHtml(String(el.content ?? ''))
    el.defaultFontName ??= slidesStore.theme.fontName
    el.defaultColor ??= slidesStore.theme.fontColor
  }
  if (el.type === 'shape' && el.text && typeof el.text === 'object') {
    const text = el.text as Record<string, unknown>
    el.text = {
      defaultFontName: slidesStore.theme.fontName,
      defaultColor: slidesStore.theme.fontColor,
      align: 'middle',
      ...text,
      content: sanitizeHtml(String(text.content ?? '')),
    }
  }
  if (el.type === 'image') {
    if (el.src === OMITTED_SRC && previous?.type === 'image') el.src = previous.src
    if (!safeSrc(el.src)) throw new Error(`Image ${String(el.id)} needs an https:// or data:image/ src`)
    el.fixedRatio ??= true
  }
  return el as unknown as PPTElement
}

const normalizeSlide = (raw: unknown, previous?: Slide): Slide => {
  if (!raw || typeof raw !== 'object') throw new Error('Every slide must be an object')
  const slide = raw as Record<string, unknown>
  if (!Array.isArray(slide.elements)) throw new Error('Every slide needs an elements array')
  const before = new Map(previous?.elements.map(el => [el.id, el]) ?? [])
  return {
    ...(slide as unknown as Slide),
    id: typeof slide.id === 'string' && slide.id ? slide.id : nanoid(10),
    elements: slide.elements.map(el => normalizeElement(el, before.get((el as { id?: string })?.id ?? ''))),
  }
}

const findSlide = (id: unknown) => {
  const index = slidesStore.slides.findIndex(slide => slide.id === id)
  if (index === -1) throw new Error(`There is no slide ${String(id)}; call get_deck for the slide ids`)
  return index
}

const result = (structuredContent: unknown): ToolResult => {
  const text = JSON.stringify(structuredContent)
  if (text.length > MAX_RESULT_CHARS) throw new Error('The result is too large; ask for a single slide or fewer elements')
  return { structuredContent: JSON.parse(text) }
}

// --- rendering a slide for the agent ----------------------------------------------------------------------------------

const renderSlide = async (slide: Slide, width: number) => {
  const container = document.createElement('div')
  container.style.cssText = 'position:fixed;left:-100000px;top:0;pointer-events:none;'
  document.body.appendChild(container)
  const app = createApp({ render: () => h(ThumbnailSlide, { slide, size: width }) })
  app.use(Directive)
  app.use(getActivePinia()!)
  app.mount(container)
  try {
    await nextTick()
    await document.fonts.ready
    const node = container.firstElementChild as HTMLElement
    await Promise.all(Array.from(node.querySelectorAll('img')).map(img => img.decode().catch(() => undefined)))
    node.querySelectorAll('foreignObject [xmlns]').forEach(span => span.removeAttribute('xmlns'))
    // fontEmbedCSS '' skips inlining the web fonts, as PPTist's own image export does by default.
    return await toJpeg(node, { quality: 0.85, fontEmbedCSS: '', backgroundColor: '#ffffff' })
  }
  finally {
    app.unmount()
    container.remove()
  }
}

// --- canvas tools -----------------------------------------------------------------------------------------------------

const { addHistorySnapshot } = useHistorySnapshot()
const { addSlidesFromData } = useAddSlidesOrElements()
const { AIPPT } = useAIPPT()
const { exportPPTX, exporting } = useExport()

const changed = () => {
  addHistorySnapshot()
  return slidesStore.slides.map((slide, index) => ({ index: index + 1, id: slide.id, title: slideTitle(slide) }))
}

const tools: Record<string, (args: Args) => Promise<ToolResult> | ToolResult> = {
  get_deck: () => {
    for (const length of [160, 60]) {
      const outline = deckOutline(length, true)
      if (JSON.stringify(outline).length <= MAX_RESULT_CHARS) return result(outline)
    }
    return result({ ...deckOutline(0, false), note: 'Elements are left out of this large deck; call get_slide for one slide.' })
  },

  get_slide: ({ slide_id }) => result(withoutDataUrls(slidesStore.slides[findSlide(slide_id)])),

  render_slide: async ({ slide_id }) => {
    const slide = slide_id ? slidesStore.slides[findSlide(slide_id)] : slidesStore.currentSlide
    const dataUrl = await renderSlide(slide, 1280)
    return {
      content: [
        { type: 'image', data: dataUrl.slice(dataUrl.indexOf(',') + 1), mimeType: 'image/jpeg' },
        { type: 'text', text: `Slide ${slide.id} rendered at 1280 px wide (the deck is ${slidesStore.viewportSize} px wide).` },
      ],
    }
  },

  generate_deck: async ({ title, template, outline }) => {
    const name = TEMPLATES.includes(String(template)) ? String(template) : TEMPLATES[0]
    const response = await fetch(`./mocks/${name}.json`)
    if (!response.ok) throw new Error(`Template ${name} could not be loaded`)
    const templateDeck = (await response.json()) as { slides: Slide[]; theme: SlideTheme }
    await flushSave()
    await showDeck(nanoid(10), { title: String(title || '') })
    slidesStore.setTheme(templateDeck.theme)
    AIPPT(templateDeck.slides, outline as AIPPTSlide[])
    slidesStore.updateSlideIndex(0)
    return result({ deck_id: deckId, title: slidesStore.title, slides: changed() })
  },

  add_slides: ({ slides, after_slide_id }) => {
    if (!Array.isArray(slides) || !slides.length) throw new Error('slides must be a non-empty array')
    const added = slides.map(slide => normalizeSlide(slide))
    slidesStore.updateSlideIndex(after_slide_id ? findSlide(after_slide_id) : slidesStore.slides.length - 1)
    const first = slidesStore.slideIndex + 1
    addSlidesFromData(added) // gives the new slides and elements fresh ids
    return result({ added: slidesStore.slides.slice(first, first + added.length).map(slide => slide.id), slides: changed() })
  },

  update_slide: ({ slide }) => {
    const index = findSlide((slide as { id?: string } | undefined)?.id)
    const updated = normalizeSlide(slide, slidesStore.slides[index])
    slidesStore.updateSlide(updated, updated.id)
    mainStore.setActiveElementIdList([])
    slidesStore.updateSlideIndex(index)
    return result({ updated: updated.id, slides: changed() })
  },

  update_elements: ({ slide_id, changes }) => {
    const index = findSlide(slide_id)
    const slide = slidesStore.slides[index]
    if (!Array.isArray(changes) || !changes.length) throw new Error('changes must be a non-empty array')
    const elements = slide.elements.slice()
    for (const change of changes as { element_id: string; props: Record<string, unknown> }[]) {
      const at = elements.findIndex(el => el.id === change.element_id)
      if (at === -1) throw new Error(`Slide ${slide.id} has no element ${change.element_id}`)
      const { id: _id, type: _type, ...props } = change.props ?? {}
      const before = elements[at]
      const merged = before.type === 'shape' && before.text && props.text && typeof props.text === 'object'
        ? { ...props, text: { ...before.text, ...(props.text as object) } }
        : props
      elements[at] = normalizeElement({ ...before, ...merged }, before)
    }
    slidesStore.updateSlide({ elements }, slide.id)
    slidesStore.updateSlideIndex(index)
    return result({ updated: (changes as { element_id: string }[]).map(change => change.element_id) })
  },

  delete_slides: ({ slide_ids }) => {
    const ids = (Array.isArray(slide_ids) ? slide_ids : []).map(String)
    ids.forEach(findSlide)
    if (ids.length >= slidesStore.slides.length) slidesStore.addSlide(blankSlide())
    slidesStore.deleteSlide(ids)
    return result({ deleted: ids, slides: changed() })
  },

  go_to_slide: ({ slide_id }) => {
    slidesStore.updateSlideIndex(findSlide(slide_id))
    mainStore.setActiveElementIdList([])
    return result({ current_slide: slidesStore.slideIndex + 1 })
  },

  new_deck: async ({ title }) => {
    await flushSave()
    await showDeck(nanoid(10), { title: String(title || '') })
    return result({ deck_id: deckId, title: slidesStore.title })
  },

  open_deck: async ({ deck_id }) => {
    const loaded = await host.callTool<{ id: string | null; deck: Deck | null }>('load_deck', { id: String(deck_id) })
    if (!loaded.structuredContent?.deck) throw new Error(`There is no saved deck ${String(deck_id)}; call list_decks`)
    await flushSave()
    await showDeck(loaded.structuredContent.id!, loaded.structuredContent.deck, true)
    // It is this conversation's deck from now on, also when nothing in it changes.
    if (inConversation) await save({ force: true })
    return result({ deck_id: deckId, title: slidesStore.title, slides: changed() })
  },

  set_title: ({ title }) => {
    slidesStore.setTitle(String(title || ''))
    return result({ title: slidesStore.title })
  },

  export_pptx: async () => {
    exportPPTX(slidesStore.slides, true, false)
    await new Promise<void>(resolve => {
      const check = () => (exporting.value ? setTimeout(check, 200) : resolve())
      setTimeout(check, 300)
    })
    return result({ file_name: `${slidesStore.title}.pptx`, note: 'The browser downloads the file for the user.' })
  },
}

// --- one deck per conversation ---------------------------------------------------------------------------------------

let moving: Promise<void> = Promise.resolve()

// Show the deck of the conversation beside the canvas; returns false when it has none.
const showConversationDeck = async () => {
  if (!inConversation) return false
  const loaded = await host.callTool<{ id: string | null; deck: Deck | null }>('load_deck', {})
  if (!loaded.structuredContent?.id || !loaded.structuredContent.deck) return false
  await showDeck(loaded.structuredContent.id, loaded.structuredContent.deck, true)
  return true
}

// The user moved the canvas to another conversation, or a new conversation started beside it.
const moveTo = async (conversation: boolean) => {
  const before = inConversation
  inConversation = conversation
  // What was open stays saved, but does not become the deck of the conversation now beside the canvas.
  await flushSave({ bind: false })
  if (await showConversationDeck()) return
  // A conversation that just started keeps the deck the user began before it.
  if (conversation && !before) {
    if (deckSaved) await save({ force: true })
    return
  }
  await showDeck(nanoid(10), null)
}

// --- start ------------------------------------------------------------------------------------------------------------

const start = async () => {
  // PPTist asks before every unload because it keeps nothing; this canvas saves to Superbot instead.
  window.onbeforeunload = null
  const context = await host.getContext()
  inConversation = context.conversation === true
  // Listen from the start, so a conversation that changes while PPTist loads is followed once the first deck is shown.
  const follow = (step: () => Promise<unknown>) => {
    moving = moving.then(step).then(() => undefined, (error: Error) => {
      message.warning(`Could not load this conversation's deck: ${error.message}`, { duration: 6000 })
    })
  }
  host.on('context_changed', (data) => {
    const conversation = (data as { conversation?: unknown } | null)?.conversation
    if (typeof conversation === 'boolean') follow(() => moveTo(conversation))
  })
  follow(async () => {
    // Wait for PPTist's own first load (public/mocks/slides.json), so it cannot replace the deck shown here.
    while (!slidesStore.slides.length || snapshotStore.snapshotLength === 0) await new Promise(resolve => setTimeout(resolve, 50))
    lastSaved = JSON.stringify(currentDeck()) // PPTist's blank first slide is not worth saving
    await showConversationDeck()
  })
  await moving
  // The tools are registered once the saved deck is shown, so no agent call edits PPTist's demo deck that the saved
  // one then replaces. In Superbot's headless canvas host, where the user may open a tab and load the saved deck at
  // any moment, each call saves its changes before it answers, and tells the agent when they could not be saved.
  background = context.background
  for (const [name, handler] of Object.entries(tools)) {
    await host.registerTool(name, background
      ? async (args) => {
        const result = await handler(args)
        await flushSave()
        if (JSON.stringify(currentDeck()) === lastSaved) return result
        const warning = `Not saved: ${saveError}. The saved deck lacks this change, so it is lost when the user opens `
          + 'the conversation, unless a later call saves it.'
        return { ...result, content: [...(result.content ?? []), { type: 'text' as const, text: warning }] }
      }
      : handler)
  }
  updateModelContext()
  slidesStore.$subscribe(() => {
    updateModelContext()
    saveSoon()
  })
}

if (window.parent !== window) {
  void import('./aina-ui.js').then(bridge => {
    host = bridge.host
    return start()
  })
}
