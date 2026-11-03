/**
 * Screenshot + measurement runner for capture/meetings-expand-panels.html (#21179).
 *
 * Two shells, from website/:
 *   npx vite --host 127.0.0.1 --port 6819 --strictPort
 *   node scripts/capture-meetings-expand-panels.mjs http://127.0.0.1:6819 \
 *     ../temp-screenshots/21179
 *
 * Each frame is checked before it is kept: compact must show the transcript and a
 * 340px sketch frame; an expanded sketch must hide the transcript and give the
 * frame more than 340px; an expanded notes pane must be taller than its 520px cap.
 * Escape must restore the compact layout with focus on the Expand control. A run
 * that photographs the wrong state exits nonzero.
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:6819'
const OUT = process.argv[3] || '../temp-screenshots/21179'

mkdirSync(OUT, { recursive: true })

// The mise-installed node exports an LD_LIBRARY_PATH that shadows the system
// libstdc++ Chromium's GPU stack needs; scrub it from the browser's env.
const env = { ...process.env }
delete env.LD_LIBRARY_PATH

const browser = await chromium.launch({ env })
let failed = 0
const fail = msg => {
  failed++
  console.error(`FAIL ${msg}`)
}

for (const theme of ['dark', 'light']) {
  for (const expand of ['none', 'sketch-artist', 'note-taker']) {
    const ctx = await browser.newContext({
      viewport: { width: 1280, height: 860 },
      deviceScaleFactor: 1.5,
      colorScheme: theme,
    })
    const page = await ctx.newPage()
    page.on('pageerror', e => fail(`${theme}-${expand}: page error ${e}`))
    await page.goto(`${BASE}/capture/meetings-expand-panels.html?expand=${expand}&theme=${theme}`, {
      waitUntil: 'load',
    })
    // `attached`: with the notes expanded, the sketch panel is hidden on purpose.
    await page.waitForSelector('[data-capture-root] iframe', { state: 'attached', timeout: 15000 })
    await page.waitForTimeout(800)

    const m = await page.evaluate(() => {
      const frame = document.querySelector('iframe')
      const pane = document.querySelector('[data-testid="agent-output-pane"]')
      const transcript = document.querySelector('[data-capture-transcript]')
      return {
        frameH: frame.getBoundingClientRect().height,
        paneH: pane.getBoundingClientRect().height,
        transcriptShown: transcript.getBoundingClientRect().height > 0,
      }
    })
    const name = `${theme}-${expand === 'none' ? 'compact' : `expanded-${expand}`}`
    if (expand === 'none') {
      if (!m.transcriptShown) fail(`${name}: transcript hidden in the compact layout`)
      if (Math.round(m.frameH) !== 340) fail(`${name}: sketch frame is ${m.frameH}px, not 340`)
    } else {
      if (m.transcriptShown) fail(`${name}: transcript still shown while expanded`)
      if (expand === 'sketch-artist' && m.frameH <= 400) fail(`${name}: frame only ${m.frameH}px`)
      if (expand === 'note-taker' && m.paneH <= 520) fail(`${name}: notes pane only ${m.paneH}px`)
    }
    console.log(`${name}: frame=${Math.round(m.frameH)} pane=${Math.round(m.paneH)} transcript=${m.transcriptShown}`)
    await page.screenshot({ path: `${OUT}/${name}.png` })

    if (expand !== 'none') {
      await page.keyboard.press('Escape')
      await page.waitForTimeout(300)
      const after = await page.evaluate(() => ({
        transcriptShown:
          document.querySelector('[data-capture-transcript]').getBoundingClientRect().height > 0,
        focus: document.activeElement?.getAttribute('aria-label') ?? '',
      }))
      if (!after.transcriptShown) fail(`${name}: Escape did not restore the compact layout`)
      if (!after.focus.startsWith('Expand ')) fail(`${name}: focus after Escape is "${after.focus}"`)
      console.log(`${name}: after Escape transcript=${after.transcriptShown} focus="${after.focus}"`)
    }
    await ctx.close()
  }
}

await browser.close()
process.exit(failed ? 1 : 0)
