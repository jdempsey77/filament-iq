import { useState } from 'preact/hooks'
import { useProvider } from '../provider/context'

// Canonical label.print / label.printNiimbot handling for a single spool --
// awaits the real print result and surfaces success/failure via toast. This
// is SpoolsTab's existing per-row pattern (its own handlers stay untouched);
// this hook exists for the two NEW SpoolEditPanel hosts (mobile slot sheet,
// desktop detail panel) that don't already carry that state themselves.
export function useSpoolPrintActions(spool) {
  const provider = useProvider()
  const [printingLabel, setPrintingLabel] = useState(false)
  const [printingNiimbotLabel, setPrintingNiimbotLabel] = useState(false)
  const [toast, setToast] = useState(null)

  const handlePrintLabel = async () => {
    if (!provider) return
    setPrintingLabel(true)
    try {
      const d = await provider.rpc('label.print', { spool_id: spool.id })
      setPrintingLabel(false)
      if (d.success) {
        setToast({ msg: 'Label printed — spool moved to shelf', type: 'ok' })
      } else {
        setToast({ msg: `Print failed: ${d.error || 'unknown error'}`, type: 'err' })
      }
      setTimeout(() => setToast(null), 5000)
    } catch (e) {
      setPrintingLabel(false)
      const msg = e?.message?.endsWith('timed out') ? 'Print label timed out' : `Print failed: ${e.message || e}`
      setToast({ msg, type: 'err' })
      setTimeout(() => setToast(null), 5000)
    }
  }

  const handlePrintSwatchLabel = async () => {
    if (!provider) return
    setPrintingNiimbotLabel(true)
    try {
      const d = await provider.rpc('label.printNiimbot', { spool_id: spool.id })
      setPrintingNiimbotLabel(false)
      if (d.success) {
        setToast({ msg: 'Swatch label queued for printing', type: 'ok' })
      } else {
        setToast({ msg: `Swatch print failed: ${d.error || 'unknown error'}`, type: 'err' })
      }
      setTimeout(() => setToast(null), 5000)
    } catch (e) {
      setPrintingNiimbotLabel(false)
      const msg = e?.message?.endsWith('timed out') ? 'Swatch print timed out' : `Swatch print failed: ${e.message || e}`
      setToast({ msg, type: 'err' })
      setTimeout(() => setToast(null), 5000)
    }
  }

  return { printingLabel, printingNiimbotLabel, toast, handlePrintLabel, handlePrintSwatchLabel }
}
