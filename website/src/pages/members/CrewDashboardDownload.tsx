import { useState } from 'react'
import { Download } from 'lucide-react'
import { IconButton } from '../../components/ui'
import ErrorNotice from '../../components/ErrorNotice'
import { useConfirm } from '../../components/ConfirmDialog'
import { saveBlob } from '../../utils/download'
import { i18nT } from '../../i18n/t'
import {
  buildDashboardDownload,
  DashboardDownloadError,
  downloadFileName,
  type DashboardDownloadInput,
} from './crewDashboardDownload'

/** The Dashboard tab's Download button, and the notice it shows when a save fails.
 *
 * The confirm comes first, every time: the file carries whatever the mate put on its
 * page, and once it is saved it can be sent anywhere. */
export default function CrewDashboardDownload({ name, input, buttonClassName }: {
  /** The mate's display name, for the file name. */
  name: string
  input: DashboardDownloadInput
  /** Classes for the button alone, so a host can float or hide it without hiding
   *  the failure notice beside it. */
  buttonClassName?: string
}) {
  const { confirm, confirmDialog } = useConfirm()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onClick = async () => {
    setError(null)
    const ok = await confirm({
      title: i18nT('pages.membersPage.dashboard_download_confirm_title'),
      body: i18nT('pages.membersPage.dashboard_download_confirm_body'),
      confirmLabel: i18nT('pages.membersPage.dashboard_download_confirm'),
      danger: false,
      primary: true,
    })
    if (!ok) return
    setBusy(true)
    try {
      const blob = await buildDashboardDownload(input)
      await saveBlob(blob, downloadFileName(name))
    } catch (e) {
      setError(
        e instanceof DashboardDownloadError && e.reason === 'too_large'
          ? i18nT('pages.membersPage.dashboard_download_too_large')
          : i18nT('pages.membersPage.dashboard_download_failed'),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      {error && (
        <ErrorNotice message={error} className="flex-1 min-w-0" testId="crew-dashboard-download-error" />
      )}
      <IconButton
        onClick={() => void onClick()}
        disabled={busy}
        // Stays shown while a save runs, so the button does not vanish mid-save.
        className={busy ? `${buttonClassName ?? ''} opacity-100` : buttonClassName}
        aria-label={i18nT('pages.membersPage.dashboard_download')}
        data-testid="crew-dashboard-download"
      >
        <Download size={14} aria-hidden="true" />
      </IconButton>
      {confirmDialog}
    </>
  )
}
