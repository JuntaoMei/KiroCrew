import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { i18nT } from '../../i18n/t'

const { build, save } = vi.hoisted(() => ({ build: vi.fn(), save: vi.fn() }))

vi.mock('./crewDashboardDownload', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./crewDashboardDownload')>()),
  buildDashboardDownload: build,
}))
vi.mock('../../utils/download', () => ({ saveBlob: save }))

import CrewDashboardDownload from './CrewDashboardDownload'
import { DashboardDownloadError } from './crewDashboardDownload'

const INPUT = { html: '<p>3</p>', themeVars: {}, mode: 'light' as const, pageScripts: true }

function renderButton() {
  render(<CrewDashboardDownload name="Build watcher" input={INPUT} />)
  return screen.getByTestId('crew-dashboard-download')
}

describe('CrewDashboardDownload', () => {
  beforeEach(() => {
    build.mockReset()
    save.mockReset()
  })

  it('asks before saving, and saves nothing on Cancel', async () => {
    const user = userEvent.setup()
    await user.click(renderButton())
    expect(await screen.findByText(i18nT('pages.membersPage.dashboard_download_confirm_title'))).toBeInTheDocument()
    expect(screen.getByText(i18nT('pages.membersPage.dashboard_download_confirm_body'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: i18nT('components.confirmDialog.cancel') }))
    expect(build).not.toHaveBeenCalled()
    expect(save).not.toHaveBeenCalled()
  })

  it('saves the page the tab shows once confirmed', async () => {
    const blob = new Blob(['<!doctype html>'], { type: 'text/html' })
    build.mockResolvedValue(blob)
    save.mockResolvedValue(undefined)
    const user = userEvent.setup()
    await user.click(renderButton())
    await user.click(await screen.findByRole('button', { name: i18nT('pages.membersPage.dashboard_download_confirm') }))
    await vi.waitFor(() => expect(save).toHaveBeenCalledTimes(1))
    expect(build).toHaveBeenCalledWith(INPUT)
    expect(save.mock.calls[0][0]).toBe(blob)
    expect(save.mock.calls[0][1]).toMatch(/^Build watcher \d{4}-\d{2}-\d{2}\.html$/)
    expect(screen.queryByTestId('crew-dashboard-download-error')).toBeNull()
  })

  it('says so when the page is too big', async () => {
    build.mockRejectedValue(new DashboardDownloadError('too_large'))
    const user = userEvent.setup()
    await user.click(renderButton())
    await user.click(await screen.findByRole('button', { name: i18nT('pages.membersPage.dashboard_download_confirm') }))
    expect(await screen.findByText(i18nT('pages.membersPage.dashboard_download_too_large'))).toBeInTheDocument()
    expect(save).not.toHaveBeenCalled()
  })

  it('says so when the save fails', async () => {
    build.mockResolvedValue(new Blob(['x']))
    save.mockRejectedValue(new Error('hand-off failed'))
    const user = userEvent.setup()
    await user.click(renderButton())
    await user.click(await screen.findByRole('button', { name: i18nT('pages.membersPage.dashboard_download_confirm') }))
    expect(await screen.findByText(i18nT('pages.membersPage.dashboard_download_failed'))).toBeInTheDocument()
  })
})
