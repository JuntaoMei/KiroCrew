/**
 * A scripted send's `meta.banner` (POST /api/chat `banner`): the bubble shows
 * the short label, and the full message the agent received opens behind a
 * disclosure. A row without one renders its content as before.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import React from 'react'
import UserMessage from '../pages/chat/UserMessage'

const FULL = 'Work order: rebase CR-1 onto mainline, rerun the build, report the result.'
const LABEL = 'Rebase CR-1 and rebuild'

function renderRow(meta?: Record<string, unknown>) {
  return render(
    <UserMessage
      content={FULL}
      meta={meta}
      messageTs="2026-11-03T19:00:00Z"
      renderContent={c => <p data-testid="rendered-content">{c}</p>}
    />,
  )
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('UserMessage banner', () => {
  it('shows the label and opens the full message on demand', () => {
    renderRow({ banner: LABEL })

    expect(screen.getByTestId('user-message-banner').textContent).toBe(LABEL)
    expect(screen.queryByText(FULL)).toBeNull()
    const toggle = screen.getByTestId('user-message-banner-toggle')
    expect(toggle.getAttribute('aria-expanded')).toBe('false')

    fireEvent.click(toggle)

    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByTestId('rendered-content').textContent).toBe(FULL)
  })

  it.each([undefined, {}, { banner: '   ' }, { banner: 42 }])(
    'renders the content as before without a usable label (%o)',
    meta => {
      renderRow(meta as Record<string, unknown> | undefined)

      expect(screen.getByTestId('rendered-content').textContent).toBe(FULL)
      expect(screen.queryByTestId('user-message-banner')).toBeNull()
    },
  )
})
