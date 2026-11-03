import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useTipTrigger } from '../components/TipCard'
import { selectActiveSlotHidesTips, tipsBlockedByMemoryMode } from '../pages/chat/page/activeSlotState'

// The chat page hides tips by the active session's memory mode: a temporary or
// an incognito session never fetches one. This drives the page's own selector
// into the real hook and watches the request.

const mockTip = {
  id: 'test-tip',
  feature: 'Cron Jobs',
  title: 'Schedule recurring tasks',
  body: 'Use cron_add to schedule recurring jobs.',
  why: '',
  doc: 'cron-and-scheduling.md',
  cta_prompt: '',
}

vi.mock('../api/client', () => ({
  api: {
    tipsFeedback: vi.fn().mockResolvedValue({ ok: true }),
    tipsNext: vi.fn().mockResolvedValue(null),
    tipsStatus: vi.fn().mockResolvedValue({ enabled_config: true, opted_out: false, cadence_hours: 6 }),
  },
}))

function stateFor(memoryMode: string) {
  return {
    dashboard: { slots: [{ key: 'slot-a', memory_mode: memoryMode }] as never },
    chat: { activeSlot: 'slot-a' },
  }
}

function Harness({ memoryMode }: { memoryMode: string }) {
  const blocked = selectActiveSlotHidesTips(stateFor(memoryMode))
  const { tip } = useTipTrigger(true, false, 'slot-a', blocked)
  return <div data-testid="tip-out">{tip ? tip.title : 'none'}</div>
}

async function runTurn(memoryMode: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <Harness memoryMode={memoryMode} />
    </QueryClientProvider>,
  )
  await act(async () => {
    vi.advanceTimersByTime(11000)
  })
  await act(async () => {
    await vi.runOnlyPendingTimersAsync()
  })
}

describe('tips by session memory mode', () => {
  beforeEach(async () => {
    vi.useFakeTimers()
    localStorage.clear()
    const { api: mockApi } = await import('../api/client')
    ;(mockApi.tipsNext as ReturnType<typeof vi.fn>).mockReset().mockResolvedValue({ tip: mockTip, glow: true })
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('names temporary and incognito, and nothing else', () => {
    expect(tipsBlockedByMemoryMode('temporary')).toBe(true)
    expect(tipsBlockedByMemoryMode('incognito')).toBe(true)
    expect(tipsBlockedByMemoryMode('persistent')).toBe(false)
    expect(tipsBlockedByMemoryMode(undefined)).toBe(false)
  })

  it('an incognito session requests no tip', async () => {
    const { api: mockApi } = await import('../api/client')
    await runTurn('incognito')
    expect(mockApi.tipsNext).not.toHaveBeenCalled()
    expect(screen.getByTestId('tip-out').textContent).toBe('none')
  })

  it('a temporary session requests no tip', async () => {
    const { api: mockApi } = await import('../api/client')
    await runTurn('temporary')
    expect(mockApi.tipsNext).not.toHaveBeenCalled()
  })

  it('a persistent session still gets its tip, so the gate is the memory mode', async () => {
    const { api: mockApi } = await import('../api/client')
    await runTurn('persistent')
    expect(mockApi.tipsNext).toHaveBeenCalled()
    expect(screen.getByTestId('tip-out').textContent).toBe(mockTip.title)
  })
})
