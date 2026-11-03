import { describe, expect, it } from 'vitest'
import {
  buildDashboardDownload,
  DashboardDownloadError,
  downloadFileName,
  inlineRuntime,
  MAX_DOWNLOAD_BYTES,
} from './crewDashboardDownload'
import { TAILWIND_RUNTIME_PATH } from '../../lib/vendorPaths'
import { withBase } from '../../lib/basePath'

const RUNTIME_URL = window.location.origin + withBase(TAILWIND_RUNTIME_PATH)
const PAGE = '<div data-dashboard-field="open">3</div><script>window.drawn = 1</script>'
const THEME = { '--bg': '#101010', '--text': '#eeeeee' }

async function saved(pageScripts: boolean, runtime = 'self.tw = 1'): Promise<string> {
  const blob = await buildDashboardDownload(
    { html: PAGE, themeVars: THEME, mode: 'dark', pageScripts },
    async (url) => {
      expect(url).toBe(RUNTIME_URL)
      return runtime
    },
  )
  return blob.text()
}

describe('buildDashboardDownload', () => {
  it('inlines the runtime and loads nothing by URL', async () => {
    const doc = await saved(true)
    expect(doc).toContain('<script>self.tw = 1</script>')
    expect(doc).not.toMatch(/<script[^>]*\ssrc=/)
    expect(doc).not.toContain('x-script-placeholder')
  })

  it('keeps the page, its theme and the frame CSP', async () => {
    const doc = await saved(true)
    expect(doc).toContain('data-dashboard-field="open"')
    expect(doc).toContain('--bg')
    expect(doc).toContain('#101010')
    expect(doc).toContain('<body class="dark"')
    expect(doc).toMatch(/http-equiv="Content-Security-Policy"/)
    expect(doc).toContain("connect-src 'none'")
  })

  it('saves a page shown without script without its script', async () => {
    const doc = await saved(false)
    const nonce = /script-src 'nonce-([^']+)'/.exec(doc)?.[1]
    expect(nonce).toBeTruthy()
    // The runtime still runs, under the document's nonce; the page's script has none.
    expect(doc).toContain(`<script nonce="${nonce}">self.tw = 1</script>`)
    expect(doc).not.toContain("script-src 'unsafe-inline'")
  })

  it('escapes every casing of </script in the runtime', async () => {
    const doc = await saved(true, 'a("</script>");b("</SCRIPT>")')
    expect(doc).toContain('a("<\\/script>");b("<\\/SCRIPT>")')
  })

  it('treats a $ in the runtime as text', async () => {
    const doc = await saved(true, "x.replace(/a/, '$&$1')")
    expect(doc).toContain("x.replace(/a/, '$&$1')")
  })

  it('refuses when the runtime cannot be read', async () => {
    await expect(saved(true, '')).rejects.toMatchObject({ reason: 'no_runtime' })
  })

  it('refuses a document over the size cap', async () => {
    await expect(saved(true, 'x'.repeat(MAX_DOWNLOAD_BYTES))).rejects.toBeInstanceOf(DashboardDownloadError)
    await expect(saved(true, 'x'.repeat(MAX_DOWNLOAD_BYTES))).rejects.toMatchObject({ reason: 'too_large' })
  })
})

describe('inlineRuntime', () => {
  it('refuses a document whose runtime tag points elsewhere', () => {
    const doc = '<script src="https://elsewhere/x.js" crossorigin="anonymous" onerror="f()"></script>'
    expect(() => inlineRuntime(doc, RUNTIME_URL, 'js')).toThrow(DashboardDownloadError)
  })
})

describe('downloadFileName', () => {
  const day = new Date('2026-11-03T12:00:00Z')

  it('names the file after the mate and the day', () => {
    expect(downloadFileName('Build watcher', day)).toBe('Build watcher 2026-11-03.html')
  })

  it('takes out characters a file system may refuse', () => {
    expect(downloadFileName('a/b:c*?"<>|\u0007d', day)).toBe('a b c d 2026-11-03.html')
  })

  it('falls back when nothing is left', () => {
    expect(downloadFileName('///', day)).toBe('dashboard 2026-11-03.html')
  })
})
