/** A mate's Dashboard page saved as one HTML file that opens anywhere, offline.
 *
 * Built in the BROWSER, from the same document the tab shows: `buildSrcdoc` over the
 * page the gateway composed, with the reader's live theme tokens. The gateway has no
 * theme to read, so a gateway-built copy could only draw the default light theme; this
 * keeps the file looking like the tab and adds no second document builder.
 *
 * The page itself is already self-contained -- the frame's CSP allows only `data:`
 * fonts and images and `connect-src 'none'` -- so the one thing the tab loads by URL
 * is the same-origin Tailwind runtime. The file cannot fetch it (a `file:` document
 * has no dashboard origin, and a cookie-authenticating proxy refuses a request with
 * no cookie), so it is fetched here once and inlined in place of that `<script src>`.
 *
 * Page script follows the tab's own grant: a page the tab shows without script is
 * saved without it (`pageScripts: false`), so opening the file never runs code the
 * tab withheld. The frame's link bridge and gesture relays are left out because no
 * host is listening to them in a saved file; links open the ordinary way.
 */
import { buildSrcdoc } from '../../lib/widgetSrcdoc'
import { TAILWIND_RUNTIME_PATH } from '../../lib/vendorPaths'
import { withBase } from '../../lib/basePath'

/** The most one saved page may be, in bytes. A page is up to 3 MiB and its data
 *  island up to 4 MiB (`dashboard_frame.MAX_PAGE_BYTES`, `sandbox_doc._MAX_BYTES`),
 *  plus the runtime and what `buildSrcdoc` adds, so a real page fits with room. */
export const MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024

/** Why a save did not produce a file. */
export type DownloadRefusal = 'too_large' | 'no_runtime'

export class DashboardDownloadError extends Error {
  constructor(readonly reason: DownloadRefusal) {
    super(reason)
    this.name = 'DashboardDownloadError'
  }
}

/** The runtime `<script src>` `buildSrcdoc` writes, in either of its two spellings
 *  (with an `onerror` handler, or with a nonce when page script is withheld). */
const RUNTIME_TAG = /<script src="([^"]*)" crossorigin="anonymous"(?: nonce="([^"]*)"| onerror="[^"]*")><\/script>/

/** Every casing of `</script` would close the inline element early. */
function escapeScriptClose(js: string): string {
  return js.replace(/<\/(script)/gi, '<\\/$1')
}

/** *srcdoc* with the runtime tag for *runtimeUrl* replaced by *runtimeJs* inline.
 *
 *  Throws `no_runtime` when the tag is not found, rather than saving a page that
 *  would open unstyled: the two are one contract, pinned by this module's test. */
export function inlineRuntime(srcdoc: string, runtimeUrl: string, runtimeJs: string): string {
  const match = RUNTIME_TAG.exec(srcdoc)
  if (!match || match[1] !== runtimeUrl) throw new DashboardDownloadError('no_runtime')
  // Built as an element and serialized, so no markup is spelled by hand here. A
  // script's text is serialized raw, which is why `</script` is escaped first.
  const script = document.createElement('script')
  if (match[2]) script.setAttribute('nonce', match[2])
  script.textContent = escapeScriptClose(runtimeJs)
  // Sliced rather than `String.replace`, so a `$` in the runtime is never a pattern.
  return srcdoc.slice(0, match.index) + script.outerHTML + srcdoc.slice(match.index + match[0].length)
}

/** The file name: the mate's name and the day, with anything a file system may
 *  refuse taken out. */
export function downloadFileName(name: string, now: Date = new Date()): string {
  const printable = Array.from(name, (ch) => (ch.charCodeAt(0) < 0x20 ? ' ' : ch)).join('')
  const safe = printable.replace(/[\\/:*?"<>|]+/g, ' ').replace(/\s+/g, ' ').trim()
  const day = now.toISOString().slice(0, 10)
  return `${safe || 'dashboard'} ${day}.html`
}

export interface DashboardDownloadInput {
  /** The composed page the tab is showing (`shown.html`). */
  html: string
  themeVars: Record<string, string>
  mode: 'dark' | 'light'
  /** Whether the tab lets this page run its own script. */
  pageScripts: boolean
}

/** The saved document as a Blob. *fetchText* reads the runtime; tests pass their own. */
export async function buildDashboardDownload(
  input: DashboardDownloadInput,
  fetchText: (url: string) => Promise<string> = defaultFetchText,
): Promise<Blob> {
  const srcdoc = buildSrcdoc({
    html: input.html,
    themeVars: input.themeVars,
    mode: input.mode,
    offlineScripts: true,
    pageScripts: input.pageScripts,
  })
  const runtimeUrl = window.location.origin + withBase(TAILWIND_RUNTIME_PATH)
  const runtimeJs = await fetchText(runtimeUrl)
  if (!runtimeJs) throw new DashboardDownloadError('no_runtime')
  const blob = new Blob([inlineRuntime(srcdoc, runtimeUrl, runtimeJs)], { type: 'text/html' })
  if (blob.size > MAX_DOWNLOAD_BYTES) throw new DashboardDownloadError('too_large')
  return blob
}

async function defaultFetchText(url: string): Promise<string> {
  const res = await fetch(url, { credentials: 'same-origin' })
  if (!res.ok) return ''
  return res.text()
}
