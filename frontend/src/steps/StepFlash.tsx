import { useRef, useState } from 'react'
import { api, type Manifest } from '../lib/api'
import {
  baud1200Reset, flash, hasFilesystem, partsForMode, serialSupported,
  type FlashMode, type FlashProgress,
} from '../lib/flasher'

type Props = {
  manifest: Manifest
  onBack: () => void
  onNext: () => void
}

export default function StepFlash({ manifest, onBack, onNext }: Props) {
  const [lines, setLines] = useState<string[]>([])
  const [progress, setProgress] = useState<FlashProgress | null>(null)
  const [state, setState] = useState<'idle' | 'busy' | 'done' | 'error'>('idle')
  const [mode, setMode] = useState<FlashMode>('update')
  const [error, setError] = useState<string | null>(null)
  const logRef = useRef<HTMLDivElement>(null)

  const supported = serialSupported()
  const serialFlashable = manifest.parts.some((p) => p.offset !== null)
  const fsAvailable = hasFilesystem(manifest)
  const selectedParts = partsForMode(manifest, mode)

  // Was ein Modus bewirkt, hängt daran, wo der Splash liegt: bei Farbdisplays
  // im Dateisystem, sonst in die App einkompiliert.
  const splashInApp = manifest.splash === 'xbm'
  const modeInfo =
    mode === 'update'
      ? `Nur die Firmware wird ersetzt. Einstellungen, Schlüssel und Dateisystem
         bleiben unangetastet. ${splashInApp
           ? 'Der Startbildschirm steckt in der Firmware und wird mit erneuert.'
           : fsAvailable
             ? 'Der Startbildschirm liegt im Dateisystem und bleibt auf dem alten Stand — dafür „inkl. Dateisystem“ wählen.'
             : ''}`
      : mode === 'update_fs'
        ? `Firmware und Dateisystem werden ersetzt, ohne den Flash zu löschen.
           Einstellungen und Schlüssel bleiben erhalten, der Startbildschirm wird erneuert.`
        : `Der gesamte Flash wird gelöscht und neu beschrieben — Bootloader,
           Partitionstabelle, OTA-Zeiger, Firmware${fsAvailable ? ' und Dateisystem' : ''}.
           Das Gerät startet danach im Auslieferungszustand.`

  function log(line: string) {
    setLines((prev) => [...prev.slice(-2000), line])
    queueMicrotask(() => {
      const el = logRef.current
      if (el) el.scrollTop = el.scrollHeight
    })
  }

  async function resetTo1200() {
    setError(null)
    try {
      await baud1200Reset(log)
    } catch (err) {
      const message = (err as Error).message
      // Abbruch im Port-Dialog ist kein Fehler, den man anzeigen muss.
      if (!/No port selected|cancell?ed/i.test(message)) setError(message)
    }
  }

  async function run() {
    setState('busy')
    setError(null)
    try {
      await flash(manifest, mode, log, setProgress)
      setState('done')
    } catch (err) {
      setError((err as Error).message)
      setState('error')
    }
  }

  return (
    <div className="card">
      <h2>Firmware aufspielen</h2>
      <p className="lead">
        Schließe dein {manifest.device_name} per USB an und starte den Vorgang.
        Der Browser fragt dann, welcher Port genutzt werden soll.
      </p>

      {!supported && (
        <div className="notice err">
          Dieser Browser unterstützt Web Serial nicht. Nutze Chrome oder Edge —
          und rufe die Seite über HTTPS oder localhost auf.
        </div>
      )}

      {!serialFlashable && supported && (
        <div className="notice info">
          <strong>1. DFU-Modus aktivieren.</strong> Der Knopf öffnet den Port kurz
          mit 1200 Baud — nRF52-Boards starten daraufhin in den UF2-Bootloader und
          melden sich als USB-Laufwerk (z. B. <code>RAK4631</code>). Klappt das
          nicht, hilft doppeltes Drücken der RST-Taste.
          <div style={{ marginTop: 10 }}>
            <button className="ghost" onClick={resetTo1200}>
              DFU-Modus aktivieren
            </button>
          </div>
        </div>
      )}

      {!serialFlashable && (
        <div className="notice warn">
          <strong>2.</strong> {manifest.device_name} wird nicht seriell geflasht.
          Lade die Datei herunter und kopiere sie auf das DFU-Laufwerk. Das Gerät
          startet nach der Übertragung selbst neu — Meldungen über abgebrochene
          Übertragungen oder ausgeworfene Laufwerke sind dabei normal:
          {manifest.parts.map((part) => (
            <div key={part.name} style={{ marginTop: 6 }}>
              <a href={api.artifactUrl(manifest.cache_key, part.name)}>{part.name}</a>
            </div>
          ))}
        </div>
      )}

      {serialFlashable && supported && state !== 'done' && (
        <div className="notice info">
          Findet der Browser das Gerät nicht oder bricht das Flashen sofort ab,
          steckt es nicht im Download-Modus. Der 1200-Baud-Reset versetzt es
          dorthin — bei manchen Boards (z. B. T-Deck) geht es ohne kaum.
          <div style={{ marginTop: 10 }}>
            <button className="ghost" onClick={resetTo1200} disabled={state === 'busy'}>
              1200-Baud-Reset
            </button>
          </div>
        </div>
      )}

      {serialFlashable && supported && state !== 'done' && (
        <>
          <div className="field" style={{ marginTop: 4 }}>
            <label htmlFor="mode">Was soll geschrieben werden?</label>
            <select
              id="mode"
              value={mode}
              disabled={state === 'busy'}
              onChange={(event) => setMode(event.target.value as FlashMode)}
            >
              <option value="update">Update — nur die Firmware</option>
              {fsAvailable && (
                <option value="update_fs">Update inkl. Dateisystem</option>
              )}
              <option value="full">Vollständig neu — alles löschen</option>
            </select>
          </div>

          <div className={mode === 'full' ? 'notice warn' : 'notice info'}>
            {modeInfo}
            <div style={{ marginTop: 8, fontSize: 12.5, opacity: 0.85 }}>
              Geschrieben wird: {selectedParts.map((p) => p.name).join(', ')}
            </div>
          </div>

          {mode === 'full' && (
            <div className="notice err">
              <strong>Vorher die Schlüssel sichern.</strong> Ein vollständiges
              Löschen entfernt den öffentlichen und privaten Schlüssel des Geräts
              samt aller Einstellungen. Ohne Sicherung ist der Node danach eine
              neue Identität im Netz — gespeicherte Kontakte und Direktnachrichten
              erreichen ihn nicht mehr.
            </div>
          )}
        </>
      )}

      {progress && state === 'busy' && (
        <>
          <div className="bar"><span style={{ width: `${progress.percent}%` }} /></div>
          <div style={{ color: 'var(--muted)', fontSize: 13 }}>
            {progress.phase} — Datei {progress.fileIndex + 1}/{progress.fileCount} ({progress.percent}%)
          </div>
        </>
      )}

      {state === 'done' && (
        <div className="notice info" style={{ borderColor: 'var(--green-dark)', color: 'var(--green)' }}>
          ✓ Fertig. Das Gerät wurde per RTS neu gestartet und zeigt gleich den
          MeshHessen-Startbildschirm.
        </div>
      )}

      {error && <div className="notice err">{error}</div>}

      {lines.length > 0 && (
        <div className="log" ref={logRef} style={{ height: 220 }}>
          {lines.map((line, index) => (
            <div key={index} className={line.startsWith('✓') ? 'ok' : undefined}>{line}</div>
          ))}
        </div>
      )}

      <div className="row" style={{ marginTop: 18 }}>
        <button className="ghost" onClick={onBack} disabled={state === 'busy'}>Zurück</button>
        <div className="spacer" />
        {state !== 'done' && serialFlashable && (
          <button onClick={run} disabled={!supported || state === 'busy'}>
            {state === 'busy'
              ? 'Flashe …'
              : state === 'error'
                ? 'Nochmal versuchen'
                : mode === 'full'
                  ? 'Löschen und flashen'
                  : 'Update flashen'}
          </button>
        )}
        {(state === 'done' || !serialFlashable) && (
          <button onClick={onNext}>Weiter</button>
        )}
      </div>
    </div>
  )
}
